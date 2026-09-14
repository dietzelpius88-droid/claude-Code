import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from newsbot import clock
from newsbot.config import Config
from newsbot.execution.paper import PaperBroker, PaperFillModel, StaticMarket
from newsbot.models import (AnalyzedEvent, EventClass, RawNews, SourceTier,
                            TradeSignal)
from newsbot.risk.limits import Portfolio
from newsbot.strategy.universe import Universe


@pytest.fixture(autouse=True)
def _real_clock():
    """Nach jedem Test zurueck auf die Systemuhr."""
    yield
    clock.reset()


@pytest.fixture
def universe() -> Universe:
    return Universe.load()


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def market() -> StaticMarket:
    m = StaticMarket()
    m.set("HYPE", 42.0, spread_bps=6.0, depth_usd=900_000, atr_bps=45.0)
    m.set("BTC", 95_000.0, spread_bps=1.0, depth_usd=12_000_000, atr_bps=14.0)
    m.set("ES", 5_800.0, spread_bps=0.5, depth_usd=40_000_000, atr_bps=8.0)
    m.set("HG", 4.25, spread_bps=2.0, depth_usd=5_000_000, atr_bps=12.0)
    return m


@pytest.fixture
def broker(market, universe) -> PaperBroker:
    return PaperBroker(market, universe, PaperFillModel(latency_ms=250, seed=11,
                                                        noise_bps=0.0))


@pytest.fixture
def portfolio(cfg) -> Portfolio:
    return Portfolio(cfg.risk.equity_usd)


def make_event(sentiment=0.8, confidence=0.8, specificity=0.6, novelty=1.0,
               ec=EventClass.CRYPTO_POLICY, tier=SourceTier.PRIMARY,
               headline="Testmeldung", half_life_s=1200, targets=None) -> AnalyzedEvent:
    news = RawNews(source_id="test", tier=tier, headline=headline)
    return AnalyzedEvent(news=news, event_class=ec, sentiment=sentiment,
                         confidence=confidence, specificity=specificity,
                         novelty=novelty, half_life_s=half_life_s,
                         target_hints=targets or [])


def make_signal(universe, symbol="HYPE", direction=None, **kw) -> TradeSignal:
    from newsbot.models import Direction
    ev = kw.pop("event", None) or make_event()
    asset = universe.get(symbol)
    return TradeSignal(
        event=ev, asset=asset,
        direction=direction or Direction.LONG,
        expected_move_bps=kw.pop("expected_move_bps", 800.0),
        expected_cost_bps=kw.pop("expected_cost_bps", 25.0),
        conviction=kw.pop("conviction", ev.conviction),
        stop_bps=kw.pop("stop_bps", 100.0),
        targets_bps=kw.pop("targets_bps", [280.0, 560.0]),
        time_stop_s=kw.pop("time_stop_s", 1200),
        max_chase_bps=kw.pop("max_chase_bps", 320.0),
        reference_px=kw.pop("reference_px", 0.0),
        **kw)
