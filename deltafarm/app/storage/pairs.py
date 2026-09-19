"""Zustandsspeicher der Paare.

Jeder Zustandsuebergang wird **vor** dem Netzwerkaufruf geschrieben. Stuerzt
die Anwendung mitten in der Ausfuehrung ab, laesst sich aus der Datenbank
rekonstruieren, was gerade versucht wurde - und der Wiederanlauf kann gegen
den tatsaechlichen Boersenzustand abgleichen.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.domain import OrderRequest, OrderResult, PairState, Side
from app.storage.db import session_scope
from app.storage.models import Event, Fill, FundingPaymentRow, Leg, Order, Pair, PairSource

LOG = structlog.get_logger(__name__)


class PairStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    # --- Anlegen ----------------------------------------------------------

    def create_pair(
        self,
        *,
        symbol: str,
        long_venue: str,
        short_venue: str,
        notional_usd: Decimal,
        size: Decimal,
        first_venue: str,
    ) -> int:
        """Legt Paar und beide Beine an, bevor irgendetwas gesendet wird."""
        with session_scope(self._sessions) as s:
            paar = Pair(
                symbol=symbol,
                long_venue=long_venue,
                short_venue=short_venue,
                status=PairState.PLANNED.value,
                notional_usd=notional_usd,
            )
            s.add(paar)
            s.flush()

            for venue, seite in ((long_venue, Side.LONG), (short_venue, Side.SHORT)):
                s.add(
                    Leg(
                        pair_id=paar.id,
                        venue=venue,
                        symbol=symbol,
                        side=seite.value,
                        target_size=size,
                        filled_size=Decimal(0),
                        status=PairState.PLANNED.value,
                    )
                )

            s.add(
                Event(
                    kind="paar_geplant",
                    message=f"{symbol}: long {long_venue}, short {short_venue}, "
                    f"{notional_usd} USD, zuerst {first_venue}",
                    symbol=symbol,
                    pair_id=paar.id,
                    payload=json.dumps(
                        {
                            "size": str(size),
                            "notional_usd": str(notional_usd),
                            "first_venue": first_venue,
                        }
                    ),
                )
            )
            return paar.id

    def adopt_pair(
        self,
        *,
        symbol: str,
        long_venue: str,
        short_venue: str,
        notional_usd: Decimal,
        legs: dict[str, Decimal],
        opened_at: Optional[datetime] = None,
    ) -> int:
        """Uebernimmt ein Paar, das an den Boersen bereits offen ist.

        Damit bekommen von Hand eroeffnete Paare denselben Codepfad wie
        geklickte: Funding-Zuordnung, Schliessen-Knopf und Ergebniszeile.
        Die Fuellmengen kommen aus den Boersenpositionen, nicht aus Orders -
        die kennen wir nicht. Dasselbe gilt fuer den Eroeffnungszeitpunkt:
        nennt ihn die Boerse nicht, bleibt er **leer** statt auf "jetzt"
        gesetzt zu werden. Sonst fiele alles Funding von vor der Uebernahme
        aus der Zuordnung - obwohl es zu dieser Position gehoert. Haltedauer
        und realisierte APR bleiben dann offen, was der Wahrheit entspricht.
        """
        with session_scope(self._sessions) as s:
            paar = Pair(
                symbol=symbol,
                long_venue=long_venue,
                short_venue=short_venue,
                status=PairState.OPEN.value,
                source=PairSource.ADOPTED.value,
                notional_usd=notional_usd,
                opened_at=opened_at,
            )
            s.add(paar)
            s.flush()

            for venue, seite in ((long_venue, Side.LONG), (short_venue, Side.SHORT)):
                groesse = legs.get(venue, Decimal(0))
                s.add(
                    Leg(
                        pair_id=paar.id,
                        venue=venue,
                        symbol=symbol,
                        side=seite.value,
                        target_size=groesse,
                        filled_size=groesse,
                        status=PairState.OPEN.value,
                    )
                )

            s.add(
                Event(
                    kind="paar_uebernommen",
                    symbol=symbol,
                    pair_id=paar.id,
                    message=f"{symbol}: offene Positionen auf {long_venue} und {short_venue} "
                    "als Paar uebernommen (nicht ueber die Vorschau eroeffnet)",
                    payload=json.dumps({k: str(v) for k, v in legs.items()}),
                )
            )
            return paar.id

    def open_pair_for(self, symbol: str) -> Optional[Pair]:
        """Das offene Paar zu einem Symbol, falls es eines gibt."""
        with session_scope(self._sessions) as s:
            return s.execute(
                select(Pair).where(
                    Pair.symbol == symbol,
                    Pair.status.in_([PairState.OPEN.value, PairState.UNHEDGED.value]),
                )
            ).scalars().first()

    def attribute_funding(self, pair_id: int) -> int:
        """Ordnet noch nicht zugeordnete Funding-Zahlungen diesem Paar zu.

        Zugeordnet wird nach Boerse, Symbol und Zeitfenster. Ohne das bleibt
        funding_payments.pair_id leer - und die Frage, was ein Paar gebracht
        hat, unbeantwortbar.
        """
        with session_scope(self._sessions) as s:
            paar = s.get(Pair, pair_id)
            if paar is None:
                raise KeyError(f"Paar {pair_id} nicht gefunden.")

            bedingungen = [
                FundingPaymentRow.pair_id.is_(None),
                FundingPaymentRow.symbol == paar.symbol,
                FundingPaymentRow.venue.in_([paar.long_venue, paar.short_venue]),
            ]
            if paar.opened_at is not None:
                bedingungen.append(FundingPaymentRow.timestamp >= paar.opened_at)
            if paar.closed_at is not None:
                bedingungen.append(FundingPaymentRow.timestamp <= paar.closed_at)

            zeilen = list(s.execute(select(FundingPaymentRow).where(*bedingungen)).scalars())
            for z in zeilen:
                z.pair_id = pair_id
            return len(zeilen)

    def funding_for(self, pair_id: int) -> list[FundingPaymentRow]:
        with session_scope(self._sessions) as s:
            return list(
                s.execute(
                    select(FundingPaymentRow)
                    .where(FundingPaymentRow.pair_id == pair_id)
                    .order_by(FundingPaymentRow.timestamp)
                ).scalars()
            )

    def fills_for(self, pair_id: int) -> list[dict]:
        """Fills des Paares, aufbereitet fuer die Ergebnisrechnung."""
        beine = {b.id: b for b in self.legs(pair_id)}
        with session_scope(self._sessions) as s:
            orders = list(
                s.execute(select(Order).where(Order.leg_id.in_(beine))).scalars()
            )
            order_nach_id = {o.id: o for o in orders}
            fills = list(
                s.execute(select(Fill).where(Fill.order_id.in_(order_nach_id))).scalars()
            )

        ergebnis: list[dict] = []
        for f in fills:
            order = order_nach_id.get(f.order_id)
            if order is None:
                continue
            bein = beine.get(order.leg_id)
            if bein is None:
                continue
            # Eine Order, deren Seite der des Beins entgegensteht, schliesst es.
            schliessend = order.side != bein.side
            ergebnis.append(
                {
                    "venue": f.venue,
                    "side": Side(bein.side),
                    "size": f.size,
                    "price": f.price,
                    "fee": f.fee,
                    "closing": schliessend,
                }
            )
        return ergebnis

    def save_result(self, pair_id: int, ergebnis) -> None:
        """Haelt das Endergebnis am Paar fest (Auftrag 6.5)."""
        with session_scope(self._sessions) as s:
            paar = s.get(Pair, pair_id)
            if paar is None:
                raise KeyError(f"Paar {pair_id} nicht gefunden.")
            paar.funding_received = ergebnis.funding_received
            paar.fees_paid = ergebnis.fees_paid
            paar.price_pnl = ergebnis.price_pnl
            paar.net_result = ergebnis.net_result
            paar.realized_apr = ergebnis.realized_apr
            paar.holding_hours = ergebnis.holding_hours

            s.add(
                Event(
                    kind="paar_ergebnis",
                    symbol=paar.symbol,
                    pair_id=pair_id,
                    message=(
                        f"{paar.symbol} geschlossen: Funding {ergebnis.funding_received}, "
                        f"Gebuehren {ergebnis.fees_paid}, Preis-PnL {ergebnis.price_pnl}, "
                        f"Netto {ergebnis.net_result}, gehalten "
                        f"{ergebnis.holding_hours.quantize(Decimal('0.1'))} h"
                        + (
                            f", realisierte APR {ergebnis.realized_apr:.2%}"
                            if ergebnis.realized_apr is not None
                            else ""
                        )
                        + (" (Gebuehren unvollstaendig)" if ergebnis.fees_incomplete else "")
                    ),
                    payload=json.dumps(
                        {
                            "funding_received": str(ergebnis.funding_received),
                            "fees_paid": str(ergebnis.fees_paid),
                            "price_pnl": str(ergebnis.price_pnl),
                            "net_result": str(ergebnis.net_result),
                            "holding_hours": str(ergebnis.holding_hours),
                            "realized_apr": (
                                str(ergebnis.realized_apr)
                                if ergebnis.realized_apr is not None
                                else None
                            ),
                            "fees_incomplete": ergebnis.fees_incomplete,
                        }
                    ),
                )
            )

    # --- Zustand ----------------------------------------------------------

    def set_state(
        self,
        pair_id: int,
        state: PairState,
        *,
        message: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        """Schreibt den Zustand und protokolliert den Uebergang."""
        with session_scope(self._sessions) as s:
            paar = s.get(Pair, pair_id)
            if paar is None:
                raise KeyError(f"Paar {pair_id} nicht gefunden.")
            vorher = paar.status
            paar.status = state.value

            if state is PairState.OPEN and paar.opened_at is None:
                paar.opened_at = datetime.now(timezone.utc)
            if state is PairState.CLOSED and paar.closed_at is None:
                paar.closed_at = datetime.now(timezone.utc)

            s.add(
                Event(
                    kind="zustand",
                    level="WARNING" if state is PairState.UNHEDGED else "INFO",
                    symbol=paar.symbol,
                    pair_id=pair_id,
                    message=message or f"{vorher} -> {state.value}",
                    payload=json.dumps({"von": vorher, "nach": state.value, **(payload or {})},
                                       default=str),
                )
            )

    def get_state(self, pair_id: int) -> PairState:
        with session_scope(self._sessions) as s:
            paar = s.get(Pair, pair_id)
            if paar is None:
                raise KeyError(f"Paar {pair_id} nicht gefunden.")
            return PairState(paar.status)

    def get_pair(self, pair_id: int) -> Optional[Pair]:
        with session_scope(self._sessions) as s:
            return s.get(Pair, pair_id)

    def legs(self, pair_id: int) -> list[Leg]:
        with session_scope(self._sessions) as s:
            return list(
                s.execute(select(Leg).where(Leg.pair_id == pair_id).order_by(Leg.id)).scalars()
            )

    def leg_for(self, pair_id: int, venue: str) -> Optional[Leg]:
        return next((b for b in self.legs(pair_id) if b.venue == venue), None)

    def unfinished_pairs(self) -> list[Pair]:
        """Paare, die beim letzten Lauf nicht sauber zu Ende gefuehrt wurden.

        Grundlage des Wiederanlaufs: diese Zustaende bedeuten, dass mitten in
        einer Aktion abgebrochen wurde.
        """
        offen = [
            PairState.OPENING_FIRST.value,
            PairState.FIRST_FILLED.value,
            PairState.OPENING_SECOND.value,
            PairState.UNHEDGED.value,
            PairState.ROLLING_BACK.value,
            PairState.CLOSING.value,
        ]
        with session_scope(self._sessions) as s:
            return list(s.execute(select(Pair).where(Pair.status.in_(offen))).scalars())

    def open_pairs(self) -> list[Pair]:
        with session_scope(self._sessions) as s:
            return list(
                s.execute(select(Pair).where(Pair.status == PairState.OPEN.value)).scalars()
            )

    def all_pairs(self, limit: int = 200) -> list[Pair]:
        with session_scope(self._sessions) as s:
            return list(
                s.execute(select(Pair).order_by(Pair.id.desc()).limit(limit)).scalars()
            )

    # --- Orders und Fills -------------------------------------------------

    def record_order(self, pair_id: int, request: OrderRequest, *, dry_run: bool) -> int:
        """Schreibt die Order, bevor sie gesendet wird."""
        with session_scope(self._sessions) as s:
            bein = next(
                (
                    b
                    for b in s.execute(
                        select(Leg).where(Leg.pair_id == pair_id, Leg.venue == request.venue)
                    ).scalars()
                ),
                None,
            )
            order = Order(
                leg_id=bein.id if bein else None,
                venue=request.venue,
                symbol=request.symbol,
                client_order_id=request.client_order_id,
                side=request.side.value,
                order_type=request.order_type.value,
                size=request.size,
                price=request.price,
                status="NEW",
                dry_run=dry_run,
            )
            s.add(order)
            s.flush()
            return order.id

    def record_result(self, order_id: int, result: OrderResult) -> None:
        """Haelt das Ergebnis fest, inklusive Teilfuellung."""
        with session_scope(self._sessions) as s:
            order = s.get(Order, order_id)
            if order is None:
                raise KeyError(f"Order {order_id} nicht gefunden.")
            order.status = result.status.value
            order.exchange_order_id = result.exchange_order_id

            if result.filled_size > 0 and result.average_price is not None:
                s.add(
                    Fill(
                        order_id=order_id,
                        venue=result.venue,
                        symbol=result.symbol,
                        exchange_fill_id=result.exchange_order_id,
                        size=result.filled_size,
                        price=result.average_price,
                        fee=result.fee,
                        timestamp=result.as_of,
                    )
                )
                if order.leg_id is not None:
                    bein = s.get(Leg, order.leg_id)
                    if bein is not None:
                        bisher = bein.filled_size or Decimal(0)
                        bein.filled_size = bisher + result.filled_size
                        bein.avg_price = result.average_price
                        bein.status = result.status.value

    def orders(self, pair_id: int) -> list[Order]:
        beine = {b.id for b in self.legs(pair_id)}
        with session_scope(self._sessions) as s:
            return list(
                s.execute(select(Order).where(Order.leg_id.in_(beine)).order_by(Order.id)).scalars()
            )
