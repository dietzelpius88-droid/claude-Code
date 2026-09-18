"""Break-even-Rechnung nach Auftrag Abschnitt 6.2.

    (taker_fee_A + taker_fee_B) * 2 + geschaetzter Spread
    geteilt durch die erwartete stuendliche Netto-Funding-Rate
    = Mindesthaltedauer in Stunden.

Alle Groessen sind relative Anteile des Notionals (0,0004 = 4 Basispunkte).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional


def round_trip_cost(
    *,
    taker_fee_a: Decimal,
    taker_fee_b: Decimal,
    spread: Decimal = Decimal(0),
) -> Decimal:
    """Gebuehren beider Beine fuer Eroeffnen und Schliessen, plus Spread."""
    return (taker_fee_a + taker_fee_b) * 2 + spread


def breakeven_hours(*, cost: Decimal, net_rate_hourly: Decimal) -> Optional[Decimal]:
    """Wie lange das Paar gehalten werden muss, bis es die Kosten eingespielt hat.

    None bedeutet: nie. Bei einer Netto-Rate von null oder darunter amortisiert
    sich die Position nicht - das ist ein Abbruchgrund, kein grosser Zahlenwert.
    """
    if cost < 0:
        raise ValueError(f"Kosten koennen nicht negativ sein, waren {cost}.")
    if net_rate_hourly <= 0:
        return None
    return cost / net_rate_hourly
