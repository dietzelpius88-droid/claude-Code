"""Neu ausrichten: beide Wege durchrechnen, der Nutzer entscheidet.

Ein Rest-Delta laesst sich auf zwei Arten abbauen - das kleinere Bein
aufstocken oder das groessere verkleinern. Beides kostet Gebuehren, beides
veraendert die Position. Das Werkzeug rechnet, entschieden wird von Hand.
"""

from decimal import Decimal

import pytest

from app.core.rebalance import RebalanceError, plan_rebalance
from app.models.domain import Side


def _plan(long_size: str, short_size: str, **kwargs):
    vorgabe = dict(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        long_size=Decimal(long_size),
        short_size=Decimal(short_size),
        mark_price=Decimal(64000),
        lot_size=Decimal("0.001"),
        long_taker_fee=Decimal("0.0001"),
        short_taker_fee=Decimal("0.0005"),
    )
    vorgabe.update(kwargs)
    return plan_rebalance(**vorgabe)


# --- Kein Handlungsbedarf -------------------------------------------------

def test_gleiche_beine_brauchen_keine_ausrichtung():
    e = _plan("0.1", "0.1")
    assert e.balanced is True
    assert e.increase is None
    assert e.decrease is None


def test_abweichung_unter_dem_raster_gilt_als_ausgeglichen():
    """Eine Differenz, die kleiner als die kleinste handelbare Menge ist,
    laesst sich gar nicht abbauen."""
    e = _plan("0.1005", "0.1")
    assert e.balanced is True


# --- Long groesser --------------------------------------------------------

def test_aufstocken_kauft_auf_der_short_seite_nach():
    e = _plan("0.12", "0.10")
    assert e.difference == Decimal("0.02")
    assert e.increase.venue == "extended"
    assert e.increase.side is Side.SHORT
    assert e.increase.size == Decimal("0.02")


def test_verkleinern_reduziert_die_long_seite():
    e = _plan("0.12", "0.10")
    assert e.decrease.venue == "lighter"
    assert e.decrease.side is Side.SHORT  # Gegenseite zum Long
    assert e.decrease.size == Decimal("0.02")
    assert e.decrease.reduce_only is True


# --- Short groesser -------------------------------------------------------

def test_richtung_dreht_sich_wenn_die_short_seite_groesser_ist():
    e = _plan("0.10", "0.12")
    assert e.increase.venue == "lighter"
    assert e.increase.side is Side.LONG
    assert e.decrease.venue == "extended"
    assert e.decrease.side is Side.LONG  # Gegenseite zum Short


# --- Kosten ---------------------------------------------------------------

def test_kosten_folgen_der_gebuehr_der_betroffenen_boerse():
    e = _plan("0.12", "0.10")
    # Aufstocken laeuft ueber extended: 0,02 * 64000 * 0,0005
    assert e.increase.estimated_fee == Decimal("0.64")
    # Verkleinern laeuft ueber lighter: 0,02 * 64000 * 0,0001
    assert e.decrease.estimated_fee == Decimal("0.128")


def test_verkleinern_ist_hier_guenstiger():
    e = _plan("0.12", "0.10")
    assert e.decrease.estimated_fee < e.increase.estimated_fee


def test_unbekannte_gebuehr_bleibt_offen():
    e = _plan("0.12", "0.10", short_taker_fee=None)
    assert e.increase.estimated_fee is None
    assert e.decrease.estimated_fee == Decimal("0.128")


# --- Ergebnis nach der Ausrichtung ---------------------------------------

def test_aufstocken_vergroessert_die_position():
    e = _plan("0.12", "0.10")
    assert e.increase.resulting_size == Decimal("0.12")
    assert e.increase.resulting_notional == Decimal("7680.00")


def test_verkleinern_verkleinert_die_position():
    e = _plan("0.12", "0.10")
    assert e.decrease.resulting_size == Decimal("0.10")
    assert e.decrease.resulting_notional == Decimal("6400.00")


# --- Rundung und Ablehnungen ---------------------------------------------

def test_differenz_wird_auf_das_raster_gerundet():
    e = _plan("0.1234", "0.10", lot_size=Decimal("0.01"))
    # 0,0234 abgerundet auf 0,02
    assert e.difference == Decimal("0.02")


def test_negative_groessen_werden_abgelehnt():
    with pytest.raises(RebalanceError):
        _plan("-0.1", "0.1")


def test_markpreis_null_wird_abgelehnt():
    with pytest.raises(RebalanceError):
        _plan("0.12", "0.10", mark_price=Decimal(0))
