"""Neu ausrichten eines Paares.

Ein Rest-Delta laesst sich auf zwei Arten abbauen: das kleinere Bein
aufstocken oder das groessere verkleinern. Beides kostet Gebuehren und
veraendert die Position - Aufstocken braucht Margin und bringt mehr Volumen
fuer die Punkte, Verkleinern gibt Margin frei und senkt das Open Interest.

Deshalb rechnet dieses Modul **beide** Wege durch und entscheidet keinen.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.core.sizing import floor_to_lot
from app.models.domain import Side


class RebalanceError(ValueError):
    """Die Ausrichtung laesst sich nicht rechnen."""


@dataclass(frozen=True)
class RebalanceLeg:
    """Ein moeglicher Weg, das Delta abzubauen."""

    venue: str
    side: Side
    size: Decimal
    reduce_only: bool
    estimated_fee: Optional[Decimal]
    resulting_size: Decimal
    resulting_notional: Decimal


@dataclass(frozen=True)
class RebalancePlan:
    symbol: str
    difference: Decimal
    balanced: bool
    increase: Optional[RebalanceLeg] = None
    decrease: Optional[RebalanceLeg] = None


def plan_rebalance(
    *,
    symbol: str,
    long_venue: str,
    short_venue: str,
    long_size: Decimal,
    short_size: Decimal,
    mark_price: Decimal,
    lot_size: Decimal,
    long_taker_fee: Optional[Decimal] = None,
    short_taker_fee: Optional[Decimal] = None,
) -> RebalancePlan:
    """Rechnet beide Wege durch, das Rest-Delta abzubauen."""
    if long_size < 0 or short_size < 0:
        raise RebalanceError(
            f"Negative Groessen: long {long_size}, short {short_size}."
        )
    if mark_price <= 0:
        raise RebalanceError(f"Ungueltiger Mark-Preis {mark_price}.")
    if lot_size <= 0:
        raise RebalanceError(f"Ungueltige Lot-Size {lot_size}.")

    roh = abs(long_size - short_size)
    # Was unterhalb des Rasters liegt, laesst sich gar nicht handeln.
    differenz = floor_to_lot(roh, lot_size)

    if differenz <= 0:
        return RebalancePlan(symbol=symbol, difference=Decimal(0), balanced=True)

    long_groesser = long_size > short_size
    gross, klein = max(long_size, short_size), min(long_size, short_size)

    def gebuehr(satz: Optional[Decimal]) -> Optional[Decimal]:
        return None if satz is None else differenz * mark_price * satz

    if long_groesser:
        # Aufstocken heisst: mehr Short dazukaufen.
        aufstocken = RebalanceLeg(
            venue=short_venue,
            side=Side.SHORT,
            size=differenz,
            reduce_only=False,
            estimated_fee=gebuehr(short_taker_fee),
            resulting_size=gross,
            resulting_notional=gross * mark_price,
        )
        # Verkleinern heisst: einen Teil des Long glattstellen.
        verkleinern = RebalanceLeg(
            venue=long_venue,
            side=Side.SHORT,
            size=differenz,
            reduce_only=True,
            estimated_fee=gebuehr(long_taker_fee),
            resulting_size=klein,
            resulting_notional=klein * mark_price,
        )
    else:
        aufstocken = RebalanceLeg(
            venue=long_venue,
            side=Side.LONG,
            size=differenz,
            reduce_only=False,
            estimated_fee=gebuehr(long_taker_fee),
            resulting_size=gross,
            resulting_notional=gross * mark_price,
        )
        verkleinern = RebalanceLeg(
            venue=short_venue,
            side=Side.LONG,
            size=differenz,
            reduce_only=True,
            estimated_fee=gebuehr(short_taker_fee),
            resulting_size=klein,
            resulting_notional=klein * mark_price,
        )

    return RebalancePlan(
        symbol=symbol,
        difference=differenz,
        balanced=False,
        increase=aufstocken,
        decrease=verkleinern,
    )
