"""Sizing: gemeinsame Menge, Rundung, Mindestgroessen, Rest-Delta.

Beide Beine muessen exakt dieselbe Menge bekommen - sonst ist das Paar von
vornherein nicht delta-neutral. Die Menge wird deshalb auf ein Raster gerundet,
das zu beiden Boersen passt, und immer abgerundet: eine zu grosse Position
sprengt die Margin, eine zu kleine kostet nur etwas Ertrag.
"""

from decimal import Decimal

import pytest

from app.core.sizing import (
    SizingError,
    common_lot_size,
    compute_sizing,
)
from app.models.domain import SymbolRules


def _rules(
    venue: str,
    *,
    lot: str = "0.001",
    tick: str = "0.1",
    min_notional: str = "10",
    taker: str | None = "0.0005",
    max_leverage: str = "20",
) -> SymbolRules:
    return SymbolRules(
        venue=venue,
        symbol="BTC-PERP",
        tick_size=Decimal(tick),
        lot_size=Decimal(lot),
        min_notional=Decimal(min_notional),
        max_leverage=Decimal(max_leverage),
        taker_fee=Decimal(taker) if taker is not None else None,
    )


# --- Gemeinsames Raster ---------------------------------------------------

def test_gleiche_lot_size_bleibt_unveraendert():
    assert common_lot_size(Decimal("0.001"), Decimal("0.001")) == Decimal("0.001")


def test_groebere_lot_size_gewinnt():
    # 0,01 ist ein Vielfaches von 0,001 - also passt sie zu beiden.
    assert common_lot_size(Decimal("0.001"), Decimal("0.01")) == Decimal("0.01")


def test_reihenfolge_spielt_keine_rolle():
    assert common_lot_size(Decimal("0.01"), Decimal("0.001")) == common_lot_size(
        Decimal("0.001"), Decimal("0.01")
    )


def test_nicht_teilbare_raster_ergeben_das_kleinste_gemeinsame_vielfache():
    """Der Fall, den ein blosses Maximum falsch loest.

    0,03 ist kein Vielfaches von 0,02. Wuerde man einfach die groebere Groesse
    nehmen, waere die Menge auf einer der beiden Boersen ungueltig.
    """
    assert common_lot_size(Decimal("0.02"), Decimal("0.03")) == Decimal("0.06")


def test_ungueltige_lot_size_wird_abgelehnt():
    with pytest.raises(ValueError):
        common_lot_size(Decimal(0), Decimal("0.01"))


# --- Menge und Rundung ----------------------------------------------------

