"""Ausfuehrung: Fill-Modell, gestaffelter Einstieg, Stops, Ziele, Zeit-Stop."""
import pytest

from newsbot import clock
from newsbot.config import ExecutionConfig, RiskConfig
from newsbot.execution.manager import ExitReason, TradeManager
from newsbot.execution.paper import PaperFillModel
from newsbot.models import Direction, OrderIntent
from newsbot.risk.sizing import size_position
from tests.conftest import make_signal


@pytest.fixture
def tm(broker, market, portfolio, cfg):
    return TradeManager(broker, market, portfolio, cfg.risk, cfg.execution)


# ---------------------------------------------------------------- Fill-Modell
async def test_zu_enges_limit_fuellt_nicht(broker, market):
    q = market.quote("HYPE")
    i = OrderIntent("s", "HYPE", "hyperliquid", Direction.LONG, qty=100,
                    limit_px=q.mid * 1.00001, tif="IOC")
    assert await broker.submit(i) is None


async def test_ausreichender_puffer_fuellt(broker, market):
    q = market.quote("HYPE")
    i = OrderIntent("s", "HYPE", "hyperliquid", Direction.LONG, qty=100,
                    limit_px=q.mid * 1.005, tif="IOC")
    f = await broker.submit(i)
    assert f is not None and f.qty == 100 and f.px > q.mid


async def test_duennes_buch_fuehrt_zu_teilausfuehrung(broker, market):
    q = market.quote("HYPE")
    i = OrderIntent("s", "HYPE", "hyperliquid", Direction.LONG, qty=100_000,
                    limit_px=q.mid * 1.02, tif="IOC")
    f = await broker.submit(i)
    assert f is not None and f.qty < 100_000


async def test_slippage_steigt_mit_der_groesse(broker, market):
    q = market.quote("HYPE")
    klein = await broker.submit(OrderIntent("s", "HYPE", "hyperliquid",
                                            Direction.LONG, 10, q.mid * 1.05))
    gross = await broker.submit(OrderIntent("s", "HYPE", "hyperliquid",
                                            Direction.LONG, 5_000, q.mid * 1.05))
    assert gross.px > klein.px


def test_latenz_kostet_geld(market):
    """Ein langsamerer Bot zahlt bei Nachrichten messbar mehr."""
    q = market.quote("HYPE")
    schnell = PaperFillModel(latency_ms=50, seed=1, noise_bps=0.0)
    langsam = PaperFillModel(latency_ms=2000, seed=1, noise_bps=0.0)
    i = OrderIntent("s", "HYPE", "hyperliquid", Direction.LONG, 100, q.mid * 1.05)
    a = schnell.fill(i, q, adv_usd=3.5e8, daily_vol_bps=780, fee_bps=2.5)
    b = langsam.fill(i, q, adv_usd=3.5e8, daily_vol_bps=780, fee_bps=2.5)
    assert b.px > a.px


# ---------------------------------------------------------------- Einstieg
async def test_gestaffelter_einstieg(tm, universe, market, portfolio, cfg):
    sig = make_signal(universe, "HYPE", reference_px=42.0)
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    pos = await tm.open(sig, sz)
    assert pos is not None
    # Erste Scheibe ist der konfigurierte Anteil
    assert pos.qty == pytest.approx(sz.qty * cfg.execution.entry_slices[0][0], rel=0.02)
    assert len(tm.pending) == len(cfg.execution.entry_slices) - 1
    assert portfolio.positions["HYPE"] is pos


