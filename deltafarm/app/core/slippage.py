"""Slippage und Tiefe aus einem Orderbuch.

Die Schaetzung laeuft das Buch Stufe fuer Stufe ab. Reicht die Tiefe nicht,
wird das gemeldet - nicht extrapoliert. Eine erfundene Stufe hinter dem Ende
des Buches waere genau dort falsch, wo es darauf ankommt.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.models.domain import OrderBook, Side


class SlippageError(ValueError):
    """Das Orderbuch laesst keine Aussage zu."""


@dataclass(frozen=True)
class FillEstimate:
    side: Side
    requested_size: Decimal
    filled_size: Decimal
    average_price: Optional[Decimal]
    best_price: Decimal
    slippage: Decimal  # relativer Abstand zum besten Preis, immer >= 0
    notional: Decimal
    fully_filled: bool

    @property
    def remaining(self) -> Decimal:
        return self.requested_size - self.filled_size


def estimate_fill(book: OrderBook, side: Side, size: Decimal) -> FillEstimate:
    """Schaetzt Durchschnittspreis und Slippage fuer eine Marktorder.

    Ein Long kauft gegen die Briefseite, ein Short verkauft gegen die
    Geldseite. Slippage ist immer ein Kostenmass und damit nie negativ.
    """
    if size <= 0:
        raise SlippageError(f"Die Menge muss positiv sein, war {size}.")

    stufen = book.asks if side is Side.LONG else book.bids
    if not stufen:
        seite = "Brief" if side is Side.LONG else "Geld"
        raise SlippageError(
            f"{book.venue} {book.symbol}: {seite}seite des Orderbuchs ist leer - "
            "keine Schaetzung moeglich."
        )

    best = stufen[0].price
    if best <= 0:
        raise SlippageError(f"{book.venue} {book.symbol}: ungueltiger Preis {best}.")

    offen = size
    gefuellt = Decimal(0)
    kosten = Decimal(0)

    for stufe in stufen:
        if offen <= 0:
            break
        menge = min(offen, stufe.size)
        kosten += menge * stufe.price
        gefuellt += menge
        offen -= menge

    schnitt = (kosten / gefuellt) if gefuellt > 0 else None

    if schnitt is None:
        slippage = Decimal(0)
    elif side is Side.LONG:
        slippage = (schnitt - best) / best
    else:
        slippage = (best - schnitt) / best

    return FillEstimate(
        side=side,
        requested_size=size,
        filled_size=gefuellt,
        average_price=schnitt,
        best_price=best,
        # Rundungsreste duerfen kein negatives Kostenmass erzeugen.
        slippage=max(Decimal(0), slippage),
        notional=kosten,
        fully_filled=offen <= 0,
    )
