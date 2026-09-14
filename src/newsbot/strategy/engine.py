"""Orchestrator: verbindet Quellen, Auswertung, Risiko und Ausfuehrung.

Ablauf je Meldung
-----------------
    RawNews
      -> AnalysisPipeline      (Lexikon, dann Sprachmodell)
      -> AssetSelector         (welches Instrument, wie viel Bewegung)
      -> size_position         (wie gross)
      -> RiskManager.approve   (darf das ueberhaupt sein)
      -> TradeManager.open     (gestaffelter Einstieg)

Der Bot kann ZWEIMAL auf dieselbe Meldung reagieren: einmal provisorisch
(nur Lexikon, halbe Groesse) und einmal nach der Bestaetigung (Aufstockung
oder sofortige Aufloesung, falls die Bestaetigung ausbleibt).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..config import Config
from ..models import AnalyzedEvent, Direction, RawNews, SignalState, TradeSignal
from ..analysis.pipeline import AnalysisPipeline
from ..execution.base import Broker, MarketData
from ..execution.manager import ExitReason, TradeManager
from ..risk.guards import GuardState
from ..risk.limits import Portfolio, RiskManager
from ..risk.sizing import size_position
from .selector import AssetSelector
from .universe import Universe

log = logging.getLogger(__name__)


@dataclass
class EngineStats:
    news_seen: int = 0
    events: int = 0
    signals: int = 0
    approved: int = 0
    rejected: int = 0
    opened: int = 0
    aborted: int = 0
    rejections: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        key = reason.split("(")[0].strip()[:48]
        self.rejections[key] = self.rejections.get(key, 0) + 1


class NewsTradingEngine:
    def __init__(self, cfg: Config, universe: Universe, pipeline: AnalysisPipeline,
                 market: MarketData, broker: Broker,
                 portfolio: Portfolio | None = None) -> None:
        self.cfg = cfg
        self.universe = universe
        self.pipeline = pipeline
        self.market = market
        self.broker = broker
        self.pf = portfolio or Portfolio(cfg.risk.equity_usd)
        self.risk = RiskManager(cfg.risk, cfg.guards.blackout_hours_utc)
        self.guards = GuardState(cfg.guards)
        self.selector = AssetSelector(universe, cfg.signal, cfg.risk, cfg.execution)
        self.tm = TradeManager(broker, market, self.pf, cfg.risk, cfg.execution)
        self.stats = EngineStats()
        # news.id -> Symbole, die aus dieser Meldung eroeffnet wurden.
        self._news_positions: dict[str, set[str]] = {}
        self._running = False

    # ==================================================================
    async def on_news(self, news: RawNews) -> list[TradeSignal]:
        """Verarbeitet eine Meldung vollstaendig. Gibt die ausgefuehrten
        Signale zurueck (fuer Tests und Replay)."""
        self.stats.news_seen += 1
        self.guards.note_news(news.source_id)
        executed: list[TradeSignal] = []

        async for event in self.pipeline.process(news):
            self.stats.events += 1

            if event.meta.get("contradiction"):
                await self._handle_contradiction(event)
                continue
            if event.meta.get("abort"):
                await self._handle_abort(event)
                continue

            executed.extend(await self._trade_event(event))
        return executed

    # ------------------------------------------------------------------
    async def _trade_event(self, event: AnalyzedEvent) -> list[TradeSignal]:
        provisional = bool(event.meta.get("provisional"))
        news_id = event.news.id

        # Bestaetigung einer bereits offenen Vorhutposition: nicht doppelt
        # eroeffnen, nur den Zeit-Stop an die neue Einschaetzung anpassen.
        if not provisional and news_id in self._news_positions:
            self._retune_open(event)
            return []

        symbols = [a.symbol for a in self.universe.candidates(event.event_class)]
        quotes = self.market.quotes(symbols)
        signals = self.selector.select(event, quotes)
        self.stats.signals += len(signals)
        if not signals:
            return []

        executed: list[TradeSignal] = []
        for sig in signals:
            if not self.guards.venue_ok(sig.asset.venue):
                self.stats.reject(f"Boerse gesperrt: {sig.asset.venue}")
                continue
            q = quotes.get(sig.asset.symbol)
            if q is None:
                self.stats.reject("kein Kurs")
                continue
            ok, why = self.guards.quote_ok(q, sig.asset.typical_spread_bps)
            if not ok:
                self.stats.reject(f"Kursqualitaet: {why}")
                continue

            sizing = size_position(
                sig, q, self.cfg.risk,
                equity_usd=self.pf.equity_usd,
                gross_exposure_usd=self.pf.gross_exposure_usd)

            if provisional and sizing.ok:
                # Vorhut: kleinere Groesse, bis die Bestaetigung vorliegt.
                f = self.cfg.signal.provisional_size_factor
                sizing.qty *= f
                sizing.notional_usd *= f
                sizing.risk_usd *= f

            decision = self.risk.approve(sig, sizing, self.pf)
            if not decision.ok:
                sig.state = SignalState.REJECTED
                self.stats.reject(decision.reason)
                continue

            self.stats.approved += 1
            pos = await self.tm.open(sig, sizing)
            if pos is None:
                self.stats.reject("Einstieg nicht ausgefuehrt")
                continue

            sig.state = SignalState.EXECUTED
            self.stats.opened += 1
            self._news_positions.setdefault(news_id, set()).add(pos.symbol)
            executed.append(sig)
        return executed

    # ------------------------------------------------------------------
    def _retune_open(self, event: AnalyzedEvent) -> None:
        """Die Bestaetigung traegt: Zeit-Stop an die bestaetigte Halbwertszeit
        anpassen, damit eine starke Meldung nicht vorzeitig ausgestoppt wird."""
        from ..models import now_ms
        for sym in self._news_positions.get(event.news.id, set()):
            pos = self.pf.positions.get(sym)
            if pos is None:
                continue
            new_stop = pos.opened_ms + event.half_life_s * 1000
            if new_stop > pos.time_stop_ms:
                pos.time_stop_ms = new_stop
                log.info("BESTAETIGT %s - Zeit-Stop verlaengert auf %ds",
                         sym, event.half_life_s)

    async def _handle_abort(self, event: AnalyzedEvent) -> None:
        """Die Bestaetigung blieb aus oder widerspricht: Vorhut sofort aufloesen."""
        syms = self._news_positions.pop(event.news.id, set())
        for sym in syms:
            if sym in self.pf.positions:
                self.stats.aborted += 1
                log.warning("ABBRUCH %s - Bestaetigung fehlt (%s)", sym,
                            event.meta.get("disagreement", "zu schwach"))
                await self.tm.flatten(sym, ExitReason.CONTRADICTION)

    async def _handle_contradiction(self, event: AnalyzedEvent) -> None:
        """Dementi/Ruecknahme: alle Positionen der betroffenen Klasse schliessen."""
        if not self.cfg.guards.flatten_on_contradiction:
            return
        for sym, pos in list(self.pf.positions.items()):
            if pos.event_class is event.event_class:
                log.warning("WIDERSPRUCH -> %s wird glattgestellt", sym)
                self.stats.aborted += 1
                await self.tm.flatten(sym, ExitReason.CONTRADICTION)

    # ==================================================================
    async def tick(self) -> None:
        """Positionspflege. Muss regelmaessig laufen (Stops, Ziele, Zeit-Stop)."""
        await self.tm.on_tick()
        if self.pf.is_halted and self.pf.positions:
            log.warning("Handel gestoppt - offene Positionen werden glattgestellt")
            await self.tm.flatten_all(ExitReason.RISK_HALT)

    async def run(self, sources, tick_interval_s: float = 1.0) -> None:
        """Hauptschleife: Quellen lesen und Positionen pflegen."""
        self._running = True
        queue: asyncio.Queue[RawNews] = asyncio.Queue(maxsize=1000)

        async def pump(src) -> None:
            async for news in src.stream():
                await queue.put(news)

        async def consume() -> None:
            while self._running:
                news = await queue.get()
                try:
                    await self.on_news(news)
                except Exception:
                    log.exception("Fehler bei der Verarbeitung von %s", news.headline[:80])
                finally:
                    queue.task_done()

        async def ticker() -> None:
            while self._running:
                try:
                    await self.tick()
                except Exception:
                    log.exception("Fehler in der Positionspflege")
                await asyncio.sleep(tick_interval_s)

        tasks = [asyncio.create_task(pump(s)) for s in sources]
        tasks.append(asyncio.create_task(consume()))
        tasks.append(asyncio.create_task(ticker()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            for t in tasks:
                t.cancel()

    async def shutdown(self, flatten: bool = True) -> None:
        self._running = False
        if flatten:
            await self.tm.flatten_all(ExitReason.MANUAL)
        await self.broker.close()

    # ------------------------------------------------------------------
    def report(self) -> dict[str, object]:
        closed = self.tm.closed
        wins = [t for t in closed if t.pnl_usd > 0]
        pnl = sum(t.pnl_usd for t in closed)
        gross_win = sum(t.pnl_usd for t in wins)
        gross_loss = -sum(t.pnl_usd for t in closed if t.pnl_usd < 0)
        return {
            "meldungen": self.stats.news_seen,
            "ereignisse": self.stats.events,
            "signale": self.stats.signals,
            "eroeffnet": self.stats.opened,
            "abgelehnt": self.stats.rejected,
            "abbrueche": self.stats.aborted,
            "ablehnungsgruende": dict(sorted(self.stats.rejections.items(),
                                             key=lambda kv: -kv[1])[:8]),
            "geschlossene_teilgeschaefte": len(closed),
            "trefferquote": round(len(wins) / len(closed), 3) if closed else None,
            "profitfaktor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "summe_R": round(sum(t.r_multiple for t in closed), 2),
            "pnl_usd": round(pnl, 2),
            "equity_usd": round(self.pf.equity_usd, 2),
            "rendite_pct": round((self.pf.equity_usd / self.pf.starting_equity_usd - 1) * 100, 3),
            "offene_positionen": list(self.pf.positions.keys()),
            "wachen": self.guards.snapshot(),
        }
