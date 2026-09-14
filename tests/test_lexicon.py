"""Regelbasierte Auswertung: Klassen, Vorzeichen und Modifikatoren."""
import pytest

from newsbot.analysis.lexicon import score_text
from newsbot.models import EventClass


@pytest.mark.parametrize("text,ec,sign", [
    ("Fed cuts interest rates by 50 basis points", EventClass.MONETARY_POLICY, +1),
    ("Fed raises rates by 25 basis points", EventClass.MONETARY_POLICY, -1),
    ("President signs order imposing tariffs on all Chinese imports",
     EventClass.TRADE_POLICY, -1),
    ("US and China reach a trade deal, tariffs lifted", EventClass.TRADE_POLICY, +1),
    ("White House announces a strategic bitcoin reserve", EventClass.CRYPTO_POLICY, +1),
    ("SEC files enforcement action over unregistered securities",
     EventClass.CRYPTO_POLICY, -1),
    ("OPEC cuts output by two million barrels", EventClass.ENERGY_POLICY, +1),
    ("Ceasefire agreed, both sides confirm", EventClass.GEOPOLITICS, +1),
    ("Missile strike reported near the capital", EventClass.GEOPOLITICS, -1),
    ("Zinssenkung beschlossen", EventClass.MONETARY_POLICY, +1),
    ("Regierung verhaengt Zoelle auf Importe", EventClass.TRADE_POLICY, -1),
])
def test_klasse_und_vorzeichen(text, ec, sign):
    r = score_text(text)
    assert r.event_class is ec, f"{text} -> {r.event_class}"
    assert (r.sentiment > 0) == (sign > 0), f"{text} -> {r.sentiment}"


def test_verneinung_dreht_das_vorzeichen():
    ja = score_text("Trump will impose tariffs on the EU")
    nein = score_text("Trump denies he will impose tariffs on the EU")
    assert ja.sentiment < 0 < nein.sentiment
    # Verneinungen sind fehleranfaellig -> weniger Vertrauen
    assert nein.confidence < ja.confidence


def test_abschwaechung_senkt_ueberzeugung():
    fest = score_text("Trump signs an order imposing tariffs on European cars")
    vage = score_text("Trump says he is considering tariffs on European cars")
    assert abs(vage.sentiment) < abs(fest.sentiment)
    assert vage.confidence < fest.confidence * 0.6
    assert "hedge" in vage.modifiers


def test_rueckblick_wird_fast_geloescht():
    neu = score_text("Powell says inflation remains too high")
    alt = score_text("Last week Powell reiterated that inflation remains too high")
    assert abs(alt.sentiment) < abs(neu.sentiment) * 0.5
    assert "retrospective" in alt.modifiers


def test_zitat_eines_kritikers_zaehlt_kaum():
    direkt = score_text("The president will impose tariffs on China")
    kritik = score_text("Critics warn that the president will impose tariffs on China")
    assert abs(kritik.sentiment) < abs(direkt.sentiment)
    assert "attribution" in kritik.modifiers


def test_verstaerker_hebt_an():
    schlicht = score_text("Fed cuts rates")
    stark = score_text("Fed announces emergency rate cut, effective immediately")
    assert stark.sentiment > schlicht.sentiment
    assert "intensifier" in stark.modifiers


def test_zahlen_erhoehen_konkretheit():
    vage = score_text("Fed cuts rates")
    konkret = score_text("Fed cuts rates by 50 basis points on March 18")
    assert konkret.specificity > vage.specificity


def test_leerer_text_ist_neutral():
    r = score_text("")
    assert r.event_class is EventClass.UNKNOWN
    assert r.sentiment == 0.0 and r.confidence == 0.0


def test_ohne_treffer_kein_signal():
    r = score_text("Der Wetterbericht meldet Regen fuer das Wochenende")
    assert r.event_class is EventClass.UNKNOWN
    assert r.confidence == 0.0


def test_auswertung_ist_schnell():
    """Der Fast Path muss im Millisekundenbereich bleiben."""
    import time
    text = ("Fed announces emergency rate cut of 50 basis points while the "
            "president signs an order imposing tariffs on Chinese imports " * 3)
    t0 = time.perf_counter()
    for _ in range(200):
        score_text(text)
    ms = (time.perf_counter() - t0) * 1000 / 200
    assert ms < 5.0, f"{ms:.2f} ms pro Auswertung ist zu langsam"
