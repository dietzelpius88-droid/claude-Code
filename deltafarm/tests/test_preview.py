"""Vorschau: Hash, Altersgrenze, Toleranz.

Der Auftrag verlangt, dass POST /api/pairs/open nur eine Vorschau annimmt, die
wenige Sekunden alt ist, und ablehnt, wenn sich Preise oder Funding seither
ueber die Toleranz bewegt haben. Damit kann ein alter Browser-Tab keine
ungewollte Order ausloesen.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.preview import (
    PreviewExpired,
    PreviewMoved,
    PreviewStore,
    PreviewUnknown,
)


class Uhr:
    def __init__(self) -> None:
        self.jetzt = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.jetzt

    def vor(self, sekunden: float) -> None:
        self.jetzt += timedelta(seconds=sekunden)


def _store(**kwargs) -> tuple[PreviewStore, Uhr]:
    uhr = Uhr()
    return PreviewStore(clock=uhr, **kwargs), uhr


MARKS = {"lighter": Decimal(64000), "extended": Decimal(64010)}
FUNDING = {"lighter": Decimal("-0.00001"), "extended": Decimal("0.00002")}


PLAN = {
    "symbol": "BTC-PERP",
    "size": "0.1",
    "long_venue": "lighter",
    "short_venue": "extended",
}


def _ablegen(store: PreviewStore, nutzdaten=None) -> str:
    return store.put(payload=nutzdaten or PLAN, marks=MARKS, funding=FUNDING)


# --- Token ----------------------------------------------------------------

def test_token_wird_erzeugt_und_ist_wiederauffindbar():
    store, _ = _store()
    token = _ablegen(store)
    assert token
    assert store.get(token) is not None


def test_gleiche_daten_ergeben_nicht_denselben_token():
    """Sonst liesse sich eine alte Vorschau durch Nachbauen wiederbeleben."""
    store, _ = _store()
    assert _ablegen(store) != _ablegen(store)


def test_unbekannter_token_wird_abgelehnt():
    store, _ = _store()
    with pytest.raises(PreviewUnknown):
        store.validate("gibtsnicht", marks=MARKS, funding=FUNDING)


def test_token_ist_nur_einmal_verwendbar():
    """Nach der Ausfuehrung ist die Vorschau verbraucht.

    Ein zweiter Klick auf denselben Knopf darf nicht zu einer zweiten Position
    fuehren.
    """
    store, _ = _store()
    token = _ablegen(store)
    store.consume(token, marks=MARKS, funding=FUNDING)

    with pytest.raises(PreviewUnknown):
        store.consume(token, marks=MARKS, funding=FUNDING)


# --- Alter ----------------------------------------------------------------

def test_frische_vorschau_wird_angenommen():
    store, uhr = _store(max_age_seconds=15)
    token = _ablegen(store)
    uhr.vor(5)
    store.validate(token, marks=MARKS, funding=FUNDING)  # wirft nicht


def test_zu_alte_vorschau_wird_abgelehnt():
    store, uhr = _store(max_age_seconds=15)
    token = _ablegen(store)
    uhr.vor(20)
    with pytest.raises(PreviewExpired, match="15"):
        store.validate(token, marks=MARKS, funding=FUNDING)


def test_altersgrenze_ist_konfigurierbar():
    store, uhr = _store(max_age_seconds=60)
    token = _ablegen(store)
    uhr.vor(30)
    store.validate(token, marks=MARKS, funding=FUNDING)


# --- Preisbewegung --------------------------------------------------------

def test_kleine_preisbewegung_bleibt_im_rahmen():
    store, _ = _store(price_tolerance=Decimal("0.002"))
    token = _ablegen(store)
    store.validate(
        token,
        marks={"lighter": Decimal("64050"), "extended": Decimal(64010)},
        funding=FUNDING,
    )


def test_grosse_preisbewegung_wird_abgelehnt():
    store, _ = _store(price_tolerance=Decimal("0.002"))
    token = _ablegen(store)
    with pytest.raises(PreviewMoved, match="lighter"):
        store.validate(
            token,
            marks={"lighter": Decimal("65000"), "extended": Decimal(64010)},
            funding=FUNDING,
        )


def test_fehlender_preis_wird_abgelehnt():
    # Keine Aussage ist kein Freibrief.
    store, _ = _store()
    token = _ablegen(store)
    with pytest.raises(PreviewMoved):
        store.validate(token, marks={"extended": Decimal(64010)}, funding=FUNDING)


# --- Funding --------------------------------------------------------------

def test_gekipptes_vorzeichen_wird_abgelehnt():
    """Der Fall aus Preflight-Pruefung 5.

    Dreht das Funding zwischen Anzeige und Ausfuehrung, ist die geplante
    Richtung falsch herum - dann wird abgebrochen.
    """
    store, _ = _store()
    token = _ablegen(store)
    gekippt = {"lighter": Decimal("0.00002"), "extended": Decimal("-0.00001")}
    with pytest.raises(PreviewMoved, match="Funding"):
        store.validate(token, marks=MARKS, funding=gekippt)


def test_kleine_funding_aenderung_bleibt_im_rahmen():
    store, _ = _store()
    token = _ablegen(store)
    leicht = {"lighter": Decimal("-0.000009"), "extended": Decimal("0.000021")}
    store.validate(token, marks=MARKS, funding=leicht)


def test_netto_funding_faellt_auf_null_ohne_dass_ein_vorzeichen_kippt():
    """Die Nettopruefung muss eigenstaendig greifen.

    Hier behaelt jede Boerse ihr Vorzeichen - die Long-Seite bleibt negativ,
    die Short-Seite positiv. Nur der Abstand ist verschwunden, und damit der
    Ertrag. Das faengt die Vorzeichenpruefung nicht ab.
    """
    store, _ = _store()
    # Beide Raten positiv: long 0,001 %, short 0,004 % -> Netto 0,003 %.
    beide_positiv = {"lighter": Decimal("0.00001"), "extended": Decimal("0.00004")}
    token = store.put(payload=PLAN, marks=MARKS, funding=beide_positiv)

    # Die Raten laufen zusammen, beide bleiben positiv - aber der Abstand
    # kehrt sich um und das Paar verdient nichts mehr.
    zusammengelaufen = {"lighter": Decimal("0.00005"), "extended": Decimal("0.00004")}
    with pytest.raises(PreviewMoved, match="Netto"):
        store.validate(token, marks=MARKS, funding=zusammengelaufen)


def test_gedrehtes_vorzeichen_wird_unabhaengig_davon_erkannt():
    store, _ = _store()
    token = _ablegen(store)
    flach = {"lighter": Decimal("0.00002"), "extended": Decimal("0.00002")}
    with pytest.raises(PreviewMoved):
        store.validate(token, marks=MARKS, funding=flach)


# --- Aufraeumen -----------------------------------------------------------

def test_abgelaufene_vorschauen_werden_entfernt():
    store, uhr = _store(max_age_seconds=10)
    token = _ablegen(store)
    uhr.vor(600)
    store.purge()
    with pytest.raises(PreviewUnknown):
        store.validate(token, marks=MARKS, funding=FUNDING)


def test_nutzdaten_bleiben_erhalten():
    store, _ = _store()
    token = store.put(payload={"symbol": "ETH-PERP"}, marks=MARKS, funding=FUNDING)
    assert store.get(token).payload["symbol"] == "ETH-PERP"
