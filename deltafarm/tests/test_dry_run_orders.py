"""Trockenlauf-Orderpfad der echten Adapter.

Der Pfad laeuft vollstaendig durch - inklusive Pruefung gegen die Regeln der
Boerse -, aber es geht keine Order raus. Eine ungueltige Groesse faellt damit
schon hier auf und nicht erst beim ersten echten Versuch.
"""

from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.base import FatalError, TradingNotSupported
from app.adapters.lighter import LighterAdapter
from app.models.domain import OrderRequest, OrderStatus, Side
from tests.fixtures_wire import lighter_details

BASE = "https://lighter.test.example"


def _adapter(dry_run: bool = True) -> LighterAdapter:
    return LighterAdapter(
        base_url=BASE, rate_per_second=1000, burst=1000, account_index=5, dry_run=dry_run
    )


def _order(size: str) -> OrderRequest:
    return OrderRequest(
        venue="lighter",
        symbol="BTC-PERP",
        side=Side.LONG,
        size=Decimal(size),
        client_order_id="df-test-1",
    )


def _regeln():
    return respx.get(f"{BASE}/api/v1/orderBookDetails").mock(
        return_value=httpx.Response(200, json=lighter_details())
    )


@respx.mock
async def test_gueltige_order_laeuft_durch_ohne_zu_senden():
    route = _regeln()
    ergebnis = await _adapter().place_order(_order("0.1"))

    assert ergebnis.status is OrderStatus.FILLED
    assert ergebnis.dry_run is True
    assert ergebnis.exchange_order_id is None
    assert "Trockenlauf" in (ergebnis.detail or "")
    # Die Regeln wurden wirklich abgefragt - der Pfad laeuft vollstaendig.
    assert route.called


@respx.mock
async def test_keine_schreibende_anfrage_an_die_boerse():
    _regeln()
    bestellung = respx.post(f"{BASE}/api/v1/sendTx").mock(
        return_value=httpx.Response(200, json={"code": 200})
    )
    await _adapter().place_order(_order("0.1"))

    assert not bestellung.called, "Im Trockenlauf darf nichts gesendet werden"


@respx.mock
async def test_groesse_neben_dem_raster_wird_abgelehnt():
    _regeln()
    # Lot-Size ist 0,0001 (supported_size_decimals = 4)
    with pytest.raises(FatalError, match="Lot-Size"):
        await _adapter().place_order(_order("0.10005"))


@respx.mock
async def test_groesse_unter_der_mindestnotional_wird_abgelehnt():
    _regeln()
    # min_quote_amount ist 10 USD, Mark 64000 -> 0,0001 BTC = 6,40 USD
    with pytest.raises(FatalError, match="Mindestgroesse"):
        await _adapter().place_order(_order("0.0001"))


@respx.mock
async def test_negative_groesse_wird_abgelehnt():
    _regeln()
    with pytest.raises(FatalError):
        await _adapter().place_order(_order("-0.1"))


@respx.mock
async def test_gebuehr_wird_geschaetzt():
    _regeln()
    ergebnis = await _adapter().place_order(_order("0.1"))
    # 0,1 * 64000,5 * 0,0001
    assert ergebnis.fee is not None
    assert ergebnis.fee > 0


@respx.mock
async def test_live_modus_ist_noch_nicht_freigeschaltet():
    """DELTAFARM_DRY_RUN=false allein schaltet nichts scharf.

    Der Adapter sagt ausdruecklich, dass er noch nicht senden kann, statt es
    stillschweigend doch zu tun oder stillschweigend nichts zu tun.
    """
    with pytest.raises(TradingNotSupported, match="Phase 4"):
        await _adapter(dry_run=False).place_order(_order("0.1"))


@respx.mock
async def test_schliessen_im_trockenlauf_meldet_es_deutlich():
    _regeln()
    ergebnis = await _adapter().close_position("BTC-PERP")
    assert ergebnis.dry_run is True
    assert "nicht wirklich geschlossen" in (ergebnis.detail or "")


async def test_stornieren_im_live_modus_ist_gesperrt():
    with pytest.raises(TradingNotSupported):
        await _adapter(dry_run=False).cancel_order("irgendeine")
