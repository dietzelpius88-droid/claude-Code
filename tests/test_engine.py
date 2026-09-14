"""End-to-End: Nachricht -> Ereignis -> Signal -> Risiko -> Position."""
import json
from pathlib import Path

import pytest

from newsbot.analysis.llm import LLMAnalyzer, make_stub_backend
from newsbot.analysis.pipeline import AnalysisPipeline
from newsbot.backtest.scenario import load_scenario, run_scenario
from newsbot.config import Config, LLMConfig
from newsbot.execution.paper import PaperBroker, PaperFillModel, StaticMarket
from newsbot.models import RawNews, SourceTier
from newsbot.risk.limits import Portfolio
from newsbot.strategy.engine import NewsTradingEngine

ROOT = Path(__file__).resolve().parent.parent


def build(cfg, market, universe, llm=None, seed=11):
    pipeline = AnalysisPipeline(cfg.signal, llm=llm, universe=universe)
    broker = PaperBroker(market, universe,
                         PaperFillModel(latency_ms=250, seed=seed, noise_bps=0.0))
    return NewsTradingEngine(cfg, universe, pipeline, market, broker,
                             Portfolio(cfg.risk.equity_usd))


async def test_eindeutige_meldung_wird_gehandelt(cfg, market, universe):
    eng = build(cfg, market, universe)
    news = RawNews("fed_press", SourceTier.PRIMARY,
                   "Fed announces emergency rate cut of 50 basis points, effective immediately")
    ausgefuehrt = await eng.on_news(news)
    assert ausgefuehrt, "eindeutige Primaermeldung muss eine Position eroeffnen"
    assert eng.pf.positions
    assert eng.stats.opened >= 1


async def test_vage_meldung_wird_nicht_gehandelt(cfg, market, universe):
    eng = build(cfg, market, universe)
    await eng.on_news(RawNews("reuters", SourceTier.WIRE,
                              "Trump says he is considering tariffs on European cars"))
    assert not eng.pf.positions


async def test_unsichere_quelle_wird_nicht_gehandelt(cfg, market, universe):
    eng = build(cfg, market, universe)
    await eng.on_news(RawNews("social:anon", SourceTier.SOCIAL,
                              "BREAKING: Fed cuts rates by 100 basis points"))
    assert not eng.pf.positions


async def test_widerspruch_stellt_glatt(cfg, market, universe):
    stub = make_stub_backend({
        "walks back": dict(event_class="monetary_policy", sentiment=-0.4,
                           confidence=0.8, specificity=0.5, targets=["ES"],
                           half_life_s=900, is_restatement=False,
                           is_contradiction=True, rationale="Ruecknahme")})
    eng = build(cfg, market, universe,
                llm=LLMAnalyzer(LLMConfig(), ["ES", "BTC", "HYPE", "HG"], backend=stub))
    await eng.on_news(RawNews("fed_press", SourceTier.PRIMARY,
                              "Fed announces emergency rate cut of 50 basis points, effective immediately"))
    assert eng.pf.positions, "Vorbedingung: Position muss offen sein"
    await eng.on_news(RawNews("reuters", SourceTier.WIRE,
                              "Fed walks back the rate cut announcement, it was an error"))
    assert not eng.pf.positions, "Widerspruch muss alle betroffenen Positionen schliessen"
    assert eng.stats.aborted >= 1


async def test_abbruch_bei_fehlender_bestaetigung(cfg, market, universe):
    """Der provisorische Trade wird aufgeloest, wenn das Modell widerspricht."""
    stub = make_stub_backend({
        "emergency rate cut": dict(event_class="monetary_policy", sentiment=-0.95,
                                   confidence=0.9, specificity=0.6, targets=["ES"],
                                   half_life_s=900, is_restatement=False,
                                   is_contradiction=False, rationale="Gegenteil")})
    eng = build(cfg, market, universe,
                llm=LLMAnalyzer(LLMConfig(), ["ES", "BTC", "HYPE", "HG"], backend=stub))
    await eng.on_news(RawNews("fed_press", SourceTier.PRIMARY,
                              "Fed announces emergency rate cut of 50 basis points, effective immediately"))
    assert not eng.pf.positions
    assert eng.stats.aborted >= 1


