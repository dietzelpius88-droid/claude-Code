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
    as_of: datetime
