"""Soziale Quellen (X, Truth Social, Telegram).

Vertrauensmodell
----------------
Der VERIFIZIERTE Originalaccount eines Entscheidungstraegers ist eine
Primaerquelle - dort steht die Nachricht frueher als irgendwo sonst.
Alles andere (Repost-Bots, "Breaking"-Accounts, anonyme Kanaele) ist
SourceTier.SOCIAL und darf nie allein einen Trade ausloesen.

Genau hier passieren die teuersten Unfaelle: gefaelschte Meldungen ueber
gehackte oder nachgeahmte Accounts haben schon mehrfach Milliarden bewegt.
Deshalb:
  * `verified_only`: nur der echte Account zaehlt als Primaerquelle,
  * `require_confirmation` in der Signal-Konfiguration erzwingt eine
    zweite, unabhaengige Quelle innerhalb weniger Sekunden.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ..models import RawNews, SourceTier
from .base import NewsSource

log = logging.getLogger(__name__)

# Beobachtete Accounts: Kennung -> (Anzeigename, Vertrauensstufe).
# Der Originalaccount eines Amtstraegers ist primaer, Kommentatoren sind es nicht.
WATCHLIST: dict[str, tuple[str, SourceTier]] = {
    "realDonaldTrump": ("Donald Trump", SourceTier.PRIMARY),
    "WhiteHouse": ("White House", SourceTier.PRIMARY),
    "POTUS": ("POTUS", SourceTier.PRIMARY),
    "federalreserve": ("Federal Reserve", SourceTier.PRIMARY),
    "USTreasury": ("US Treasury", SourceTier.PRIMARY),
    "SECGov": ("SEC", SourceTier.PRIMARY),
    "CFTC": ("CFTC", SourceTier.PRIMARY),
    "ecb": ("EZB", SourceTier.PRIMARY),
    "elonmusk": ("Elon Musk", SourceTier.MAINSTREAM),
    "DeItaone": ("Walter Bloomberg", SourceTier.AGGREGATOR),
    "FirstSquawk": ("First Squawk", SourceTier.AGGREGATOR),
}

# Signatur: (account) -> Liste von Posts als Dicts
FetchFn = Callable[[str], Awaitable[list[dict[str, Any]]]]


class SocialAccountSource(NewsSource):
    """Beobachtet einen einzelnen Account ueber eine austauschbare Abruffunktion.

    `fetch` kapselt den Anbieter (offizielle API, Streaming-Endpunkt,
    Telegram-Client). Erwartetes Format je Post:
        {"id", "text", "created_ms", "url", "is_repost", "is_reply"}
    """

    def __init__(self, account: str, fetch: FetchFn, *,
                 tier: SourceTier | None = None, poll_s: float = 2.0,
                 skip_reposts: bool = True, skip_replies: bool = False,
                 min_chars: int = 12) -> None:
        name, default_tier = WATCHLIST.get(account, (account, SourceTier.SOCIAL))
        super().__init__(f"social:{account}", tier or default_tier)
        self.account = account
        self.display_name = name
        self.fetch = fetch
        self.poll_s = poll_s
        self.skip_reposts = skip_reposts
        self.skip_replies = skip_replies
        self.min_chars = min_chars
        self._first_pass = True

    async def stream(self) -> AsyncIterator[RawNews]:
        attempt = 0
        while True:
            try:
                posts = await self.fetch(self.account)
                attempt = 0
                for post in sorted(posts, key=lambda p: p.get("created_ms", 0)):
                    text = (post.get("text") or "").strip()
                    if len(text) < self.min_chars:
                        continue
                    if self.skip_reposts and post.get("is_repost"):
                        continue
                    if self.skip_replies and post.get("is_reply"):
                        continue
                    news = self.emit(
                        text, url=post.get("url", ""), author=self.display_name,
                        published_ms=post.get("created_ms"),
                        key=str(post.get("id") or text[:100]),
                        account=self.account, channel="social")
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
