"""Deterministischer Testadapter.

Die Engine wird nie gegen echte Boersen entwickelt. Der MockAdapter liefert
feste Werte und laesst sich gezielt auf Fehler stellen - in Phase 3 kommen
Teilfuellung, Timeout und Ablehnung des zweiten Beins dazu.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

from app.adapters.base import FatalError, ReadOnlyAdapter, RetryableError
from app.core.funding import normalize_to_hourly, to_apr
from app.models.domain import FundingInfo, Market, OrderBook, OrderBookLevel, SymbolRules


class MockAdapter(ReadOnlyAdapter):
    """Feste Marktdaten, einstellbare Fehlerfaelle."""

    def __init__(
        self,
        name: str = "mock",
        *,
        rates: Optional[dict[str, Decimal]] = None,
        mark_prices: Optional[dict[str, Decimal]] = None,
        interval_hours: Decimal = Decimal(1),
        tick_size: Decimal = Decimal("0.1"),
        lot_size: Decimal = Decimal("0.001"),
        min_notional: Decimal = Decimal(10),
        taker_fee: Optional[Decimal] = Decimal("0.0004"),
        maker_fee: Optional[Decimal] = Decimal("0.0002"),
        max_leverage: Decimal = Decimal(20),
        funding_spacing_hours: Decimal = Decimal(1),
        fail_with: Optional[Exception] = None,
        supports_trading: bool = False,
    ) -> None:
        self.name = name
        self.supports_trading = supports_trading
        self.declared_interval_hours = interval_hours
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
        hourly = normalize_to_hourly(native, self.declared_interval_hours)
        return FundingInfo(
            venue=self.name,
            symbol=symbol,
            native_rate=native,
            native_interval_hours=self.declared_interval_hours,
            rate_hourly=hourly,
            apr=to_apr(hourly),
            as_of=datetime(2026, 9, 18, tzinfo=timezone.utc),
        )

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        self._maybe_fail(f"get_orderbook:{symbol}")
        mark = self._marks.get(symbol, Decimal(60000))
        return OrderBook(
            venue=self.name,
            symbol=symbol,
            bids=tuple(
                OrderBookLevel(price=mark - Decimal(i + 1), size=Decimal(1)) for i in range(depth)
            ),
            asks=tuple(
                OrderBookLevel(price=mark + Decimal(i + 1), size=Decimal(1)) for i in range(depth)
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


def failing_adapter(name: str, *, retryable: bool = True) -> MockAdapter:
    fehler = (
        RetryableError(f"{name}: simulierter Netzfehler", venue=name)
        if retryable
        else FatalError(f"{name}: simulierter fachlicher Fehler", venue=name)
    )
    return MockAdapter(name, fail_with=fehler)