async def test_risikostopp_verhindert_neue_trades(cfg, market, universe):
    eng = build(cfg, market, universe)
    eng.pf.record_close(-2_500)              # ueber der Tagesgrenze
    await eng.on_news(RawNews("fed_press", SourceTier.PRIMARY,
                              "Fed announces emergency rate cut of 50 basis points, effective immediately"))
    assert not eng.pf.positions
    assert eng.stats.rejected >= 1


async def test_gruppenlimit_begrenzt_haeufung(cfg, market, universe):
    """Eine Krypto-Nachricht darf nicht drei korrelierte Altcoins gleichzeitig kaufen."""
    cfg.signal.max_assets_per_event = 4
    for sym, px in [("SOL", 180.0), ("ETH", 3200.0), ("XRP", 2.4), ("DOGE", 0.34)]:
        market.set(sym, px, spread_bps=2.5, depth_usd=3_000_000, atr_bps=20)
    eng = build(cfg, market, universe)
    await eng.on_news(RawNews("whitehouse", SourceTier.PRIMARY,
                              "White House announces a strategic bitcoin reserve, "
                              "executive order signed, effective immediately"))
    cluster = [eng.universe.get(s).cluster for s in eng.pf.positions]
    assert len(cluster) == len(set(cluster)), f"Haeufung in einer Gruppe: {cluster}"


async def test_bericht_ist_vollstaendig(cfg, market, universe):
    eng = build(cfg, market, universe)
    await eng.on_news(RawNews("fed_press", SourceTier.PRIMARY,
                              "Fed announces emergency rate cut of 50 basis points, effective immediately"))
    r = eng.report()
    for schluessel in ("meldungen", "eroeffnet", "pnl_usd", "equity_usd", "wachen"):
        assert schluessel in r
    assert json.dumps(r)          # muss serialisierbar bleiben


# ---------------------------------------------------------------- Szenarien
async def test_szenario_hyperliquid_wird_gehandelt(cfg, universe):
    """Regressionstest fuer den Fall aus der Aufgabenstellung."""
    datei = ROOT / "examples" / "szenario_trump_hyperliquid.jsonl"
    market = StaticMarket()
    eng = build(cfg, market, universe, seed=42)
    bericht = await run_scenario(eng, market, load_scenario(datei), tick_every_ms=2000)

    assert bericht["eroeffnet"] >= 1
    assert "HYPE" in {t.symbol for t in eng.tm.closed}
    assert bericht["pnl_usd"] > 0
    # Risiko pro Trade eingehalten: der maximale Einzelverlust bleibt klein.
    verluste = [t.pnl_usd for t in eng.tm.closed if t.pnl_usd < 0]
    assert all(abs(v) < cfg.risk.equity_usd * 0.02 for v in verluste)


async def test_szenario_fallen_werden_erkannt(cfg, universe):
    """Spekulation, Wiederholung und unsichere Eilmeldung duerfen nicht handeln."""
    datei = ROOT / "examples" / "szenario_gemischt.jsonl"
    market = StaticMarket()
    eng = build(cfg, market, universe, seed=42)
    bericht = await run_scenario(eng, market, load_scenario(datei), tick_every_ms=2000)

    assert bericht["meldungen"] == 4
    assert bericht["ereignisse"] == 1, "nur das unterzeichnete Dekret ist handelbar"
    assert bericht["eroeffnet"] >= 1
    gehandelt = {t.symbol for t in eng.tm.closed}
    assert gehandelt, "das echte Ereignis muss gehandelt werden"


async def test_szenario_ist_reproduzierbar(cfg, universe):
    datei = ROOT / "examples" / "szenario_trump_hyperliquid.jsonl"
    recs = load_scenario(datei)
    ergebnisse = []
    for _ in range(2):
        m = StaticMarket()
        e = build(cfg, m, universe, seed=7)
        ergebnisse.append((await run_scenario(e, m, recs, tick_every_ms=2000))["pnl_usd"])
    assert ergebnisse[0] == ergebnisse[1]
