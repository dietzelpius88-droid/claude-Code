"""Demomodus: Oberflaeche ohne Schluessel und ohne Netzzugang ansehen.

Startet dieselbe Anwendung, aber mit MockAdaptern statt echten Boersen. Dient
zum Ansehen des Dashboards und als Gegenprobe, ob ein Problem an der Oberflaeche
oder an der Boersenanbindung liegt.

    .venv/bin/python -m app.demo
"""

from __future__ import annotations

from decimal import Decimal

import uvicorn

from datetime import datetime, timedelta, timezone

from app.adapters.mock import MockAdapter
from app.core.funding import RateConvention
from app.models.domain import Balance, FundingPayment, Position, Side
from app.config import get_settings
from app.main import create_app

# Erfundene, aber plausible Raten: teils positiv, teils negativ, in der
# Groessenordnung echter stuendlicher Funding-Raten.
EXTENDED_RATEN = {
    "BTC-PERP": Decimal("0.0000125"),
    "ETH-PERP": Decimal("0.0000090"),
    "SOL-PERP": Decimal("0.0000310"),
    "AVAX-PERP": Decimal("-0.0000040"),
    "LINK-PERP": Decimal("0.0000155"),
    "DOGE-PERP": Decimal("0.0000220"),
    "XRP-PERP": Decimal("0.0000075"),
    "SUI-PERP": Decimal("0.0000410"),
}
# Lighter gibt die Rate als Prozent pro Jahr an - dieselben Groessenordnungen,
# nur anders aufgeschrieben (0,0000065 je Stunde entsprechen 5,69 % p. a.).
LIGHTER_RATEN = {
    "BTC-PERP": Decimal("-5.69"),
    "ETH-PERP": Decimal("10.51"),
    "SOL-PERP": Decimal("-15.77"),
    "AVAX-PERP": Decimal("22.78"),
    "LINK-PERP": Decimal("12.26"),
    "DOGE-PERP": Decimal("-7.88"),
    "XRP-PERP": Decimal("7.01"),
    "SUI-PERP": Decimal("4.38"),
}
MARKEN = {
    "BTC-PERP": Decimal("64000"),
    "ETH-PERP": Decimal("3100"),
    "SOL-PERP": Decimal("148"),
    "AVAX-PERP": Decimal("27"),
    "LINK-PERP": Decimal("16"),
    "DOGE-PERP": Decimal("0.21"),
    "XRP-PERP": Decimal("2.4"),
    "SUI-PERP": Decimal("3.1"),
}


class DemoKonto(MockAdapter):
    """MockAdapter mit Kontodaten, damit Bereich C etwas zeigt.

    Die Positionen bilden ein von Hand eroeffnetes Paar nach: long auf
    Extended, short auf Lighter, mit einem kleinen Rest-Delta.
    """

    has_account_access = True

    def __init__(self, name: str, *, position: Position | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self._position = position

    async def get_balance(self) -> Balance:
        return Balance(
            venue=self.name,
            collateral="USD",
            equity=Decimal("12500.00"),
            available=Decimal("8200.00"),
            unrealised_pnl=Decimal("42.50"),
            initial_margin=Decimal("4300.00"),
            as_of=datetime.now(timezone.utc),
        )

    async def get_positions(self) -> list[Position]:
        return [self._position] if self._position else []

    async def get_funding_payments(self, since):
        start = datetime.now(timezone.utc) - timedelta(hours=6)
        return [
            FundingPayment(
                venue=self.name,
                symbol="SOL-PERP",
                amount=Decimal("0.42") if self.name == "lighter" else Decimal("-0.18"),
                rate=Decimal("15.77") if self.name == "lighter" else Decimal("0.0000031"),
                position_size=Decimal("28"),
                timestamp=start + timedelta(hours=i),
                confirmed=True,
                external_id=f"{self.name}-demo-{i}",
            )
            for i in range(6)
        ]


def _demo_position(venue: str, side: Side, notional: str, liq: str) -> Position:
    preis = MARKEN["SOL-PERP"]
    groesse = Decimal(notional) / preis
    return Position(
        venue=venue,
        symbol="SOL-PERP",
        side=side,
        size=groesse,
        entry_price=preis - Decimal("1.20") if side is Side.LONG else preis + Decimal("0.90"),
        mark_price=preis,
        notional=Decimal(notional),
        unrealised_pnl=Decimal("33.60") if side is Side.LONG else Decimal("-25.20"),
        liquidation_price=Decimal(liq),
        funding_paid=Decimal("2.52") if venue == "lighter" else None,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=19, minutes=30),
        as_of=datetime.now(timezone.utc),
    )


def build_demo_app():
    adapters = [
        DemoKonto(
            "extended",
            supports_trading=True,
            lot_size=Decimal("0.001"),
            min_notional=Decimal(10),
            rates=EXTENDED_RATEN,
            mark_prices=MARKEN,
            # Extended liefert Gebuehren erst mit Account-Zugang (ADR-005) -
            # der Demomodus bildet das nach.
            taker_fee=None,
            maker_fee=None,
            position=_demo_position("extended", Side.LONG, "4180", "104.50"),
        ),
        DemoKonto(
            "lighter",
            supports_trading=True,
            lot_size=Decimal("0.001"),
            min_notional=Decimal(10),
            rates=LIGHTER_RATEN,
            mark_prices=MARKEN,
            convention=RateConvention.ANNUALIZED_PERCENT,
            taker_fee=Decimal("0.0001"),
            maker_fee=Decimal("0"),
            position=_demo_position("lighter", Side.SHORT, "4100", "201.30"),
        ),
    ]
    return create_app(adapters=adapters, start_background=True)


def main() -> None:  # pragma: no cover
    s = get_settings()
    print(f"Demomodus (Mock-Daten) -> http://{s.host}:{s.port}")
    uvicorn.run(build_demo_app(), host=s.host, port=s.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
