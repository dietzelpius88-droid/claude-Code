"""RSS-/Atom-Quelle mit bedingtem GET.

Latenz-Realitaet
----------------
RSS ist KEIN Niedriglatenz-Kanal. Zwischen dem Ereignis und dem Erscheinen im
Feed liegen je nach Anbieter 2-60 Sekunden, bei Behoerden auch mehrere Minuten.
Fuer Zinsentscheide und Zolldekrete, die der Markt in Millisekunden handelt,
ist RSS zu langsam - dafuer braucht es einen kommerziellen Wire-Feed oder das
direkte Abgreifen der Behoerdenseite (siehe `poller.py`).

RSS ist trotzdem unverzichtbar: als breite Abdeckung, als Zweitbestaetigung
fuer schnelle, aber unsichere Quellen, und fuer Ereignisse mit langer
Halbwertszeit.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from collections.abc import AsyncIterator

from ..models import RawNews, SourceTier
from .base import NewsSource

log = logging.getLogger(__name__)


class RSSSource(NewsSource):
    def __init__(self, source_id: str, url: str, tier: SourceTier,
                 poll_s: float = 5.0, *, timeout_s: float = 8.0,
                 include_body: bool = True) -> None:
        super().__init__(source_id, tier)
        self.url = url
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.include_body = include_body
        self._etag: str | None = None
        self._modified: str | None = None
        self._first_pass = True

    # ------------------------------------------------------------------
    async def stream(self) -> AsyncIterator[RawNews]:
        try:
            import aiohttp
            import feedparser
        except ImportError:
            log.error("%s: aiohttp/feedparser fehlen - Quelle inaktiv "
                      "(pip install '.[live]')", self.source_id)
            return

        attempt = 0
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        headers_base = {"User-Agent": "newsbot/0.1 (+research)"}

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    headers = dict(headers_base)
                    # Bedingtes GET spart Bandbreite und erlaubt kuerzere Intervalle.
                    if self._etag:
                        headers["If-None-Match"] = self._etag
                    if self._modified:
                        headers["If-Modified-Since"] = self._modified

                    async with session.get(self.url, headers=headers) as resp:
                        if resp.status == 304:
                            await asyncio.sleep(self.poll_s)
                            continue
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        self._etag = resp.headers.get("ETag") or self._etag
                        self._modified = resp.headers.get("Last-Modified") or self._modified
                        raw = await resp.read()

                    attempt = 0
                    feed = feedparser.parse(raw)
                    # Aeltester zuerst, damit die Reihenfolge stimmt.
                    for entry in reversed(feed.entries or []):
                        news = self._to_news(entry)
                        if news is None:
                            continue
                        if self._first_pass:
                            # Beim Start nur den Bestand merken, nicht handeln.
                            continue
                        yield news
                    self._first_pass = False

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.stats.errors += 1
                    log.warning("%s: %s", self.source_id, exc)
                    attempt += 1
                    await self._backoff(attempt)
                    continue

                await asyncio.sleep(self.poll_s)

    # ------------------------------------------------------------------
    def _to_news(self, entry) -> RawNews | None:
        title = getattr(entry, "title", "") or ""
        if not title:
            return None
        link = getattr(entry, "link", "") or ""
        key = getattr(entry, "id", None) or link or title

        published_ms = None
        for attr in ("published_parsed", "updated_parsed"):
            tm = getattr(entry, attr, None)
            if tm:
                published_ms = int(calendar.timegm(tm) * 1000)
                break

        body = ""
        if self.include_body:
            body = (getattr(entry, "summary", "") or "")[:2000]

        return self.emit(title, body=body, url=link, key=key,
                         published_ms=published_ms,
                         author=getattr(entry, "author", "") or "")
