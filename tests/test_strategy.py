"""Asset-Auswahl: Erwartungswert, Kosten, Rangfolge, Gates."""
import pytest

from newsbot.models import Direction, EventClass, Quote
from newsbot.strategy.selector import AssetSelector
from tests.conftest import make_event


@pytest.fixture
def selector(universe, cfg):
    return AssetSelector(universe, cfg.signal, cfg.risk, cfg.execution)


def test_namentliche_nennung_gewinnt(selector):
    """Die Rede nennt Hyperliquid - HYPE muss vorn stehen."""
    ev = make_event(sentiment=0.8, ec=EventClass.CRYPTO_POLICY, targets=["HYPE"],
                    headline="I looked at Hyperliquid, it is impressive")
    cands = [c for c in selector.build_candidates(ev) if c.rejected is None]
    assert cands[0].asset.symbol == "HYPE"
    assert cands[0].mention >= 0.8


def test_gegenlaeufige_instrumente_drehen_die_richtung(selector):
    """Zoelle sind baerisch fuer Aktien und bullisch fuer Gold."""
    ev = make_event(sentiment=-0.9, ec=EventClass.TRADE_POLICY,
                    headline="Tariffs imposed on all imports")
    richtung = {c.asset.symbol: c.direction for c in selector.build_candidates(ev)}
    assert richtung["ES"] is Direction.SHORT
    assert richtung["NQ"] is Direction.SHORT
    assert richtung["GC"] is Direction.LONG      # negatives Beta


def test_hohes_beta_bewegt_sich_mehr(selector):
    ev = make_event(sentiment=0.8, ec=EventClass.CRYPTO_POLICY)
    m = {c.asset.symbol: c.expected_move_bps for c in selector.build_candidates(ev)}
    assert m["HYPE"] > m["SOL"] > m["BTC"]


def test_zu_kleine_kante_wird_abgelehnt(selector):
    ev = make_event(sentiment=0.05, confidence=0.4, ec=EventClass.CRYPTO_POLICY)
    assert all(c.rejected for c in selector.build_candidates(ev))


def test_breiter_spread_sperrt_das_instrument(selector, universe):
    ev = make_event(sentiment=0.8, ec=EventClass.CRYPTO_POLICY, targets=["HYPE"])
    px = 42.0
    schlecht = Quote("HYPE", px * 0.995, px * 1.005, depth_usd=900_000, atr_bps=45)
    treffer = [c for c in selector.build_candidates(ev, {"HYPE": schlecht})
               if c.asset.symbol == "HYPE"]
    assert treffer and treffer[0].rejected is not None
    assert "Spread" in treffer[0].rejected


def test_duennes_buch_sperrt_das_instrument(selector):
    ev = make_event(sentiment=0.8, ec=EventClass.CRYPTO_POLICY, targets=["HYPE"])
    duenn = Quote("HYPE", 41.99, 42.01, depth_usd=5_000, atr_bps=45)
    treffer = [c for c in selector.build_candidates(ev, {"HYPE": duenn})
               if c.asset.symbol == "HYPE"]
    assert treffer and "Buchtiefe" in (treffer[0].rejected or "")


def test_veralteter_kurs_sperrt_das_instrument(selector):
    from newsbot.models import now_ms
    ev = make_event(sentiment=0.8, ec=EventClass.CRYPTO_POLICY, targets=["HYPE"])
    alt = Quote("HYPE", 41.99, 42.01, ts_ms=now_ms() - 30_000,
                depth_usd=900_000, atr_bps=45)
    treffer = [c for c in selector.build_candidates(ev, {"HYPE": alt})
               if c.asset.symbol == "HYPE"]
    assert treffer and "veraltet" in (treffer[0].rejected or "")


def test_stop_nutzt_ereignisvolatilitaet_nicht_ruhe_atr(selector, universe):
    """Eine im Ruhezustand gemeldete Mini-ATR darf den Stop nicht verengen -
    sonst rutschen illiquide Werte faelschlich an die Spitze der Rangliste."""
    hype = universe.get("HYPE")
    ruhig = Quote("HYPE", 41.99, 42.01, depth_usd=900_000, atr_bps=3.0)
    stop = selector.estimate_stop_bps(hype, ruhig)
    assert stop > 50, f"Stop {stop:.1f} bps ist fuer 780 bps Tagesvolatilitaet zu eng"
    # Meldet die Quote eine hoehere ATR, gewinnt diese.
    heiss = Quote("HYPE", 41.99, 42.01, depth_usd=900_000, atr_bps=200.0)
    assert selector.estimate_stop_bps(hype, heiss) > stop


def test_ziele_skalieren_mit_der_chance(selector):
    ev = make_event(sentiment=0.85, confidence=0.85, specificity=0.6,
                    ec=EventClass.CRYPTO_POLICY, targets=["HYPE"],
                    headline="Hyperliquid praised")
    sigs = selector.select(ev)
    assert sigs
    s = sigs[0]
    # Bei einer 8R-Chance darf die Leiter nicht bei 2R enden.
    assert max(s.targets_bps) > 3 * s.stop_bps


def test_hoechstens_konfigurierte_anzahl_instrumente(selector, cfg):
    ev = make_event(sentiment=0.9, confidence=0.9, ec=EventClass.CRYPTO_POLICY)
    assert len(selector.select(ev)) <= cfg.signal.max_assets_per_event


def test_unbekannte_klasse_liefert_keine_kandidaten(selector):
    ev = make_event(sentiment=0.9, ec=EventClass.UNKNOWN)
    assert selector.select(ev) == []


def test_kosten_steigen_mit_der_ordergroesse(selector, universe):
    hype = universe.get("HYPE")
    klein = selector.estimate_cost_bps(hype, 5_000, None)
    gross = selector.estimate_cost_bps(hype, 5_000_000, None)
    assert gross > klein * 2