async def test_zu_spaeter_einstieg_wird_verworfen(tm, universe, market, cfg):
    """Ist der Preis schon ueber das Chase-Limit gelaufen, ist die Kante weg."""
    sig = make_signal(universe, "HYPE", reference_px=42.0, max_chase_bps=100.0)
    market.set("HYPE", 42.0 * 1.05, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    assert await tm.open(sig, sz) is None


async def test_nachkauf_mittelt_den_einstand(tm, universe, market, portfolio, cfg):
    clock.set_virtual(1_700_000_000_000)
    sig = make_signal(universe, "HYPE", reference_px=42.0)
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    pos = await tm.open(sig, sz)
    erst_menge, erst_px = pos.qty, pos.entry_px
    market.set("HYPE", 41.5, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    clock.advance(5_000)
    await tm._process_pending()
    assert pos.qty > erst_menge
    assert pos.entry_px < erst_px          # guenstiger nachgekauft


# ---------------------------------------------------------------- Ausstieg
async def test_stop_wird_ausgeloest(tm, universe, market, portfolio, cfg):
    sig = make_signal(universe, "HYPE", reference_px=42.0, stop_bps=100.0)
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    pos = await tm.open(sig, sz)
    market.set("HYPE", pos.stop_px * 0.995, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    closed = await tm.on_tick()
    assert closed and closed[0].reason is ExitReason.STOP
    assert "HYPE" not in portfolio.positions
    assert closed[0].pnl_usd < 0


async def test_ziele_verkaufen_anteilig(tm, universe, market, portfolio, cfg):
    sig = make_signal(universe, "HYPE", reference_px=42.0, stop_bps=100.0,
                      targets_bps=[300.0, 600.0])
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    pos = await tm.open(sig, sz)
    start = pos.qty
    market.set("HYPE", pos.entry_px * 1.035, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    closed = await tm.on_tick()
    assert closed and closed[0].reason is ExitReason.TARGET
    # 40 % der Ursprungsmenge (erste Stufe der Leiter)
    assert closed[0].qty == pytest.approx(start * 0.4, rel=0.05)
    assert portfolio.positions["HYPE"].qty == pytest.approx(start * 0.6, rel=0.05)


async def test_stop_wandert_auf_einstand(tm, universe, market, portfolio, cfg):
    sig = make_signal(universe, "HYPE", reference_px=42.0, stop_bps=100.0,
                      targets_bps=[900.0, 1800.0])
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    pos = await tm.open(sig, sz)
    alt = pos.stop_px
    market.set("HYPE", pos.entry_px * 1.02, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    await tm.on_tick()
    assert portfolio.positions["HYPE"].stop_px > alt
    assert portfolio.positions["HYPE"].stop_px >= pos.entry_px
    assert portfolio.positions["HYPE"].trailed


async def test_zeitstop_schliesst_die_position(tm, universe, market, portfolio, cfg):
    clock.set_virtual(1_700_000_000_000)
    sig = make_signal(universe, "HYPE", reference_px=42.0, time_stop_s=600)
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    await tm.open(sig, sz)
    clock.advance(601_000)
    market.set("HYPE", 42.05, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    closed = await tm.on_tick()
    assert closed and closed[0].reason is ExitReason.TIME_STOP
    assert "HYPE" not in portfolio.positions


async def test_glattstellen_raeumt_offene_scheiben(tm, universe, market, portfolio, cfg):
    sig = make_signal(universe, "HYPE", reference_px=42.0)
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    await tm.open(sig, sz)
    assert tm.pending
    t = await tm.flatten("HYPE", ExitReason.CONTRADICTION)
    assert t is not None and t.reason is ExitReason.CONTRADICTION
    assert tm.pending == []
    assert "HYPE" not in portfolio.positions


async def test_r_vielfaches_misst_gegen_den_urspruenglichen_stop(
        tm, universe, market, portfolio, cfg):
    """Ein nachgezogener Stop darf die Kennzahl nicht schoenrechnen."""
    sig = make_signal(universe, "HYPE", reference_px=42.0, stop_bps=100.0,
                      targets_bps=[300.0, 600.0])
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    pos = await tm.open(sig, sz)
    market.set("HYPE", pos.entry_px * 1.035, spread_bps=6.0, depth_usd=900_000, atr_bps=45)
    closed = await tm.on_tick()
    # 300 bps Ziel bei 100 bps Stop -> rund 3R, nicht mehr
    assert 2.0 < closed[0].r_multiple < 4.5


def test_slippage_budget_skaliert_mit_der_chance(tm, universe):
    klein = make_signal(universe, "ES", expected_move_bps=60.0)
    gross = make_signal(universe, "HYPE", expected_move_bps=900.0)
    assert tm.slippage_budget_bps(klein) < tm.slippage_budget_bps(gross)
    assert tm.slippage_budget_bps(klein) >= ExecutionConfig().min_slippage_bps
    assert tm.slippage_budget_bps(gross) <= ExecutionConfig().max_slippage_bps
