"""Ereignisstudie und Kalibrierungswerkzeuge."""
import math

import pytest

from newsbot.backtest.event_study import (BetaEstimate, EventObservation,
                                          estimate_betas, estimate_half_life,
                                          geometric_mean_growth, summarize)


def _obs(n=20, beta=2.0, vol=800.0, rauschen=0.0, ec="crypto_policy"):
    """Erzeugt Beobachtungen mit bekanntem Beta, damit die Schaetzung pruefbar ist."""
    import random
    rng = random.Random(3)
    out = []
    for i in range(n):
        s = rng.choice([-1.0, -0.6, 0.6, 1.0])
        wahr = s * 0.35 * beta * vol
        out.append(EventObservation(
            event_class=ec, sentiment=s, ts_ms=i * 1000,
            returns_bps={"HYPE": wahr + rng.gauss(0, rauschen)}))
    return out


def test_beta_wird_wiedergefunden():
    schaetzung = estimate_betas(_obs(beta=2.4), {"HYPE": 800.0},
                                {"crypto_policy": 0.35})
    assert len(schaetzung) == 1
    assert schaetzung[0].beta == pytest.approx(2.4, rel=0.01)
    assert schaetzung[0].hit_rate == 1.0
    assert schaetzung[0].reliable


def test_rauschen_senkt_bestimmtheitsmass():
    sauber = estimate_betas(_obs(rauschen=0), {"HYPE": 800.0}, {"crypto_policy": 0.35})
    verrauscht = estimate_betas(_obs(rauschen=900), {"HYPE": 800.0},
                                {"crypto_policy": 0.35})
    assert verrauscht[0].r_squared < sauber[0].r_squared


def test_zu_wenige_beobachtungen_sind_nicht_nutzbar():
    e = estimate_betas(_obs(n=4), {"HYPE": 800.0}, {"crypto_policy": 0.35})
    assert not e[0].reliable


def test_schlechte_trefferquote_ist_nicht_nutzbar():
    e = BetaEstimate("X", "crypto_policy", 1.0, 40, 0.48, 100.0, 0.1)
    assert not e.reliable


def test_ohne_daten_kein_ergebnis():
    assert estimate_betas([], {}, {}) == []
    assert estimate_betas(_obs(), {}, {"crypto_policy": 0.35}) == []


def test_halbwertszeit_wird_geschaetzt():
    """Impuls faellt exponentiell - die Schaetzung muss die Haelfte finden."""
    obs = []
    for i in range(12):
        pfad = [(t * 1000, 100.0 * math.exp(-t / 600.0)) for t in range(0, 1800, 60)]
        obs.append(EventObservation("crypto_policy", 1.0, i, {}, {"HYPE": pfad}))
    hl = estimate_half_life(obs, "HYPE", "crypto_policy")
    assert hl is not None
    # exponentieller Zerfall mit tau=600 -> Halbwertszeit ~416 s
    assert 250 < hl < 650, hl


def test_halbwertszeit_ohne_pfade():
    assert estimate_half_life([], "HYPE", "crypto_policy") is None


def test_uebersicht_ist_lesbar():
    t = summarize(estimate_betas(_obs(), {"HYPE": 800.0}, {"crypto_policy": 0.35}))
    assert "BETA" in t and "HYPE" in t


# ------------------------------------------------------------- Einsatzgroesse
def test_zu_hoher_einsatz_frisst_den_vorteil():
    """Der Kern der Begruendung fuer 0,5 % Risiko pro Trade.

    Eine realistische News-Serie hat viele kleine Verluste und wenige grosse
    Gewinner. Der Erwartungswert ist positiv - trotzdem wird das GEOMETRISCHE
    Wachstum ab einem gewissen Einsatz negativ, weil Verluste multiplikativ
    wirken. Es gibt ein Optimum, und darueber hinaus zerstoert mehr Einsatz
    das Ergebnis."""
    r = [-1.0] * 6 + [3.0, 3.0, 2.0]      # Erwartungswert +2R auf 9 Trades
    assert sum(r) > 0

    vorsichtig = geometric_mean_growth(r, 0.005)
    mittel = geometric_mean_growth(r, 0.10)
    masslos = geometric_mean_growth(r, 0.50)

    assert vorsichtig > 0
    assert mittel > vorsichtig, "etwas mehr Einsatz waechst schneller"
    assert masslos < 0, "zu viel Einsatz vernichtet Kapital trotz positiver Kante"


def test_totalverlust_wird_erkannt():
    assert geometric_mean_growth([-1.0], 1.0) == float("-inf")


def test_leere_serie():
    assert geometric_mean_growth([], 0.01) == 0.0
