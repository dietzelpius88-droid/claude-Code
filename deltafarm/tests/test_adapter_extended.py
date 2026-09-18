"""Extended-Adapter gegen nachgebildete Antworten.

Ohne Netzzugang ist das der einzige Weg, das Parsen zu pruefen. Die
Beispieldaten folgen den Modellen des offiziellen SDK.
"""

from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.base import FatalError
from app.adapters.extended import ExtendedAdapter
from tests.fixtures_wire import (
    extended_funding_history,
    extended_markets,
    extended_orderbook,
    extended_orderbook_kurzform,
)

BASE = "https://api.test.example/api/v1"


def _adapter() -> ExtendedAdapter:
    return ExtendedAdapter(base_url=BASE, rate_per_second=1000, burst=1000)


@respx.mock
async def test_maerkte_werden_gelesen_und_uebersetzt():
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=extended_markets()))
    maerkte = await _adapter().list_markets()

    assert len(maerkte) == 1  # PEPE-USD steht nicht in der Tabelle
    m = maerkte[0]
    assert m.symbol == "BTC-PERP"
    assert m.native_symbol == "BTC-USD"
    assert m.mark_price == Decimal("64000.5")


@respx.mock
async def test_unbekanntes_listing_wird_uebersprungen_statt_geraten():
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=extended_markets()))
    maerkte = await _adapter().list_markets()
    assert "PEPE" not in {m.native_symbol for m in maerkte}


@respx.mock
async def test_funding_wird_normalisiert_und_intervall_mitgefuehrt():
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=extended_markets()))
    info = await _adapter().get_funding("BTC-PERP")

    assert info.native_rate == Decimal("0.0000125")
    assert info.native_interval_hours == Decimal(1)
    assert info.rate_hourly == Decimal("0.0000125")
    assert info.apr == Decimal("0.1095")


@respx.mock
async def test_funding_zahl_wird_nicht_durch_float_geschleust():
    """Auch wenn die Antwort die Rate als JSON-Zahl statt als String liefert."""
    roh = extended_markets()
    roh["data"][0]["marketStats"]["fundingRate"] = 0.0000125  # echte JSON-Zahl
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=roh))

    info = await _adapter().get_funding("BTC-PERP")
    assert info.native_rate == Decimal("0.0000125")
    assert isinstance(info.native_rate, Decimal)


@respx.mock
async def test_negatives_funding_behaelt_vorzeichen():
    respx.get(f"{BASE}/info/markets").mock(
        return_value=httpx.Response(200, json=extended_markets(funding_rate="-0.0000125"))
    )
    info = await _adapter().get_funding("BTC-PERP")
    assert info.rate_hourly == Decimal("-0.0000125")
    assert info.receiver.value == "LONG"


@respx.mock
async def test_orderbuch_lange_schreibweise():
    respx.get(f"{BASE}/info/markets/BTC-USD/orderbook").mock(
        return_value=httpx.Response(200, json=extended_orderbook())
    )
    buch = await _adapter().get_orderbook("BTC-PERP", depth=10)
    assert buch.best_bid == Decimal("63999.0")
    assert buch.best_ask == Decimal("64001.0")
    assert len(buch.bids) == 2


@respx.mock
async def test_orderbuch_kurzschreibweise_wird_ebenfalls_verstanden():
    # Das SDK akzeptiert beide Schreibweisen, also tun wir es auch.
    respx.get(f"{BASE}/info/markets/BTC-USD/orderbook").mock(
        return_value=httpx.Response(200, json=extended_orderbook_kurzform())
    )
    buch = await _adapter().get_orderbook("BTC-PERP")
    assert buch.best_bid == Decimal("63999.0")
    assert buch.best_ask == Decimal("64001.0")


@respx.mock
async def test_orderbuch_tiefe_wird_begrenzt():
    respx.get(f"{BASE}/info/markets/BTC-USD/orderbook").mock(
        return_value=httpx.Response(200, json=extended_orderbook())
    )
    buch = await _adapter().get_orderbook("BTC-PERP", depth=1)
    assert len(buch.bids) == 1


@respx.mock
async def test_symbolregeln_inklusive_abgeleiteter_mindestnotional():
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=extended_markets()))
    regeln = await _adapter().get_symbol_rules("BTC-PERP")

    assert regeln.tick_size == Decimal("0.1")
    assert regeln.lot_size == Decimal("0.0001")
    # Extended nennt keine Mindest-Notional: 0,0001 BTC * 64000,5 = 6,40005 USD
    assert regeln.min_notional == Decimal("6.40005")
    # Gebuehren sind accountabhaengig und kommen erst in Phase 2 (ADR-005).
    assert regeln.taker_fee is None


@respx.mock
async def test_hebelstufen_werden_aus_risk_factor_config_gelesen():
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=extended_markets()))
    regeln = await _adapter().get_symbol_rules("BTC-PERP")

    # riskFactor 0,02 -> Hebel 50 bis 100k; 0,05 -> Hebel 20 bis 1 Mio.
    assert regeln.leverage_tiers == (
        (Decimal("100000"), Decimal("50.00")),
        (Decimal("1000000"), Decimal("20.00")),
    )
    assert regeln.max_leverage_for_notional(Decimal("50000")) == Decimal("50.00")
    assert regeln.max_leverage_for_notional(Decimal("500000")) == Decimal("20.00")
    assert regeln.max_leverage_for_notional(Decimal("5000000")) == Decimal(0)


@respx.mock
async def test_fehlerstatus_der_boerse_wird_endgueltiger_fehler():
    respx.get(f"{BASE}/info/markets").mock(
        return_value=httpx.Response(
            200, json={"status": "ERROR", "error": {"code": 1001, "message": "kaputt"}}
        )
    )
    with pytest.raises(FatalError, match="kaputt"):
        await _adapter().list_markets()


@respx.mock
async def test_fehlendes_pflichtfeld_nennt_den_kontext():
    roh = extended_markets()
    del roh["data"][0]["marketStats"]["fundingRate"]
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=roh))

    with pytest.raises(FatalError, match="fundingRate"):
        await _adapter().get_funding("BTC-PERP")


@respx.mock
async def test_unbekannter_markt_wird_benannt():
    roh = extended_markets()
    roh["data"] = [roh["data"][1]]  # nur PEPE-USD
    respx.get(f"{BASE}/info/markets").mock(return_value=httpx.Response(200, json=roh))

    with pytest.raises(FatalError, match="BTC-USD"):
        await _adapter().get_funding("BTC-PERP")


@respx.mock
async def test_funding_zeitstempel_fuer_die_intervallpruefung():
    stamps = [1789000000000 + i * 3_600_000 for i in range(5)]
    respx.get(f"{BASE}/info/BTC-USD/funding").mock(
        return_value=httpx.Response(200, json=extended_funding_history(stamps))
    )
    zeiten = await _adapter().funding_timestamps("BTC-PERP", lookback_hours=6)

    assert len(zeiten) == 5
    assert (zeiten[1] - zeiten[0]).total_seconds() == 3600
