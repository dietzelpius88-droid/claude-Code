"""Break-even-Rechnung gegen haendisch gepruefte Beispiele.

Formel laut Auftrag Abschnitt 6.2:
    (taker_fee_A + taker_fee_B) * 2 + geschaetzter Spread
    geteilt durch die erwartete stuendliche Netto-Funding-Rate.
Die 2 steht fuer Eroeffnen und Schliessen beider Beine.
"""

from decimal import Decimal

import pytest

from app.core.breakeven import breakeven_hours, round_trip_cost


# --- Kostenseite ----------------------------------------------------------

def test_round_trip_kosten_von_hand():
    # (0,05 % + 0,05 %) * 2 + 0 = 0,2 %
    cost = round_trip_cost(
        taker_fee_a=Decimal("0.0005"),
        taker_fee_b=Decimal("0.0005"),
        spread=Decimal(0),
    )
    assert cost == Decimal("0.002")


def test_round_trip_kosten_mit_spread():
    # (0,04 % + 0,02 %) * 2 + 0,01 % = 0,13 %
    cost = round_trip_cost(
        taker_fee_a=Decimal("0.0004"),
        taker_fee_b=Decimal("0.0002"),
        spread=Decimal("0.0001"),
    )
    assert cost == Decimal("0.0013")


def test_round_trip_kosten_ohne_spread_argument():
    cost = round_trip_cost(taker_fee_a=Decimal("0.0003"), taker_fee_b=Decimal("0.0003"))
    assert cost == Decimal("0.0012")


# --- Mindesthaltedauer ----------------------------------------------------

def test_breakeven_glatte_hundert_stunden():
    # Kosten 0,2 % bei 0,002 %/h Netto -> genau 100 Stunden.
    hours = breakeven_hours(
        cost=Decimal("0.002"),
        net_rate_hourly=Decimal("0.00002"),
    )
    assert hours == Decimal(100)


def test_breakeven_realistisches_beispiel():
    # Kosten 0,13 % bei 0,003 %/h Netto -> 43,333... Stunden.
    hours = breakeven_hours(
        cost=Decimal("0.0013"),
        net_rate_hourly=Decimal("0.00003"),
    )
    assert hours is not None
    assert hours.quantize(Decimal("0.01")) == Decimal("43.33")


def test_breakeven_undefiniert_wenn_netto_funding_null():
    # Eine Position, die nichts einbringt, amortisiert sich nie.
    assert breakeven_hours(cost=Decimal("0.002"), net_rate_hourly=Decimal(0)) is None


def test_breakeven_undefiniert_wenn_netto_funding_negativ():
    # Das Paar kostet laufend Geld - kein Break-even, sondern ein Abbruchgrund.
    assert breakeven_hours(cost=Decimal("0.002"), net_rate_hourly=Decimal("-0.00001")) is None


def test_breakeven_null_bei_kostenfreiem_einstieg():
    assert breakeven_hours(cost=Decimal(0), net_rate_hourly=Decimal("0.00002")) == Decimal(0)


def test_negative_kosten_werden_abgelehnt():
    # Negative Kosten waeren ein Rechenfehler weiter oben, kein Geschenk.
    with pytest.raises(ValueError):
        breakeven_hours(cost=Decimal("-0.001"), net_rate_hourly=Decimal("0.00002"))
