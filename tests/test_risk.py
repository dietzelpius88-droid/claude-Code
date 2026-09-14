"""Positionsgroesse, Portfoliolimits und Not-Aus."""
import pytest

from newsbot.config import GuardConfig, RiskConfig
from newsbot.models import Direction, EventClass, Position, Quote, now_ms
from newsbot.risk.guards import GuardState
from newsbot.risk.limits import Portfolio, RiskManager
from newsbot.risk.sizing import conviction_multiplier, size_position
from tests.conftest import make_event, make_signal


# ---------------------------------------------------------------- Sizing
def test_groesse_folgt_dem_risikobudget(universe, market):
    sig = make_signal(universe, "BTC", stop_bps=100.0)
    r = size_position(sig, market.quote("BTC"), RiskConfig(max_notional_per_trade_pct=1.0))
    assert r.ok
    # 0,5 % von 100k, skaliert mit der Conviction, bei 100 bps Stop
    erwartet = 100_000 * 0.005 * conviction_multiplier(sig.conviction)
    assert r.risk_usd == pytest.approx(erwartet, rel=0.02)


def test_conviction_skaliert_und_deckelt():
    assert conviction_multiplier(0.0) == 0.0
    assert conviction_multiplier(0.55) == pytest.approx(1.0)
    assert conviction_multiplier(5.0) == 2.0        # gedeckelt
    assert conviction_multiplier(0.01) == 0.35      # Untergrenze


def test_nominalwert_deckel_greift(universe, market):
    sig = make_signal(universe, "BTC", stop_bps=20.0)   # enger Stop -> grosse Menge
    r = size_position(sig, market.quote("BTC"), RiskConfig())
    assert r.binding == "notional"
    assert r.notional_usd <= 100_000 * 0.25 * 1.001


def test_buchtiefe_deckelt(universe):
    sig = make_signal(universe, "HYPE", stop_bps=100.0)
    q = Quote("HYPE", 41.99, 42.01, bid_size_usd=20_000, ask_size_usd=20_000,
              depth_usd=40_000, atr_bps=45)
    r = size_position(sig, q, RiskConfig())
    assert r.binding == "book"
    assert r.notional_usd <= 40_000 * 0.15 * 1.001


def test_tagesvolumen_deckelt(universe, market):
    """Bei duennen Instrumenten darf der Bot nicht selbst den Kurs machen.

    Alle anderen Deckel werden bewusst hochgesetzt, damit genau der
    Volumenanteil bindet."""
    sig = make_signal(universe, "HYPE", stop_bps=2.0)
    q = Quote("HYPE", 41.99, 42.01, depth_usd=500_000_000, atr_bps=45)
    r = size_position(sig, q, RiskConfig(equity_usd=2_000_000,
                                         max_notional_per_trade_pct=5.0,
                                         max_leverage=100.0))
    assert r.binding == "adv", r.caps
    assert r.notional_usd <= universe.get("HYPE").adv_usd * 0.005 * 1.001


def test_hebel_deckelt_bei_offenem_bestand(universe, market):
    sig = make_signal(universe, "BTC", stop_bps=50.0)
    r = size_position(sig, market.quote("BTC"), RiskConfig(max_notional_per_trade_pct=5.0),
                      gross_exposure_usd=299_000)
    assert r.binding == "leverage"
    assert r.notional_usd <= 1_000 * 1.001


def test_ungueltiger_kurs_wird_abgelehnt(universe):
    sig = make_signal(universe, "BTC")
    r = size_position(sig, Quote("BTC", 0.0, 0.0), RiskConfig())
    assert not r.ok


def test_zu_kleine_position_wird_abgelehnt(universe, market):
    sig = make_signal(universe, "BTC", stop_bps=100.0)
    r = size_position(sig, market.quote("BTC"), RiskConfig(equity_usd=5.0))
    assert not r.ok and "zu klein" in r.reason


def test_stop_liegt_auf_der_richtigen_seite(universe, market):
    lang = make_signal(universe, "BTC", direction=Direction.LONG, stop_bps=100.0)
    kurz = make_signal(universe, "BTC", direction=Direction.SHORT, stop_bps=100.0)
    q = market.quote("BTC")
    assert size_position(lang, q, RiskConfig()).stop_px < q.mid
    assert size_position(kurz, q, RiskConfig()).stop_px > q.mid


# ---------------------------------------------------------------- Limits
def _pos(symbol, cluster, side=Direction.LONG, notional=10_000.0):
    px = 100.0
    return Position(symbol=symbol, venue="paper", side=side, qty=notional / px,
                    entry_px=px, signal_id="s", event_class=EventClass.CRYPTO_POLICY,
                    cluster=cluster, stop_px=px * 0.99,
                    time_stop_ms=now_ms() + 600_000)


def test_tagesverlust_stoppt_den_handel(portfolio, cfg):
    rm = RiskManager(cfg.risk)
    portfolio.record_close(-2_100)
    assert not rm.check_halts(portfolio)
    assert portfolio.is_halted


def test_wochenverlust_stoppt_den_handel(cfg):
    pf = Portfolio(100_000)
    rm = RiskManager(cfg.risk)
    # unter der Tagesgrenze, aber ueber der Wochengrenze
    for _ in range(4):
        pf.record_close(-1_500)
    assert not rm.check_halts(pf)


