"""Zwei-Geschwindigkeiten-Pipeline: provisorisch, bestaetigt, Abbruch."""
import pytest

from newsbot.analysis.llm import LLMAnalyzer, make_stub_backend
from newsbot.analysis.pipeline import AnalysisPipeline
from newsbot.config import LLMConfig, SignalConfig
from newsbot.models import RawNews, SourceTier, now_ms


def _llm(responses, delay_ms=0, symbols=None):
    return LLMAnalyzer(LLMConfig(), symbols or ["BTC", "ETH", "SOL", "HYPE", "ES", "NQ", "HG"],
                       backend=make_stub_backend(responses, delay_ms=delay_ms))


def _verdict(**kw):
    base = dict(event_class="crypto_policy", sentiment=0.85, confidence=0.8,
                specificity=0.6, targets=["HYPE"], half_life_s=1200,
                is_restatement=False, is_contradiction=False, rationale="test")
    base.update(kw)
    return base


async def _events(pipe, news):
    return [ev async for ev in pipe.process(news)]


async def test_eindeutige_primaerquelle_loest_sofort_aus(universe):
    pipe = AnalysisPipeline(SignalConfig(), llm=None, universe=universe)
    news = RawNews("fed_press", SourceTier.PRIMARY,
                   "Fed announces emergency rate cut of 50 basis points, effective immediately")
    evs = await _events(pipe, news)
    assert evs, "eindeutige Meldung muss ein Ereignis erzeugen"
    assert evs[0].meta.get("provisional") is True
    assert evs[0].conviction >= SignalConfig().min_conviction_instant


async def test_vage_meldung_erzeugt_nichts(universe):
    pipe = AnalysisPipeline(SignalConfig(), llm=None, universe=universe)
    news = RawNews("reuters", SourceTier.WIRE,
                   "Trump says he is considering tariffs on European cars")
    assert await _events(pipe, news) == []


async def test_unsichere_quelle_braucht_bestaetigung(universe):
    """Stufe 5 darf allein nie handeln, egal wie eindeutig der Text ist."""
    pipe = AnalysisPipeline(SignalConfig(), llm=None, universe=universe)
    news = RawNews("social:anon", SourceTier.SOCIAL,
                   "BREAKING: Fed announces emergency rate cut of 100 basis points")
    assert await _events(pipe, news) == []


async def test_bestaetigung_durch_modell(universe):
    pipe = AnalysisPipeline(SignalConfig(),
                            llm=_llm({"Hyperliquid": _verdict()}), universe=universe)
    news = RawNews("speech_potus", SourceTier.PRIMARY,
                   "I looked at Hyperliquid, it is impressive, tremendous technology, we support it")
    evs = await _events(pipe, news)
    assert evs and evs[-1].analyzer == "fusion"
    assert evs[-1].conviction >= SignalConfig().min_conviction
    assert "HYPE" in evs[-1].target_hints


async def test_widerspruch_wird_gemeldet(universe):
    pipe = AnalysisPipeline(SignalConfig(),
                            llm=_llm({"walks back": _verdict(is_contradiction=True,
                                                            sentiment=-0.5)}),
                            universe=universe)
    news = RawNews("reuters", SourceTier.WIRE,
                   "White House walks back the crypto reserve announcement, no strategic bitcoin reserve planned")
    evs = await _events(pipe, news)
    assert any(e.meta.get("contradiction") for e in evs)


async def test_dissens_verhindert_handel(universe):
    """Lexikon stark bullisch, Modell stark baerisch -> kein Trade."""
    pipe = AnalysisPipeline(SignalConfig(),
                            llm=_llm({"emergency rate cut":
                                      _verdict(event_class="monetary_policy",
                                               sentiment=-0.9, confidence=0.85)}),
                            universe=universe)
    news = RawNews("fed_press", SourceTier.PRIMARY,
                   "Fed announces emergency rate cut of 50 basis points, effective immediately")
    evs = await _events(pipe, news)
    # Der provisorische Trade wird erzeugt, die Bestaetigung bricht ihn ab.
    assert evs[0].meta.get("provisional") is True
    assert evs[-1].meta.get("abort") is True


async def test_zu_alte_meldung_wird_verworfen(universe):
    pipe = AnalysisPipeline(SignalConfig(), llm=None, universe=universe)
    now = now_ms()
    news = RawNews("fed_press", SourceTier.PRIMARY,
                   "Fed announces emergency rate cut of 50 basis points",
                   published_ms=now - 90_000, received_ms=now)
    assert await _events(pipe, news) == []


async def test_llm_timeout_faellt_auf_lexikon_zurueck(universe):
    cfg = LLMConfig(timeout_ms=40, max_retries=0)
    llm = LLMAnalyzer(cfg, ["BTC", "ES"],
                      backend=make_stub_backend({"Fed": _verdict()}, delay_ms=400))
    pipe = AnalysisPipeline(SignalConfig(), llm=llm, universe=universe)
    news = RawNews("fed_press", SourceTier.PRIMARY,
                   "Fed announces emergency rate cut of 50 basis points, effective immediately")
    evs = await _events(pipe, news)
    assert evs, "Timeout darf den Bot nicht blockieren"
    assert llm.stats["timeouts"] == 1
    assert evs[0].analyzer == "lexicon"


async def test_wiederholung_senkt_neuigkeit(universe):
    pipe = AnalysisPipeline(SignalConfig(), llm=None, universe=universe)
    text = "Fed announces emergency rate cut of 50 basis points, effective immediately"
    first = await _events(pipe, RawNews("fed_press", SourceTier.PRIMARY, text))
    second = await _events(pipe, RawNews("aggregator", SourceTier.WIRE, text))
    assert first[0].novelty == 1.0
    assert not second or second[0].novelty < 0.3