def test_einfache_aufteilung():
    # 6400 USD bei 64000 USD/BTC = 0,1 BTC
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended"),
        short_rules=_rules("lighter"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.size == Decimal("0.1")
    assert e.long_notional == Decimal(6400)
    assert e.short_notional == Decimal(6400)


def test_menge_wird_auf_das_gemeinsame_raster_abgerundet():
    # 6400 / 64000 = 0,1 -> bei Raster 0,03 abgerundet auf 0,09
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended", lot="0.03"),
        short_rules=_rules("lighter", lot="0.01"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.lot_size == Decimal("0.03")
    assert e.size == Decimal("0.09")


def test_abgerundet_wird_nie_aufgerundet():
    """Eine zu grosse Position sprengt die Margin - eine zu kleine nicht."""
    e = compute_sizing(
        notional_usd=Decimal(6399),
        long_rules=_rules("extended", lot="0.01"),
        short_rules=_rules("lighter", lot="0.01"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.size == Decimal("0.09")
    assert e.size * Decimal(64000) <= Decimal(6399)


def test_beide_beine_bekommen_exakt_dieselbe_menge():
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended", lot="0.001"),
        short_rules=_rules("lighter", lot="0.01"),
        long_mark=Decimal("64000"),
        short_mark=Decimal("64100"),
    )
    assert e.size == e.long_size == e.short_size


# --- Rest-Delta -----------------------------------------------------------

def test_gleiche_preise_ergeben_kein_rest_delta():
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended"),
        short_rules=_rules("lighter"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.residual_delta_usd == Decimal(0)
    assert e.residual_delta_pct == Decimal(0)


def test_preisunterschied_erzeugt_rest_delta():
    """Gleiche Menge, unterschiedliche Preise - das ist das Rest-Delta.

    Bei 0,099 BTC ergeben 64000 gegen 64100 ein Ungleichgewicht von 9,90 USD.
    """
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended"),
        short_rules=_rules("lighter"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64100),
    )
    assert e.size == Decimal("0.099")
    assert e.residual_delta_usd == Decimal("-9.900")
    assert e.residual_delta_pct.quantize(Decimal("0.00001")) == Decimal("-0.00156")


def test_referenzpreis_ist_der_hoehere_mark():
    """Damit kein Bein den gewuenschten Betrag ueberschreitet.

    Waere der niedrigere Preis die Grundlage, laege die Position auf der
    teureren Boerse ueber dem Betrag, den der Nutzer eingegeben hat - und
    damit auch ueber der Margin, die er dafuer eingeplant hat.
    """
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended"),
        short_rules=_rules("lighter"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64100),
    )
    assert e.long_notional <= Decimal(6400)
    assert e.short_notional <= Decimal(6400)


def test_rest_delta_vorzeichen_folgt_der_long_seite():
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended"),
        short_rules=_rules("lighter"),
        long_mark=Decimal(64100),
        short_mark=Decimal(64000),
    )
    assert e.residual_delta_usd == Decimal("9.900")


# --- Ablehnungen ----------------------------------------------------------

def test_unter_der_mindestgroesse_einer_boerse_wird_abgelehnt():
    # Die Menge waere handelbar, aber 44,80 USD liegen unter Lighters
    # Mindestgroesse von 100 USD.
    with pytest.raises(SizingError, match="lighter"):
        compute_sizing(
            notional_usd=Decimal(50),
            long_rules=_rules("extended", lot="0.0001", min_notional="10"),
            short_rules=_rules("lighter", lot="0.0001", min_notional="100"),
            long_mark=Decimal(64000),
            short_mark=Decimal(64000),
        )


def test_zu_kleiner_betrag_ergibt_menge_null_und_wird_abgelehnt():
    with pytest.raises(SizingError, match="Raster"):
        compute_sizing(
            notional_usd=Decimal(10),
            long_rules=_rules("extended", lot="1"),
            short_rules=_rules("lighter", lot="1"),
            long_mark=Decimal(64000),
            short_mark=Decimal(64000),
        )


def test_negativer_oder_nullbetrag_wird_abgelehnt():
    for betrag in (Decimal(0), Decimal(-100)):
        with pytest.raises(SizingError):
            compute_sizing(
                notional_usd=betrag,
                long_rules=_rules("extended"),
                short_rules=_rules("lighter"),
                long_mark=Decimal(64000),
                short_mark=Decimal(64000),
            )


def test_markpreis_null_wird_abgelehnt():
    with pytest.raises(SizingError):
        compute_sizing(
            notional_usd=Decimal(6400),
            long_rules=_rules("extended"),
            short_rules=_rules("lighter"),
            long_mark=Decimal(0),
            short_mark=Decimal(64000),
        )


# --- Grenzfaelle ----------------------------------------------------------

def test_sehr_grosser_betrag():
    e = compute_sizing(
        notional_usd=Decimal(10_000_000),
        long_rules=_rules("extended"),
        short_rules=_rules("lighter"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.size == Decimal("156.25")
    assert e.long_notional == Decimal(10_000_000)


def test_sehr_kleiner_betrag_knapp_ueber_der_schwelle():
    e = compute_sizing(
        notional_usd=Decimal(20),
        long_rules=_rules("extended", lot="0.0001", min_notional="10"),
        short_rules=_rules("lighter", lot="0.0001", min_notional="10"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.size == Decimal("0.0003")
    assert e.long_notional == Decimal("19.2")


def test_sehr_kleines_raster_bei_guenstigem_asset():
    e = compute_sizing(
        notional_usd=Decimal(100),
        long_rules=_rules("extended", lot="1", min_notional="10"),
        short_rules=_rules("lighter", lot="1", min_notional="10"),
        long_mark=Decimal("0.21"),
        short_mark=Decimal("0.21"),
    )
    assert e.size == Decimal(476)  # 100 / 0,21 = 476,19 -> abgerundet


def test_gebuehrenschaetzung_wird_mitgeliefert():
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended", taker="0.0005"),
        short_rules=_rules("lighter", taker="0.0001"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    # 6400 * 0,0005 + 6400 * 0,0001 = 3,20 + 0,64
    assert e.estimated_open_fees == Decimal("3.84")
    # Eroeffnen und Schliessen
    assert e.estimated_round_trip_fees == Decimal("7.68")


def test_ohne_gebuehren_bleibt_die_schaetzung_offen():
    # Extendeds Gebuehren kommen erst mit Account-Zugang (ADR-005).
    e = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("extended", taker=None),
        short_rules=_rules("lighter", taker="0.0001"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    assert e.estimated_open_fees is None
    assert e.estimated_round_trip_fees is None
