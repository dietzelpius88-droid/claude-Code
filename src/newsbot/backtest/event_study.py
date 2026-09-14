"""Ereignisstudie: Betas und Impact-Skalen aus historischen Daten schaetzen.

Die Betas in `strategy/universe.py` sind Startwerte. Sie MUESSEN gegen echte
Daten nachkalibriert werden, sonst handelt der Bot eine Wunschvorstellung.

Verfahren
---------
Fuer jedes historische Ereignis (Klasse, Sentiment, Zeitpunkt) wird die
realisierte Bewegung jedes Instruments ueber ein Zeitfenster gemessen:

    ret_bps = (px[t0 + horizont] / px[t0] - 1) * 10000

Dann wird je (Instrument, Klasse) eine Regression durch den Ursprung gerechnet:

    ret_bps / tagesvol_bps  =  beta * sentiment * impact

`impact` wird je Klasse aus dem Leitinstrument bestimmt, `beta` daraus
abgeleitet. Zusaetzlich werden Trefferquote (richtiges Vorzeichen) und die
geschaetzte Halbwertszeit ausgegeben - Letztere setzt den Zeit-Stop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class EventObservation:
    """Ein historisches Ereignis mit den gemessenen Reaktionen."""

    event_class: str
    sentiment: float                    # -1..+1, wie es der Bot bewertet haette
    ts_ms: int
    # Symbol -> Rendite in bps ueber das Messfenster (vorzeichenbehaftet).
    returns_bps: dict[str, float] = field(default_factory=dict)
    # Symbol -> Rendite je Zwischenzeitpunkt, fuer die Halbwertszeit.
    path_bps: dict[str, list[tuple[int, float]]] = field(default_factory=dict)


@dataclass(slots=True)
class BetaEstimate:
    symbol: str
    event_class: str
    beta: float
    n: int
    hit_rate: float
    mean_abs_move_bps: float
    r_squared: float

    @property
    def reliable(self) -> bool:
        """Faustregel: unter 8 Beobachtungen oder ohne Trefferquote ueber 55 %
        ist die Schaetzung nicht handelbar."""
        return self.n >= 8 and self.hit_rate >= 0.55


def estimate_betas(observations: list[EventObservation],
                   daily_vol_bps: dict[str, float],
                   impact_scale: dict[str, float]) -> list[BetaEstimate]:
    """Regression durch den Ursprung je (Symbol, Ereignisklasse)."""
    buckets: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for obs in observations:
        if obs.sentiment == 0.0:
            continue
        scale = impact_scale.get(obs.event_class, 0.3)
        x = obs.sentiment * scale
        for sym, ret in obs.returns_bps.items():
            vol = daily_vol_bps.get(sym, 0.0)
            if vol <= 0:
                continue
            buckets.setdefault((sym, obs.event_class), []).append((x, ret / vol))

    out: list[BetaEstimate] = []
    for (sym, ec), pairs in buckets.items():
        sxy = sum(x * y for x, y in pairs)
        sxx = sum(x * x for x, y in pairs)
        if sxx <= 0:
            continue
        beta = sxy / sxx
        hits = sum(1 for x, y in pairs if x * y > 0)
        ss_res = sum((y - beta * x) ** 2 for x, y in pairs)
        mean_y = sum(y for _, y in pairs) / len(pairs)
        ss_tot = sum((y - mean_y) ** 2 for _, y in pairs)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        out.append(BetaEstimate(
            symbol=sym, event_class=ec, beta=round(beta, 4), n=len(pairs),
            hit_rate=round(hits / len(pairs), 3),
            mean_abs_move_bps=round(
                sum(abs(y) for _, y in pairs) / len(pairs)
                * daily_vol_bps.get(sym, 0.0), 1),
            r_squared=round(r2, 3)))
    out.sort(key=lambda b: (b.event_class, -abs(b.beta)))
    return out


def estimate_half_life(observations: list[EventObservation], symbol: str,
                       event_class: str) -> int | None:
    """Schaetzt, nach wie vielen Sekunden die Haelfte des Impulses verfallen ist.

    Gemessen am Median des Verhaeltnisses `ret(t) / ret(peak)`. Das Ergebnis
    setzt den Zeit-Stop: laeuft der Impuls typischerweise 20 Minuten, ist es
    unsinnig, zwei Stunden zu halten.
    """
    ratios: list[tuple[int, float]] = []
    for obs in observations:
        if obs.event_class != event_class:
            continue
        path = obs.path_bps.get(symbol)
        if not path:
            continue
        peak_t, peak_v = max(path, key=lambda tv: abs(tv[1]))
        if abs(peak_v) < 1e-9:
            continue
        for t, v in path:
            if t <= peak_t:
                continue
            ratios.append((t - peak_t, v / peak_v))

    if not ratios:
        return None
    ratios.sort()
    # Erster Zeitpunkt, an dem der gleitende Median unter 0.5 faellt.
    window: list[float] = []
    for dt, r in ratios:
        window.append(r)
        if len(window) < 5:
            continue
        med = sorted(window[-15:])[len(window[-15:]) // 2]
        if med < 0.5:
            return int(dt / 1000)
    return None


def summarize(estimates: list[BetaEstimate]) -> str:
    rows = [f"{'KLASSE':18s} {'SYM':6s} {'BETA':>7s} {'N':>4s} {'TREFFER':>8s} "
            f"{'|MOVE|bps':>10s} {'R2':>6s}  BEWERTUNG", "-" * 78]
    for e in estimates:
        flag = "nutzbar" if e.reliable else "zu duenn"
        rows.append(f"{e.event_class:18s} {e.symbol:6s} {e.beta:+7.3f} {e.n:4d} "
                    f"{e.hit_rate:8.1%} {e.mean_abs_move_bps:10.1f} {e.r_squared:6.2f}  {flag}")
    return "\n".join(rows)


def geometric_mean_growth(r_multiples: list[float], risk_fraction: float) -> float:
    """Geometrisches Wachstum pro Trade bei gegebenem Risikoanteil.

    Werkzeug fuer die Risikowahl: Ist das Ergebnis negativ, ist der Einsatz zu
    hoch - selbst bei positivem Erwartungswert. Deshalb liegt `risk_per_trade`
    im Bot deutlich unter dem, was der Kelly-Anteil nahelegen wuerde.
    """
    if not r_multiples:
        return 0.0
    total = 0.0
    for r in r_multiples:
        factor = 1.0 + risk_fraction * r
        if factor <= 0:
            return float("-inf")       # Totalverlust
        total += math.log(factor)
    return math.exp(total / len(r_multiples)) - 1.0
