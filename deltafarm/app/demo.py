"""Demomodus: Oberflaeche ohne Schluessel und ohne Netzzugang ansehen.

Startet dieselbe Anwendung, aber mit MockAdaptern statt echten Boersen. Dient
zum Ansehen des Dashboards und als Gegenprobe, ob ein Problem an der Oberflaeche
oder an der Boersenanbindung liegt.

    .venv/bin/python -m app.demo
"""

from __future__ import annotations

from decimal import Decimal

import uvicorn

from app.adapters.mock import MockAdapter
from app.core.funding import RateConvention
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


def build_demo_app():
    adapters = [
        MockAdapter(
            "extended",
            rates=EXTENDED_RATEN,
            mark_prices=MARKEN,
            # Extended liefert Gebuehren erst mit Account-Zugang (ADR-005) -
            # der Demomodus bildet das nach.
            taker_fee=None,
            maker_fee=None,
        ),
        MockAdapter(
            "lighter",
            rates=LIGHTER_RATEN,
            mark_prices=MARKEN,
            convention=RateConvention.ANNUALIZED_PERCENT,
            taker_fee=Decimal("0.0001"),
            maker_fee=Decimal("0"),
        ),
    ]
    return create_app(adapters=adapters, start_background=True)


def main() -> None:  # pragma: no cover
    s = get_settings()
    print(f"Demomodus (Mock-Daten) -> http://{s.host}:{s.port}")
    uvicorn.run(build_demo_app(), host=s.host, port=s.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
