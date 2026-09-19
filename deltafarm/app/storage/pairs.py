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
from app.storage.models import Event, Fill, Leg, Order, Pair

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
