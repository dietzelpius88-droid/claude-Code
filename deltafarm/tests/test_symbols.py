"""Symboltabelle: Uebersetzung in beide Richtungen, keine Rateversuche."""

import pytest

from app.core.symbols import (
    EXTENDED,
    LIGHTER,
    SYMBOL_MAP,
    UnknownSymbol,
    canonical_symbols,
    symbols_for,
    to_canonical,
    to_native,
    unavailable_symbols,
)


def test_kanonisch_nach_extended():
    # Belegt aus x10xchange/python_sdk: Maerkte heissen dort "BTC-USD".
    assert to_native(EXTENDED, "BTC-PERP") == "BTC-USD"


def test_kanonisch_nach_lighter():
    # Belegt aus elliottech/lighter-python: Symbol ist der Basiswert.
    assert to_native(LIGHTER, "BTC-PERP") == "BTC"


def test_rueckuebersetzung():
    assert to_canonical(EXTENDED, "ETH-USD") == "ETH-PERP"
    assert to_canonical(LIGHTER, "ETH") == "ETH-PERP"


def test_unbekannte_boersenschreibweise_gibt_none():
    # Ein unbekanntes Listing wird ignoriert, nicht erraten.
    assert to_canonical(EXTENDED, "PEPE-USD") is None


def test_unbekanntes_symbol_wirft():
    with pytest.raises(UnknownSymbol):
        to_native(EXTENDED, "PEPE-PERP")


def test_symbol_ohne_eintrag_fuer_diese_boerse_wirft():
    with pytest.raises(UnknownSymbol):
        to_native("variational", "BTC-PERP")


def test_jede_boerse_fuehrt_alle_kanonischen_symbole():
    # Sonst entstehen Paare, die es nie geben kann.
    for symbol in canonical_symbols():
        assert EXTENDED in SYMBOL_MAP[symbol]
        assert LIGHTER in SYMBOL_MAP[symbol]


def test_keine_doppelten_boersenschreibweisen():
    # Zwei kanonische Symbole duerfen nicht auf dasselbe Listing zeigen.
    for venue in (EXTENDED, LIGHTER):
        nativ = [SYMBOL_MAP[s][venue] for s in symbols_for(venue)]
        assert len(nativ) == len(set(nativ))


def test_fehlende_listings_werden_gemeldet():
    vorhanden = ["BTC-USD", "ETH-USD"]
    fehlend = unavailable_symbols(EXTENDED, vorhanden)
    assert "SOL-PERP" in fehlend
    assert "BTC-PERP" not in fehlend


def test_alle_listings_vorhanden_ergibt_leere_meldung():
    vorhanden = [SYMBOL_MAP[s][LIGHTER] for s in symbols_for(LIGHTER)]
    assert unavailable_symbols(LIGHTER, vorhanden) == ()
