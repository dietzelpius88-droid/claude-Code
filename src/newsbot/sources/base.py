"""Basis fuer alle Nachrichtenquellen.

Eine Quelle ist ein asynchroner Erzeuger von `RawNews`. Wichtig ist, dass jede
Quelle
  * ihren eigenen Vertrauensgrad (`SourceTier`) kennt,
  * Duplikate innerhalb der Quelle selbst unterdrueckt,
  * bei Fehlern nicht stirbt, sondern mit wachsender Wartezeit neu verbindet,
  * ihre Latenz misst - sonst weiss man nie, warum man zu spaet war.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ..models import RawNews, SourceTier, now_ms

log = logging.getLogger(__name__)


@dataclass
class SourceStats:
    emitted: int = 0
    duplicates: int = 0
    errors: int = 0
    last_ms: int = 0
    latency_samples: deque[int] = field(default_factory=lambda: deque(maxlen=200))

    @property
    def median_latency_ms(self) -> int:
        if not self.latency_samples:
            return 0
        s = sorted(self.latency_samples)
        return s[len(s) // 2]


class NewsSource(ABC):
    def __init__(self, source_id: str, tier: SourceTier, *,
                 seen_cache: int = 2000) -> None:
        self.source_id = source_id
        self.tier = tier
        self.stats = SourceStats()
        self._seen: deque[str] = deque(maxlen=seen_cache)
        self._seen_set: set[str] = set()

    # ------------------------------------------------------------------
    def _is_new(self, key: str) -> bool:
        if key in self._seen_set:
            self.stats.duplicates += 1
            return False
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(key)
        self._seen_set.add(key)
        return True

    def emit(self, headline: str, *, body: str = "", url: str = "",
             author: str = "", published_ms: int | None = None,
             key: str | None = None, **meta) -> RawNews | None:
        """Baut eine RawNews, sofern sie neu ist."""
        k = key or url or headline
        if not self._is_new(k):
            return None
        now = now_ms()
        news = RawNews(
            source_id=self.source_id, tier=self.tier, headline=headline.strip(),
            body=body.strip(), url=url, author=author,
            published_ms=published_ms if published_ms else now,
            received_ms=now, meta=meta)
        self.stats.emitted += 1
        self.stats.last_ms = now
        self.stats.latency_samples.append(news.source_latency_ms)
        return news

    # ------------------------------------------------------------------
    @abstractmethod
    def stream(self) -> AsyncIterator[RawNews]:
        """Endloser Strom von Meldungen."""
        raise NotImplementedError

    async def _backoff(self, attempt: int, base: float = 1.0, cap: float = 60.0) -> None:
        delay = min(cap, base * (2 ** attempt))
        log.warning("%s: Neuverbindung in %.1fs (Versuch %d)", self.source_id, delay, attempt)
        await asyncio.sleep(delay)
