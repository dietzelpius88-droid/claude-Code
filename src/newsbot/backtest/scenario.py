"""Szenario-Replay: Nachrichten UND Kursverlauf deterministisch nachspielen.

Dateiformat (JSON Lines, nach `t_ms` sortiert):

    {"t_ms": 0,     "type": "price", "symbol": "HYPE", "mid": 42.0,
     "spread_bps": 6, "depth_usd": 800000, "atr_bps": 33}
    {"t_ms": 12000, "type": "news",  "source_id": "speech_potus", "tier": 1,
     "headline": "...", "body": "...", "expect": "long HYPE"}
    {"t_ms": 20000, "type": "price", "symbol": "HYPE", "mid": 44.1}

Der Runner setzt die virtuelle Uhr auf `t_ms`, aktualisiert Kurse, gibt
Nachrichten in die Engine und ruft nach jedem Schritt die Positionspflege auf.
Damit sind Zeit-Stops, Trailing und Teilverkaeufe exakt reproduzierbar.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .. import clock
from ..execution.paper import StaticMarket
from ..models import RawNews, SourceTier
from ..strategy.engine import NewsTradingEngine

log = logging.getLogger(__name__)


def load_scenario(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Szenario nicht gefunden: {p}")
    recs: list[dict[str, Any]] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p}:{i} ist kein gueltiges JSON: {exc}") from exc
    recs.sort(key=lambda r: r.get("t_ms", 0))
    return recs


async def run_scenario(engine: NewsTradingEngine, market: StaticMarket,
                       records: list[dict[str, Any]], *,
                       start_ms: int = 1_700_000_000_000,
                       tick_every_ms: int = 1_000,
                       tail_s: int = 3_600) -> dict[str, Any]:
    """Spielt ein Szenario ab und gibt den Ergebnisbericht zurueck."""
    clock.set_virtual(start_ms)
    try:
        t_prev = 0
        for rec in records:
            t = int(rec.get("t_ms", 0))
            # Zwischenschritte fuer die Positionspflege, damit Stops und
            # Zeit-Stops nicht ueber grosse Zeitspruenge hinweg verpasst werden.
            while t_prev + tick_every_ms < t:
                t_prev += tick_every_ms
                clock.set_virtual(start_ms + t_prev)
                _refresh(market)
                await engine.tick()
            t_prev = t
            clock.set_virtual(start_ms + t)

            kind = rec.get("type", "news")
            if kind == "price":
                market.set(rec["symbol"], float(rec["mid"]),
                           spread_bps=float(rec.get("spread_bps", 2.0)),
                           depth_usd=float(rec.get("depth_usd", 500_000)),
                           atr_bps=float(rec.get("atr_bps", 0.0)))
            elif kind == "news":
                news = RawNews(
                    source_id=rec.get("source_id", "replay"),
                    tier=SourceTier(int(rec.get("tier", 2))),
                    headline=rec.get("headline", ""), body=rec.get("body", ""),
                    url=rec.get("url", ""), author=rec.get("author", ""),
                    published_ms=clock.now_ms() - int(rec.get("lag_ms", 0)),
                    received_ms=clock.now_ms(),
                    meta={"expect": rec.get("expect")})
                log.info("[t=%6.1fs] NACHRICHT %s", t / 1000.0, news.headline[:90])
                await engine.on_news(news)
            await engine.tick()

        # Nachlauf, damit Zeit-Stops und Trailing zu Ende laufen.
        for _ in range(max(0, tail_s * 1000 // tick_every_ms)):
            t_prev += tick_every_ms
            clock.set_virtual(start_ms + t_prev)
            _refresh(market)
            await engine.tick()
            if not engine.pf.positions:
                break

        if engine.pf.positions:
            log.info("Nachlauf beendet - verbleibende Positionen werden glattgestellt")
            from ..execution.manager import ExitReason
            await engine.tm.flatten_all(ExitReason.TIME_STOP)

        return engine.report()
    finally:
        clock.reset()


def _refresh(market: StaticMarket) -> None:
    """Zeitstempel der Kurse mitfuehren, damit sie nicht als veraltet gelten."""
    for sym, q in list(market.prices.items()):
        market.set(sym, q.mid, spread_bps=q.spread_bps,
                   depth_usd=q.depth_usd, atr_bps=q.atr_bps)
