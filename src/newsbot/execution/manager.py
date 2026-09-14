"""Lebenszyklus einer Position: Einstieg, Absicherung, Ausstieg.

Einstieg in drei Scheiben
-------------------------
    Scheibe 1 (50 %) sofort, aggressiv limitiert  -> Kante sichern
    Scheibe 2 (30 %) nach ~4 s auf den Ruecksetzer -> besserer Schnitt
    Scheibe 3 (20 %) nach ~20 s nur bei Fortsetzung -> Bestaetigung

So bekommt der Bot bei echten Ereignissen eine volle Position, zahlt bei
Fehlsignalen aber nur einen Teil des Lehrgelds.

Ausstieg
--------
    * harter Stop aus der Ereignisvolatilitaet,
    * Teilverkaeufe an den Zielen,
    * Stop auf Einstand ab `breakeven_at_r`,
    * Chandelier-Trailing fuer den Rest,
    * Zeit-Stop nach einer Halbwertszeit (der wichtigste Ausstieg beim
      News-Trading: laeuft der Impuls aus, ist der Vorteil weg),
    * Sofort-Glattstellung bei Widerspruchsmeldung.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from ..config import ExecutionConfig, RiskConfig
from ..models import (Direction, Fill, OrderIntent, Position, Quote,
                      TradeSignal, now_ms)
from ..risk.limits import Portfolio
from ..risk.sizing import SizingResult
from .base import Broker, MarketData

log = logging.getLogger(__name__)


class ExitReason(str, Enum):
    STOP = "stop"
    TARGET = "target"
    TRAIL = "trail"
    TIME_STOP = "time_stop"
    CONTRADICTION = "contradiction"
    MANUAL = "manual"
    RISK_HALT = "risk_halt"


@dataclass(slots=True)
class PendingSlice:
    signal: TradeSignal
    qty: float
    due_ms: int
    offset_bps: float
    idx: int
    expires_ms: int


@dataclass(slots=True)
class ClosedTrade:
    symbol: str
    side: Direction
    qty: float
    entry_px: float
    exit_px: float
    pnl_usd: float
    fees_usd: float
    reason: ExitReason
    r_multiple: float
    held_s: float
    signal_id: str
    event_class: str
    opened_ms: int
    closed_ms: int = field(default_factory=now_ms)


class TradeManager:
    def __init__(self, broker: Broker, market: MarketData, portfolio: Portfolio,
                 risk_cfg: RiskConfig, exec_cfg: ExecutionConfig) -> None:
        self.broker = broker
        self.market = market
        self.pf = portfolio
        self.rcfg = risk_cfg
        self.ecfg = exec_cfg
        self.pending: list[PendingSlice] = []
        self.closed: list[ClosedTrade] = []
        # Erreichte Ziele je Position, damit nicht doppelt verkauft wird.
        self._hit_targets: dict[str, set[int]] = {}

    # ==================================================================
    # Einstieg
    # ==================================================================
    async def open(self, signal: TradeSignal, sizing: SizingResult) -> Position | None:
        """Startet den gestaffelten Einstieg. Gibt die Position nach der
        ersten Ausfuehrung zurueck (None, wenn Scheibe 1 nicht fuellt)."""
        q = self.market.quote(signal.asset.symbol)
        if q is None:
            log.warning("kein Kurs fuer %s - Einstieg abgebrochen", signal.asset.symbol)
            return None

        if not self._chase_ok(signal, q):
            return None

        slices = self.ecfg.entry_slices or [(1.0, 0, 15.0)]
        first_share, _, first_offset = slices[0]
        first_qty = self._round_lot(signal, sizing.qty * first_share)
        if first_qty <= 0:
            return None

        fill = await self._submit(signal, first_qty, first_offset, tag="entry", idx=0)
        if fill is None:
            log.info("Scheibe 1 nicht ausgefuehrt - kein Einstieg in %s",
                     signal.asset.symbol)
            return None

        pos = self._position_from_fill(signal, sizing, fill)
        self.pf.positions[pos.symbol] = pos
        self._hit_targets[pos.id] = set()

        # Restscheiben einplanen.
        now = now_ms()
        for idx, (share, delay_ms, offset) in enumerate(slices[1:], start=1):
            qty = self._round_lot(signal, sizing.qty * share)
            if qty <= 0:
                continue
            self.pending.append(PendingSlice(
                signal=signal, qty=qty, due_ms=now + delay_ms, offset_bps=offset,
                idx=idx, expires_ms=now + delay_ms + self.ecfg.slice_expiry_ms))

        log.info("POSITION AUF %s %s qty=%.6f @ %.6f stop=%.6f risiko=%.2f USD "
                 "zeitstop=%ds", pos.side.value, pos.symbol, pos.qty, pos.entry_px,
                 pos.stop_px, pos.risk_usd, signal.time_stop_s)
        return pos

    # ------------------------------------------------------------------
    def _chase_ok(self, signal: TradeSignal, q: Quote) -> bool:
        """Ist der Preis seit der Nachricht schon zu weit gelaufen?"""
        ref = signal.reference_px
        if ref <= 0 or signal.max_chase_bps <= 0:
            return True
        d = 1.0 if signal.direction is Direction.LONG else -1.0
        moved_bps = (q.mid - ref) / ref * 10_000.0 * d
        if moved_bps > signal.max_chase_bps:
            log.info("ZU SPAET %s: bereits %.0f bps gelaufen (Limit %.0f) - kein Einstieg",
                     signal.asset.symbol, moved_bps, signal.max_chase_bps)
            return False
        return True

    def _round_lot(self, signal: TradeSignal, qty: float) -> float:
        lot = signal.asset.lot_size or 0.0
        if lot <= 0:
            return max(0.0, qty)
        return int(qty / lot) * lot

    def slippage_budget_bps(self, signal: TradeSignal) -> float:
        """Erlaubte Abweichung vom Mittelkurs, skaliert mit der Chance."""
        budget = signal.expected_move_bps * self.ecfg.max_slippage_ratio
        return max(self.ecfg.min_slippage_bps,
                   min(self.ecfg.max_slippage_bps, budget))

    async def _submit(self, signal: TradeSignal, qty: float, offset_bps: float,
                      *, tag: str, idx: int) -> Fill | None:
        q = self.market.quote(signal.asset.symbol)
        if q is None:
            return None
        d = 1.0 if signal.direction is Direction.LONG else -1.0

        if offset_bps >= 0:
            # Aggressive Scheibe: Bezugspunkt ist der BERUEHRUNGSKURS, nicht die
            # Mitte. Sonst haengt die Ausfuehrbarkeit am Spread statt an unserer
            # Zahlungsbereitschaft - bei News ist der Spread aber genau das,
            # was sich unvorhersehbar weitet.
            touch = q.ask if d > 0 else q.bid
            limit = touch * (1.0 + d * offset_bps / 10_000.0)
        else:
            # Passive Scheibe: bewusst unter/ueber der Mitte auf den Ruecksetzer.
            limit = q.mid * (1.0 + d * offset_bps / 10_000.0)

        # Nie ueber das Slippage-Budget hinaus.
        cap = q.mid * (1.0 + d * self.slippage_budget_bps(signal) / 10_000.0)
        limit = min(limit, cap) if d > 0 else max(limit, cap)

        intent = OrderIntent(
            signal_id=signal.id, symbol=signal.asset.symbol, venue=signal.asset.venue,
            side=signal.direction, qty=qty, limit_px=limit, tif="IOC",
            slice_idx=idx, tag=tag)
        return await self.broker.submit(intent)

    def _position_from_fill(self, signal: TradeSignal, sizing: SizingResult,
                            fill: Fill) -> Position:
        d = 1.0 if signal.direction is Direction.LONG else -1.0
        stop_px = fill.px * (1.0 - d * signal.stop_bps / 10_000.0)
        targets = []
        shares = [s for _, s in self.rcfg.scale_out]
        for i, t_bps in enumerate(signal.targets_bps):
            share = shares[i] if i < len(shares) else 0.0
            targets.append((fill.px * (1.0 + d * t_bps / 10_000.0), share))
        return Position(
            symbol=signal.asset.symbol, venue=signal.asset.venue,
            side=signal.direction, qty=fill.qty, entry_px=fill.px,
            signal_id=signal.id, event_class=signal.event.event_class,
            cluster=signal.asset.cluster, stop_px=stop_px,
            time_stop_ms=now_ms() + signal.time_stop_s * 1000,
            targets=targets,
            risk_usd=fill.qty * abs(fill.px - stop_px),
            peak_px=fill.px, fees_usd=fill.fee_usd,
            initial_qty=fill.qty, initial_stop_px=stop_px,
            expected_move_bps=signal.expected_move_bps)

    # ==================================================================
    # Laufende Verwaltung
    # ==================================================================
    async def on_tick(self) -> list[ClosedTrade]:
        await self._process_pending()
        closed: list[ClosedTrade] = []
        for symbol in list(self.pf.positions.keys()):
            done = await self._manage(symbol)
            closed.extend(done)
        return closed

    async def _process_pending(self) -> None:
        now = now_ms()
        still: list[PendingSlice] = []
        for sl in self.pending:
            if now < sl.due_ms:
                still.append(sl)
                continue
            if now > sl.expires_ms:
                log.info("Scheibe %d fuer %s verfallen", sl.idx, sl.signal.asset.symbol)
                continue
            pos = self.pf.positions.get(sl.signal.asset.symbol)
            if pos is None:            # Position wurde schon geschlossen
                continue
            q = self.market.quote(sl.signal.asset.symbol)
            if q is None or not self._chase_ok(sl.signal, q):
                continue

            offset = sl.offset_bps
            if offset < 0:
                # Ruecksetzer-Scheibe. Ein passives Limit unter dem Briefkurs
                # kann als IOC per Definition nie ausgefuehrt werden. Deshalb
                # wirkt der negative Offset als PREIS-TRIGGER: erst wenn der
                # Markt tatsaechlich zurueckgekommen ist, wird aggressiv
                # zugegriffen. Das entspricht einer ruhenden Order, ohne sie
                # im Buch zeigen zu muessen.
                d = 1.0 if sl.signal.direction is Direction.LONG else -1.0
                trigger = pos.entry_px * (1.0 + d * offset / 10_000.0)
                erreicht = (q.mid <= trigger) if d > 0 else (q.mid >= trigger)
                if not erreicht:
                    still.append(sl)
                    continue
                offset = self.ecfg.entry_slices[0][2] if self.ecfg.entry_slices else 12.0

            fill = await self._submit(sl.signal, sl.qty, offset,
                                      tag="add", idx=sl.idx)
            if fill is not None:
                self._merge_fill(pos, fill)
                log.info("NACHGEKAUFT %s Scheibe %d qty=%.6f @ %.6f -> gesamt %.6f",
                         pos.symbol, sl.idx, fill.qty, fill.px, pos.qty)
            elif now <= sl.expires_ms:
                still.append(sl)       # nochmal versuchen, solange gueltig
        self.pending = still

    def _merge_fill(self, pos: Position, fill: Fill) -> None:
        total = pos.qty + fill.qty
        if total <= 0:
            return
        d = 1.0 if pos.side is Direction.LONG else -1.0
        old_stop_frac = abs(pos.entry_px - pos.stop_px) / pos.entry_px
        pos.entry_px = (pos.entry_px * pos.qty + fill.px * fill.qty) / total
        pos.qty = total
        pos.initial_qty = total
        pos.fees_usd += fill.fee_usd
        # Stop relativ zum neuen Durchschnittseinstand nachziehen.
        pos.stop_px = pos.entry_px * (1.0 - d * old_stop_frac)
        pos.initial_stop_px = pos.stop_px
        pos.risk_usd = pos.qty * abs(pos.entry_px - pos.stop_px)

    # ------------------------------------------------------------------
    async def _manage(self, symbol: str) -> list[ClosedTrade]:
        pos = self.pf.positions.get(symbol)
        if pos is None:
            return []
        q = self.market.quote(symbol)
        if q is None:
            return []

        d = 1.0 if pos.side is Direction.LONG else -1.0
        # Konservativ bewerten: Longs am Geldkurs, Shorts am Briefkurs.
        mark = q.bid if pos.side is Direction.LONG else q.ask
        pos.peak_px = max(pos.peak_px, mark) if d > 0 else min(pos.peak_px or mark, mark)

        # 1. Stop -----------------------------------------------------
        if (d > 0 and mark <= pos.stop_px) or (d < 0 and mark >= pos.stop_px):
            reason = ExitReason.TRAIL if pos.trailed else ExitReason.STOP
            return [await self._close(pos, pos.qty, reason, q)]

        # 2. Zeit-Stop ------------------------------------------------
        if now_ms() >= pos.time_stop_ms:
            return [await self._close(pos, pos.qty, ExitReason.TIME_STOP, q)]

        closed: list[ClosedTrade] = []

        # 3. Teilverkaeufe an den Zielen -------------------------------
        hits = self._hit_targets.setdefault(pos.id, set())
        for i, (tp_px, share) in enumerate(pos.targets):
            if i in hits or share <= 0:
                continue
            if (d > 0 and mark >= tp_px) or (d < 0 and mark <= tp_px):
                hits.add(i)
                # Anteile beziehen sich immer auf die URSPRUNGSmenge, sonst
                # verkauft die zweite Stufe einen Anteil vom bereits
                # reduzierten Rest und die Leiter verschiebt sich.
                qty = min(pos.qty, (pos.initial_qty or pos.qty) * share)
                if qty > 0:
                    t = await self._close(pos, qty, ExitReason.TARGET, q)
                    if t is not None:
                        closed.append(t)
                if symbol not in self.pf.positions:
                    return closed

        pos = self.pf.positions.get(symbol)
        if pos is None:
            return closed

        # 4. Stop auf Einstand -----------------------------------------
        r = pos.r_multiple(mark)
        if r >= self.rcfg.breakeven_at_r:
            be = pos.entry_px * (1.0 + d * 2.0 / 10_000.0)   # Gebuehren abdecken
            if (d > 0 and be > pos.stop_px) or (d < 0 and be < pos.stop_px):
                pos.stop_px = be
                pos.trailed = True
                log.info("STOP AUF EINSTAND %s @ %.6f (R=%.2f)", symbol, be, r)

        # 5. Chandelier-Trailing ---------------------------------------
        atr_bps = q.atr_bps or 0.0
        if r >= self.rcfg.breakeven_at_r:
            trail_bps = max(self.rcfg.trail_atr_mult * atr_bps,
                            self.rcfg.trail_move_ratio * pos.expected_move_bps)
            if trail_bps > 0:
                trail = pos.peak_px * (1.0 - d * trail_bps / 10_000.0)
                if (d > 0 and trail > pos.stop_px) or (d < 0 and trail < pos.stop_px):
                    pos.stop_px = trail
                    pos.trailed = True

        return closed

    # ==================================================================
    # Ausstieg
    # ==================================================================
    async def flatten(self, symbol: str, reason: ExitReason = ExitReason.MANUAL
                      ) -> ClosedTrade | None:
        pos = self.pf.positions.get(symbol)
        if pos is None:
            return None
        q = self.market.quote(symbol)
        if q is None:
            return None
        self.pending = [p for p in self.pending if p.signal.asset.symbol != symbol]
        return await self._close(pos, pos.qty, reason, q)

    async def flatten_all(self, reason: ExitReason = ExitReason.MANUAL) -> list[ClosedTrade]:
        out = []
        for s in list(self.pf.positions.keys()):
            t = await self.flatten(s, reason)
            if t is not None:
                out.append(t)
        return out

    async def _close(self, pos: Position, qty: float, reason: ExitReason,
                     q: Quote) -> ClosedTrade | None:
        qty = min(qty, pos.qty)
        if qty <= 0:
            return None
        exit_side = Direction.SHORT if pos.side is Direction.LONG else Direction.LONG
        d = 1.0 if exit_side is Direction.LONG else -1.0
        # Ausstieg aggressiv limitieren - beim Stop zaehlt Ausfuehrung, nicht Preis.
        exit_budget = max(self.ecfg.max_slippage_bps, q.spread_bps * 3.0)
        limit = q.mid * (1.0 + d * exit_budget / 10_000.0)
        intent = OrderIntent(signal_id=pos.signal_id, symbol=pos.symbol,
                             venue=pos.venue, side=exit_side, qty=qty,
                             limit_px=limit, tif="IOC", reduce_only=True,
                             tag="stop" if reason is ExitReason.STOP else "tp")
        fill = await self.broker.submit(intent)
        if fill is None:
            log.error("AUSSTIEG FEHLGESCHLAGEN %s (%s) - Position bleibt offen!",
                      pos.symbol, reason.value)
            return None

        pd = 1.0 if pos.side is Direction.LONG else -1.0
        pnl = (fill.px - pos.entry_px) * fill.qty * pd - fill.fee_usd
        # Immer gegen den URSPRUENGLICHEN Stop rechnen.
        risk_per_unit = abs(pos.entry_px - (pos.initial_stop_px or pos.stop_px))
        r_mult = (pnl / (risk_per_unit * fill.qty)) if risk_per_unit > 0 else 0.0

        pos.qty -= fill.qty
        pos.realized_pnl_usd += pnl
        pos.fees_usd += fill.fee_usd
        self.pf.record_close(pnl)

        trade = ClosedTrade(
            symbol=pos.symbol, side=pos.side, qty=fill.qty, entry_px=pos.entry_px,
            exit_px=fill.px, pnl_usd=round(pnl, 2), fees_usd=round(fill.fee_usd, 4),
            reason=reason, r_multiple=round(r_mult, 3),
            held_s=round((now_ms() - pos.opened_ms) / 1000.0, 1),
            signal_id=pos.signal_id, event_class=pos.event_class.value,
            opened_ms=pos.opened_ms)
        self.closed.append(trade)

        log.info("ZU %-12s %-5s qty=%.6f @ %.6f -> PnL %+.2f USD (%.2fR) nach %.0fs",
                 reason.value, pos.symbol, fill.qty, fill.px, pnl, r_mult, trade.held_s)

        if pos.qty <= (pos.qty + fill.qty) * 1e-6:
            self.pf.positions.pop(pos.symbol, None)
            self._hit_targets.pop(pos.id, None)
            self.pending = [p for p in self.pending if p.signal.asset.symbol != pos.symbol]
        return trade
