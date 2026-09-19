"""Journal: lueckenloses Protokoll aller Aktionen und Funding-Zahlungen.

Zwei Regeln:
  * Betraege werden nie durch float geschleust - auch nicht auf dem Weg in die
    Datenbank oder in den CSV-Export.
  * Eine gerechnete Funding-Zahlung wird als solche gekennzeichnet und sieht
    nie wie eine von der Boerse bestaetigte aus.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.domain import Balance, FundingPayment
from app.storage.db import session_scope
from app.storage.models import Event, FundingPaymentRow, Snapshot

LOG = structlog.get_logger(__name__)

CSV_HEADER = [
    "zeitpunkt",
    "art",
    "boerse",
    "symbol",
    "betrag",
    "rate",
    "positionsgroesse",
    "bestaetigt",
    "beschreibung",
    "details",
]


def ausgeschrieben(wert: Optional[Decimal]) -> str:
    """Decimal ohne wissenschaftliche Notation.

    str(Decimal("0.000000000000000001")) ergibt "1E-18" - wertgleich, aber in
    einer Tabellenkalkulation schlecht lesbar und je nach Gebietsschema als
    Text interpretiert. format(..., "f") schreibt die Zahl aus, ohne zu runden.
    """
    return "" if wert is None else format(wert, "f")


def _json_default(wert: Any) -> str:
    # Decimal als String, damit auch im Protokoll nichts gerundet wird.
    if isinstance(wert, Decimal):
        return str(wert)
    if isinstance(wert, datetime):
        return wert.isoformat()
    return str(wert)


class Journal:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    # --- Schreiben --------------------------------------------------------

    def record_event(
        self,
        *,
        kind: str,
        message: str = "",
        level: str = "INFO",
        venue: Optional[str] = None,
        symbol: Optional[str] = None,
        pair_id: Optional[int] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> int:
        with session_scope(self._sessions) as s:
            eintrag = Event(
                timestamp=datetime.now(timezone.utc),
                kind=kind,
                level=level,
                venue=venue,
                symbol=symbol,
                pair_id=pair_id,
                message=message,
                payload=json.dumps(payload, default=_json_default) if payload else None,
            )
            s.add(eintrag)
            s.flush()
            return eintrag.id

    def record_funding_payments(self, payments: Iterable[FundingPayment]) -> int:
        """Speichert Zahlungen und gibt zurueck, wie viele neu waren.

        Die Zahlungen werden periodisch nachgeladen; dieselbe Zahlung darf
        dabei nicht mehrfach im Journal landen, sonst ist die Summe falsch.
        Erkannt wird das an (Boerse, externe ID).
        """
        neu = 0
        with session_scope(self._sessions) as s:
            for z in payments:
                if z.external_id is not None:
                    vorhanden = s.execute(
                        select(FundingPaymentRow.id).where(
                            FundingPaymentRow.venue == z.venue,
                            FundingPaymentRow.external_id == z.external_id,
                        )
                    ).first()
                    if vorhanden is not None:
                        continue
                s.add(
                    FundingPaymentRow(
                        venue=z.venue,
                        symbol=z.symbol,
                        amount=z.amount,
                        rate=z.rate,
                        position_size=z.position_size,
                        timestamp=z.timestamp,
                        confirmed=z.confirmed,
                        external_id=z.external_id,
                    )
                )
                neu += 1
        return neu

    def record_snapshot(self, balance: Balance, *, payload: Optional[dict[str, Any]] = None) -> None:
        with session_scope(self._sessions) as s:
            s.add(
                Snapshot(
                    timestamp=balance.as_of,
                    venue=balance.venue,
                    equity=balance.equity,
                    available=balance.available,
                    payload=json.dumps(payload, default=_json_default) if payload else None,
                )
            )

    # --- Lesen ------------------------------------------------------------

    def funding_payments(
        self,
        *,
        since: Optional[datetime] = None,
        venue: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> list[FundingPaymentRow]:
        with session_scope(self._sessions) as s:
            abfrage = select(FundingPaymentRow).order_by(FundingPaymentRow.timestamp)
            if since is not None:
                abfrage = abfrage.where(FundingPaymentRow.timestamp >= since.replace(tzinfo=None))
            if venue is not None:
                abfrage = abfrage.where(FundingPaymentRow.venue == venue)
            if symbol is not None:
                abfrage = abfrage.where(FundingPaymentRow.symbol == symbol)
            return list(s.execute(abfrage).scalars())

    def funding_total(
        self,
        *,
        venue: Optional[str] = None,
        symbol: Optional[str] = None,
        since: Optional[datetime] = None,
        confirmed_only: bool = False,
    ) -> Decimal:
        """Summe der Funding-Zahlungen.

        `confirmed_only` trennt bestaetigte von gerechneten Betraegen - fuer
        eine ehrliche Auswertung der tatsaechlichen Ertraege.
        """
        zahlungen = self.funding_payments(since=since, venue=venue, symbol=symbol)
        return sum(
            (z.amount for z in zahlungen if z.confirmed or not confirmed_only),
            Decimal(0),
        )

    def events(
        self,
        *,
        since: Optional[datetime] = None,
        kinds: Optional[Sequence[str]] = None,
        venue: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Event]:
        with session_scope(self._sessions) as s:
            abfrage = select(Event).order_by(Event.timestamp)
            if since is not None:
                abfrage = abfrage.where(Event.timestamp >= since.replace(tzinfo=None))
            if kinds:
                abfrage = abfrage.where(Event.kind.in_(list(kinds)))
            if venue is not None:
                abfrage = abfrage.where(Event.venue == venue)
            if limit is not None:
                abfrage = abfrage.limit(limit)
            return list(s.execute(abfrage).scalars())

    def snapshots(self, *, since: Optional[datetime] = None) -> list[Snapshot]:
        with session_scope(self._sessions) as s:
            abfrage = select(Snapshot).order_by(Snapshot.timestamp)
            if since is not None:
                abfrage = abfrage.where(Snapshot.timestamp >= since.replace(tzinfo=None))
            return list(s.execute(abfrage).scalars())

    # --- Export -----------------------------------------------------------

    def to_csv(self, *, since: Optional[datetime] = None) -> str:
        """Chronologischer Export aus Ereignissen und Funding-Zahlungen."""
        zeilen: list[tuple[datetime, list[str]]] = []

        for e in self.events(since=since):
            zeilen.append(
                (
                    e.timestamp,
                    [
                        e.timestamp.isoformat(),
                        "ereignis",
                        e.venue or "",
                        e.symbol or "",
                        "",
                        "",
                        "",
                        "",
                        e.message,
                        e.payload or "",
                    ],
                )
            )

        for z in self.funding_payments(since=since):
            zeilen.append(
                (
                    z.timestamp,
                    [
                        z.timestamp.isoformat(),
                        "funding",
                        z.venue,
                        z.symbol,
                        # Ausgeschrieben statt wissenschaftlich, aber ungerundet.
                        ausgeschrieben(z.amount),
                        ausgeschrieben(z.rate),
                        ausgeschrieben(z.position_size),
                        "ja" if z.confirmed else "gerechnet",
                        "Funding-Zahlung",
                        z.external_id or "",
                    ],
                )
            )

        zeilen.sort(key=lambda p: p[0])

        puffer = io.StringIO()
        schreiber = csv.writer(puffer, lineterminator="\n")
        schreiber.writerow(CSV_HEADER)
        for _, zeile in zeilen:
            schreiber.writerow(zeile)
        return puffer.getvalue()
