"""Sizing eines delta-neutralen Paares.

Zwei Regeln bestimmen alles Weitere:

1. Beide Beine bekommen **exakt dieselbe Menge**. Sonst ist das Paar schon beim
   Oeffnen nicht delta-neutral, und der Fehler laesst sich nachtraeglich nur
   mit weiteren Gebuehren korrigieren.
2. Gerundet wird **abwaerts**. Eine zu grosse Position sprengt die Margin; eine
   zu kleine kostet nur etwas Ertrag.

Das Rest-Delta entsteht danach nicht mehr aus der Menge, sondern aus den
unterschiedlichen Mark-Preisen beider Boersen.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from app.models.domain import SymbolRules


class SizingError(ValueError):
    """Die gewuenschte Groesse laesst sich nicht sauber auf beide Boersen legen."""


def common_lot_size(a: Decimal, b: Decimal) -> Decimal:
    """Kleinstes Raster, das zu beiden Boersen passt.

    Meist ist das schlicht die groebere der beiden Lot-Sizes, weil Boersen
    Zehnerpotenzen verwenden. Sind die Raster nicht durcheinander teilbar
    (etwa 0,02 und 0,03), waere die groebere Groesse auf der anderen Boerse
    ungueltig - dann ist das kleinste gemeinsame Vielfache noetig.
    """
    if a <= 0 or b <= 0:
        raise ValueError(f"Lot-Size muss positiv sein, war {a} und {b}.")

    fa, fb = Fraction(a), Fraction(b)
    # kgV zweier Brueche: (Zaehler-kgV) / (Nenner-ggT)
    zaehler_kgv = fa.numerator * fb.numerator // _ggt(fa.numerator, fb.numerator)
    nenner_ggt = _ggt(fa.denominator, fb.denominator)
    kgv = Fraction(zaehler_kgv, nenner_ggt)
    return Decimal(kgv.numerator) / Decimal(kgv.denominator)


def _ggt(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def floor_to_lot(menge: Decimal, lot: Decimal) -> Decimal:
    """Auf ein Vielfaches des Rasters abrunden."""
    if lot <= 0:
        raise ValueError(f"Lot-Size muss positiv sein, war {lot}.")
    return (menge / lot).to_integral_value(rounding="ROUND_FLOOR") * lot


@dataclass(frozen=True)
class Sizing:
    """Ergebnis der Groessenrechnung, wie es in der Vorschau steht."""

    symbol: str
    requested_notional_usd: Decimal
    size: Decimal  # identisch auf beiden Beinen
    lot_size: Decimal
    long_venue: str
    short_venue: str
    long_mark: Decimal
    short_mark: Decimal
    long_notional: Decimal
    short_notional: Decimal
    residual_delta_usd: Decimal
    residual_delta_pct: Decimal
    estimated_open_fees: Optional[Decimal] = None
    estimated_round_trip_fees: Optional[Decimal] = None

    @property
    def long_size(self) -> Decimal:
        return self.size

    @property
    def short_size(self) -> Decimal:
        return self.size


def compute_sizing(
    *,
    notional_usd: Decimal,
    long_rules: SymbolRules,
    short_rules: SymbolRules,
    long_mark: Decimal,
    short_mark: Decimal,
) -> Sizing:
    """Rechnet einen USD-Betrag in eine fuer beide Boersen gueltige Menge um."""
    if notional_usd <= 0:
        raise SizingError(f"Der Betrag muss positiv sein, war {notional_usd}.")
    if long_mark <= 0 or short_mark <= 0:
        raise SizingError(
            f"Ungueltiger Mark-Preis: {long_rules.venue}={long_mark}, "
            f"{short_rules.venue}={short_mark}."
        )

    lot = common_lot_size(long_rules.lot_size, short_rules.lot_size)

    # Der Referenzpreis ist der hoehere der beiden: damit bleibt der tatsaechlich
    # eingesetzte Betrag auf beiden Seiten unter dem gewuenschten.
    referenz = max(long_mark, short_mark)
    menge = floor_to_lot(notional_usd / referenz, lot)

    if menge <= 0:
        raise SizingError(
            f"{notional_usd} USD ergeben bei einem Raster von {lot} keine handelbare "
            f"Menge. Kleinster moeglicher Betrag: etwa {(lot * referenz):.2f} USD."
        )

    long_notional = menge * long_mark
    short_notional = menge * short_mark

    for regeln, notional in ((long_rules, long_notional), (short_rules, short_notional)):
        if notional < regeln.min_notional:
            raise SizingError(
                f"{regeln.venue}: {notional:.2f} USD liegen unter der Mindestgroesse "
                f"von {regeln.min_notional} USD."
            )

    # Gleiche Menge, unterschiedliche Preise - daraus entsteht das Rest-Delta.
    delta = long_notional - short_notional
    bezug = min(long_notional, short_notional)
    delta_pct = (delta / bezug) if bezug > 0 else Decimal(0)

    gebuehren = None
    round_trip = None
    if long_rules.taker_fee is not None and short_rules.taker_fee is not None:
        gebuehren = long_notional * long_rules.taker_fee + short_notional * short_rules.taker_fee
        round_trip = gebuehren * 2

    return Sizing(
        symbol=long_rules.symbol,
        requested_notional_usd=notional_usd,
        size=menge,
        lot_size=lot,
        long_venue=long_rules.venue,
        short_venue=short_rules.venue,
        long_mark=long_mark,
        short_mark=short_mark,
        long_notional=long_notional,
        short_notional=short_notional,
        residual_delta_usd=delta,
        residual_delta_pct=delta_pct,
        estimated_open_fees=gebuehren,
        estimated_round_trip_fees=round_trip,
    )
