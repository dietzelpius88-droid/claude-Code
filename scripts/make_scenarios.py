#!/usr/bin/env python3
"""Erzeugt die Beispielszenarien in examples/.

Die Kursverlaeufe sind nachgebildet, nicht historisch exakt - sie bilden die
typische Form eines News-Impulses ab: steiler Erstschub, Ruecksetzer,
Fortsetzung, Abflachen.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "examples"
OUT.mkdir(exist_ok=True)


def impulse_path(start: float, total_move_pct: float, duration_s: int,
                 *, step_s: int = 20, pullback: float = 0.30,
                 noise_bps: float = 12.0, decay: float = 0.0,
                 seed: int = 1) -> list[tuple[int, float]]:
    """Typische Impulsform: schneller Anstieg, Ruecksetzer, Fortsetzung, Fade."""
    rng = random.Random(seed)
    pts: list[tuple[int, float]] = []
    peak_t = duration_s * 0.55
    for t in range(0, duration_s + step_s, step_s):
        if t <= peak_t:
            # saettigende Bewegung zum Hoch
            prog = 1.0 - math.exp(-3.2 * t / max(1.0, peak_t))
            # Ruecksetzer im ersten Drittel
            dip = 0.0
            if 0.18 * peak_t < t < 0.42 * peak_t:
                phase = (t - 0.18 * peak_t) / (0.24 * peak_t)
                dip = -pullback * math.sin(math.pi * phase)
            frac = prog + dip
        else:
            over = (t - peak_t) / max(1.0, duration_s - peak_t)
            frac = 1.0 - decay * over
        px = start * (1.0 + total_move_pct / 100.0 * frac)
        px *= 1.0 + rng.gauss(0.0, noise_bps) / 10_000.0
        pts.append((int(t * 1000), round(px, 6)))
    return pts


def price_rec(t_ms, symbol, mid, spread_bps, depth_usd, atr_bps):
    return {"t_ms": t_ms, "type": "price", "symbol": symbol, "mid": mid,
            "spread_bps": spread_bps, "depth_usd": depth_usd, "atr_bps": atr_bps}


def write(path: Path, recs: list[dict], header: list[str]) -> None:
    recs.sort(key=lambda r: (r["t_ms"], 0 if r["type"] == "price" else 1))
    with path.open("w", encoding="utf-8") as fh:
        for line in header:
            fh.write(f"// {line}\n")
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{path.name}: {len(recs)} Datensaetze")


# ==========================================================================
# Szenario 1: Rede mit namentlicher Erwaehnung von Hyperliquid
# ==========================================================================
def scenario_hyperliquid() -> None:
    recs: list[dict] = []
    # Ausgangskurse
    for sym, px, spr, depth, atr in [
            ("HYPE", 42.00, 6.0, 900_000, 20.0),
            ("BTC", 95_000.0, 1.0, 12_000_000, 7.0),
            ("ETH", 3_200.0, 1.2, 6_000_000, 9.0),
            ("SOL", 180.0, 2.0, 3_000_000, 13.0),
            ("XRP", 2.40, 2.5, 1_500_000, 14.0),
            ("DOGE", 0.34, 3.0, 1_200_000, 17.0)]:
        recs.append(price_rec(0, sym, px, spr, depth, atr))

    # Ruhige Vorlaufphase
    for t in range(20, 60, 20):
        recs.append(price_rec(t * 1000, "HYPE", 42.0 * (1 + random.Random(t).gauss(0, 6) / 10_000), 6.0, 900_000, 20.0))

    T = 60_000     # Redebeginn der relevanten Passage
    recs.append({"t_ms": T, "type": "news", "source_id": "speech_potus", "tier": 1,
                 "author": "POTUS", "lag_ms": 400,
                 "headline": "We are going to make America the crypto capital of the world, that I can tell you.",
                 "body": "Rede auf der Digital Asset Summit, Live-Transkript.",
                 "expect": "schwach bullisch Krypto - qualitativ, ohne Zahlen"})

    recs.append({"t_ms": T + 9_000, "type": "news", "source_id": "speech_potus", "tier": 1,
                 "author": "POTUS", "lag_ms": 400,
                 "headline": "And I looked at Hyperliquid, this platform, it is very impressive, tremendous technology, we support it.",
                 "body": "We are going to make America the crypto capital of the world, that I can tell you.",
                 "expect": "long HYPE - direkte namentliche Nennung eines duennen Assets"})

    # Kursreaktion: HYPE +15 % in 28 Minuten, Rest deutlich weniger
    paths = {
        "HYPE": (42.00, 15.2, 1700, 6.0, 900_000, 45.0, 0.22, 11),
        "BTC":  (95_000.0, 1.4, 1700, 1.0, 12_000_000, 14.0, 0.15, 12),
        "ETH":  (3_200.0, 1.9, 1700, 1.2, 6_000_000, 18.0, 0.15, 13),
        "SOL":  (180.0, 3.1, 1700, 2.0, 3_000_000, 26.0, 0.18, 14),
        "XRP":  (2.40, 2.6, 1700, 2.5, 1_500_000, 28.0, 0.18, 15),
        "DOGE": (0.34, 2.9, 1700, 3.0, 1_200_000, 34.0, 0.20, 16),
    }
    for sym, (px0, move, dur, spr, depth, atr, decay, seed) in paths.items():
        # Spread weitet sich im ersten Moment stark
        for dt, px in impulse_path(px0, move, dur, step_s=20, decay=decay, seed=seed):
            s = spr * (3.5 if dt < 20_000 else 1.6 if dt < 90_000 else 1.0)
            d = depth * (0.35 if dt < 20_000 else 0.7 if dt < 90_000 else 1.0)
            recs.append(price_rec(T + 9_000 + dt, sym, px, round(s, 2), int(d), atr))

    write(OUT / "szenario_trump_hyperliquid.jsonl", recs, [
        "Szenario: Rede mit namentlicher Erwaehnung von Hyperliquid.",
        "Erwartetes Verhalten: kein Trade auf den vagen ersten Satz, Einstieg",
        "long HYPE nach der direkten Nennung, Teilverkaeufe auf dem Weg nach oben.",
        "Kursverlauf nachgebildet (+15,2 % in ca. 28 Minuten, mit Ruecksetzer).",
    ])


# ==========================================================================
# Szenario 2: gemischter Nachrichtenstrom mit Fallen
# ==========================================================================
def scenario_mixed() -> None:
    recs: list[dict] = []
    base = {"ES": (5_800.0, 0.5, 40_000_000, 4.0), "NQ": (20_500.0, 0.6, 30_000_000, 5.5),
            "BTC": (95_000.0, 1.0, 12_000_000, 7.0), "GC": (2_650.0, 1.0, 20_000_000, 4.0),
            "ZN": (110.5, 0.4, 25_000_000, 1.5), "HG": (4.25, 2.0, 5_000_000, 6.0),
            "HYPE": (42.0, 6.0, 900_000, 20.0)}
    for sym, (px, spr, depth, atr) in base.items():
        recs.append(price_rec(0, sym, px, spr, depth, atr))

    def hold(sym, t0, t1, px, spr, depth, atr, step=30_000):
        for t in range(t0, t1, step):
            recs.append(price_rec(t, sym, px, spr, depth, atr))

    # --- Falle 1: reine Spekulation, gehedged --------------------------
    recs.append({"t_ms": 30_000, "type": "news", "source_id": "reuters", "tier": 2,
                 "headline": "Trump says he is considering new tariffs on European cars, sources say",
                 "expect": "KEIN Trade - Erwaegung ohne Beschluss"})

    # --- Falle 2: alte Nachricht neu aufgewaermt ------------------------
    recs.append({"t_ms": 90_000, "type": "news", "source_id": "bloomberg", "tier": 2,
                 "headline": "Last week Powell reiterated that inflation remains too high",
                 "expect": "KEIN Trade - Wiederholung, laengst eingepreist"})

    # --- Falle 3: unbestaetigte Eilmeldung von unsicherer Quelle --------
    recs.append({"t_ms": 150_000, "type": "news", "source_id": "social:anon_breaking", "tier": 5,
                 "headline": "BREAKING: Fed announces emergency rate cut of 100 basis points",
                 "expect": "KEIN Trade - Stufe 5 braucht Zweitbestaetigung"})

    # --- echtes Ereignis: unterzeichnetes Zolldekret --------------------
    T = 300_000
    recs.append({"t_ms": T, "type": "news", "source_id": "federal_register", "tier": 1,
                 "headline": "Presidential Proclamation: President signs executive order imposing 25% tariffs on all imports from China, effective immediately",
                 "body": "Section 232 proclamation, signed today.",
                 "expect": "short ES/NQ bzw. long GC - konkret, unterzeichnet, sofort wirksam"})

    for sym, (px0, move, spr, depth, atr, seed) in {
            "ES": (5_800.0, -1.6, 0.5, 40_000_000, 9.0, 21),
            "NQ": (20_500.0, -2.2, 0.6, 30_000_000, 12.0, 22),
            "HG": (4.25, -2.6, 2.0, 5_000_000, 14.0, 23),
            "GC": (2_650.0, 1.1, 1.0, 20_000_000, 8.0, 24),
            "ZN": (110.5, 0.25, 0.4, 25_000_000, 3.0, 25),
            "BTC": (95_000.0, -1.3, 1.0, 12_000_000, 14.0, 26)}.items():
        for dt, px in impulse_path(px0, move, 1500, step_s=20, decay=0.25, seed=seed):
            s = spr * (3.0 if dt < 15_000 else 1.4 if dt < 60_000 else 1.0)
            recs.append(price_rec(T + dt, sym, px, round(s, 2), depth, atr))

    hold("HYPE", 0, T + 1500_000, 42.0, 6.0, 900_000, 20.0, step=60_000)

    write(OUT / "szenario_gemischt.jsonl", recs, [
        "Gemischter Strom mit drei Fallen und einem echten Ereignis.",
        "Erwartet: nur das unterzeichnete Zolldekret wird gehandelt.",
    ])


if __name__ == "__main__":
    scenario_hyperliquid()
    scenario_mixed()
