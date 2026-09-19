"""Fachliche Modelle. Alle Geldbetraege, Groessen und Preise sind Decimal."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class _Model(BaseModel):
    # Modelle sind unveraenderlich: eine FundingInfo, die durch die Engine wandert,
    # darf unterwegs nicht stillschweigend veraendert werden.
    model_config = ConfigDict(frozen=True)


class SymbolRules(_Model):
    """Regeln der Boerse fuer korrektes Sizing.

    tick_size/lot_size/min_notional kommen aus den Marktdaten der Boerse.
    Die Gebuehren sind bei manchen Boersen accountabhaengig (Extended) und
    werden dann erst im authentifizierten Pfad gefuellt - bis dahin None.
    """

    venue: str
    symbol: str
    tick_size: Decimal
    lot_size: Decimal
    min_notional: Decimal
    max_leverage: Decimal
    maker_fee: Optional[Decimal] = None
    taker_fee: Optional[Decimal] = None
    # ADR-006: Extended liefert den Hebel abhaengig vom Positionswert
    # (risk_factor_config), Paradex zusaetzlich ein position_limit. Die Liste ist
    # nach upper_bound aufsteigend sortiert; max_leverage ist der erste Eintrag.
    leverage_tiers: tuple[tuple[Decimal, Decimal], ...] = ()

    def max_leverage_for_notional(self, notional: Decimal) -> Decimal:
        """Maximaler Hebel fuer einen gegebenen Positionswert."""
        for upper_bound, leverage in self.leverage_tiers:
            if notional <= upper_bound:
                return leverage
        return self.max_leverage if not self.leverage_tiers else Decimal(0)


class Market(_Model):
    venue: str
    symbol: str  # kanonisch, z. B. BTC-PERP
    native_symbol: str  # Schreibweise der Boerse
    active: bool
    mark_price: Optional[Decimal] = None
    index_price: Optional[Decimal] = None
    open_interest: Optional[Decimal] = None
    daily_volume: Optional[Decimal] = None


class FundingInfo(_Model):
    """Funding einer Boerse fuer ein Symbol.

    Die Boersen liefern vier verschiedene Mechaniken (siehe RESEARCH.md
    Abschnitt 3). Deshalb werden native Rate UND natives Intervall UND die
    normalisierte Stundenrate gespeichert. Verglichen wird ausschliesslich
    ueber rate_hourly - eine 8h-Rate darf nie neben einer 1h-Rate stehen.

    Vorzeichenkonvention: rate > 0 bedeutet, Long zahlt an Short.
    """

    venue: str
    symbol: str
    native_rate: Decimal
    # Wie die Boerse die Rate angibt - Bruch je Intervall oder Prozent p. a.
    # Das ist unabhaengig vom Zahlungsintervall darunter.
    native_convention: str = "interval_fraction"
    native_interval_hours: Optional[Decimal] = None  # None = kontinuierlich
    rate_hourly: Decimal
    apr: Decimal
    as_of: datetime
    next_funding_time: Optional[datetime] = None

    @property
    def receiver(self) -> Side:
        """Welche Seite Funding empfaengt. Bei Rate 0 per Konvention SHORT."""
        return Side.SHORT if self.rate_hourly >= 0 else Side.LONG


class OrderBookLevel(_Model):
    price: Decimal
    size: Decimal


class OrderBook(_Model):
    venue: str
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    as_of: datetime

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def spread_bps(self) -> Optional[Decimal]:
        """Spread in Basispunkten, bezogen auf die Mitte."""
        if not self.bids or not self.asks:
            return None
        bid, ask = self.bids[0].price, self.asks[0].price
        mid = (bid + ask) / 2
        if mid <= 0:
            return None
        return (ask - bid) / mid * Decimal(10_000)


class VenueStatus(_Model):
    name: str
    supports_trading: bool
    reachable: bool
    environment: str  # testnet | mainnet
    detail: Optional[str] = None
    clock_skew_seconds: Optional[Decimal] = None


class Opportunity(_Model):
    """Ein Paarvorschlag: long auf einer Boerse, short auf der anderen."""

    symbol: str
    long_venue: str
    short_venue: str
    long_rate_hourly: Decimal
    short_rate_hourly: Decimal
    net_rate_hourly: Decimal
    net_apr: Decimal
    breakeven_hours: Optional[Decimal] = None
    # Kosten, mit denen der Break-even gerechnet wurde - damit in der UI
    # sichtbar ist, ob mit echten Accountgebuehren oder mit Annahmen gerechnet wurde.
    cost_basis_known: bool = False
    # Relative Abweichung der Mark-Preise beider Boersen. None = nicht pruefbar.
    price_deviation: Optional[Decimal] = None
    # True = Preise bestaetigen dasselbe Asset, False = widerlegen es,
    # None = keine Aussage moeglich. Ein gleicher Ticker allein genuegt nicht.
    asset_match: Optional[bool] = None
    min_depth_usd: Optional[Decimal] = None
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Balance(_Model):
    """Kontostand einer Boerse, auf die Sicherheitswaehrung bezogen."""

    venue: str
    collateral: str
    equity: Decimal
    available: Decimal
    unrealised_pnl: Decimal = Decimal(0)
    initial_margin: Decimal = Decimal(0)
    maintenance_margin: Optional[Decimal] = None
    as_of: datetime

    @property
    def margin_usage(self) -> Optional[Decimal]:
        """Anteil des Eigenkapitals, der als Initial Margin gebunden ist."""
        if self.equity <= 0:
            return None
        return self.initial_margin / self.equity


class Position(_Model):
    """Eine offene Position auf einer Boerse."""

    venue: str
    symbol: str
    side: Side
    size: Decimal  # immer positiv; die Richtung steht in side
    entry_price: Decimal
    mark_price: Decimal
    notional: Decimal
    unrealised_pnl: Decimal = Decimal(0)
    realised_pnl: Decimal = Decimal(0)
    liquidation_price: Optional[Decimal] = None
    leverage: Optional[Decimal] = None
    funding_paid: Optional[Decimal] = None  # Summe, sofern die Boerse sie nennt
    opened_at: Optional[datetime] = None  # nur, wenn die Boerse es nennt
    as_of: datetime

    @property
    def signed_size(self) -> Decimal:
        return self.size if self.side is Side.LONG else -self.size

    @property
    def signed_notional(self) -> Decimal:
        return self.notional if self.side is Side.LONG else -self.notional


class FundingPayment(_Model):
    """Eine einzelne Funding-Zahlung.

    `confirmed` unterscheidet, ob die Boerse die Zahlung gemeldet hat oder ob
    wir sie aus Rate und Groesse gerechnet haben. Eine gerechnete Zahlung darf
    im Journal nie wie eine bestaetigte aussehen.
    """

    venue: str
    symbol: str
    amount: Decimal  # positiv = erhalten, negativ = gezahlt
    rate: Optional[Decimal] = None
    position_size: Optional[Decimal] = None
    timestamp: datetime
    confirmed: bool = True
    external_id: Optional[str] = None


class HealthLevel(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class PositionHealth(_Model):
    """Abstand zur Liquidation und Margin-Auslastung, als Ampel."""

    venue: str
    symbol: str
    liquidation_distance: Optional[Decimal] = None  # Anteil der Preisbewegung
    margin_usage: Optional[Decimal] = None
    level: HealthLevel = HealthLevel.GREEN
    detail: Optional[str] = None
