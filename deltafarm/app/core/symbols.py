"""Uebersetzung zwischen kanonischem Symbol und Boersenschreibweise.

Kein String-Raten zur Laufzeit: die Zuordnung steht hier als Tabelle. Belegt
aus den offiziellen SDKs - Extended fuehrt Maerkte als "BTC-USD"
(x10xchange/python_sdk, tests/fixtures), Lighter fuehrt sie unter dem
Basiswert "BTC" mit einer numerischen market_id (elliottech/lighter-python,
openapi.json).

Die numerische market_id von Lighter wird beim Start aus der Marktliste der
Boerse aufgeloest. Das ist kein Raten, sondern ein Nachschlagen des hier
deklarierten Symbols in den Daten der Boerse - schlaegt es fehl, sagt der
Adapter das laut, statt sich etwas auszudenken.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

EXTENDED = "extended"
LIGHTER = "lighter"

# kanonisches Symbol -> Boerse -> Schreibweise der Boerse
SYMBOL_MAP: Mapping[str, Mapping[str, str]] = {
    "BTC-PERP": {EXTENDED: "BTC-USD", LIGHTER: "BTC"},
    "ETH-PERP": {EXTENDED: "ETH-USD", LIGHTER: "ETH"},
    "SOL-PERP": {EXTENDED: "SOL-USD", LIGHTER: "SOL"},
    "AVAX-PERP": {EXTENDED: "AVAX-USD", LIGHTER: "AVAX"},
    "LINK-PERP": {EXTENDED: "LINK-USD", LIGHTER: "LINK"},
    "DOGE-PERP": {EXTENDED: "DOGE-USD", LIGHTER: "DOGE"},
    "XRP-PERP": {EXTENDED: "XRP-USD", LIGHTER: "XRP"},
    "SUI-PERP": {EXTENDED: "SUI-USD", LIGHTER: "SUI"},
}


class UnknownSymbol(KeyError):
    """Das Symbol steht nicht in der Tabelle."""


def canonical_symbols() -> tuple[str, ...]:
    return tuple(SYMBOL_MAP)


def to_native(venue: str, canonical: str) -> str:
    """Kanonisches Symbol in die Schreibweise der Boerse uebersetzen."""
    eintrag = SYMBOL_MAP.get(canonical)
    if eintrag is None:
        raise UnknownSymbol(f"{canonical} steht nicht in der Symboltabelle.")
    native = eintrag.get(venue)
    if native is None:
        raise UnknownSymbol(f"{canonical} ist fuer {venue} nicht hinterlegt.")
    return native


def to_canonical(venue: str, native: str) -> Optional[str]:
    """Boersenschreibweise zurueck in das kanonische Symbol. None, wenn unbekannt."""
    for kanonisch, eintrag in SYMBOL_MAP.items():
        if eintrag.get(venue) == native:
            return kanonisch
    return None


def symbols_for(venue: str) -> tuple[str, ...]:
    """Alle kanonischen Symbole, die fuer diese Boerse hinterlegt sind."""
    return tuple(s for s, e in SYMBOL_MAP.items() if venue in e)


def unavailable_symbols(venue: str, available_native: Iterable[str]) -> tuple[str, ...]:
    """Deklarierte Symbole, die die Boerse tatsaechlich nicht fuehrt.

    Wird beim Start aufgerufen. Listings aendern sich, deshalb ist das eine
    Warnung und kein Abbruch - aber eine sichtbare.
    """
    vorhanden = set(available_native)
    return tuple(
        s for s in symbols_for(venue) if SYMBOL_MAP[s][venue] not in vorhanden
    )
