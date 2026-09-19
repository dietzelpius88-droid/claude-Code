"""Slippage-Schaetzung aus dem Orderbuch.

Vor jeder Ausfuehrung muss feststehen, ob das Orderbuch die geplante Groesse
ueberhaupt hergibt - und zu welchem Durchschnittspreis. Eine Schaetzung, die
die Tiefe ignoriert, ist im entscheidenden Moment wertlos.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.slippage import SlippageError, estimate_fill
from app.models.domain import OrderBook, OrderBookLevel, Side


def _book(bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> OrderBook:
    return OrderBook(
        venue="extended",
        symbol="BTC-PERP",
        bids=tuple(OrderBookLevel(price=Decimal(p), size=Decimal(s)) for p, s in bids),
        asks=tuple(OrderBookLevel(price=Decimal(p), size=Decimal(s)) for p, s in asks),
        as_of=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )


STANDARD = _book(
    bids=[("63990", "1"), ("63980", "2"), ("63950", "5")],
    asks=[("64010", "1"), ("64020", "2"), ("64050", "5")],
)


# --- Kauf -----------------------------------------------------------------

def test_kleine_order_fuellt_sich_auf_der_besten_stufe():
    e = estimate_fill(STANDARD, Side.LONG, Decimal("0.5"))
    assert e.average_price == Decimal("64010")
    assert e.slippage == Decimal(0)
    assert e.fully_filled is True


def test_groessere_order_laeuft_durch_mehrere_stufen():
    """2 BTC: 1 zu 64010, 1 zu 64020 -> Schnitt 64015."""
    e = estimate_fill(STANDARD, Side.LONG, Decimal(2))
    assert e.average_price == Decimal("64015")
    # (64015 - 64010) / 64010
    assert e.slippage.quantize(Decimal("0.0000001")) == Decimal("0.0000781")


def test_slippage_ist_bei_kauf_immer_positiv():
    e = estimate_fill(STANDARD, Side.LONG, Decimal(6))
    assert e.slippage > 0
    assert e.average_price > Decimal("64010")


# --- Verkauf --------------------------------------------------------------

def test_verkauf_laeuft_gegen_die_gebote():
    """2 BTC: 1 zu 63990, 1 zu 63980 -> Schnitt 63985."""
    e = estimate_fill(STANDARD, Side.SHORT, Decimal(2))
    assert e.average_price == Decimal("63985")
    assert e.slippage.quantize(Decimal("0.0000001")) == Decimal("0.0000781")


def test_slippage_ist_auch_beim_verkauf_positiv():
    # Slippage ist immer ein Kostenmass, nie ein Vorzeichen fuer die Richtung.
    e = estimate_fill(STANDARD, Side.SHORT, Decimal(6))
    assert e.slippage > 0
    assert e.average_price < Decimal("63990")


# --- Tiefe reicht nicht ---------------------------------------------------

def test_zu_geringe_tiefe_wird_gemeldet_statt_extrapoliert():
    """Das Buch endet - was darueber hinausgeht, wird nicht geschaetzt."""
    e = estimate_fill(STANDARD, Side.LONG, Decimal(20))
    assert e.fully_filled is False
    assert e.filled_size == Decimal(8)  # 1 + 2 + 5
    assert e.remaining == Decimal(12)


def test_teilfuellung_liefert_trotzdem_einen_durchschnittspreis():
    e = estimate_fill(STANDARD, Side.LONG, Decimal(20))
    assert e.average_price is not None
    assert e.average_price > Decimal("64010")


def test_leeres_buch_wird_abgelehnt():
    leer = _book(bids=[], asks=[])
    with pytest.raises(SlippageError, match="leer"):
        estimate_fill(leer, Side.LONG, Decimal(1))


def test_nur_eine_seite_vorhanden():
    nur_bids = _book(bids=[("63990", "1")], asks=[])
    with pytest.raises(SlippageError):
        estimate_fill(nur_bids, Side.LONG, Decimal(1))
    # Die Gegenseite funktioniert weiterhin.
    assert estimate_fill(nur_bids, Side.SHORT, Decimal("0.5")).fully_filled is True


# --- Eingaben -------------------------------------------------------------

def test_menge_null_oder_negativ_wird_abgelehnt():
    for menge in (Decimal(0), Decimal(-1)):
        with pytest.raises(SlippageError):
            estimate_fill(STANDARD, Side.LONG, menge)


def test_notional_wird_mitgeliefert():
    e = estimate_fill(STANDARD, Side.LONG, Decimal(2))
    assert e.notional == Decimal("128030")  # 2 * 64015


def test_verfuegbare_tiefe_in_usd():
    e = estimate_fill(STANDARD, Side.LONG, Decimal(20))
    # 1*64010 + 2*64020 + 5*64050 = 512300
    assert e.notional == Decimal("512300")
