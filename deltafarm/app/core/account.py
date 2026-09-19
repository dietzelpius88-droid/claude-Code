"""Account-Auswertung: Kontostaende, Positionen, erkannte Paare.

Phase 2 sendet keine Orders. Das Ziel ist, Positionen sichtbar zu machen, die
von Hand eroeffnet wurden - inklusive der Frage, ob sie ueberhaupt gehedgt sind.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Optional, Sequence

import structlog

from app.adapters.base import AdapterError, TradingNotSupported
from app.core.funding import net_hourly_rate, to_apr
from app.core.positions import (
    LiquidationThresholds,
    MarginThresholds,
    assess_position,
    net_delta_usd,
)
from app.storage.zeit import als_utc, jetzt
from app.models.domain import (
    Balance,
    PairState,
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
        sekunden = (jetzt() - als_utc(self.opened_at)).total_seconds()
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


@dataclass(frozen=True)
class PairView:
    """Ein Paar, wie die Oberflaeche es zeigt.

    Fuehrt zusammen, was bisher getrennt war: den Eintrag in der Datenbank,
    die tatsaechlich offenen Positionen, das zugeordnete Funding und die
    Gebuehren aus den Fills.
    """

    pair_id: int
    symbol: str
    source: str
    status: str
    legs: tuple[PositionView, ...]
    net_delta_usd: Decimal
    net_delta_pct: Decimal
    combined_pnl: Decimal
    funding_received: Decimal
    fees_paid: Decimal
    fees_known: bool
    net_funding_hourly: Optional[Decimal]
    net_funding_apr: Optional[Decimal]
    funding_negative: bool
    costs_recovered: Optional[bool]
    hedged: bool
    positions_missing: bool
    level: HealthLevel
    opened_at: Optional[datetime] = None
    notional_usd: Optional[Decimal] = None

    @property
    def holding_hours(self) -> Optional[Decimal]:
        if self.opened_at is None:
            return None
        sekunden = (jetzt() - als_utc(self.opened_at)).total_seconds()
        return (Decimal(sekunden) / Decimal(3600)).quantize(Decimal("0.1"))


def _erweitere_account_service():
    """Ergaenzt AccountService um Uebernahme und gemeinsame Ansicht."""

    def adopt_open_pairs(self, store, positions: Sequence[Position]) -> list[int]:
        """Uebernimmt gehedgte Positionen, die noch kein Paar in der Datenbank haben.

        Damit bekommen von Hand eroeffnete Paare denselben Codepfad wie
        geklickte - inklusive Funding-Zuordnung, Schliessen-Knopf und
        Ergebniszeile. Ein einzelnes Bein wird nicht uebernommen: eine
        ungesicherte Position soll auffallen, nicht stillschweigend zu einem
        Paar erklaert werden.
        """
        neue: list[int] = []
        for erkannt in self.detect_pairs(positions):
            if not erkannt.hedged:
                continue
            if store.open_pair_for(erkannt.symbol) is not None:
                continue

            lang = next(a.position for a in erkannt.legs if a.position.side is Side.LONG)
            kurz = next(a.position for a in erkannt.legs if a.position.side is Side.SHORT)
            pair_id = store.adopt_pair(
                symbol=erkannt.symbol,
                long_venue=lang.venue,
                short_venue=kurz.venue,
                notional_usd=min(lang.notional, kurz.notional),
                legs={lang.venue: lang.size, kurz.venue: kurz.size},
                opened_at=erkannt.opened_at,
            )
            neue.append(pair_id)
            LOG.info("paar_uebernommen", pair_id=pair_id, symbol=erkannt.symbol)
        return neue

    def build_pair_views(
        self,
        store,
        positions: Sequence[Position],
        *,
        funding_rates: Optional[Mapping[tuple[str, str], Decimal]] = None,
        balances: Sequence[Balance] = (),
    ) -> list[PairView]:
        """Baut je offenem Paar eine Ansicht aus Datenbank und Live-Daten."""
        raten = funding_rates or {}
        auslastung = {b.venue: b.margin_usage for b in balances}

        nach_symbol: dict[str, list[Position]] = {}
        for p in positions:
            nach_symbol.setdefault(p.symbol, []).append(p)

        ansichten: list[PairView] = []
        for paar in store.open_pairs() + [
            p for p in store.unfinished_pairs() if p.status == PairState.UNHEDGED.value
        ]:
            beine = [
                p
                for p in nach_symbol.get(paar.symbol, [])
                if p.venue in (paar.long_venue, paar.short_venue)
            ]
            fehlen = len(beine) < 2

            zahlungen = sum((z.amount for z in store.funding_for(paar.id)), Decimal(0))

            fills = store.fills_for(paar.id)
            gebuehren = sum((f["fee"] for f in fills if f["fee"] is not None), Decimal(0))
            # Ein uebernommenes Paar hat keine eigenen Orders - dann sind die
            # Gebuehren unbekannt, nicht null.
            gebuehren_bekannt = bool(fills) and all(f["fee"] is not None for f in fills)

            lang_rate = raten.get((paar.long_venue, paar.symbol))
            kurz_rate = raten.get((paar.short_venue, paar.symbol))
            netto = (
                net_hourly_rate(long_rate_hourly=lang_rate, short_rate_hourly=kurz_rate)
                if lang_rate is not None and kurz_rate is not None
                else None
            )

            ansichten_beine = tuple(
                self._view(p, auslastung.get(p.venue)) for p in sorted(beine, key=lambda x: x.venue)
            )
            delta, anteil = net_delta_usd(beine)

            stufe = HealthLevel.GREEN
            for a in ansichten_beine:
                if a.health.level is HealthLevel.RED:
                    stufe = HealthLevel.RED
                    break
                if a.health.level is HealthLevel.YELLOW:
                    stufe = HealthLevel.YELLOW

            gehedgt = len({p.side for p in beine}) == 2
            funding_negativ = netto is not None and netto <= 0

            # Rot, wenn das Paar nicht mehr verdient, nicht gehedgt ist oder
            # die Positionen zum Eintrag fehlen. Alles drei verlangt eine
            # Entscheidung.
            if fehlen or not gehedgt or funding_negativ:
                stufe = HealthLevel.RED
            elif abs(anteil) >= self._delta_warn and stufe is HealthLevel.GREEN:
                stufe = HealthLevel.YELLOW

            kosten_eingespielt = (
                (zahlungen >= gebuehren) if gebuehren_bekannt else None
            )

            ansichten.append(
                PairView(
                    pair_id=paar.id,
                    symbol=paar.symbol,
                    source=paar.source,
                    status=paar.status,
                    legs=ansichten_beine,
                    net_delta_usd=delta,
                    net_delta_pct=anteil,
                    combined_pnl=sum((p.unrealised_pnl for p in beine), Decimal(0)),
                    funding_received=zahlungen,
                    fees_paid=gebuehren,
                    fees_known=gebuehren_bekannt,
                    net_funding_hourly=netto,
                    net_funding_apr=to_apr(netto) if netto is not None else None,
                    funding_negative=funding_negativ,
                    costs_recovered=kosten_eingespielt,
                    hedged=gehedgt,
                    positions_missing=fehlen,
                    level=stufe,
                    opened_at=paar.opened_at,
                    notional_usd=paar.notional_usd,
                )
            )

        ansichten.sort(key=lambda a: a.symbol)
        return ansichten

    AccountService.adopt_open_pairs = adopt_open_pairs
    AccountService.build_pair_views = build_pair_views


_erweitere_account_service()
