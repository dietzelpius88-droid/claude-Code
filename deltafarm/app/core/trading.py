"""Orchestrierung von Vorschau und Ausfuehrung.

Holt alles zusammen, was der Preflight braucht, stellt die Vorschau aus und
fuehrt sie - nach erneuter Pruefung - aus.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional

import structlog

from app.adapters.base import AdapterError
from app.core.execution import ExecutionEngine, ExecutionOutcome, ExecutionPlan
from app.core.preflight import PreflightInput, PreflightResult, run_preflight
from app.core.preview import PreviewStore
from app.core.rebalance import RebalanceError, RebalancePlan, plan_rebalance
from app.core.sizing import Sizing, SizingError, compute_sizing
from app.models.domain import Balance, FundingInfo, OrderBook, SymbolRules

LOG = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PreviewRequest:
    symbol: str
    notional_usd: Decimal
    long_venue: str
    short_venue: str
    max_slippage: Decimal = Decimal("0.002")
    long_leverage: Decimal = Decimal(1)
    short_leverage: Decimal = Decimal(1)
    # Zuerst das illiquidere oder langsamere Bein. Ohne Angabe die Long-Seite.
    first_venue: Optional[str] = None
    hedge_timeout_seconds: float = 10.0
    auto_rollback: bool = False


@dataclass(frozen=True)
class PreviewResponse:
    token: Optional[str]
    sizing: Optional[Sizing]
    preflight: Optional[PreflightResult]
    marks: Mapping[str, Decimal]
    funding: Mapping[str, Decimal]
    error: Optional[str] = None


class TradingService:
    def __init__(
        self,
        *,
        adapters: Mapping[str, object],
        engine: ExecutionEngine,
        previews: PreviewStore,
        journal=None,
        margin_share: Decimal = Decimal("0.5"),
    ) -> None:
        self._adapters = dict(adapters)
        self._engine = engine
        self._previews = previews
        self._journal = journal
        self._margin_share = margin_share

    def _adapter(self, venue: str):
        adapter = self._adapters.get(venue)
        if adapter is None:
            raise KeyError(f"Kein Adapter fuer {venue} registriert.")
        return adapter

    # --- Daten einsammeln -------------------------------------------------

    async def _optional(self, coro):
        """Ein fehlender Wert ist kein Absturz - der Preflight wertet ihn."""
        try:
            return await coro
        except (AdapterError, KeyError, NotImplementedError) as exc:
            LOG.warning("vorschau_teil_fehlt", fehler=str(exc))
            return None

    async def _gather(self, venue: str, symbol: str, size_hint: int = 50):
        adapter = self._adapter(venue)
        regeln, funding, buch, mark, balance = await asyncio.gather(
            self._optional(adapter.get_symbol_rules(symbol)),
            self._optional(adapter.get_funding(symbol)),
            self._optional(adapter.get_orderbook(symbol, size_hint)),
            self._optional(adapter.get_mark_price(symbol)),
            self._optional(adapter.get_balance()),
        )
        return regeln, funding, buch, mark, balance

    # --- Vorschau ---------------------------------------------------------

    async def preview(self, anfrage: PreviewRequest) -> PreviewResponse:
        lang, kurz = anfrage.long_venue, anfrage.short_venue

        (lr, lf, lb, lm, lbal), (kr, kf, kb, km, kbal) = await asyncio.gather(
            self._gather(lang, anfrage.symbol),
            self._gather(kurz, anfrage.symbol),
        )

        marks = {v: m for v, m in ((lang, lm), (kurz, km)) if m is not None}
        funding_raten = {
            v: f.rate_hourly for v, f in ((lang, lf), (kurz, kf)) if f is not None
        }

        fehlend = [
            name
            for name, wert in (
                (f"Regeln von {lang}", lr),
                (f"Regeln von {kurz}", kr),
                (f"Funding von {lang}", lf),
                (f"Funding von {kurz}", kf),
                (f"Mark-Preis von {lang}", lm),
                (f"Mark-Preis von {kurz}", km),
            )
            if wert is None
        ]
        if fehlend:
            return PreviewResponse(
                token=None,
                sizing=None,
                preflight=None,
                marks=marks,
                funding=funding_raten,
                error="Vorschau nicht moeglich, es fehlen: " + ", ".join(fehlend),
            )

        try:
            sizing = compute_sizing(
                notional_usd=anfrage.notional_usd,
                long_rules=lr,
                short_rules=kr,
                long_mark=lm,
                short_mark=km,
            )
        except SizingError as exc:
            return PreviewResponse(
                token=None, sizing=None, preflight=None, marks=marks,
                funding=funding_raten, error=str(exc),
            )

        erreichbar = {lang: lb is not None or lm is not None, kurz: kb is not None or km is not None}

        ergebnis = run_preflight(
            PreflightInput(
                sizing=sizing,
                long_rules=lr,
                short_rules=kr,
                long_funding=lf,
                short_funding=kf,
                long_book=lb,
                short_book=kb,
                long_balance=lbal,
                short_balance=kbal,
                reachable=erreichbar,
                # Weder Extended noch Lighter dokumentieren einen Endpunkt fuer
                # die Serverzeit. Der Zeitversatz bleibt damit unbekannt und
                # erzeugt eine Warnung statt eines stillen Bestehens.
                clock_skew_seconds={lang: None, kurz: None},
                max_slippage=anfrage.max_slippage,
                long_leverage=anfrage.long_leverage,
                short_leverage=anfrage.short_leverage,
                margin_share=self._margin_share,
            )
        )

        token = self._previews.put(
            payload={
                "symbol": anfrage.symbol,
                "long_venue": lang,
                "short_venue": kurz,
                "size": str(sizing.size),
                "notional_usd": str(anfrage.notional_usd),
                "first_venue": anfrage.first_venue or lang,
                "hedge_timeout_seconds": anfrage.hedge_timeout_seconds,
                "auto_rollback": anfrage.auto_rollback,
                "preflight_ok": ergebnis.ok,
            },
            marks=marks,
            funding=funding_raten,
        )

        if self._journal is not None:
            self._journal.record_event(
                kind="vorschau",
                symbol=anfrage.symbol,
                message=f"Vorschau {anfrage.symbol}: long {lang}, short {kurz}, "
                f"{sizing.size} ({'bestanden' if ergebnis.ok else 'blockiert'})",
                payload={
                    "size": str(sizing.size),
                    "netto_apr": str(ergebnis.net_apr),
                    "preflight_ok": ergebnis.ok,
                    "gruende": ergebnis.blocking_reasons,
                },
            )

        return PreviewResponse(
            token=token, sizing=sizing, preflight=ergebnis, marks=marks, funding=funding_raten
        )

    # --- Ausfuehrung ------------------------------------------------------

    async def open_from_token(self, token: str) -> ExecutionOutcome:
        """Prueft die Vorschau erneut und fuehrt sie aus.

        Zwischen Vorschau und Klick koennen Sekunden liegen - in denen sich
        Preis und Funding bewegen. Deshalb wird hier frisch geholt und gegen
        die Vorschau gehalten, bevor irgendetwas passiert.
        """
        vorschau = self._previews.get(token)
        if vorschau is None:
            from app.core.preview import PreviewUnknown

            raise PreviewUnknown("Diese Vorschau ist unbekannt oder bereits verwendet.")

        daten = vorschau.payload
        lang, kurz = daten["long_venue"], daten["short_venue"]
        symbol = daten["symbol"]

        # Zuerst die Frage, ob diese Vorschau ueberhaupt je ausfuehrbar war.
        # Sonst bekaeme der Nutzer bei einer nie freigegebenen Vorschau eine
        # Meldung ueber Marktbewegung - und suchte den Fehler an der falschen
        # Stelle.
        if not daten.get("preflight_ok", False):
            raise ValueError(
                "Diese Vorschau hat den Preflight nicht bestanden und wird nicht ausgefuehrt."
            )

        aktuelle_marks: dict[str, Decimal] = {}
        aktuelles_funding: dict[str, Decimal] = {}
        for venue in (lang, kurz):
            adapter = self._adapter(venue)
            mark = await self._optional(adapter.get_mark_price(symbol))
            funding = await self._optional(adapter.get_funding(symbol))
            if mark is not None:
                aktuelle_marks[venue] = mark
            if funding is not None:
                aktuelles_funding[venue] = funding.rate_hourly

        # Wirft, wenn zu alt oder zu weit bewegt - und verbraucht den Token.
        self._previews.consume(token, marks=aktuelle_marks, funding=aktuelles_funding)

        plan = ExecutionPlan(
            symbol=symbol,
            long_venue=lang,
            short_venue=kurz,
            size=Decimal(daten["size"]),
            notional_usd=Decimal(daten["notional_usd"]),
            first_venue=daten["first_venue"],
            hedge_timeout_seconds=float(daten["hedge_timeout_seconds"]),
            auto_rollback=bool(daten["auto_rollback"]),
        )
        return await self._engine.open_pair(plan)


    # --- Neu ausrichten ---------------------------------------------------

    async def rebalance_plan(self, pair_id: int) -> RebalancePlan:
        """Rechnet beide Wege durch, ein Rest-Delta abzubauen.

        Entschieden wird nichts - das bleibt beim Nutzer, weil Aufstocken und
        Verkleinern verschiedene Dinge kosten und verschiedene Dinge bringen.
        """
        store = self._engine._store
        paar = store.get_pair(pair_id)
        if paar is None:
            raise KeyError(f"Paar {pair_id} nicht gefunden.")

        beine = {b.venue: (b.filled_size or Decimal(0)) for b in store.legs(pair_id)}

        lang_regeln = await self._optional(
            self._adapter(paar.long_venue).get_symbol_rules(paar.symbol)
        )
        kurz_regeln = await self._optional(
            self._adapter(paar.short_venue).get_symbol_rules(paar.symbol)
        )
        mark = await self._optional(self._adapter(paar.long_venue).get_mark_price(paar.symbol))

        if lang_regeln is None or kurz_regeln is None or mark is None:
            raise RebalanceError(
                f"{paar.symbol}: Regeln oder Mark-Preis nicht abrufbar - ohne sie wird nichts "
                "gerechnet."
            )

        # Das groebere Raster gilt, damit die Menge auf beiden Seiten passt.
        from app.core.sizing import common_lot_size

        return plan_rebalance(
            symbol=paar.symbol,
            long_venue=paar.long_venue,
            short_venue=paar.short_venue,
            long_size=beine.get(paar.long_venue, Decimal(0)),
            short_size=beine.get(paar.short_venue, Decimal(0)),
            mark_price=mark,
            lot_size=common_lot_size(lang_regeln.lot_size, kurz_regeln.lot_size),
            long_taker_fee=lang_regeln.taker_fee,
            short_taker_fee=kurz_regeln.taker_fee,
        )

    async def rebalance(self, pair_id: int, *, action: str):
        """Fuehrt einen der beiden Wege aus."""
        if action not in ("increase", "decrease"):
            raise ValueError(
                f"Unbekannte Aktion {action!r} - erlaubt sind 'increase' und 'decrease'."
            )

        plan = await self.rebalance_plan(pair_id)
        if plan.balanced:
            raise ValueError("Die Beine sind bereits gleich gross - nichts auszurichten.")

        bein = plan.increase if action == "increase" else plan.decrease
        assert bein is not None

        ergebnis, fehler = await self._engine._send(
            pair_id,
            bein.venue,
            plan.symbol,
            bein.side,
            bein.size,
            zweck="rebalance",
            reduce_only=bein.reduce_only,
        )

        if self._journal is not None:
            self._journal.record_event(
                kind="neu_ausgerichtet",
                symbol=plan.symbol,
                pair_id=pair_id,
                level="INFO" if fehler is None else "ERROR",
                message=(
                    f"{plan.symbol}: {bein.size} auf {bein.venue} "
                    f"({'aufgestockt' if action == 'increase' else 'verkleinert'})"
                    + (f" - fehlgeschlagen: {fehler}" if fehler else "")
                ),
                payload={
                    "action": action,
                    "venue": bein.venue,
                    "size": str(bein.size),
                    "reduce_only": bein.reduce_only,
                },
            )

        if fehler is not None:
            raise RebalanceError(fehler)
        return ergebnis
