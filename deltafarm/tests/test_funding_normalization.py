"""Funding-Normalisierung gegen von Hand gerechnete Referenzwerte.

Der wichtigste Test des Projekts: eine 8h-Rate darf niemals unnormalisiert
neben einer 1h-Rate stehen. Die Referenzwerte sind bewusst so gewaehlt, dass
1h-, 4h- und 8h-Angaben derselben wirtschaftlichen Rate entsprechen und nach
der Normalisierung identisch sein muessen.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.funding import (
    HOURS_PER_YEAR,
    infer_interval_hours,
    net_hourly_rate,
    normalize_to_hourly,
    preferred_direction,
    to_apr,
)
from app.models.domain import Side


# --- Normalisierung auf die Stundenrate ---------------------------------

def test_one_hour_rate_bleibt_unveraendert():
    assert normalize_to_hourly(Decimal("0.0000125"), Decimal(1)) == Decimal("0.0000125")


def test_acht_stunden_rate_wird_gedrittelt_durch_acht():
    # 0,01 % pro 8 h = 0,00125 % pro Stunde
    assert normalize_to_hourly(Decimal("0.0001"), Decimal(8)) == Decimal("0.0000125")


def test_vier_stunden_rate_wird_halbiert_gegenueber_acht():
    # Aster liefert je Symbol 4 h oder 8 h - beide muessen hier zusammenfallen.
    assert normalize_to_hourly(Decimal("0.00005"), Decimal(4)) == Decimal("0.0000125")


def test_drei_intervalle_derselben_wirtschaftlichen_rate_sind_gleich():
    """Der Kern der Anforderung: gleiche Wirtschaftlichkeit, gleiche Kennzahl."""
    stunde = normalize_to_hourly(Decimal("0.0000125"), Decimal(1))
    vier = normalize_to_hourly(Decimal("0.00005"), Decimal(4))
    acht = normalize_to_hourly(Decimal("0.0001"), Decimal(8))
    assert stunde == vier == acht


def test_negative_rate_behaelt_vorzeichen():
    assert normalize_to_hourly(Decimal("-0.0001"), Decimal(8)) == Decimal("-0.0000125")


def test_intervall_null_oder_negativ_wird_abgelehnt():
    # Lieber ein Fehler als eine stillschweigend falsche Kennzahl.
    with pytest.raises(ValueError):
        normalize_to_hourly(Decimal("0.0001"), Decimal(0))
    with pytest.raises(ValueError):
        normalize_to_hourly(Decimal("0.0001"), Decimal(-8))


def test_normalisierung_bleibt_exakt_ohne_float_drift():
    # 0,03 % pro 8 h, normalisiert und ueber einen Tag aufsummiert.
    # Dieselbe Rechnung ergibt mit float 0.0008999999999999999 - also einen
    # Wert, der nach 24 Stunden bereits von der Wahrheit abweicht.
    hourly = normalize_to_hourly(Decimal("0.0003"), Decimal(8))
    assert sum([hourly] * 24, Decimal(0)) == Decimal("0.0009")
    assert sum([float(hourly)] * 24) != 0.0009


# --- APR ------------------------------------------------------------------

def test_apr_basiert_auf_8760_stunden():
    assert HOURS_PER_YEAR == Decimal(24 * 365)


def test_apr_von_hand_gerechnet():
    # 0,00125 % pro Stunde * 8760 = 10,95 % p. a.
    assert to_apr(Decimal("0.0000125")) == Decimal("0.1095")


def test_apr_negativ_wenn_rate_negativ():
    assert to_apr(Decimal("-0.0000125")) == Decimal("-0.1095")


# --- Vorzeichenlogik ------------------------------------------------------

def test_positive_rate_bedeutet_short_empfaengt():
    # Konvention: Rate > 0 heisst, Long zahlt an Short.
    assert preferred_direction(Decimal("0.00002")) is Side.SHORT


def test_negative_rate_bedeutet_long_empfaengt():
    assert preferred_direction(Decimal("-0.00002")) is Side.LONG


def test_rate_null_faellt_per_konvention_auf_short():
    assert preferred_direction(Decimal(0)) is Side.SHORT


# --- Netto-Funding eines Paares ------------------------------------------

def test_netto_rate_long_zahlt_short_empfaengt():
    """Long auf A (-0,001 %/h), Short auf B (+0,002 %/h).

    Auf A bekommt der Long 0,001 %/h, auf B bekommt der Short 0,002 %/h.
    Summe von Hand: 0,003 % pro Stunde.
    """
    net = net_hourly_rate(
        long_rate_hourly=Decimal("-0.00001"),
        short_rate_hourly=Decimal("0.00002"),
    )
    assert net == Decimal("0.00003")


def test_netto_rate_wird_negativ_wenn_richtung_falsch_herum():
    # Genau der Fall, vor dem Preflight-Pruefung 5 schuetzen soll.
    net = net_hourly_rate(
        long_rate_hourly=Decimal("0.00002"),
        short_rate_hourly=Decimal("-0.00001"),
    )
    assert net == Decimal("-0.00003")


def test_gleiche_raten_ergeben_kein_netto_funding():
    net = net_hourly_rate(
        long_rate_hourly=Decimal("0.00002"),
        short_rate_hourly=Decimal("0.00002"),
    )
    assert net == Decimal(0)


def test_netto_apr_eines_realistischen_paares():
    net = net_hourly_rate(
        long_rate_hourly=Decimal("-0.00001"),
        short_rate_hourly=Decimal("0.00002"),
    )
    # 0,00003 * 8760 = 0,2628 -> 26,28 % p. a.
    assert to_apr(net) == Decimal("0.26280")


# --- Empirische Intervallpruefung (ADR-004) -------------------------------

def _stamps(count: int, spacing_hours: float) -> list[datetime]:
    start = datetime(2026, 9, 18, tzinfo=timezone.utc)
    return [start + timedelta(hours=spacing_hours * i) for i in range(count)]


def test_intervall_aus_stuendlichen_zeitstempeln_erkannt():
    assert infer_interval_hours(_stamps(24, 1.0)) == Decimal(1)


def test_intervall_aus_achtstuendigen_zeitstempeln_erkannt():
    assert infer_interval_hours(_stamps(12, 8.0)) == Decimal(8)


def test_intervall_toleriert_einzelnen_ausreisser():
    # Der Median ist unempfindlich gegen eine einzelne Luecke, etwa nach
    # einer Wartung der Boerse.
    stamps = _stamps(10, 1.0)
    stamps.append(stamps[-1] + timedelta(hours=9))
    assert infer_interval_hours(stamps) == Decimal(1)


def test_intervall_unbestimmbar_bei_zu_wenigen_punkten():
    assert infer_interval_hours(_stamps(1, 1.0)) is None
    assert infer_interval_hours([]) is None


def test_unsortierte_zeitstempel_werden_sortiert():
    stamps = _stamps(6, 1.0)
    assert infer_interval_hours(list(reversed(stamps))) == Decimal(1)
