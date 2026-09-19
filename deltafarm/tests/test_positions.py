"""Positionsauswertung: Liquidationsabstand, Margin-Auslastung, Ampel, Rest-Delta."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.positions import (
    LiquidationThresholds,
    assess_position,
    liquidation_distance,
    net_delta_usd,
)
from app.models.domain import HealthLevel, Position, Side


def _pos(side: Side, mark: str, liq: str | None, size: str = "1", notional: str | None = None) -> Position:
    m = Decimal(mark)
    s = Decimal(size)
    return Position(
        venue="extended",
        symbol="BTC-PERP",
        side=side,
        size=s,
        entry_price=m,
        mark_price=m,
        notional=Decimal(notional) if notional else m * s,
        liquidation_price=Decimal(liq) if liq is not None else None,
        as_of=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )


# --- Liquidationsabstand --------------------------------------------------

def test_long_abstand_nach_unten():
    # Mark 100, Liquidation bei 80 -> 20 % Preisbewegung Puffer.
    assert liquidation_distance(_pos(Side.LONG, "100", "80")) == Decimal("0.2")


def test_short_abstand_nach_oben():
    # Mark 100, Liquidation bei 125 -> 25 % Puffer.
    assert liquidation_distance(_pos(Side.SHORT, "100", "125")) == Decimal("0.25")


def test_ohne_liquidationspreis_kein_abstand():
    # Keine erfundene Zahl, wenn die Boerse keinen Preis nennt.
    assert liquidation_distance(_pos(Side.LONG, "100", None)) is None


def test_long_bereits_unter_dem_liquidationspreis():
    # Darf nicht negativ "schoengerechnet" werden - null heisst akut.
    assert liquidation_distance(_pos(Side.LONG, "70", "80")) == Decimal(0)


def test_short_bereits_ueber_dem_liquidationspreis():
    assert liquidation_distance(_pos(Side.SHORT, "130", "125")) == Decimal(0)


def test_markpreis_null_ergibt_keinen_abstand():
    assert liquidation_distance(_pos(Side.LONG, "0", "0")) is None


# --- Ampel ----------------------------------------------------------------

def test_ampel_gruen_bei_weitem_abstand():
    bewertung = assess_position(_pos(Side.LONG, "100", "50"), margin_usage=Decimal("0.2"))
    assert bewertung.level is HealthLevel.GREEN


def test_ampel_gelb_bei_mittlerem_abstand():
    # Standardschwellen: gruen ab 25 %, gelb ab 10 %.
    bewertung = assess_position(_pos(Side.LONG, "100", "85"), margin_usage=Decimal("0.2"))
    assert bewertung.level is HealthLevel.YELLOW


def test_ampel_rot_bei_knappem_abstand():
    bewertung = assess_position(_pos(Side.LONG, "100", "95"), margin_usage=Decimal("0.2"))
    assert bewertung.level is HealthLevel.RED


def test_ampel_rot_bei_hoher_margin_auslastung_trotz_weitem_abstand():
    # Beide Kriterien zaehlen; das schlechtere gewinnt.
    bewertung = assess_position(_pos(Side.LONG, "100", "50"), margin_usage=Decimal("0.95"))
    assert bewertung.level is HealthLevel.RED


def test_ampel_rot_ohne_liquidationspreis():
    """Eine Position ohne bekannten Liquidationspreis ist nicht gruen.

    Unbekannt ist nicht dasselbe wie sicher.
    """
    bewertung = assess_position(_pos(Side.LONG, "100", None), margin_usage=Decimal("0.1"))
    assert bewertung.level is HealthLevel.RED
    assert "Liquidationspreis" in (bewertung.detail or "")


def test_schwellen_sind_konfigurierbar():
    streng = LiquidationThresholds(green=Decimal("0.5"), yellow=Decimal("0.3"))
    bewertung = assess_position(
        _pos(Side.LONG, "100", "60"), margin_usage=Decimal("0.1"), thresholds=streng
    )
    assert bewertung.level is HealthLevel.YELLOW


def test_unsinnige_schwellen_werden_abgelehnt():
    with pytest.raises(ValueError):
        LiquidationThresholds(green=Decimal("0.1"), yellow=Decimal("0.3"))


# --- Rest-Delta -----------------------------------------------------------

def test_perfekt_gehedgtes_paar_hat_kein_delta():
    lang = _pos(Side.LONG, "100", "80", size="1", notional="100")
    kurz = _pos(Side.SHORT, "100", "125", size="1", notional="100")
    delta, anteil = net_delta_usd([lang, kurz])
    assert delta == Decimal(0)
    assert anteil == Decimal(0)


def test_ungleiche_beine_ergeben_rest_delta():
    lang = _pos(Side.LONG, "100", "80", size="1.2", notional="120")
    kurz = _pos(Side.SHORT, "100", "125", size="1", notional="100")
    delta, anteil = net_delta_usd([lang, kurz])
    assert delta == Decimal(20)
    # 20 USD auf das kleinere Bein bezogen (100) = 20 %
    assert anteil == Decimal("0.2")


def test_einseitige_position_ist_voll_gerichtet():
    lang = _pos(Side.LONG, "100", "80", size="1", notional="100")
    delta, anteil = net_delta_usd([lang])
    assert delta == Decimal(100)
    assert anteil == Decimal(1)


def test_ohne_positionen_kein_delta():
    assert net_delta_usd([]) == (Decimal(0), Decimal(0))
