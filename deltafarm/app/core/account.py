"""Account-Auswertung: Kontostaende, Positionen, erkannte Paare.

Phase 2 sendet keine Orders. Das Ziel ist, Positionen sichtbar zu machen, die
von Hand eroeffnet wurden - inklusive der Frage, ob sie ueberhaupt gehedgt sind.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

import structlog

from app.adapters.base import AdapterError, TradingNotSupported
from app.core.positions import (
    LiquidationThresholds,
    MarginThresholds,
    assess_position,
    net_delta_usd,
)
from app.models.domain import (
    Balance,
    FundingPayment,
    HealthLevel,
    Position,
    PositionHealth,
    Side,
)

LOG = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PositionView:
    position: Position
    health: PositionHealth
    funding_received: Optional[Decimal] = None  # aus dem Journal, echte Zahlungen


@dataclass(frozen=True)
class DetectedPair:
    """Ein Paar, das aus den offenen Positionen beider Boersen erkannt wurde.

    `hedged` ist False, wenn nur ein Bein existiert - dann ist das eine
    ungesicherte Richtungswette und keine delta-neutrale Position.
    """

    symbol: str
    legs: tuple[PositionView, ...]
    net_delta_usd: Decimal
    net_delta_pct: Decimal
    combined_pnl: Decimal
    funding_received: Decimal
    hedged: bool
    level: HealthLevel
    opened_at: Optional[datetime] = None

    @property
    def holding_hours(self) -> Optional[Decimal]:
        if self.opened_at is None:
            return None
        sekunden = (datetime.now(timezone.utc) - self.opened_at).total_seconds()
        return (Decimal(sekunden) / Decimal(3600)).quantize(Decimal("0.1"))


class AccountService:
    """Holt Kontodaten aller Adapter und verdichtet sie."""

    def __init__(
        self,
        adapters: Sequence,
        *,
        journal=None,
        liquidation_thresholds: Optional[LiquidationThresholds] = None,
        margin_thresholds: Optional[MarginThresholds] = None,
        delta_warn_pct: Decimal = Decimal("0.02"),
    ) -> None:
        self._adapters = list(adapters)
        self._journal = journal
        self._liq = liquidation_thresholds or LiquidationThresholds()
        self._mar = margin_thresholds or MarginThresholds()
        self._delta_warn = delta_warn_pct

    @property
    def has_any_account_access(self) -> bool:
        return any(getattr(a, "has_account_access", False) for a in self._adapters)

    # --- Rohdaten ---------------------------------------------------------

    async def _safe(self, adapter, methode: str, *args, **kwargs):
        """Ruft eine Adaptermethode auf und schluckt nur erwartbare Fehler."""
        fn = getattr(adapter, methode, None)
        if fn is None:
            return None
        try:
            return await fn(*args, **kwargs)
        except TradingNotSupported as exc:
            # Kein Zugang konfiguriert - das ist kein Fehler, nur kein Ergebnis.
            LOG.debug("kein_account_zugang", venue=adapter.name, methode=methode, grund=str(exc))
            return None
        except AdapterError as exc:
            LOG.warning("account_abruf_fehlgeschlagen", venue=adapter.name, methode=methode, fehler=str(exc))
            return None

    async def balances(self) -> list[Balance]:
        ergebnisse = await asyncio.gather(
            *(self._safe(a, "get_balance") for a in self._adapters)
        )
        return [b for b in ergebnisse if b is not None]

    async def positions(self) -> list[Position]:
        ergebnisse = await asyncio.gather(
            *(self._safe(a, "get_positions") for a in self._adapters)
        )
        return [p for liste in ergebnisse if liste for p in liste]

    async def funding_payments(self, since: datetime) -> list[FundingPayment]:
        ergebnisse = await asyncio.gather(
            *(self._safe(a, "get_funding_payments", since) for a in self._adapters)
        )
        return [z for liste in ergebnisse if liste for z in liste]

    # --- Verdichtung ------------------------------------------------------

    def _view(self, position: Position, margin_usage: Optional[Decimal]) -> PositionView:
        gesundheit = assess_position(
            position,
            margin_usage=margin_usage,
            thresholds=self._liq,
            margin_thresholds=self._mar,
        )
        erhalten = None
        if self._journal is not None:
            erhalten = self._journal.funding_total(
                venue=position.venue, symbol=position.symbol
            )
        return PositionView(position=position, health=gesundheit, funding_received=erhalten)

    def detect_pairs(
        self,
        positions: Sequence[Position],
        balances: Sequence[Balance] = (),
    ) -> list[DetectedPair]:
        """Gruppiert offene Positionen je Symbol zu Paaren.

        Ein Symbol mit nur einem Bein wird trotzdem aufgefuehrt, aber als
        `hedged=False` - genau das soll in der Oberflaeche auffallen.
        """
        auslastung = {b.venue: b.margin_usage for b in balances}

        je_symbol: dict[str, list[Position]] = {}
        for p in positions:
            je_symbol.setdefault(p.symbol, []).append(p)

        paare: list[DetectedPair] = []
        for symbol, beine in sorted(je_symbol.items()):
            ansichten = tuple(self._view(p, auslastung.get(p.venue)) for p in beine)
            delta, anteil = net_delta_usd(beine)

            seiten = {p.side for p in beine}
            gehedgt = Side.LONG in seiten and Side.SHORT in seiten and len(beine) >= 2

            stufe = HealthLevel.GREEN
            for a in ansichten:
                if a.health.level is HealthLevel.RED:
                    stufe = HealthLevel.RED
                    break
                if a.health.level is HealthLevel.YELLOW:
                    stufe = HealthLevel.YELLOW
            if not gehedgt:
                # Eine ungesicherte Richtungswette ist nie gruen.
                stufe = HealthLevel.RED
            elif abs(anteil) >= self._delta_warn and stufe is HealthLevel.GREEN:
                stufe = HealthLevel.YELLOW

            eroeffnet = [p.opened_at for p in beine if p.opened_at is not None]

            paare.append(
                DetectedPair(
                    symbol=symbol,
                    legs=ansichten,
                    net_delta_usd=delta,
                    net_delta_pct=anteil,
                    combined_pnl=sum((p.unrealised_pnl for p in beine), Decimal(0)),
                    funding_received=sum(
                        (a.funding_received or Decimal(0) for a in ansichten), Decimal(0)
                    ),
                    hedged=gehedgt,
                    level=stufe,
                    opened_at=min(eroeffnet) if eroeffnet else None,
                )
            )
        return paare

    # --- Journal ----------------------------------------------------------

    async def sync(self, *, lookback_hours: int = 48) -> dict[str, int]:
        """Holt Kontodaten und schreibt sie ins Journal.

        Gibt zurueck, wie viele Zahlungen neu waren und wie viele
        Momentaufnahmen geschrieben wurden.
        """
        if self._journal is None:
            return {"funding_neu": 0, "snapshots": 0}

        seit = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        zahlungen, kontostaende = await asyncio.gather(
            self.funding_payments(seit), self.balances()
        )

        neu = self._journal.record_funding_payments(zahlungen)
        for b in kontostaende:
            self._journal.record_snapshot(b)

        if neu:
            self._journal.record_event(
                kind="funding_nachgeladen",
                message=f"{neu} neue Funding-Zahlungen uebernommen",
                payload={"anzahl": neu, "seit": seit},
            )
        return {"funding_neu": neu, "snapshots": len(kontostaende)}