def test_verluste_in_folge_stoppen(cfg):
    pf = Portfolio(100_000)
    rm = RiskManager(cfg.risk)
    for _ in range(3):
        pf.record_close(-100)
    assert not rm.check_halts(pf)
    assert "Verluste in Folge" in pf.halt_reason


def test_gewinn_setzt_verlustserie_zurueck(cfg):
    pf = Portfolio(100_000)
    pf.record_close(-100)
    pf.record_close(-100)
    pf.record_close(+50)
    assert pf.consecutive_losses == 0
    assert RiskManager(cfg.risk).check_halts(pf)


def test_maximale_positionszahl(universe, market, cfg, portfolio):
    rm = RiskManager(cfg.risk)
    for i, (sym, cl) in enumerate([("A", "c1"), ("B", "c2"), ("C", "c3")]):
        portfolio.positions[sym] = _pos(sym, cl)
    sig = make_signal(universe, "BTC")
    sz = size_position(sig, market.quote("BTC"), cfg.risk)
    d = rm.approve(sig, sz, portfolio)
    assert not d and "offene Positionen" in d.reason


def test_ein_trade_pro_korrelationsgruppe(universe, market, cfg, portfolio):
    rm = RiskManager(cfg.risk)
    portfolio.positions["SOL"] = _pos("SOL", "crypto_beta")
    sig = make_signal(universe, "HYPE")        # ebenfalls crypto_beta
    sz = size_position(sig, market.quote("HYPE"), cfg.risk)
    d = rm.approve(sig, sz, portfolio)
    assert not d and "Gruppe" in d.reason


def test_keine_doppelposition(universe, market, cfg, portfolio):
    rm = RiskManager(cfg.risk)
    portfolio.positions["BTC"] = _pos("BTC", "crypto_major")
    sig = make_signal(universe, "BTC")
    sz = size_position(sig, market.quote("BTC"), cfg.risk)
    assert not rm.approve(sig, sz, portfolio)


def test_keine_gegenposition_in_derselben_gruppe(universe, market, cfg, portfolio):
    rm = RiskManager(cfg.risk)
    portfolio.positions["BTC"] = _pos("BTC", "crypto_major", side=Direction.SHORT)
    sig = make_signal(universe, "ETH", direction=Direction.LONG)
    sz = size_position(sig, market.quote("BTC"), cfg.risk)
    d = rm.approve(sig, sz, portfolio)
    assert not d


def test_bruttoexposure_begrenzt(universe, market, cfg, portfolio):
    rm = RiskManager(RiskConfig(max_positions_per_cluster=9,
                                max_concurrent_positions=9,
                                max_gross_exposure_pct=0.05))
    portfolio.positions["X"] = _pos("X", "misc", notional=4_900)
    sig = make_signal(universe, "BTC")
    sz = size_position(sig, market.quote("BTC"), cfg.risk)
    d = rm.approve(sig, sz, portfolio)
    assert not d and "Bruttoexposure" in d.reason


def test_handelssperre_nach_uhrzeit(universe, market, cfg, portfolio):
    from datetime import datetime, timezone
    stunde = datetime.now(tz=timezone.utc).hour
    rm = RiskManager(cfg.risk, blackout_hours_utc=[stunde])
    sig = make_signal(universe, "BTC")
    sz = size_position(sig, market.quote("BTC"), cfg.risk)
    d = rm.approve(sig, sz, portfolio)
    assert not d and "Handelssperre" in d.reason


def test_gesunde_lage_wird_freigegeben(universe, market, cfg, portfolio):
    rm = RiskManager(cfg.risk)
    sig = make_signal(universe, "BTC")
    sz = size_position(sig, market.quote("BTC"), cfg.risk)
    d = rm.approve(sig, sz, portfolio)
    assert d, d.reason
    assert all(d.checks.values())


# ---------------------------------------------------------------- Wachen
def test_boerse_wird_nach_fehlern_gesperrt():
    g = GuardState(GuardConfig())
    for _ in range(3):
        g.note_order_error("hyperliquid")
    assert not g.venue_ok("hyperliquid")
    assert g.venue_ok("cme")


def test_erfolgreiche_order_setzt_fehlerzaehler_zurueck():
    g = GuardState(GuardConfig())
    g.note_order_error("x")
    g.note_order_ok("x")
    g.note_order_error("x")
    assert g.venue_ok("x")


def test_ausfuehrungsanomalie_sperrt_die_boerse():
    g = GuardState(GuardConfig())
    g.note_fill("hyperliquid", expected_px=100.0, actual_px=102.0)   # 200 bps
    assert not g.venue_ok("hyperliquid")


def test_spread_explosion_blockiert_kurse():
    g = GuardState(GuardConfig())
    q = Quote("HYPE", 41.0, 43.0)        # ~476 bps Spread
    ok, why = g.quote_ok(q, typical_spread_bps=6.0)
    assert not ok and "Spread" in why


def test_veralteter_kurs_blockiert():
    g = GuardState(GuardConfig())
    q = Quote("HYPE", 41.99, 42.01, ts_ms=now_ms() - 20_000)
    ok, why = g.quote_ok(q, 6.0)
    assert not ok and "alt" in why


def test_stiller_feed_wird_gemeldet():
    g = GuardState(GuardConfig(feed_stale_s=5))
    g.note_news("fed_press")
    assert g.stale_feeds() == []
    g.last_news_ms["fed_press"] = now_ms() - 10_000
    assert "fed_press" in g.stale_feeds()
