"""Direktes Abgreifen von Behoerden-Endpunkten - der schnelle Weg.

Warum nicht RSS: Behoerden veroeffentlichen ihre Dokumente typischerweise
mehrere Sekunden bis Minuten frueher auf der eigentlichen Seite bzw. in der API
als im Feed. Wer bei Zinsentscheiden oder Zolldekreten vorne sein will, fragt
den Zielendpunkt direkt und haeufig ab.

Fairness-Hinweis: Abfrageintervalle unter einer Sekunde auf oeffentlichen
Behoerdenservern sind unhoeflich und koennen zur Sperre fuehren. `min_poll_s`
ist deshalb eine harte Untergrenze.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..models import RawNews, SourceTier
from .base import NewsSource

log = logging.getLogger(__name__)

# Untergrenze fuer oeffentliche Endpunkte.
MIN_POLL_S = 0.5


class JSONPollSource(NewsSource):
    """Fragt einen JSON-Endpunkt ab und wandelt Eintraege in Meldungen um.

    `extract` bekommt die dekodierte Antwort und liefert eine Liste von Dicts
    mit den Schluesseln: headline, body, url, published_ms, key.
    """

    def __init__(self, source_id: str, url: str, tier: SourceTier,
                 extract: Callable[[Any], list[dict[str, Any]]],
                 poll_s: float = 2.0, *, timeout_s: float = 5.0,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(source_id, tier)
        self.url = url
        self.extract = extract
        self.poll_s = max(MIN_POLL_S, poll_s)
        self.timeout_s = timeout_s
        self.headers = {"User-Agent": "newsbot/0.1 (+research)", **(headers or {})}
        self._first_pass = True

    async def stream(self) -> AsyncIterator[RawNews]:
        try:
            import aiohttp
        except ImportError:
            log.error("%s: aiohttp fehlt - Quelle inaktiv", self.source_id)
            return

        attempt = 0
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    async with session.get(self.url, headers=self.headers) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        data = await resp.json(content_type=None)
                    attempt = 0
                    for item in self.extract(data):
                        news = self.emit(
                            item.get("headline", ""),
                            body=item.get("body", ""),
                            url=item.get("url", ""),
                            author=item.get("author", ""),
                            published_ms=item.get("published_ms"),
                            key=item.get("key"))
                        if news is not None and not self._first_pass:
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


# --------------------------------------------------------------------------
def federal_register_extract(data: Any) -> list[dict[str, Any]]:
    """Parser fuer die Federal-Register-API.

    Praesidialdokumente (Executive Orders, Proclamations) sind der amtliche
    Kanal fuer Zoelle und Sanktionen. Die API liefert sie frueher als die
    meisten Nachrichtenagenturen sie verarbeiten.
    """
    import calendar
    from datetime import datetime

    out: list[dict[str, Any]] = []
    for doc in (data or {}).get("results", []):
        title = doc.get("title") or ""
        if not title:
            continue
        pub_ms = None
        raw_date = doc.get("publication_date") or doc.get("signing_date")
        if raw_date:
            try:
                dt = datetime.strptime(raw_date, "%Y-%m-%d")
                pub_ms = int(calendar.timegm(dt.timetuple()) * 1000)
            except ValueError:
                pass
        out.append({
            "headline": title,
            "body": (doc.get("abstract") or "")[:1500],
            "url": doc.get("html_url", ""),
            "published_ms": pub_ms,
            "key": doc.get("document_number") or doc.get("html_url") or title,
        })
    return out


def make_federal_register_source(poll_s: float = 30.0) -> JSONPollSource:
    """Praesidialdokumente der letzten Tage, neueste zuerst."""
    url = (
        "https://www.federalregister.gov/api/v1/documents.json"
        "?per_page=20&order=newest"
        "&conditions[type][]=PRESDOCU"
        "&fields[]=title&fields[]=abstract&fields[]=html_url"
        "&fields[]=publication_date&fields[]=signing_date&fields[]=document_number"
    )
    return JSONPollSource("federal_register", url, SourceTier.PRIMARY,
                          federal_register_extract, poll_s=poll_s)
