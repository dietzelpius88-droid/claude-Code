"""Dienstschicht: Marktdaten einsammeln, pruefen, zu Paaren verdichten."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Optional, Sequence

import structlog

from app.adapters.base import AdapterError
from app.core.funding import infer_interval_hours, interval_matches
from app.core.opportunities import build_opportunities
from app.models.domain import FundingInfo, Market, Opportunity, VenueStatus

LOG = structlog.get_logger(__name__)

# Ab diesem Verhaeltnis zwischen den mittleren Ratenbetraegen zweier Boersen
# ist ein Einheitenfehler wahrscheinlicher als ein echter Marktunterschied.
# Bruch gegen Prozent waere Faktor 100 - 20 faengt das sicher, ohne bei
# normalen Unterschieden anzuschlagen.
UNIT_MISMATCH_FACTOR = Decimal(20)


@dataclass(frozen=True)
class IntervalCheck:
    """Ergebnis der Startpruefung aus ADR-004."""

    venue: str
    symbol: str
    declared_hours: Decimal
    observed_hours: Optional[Decimal]
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Snapshot:
    funding: tuple[FundingInfo, ...]
    opportunities: tuple[Opportunity, ...]
    venues: tuple[VenueStatus, ...]
    warnings: tuple[str, ...] = ()
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MarketDataService:
    """Haelt die Adapter, holt Daten nebenlaeufig und verdichtet sie."""

    def __init__(self, adapters: Sequence, *, environment: str = "testnet") -> None:
        self._adapters = list(adapters)
        self._environment = environment
        self._snapshot: Optional[Snapshot] = None
        self._rules_cache: dict[tuple[str, str], object] = {}

    @property
    def adapters(self) -> list:
        return list(self._adapters)

    # --- Startpruefung ----------------------------------------------------

    async def verify_intervals(self, symbols: Sequence[str]) -> list[IntervalCheck]:
        """Prueft je Boerse und Symbol das deklarierte Funding-Intervall.

        ADR-004: Statt zu glauben, was die Doku sagt, messen wir die Abstaende
        der tatsaechlichen Funding-Zeitstempel. Ein Faktor 8 faellt hier auf,
        bevor er in die Vergleichsansicht gelangt.
        """
        ergebnisse: list[IntervalCheck] = []
        for adapter in self._adapters:
            deklariert = getattr(adapter, "declared_interval_hours", Decimal(1))
            for symbol in symbols:
                try:
                    stempel = await adapter.funding_timestamps(symbol, lookback_hours=48)
                    gemessen = infer_interval_hours(stempel)
                except AdapterError as exc:
                    ergebnisse.append(
                        IntervalCheck(
                            venue=adapter.name,
                            symbol=symbol,
                            declared_hours=deklariert,
                            observed_hours=None,
                            ok=False,
                            detail=f"nicht pruefbar: {exc}",
                        )
                    )
                    continue

                passt = interval_matches(deklariert, gemessen)
                ergebnisse.append(
                    IntervalCheck(
                        venue=adapter.name,
                        symbol=symbol,
                        declared_hours=deklariert,
                        observed_hours=gemessen,
                        ok=passt,
                        detail=(
                            ""
                            if passt
                            else (
                                f"deklariert {deklariert} h, gemessen {gemessen} h - "
                                "die Normalisierung waere falsch"
                            )
                        ),
                    )
                )
        return ergebnisse

    # --- Plausibilitaet ---------------------------------------------------

    @staticmethod
    def unit_warnings(funding: Sequence[FundingInfo]) -> list[str]:
        """Warnt, wenn zwei Boersen um Groessenordnungen auseinanderliegen.

        Lighter dokumentiert die Einheit seines Feldes 'rate' nicht
        (RESEARCH.md, OFFEN-14). Bruch gegen Prozent waere Faktor 100 - und
        wuerde die ganze Vergleichsansicht unbrauchbar machen, ohne dass man es
        der Tabelle ansieht. Deshalb diese Gegenprobe.
        """
        je_venue: dict[str, list[Decimal]] = {}
        for f in funding:
            je_venue.setdefault(f.venue, []).append(abs(f.rate_hourly))

        mittel: dict[str, Decimal] = {}
        for venue, werte in je_venue.items():
            werte = sorted(w for w in werte if w > 0)
            if werte:
                mittel[venue] = werte[len(werte) // 2]

        warnungen: list[str] = []
        namen = sorted(mittel)
        for i, a in enumerate(namen):
            for b in namen[i + 1 :]:
                gross, klein = max(mittel[a], mittel[b]), min(mittel[a], mittel[b])
                if klein > 0 and gross / klein >= UNIT_MISMATCH_FACTOR:
                    warnungen.append(
                        f"Funding-Raten von {a} und {b} unterscheiden sich im Mittel um "
                        f"Faktor {(gross / klein).quantize(Decimal('1'))}. Das deutet eher auf "
                        f"unterschiedliche Einheiten (Bruch gegen Prozent) als auf einen "
                        f"echten Marktunterschied hin - bitte pruefen, bevor darauf gehandelt wird."
                    )
        return warnungen

    # --- Datenabruf -------------------------------------------------------

    async def _funding_for(self, adapter, symbol: str) -> Optional[FundingInfo]:
        try:
            return await adapter.get_funding(symbol)
        except AdapterError as exc:
            LOG.warning("funding_nicht_abrufbar", venue=adapter.name, symbol=symbol, fehler=str(exc))
            return None

    async def collect_funding(self, symbols: Sequence[str]) -> list[FundingInfo]:
        aufgaben = [
            self._funding_for(adapter, symbol)
            for adapter in self._adapters
            for symbol in symbols
        ]
        ergebnisse = await asyncio.gather(*aufgaben)
        return [e for e in ergebnisse if e is not None]

    async def _markets_for(self, adapter) -> list[Market]:
        try:
            return await adapter.list_markets()
        except AdapterError as exc:
            LOG.warning("maerkte_nicht_abrufbar", venue=adapter.name, fehler=str(exc))
            return []

    async def collect_mark_prices(self) -> dict[tuple[str, str], Decimal]:
        """Mark-Preise je Boerse und Symbol.

        Grundlage der Asset-Pruefung: ein gleicher Ticker allein belegt nicht,
        dass zwei Listings dasselbe Asset meinen.
        """
        listen = await asyncio.gather(*(self._markets_for(a) for a in self._adapters))
        preise: dict[tuple[str, str], Decimal] = {}
        for liste in listen:
            for m in liste:
                if m.mark_price is not None:
                    preise[(m.venue, m.symbol)] = m.mark_price
        return preise

    @staticmethod
    def asset_warnings(opportunities: Sequence[Opportunity]) -> list[str]:
        """Warnt, wenn zwei Listings mit gleichem Ticker nicht zusammenpassen."""
        warnungen: list[str] = []
        for o in opportunities:
            if o.asset_match is False:
                warnungen.append(
                    f"{o.symbol}: Die Mark-Preise von {o.long_venue} und {o.short_venue} "
                    f"weichen um {o.price_deviation:.0%} voneinander ab. Der gleiche Ticker "
                    f"steht hier offenbar fuer zwei verschiedene Assets - die Funding-Raten "
                    f"sind nicht vergleichbar und das Paar ist nicht handelbar."
                )
        return warnungen

    async def venue_status(self) -> list[VenueStatus]:
        stati: list[VenueStatus] = []
        for adapter in self._adapters:
            try:
                await adapter.list_markets()
                erreichbar, detail = True, None
            except AdapterError as exc:
                erreichbar, detail = False, str(exc)
            stati.append(
                VenueStatus(
                    name=adapter.name,
                    supports_trading=bool(getattr(adapter, "supports_trading", False)),
                    reachable=erreichbar,
                    environment=self._environment,
                    detail=detail,
                )
            )
        return stati

    async def refresh(
        self,
        symbols: Sequence[str],
        *,
        taker_fees: Optional[Mapping[str, Decimal]] = None,
    ) -> Snapshot:
        """Holt Funding und Status und baut die Paarliste."""
        funding, stati, mark_prices = await asyncio.gather(
            self.collect_funding(symbols),
            self.venue_status(),
            self.collect_mark_prices(),
        )

        # Gebuehren: was die Boerse oeffentlich mitliefert, nutzen wir. Extended
        # liefert sie accountabhaengig und damit erst ab Phase 2 (ADR-005).
        gebuehren = dict(taker_fees or {})
        if not gebuehren:
            for adapter in self._adapters:
                for symbol in symbols:
                    schluessel = (adapter.name, symbol)
                    if schluessel in self._rules_cache:
                        regeln = self._rules_cache[schluessel]
                    else:
                        try:
                            regeln = await adapter.get_symbol_rules(symbol)
                        except AdapterError:
                            continue
                        self._rules_cache[schluessel] = regeln
                    if getattr(regeln, "taker_fee", None) is not None:
                        gebuehren[adapter.name] = regeln.taker_fee
                    break  # eine Abfrage je Boerse genuegt fuer die Gebuehr

        chancen = build_opportunities(
            funding, taker_fees=gebuehren, mark_prices=mark_prices
        )

        snapshot = Snapshot(
            funding=tuple(funding),
            opportunities=tuple(chancen),
            venues=tuple(stati),
            warnings=tuple(self.unit_warnings(funding) + self.asset_warnings(chancen)),
        )
        self._snapshot = snapshot
        return snapshot

    @property
    def last_snapshot(self) -> Optional[Snapshot]:
        return self._snapshot

    async def aclose(self) -> None:
        for adapter in self._adapters:
            schliessen = getattr(adapter, "aclose", None)
            if schliessen is not None:
                await schliessen()
