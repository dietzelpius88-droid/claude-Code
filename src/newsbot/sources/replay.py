"""Replay aufgezeichneter Meldungen - fuer Backtests und Regressionstests.

Dateiformat: JSON Lines, ein Objekt je Zeile
    {"t_ms": 0, "source_id": "...", "tier": 1, "headline": "...", "body": "...",
     "prices": {"HYPE": {"path_bps": [0, 120, ...], "step_s": 10}}}

`t_ms` ist der Versatz zum Szenariobeginn. `prices` ist optional und beschreibt
den Kursverlauf nach dem Ereignis - damit laesst sich eine komplette Reaktion
nachspielen.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..models import RawNews, SourceTier, now_ms
from .base import NewsSource

log = logging.getLogger(__name__)


class ReplaySource(NewsSource):
    def __init__(self, path: str | Path, *, speed: float = 0.0,
                 source_id: str = "replay") -> None:
        """speed=0 spielt so schnell wie moeglich ab, 1.0 in Echtzeit."""
        super().__init__(source_id, SourceTier.WIRE)
        self.path = Path(path)
        self.speed = speed
        self.records: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Replay-Datei nicht gefunden: {self.path}")
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.records.append(json.loads(line))
        self.records.sort(key=lambda r: r.get("t_ms", 0))
        log.info("Replay geladen: %d Meldungen aus %s", len(self.records), self.path)

    async def stream(self) -> AsyncIterator[RawNews]:
        base = now_ms()
        prev = 0
        for rec in self.records:
            t = int(rec.get("t_ms", 0))
            if self.speed > 0:
                await asyncio.sleep(max(0, t - prev) / 1000.0 / self.speed)
            prev = t
            tier = SourceTier(int(rec.get("tier", 2)))
            news = RawNews(
                source_id=rec.get("source_id", self.source_id), tier=tier,
                headline=rec.get("headline", ""), body=rec.get("body", ""),
                url=rec.get("url", ""), author=rec.get("author", ""),
                published_ms=base + t, received_ms=base + t + int(rec.get("lag_ms", 0)),
                meta={"replay": True, "prices": rec.get("prices", {}),
                      "t_ms": t, "expect": rec.get("expect")})
            self.stats.emitted += 1
            yield news
