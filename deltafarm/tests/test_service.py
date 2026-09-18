"""Dienstschicht: Intervallpruefung, Einheitenwarnung, Verdichtung."""

from decimal import Decimal

import pytest

from app.adapters.base import FatalError, RetryableError
from app.adapters.mock import MockAdapter, failing_adapter
from app.core.funding import to_apr
from app.core.service import MarketDataService
from app.models.domain import FundingInfo
from datetime import datetime, timezone


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


# --- Intervallpruefung (ADR-004) -----------------------------------------

async def test_passendes_intervall_besteht_die_pruefung():
    adapter = MockAdapter("a", interval_hours=Decimal(1), funding_spacing_hours=Decimal(1))
    ergebnis = await MarketDataService([adapter]).verify_intervals(["BTC-PERP"])

    assert len(ergebnis) == 1
    assert ergebnis[0].ok is True
    assert ergebnis[0].observed_hours == Decimal(1)


async def test_falsch_deklariertes_intervall_faellt_auf():
    """Der Fall, um den es geht: deklariert 1 h, tatsaechlich 8 h.

    Ohne diese Pruefung waere jeder angezeigte APR um Faktor 8 zu hoch.
    """
    adapter = MockAdapter("a", interval_hours=Decimal(1), funding_spacing_hours=Decimal(8))
    ergebnis = await MarketDataService([adapter]).verify_intervals(["BTC-PERP"])

    assert ergebnis[0].ok is False
    assert ergebnis[0].observed_hours == Decimal(8)
    assert "Faktor" in ergebnis[0].detail or "falsch" in ergebnis[0].detail


async def test_nicht_erreichbare_boerse_faellt_durch_statt_stillschweigend_zu_bestehen():
    ergebnis = await MarketDataService([failing_adapter("a")]).verify_intervals(["BTC-PERP"])
    assert ergebnis[0].ok is False
    assert "nicht pruefbar" in ergebnis[0].detail


async def test_geringe_abweichung_wird_toleriert():
    # Eine verspaetete Zahlung darf den Start nicht blockieren.
    adapter = MockAdapter("a", interval_hours=Decimal(1), funding_spacing_hours=Decimal("1.1"))
    ergebnis = await MarketDataService([adapter]).verify_intervals(["BTC-PERP"])
    assert ergebnis[0].ok is True


# --- Einheitenwarnung (OFFEN-14) -----------------------------------------

def test_warnung_bei_verdacht_auf_prozent_gegen_bruch():
    funding = [
        _funding("extended", "BTC-PERP", "0.0000125"),
        _funding("lighter", "BTC-PERP", "0.00125"),  # Faktor 100
    ]
    warnungen = MarketDataService.unit_warnings(funding)
    assert len(warnungen) == 1
    assert "Einheiten" in warnungen[0]


def test_keine_warnung_bei_normalen_unterschieden():
    funding = [
        _funding("extended", "BTC-PERP", "0.0000125"),
        _funding("lighter", "BTC-PERP", "0.00003"),  # Faktor 2,4
    ]
    assert MarketDataService.unit_warnings(funding) == []


def test_keine_warnung_bei_nur_einer_boerse():
    assert MarketDataService.unit_warnings([_funding("extended", "BTC-PERP", "0.001")]) == []


def test_nullraten_stoeren_die_warnung_nicht():
    funding = [
        _funding("extended", "BTC-PERP", "0"),
        _funding("extended", "ETH-PERP", "0.00001"),
        _funding("lighter", "BTC-PERP", "0.00001"),
    ]
    assert MarketDataService.unit_warnings(funding) == []


# --- Verdichtung ----------------------------------------------------------

async def test_refresh_baut_paare_aus_beiden_boersen():
    a = MockAdapter("extended", rates={"BTC-PERP": Decimal("0.00002")})
    b = MockAdapter("lighter", rates={"BTC-PERP": Decimal("-0.00001")})
    snapshot = await MarketDataService([a, b]).refresh(["BTC-PERP"])

    assert len(snapshot.funding) == 2
    assert len(snapshot.opportunities) == 1
    op = snapshot.opportunities[0]
    assert op.short_venue == "extended"
    assert op.net_rate_hourly == Decimal("0.00003")


async def test_refresh_nutzt_oeffentliche_gebuehren_fuer_den_breakeven():
    a = MockAdapter("extended", rates={"BTC-PERP": Decimal("0.00002")}, taker_fee=Decimal("0.0004"))
    b = MockAdapter("lighter", rates={"BTC-PERP": Decimal("-0.00001")}, taker_fee=Decimal("0.0002"))
    snapshot = await MarketDataService([a, b]).refresh(["BTC-PERP"])

    op = snapshot.opportunities[0]
    assert op.cost_basis_known is True
    # (0,0004 + 0,0002) * 2 / 0,00003 = 40 Stunden
    assert op.breakeven_hours == Decimal(40)


async def test_fehlende_gebuehr_laesst_den_breakeven_offen():
    # Extended liefert Gebuehren erst mit Account-Zugang (ADR-005).
    a = MockAdapter("extended", rates={"BTC-PERP": Decimal("0.00002")}, taker_fee=None)
    b = MockAdapter("lighter", rates={"BTC-PERP": Decimal("-0.00001")}, taker_fee=Decimal("0.0002"))
    snapshot = await MarketDataService([a, b]).refresh(["BTC-PERP"])

    op = snapshot.opportunities[0]
    assert op.cost_basis_known is False
    assert op.breakeven_hours is None


async def test_ausfall_einer_boerse_kippt_nicht_die_ganze_ansicht():
    a = MockAdapter("extended", rates={"BTC-PERP": Decimal("0.00002")})
    b = failing_adapter("lighter")
    snapshot = await MarketDataService([a, b]).refresh(["BTC-PERP"])

    assert len(snapshot.funding) == 1
    assert snapshot.opportunities == ()  # ein Bein allein ergibt kein Paar
    status = {v.name: v.reachable for v in snapshot.venues}
    assert status == {"extended": True, "lighter": False}


async def test_venue_status_meldet_handelsfaehigkeit():
    a = MockAdapter("extended", supports_trading=False)
    stati = await MarketDataService([a], environment="testnet").venue_status()
    assert stati[0].supports_trading is False
    assert stati[0].environment == "testnet"


async def test_snapshot_wird_gemerkt():
    a = MockAdapter("extended", rates={"BTC-PERP": Decimal("0.00002")})
    dienst = MarketDataService([a])
    assert dienst.last_snapshot is None
    await dienst.refresh(["BTC-PERP"])
    assert dienst.last_snapshot is not None
