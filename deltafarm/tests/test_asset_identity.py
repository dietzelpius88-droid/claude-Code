"""Gleicher Ticker heisst nicht gleiches Asset.

Anlass: auf einem fremden Werkzeug wurden Funding-Raten zweier
unterschiedlicher Assets verglichen, weil beide denselben Ticker trugen. Ein
Ticker ist eine Zeichenkette, kein Beweis. Bevor zwei Raten verglichen werden,
muss der Preis bestaetigen, dass es sich um dasselbe Asset handelt.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.funding import to_apr
from app.core.opportunities import build_opportunities, price_deviation
from app.models.domain import FundingInfo


def _funding(venue: str, symbol: str, rate: str) -> FundingInfo:
    r = Decimal(rate)
    return FundingInfo(
        venue=venue,
        symbol=symbol,
        native_rate=r,
        native_interval_hours=Decimal(1),
        rate_hourly=r,
        apr=to_apr(r),
        as_of=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )


# --- Abweichungsmass ------------------------------------------------------

def test_gleiche_preise_ergeben_keine_abweichung():
    assert price_deviation(Decimal(64000), Decimal(64000)) == Decimal(0)


def test_kleine_abweichung_wird_relativ_gemessen():
    # 64000 gegen 64320 = 0,5 %
    assert price_deviation(Decimal(64000), Decimal(64320)) == Decimal("0.005")


def test_abweichung_ist_symmetrisch():
    assert price_deviation(Decimal(64000), Decimal(64320)) == price_deviation(
        Decimal(64320), Decimal(64000)
    )


def test_voellig_verschiedene_assets_fallen_drastisch_auf():
    # 64000 gegen 3100 - das sind keine zwei Notierungen desselben Assets.
    abweichung = price_deviation(Decimal(64000), Decimal(3100))
    assert abweichung > Decimal(19)


def test_ohne_preis_keine_aussage():
    assert price_deviation(None, Decimal(64000)) is None
    assert price_deviation(Decimal(64000), None) is None
    assert price_deviation(Decimal(0), Decimal(64000)) is None


# --- Wirkung auf die Paarbildung -----------------------------------------

def _paar(preis_a: str, preis_b: str, **kwargs):
    return build_opportunities(
        [
            _funding("extended", "LIT-PERP", "0.00002"),
            _funding("lighter", "LIT-PERP", "-0.00001"),
        ],
        mark_prices={
            ("extended", "LIT-PERP"): Decimal(preis_a),
            ("lighter", "LIT-PERP"): Decimal(preis_b),
        },
        **kwargs,
    )


def test_aehnliche_preise_ergeben_ein_gueltiges_paar():
    op = _paar("1.50", "1.503")[0]
    assert op.asset_match is True
    assert op.price_deviation == Decimal("0.002")


def test_unterschiedliche_assets_ergeben_kein_gueltiges_paar():
    """Zwei Token namens LIT zu unterschiedlichen Preisen.

    Das Paar wird weiterhin aufgefuehrt - aber als ungueltig markiert, damit
    in der Oberflaeche sichtbar ist, warum es fehlt.
    """
    op = _paar("1.50", "18.30")[0]
    assert op.asset_match is False
    assert op.price_deviation > Decimal(11)


def test_ungueltige_paare_stehen_nicht_oben():
    ops = build_opportunities(
        [
            _funding("extended", "LIT-PERP", "0.00009"),   # waere der beste APR
            _funding("lighter", "LIT-PERP", "-0.00009"),
            _funding("extended", "BTC-PERP", "0.00001"),
            _funding("lighter", "BTC-PERP", "-0.00001"),
        ],
        mark_prices={
            ("extended", "LIT-PERP"): Decimal("1.50"),
            ("lighter", "LIT-PERP"): Decimal("18.30"),
            ("extended", "BTC-PERP"): Decimal(64000),
            ("lighter", "BTC-PERP"): Decimal(64010),
        },
    )
    # Trotz des hoeheren APR darf LIT nicht als bester Vorschlag oben stehen.
    assert ops[0].symbol == "BTC-PERP"
    assert ops[0].asset_match is True
    assert ops[-1].symbol == "LIT-PERP"
    assert ops[-1].asset_match is False


def test_schwelle_ist_konfigurierbar():
    op = _paar("1.50", "1.60", max_price_deviation=Decimal("0.02"))[0]
    # 6,7 % Abweichung, Schwelle 2 % -> kein gueltiges Paar
    assert op.asset_match is False


def test_ohne_preise_bleibt_die_pruefung_unentschieden():
    """Ohne Preise wird nichts behauptet - weder Zustimmung noch Ablehnung.

    Das Paar gilt als nicht bestaetigt und wird entsprechend gekennzeichnet.
    """
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "0.00002"),
        _funding("lighter", "BTC-PERP", "-0.00001"),
    ])
    assert ops[0].price_deviation is None
    assert ops[0].asset_match is None


def test_nur_ein_preis_bekannt_ist_ebenfalls_unentschieden():
    ops = build_opportunities(
        [
            _funding("extended", "BTC-PERP", "0.00002"),
            _funding("lighter", "BTC-PERP", "-0.00001"),
        ],
        mark_prices={("extended", "BTC-PERP"): Decimal(64000)},
    )
    assert ops[0].asset_match is None
