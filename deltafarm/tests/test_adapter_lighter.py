"""Lighter-Adapter gegen nachgebildete Antworten (Felder aus openapi.json)."""

from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.base import FatalError
from app.adapters.lighter import LighterAdapter
from tests.fixtures_wire import (
    lighter_details,
    lighter_fundings,
    lighter_funding_rates,
    lighter_orderbook,
)

BASE = "https://lighter.test.example"


def _adapter() -> LighterAdapter:
    return LighterAdapter(base_url=BASE, rate_per_second=1000, burst=1000)


@respx.mock
async def test_maerkte_werden_gelesen_und_uebersetzt():
    respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    maerkte = await _adapter().list_markets()

    assert len(maerkte) == 1  # PEPE steht nicht in der Tabelle
    assert maerkte[0].symbol == "BTC-PERP"
    assert maerkte[0].native_symbol == "BTC"
    assert maerkte[0].mark_price == Decimal("64000.5")


@respx.mock
async def test_prozent_pro_jahr_wird_in_eine_stundenrate_umgerechnet():
    """Lighter gibt die Rate als Prozent pro Jahr an, nicht als Bruch je Stunde.

    Wuerde man den Wert ungerechnet uebernehmen, waere der angezeigte APR um
    Faktor 876 000 zu hoch.
    """
    respx.get(f"{BASE}/api/v1/funding-rates").mock(
        return_value=httpx.Response(200, json=lighter_funding_rates())
    )
    info = await _adapter().get_funding("BTC-PERP")

    assert info.native_rate == Decimal("10.95")
    assert info.native_convention == "annualized_percent"
    assert info.rate_hourly == Decimal("0.0000125")
    assert info.apr == Decimal("0.1095")
    # Das Zahlungsintervall bleibt davon unberuehrt.
    assert info.native_interval_hours == Decimal(1)


@respx.mock
async def test_native_rate_wird_nicht_versehentlich_als_stundenrate_uebernommen():
    respx.get(f"{BASE}/api/v1/funding-rates").mock(
        return_value=httpx.Response(200, json=lighter_funding_rates())
    )
    info = await _adapter().get_funding("BTC-PERP")
    assert info.rate_hourly != info.native_rate


@respx.mock
async def test_fremde_boersenrate_wird_niemals_als_lighter_rate_verbucht():
    """Der Endpunkt liefert auch Raten anderer Boersen.

    Steht kein als Lighter erkennbarer Eintrag darin, bricht der Adapter ab,
    statt den erstbesten Treffer zu nehmen. Eine Binance-Rate als Lighter-Rate
    zu fuehren waere der teuerste denkbare Fehler.
    """
    antwort = lighter_funding_rates()
    antwort["funding_rates"] = [
        e for e in antwort["funding_rates"] if e["exchange"] != "lighter"
    ]
    respx.get(f"{BASE}/api/v1/funding-rates").mock(return_value=httpx.Response(200, json=antwort))

    with pytest.raises(FatalError, match="binance"):
        await _adapter().get_funding("BTC-PERP")


@respx.mock
async def test_richtige_boerse_wird_aus_mehreren_eintraegen_gewaehlt():
    respx.get(f"{BASE}/api/v1/funding-rates").mock(
        return_value=httpx.Response(200, json=lighter_funding_rates(rate="26.28"))
    )
    info = await _adapter().get_funding("BTC-PERP")
    # 78,84 waere der Binance-Eintrag gewesen.
    assert info.native_rate == Decimal("26.28")
    assert info.rate_hourly == Decimal("0.00003")


@respx.mock
async def test_unbekanntes_symbol_in_der_ratenliste():
    antwort = lighter_funding_rates()
    antwort["funding_rates"] = []
    respx.get(f"{BASE}/api/v1/funding-rates").mock(return_value=httpx.Response(200, json=antwort))

    with pytest.raises(FatalError, match="BTC"):
        await _adapter().get_funding("BTC-PERP")


@respx.mock
async def test_orderbuch():
    respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    respx.get(f"{BASE}/api/v1/orderBookOrders").mock(
        return_value=httpx.Response(200, json=lighter_orderbook())
    )
    buch = await _adapter().get_orderbook("BTC-PERP", depth=10)

    assert buch.best_bid == Decimal("63999.0")
    assert buch.best_ask == Decimal("64001.0")
    assert buch.bids[0].size == Decimal("1.5")


@respx.mock
async def test_spread_in_basispunkten():
    respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    respx.get(f"{BASE}/api/v1/orderBookOrders").mock(
        return_value=httpx.Response(200, json=lighter_orderbook())
    )
    buch = await _adapter().get_orderbook("BTC-PERP")
    # (64001 - 63999) / 64000 * 10000 = 0,3125 bp
    assert buch.spread_bps.quantize(Decimal("0.0001")) == Decimal("0.3125")


@respx.mock
async def test_symbolregeln_aus_dezimalstellen_und_gebuehren():
    respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    regeln = await _adapter().get_symbol_rules("BTC-PERP")

    assert regeln.tick_size == Decimal("0.1")     # supported_price_decimals = 1
    assert regeln.lot_size == Decimal("0.0001")   # supported_size_decimals = 4
    assert regeln.min_notional == Decimal("10")
    # Lighter liefert die Gebuehren oeffentlich mit - anders als Extended.
    assert regeln.taker_fee == Decimal("0.0001")
    assert regeln.maker_fee == Decimal("0.0000")
    # initial margin fraction 0,02 -> Hebel 50
    assert regeln.max_leverage == Decimal("50.00")


@respx.mock
async def test_market_id_wird_aus_den_boersendaten_aufgeloest():
    route = respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    orders = respx.get(f"{BASE}/api/v1/orderBookOrders").mock(
        return_value=httpx.Response(200, json=lighter_orderbook())
    )
    adapter = _adapter()
    await adapter.get_orderbook("BTC-PERP")

    assert route.called
    assert orders.calls[0].request.url.params["market_id"] == "1"


@respx.mock
async def test_market_id_wird_zwischengespeichert():
    details = respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    respx.get(f"{BASE}/api/v1/orderBookOrders").mock(
        return_value=httpx.Response(200, json=lighter_orderbook())
    )
    adapter = _adapter()
    await adapter.get_orderbook("BTC-PERP")
    await adapter.get_orderbook("BTC-PERP")

    assert details.call_count == 1  # kein zweiter Aufruf noetig


@respx.mock
async def test_fehlercode_der_boerse_wird_endgueltiger_fehler():
    respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json={"code": 400, "message": "kaputt"})
    )
    with pytest.raises(FatalError, match="kaputt"):
        await _adapter().list_markets()


@respx.mock
async def test_funding_zeitstempel_fuer_die_intervallpruefung():
    respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    stamps = [1789000000 + i * 3600 for i in range(5)]
    respx.get(f"{BASE}/api/v1/fundings").mock(
        return_value=httpx.Response(200, json=lighter_fundings(stamps))
    )
    zeiten = await _adapter().funding_timestamps("BTC-PERP", lookback_hours=6)

    assert len(zeiten) == 5
    assert (zeiten[1] - zeiten[0]).total_seconds() == 3600


@respx.mock
async def test_kennung_lit_wird_als_lighter_erkannt():
    """Der Ticker des Lighter-Tokens als Boersenkennung."""
    respx.get(f"{BASE}/api/v1/funding-rates").mock(
        return_value=httpx.Response(200, json=lighter_funding_rates(exchange="LIT"))
    )
    info = await _adapter().get_funding("BTC-PERP")
    assert info.rate_hourly == Decimal("0.0000125")
