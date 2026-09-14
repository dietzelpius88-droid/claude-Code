"""WebSocket-Quelle fuer kommerzielle Niedriglatenz-Feeds.

Das ist der Kanal, auf dem professionelle News-Trader arbeiten: strukturierte
Meldungen mit Millisekunden-Zeitstempel, oft schon maschinenlesbar
vorkategorisiert. Anbieter u.a. Dow Jones Newswires, Bloomberg B-PIPE,
Reuters/LSEG, Benzinga Pro, Cision/PRNewswire.

Die Abbildung auf `RawNews` uebernimmt eine anbieterspezifische `parse`-Funktion,
damit der restliche Bot anbieterunabhaengig bleibt.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..models import RawNews, SourceTier
from .base import NewsSource

log = logging.getLogger(__name__)


class WebSocketSource(NewsSource):
    def __init__(self, source_id: str, url: str, tier: SourceTier,
                 parse: Callable[[dict[str, Any]], dict[str, Any] | None],
                 *, subscribe_msg: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None,
                 ping_interval: float = 20.0) -> None:
        super().__init__(source_id, tier)
        self.url = url
        self.parse = parse
        self.subscribe_msg = subscribe_msg
        self.headers = headers or {}
        self.ping_interval = ping_interval

    async def stream(self) -> AsyncIterator[RawNews]:
        try:
            import websockets
        except ImportError:
            log.error("%s: websockets fehlt - Quelle inaktiv "
                      "(pip install '.[live]')", self.source_id)
            return

        attempt = 0
        while True:
            try:
                async with websockets.connect(
                        self.url, additional_headers=self.headers,
                        ping_interval=self.ping_interval) as ws:
                    if self.subscribe_msg:
                        await ws.send(json.dumps(self.subscribe_msg))
                    attempt = 0
                    log.info("%s: verbunden", self.source_id)
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        item = self.parse(payload)
                        if not item:
                            continue
                        news = self.emit(
                            item.get("headline", ""),
                            body=item.get("body", ""),
                            url=item.get("url", ""),
                            author=item.get("author", ""),
                            published_ms=item.get("published_ms"),
                            key=item.get("key"),
                            **item.get("meta", {}))
                        if news is not None:
                            yield news
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.errors += 1
                log.warning("%s: Verbindung verloren (%s)", self.source_id, exc)
                attempt += 1
                await self._backoff(attempt)
