"""SQLite-Schema.

Wichtig: Decimal-Werte werden als Text gespeichert. SQLAlchemys Numeric geht
auf SQLite durch float - genau das soll dieses Projekt vermeiden. Der
TypeDecorator unten haelt die Werte exakt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship



class DecimalText(TypeDecorator):
    """Decimal verlustfrei als Text ablegen."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: D102
        if value is None:
            return None
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return str(value)

    def process_result_value(self, value, dialect):  # noqa: D102
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    environment: Mapped[str] = mapped_column(String(16))
    supports_trading: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PairSource(StrEnum):
    """Woher ein Paar stammt."""

    # Ueber die Vorschau eroeffnet.
    ENGINE = "ENGINE"
    # Aus offenen Boersenpositionen erkannt und uebernommen, damit auch von
    # Hand eroeffnete Paare Funding-Zuordnung, Schliessen-Knopf und
    # Ergebniszeile bekommen.
    ADOPTED = "ADOPTED"


class Pair(Base):
    """Ein delta-neutrales Paar."""

    __tablename__ = "pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    long_venue: Mapped[str] = mapped_column(String(32))
    short_venue: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="PLANNED")
    source: Mapped[str] = mapped_column(String(16), default=PairSource.ENGINE.value)
    notional_usd: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # Endergebnis, beim Schliessen einmal gerechnet und festgehalten
    # (Auftrag 6.5). Als Momentaufnahme, damit die Journalzeile auch dann
    # stimmt, wenn sich spaeter etwas nachtraeglich aendert.
    funding_received: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    fees_paid: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    price_pnl: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    net_result: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    realized_apr: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    holding_hours: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)

    legs: Mapped[list["Leg"]] = relationship(back_populates="pair", cascade="all, delete-orphan")


class Leg(Base):
    __tablename__ = "legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[int] = mapped_column(ForeignKey("pairs.id"))
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    target_size: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    filled_size: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    avg_price: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PLANNED")

    pair: Mapped[Pair] = relationship(back_populates="legs")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[Optional[int]] = mapped_column(ForeignKey("legs.id"), nullable=True)
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    # Selbst erzeugte ID. Ein Retry darf nie zu einer zweiten Position fuehren.
    client_order_id: Mapped[str] = mapped_column(String(64))
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16))
    size: Mapped[Decimal] = mapped_column(DecimalText(64))
    price: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="NEW")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (UniqueConstraint("venue", "client_order_id", name="uq_orders_venue_client"),)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    exchange_fill_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size: Mapped[Decimal] = mapped_column(DecimalText(64))
    price: Mapped[Decimal] = mapped_column(DecimalText(64))
    fee: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)


class FundingPaymentRow(Base):
    __tablename__ = "funding_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pairs.id"), nullable=True)
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(DecimalText(64))
    rate: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    position_size: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    # False = aus Rate und Groesse gerechnet, nicht von der Boerse bestaetigt.
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        # Verhindert Doppelzaehlung beim wiederholten Nachladen.
        UniqueConstraint("venue", "external_id", name="uq_funding_venue_external"),
        Index("ix_funding_time", "timestamp"),
    )


class Event(Base):
    """Jede Zustandsaenderung. Grundlage der spaeteren Fehlersuche."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    kind: Mapped[str] = mapped_column(String(48))
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    venue: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    pair_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pairs.id"), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    __table_args__ = (Index("ix_events_time", "timestamp"),)


class Snapshot(Base):
    """Regelmaessige Momentaufnahme von Kontostand und Positionen."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    venue: Mapped[str] = mapped_column(String(32))
    equity: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    available: Mapped[Optional[Decimal]] = mapped_column(DecimalText(64), nullable=True)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    __table_args__ = (Index("ix_snapshots_time", "timestamp"),)
