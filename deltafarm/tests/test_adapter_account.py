"""Account-Methoden beider Adapter (Phase 2, nur lesend)."""

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.base import TradingNotSupported
from app.adapters.extended import ExtendedAdapter
from app.adapters.lighter import LighterAdapter
from app.models.domain import Side
from tests.fixtures_wire import (
    extended_balance,
    extended_fees,
    extended_positions,
    extended_positions_history,
    lighter_account,
    lighter_details,
    lighter_position_funding,
)

EXT = "https://api.test.example/api/v1"
LIT = "https://lighter.test.example"
SEIT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _extended(api_key: str | None = "geheim") -> ExtendedAdapter:
    return ExtendedAdapter(base_url=EXT, rate_per_second=1000, burst=1000, api_key=api_key)


def _lighter(account_index: int | None = 5) -> LighterAdapter:
    return LighterAdapter(
        base_url=LIT, rate_per_second=1000, burst=1000, account_index=account_index
    )


# --- Zugang ---------------------------------------------------------------

async def test_extended_ohne_schluessel_sagt_es_deutlich():
    with pytest.raises(TradingNotSupported, match="EXTENDED_API_KEY"):
        await _extended(api_key=None).get_balance()


async def test_lighter_ohne_account_index_sagt_es_deutlich():
    with pytest.raises(TradingNotSupported, match="LIGHTER_ACCOUNT_INDEX"):
        await _lighter(account_index=None).get_balance()


def test_zugangsstatus_ist_abfragbar():
    assert _extended().has_account_access is True
    assert _extended(api_key=None).has_account_access is False
    assert _lighter().has_account_access is True
    assert _lighter(account_index=None).has_account_access is False


@respx.mock
async def test_extended_sendet_den_schluessel_nur_im_header():
    route = respx.get(f"{EXT}/user/balance").mock(
        return_value=httpx.Response(200, json=extended_balance())
    )
    await _extended().get_balance()

    anfrage = route.calls[0].request
    assert anfrage.headers["X-Api-Key"] == "geheim"
    assert "geheim" not in str(anfrage.url)


# --- Extended -------------------------------------------------------------

@respx.mock
async def test_extended_kontostand():
    respx.get(f"{EXT}/user/balance").mock(return_value=httpx.Response(200, json=extended_balance()))
    b = await _extended().get_balance()

    assert b.collateral == "USD"
    assert b.equity == Decimal("10250.5")
    assert b.available == Decimal("8000")
    assert b.initial_margin == Decimal("2050.1")
    # 2050,1 / 10250,5 = 0,2
    assert b.margin_usage == Decimal("0.2")


@respx.mock
async def test_extended_positionen():
    respx.get(f"{EXT}/user/positions").mock(
        return_value=httpx.Response(200, json=extended_positions())
    )
    positionen = await _extended().get_positions()

    # PEPE wird nicht gefuehrt, die Nullposition ist keine.
    assert len(positionen) == 1
    p = positionen[0]
    assert p.symbol == "BTC-PERP"
    assert p.side is Side.LONG
    assert p.size == Decimal("0.5")
    assert p.notional == Decimal("32000")
    assert p.liquidation_price == Decimal("58000")
    assert p.signed_notional == Decimal("32000")


@respx.mock
async def test_extended_gebuehren_accountabhaengig():
    route = respx.get(f"{EXT}/user/fees").mock(
        return_value=httpx.Response(200, json=extended_fees())
    )
    adapter = _extended()
    maker, taker = await adapter.get_fees("BTC-PERP")

    assert maker == Decimal("0.0002")
    assert taker == Decimal("0.0005")
    assert route.calls[0].request.url.params["market"] == "BTC-USD"


@respx.mock
async def test_extended_gebuehren_werden_zwischengespeichert():
    route = respx.get(f"{EXT}/user/fees").mock(
        return_value=httpx.Response(200, json=extended_fees())
    )
    adapter = _extended()
    await adapter.get_fees("BTC-PERP")
    await adapter.get_fees("BTC-PERP")
    assert route.call_count == 1


@respx.mock
async def test_extended_funding_summe_geschlossener_positionen():
    """Extended hat keinen Endpunkt fuer einzelne Zahlungen (OFFEN-6).

    Was es gibt, ist die Summe je geschlossener Position - die ist von der
    Boerse und damit bestaetigt, nur nicht nach Zeitpunkten aufgeloest.
    """
    respx.get(f"{EXT}/user/positions/history").mock(
        return_value=httpx.Response(200, json=extended_positions_history())
    )
    zahlungen = await _extended().get_funding_payments(SEIT)

    assert len(zahlungen) == 1
    z = zahlungen[0]
    assert z.symbol == "BTC-PERP"
    assert z.amount == Decimal("35.25")
    assert z.confirmed is True
    assert z.external_id == "7"


# --- Lighter --------------------------------------------------------------

@respx.mock
async def test_lighter_kontostand():
    respx.get(f"{LIT}/api/v1/account").mock(
        return_value=httpx.Response(200, json=lighter_account())
    )
    b = await _lighter().get_balance()

    assert b.equity == Decimal("10250.5")
    assert b.available == Decimal("8000")
    assert b.maintenance_margin == Decimal("1025")
    assert b.margin_usage == Decimal("0.2")


@respx.mock
async def test_lighter_account_wird_ueber_den_index_abgefragt():
    route = respx.get(f"{LIT}/api/v1/account").mock(
        return_value=httpx.Response(200, json=lighter_account())
    )
    await _lighter().get_balance()

    params = route.calls[0].request.url.params
    assert params["by"] == "index"
    assert params["value"] == "5"


@respx.mock
async def test_lighter_positionen_mit_vorzeichen():
    respx.get(f"{LIT}/api/v1/account").mock(
        return_value=httpx.Response(200, json=lighter_account())
    )
    positionen = await _lighter().get_positions()

    assert len(positionen) == 1  # PEPE wird nicht gefuehrt
    p = positionen[0]
    assert p.symbol == "BTC-PERP"
    assert p.side is Side.SHORT  # sign = -1
    assert p.size == Decimal("0.5")
    assert p.signed_notional == Decimal("-32000")
    assert p.liquidation_price == Decimal("70000")
    assert p.funding_paid == Decimal("12.5")


@respx.mock
async def test_lighter_markpreis_wird_abgeleitet_statt_erfunden():
    """AccountPosition nennt keinen Mark-Preis.

    Er wird aus Wert und Groesse gerechnet: 32000 / 0,5 = 64000.
    """
    respx.get(f"{LIT}/api/v1/account").mock(
        return_value=httpx.Response(200, json=lighter_account())
    )
    p = (await _lighter().get_positions())[0]
    assert p.mark_price == Decimal("64000")


@respx.mock
async def test_lighter_echte_funding_zahlungen():
    respx.get(f"{LIT}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )
    respx.get(f"{LIT}/api/v1/positionFunding").mock(
        return_value=httpx.Response(200, json=lighter_position_funding())
    )
    zahlungen = await _lighter().get_funding_payments(SEIT)

    assert len(zahlungen) == 2
    assert [z.amount for z in zahlungen] == [Decimal("1.25"), Decimal("-0.75")]
    assert all(z.symbol == "BTC-PERP" for z in zahlungen)
    assert all(z.confirmed for z in zahlungen)
    assert zahlungen[0].external_id == "99"
    assert zahlungen[0].position_size == Decimal("0.5")
