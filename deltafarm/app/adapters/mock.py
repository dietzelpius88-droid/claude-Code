"""Deterministischer Testadapter.

Die Engine wird nie gegen echte Boersen entwickelt. Der MockAdapter liefert
feste Werte und laesst sich gezielt auf Fehler stellen - in Phase 3 kommen
Teilfuellung, Timeout und Ablehnung des zweiten Beins dazu.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

import asyncio

from app.adapters.base import FatalError, ReadOnlyAdapter, RetryableError
from app.core.funding import RateConvention, to_apr, to_hourly_fraction
from app.models.domain import (
    FundingInfo,
    Position,
    Market,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Side,
    SymbolRules,
)


class MockAdapter(ReadOnlyAdapter):
    """Feste Marktdaten, einstellbare Fehlerfaelle."""

    def __init__(
        self,
        name: str = "mock",
        *,
        rates: Optional[dict[str, Decimal]] = None,
        mark_prices: Optional[dict[str, Decimal]] = None,
        interval_hours: Decimal = Decimal(1),
        convention: RateConvention = RateConvention.INTERVAL_FRACTION,
        tick_size: Decimal = Decimal("0.1"),
        lot_size: Decimal = Decimal("0.001"),
        min_notional: Decimal = Decimal(10),
        taker_fee: Optional[Decimal] = Decimal("0.0004"),
        maker_fee: Optional[Decimal] = Decimal("0.0002"),
        max_leverage: Decimal = Decimal(20),
        funding_spacing_hours: Decimal = Decimal(1),
        fail_with: Optional[Exception] = None,
        supports_trading: bool = False,
        # --- Handel (Phase 3) ---
        fill_ratio: Decimal = Decimal(1),
        reject_orders: bool = False,
        order_delay_seconds: float = 0.0,
        reject_after: Optional[int] = None,
        positions: Optional[list] = None,
        book_step: Decimal = Decimal("0.0001"),
        book_depth_per_level: Optional[Decimal] = None,
    ) -> None:
        self.name = name
        self.supports_trading = supports_trading
        self.declared_interval_hours = interval_hours
        self.rate_convention = convention
        self._rates = rates or {"BTC-PERP": Decimal("0.00001")}
        self._marks = mark_prices or {"BTC-PERP": Decimal(60000)}
        self._tick = tick_size
        self._lot = lot_size
        self._min_notional = min_notional
        self._taker = taker_fee
        self._maker = maker_fee
        self._max_leverage = max_leverage
        self._spacing = funding_spacing_hours
        self._fail_with = fail_with
        self.calls: list[str] = []
        # Handel
        self._fill_ratio = fill_ratio
        self._reject_orders = reject_orders
        self._order_delay = order_delay_seconds
        self._reject_after = reject_after
        self.placed_orders: list[OrderRequest] = []
        self.cancelled_orders: list[str] = []
        self.closed_positions: list[str] = []
        self._positions = list(positions or [])
        self._book_step = book_step
        # Tiefe je Stufe: ohne Angabe so gewaehlt, dass uebliche Groessen
        # durchlaufen, ohne das Buch zu leeren.
        self._book_depth_per_level = book_depth_per_level or Decimal(100)

    def _maybe_fail(self, was: str) -> None:
        self.calls.append(was)
        if self._fail_with is not None:
            raise self._fail_with

    async def list_markets(self) -> list[Market]:
        self._maybe_fail("list_markets")
        return [
            Market(
                venue=self.name,
                symbol=s,
                native_symbol=s,
                active=True,
                mark_price=self._marks.get(s),
            )
            for s in self._rates
        ]

    async def get_funding(self, symbol: str) -> FundingInfo:
        self._maybe_fail(f"get_funding:{symbol}")
        if symbol not in self._rates:
            raise FatalError(f"{self.name}: {symbol} unbekannt.", venue=self.name)
        native = self._rates[symbol]
        hourly = to_hourly_fraction(
            native,
            convention=self.rate_convention,
            interval_hours=(
                self.declared_interval_hours
                if self.rate_convention is RateConvention.INTERVAL_FRACTION
                else None
            ),
        )
        return FundingInfo(
            venue=self.name,
            symbol=symbol,
            native_rate=native,
            native_convention=self.rate_convention.value,
            native_interval_hours=self.declared_interval_hours,
            rate_hourly=hourly,
            apr=to_apr(hourly),
            as_of=datetime(2026, 9, 18, tzinfo=timezone.utc),
        )

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        self._maybe_fail(f"get_orderbook:{symbol}")
        mark = self._marks.get(symbol, Decimal(60000))
        # Der Preisschritt ist relativ zum Mark-Preis (ein Basispunkt je
        # Stufe). Ein fester Schritt von einem Dollar waere bei einem
        # 148-Dollar-Asset absurd steil und bei Bitcoin unmerklich flach.
        schritt = mark * self._book_step
        return OrderBook(
            venue=self.name,
            symbol=symbol,
            bids=tuple(
                OrderBookLevel(price=mark - schritt * (i + 1), size=self._book_depth_per_level)
                for i in range(depth)
            ),
            asks=tuple(
                OrderBookLevel(price=mark + schritt * (i + 1), size=self._book_depth_per_level)
                for i in range(depth)
            ),
            as_of=datetime(2026, 9, 18, tzinfo=timezone.utc),
        )

    async def get_mark_price(self, symbol: str) -> Decimal:
        self._maybe_fail(f"get_mark_price:{symbol}")
        return self._marks.get(symbol, Decimal(60000))

    async def get_symbol_rules(self, symbol: str) -> SymbolRules:
        self._maybe_fail(f"get_symbol_rules:{symbol}")
        return SymbolRules(
            venue=self.name,
            symbol=symbol,
            tick_size=self._tick,
            lot_size=self._lot,
            min_notional=self._min_notional,
            max_leverage=self._max_leverage,
            maker_fee=self._maker,
            taker_fee=self._taker,
        )

    async def funding_timestamps(self, symbol: str, lookback_hours: int = 48) -> Sequence[datetime]:
        self._maybe_fail(f"funding_timestamps:{symbol}")
        start = datetime(2026, 9, 18, tzinfo=timezone.utc)
        schritte = int(lookback_hours / float(self._spacing)) or 1
        return [start + timedelta(hours=float(self._spacing) * i) for i in range(schritte)]

    async def aclose(self) -> None:
        return None


    # --- Handel (Phase 3) -------------------------------------------------

    async def place_order(self, req: OrderRequest) -> OrderResult:
        """Simuliert eine Order. Verhalten ueber den Konstruktor einstellbar."""
        self.calls.append(f"place_order:{req.venue}:{req.side.value}:{req.size}")

        if self._order_delay:
            await asyncio.sleep(self._order_delay)

        self.placed_orders.append(req)

        abgelehnt = self._reject_orders or (
            self._reject_after is not None and len(self.placed_orders) > self._reject_after
        )
        if abgelehnt:
            return OrderResult(
                venue=self.name,
                symbol=req.symbol,
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                dry_run=True,
                detail="vom MockAdapter abgelehnt",
                as_of=datetime(2026, 9, 19, tzinfo=timezone.utc),
            )

        gefuellt = req.size * self._fill_ratio
        preis = self._marks.get(req.symbol, Decimal(60000))
        status = OrderStatus.FILLED if gefuellt >= req.size else OrderStatus.PARTIALLY_FILLED

        return OrderResult(
            venue=self.name,
            symbol=req.symbol,
            client_order_id=req.client_order_id,
            exchange_order_id=f"mock-{len(self.placed_orders)}",
            status=status,
            filled_size=gefuellt,
            average_price=preis,
            fee=gefuellt * preis * (self._taker or Decimal(0)),
            dry_run=True,
            as_of=datetime(2026, 9, 19, tzinfo=timezone.utc),
        )

    async def cancel_order(self, order_id: str) -> None:
        self.calls.append(f"cancel_order:{order_id}")
        self.cancelled_orders.append(order_id)

    async def close_position(self, symbol: str) -> OrderResult:
        self.calls.append(f"close_position:{symbol}")
        self.closed_positions.append(symbol)
        preis = self._marks.get(symbol, Decimal(60000))
        return OrderResult(
            venue=self.name,
            symbol=symbol,
            client_order_id=f"close-{symbol}",
            exchange_order_id=f"mock-close-{len(self.closed_positions)}",
            status=OrderStatus.FILLED,
            filled_size=Decimal(1),
            average_price=preis,
            dry_run=True,
            as_of=datetime(2026, 9, 19, tzinfo=timezone.utc),
        )

    async def get_positions(self) -> list[Position]:
        """Offene Positionen. Leer, solange keine gesetzt wurden."""
        self.calls.append("get_positions")
        return list(self._positions)

    async def open_orders(self, symbol: Optional[str] = None) -> list[str]:
        """Offene Order-IDs. Der MockAdapter meldet keine, sofern nicht gesetzt."""
        return getattr(self, "_open_orders", [])


def failing_adapter(name: str, *, retryable: bool = True) -> MockAdapter:
    fehler = (
        RetryableError(f"{name}: simulierter Netzfehler", venue=name)
        if retryable
        else FatalError(f"{name}: simulierter fachlicher Fehler", venue=name)
    )
    return MockAdapter(name, fail_with=fehler)
