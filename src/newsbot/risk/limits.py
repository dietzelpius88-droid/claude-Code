"""Portfolio-Zustand und harte Risikogrenzen.

Jede Order passiert `RiskManager.approve()`. Faellt auch nur eine Pruefung
durch, wird nicht gehandelt - ohne Ausnahme und ohne Uebersteuerung durch
das Sprachmodell.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..clock import now_ms as _clock_now_ms
from ..config import RiskConfig
from ..models import Direction, Position, TradeSignal, now_ms
from .sizing import SizingResult

log = logging.getLogger(__name__)


def _day_key(ts: float | None = None) -> str:
    d = datetime.fromtimestamp(ts or _clock_now_ms() / 1000.0, tz=timezone.utc)
    return d.strftime("%Y-%m-%d")


def _week_key(ts: float | None = None) -> str:
    d = datetime.fromtimestamp(ts or _clock_now_ms() / 1000.0, tz=timezone.utc)
    return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"


@dataclass
class Portfolio:
    """Laufender Kontozustand."""

    starting_equity_usd: float
    equity_usd: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    realized_by_day: dict[str, float] = field(default_factory=dict)
    realized_by_week: dict[str, float] = field(default_factory=dict)
    consecutive_losses: int = 0
    halted_until_ms: int = 0
    halt_reason: str = ""
    trade_count: int = 0
    win_count: int = 0

    def __post_init__(self) -> None:
        if self.equity_usd == 0.0:
            self.equity_usd = self.starting_equity_usd

    # ------------------------------------------------------------------
    @property
    def gross_exposure_usd(self) -> float:
        return sum(abs(p.notional_usd) for p in self.positions.values())

    @property
    def open_clusters(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.positions.values():
            out[p.cluster] = out.get(p.cluster, 0) + 1
        return out

    def day_pnl(self, ts: float | None = None) -> float:
        return self.realized_by_day.get(_day_key(ts), 0.0)

    def week_pnl(self, ts: float | None = None) -> float:
        return self.realized_by_week.get(_week_key(ts), 0.0)

    @property
    def is_halted(self) -> bool:
        return now_ms() < self.halted_until_ms

    # ------------------------------------------------------------------
    def record_close(self, pnl_usd: float, ts: float | None = None) -> None:
        """Bucht ein geschlossenes Geschaeft."""
        self.equity_usd += pnl_usd
        dk, wk = _day_key(ts), _week_key(ts)
        self.realized_by_day[dk] = self.realized_by_day.get(dk, 0.0) + pnl_usd
        self.realized_by_week[wk] = self.realized_by_week.get(wk, 0.0) + pnl_usd
        self.trade_count += 1
        if pnl_usd > 0:
            self.win_count += 1
            self.consecutive_losses = 0
        elif pnl_usd < 0:
            self.consecutive_losses += 1

    def halt(self, reason: str, seconds: int) -> None:
        self.halted_until_ms = now_ms() + seconds * 1000
        self.halt_reason = reason
        log.error("HANDEL GESTOPPT (%d s): %s", seconds, reason)

    def resume(self) -> None:
        self.halted_until_ms = 0
        self.halt_reason = ""


@dataclass(slots=True)
class RiskDecision:
    ok: bool
    reason: str = ""
    checks: dict[str, bool] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


class RiskManager:
    def __init__(self, cfg: RiskConfig, blackout_hours_utc: list[int] | None = None) -> None:
        self.cfg = cfg
        self.blackout = set(blackout_hours_utc or [])

    # ------------------------------------------------------------------
    def check_halts(self, pf: Portfolio, ts: float | None = None) -> RiskDecision:
        """Nur die kontoweiten Abschaltgruende - auch ohne konkretes Signal
        aufrufbar (z.B. beim periodischen Gesundheitscheck)."""
        if pf.is_halted:
            return RiskDecision(False, f"Handel pausiert: {pf.halt_reason}")

        day_limit = -abs(pf.starting_equity_usd * self.cfg.daily_loss_limit_pct)
        if pf.day_pnl(ts) <= day_limit:
            pf.halt(f"Tagesverlustgrenze erreicht ({pf.day_pnl(ts):,.2f} USD)",
                    self.cfg.cooldown_after_halt_s)
            return RiskDecision(False, "Tagesverlustgrenze erreicht")

        week_limit = -abs(pf.starting_equity_usd * self.cfg.weekly_loss_limit_pct)
        if pf.week_pnl(ts) <= week_limit:
            pf.halt(f"Wochenverlustgrenze erreicht ({pf.week_pnl(ts):,.2f} USD)",
                    self.cfg.cooldown_after_halt_s * 6)
            return RiskDecision(False, "Wochenverlustgrenze erreicht")

        if pf.consecutive_losses >= self.cfg.consecutive_loss_halt:
            pf.halt(f"{pf.consecutive_losses} Verluste in Folge",
                    self.cfg.cooldown_after_halt_s)
            return RiskDecision(False, "zu viele Verluste in Folge")

        return RiskDecision(True)

    # ------------------------------------------------------------------
    def approve(self, signal: TradeSignal, sizing: SizingResult, pf: Portfolio,
                ts: float | None = None) -> RiskDecision:
        checks: dict[str, bool] = {}

        def fail(name: str, reason: str) -> RiskDecision:
            checks[name] = False
            log.info("ABGELEHNT %s (%s): %s", signal.asset.symbol, name, reason)
            return RiskDecision(False, reason, checks)

        halts = self.check_halts(pf, ts)
        checks["halts"] = halts.ok
        if not halts.ok:
            return RiskDecision(False, halts.reason, checks)

        if not sizing.ok:
            return fail("sizing", sizing.reason)

        hour = datetime.fromtimestamp(ts or _clock_now_ms() / 1000.0, tz=timezone.utc).hour
        if hour in self.blackout:
            return fail("blackout", f"Handelssperre in UTC-Stunde {hour}")
        checks["blackout"] = True

        if len(pf.positions) >= self.cfg.max_concurrent_positions:
            return fail("max_positions",
                        f"{len(pf.positions)} offene Positionen (max "
                        f"{self.cfg.max_concurrent_positions})")
        checks["max_positions"] = True

        cluster = signal.asset.cluster
        in_cluster = pf.open_clusters.get(cluster, 0)
        if in_cluster >= self.cfg.max_positions_per_cluster:
            return fail("cluster",
                        f"Gruppe '{cluster}' bereits mit {in_cluster} Position(en) belegt")
        checks["cluster"] = True

        existing = pf.positions.get(signal.asset.symbol)
        if existing is not None:
            if existing.side is not signal.direction:
                return fail("conflict",
                            f"Gegenposition in {signal.asset.symbol} offen "
                            f"({existing.side.value})")
            return fail("duplicate", f"Position in {signal.asset.symbol} bereits offen")
        checks["conflict"] = True

        # Gegenlaeufige Positionen innerhalb derselben Korrelationsgruppe
        # heben sich auf und verbrennen nur Gebuehren.
        for p in pf.positions.values():
            if p.cluster == cluster and p.side is not signal.direction:
                return fail("cluster_conflict",
                            f"{p.symbol} laeuft {p.side.value} in derselben Gruppe")

        new_gross = pf.gross_exposure_usd + sizing.notional_usd
        max_gross = pf.equity_usd * self.cfg.max_gross_exposure_pct
        if new_gross > max_gross:
            return fail("gross_exposure",
                        f"Bruttoexposure {new_gross:,.0f} > {max_gross:,.0f} USD")
        checks["gross_exposure"] = True

        max_risk = pf.equity_usd * self.cfg.max_risk_per_trade_pct
        if sizing.risk_usd > max_risk * 1.001:
            return fail("trade_risk",
                        f"Trade-Risiko {sizing.risk_usd:,.2f} > {max_risk:,.2f} USD")
        checks["trade_risk"] = True

        age_ms = now_ms() - signal.created_ms
        if age_ms > 120_000:
            return fail("stale_signal", f"Signal ist {age_ms/1000:.0f}s alt")
        checks["stale_signal"] = True

        return RiskDecision(True, "", checks)
