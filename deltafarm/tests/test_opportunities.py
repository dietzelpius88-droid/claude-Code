"""Paarbildung: Richtung, Netto-APR, Sortierung."""

from datetime import datetime, timezone
from decimal import Decimal

from app.core.funding import to_apr
from app.core.opportunities import build_opportunities
from app.models.domain import FundingInfo


def _funding(venue: str, symbol: str, rate_hourly: str) -> FundingInfo:
    r = Decimal(rate_hourly)
    return FundingInfo(
        venue=venue,
        symbol=symbol,
        native_rate=r,
        native_interval_hours=Decimal(1),
        rate_hourly=r,
        apr=to_apr(r),
        as_of=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )


def test_richtung_short_auf_der_hoeheren_rate():
    """Wir wollen auf der Seite stehen, die Funding empfaengt.

    Bei positiver Rate zahlt Long an Short, also gehoert die Boerse mit der
    hoeheren Rate auf die Short-Seite.
    """
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "0.00002"),
        _funding("lighter", "BTC-PERP", "-0.00001"),
    ])
    assert len(ops) == 1
    op = ops[0]
    assert op.short_venue == "extended"
    assert op.long_venue == "lighter"
    assert op.net_rate_hourly == Decimal("0.00003")


def test_richtung_dreht_sich_mit_den_raten():
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "-0.00004"),
        _funding("lighter", "BTC-PERP", "0.00001"),
    ])
    op = ops[0]
    assert op.short_venue == "lighter"
    assert op.long_venue == "extended"
    assert op.net_rate_hourly == Decimal("0.00005")


def test_netto_rate_ist_nie_negativ_weil_richtung_frei_waehlbar():
    # Egal welche Rate wo steht - die guenstigere Richtung ist immer waehlbar.
    for a, b in [("0.00002", "0.00001"), ("0.00001", "0.00002"), ("-0.00003", "-0.00009")]:
        ops = build_opportunities([
            _funding("extended", "ETH-PERP", a),
            _funding("lighter", "ETH-PERP", b),
        ])
        assert ops[0].net_rate_hourly >= 0


def test_symbol_nur_auf_einer_boerse_ergibt_kein_paar():
    ops = build_opportunities([_funding("extended", "SOL-PERP", "0.00002")])
    assert ops == []


def test_sortierung_nach_netto_apr_absteigend():
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "0.00001"),
        _funding("lighter", "BTC-PERP", "0.00000"),
        _funding("extended", "ETH-PERP", "0.00005"),
        _funding("lighter", "ETH-PERP", "-0.00005"),
        _funding("extended", "SOL-PERP", "0.00002"),
        _funding("lighter", "SOL-PERP", "0.00001"),
    ])
    symbols = [o.symbol for o in ops]
    assert symbols == ["ETH-PERP", "BTC-PERP", "SOL-PERP"]
    assert ops[0].net_rate_hourly == Decimal("0.00010")


def test_netto_apr_wird_mitgeliefert():
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "0.00002"),
        _funding("lighter", "BTC-PERP", "-0.00001"),
    ])
    assert ops[0].net_apr == Decimal("0.26280")


def test_breakeven_wird_mit_gebuehren_gerechnet():
    ops = build_opportunities(
        [
            _funding("extended", "BTC-PERP", "0.00002"),
            _funding("lighter", "BTC-PERP", "-0.00001"),
        ],
        taker_fees={"extended": Decimal("0.0004"), "lighter": Decimal("0.0002")},
        spreads={"BTC-PERP": Decimal("0.0001")},
    )
    op = ops[0]
    assert op.cost_basis_known is True
    assert op.breakeven_hours is not None
    assert op.breakeven_hours.quantize(Decimal("0.01")) == Decimal("43.33")


def test_breakeven_bleibt_offen_ohne_gebuehren():
    # Ohne bekannte Gebuehren wird nichts geschaetzt - die UI muss das sehen.
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "0.00002"),
        _funding("lighter", "BTC-PERP", "-0.00001"),
    ])
    assert ops[0].cost_basis_known is False
    assert ops[0].breakeven_hours is None


def test_mehr_als_zwei_boersen_ergeben_alle_paare():
    ops = build_opportunities([
        _funding("extended", "BTC-PERP", "0.00002"),
        _funding("lighter", "BTC-PERP", "0.00001"),
        _funding("variational", "BTC-PERP", "-0.00001"),
    ])
    assert len(ops) == 3
