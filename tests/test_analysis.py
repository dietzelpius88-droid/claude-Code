"""Entitaets-Sentiment, Duplikaterkennung, Datenueberraschung und Fusion."""
import pytest

from newsbot.analysis.dedup import NoveltyTracker, jaccard, shingles, normalize
from newsbot.analysis.entity_sentiment import score_entity_sentiment
from newsbot.analysis.pipeline import _Component, fuse
from newsbot.analysis.surprise import parse_surprise
from newsbot.models import EventClass


# ---------------------------------------------------------------- Entitaeten
@pytest.fixture
def cluster_of(universe):
    return {a.symbol: a.cluster for a in universe.all()}


def test_lob_fuer_benanntes_asset(universe, cluster_of):
    t = "I looked at Hyperliquid, it is very impressive, tremendous technology, we support it."
    r = score_entity_sentiment(t, universe.extract_entities(t), cluster_of)
    assert r is not None
    assert r.sentiment > 0.6
    assert r.symbols[0] == "HYPE"
    assert r.event_class is EventClass.CRYPTO_POLICY


def test_kritik_fuer_benanntes_asset(universe, cluster_of):
    t = "Bitcoin is a scam and a fraud, based on thin air"
    r = score_entity_sentiment(t, universe.extract_entities(t), cluster_of)
    assert r is not None and r.sentiment < -0.6 and r.symbols[0] == "BTC"


def test_fremdzuschreibung_wird_gedaempft(universe, cluster_of):
    direkt = "Hyperliquid is impressive, tremendous technology"
    fremd = "Some people say Hyperliquid is impressive"
    a = score_entity_sentiment(direkt, universe.extract_entities(direkt), cluster_of)
    b = score_entity_sentiment(fremd, universe.extract_entities(fremd), cluster_of)
    assert b.sentiment < a.sentiment
    assert b.confidence < a.confidence


def test_aktienindex_nutzt_diesen_pfad_nicht(universe, cluster_of):
    """Lob auf den Aktienmarkt bewegt keinen Kurs - nur die Politik dahinter."""
    t = "The stock market is doing great, tremendous numbers"
    assert score_entity_sentiment(t, universe.extract_entities(t), cluster_of) is None


def test_lob_ohne_bezug_ergibt_nichts(universe, cluster_of):
    t = "This is tremendous, absolutely impressive work by everyone"
    assert score_entity_sentiment(t, universe.extract_entities(t), cluster_of) is None


# ---------------------------------------------------------------- Duplikate
def test_wortgleiches_duplikat_ist_wertlos():
    t = NoveltyTracker(window_s=1800)
    txt = "Fed cuts rates by 50 basis points in an emergency move"
    assert t.assess_and_register(txt) == 1.0
    assert t.assess_and_register(txt) < 0.1


def test_gleiche_aussage_andere_worte():
    t = NoveltyTracker(window_s=1800)
    claim = "monetary_policy|+1|BTC,ES|beschluss"
    t.assess_and_register("Fed cuts rates by 50 basis points", claim=claim)
    zweit = t.assess_and_register("Federal Reserve lowers its target by 50bp", claim=claim)
    assert zweit < 0.3, "Zweitmeldung ueber anderen Feed muss als Duplikat gelten"


def test_andere_aussage_bleibt_neu():
    t = NoveltyTracker(window_s=1800)
    t.assess_and_register("Fed cuts rates", claim="monetary_policy|+1||beschluss")
    assert t.assess_and_register("Trump imposes tariffs on China",
                                 claim="trade_policy|-1|HG|beschluss") == 1.0


def test_neuigkeit_erholt_sich_mit_der_zeit():
    import time
    t = NoveltyTracker(window_s=1800)
    now = time.time()
    claim = "monetary_policy|+1||beschluss"
    t.assess_and_register("Fed cuts rates by 50bp", claim=claim, now=now)
    frisch = t.assess("Fed cuts rates by 50bp", claim=claim, now=now + 10)
    spaeter = t.assess("Fed cuts rates by 50bp", claim=claim, now=now + 1700)
    assert frisch < spaeter


def test_signatur_trennt_spekulation_von_beschluss():
    a = NoveltyTracker.claim_signature("trade_policy", -1, [], "spekulation")
    b = NoveltyTracker.claim_signature("trade_policy", -1, [], "beschluss")
    assert a != b


def test_shingle_hilfsfunktionen():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a b c"}, {"a b c"}) == 1.0
    assert len(shingles(normalize("eins zwei drei vier"), 3)) == 2


# ---------------------------------------------------------------- Ueberraschung
@pytest.mark.parametrize("text,vorzeichen", [
    ("US CPI YoY 3.4% vs 3.1% expected", -1),      # heisser -> schlecht fuer Risiko
    ("US CPI YoY 2.8% vs 3.1% expected", +1),
    ("Nonfarm payrolls 310k vs 190k forecast", +1),
    ("Core PCE 2.6% vs 2.8% expected", +1),
    ("Unemployment rate 4.5% vs 4.1% expected", -1),
])
def test_ueberraschung_vorzeichen(text, vorzeichen):
    r = parse_surprise(text)
    assert r is not None
    assert (r.sentiment > 0) == (vorzeichen > 0), f"{text} -> {r.sentiment}"


def test_grosse_ueberraschung_erzeugt_mehr_vertrauen():
    klein = parse_surprise("US CPI YoY 3.15% vs 3.1% expected")
    gross = parse_surprise("US CPI YoY 3.7% vs 3.1% expected")
    assert gross.confidence > klein.confidence
    assert abs(gross.sentiment) > abs(klein.sentiment)


def test_ohne_erwartungswert_kein_ergebnis():
    assert parse_surprise("CPI report released today") is None
    assert parse_surprise("Trump signs tariff order") is None


# ---------------------------------------------------------------- Fusion
def test_fusion_stumme_bausteine_verwaessern_nicht():
    """Nur der Entitaets-Pfad hat etwas erkannt - das Ergebnis muss stehen."""
    s, c = fuse([
        _Component("lexicon", 0.0, 0.0, 0.40),
        _Component("entity", 0.9, 0.8, 0.25),
        _Component("surprise", 0.0, 0.0, 0.15),
    ])
    assert s == pytest.approx(0.9)
    assert c == pytest.approx(0.8, abs=0.01)


def test_fusion_bestaetigung_erhoeht_vertrauen():
    allein, c1 = fuse([_Component("lexicon", 0.8, 0.7, 0.40)])
    _, c2 = fuse([_Component("lexicon", 0.8, 0.7, 0.40),
                  _Component("llm", 0.8, 0.7, 0.45)])
    assert c2 > c1


def test_fusion_ohne_aktive_bausteine():
    assert fuse([_Component("x", 0.5, 0.0, 1.0)]) == (0.0, 0.0)


def test_fusion_mittelt_widerspruch_aus():
    s, _ = fuse([_Component("a", 1.0, 0.8, 0.5), _Component("b", -1.0, 0.8, 0.5)])
    assert abs(s) < 0.05
