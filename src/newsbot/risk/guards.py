"""Not-Aus-Bedingungen ausserhalb des Portfolios.

Diese Wachen betreffen die INFRASTRUKTUR: Feeds, Boersenanbindung,
Kursqualitaet. Sie sind bewusst getrennt von den Portfoliolimits, weil sie
auch dann greifen muessen, wenn das Konto voellig gesund ist.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import GuardConfig
from ..models import Quote, now_ms

log = logging.getLogger(__name__)


@dataclass
class GuardState:
    cfg: GuardConfig
    last_news_ms: dict[str, int] = field(default_factory=dict)
    venue_errors: dict[str, int] = field(default_factory=dict)
    blocked_venues: dict[str, int] = field(default_factory=dict)   # venue -> bis wann
    tripped: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def note_news(self, source_id: str) -> None:
        self.last_news_ms[source_id] = now_ms()

    def stale_feeds(self) -> list[str]:
        """Quellen, die verdaechtig lange geschwiegen haben."""
        cutoff = now_ms() - self.cfg.feed_stale_s * 1000
        return [s for s, ts in self.last_news_ms.items() if ts < cutoff]

    # ------------------------------------------------------------------
    def note_order_error(self, venue: str) -> None:
        n = self.venue_errors.get(venue, 0) + 1
        self.venue_errors[venue] = n
        if n >= self.cfg.max_consecutive_order_errors:
            self.block_venue(venue, 300, f"{n} Orderfehler in Folge")

    def note_order_ok(self, venue: str) -> None:
        self.venue_errors[venue] = 0

    def note_fill(self, venue: str, expected_px: float, actual_px: float) -> float:
        """Prueft Ausfuehrungsqualitaet. Gibt die Abweichung in bps zurueck."""
        if expected_px <= 0:
            return 0.0
        dev_bps = abs(actual_px - expected_px) / expected_px * 10_000.0
        if dev_bps > self.cfg.fill_anomaly_bps:
            self.block_venue(venue, 600,
                             f"Ausfuehrung wich {dev_bps:.0f} bps vom Erwartungswert ab")
        return dev_bps

    def block_venue(self, venue: str, seconds: int, reason: str) -> None:
        self.blocked_venues[venue] = now_ms() + seconds * 1000
        self.tripped.append(f"{venue}: {reason}")
        log.error("BOERSE GESPERRT %s fuer %ds: %s", venue, seconds, reason)

    def venue_ok(self, venue: str) -> bool:
        return now_ms() >= self.blocked_venues.get(venue, 0)

    # ------------------------------------------------------------------
    def quote_ok(self, q: Quote, typical_spread_bps: float) -> tuple[bool, str]:
        if not self.cfg.enabled:
            return True, ""
        if q.age_ms > self.cfg.quote_stale_ms:
            return False, f"Kurs {q.age_ms} ms alt"
        if q.bid <= 0 or q.ask <= 0 or q.ask <= q.bid:
            return False, "unplausibles Quote (bid/ask)"
        if typical_spread_bps > 0 and q.spread_bps > typical_spread_bps * self.cfg.spread_blowout_mult:
            return False, (f"Spread {q.spread_bps:.1f}bps vs. normal "
                           f"{typical_spread_bps:.1f}bps")
        return True, ""

    def snapshot(self) -> dict[str, object]:
        return {
            "blocked_venues": {v: t for v, t in self.blocked_venues.items() if now_ms() < t},
            "stale_feeds": self.stale_feeds(),
            "venue_errors": dict(self.venue_errors),
            "tripped": self.tripped[-10:],
        }
