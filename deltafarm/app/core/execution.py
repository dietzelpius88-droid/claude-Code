"""Ausfuehrungsengine fuer delta-neutrale Paare.

Das ist der riskanteste Teil des Systems, weil ein einseitig gefuelltes Paar
eine ungesicherte Richtungswette ist. Drei Regeln halten das in Schach:

1. **Zustand vor Netzwerkaufruf.** Jeder Uebergang steht in der Datenbank,
   bevor die Order rausgeht. Ein Absturz mitten in der Ausfuehrung ist damit
   rekonstruierbar.
2. **UNHEDGED entsteht nie stillschweigend und vergeht nie stillschweigend.**
   Er wird laut protokolliert und verlangt eine Entscheidung - ausser der
   Auto-Rollback ist ausdruecklich eingeschaltet.
3. **Die Gegenseite folgt der tatsaechlich gefuellten Menge**, nie der
   geplanten.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping, Optional

import structlog

from app.adapters.base import AdapterError, TradingNotSupported
from app.models.domain import (
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    PairState,
    Side,
)
from app.core.results import ResultInput, compute_pair_result
from app.storage.pairs import PairStore
from app.storage.zeit import als_utc

LOG = structlog.get_logger(__name__)

DEFAULT_HEDGE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ExecutionPlan:
    symbol: str
    long_venue: str
    short_venue: str
    size: Decimal
    notional_usd: Decimal
    # Zuerst das illiquidere oder langsamere Bein. Konfiguriert, nicht geraten.
    first_venue: str
    hedge_timeout_seconds: float = DEFAULT_HEDGE_TIMEOUT_SECONDS
    auto_rollback: bool = False

    @property
    def second_venue(self) -> str:
        return self.short_venue if self.first_venue == self.long_venue else self.long_venue

    def side_for(self, venue: str) -> Side:
        return Side.LONG if venue == self.long_venue else Side.SHORT


@dataclass(frozen=True)
class ExecutionOutcome:
    pair_id: int
    state: PairState
    detail: str = ""
    filled: Mapping[str, Decimal] = None  # je Boerse


@dataclass(frozen=True)
class RecoveryReport:
    pair_id: int
    symbol: str
    state: PairState
    discrepancy: bool
    detail: str


@dataclass(frozen=True)
class PanicOutcome:
    cancelled_orders: int
    closed_positions: int
    errors: list[str]


class ExecutionEngine:
    def __init__(
        self,
        *,
        adapters: Mapping[str, object],
        store: PairStore,
        journal=None,
        dry_run: bool = True,
    ) -> None:
        self._adapters = dict(adapters)
        self._store = store
        self.journal = journal
        self._dry_run = dry_run

    def _adapter(self, venue: str):
        adapter = self._adapters.get(venue)
        if adapter is None:
            raise KeyError(f"Kein Adapter fuer {venue} registriert.")
        return adapter

    @staticmethod
    def _client_order_id(pair_id: int, venue: str, zweck: str) -> str:
        """Selbst erzeugte ID. Ein Retry darf nie zu einer zweiten Position fuehren."""
        return f"df-{pair_id}-{venue}-{zweck}-{uuid.uuid4().hex[:8]}"

    # --- Oeffnen ----------------------------------------------------------

    async def _send(
        self,
        pair_id: int,
        venue: str,
        symbol: str,
        side: Side,
        size: Decimal,
        *,
        zweck: str,
        timeout: Optional[float] = None,
        reduce_only: bool = False,
    ) -> tuple[Optional[OrderResult], Optional[str]]:
        """Schreibt die Order, sendet sie und haelt das Ergebnis fest.

        Gibt (Ergebnis, Fehlertext) zurueck. Bei einem Timeout ist das Ergebnis
        None und der Fehlertext sagt ausdruecklich, dass der Zustand der Order
        **unbekannt** ist - die Boerse kann sie trotzdem ausgefuehrt haben.
        """
        anfrage = OrderRequest(
            venue=venue,
            symbol=symbol,
            side=side,
            size=size,
            order_type=OrderType.MARKET,
            reduce_only=reduce_only,
            client_order_id=self._client_order_id(pair_id, venue, zweck),
        )
        order_id = self._store.record_order(pair_id, anfrage, dry_run=self._dry_run)

        adapter = self._adapter(venue)
        try:
            if timeout is not None:
                ergebnis = await asyncio.wait_for(adapter.place_order(anfrage), timeout=timeout)
            else:
                ergebnis = await adapter.place_order(anfrage)
        except asyncio.TimeoutError:
            text = (
                f"{venue}: keine Antwort innerhalb von {timeout} s. Der Zustand der Order "
                f"{anfrage.client_order_id} ist unbekannt - sie kann an der Boerse trotzdem "
                "ausgefuehrt worden sein. Vor dem Nachziehen pruefen, sonst wird doppelt gehedgt."
            )
            LOG.error("order_timeout", venue=venue, client_order_id=anfrage.client_order_id)
            return None, text
        except AdapterError as exc:
            LOG.error("order_fehlgeschlagen", venue=venue, fehler=str(exc))
            return None, f"{venue}: {exc}"

        self._store.record_result(order_id, ergebnis)
        return ergebnis, None

    async def open_pair(self, plan: ExecutionPlan) -> ExecutionOutcome:
        """Oeffnet ein Paar: erst das konfigurierte Bein, dann sofort das zweite."""
        pair_id = self._store.create_pair(
            symbol=plan.symbol,
            long_venue=plan.long_venue,
            short_venue=plan.short_venue,
            notional_usd=plan.notional_usd,
            size=plan.size,
            first_venue=plan.first_venue,
        )

        erste = plan.first_venue
        zweite = plan.second_venue

        # --- erstes Bein ---
        self._store.set_state(pair_id, PairState.OPENING_FIRST, message=f"Order an {erste}")
        ergebnis, fehler = await self._send(
            pair_id, erste, plan.symbol, plan.side_for(erste), plan.size, zweck="open1"
        )

        if ergebnis is None or ergebnis.filled_size <= 0:
            grund = fehler or (ergebnis.detail if ergebnis else "keine Füllung")
            self._store.set_state(
                pair_id,
                PairState.FAILED,
                message=f"Erstes Bein ({erste}) nicht gefuellt: {grund}",
            )
            # Keine Position entstanden - das zweite Bein wird gar nicht erst versucht.
            return ExecutionOutcome(pair_id, PairState.FAILED, grund, {})

        gefuellt_erste = ergebnis.filled_size
        self._store.set_state(
            pair_id,
            PairState.FIRST_FILLED,
            message=f"{erste} gefuellt: {gefuellt_erste}",
            payload={"gefuellt": str(gefuellt_erste)},
        )

        # --- zweites Bein, auf die tatsaechlich gefuellte Menge ---
        self._store.set_state(
            pair_id,
            PairState.OPENING_SECOND,
            message=f"Order an {zweite} ueber {gefuellt_erste} (Timer {plan.hedge_timeout_seconds} s)",
        )
        zweites, fehler2 = await self._send(
            pair_id,
            zweite,
            plan.symbol,
            plan.side_for(zweite),
            gefuellt_erste,
            zweck="open2",
            timeout=plan.hedge_timeout_seconds,
        )

        gefuellt_zweite = zweites.filled_size if zweites else Decimal(0)
        beide_gleich = zweites is not None and gefuellt_zweite == gefuellt_erste

        if beide_gleich:
            self._store.set_state(
                pair_id, PairState.OPEN, message=f"Beide Beine ueber {gefuellt_erste} gefuellt"
            )
            return ExecutionOutcome(
                pair_id,
                PairState.OPEN,
                "beide Beine gefüllt",
                {erste: gefuellt_erste, zweite: gefuellt_zweite},
            )

        grund = fehler2 or (
            f"{zweite}: nur {gefuellt_zweite} von {gefuellt_erste} gefüllt"
            if zweites
            else "keine Antwort von der Börse"
        )
        self._store.set_state(
            pair_id,
            PairState.UNHEDGED,
            message=f"UNGESICHERT: {erste} steht mit {gefuellt_erste}, {zweite} nicht. {grund}",
            payload={"erste": str(gefuellt_erste), "zweite": str(gefuellt_zweite)},
        )
        LOG.error(
            "unhedged",
            pair_id=pair_id,
            symbol=plan.symbol,
            erste_boerse=erste,
            gefuellt=str(gefuellt_erste),
            grund=grund,
        )

        if plan.auto_rollback:
            LOG.warning("auto_rollback", pair_id=pair_id)
            return await self.resolve_unhedged(pair_id, action="rollback", grund="Auto-Rollback")

        return ExecutionOutcome(
            pair_id,
            PairState.UNHEDGED,
            grund,
            {erste: gefuellt_erste, zweite: gefuellt_zweite},
        )

    # --- UNHEDGED aufloesen ----------------------------------------------

    async def resolve_unhedged(
        self,
        pair_id: int,
        *,
        action: Literal["hedge", "rollback"],
        grund: str = "",
    ) -> ExecutionOutcome:
        """Zieht die Gegenseite nach oder schliesst das erste Bein wieder."""
        if action not in ("hedge", "rollback"):
            raise ValueError(f"Unbekannte Aktion {action!r} - erlaubt sind 'hedge' und 'rollback'.")

        paar = self._store.get_pair(pair_id)
        if paar is None:
            raise KeyError(f"Paar {pair_id} nicht gefunden.")

        beine = self._store.legs(pair_id)
        gefuellt = {b.venue: (b.filled_size or Decimal(0)) for b in beine}
        offen = [b for b in beine if (b.filled_size or Decimal(0)) > 0]

        if action == "hedge":
            # Die Differenz zwischen den Beinen nachziehen.
            lang = gefuellt.get(paar.long_venue, Decimal(0))
            kurz = gefuellt.get(paar.short_venue, Decimal(0))
            fehlbetrag = abs(lang - kurz)
            ziel = paar.short_venue if lang > kurz else paar.long_venue
            seite = Side.SHORT if ziel == paar.short_venue else Side.LONG

            if fehlbetrag <= 0:
                self._store.set_state(pair_id, PairState.OPEN, message="Beine bereits gleich gross")
                return ExecutionOutcome(pair_id, PairState.OPEN, "nichts nachzuziehen", gefuellt)

            self._store.set_state(
                pair_id,
                PairState.OPENING_SECOND,
                message=f"Nachziehen: {fehlbetrag} auf {ziel} ({grund or 'manuell'})",
            )
            ergebnis, fehler = await self._send(
                pair_id, ziel, paar.symbol, seite, fehlbetrag, zweck="hedge"
            )

            if ergebnis is not None and ergebnis.filled_size >= fehlbetrag:
                self._store.set_state(pair_id, PairState.OPEN, message="Gegenseite nachgezogen")
                return ExecutionOutcome(pair_id, PairState.OPEN, "Gegenseite nachgezogen", gefuellt)

            text = fehler or f"{ziel}: Nachziehen nicht vollständig gefüllt"
            self._store.set_state(pair_id, PairState.UNHEDGED, message=f"Nachziehen gescheitert: {text}")
            return ExecutionOutcome(pair_id, PairState.UNHEDGED, text, gefuellt)

        # --- Rollback ---
        self._store.set_state(
            pair_id,
            PairState.ROLLING_BACK,
            message=f"Rollback: offene Beine schliessen ({grund or 'manuell'})",
        )
        fehler: list[str] = []
        for bein in offen:
            try:
                problem = await self._close_leg(pair_id, paar.symbol, bein)
            except (AdapterError, KeyError) as exc:
                problem = f"{bein.venue}: {exc}"
            if problem:
                fehler.append(problem)

        if fehler:
            self._store.set_state(
                pair_id, PairState.UNHEDGED, message=f"Rollback unvollstaendig: {'; '.join(fehler)}"
            )
            return ExecutionOutcome(pair_id, PairState.UNHEDGED, "; ".join(fehler), gefuellt)

        self._store.set_state(pair_id, PairState.CLOSED, message="Rollback abgeschlossen")
        self._finalise(pair_id)
        return ExecutionOutcome(pair_id, PairState.CLOSED, "zurückgerollt", gefuellt)

    # --- Schliessen -------------------------------------------------------

    async def _close_leg(self, pair_id: int, symbol: str, bein) -> Optional[str]:
        """Schliesst ein Bein ueber eine Reduce-Only-Order.

        Nicht ueber close_position(symbol): die Order laeuft durch dieselbe
        Erfassung wie das Oeffnen, damit Menge, Preis und Gebuehr als Fill in
        der Datenbank landen. Ohne diese Gegenbuchung gaebe es spaeter kein
        Preis-PnL und damit keine Ergebniszeile.
        """
        menge = bein.filled_size or Decimal(0)
        if menge <= 0:
            return None

        gegenseite = Side.SHORT if Side(bein.side) is Side.LONG else Side.LONG
        ergebnis, fehler = await self._send(
            pair_id, bein.venue, symbol, gegenseite, menge, zweck="close", reduce_only=True
        )
        if fehler is not None:
            return fehler
        if ergebnis is None or ergebnis.filled_size < menge:
            gefuellt = ergebnis.filled_size if ergebnis else Decimal(0)
            return f"{bein.venue}: nur {gefuellt} von {menge} glattgestellt"
        return None

    def _finalise(self, pair_id: int) -> None:
        """Ordnet Funding zu und haelt das Endergebnis fest (Auftrag 6.5)."""
        paar = self._store.get_pair(pair_id)
        if paar is None:  # pragma: no cover
            return

        self._store.attribute_funding(pair_id)
        zahlungen = [z.amount for z in self._store.funding_for(pair_id)]

        ergebnis = compute_pair_result(
            ResultInput(
                symbol=paar.symbol,
                long_venue=paar.long_venue,
                short_venue=paar.short_venue,
                opened_at=als_utc(paar.opened_at),
                closed_at=als_utc(paar.closed_at),
                fills=self._store.fills_for(pair_id),
                funding_payments=zahlungen,
                notional_usd=paar.notional_usd or Decimal(0),
            )
        )
        self._store.save_result(pair_id, ergebnis)
        LOG.info(
            "paar_ergebnis",
            pair_id=pair_id,
            symbol=paar.symbol,
            netto=str(ergebnis.net_result),
            funding=str(ergebnis.funding_received),
            gebuehren=str(ergebnis.fees_paid),
        )

    async def close_pair(self, pair_id: int) -> ExecutionOutcome:
        """Schliesst beide Beine, mit derselben Absicherungslogik wie beim Oeffnen."""
        paar = self._store.get_pair(pair_id)
        if paar is None:
            raise KeyError(f"Paar {pair_id} nicht gefunden.")

        self._store.set_state(pair_id, PairState.CLOSING, message="Beide Beine schliessen")

        fehler: list[str] = []
        for bein in self._store.legs(pair_id):
            try:
                problem = await self._close_leg(pair_id, paar.symbol, bein)
            except (AdapterError, KeyError) as exc:
                problem = f"{bein.venue}: {exc}"
            if problem:
                fehler.append(problem)

        if fehler:
            # Ein halb geschlossenes Paar ist genauso ungesichert wie ein halb
            # geoeffnetes.
            self._store.set_state(
                pair_id, PairState.UNHEDGED, message=f"Schliessen unvollstaendig: {'; '.join(fehler)}"
            )
            return ExecutionOutcome(pair_id, PairState.UNHEDGED, "; ".join(fehler), {})

        self._store.set_state(pair_id, PairState.CLOSED, message="Paar geschlossen")
        self._finalise(pair_id)
        return ExecutionOutcome(pair_id, PairState.CLOSED, "beide Beine geschlossen", {})

    # --- Wiederanlauf -----------------------------------------------------

    async def recover(self) -> list[RecoveryReport]:
        """Gleicht haengengebliebene Paare gegen den tatsaechlichen Boersenzustand ab."""
        berichte: list[RecoveryReport] = []

        for paar in self._store.unfinished_pairs():
            erwartete = {paar.long_venue, paar.short_venue}
            tatsaechlich: dict[str, Decimal] = {}
            probleme: list[str] = []

            for venue in erwartete:
                try:
                    positionen = await self._adapter(venue).get_positions()
                except (AdapterError, KeyError, NotImplementedError) as exc:
                    probleme.append(f"{venue} nicht abfragbar: {exc}")
                    continue
                passend = [p for p in positionen if p.symbol == paar.symbol]
                tatsaechlich[venue] = sum((p.size for p in passend), Decimal(0))

            fehlend = [v for v in erwartete if tatsaechlich.get(v, Decimal(0)) <= 0]
            abweichung = bool(fehlend or probleme)

            detail = (
                f"Position fehlt auf: {', '.join(sorted(fehlend))}" if fehlend else "Beine vorhanden"
            )
            if probleme:
                detail = f"{detail}; {'; '.join(probleme)}"

            LOG.warning(
                "wiederanlauf_paar",
                pair_id=paar.id,
                symbol=paar.symbol,
                zustand=paar.status,
                abweichung=abweichung,
                detail=detail,
            )
            berichte.append(
                RecoveryReport(
                    pair_id=paar.id,
                    symbol=paar.symbol,
                    state=PairState(paar.status),
                    discrepancy=abweichung,
                    detail=detail,
                )
            )

        return berichte

    # --- Kill Switch ------------------------------------------------------

    async def panic(self) -> PanicOutcome:
        """Storniert alle offenen Orders und schliesst alle Positionen.

        Bewusst ohne Ruecksicht auf den Zustand einzelner Paare: im Notfall
        zaehlt, dass nichts mehr offen ist.
        """
        storniert = 0
        geschlossen = 0
        fehler: list[str] = []

        # Zweite Quelle fuer Symbole: was die Datenbank an offenen Paaren kennt.
        # Im Notfall darf der Kill Switch nicht daran scheitern, dass die
        # Positionsabfrage der Boerse gerade nicht antwortet.
        aus_datenbank: dict[str, set[str]] = {}
        for paar in self._store.open_pairs() + self._store.unfinished_pairs():
            aus_datenbank.setdefault(paar.long_venue, set()).add(paar.symbol)
            aus_datenbank.setdefault(paar.short_venue, set()).add(paar.symbol)

        for venue, adapter in self._adapters.items():
            # Im Kill Switch wird bewusst *jede* Ausnahme abgefangen, nicht nur
            # AdapterError. Das ist die letzte Verteidigungslinie: sie darf an
            # keinem Fehler einer einzelnen Boerse haengenbleiben, auch nicht an
            # einem, den niemand vorhergesehen hat. Ueberall sonst im Projekt
            # gilt weiterhin die enge Fehlerbehandlung.
            try:
                ids = await adapter.open_orders()
                for order_id in ids:
                    await adapter.cancel_order(order_id)
                    storniert += 1
            except TradingNotSupported:
                pass  # kein Zugang konfiguriert - nichts zu stornieren
            except Exception as exc:  # noqa: BLE001 - siehe Kommentar oben
                fehler.append(f"{venue}: Orders nicht stornierbar: {exc}")

            symbole: set[str] = set(aus_datenbank.get(venue, set()))
            try:
                for position in await adapter.get_positions():
                    symbole.add(position.symbol)
            except TradingNotSupported:
                pass  # kein Zugang - die Datenbank bleibt die einzige Quelle
            except Exception as exc:  # noqa: BLE001
                fehler.append(f"{venue}: Positionen nicht abrufbar: {exc}")

            for symbol in sorted(symbole):
                try:
                    await adapter.close_position(symbol)
                    geschlossen += 1
                except TradingNotSupported as exc:
                    fehler.append(f"{venue} {symbol}: kein Handelszugang ({exc})")
                except Exception as exc:  # noqa: BLE001
                    fehler.append(f"{venue} {symbol}: {exc}")

        for paar in self._store.open_pairs() + self._store.unfinished_pairs():
            self._store.set_state(paar.id, PairState.CLOSED, message="Kill Switch")

        if self.journal is not None:
            self.journal.record_event(
                kind="kill_switch",
                level="WARNING",
                message=f"Kill Switch: {storniert} Orders storniert, "
                f"{geschlossen} Positionen geschlossen",
                payload={"storniert": storniert, "beide Beine geschlossen": geschlossen, "fehler": fehler},
            )

        LOG.error("kill_switch", storniert=storniert, geschlossen=geschlossen, fehler=fehler)
        return PanicOutcome(cancelled_orders=storniert, closed_positions=geschlossen, errors=fehler)
