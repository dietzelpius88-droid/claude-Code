"""Paper-Trading mit realistischem Ausfuehrungsmodell.

Bewusst pessimistisch: Die haeufigste Ursache fuer Strategien, die im Backtest
glaenzen und live verlieren, ist ein zu optimistisches Fill-Modell. Hier wird
angenommen:

  * IOC-Limits fuellen nur, wenn sie den Markt kreuzen.
  * Der Fill-Preis enthaelt Wurzel-Markteinfluss ueber dem besten Kurs.
  * Die Menge ist durch die sichtbare Buchtiefe begrenzt (Teilausfuehrungen).
  * Zusaetzlich wirkt eine konfigurierbare Latenzstrafe: Bei News laeuft der
    Preis waehrend unserer Reaktionszeit weiter - gegen uns.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

from ..models import Direction, Fill, OrderIntent, Quote, now_ms
from .base import Broker, MarketData

log = logging.getLogger(__name__)

IMPACT_COEFF = 0.6


@dataclass
class PaperFillModel:
    # Reaktionszeit des Gesamtsystems in ms (Feed -> Analyse -> Order -> Boerse).
    latency_ms: int = 350
    # Wie stark laeuft der Preis pro 100 ms Latenz in Basispunkten davon
    # (nur waehrend eines Ereignisses relevant).
    drift_bps_per_100ms: float = 1.2
    # Anteil der sichtbaren Tiefe, den wir pro Order abraeumen koennen.
    max_depth_take: float = 0.35
    # Zufaellige Restunsicherheit in bps (Standardabweichung).
    noise_bps: float = 2.0
    seed: int | None = None

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def fill(self, intent: OrderIntent, q: Quote, *, adv_usd: float,
             daily_vol_bps: float, fee_bps: float,
             event_active: bool = True) -> Fill | None:
        d = 1.0 if intent.side is Direction.LONG else -1.0
        best = q.ask if intent.side is Direction.LONG else q.bid
        if best <= 0:
            return None

        # 1. Latenzdrift: der Markt bewegt sich waehrend unserer Reaktionszeit.
        drift = 0.0
        if event_active:
            drift = self.drift_bps_per_100ms * (self.latency_ms / 100.0)
        # 2. Markteinfluss nach dem Wurzelgesetz.
        notional = intent.qty * best
        impact = IMPACT_COEFF * daily_vol_bps * math.sqrt(max(0.0, notional) / max(adv_usd, 1.0))
        # 3. Restrauschen.
        noise = self._rng.gauss(0.0, self.noise_bps)

        slip_bps = drift + impact + noise
        px = best * (1.0 + d * slip_bps / 10_000.0)

        # IOC-Limit: schlechter als das Limit wird nicht ausgefuehrt.
        qty = intent.qty
        if intent.side is Direction.LONG and px > intent.limit_px:
            if intent.tif == "IOC":
                return None
            px = intent.limit_px
        if intent.side is Direction.SHORT and px < intent.limit_px:
            if intent.tif == "IOC":
                return None
            px = intent.limit_px

        # Teilausfuehrung bei duennem Buch.
        depth = q.ask_size_usd if intent.side is Direction.LONG else q.bid_size_usd
        depth = depth or q.depth_usd
        if depth > 0:
            max_qty = (depth * self.max_depth_take) / px
            if qty > max_qty:
                log.info("Teilausfuehrung %s: %.4f von %.4f (Buchtiefe)",
                         intent.symbol, max_qty, qty)
                qty = max_qty
        if qty <= 0:
            return None

        fee = qty * px * fee_bps / 10_000.0
        return Fill(order_id=intent.id, symbol=intent.symbol, side=intent.side,
                    qty=qty, px=px, fee_usd=fee)


class PaperBroker(Broker):
    """Simulierte Boerse. Braucht Marktdaten und Instrumentenstammdaten."""

    name = "paper"

    def __init__(self, market: MarketData, universe, model: PaperFillModel | None = None):
        self.market = market
        self.universe = universe
        self.model = model or PaperFillModel()
        self.fills: list[Fill] = []
        self.rejected: list[tuple[OrderIntent, str]] = []

    async def submit(self, intent: OrderIntent) -> Fill | None:
        q = self.market.quote(intent.symbol)
        if q is None:
            self.rejected.append((intent, "kein Kurs"))
            return None
        asset = self.universe.get(intent.symbol)
        if asset is None:
            self.rejected.append((intent, "unbekanntes Instrument"))
            return None

        fill = self.model.fill(
            intent, q,
            adv_usd=asset.adv_usd,
            daily_vol_bps=asset.daily_vol_bps,
            fee_bps=asset.taker_fee_bps,
            event_active=intent.tag in ("entry", "add"),
        )
        if fill is None:
            self.rejected.append((intent, "Limit nicht erreicht"))
            log.info("NICHT AUSGEFUEHRT %s %s limit=%.6f markt=%.6f",
                     intent.side.value, intent.symbol, intent.limit_px, q.mid)
            return None

        self.fills.append(fill)
        slip = (fill.px - q.mid) / q.mid * 10_000.0 * (1 if intent.side is Direction.LONG else -1)
        log.info("FILL %-5s %-6s qty=%.6f @ %.6f (slippage %.1f bps, gebuehr %.2f USD) [%s]",
                 intent.side.value, intent.symbol, fill.qty, fill.px, slip,
                 fill.fee_usd, intent.tag)
        return fill

    async def cancel_all(self, symbol: str) -> int:
        return 0


@dataclass
class StaticMarket(MarketData):
    """Einfache Kursquelle fuer Tests und Replay: Preise werden gesetzt."""

    prices: dict[str, Quote] = field(default_factory=dict)

    def quote(self, symbol: str) -> Quote | None:
        return self.prices.get(symbol)

    def set(self, symbol: str, mid: float, *, spread_bps: float = 2.0,
            depth_usd: float = 500_000.0, atr_bps: float = 0.0) -> Quote:
        half = mid * spread_bps / 20_000.0
        q = Quote(symbol=symbol, bid=mid - half, ask=mid + half, ts_ms=now_ms(),
                  bid_size_usd=depth_usd / 2, ask_size_usd=depth_usd / 2,
                  atr_bps=atr_bps, depth_usd=depth_usd)
        self.prices[symbol] = q
        return q

    def move_bps(self, symbol: str, bps: float) -> Quote | None:
        q = self.prices.get(symbol)
        if q is None:
            return None
        return self.set(symbol, q.mid * (1 + bps / 10_000.0),
                        spread_bps=q.spread_bps, depth_usd=q.depth_usd, atr_bps=q.atr_bps)
