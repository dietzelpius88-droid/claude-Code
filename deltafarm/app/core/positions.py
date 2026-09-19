"""Positionsauswertung: Liquidationsabstand, Margin-Auslastung, Rest-Delta.

Der Abstand zur Liquidation wird als Anteil der noetigen Preisbewegung
angegeben, nicht als Preisdifferenz - nur so sind zwei Boersen mit
verschiedenen Preisniveaus vergleichbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from app.models.domain import HealthLevel, Position, PositionHealth, Side


@dataclass(frozen=True)
class LiquidationThresholds:
    """Schwellen der Ampel, als Anteil der noetigen Preisbewegung."""

    green: Decimal = Decimal("0.25")
    yellow: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        if self.green <= self.yellow:
            raise ValueError(
                f"Die gruene Schwelle ({self.green}) muss ueber der gelben "
                f"({self.yellow}) liegen."
            )


@dataclass(frozen=True)
class MarginThresholds:
    """Schwellen fuer die Margin-Auslastung, als Anteil des Eigenkapitals."""

    yellow: Decimal = Decimal("0.60")
    red: Decimal = Decimal("0.85")

    def __post_init__(self) -> None:
        if self.red <= self.yellow:
            raise ValueError(
                f"Die rote Schwelle ({self.red}) muss ueber der gelben "
                f"({self.yellow}) liegen."
            )


def liquidation_distance(position: Position) -> Optional[Decimal]:
    """Wie weit sich der Preis bewegen muss, bis liquidiert wird.

    Als Anteil vom Mark-Preis. None, wenn die Boerse keinen Liquidationspreis
    nennt - dann wird nichts geschaetzt.
    """
    if position.liquidation_price is None or position.mark_price <= 0:
        return None

    if position.side is Side.LONG:
        abstand = position.mark_price - position.liquidation_price
    else:
        abstand = position.liquidation_price - position.mark_price

    # Ist der Liquidationspreis bereits durchbrochen, ist der Abstand null -
    # nicht negativ. Ein negativer Wert waere in der Ampel nicht lesbar.
    return max(Decimal(0), abstand) / position.mark_price


def assess_position(
    position: Position,
    *,
    margin_usage: Optional[Decimal] = None,
    thresholds: Optional[LiquidationThresholds] = None,
    margin_thresholds: Optional[MarginThresholds] = None,
) -> PositionHealth:
    """Bewertet eine Position. Das schlechtere der beiden Kriterien gewinnt."""
    liq = thresholds or LiquidationThresholds()
    mar = margin_thresholds or MarginThresholds()

    abstand = liquidation_distance(position)

    if abstand is None:
        # Unbekannt ist nicht dasselbe wie sicher.
        return PositionHealth(
            venue=position.venue,
            symbol=position.symbol,
            liquidation_distance=None,
            margin_usage=margin_usage,
            level=HealthLevel.RED,
            detail="Die Boerse nennt keinen Liquidationspreis - Abstand unbekannt.",
        )

    if abstand >= liq.green:
        stufe = HealthLevel.GREEN
    elif abstand >= liq.yellow:
        stufe = HealthLevel.YELLOW
    else:
        stufe = HealthLevel.RED

    hinweis: Optional[str] = None
    if margin_usage is not None:
        if margin_usage >= mar.red:
            stufe = HealthLevel.RED
            hinweis = f"Margin-Auslastung {margin_usage:.0%}"
        elif margin_usage >= mar.yellow and stufe is HealthLevel.GREEN:
            stufe = HealthLevel.YELLOW
            hinweis = f"Margin-Auslastung {margin_usage:.0%}"

    return PositionHealth(
        venue=position.venue,
        symbol=position.symbol,
        liquidation_distance=abstand,
        margin_usage=margin_usage,
        level=stufe,
        detail=hinweis,
    )


def net_delta_usd(positions: Sequence[Position]) -> tuple[Decimal, Decimal]:
    """Rest-Delta eines Paares in USD und als Anteil.

    Der Anteil bezieht sich auf das kleinere Bein - das ist die Groesse, die
    tatsaechlich gehedgt ist. Auf das groessere bezogen sieht ein
    Ungleichgewicht kleiner aus, als es ist.
    """
    if not positions:
        return Decimal(0), Decimal(0)

    delta = sum((p.signed_notional for p in positions), Decimal(0))

    lang = sum((p.notional for p in positions if p.side is Side.LONG), Decimal(0))
    kurz = sum((p.notional for p in positions if p.side is Side.SHORT), Decimal(0))
    bezug = min(lang, kurz) if lang > 0 and kurz > 0 else max(lang, kurz)

    anteil = (delta / bezug) if bezug > 0 else Decimal(0)
    return delta, anteil
