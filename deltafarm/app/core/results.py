"""Ergebnisrechnung eines Paares (Auftrag 6.5).

Beantwortet die Frage, die das ganze Werkzeug rechtfertigt: was hat dieses
Paar unterm Strich gebracht - und damit, was ein Airdrop-Punkt gekostet hat.

Zwei Grundsaetze:
  * Eine unbekannte Gebuehr wird als null gerechnet, aber ausdruecklich
    vermerkt. Eine stillschweigend zu niedrige Kostensumme waere genau die
    Art von Fehler, die man erst nach Monaten bemerkt.
  * Die realisierte APR bezieht sich auf das **Notional**, nicht auf die
    hinterlegte Margin. Mit Hebel ist die Rendite auf das eingesetzte Kapital
    entsprechend hoeher; die Oberflaeche benennt den Bezug deshalb.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

HOURS_PER_YEAR = Decimal(24 * 365)


@dataclass(frozen=True)
class ResultInput:
    symbol: str
    long_venue: str
    short_venue: str
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]
    # Je Fill: venue, side, size, price, fee, closing
    fills: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    funding_payments: Sequence[Decimal] = field(default_factory=tuple)
    notional_usd: Decimal = Decimal(0)


@dataclass(frozen=True)
class PairResult:
    symbol: str
    closed: bool
    funding_received: Decimal
    fees_paid: Decimal
    price_pnl: Decimal
    net_result: Decimal
    holding_hours: Decimal
    realized_apr: Optional[Decimal] = None
    # True, wenn mindestens eine Gebuehr unbekannt war.
    fees_incomplete: bool = False


def _mittel(fills: Sequence[Mapping[str, Any]], venue: str, closing: bool) -> tuple[Decimal, Decimal]:
    """Mengengewichteter Durchschnittspreis und Gesamtmenge einer Seite."""
    menge = Decimal(0)
    wert = Decimal(0)
    for f in fills:
        if f["venue"] != venue or bool(f.get("closing", False)) != closing:
            continue
        groesse = Decimal(f["size"])
        menge += groesse
        wert += groesse * Decimal(f["price"])
    schnitt = (wert / menge) if menge > 0 else Decimal(0)
    return schnitt, menge


def compute_pair_result(e: ResultInput) -> PairResult:
    """Rechnet Funding, Gebuehren, Preis-PnL, Netto und realisierte APR."""
    funding = sum(e.funding_payments, Decimal(0))

    gebuehren = Decimal(0)
    unvollstaendig = False
    for f in e.fills:
        gebuehr = f.get("fee")
        if gebuehr is None:
            unvollstaendig = True
            continue
        gebuehren += Decimal(gebuehr)

    # Preis-PnL je Bein: nur, wenn es auch eine Gegenbuchung gibt.
    preis_pnl = Decimal(0)
    for venue, richtung in ((e.long_venue, Decimal(1)), (e.short_venue, Decimal(-1))):
        einstieg, menge_auf = _mittel(e.fills, venue, closing=False)
        ausstieg, menge_zu = _mittel(e.fills, venue, closing=True)
        if menge_auf <= 0 or menge_zu <= 0:
            continue
        # Nur die tatsaechlich glattgestellte Menge zaehlt.
        menge = min(menge_auf, menge_zu)
        preis_pnl += richtung * (ausstieg - einstieg) * menge

    netto = funding + preis_pnl - gebuehren

    stunden = Decimal(0)
    if e.opened_at is not None and e.closed_at is not None:
        sekunden = Decimal((e.closed_at - e.opened_at).total_seconds())
        stunden = max(Decimal(0), sekunden / Decimal(3600))

    apr: Optional[Decimal] = None
    if e.closed_at is not None and stunden > 0 and e.notional_usd > 0:
        rendite = netto / e.notional_usd
        apr = rendite * (HOURS_PER_YEAR / stunden)

    return PairResult(
        symbol=e.symbol,
        closed=e.closed_at is not None,
        funding_received=funding,
        fees_paid=gebuehren,
        price_pnl=preis_pnl,
        net_result=netto,
        holding_hours=stunden,
        realized_apr=apr,
        fees_incomplete=unvollstaendig,
    )
