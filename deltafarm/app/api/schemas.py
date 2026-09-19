"""Antwortmodelle der API.

Decimal-Felder werden von pydantic im JSON-Modus als String ausgegeben. Das ist
Absicht: eine Rate als JSON-Zahl waere im Browser wieder ein double, und damit
waere die Decimal-Vorgabe an der Schnittstelle aufgegeben.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class VenueOut(BaseModel):
    name: str
    supports_trading: bool
    reachable: bool
    environment: str
    detail: Optional[str] = None


class HealthOut(BaseModel):
    status: str
    dry_run: bool
    environment: str
    version: str


class MarketOut(BaseModel):
    venue: str
    symbol: str
    native_symbol: str
    active: bool
    mark_price: Optional[Decimal] = None


class RateOut(BaseModel):
    rate_hourly: Decimal
    apr: Decimal
    native_rate: Decimal
    native_convention: str
    native_interval_hours: Optional[Decimal]
    next_funding_time: Optional[datetime] = None
    as_of: datetime


class BestPairOut(BaseModel):
    long_venue: str
    short_venue: str
    net_rate_hourly: Decimal
    net_apr: Decimal
    breakeven_hours: Optional[Decimal] = None
    cost_basis_known: bool
    price_deviation: Optional[Decimal] = None
    asset_match: Optional[bool] = None


class FundingRowOut(BaseModel):
    symbol: str
    rates: dict[str, RateOut]
    best: Optional[BestPairOut] = None


class FundingMatrixOut(BaseModel):
    as_of: datetime
    venues: list[str]
    rows: list[FundingRowOut]
    warnings: list[str] = []


class OpportunityOut(BaseModel):
    symbol: str
    long_venue: str
    short_venue: str
    long_rate_hourly: Decimal
    short_rate_hourly: Decimal
    net_rate_hourly: Decimal
    net_apr: Decimal
    breakeven_hours: Optional[Decimal] = None
    cost_basis_known: bool
    price_deviation: Optional[Decimal] = None
    asset_match: Optional[bool] = None
    as_of: datetime


# --- Phase 2 --------------------------------------------------------------


class BalanceOut(BaseModel):
    venue: str
    collateral: str
    equity: Decimal
    available: Decimal
    unrealised_pnl: Decimal
    initial_margin: Decimal
    margin_usage: Optional[Decimal] = None
    as_of: datetime


class HealthOutModel(BaseModel):
    liquidation_distance: Optional[Decimal] = None
    margin_usage: Optional[Decimal] = None
    level: str
    detail: Optional[str] = None


class LegOut(BaseModel):
    venue: str
    symbol: str
    side: str
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal
    notional: Decimal
    unrealised_pnl: Decimal
    liquidation_price: Optional[Decimal] = None
    funding_paid: Optional[Decimal] = None
    funding_received: Optional[Decimal] = None
    health: HealthOutModel


class DetectedPairOut(BaseModel):
    symbol: str
    legs: list[LegOut]
    net_delta_usd: Decimal
    net_delta_pct: Decimal
    combined_pnl: Decimal
    funding_received: Decimal
    hedged: bool
    level: str
    holding_hours: Optional[Decimal] = None


class JournalEntryOut(BaseModel):
    timestamp: datetime
    kind: str
    venue: Optional[str] = None
    symbol: Optional[str] = None
    amount: Optional[Decimal] = None
    confirmed: Optional[bool] = None
    message: str = ""


# --- Phase 3: Vorschau und Ausfuehrung ------------------------------------


class PreviewRequestIn(BaseModel):
    symbol: str
    notional_usd: Decimal
    long_venue: str
    short_venue: str
    max_slippage: Decimal = Decimal("0.002")
    long_leverage: Decimal = Decimal(1)
    short_leverage: Decimal = Decimal(1)
    first_venue: Optional[str] = None
    hedge_timeout_seconds: float = 10.0
    auto_rollback: bool = False


class CheckOut(BaseModel):
    key: str
    label: str
    status: str
    detail: str = ""


class SizingOut(BaseModel):
    size: Decimal
    lot_size: Decimal
    long_venue: str
    short_venue: str
    long_mark: Decimal
    short_mark: Decimal
    long_notional: Decimal
    short_notional: Decimal
    residual_delta_usd: Decimal
    residual_delta_pct: Decimal
    estimated_open_fees: Optional[Decimal] = None
    estimated_round_trip_fees: Optional[Decimal] = None


class PreviewOut(BaseModel):
    token: Optional[str] = None
    ok: bool
    sizing: Optional[SizingOut] = None
    checks: list[CheckOut] = []
    net_rate_hourly: Optional[Decimal] = None
    net_apr: Optional[Decimal] = None
    breakeven_hours: Optional[Decimal] = None
    blocking_reasons: list[str] = []
    error: Optional[str] = None
    # Wie lange die Vorschau gueltig bleibt.
    valid_for_seconds: float = 15.0


class OpenRequestIn(BaseModel):
    token: str


class ResolveRequestIn(BaseModel):
    action: str  # hedge | rollback


class ExecutionOut(BaseModel):
    pair_id: int
    state: str
    detail: str = ""
    dry_run: bool = True


class PairOut(BaseModel):
    id: int
    symbol: str
    long_venue: str
    short_venue: str
    status: str
    notional_usd: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    legs: list[dict] = []


class PanicOut(BaseModel):
    cancelled_orders: int
    closed_positions: int
    errors: list[str] = []
    dry_run: bool = True
