"""Zentrale Datenstrukturen der Pipeline.

Datenfluss:
    RawNews  ->  AnalyzedEvent  ->  TradeSignal  ->  OrderIntent  ->  Position
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .clock import now_ms  # Systemuhr oder virtuelle Uhr (Backtest)

__all_time__ = ("now_ms",)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Taxonomie
# --------------------------------------------------------------------------
class EventClass(str, Enum):
    """Ereignisklasse - bestimmt Playbook, Halbwertszeit und Beta-Tabelle."""

    MONETARY_POLICY = "monetary_policy"      # Zinsen, QE/QT, Fed/EZB-Kommunikation
    TRADE_POLICY = "trade_policy"            # Zoelle, Exportkontrollen, Sanktionen
    FISCAL_POLICY = "fiscal_policy"          # Steuern, Ausgabenpakete, Schuldenobergrenze
    CRYPTO_POLICY = "crypto_policy"          # SEC/CFTC, ETFs, strategische Reserve, Regulierung
    GEOPOLITICS = "geopolitics"              # Krieg, Waffenruhe, Wahlen, OPEC
    MACRO_DATA = "macro_data"                # CPI, NFP, PCE, GDP, PMI
    ENERGY_POLICY = "energy_policy"          # SPR, Foerderung, Embargos
    CORPORATE = "corporate"                  # Earnings, M&A, Guidance
    UNKNOWN = "unknown"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class SourceTier(int, Enum):
    """Vertrauensstufe der Quelle. Steuert, ob sofort oder erst nach
    Zweitbestaetigung gehandelt werden darf."""

    PRIMARY = 1      # Originalquelle: Fed-Website, Federal Register, offizieller Account
    WIRE = 2         # Nachrichtenagentur: Reuters, AP, Bloomberg, DJ Newswire
    MAINSTREAM = 3   # Etablierte Medien mit Redaktion
    AGGREGATOR = 4   # Aggregatoren, schnelle Bots, Terminal-Feeds ohne Redaktion
    SOCIAL = 5       # X/Telegram/anonyme Accounts - nie allein handelbar

    @property
    def trust(self) -> float:
        """Vertrauensfaktor 0..1, multipliziert in die Conviction."""
        return {1: 1.00, 2: 0.95, 3: 0.80, 4: 0.60, 5: 0.35}[int(self)]


class SignalState(str, Enum):
    PROPOSED = "proposed"        # Fast Path hat Signal erzeugt
    CONFIRMED = "confirmed"      # Slow Path (LLM/Zweitquelle) bestaetigt
    REJECTED = "rejected"        # Risiko-Gate oder Widerspruch
    EXECUTED = "executed"
    EXPIRED = "expired"


# --------------------------------------------------------------------------
# Stufe 1: Rohnachricht
# --------------------------------------------------------------------------
@dataclass(slots=True)
class RawNews:
    """Eine eingegangene Rohmeldung, bevor sie bewertet wurde."""

    source_id: str                     # z.B. "fed_press", "truthsocial_djt"
    tier: SourceTier
    headline: str
    body: str = ""
    url: str = ""
    author: str = ""
    # published_ms: Zeitstempel der Quelle; received_ms: Eingang bei uns.
    published_ms: int = field(default_factory=now_ms)
    received_ms: int = field(default_factory=now_ms)
    id: str = field(default_factory=lambda: new_id("news"))
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.headline}\n{self.body}".strip()

    @property
    def source_latency_ms(self) -> int:
        """Wie alt war die Meldung schon, als wir sie bekamen."""
        return max(0, self.received_ms - self.published_ms)


# --------------------------------------------------------------------------
# Stufe 2: bewertetes Ereignis
# --------------------------------------------------------------------------
@dataclass(slots=True)
class AnalyzedEvent:
    """Ergebnis der Auswertung: was ist passiert, wen trifft es, wie stark."""

    news: RawNews
    event_class: EventClass
    # sentiment: -1 (maximal bearish) .. +1 (maximal bullish) fuer die
    # betroffene Anlageklasse in ihrer "natuerlichen" Richtung.
    sentiment: float
    # confidence: Wie sicher ist die Klassifikation selbst (0..1)?
    confidence: float
    # specificity: Konkretheit (Zahlen, Datum, "unterzeichnet") 0..1.
    specificity: float
    # novelty: 1.0 = neu, 0.0 = bereits eingepreiste Wiederholung.
    novelty: float
    # Halbwertszeit des Impulses in Sekunden -> steuert Time-Stop.
    half_life_s: int
    entities: list[str] = field(default_factory=list)      # erkannte Begriffe
    target_hints: list[str] = field(default_factory=list)  # direkt genannte Symbole
    rationale: str = ""
    analyzer: str = "lexicon"          # "lexicon" | "llm" | "fusion"
    analyzed_ms: int = field(default_factory=now_ms)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def conviction(self) -> float:
        """Gesamtueberzeugung 0..1 - das zentrale Gate vor jedem Trade.

        Multiplikativ, damit ein einzelner schwacher Faktor (z.B. unsichere
        Quelle oder alte Nachricht) den Trade zuverlaessig blockiert.
        """
        return (
            abs(self.sentiment)
            * self.confidence
            * self.news.tier.trust
            # Konkretheit daempft, loescht aber nicht: auch eine rein
            # qualitative Aussage ("wir umarmen Krypto") bewegt Maerkte.
            * (0.55 + 0.45 * self.specificity)
            * self.novelty
        )

    @property
    def direction(self) -> Direction:
        if self.sentiment > 0:
            return Direction.LONG
        if self.sentiment < 0:
            return Direction.SHORT
        return Direction.FLAT


# --------------------------------------------------------------------------
# Handelsuniversum
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Asset:
    """Ein handelbares Instrument samt Reaktionsprofil auf Ereignisklassen."""

    symbol: str
    venue: str
    kind: str = "perp"                  # perp | spot | future | cfd
    # Beta je Ereignisklasse: erwartete Bewegung relativ zum Klassen-Leitwert.
    betas: dict[str, float] = field(default_factory=dict)
    adv_usd: float = 0.0                # durchschn. Tagesvolumen in USD
    typical_spread_bps: float = 2.0
    daily_vol_bps: float = 300.0        # typische Tagesvolatilitaet in bps
    taker_fee_bps: float = 4.5
    max_leverage: float = 5.0
    min_notional_usd: float = 10.0
    lot_size: float = 0.001
    cluster: str = "misc"               # Korrelationsgruppe fuer Risiko-Limits
    inverse: bool = False               # True: reagiert gegenlaeufig (z.B. Bonds/VIX)
    enabled: bool = True

    def beta_for(self, ec: EventClass) -> float:
        return float(self.betas.get(ec.value, 0.0))


@dataclass(slots=True)
class Quote:
    """Momentaufnahme des Marktes fuer ein Symbol."""

    symbol: str
    bid: float
    ask: float
    ts_ms: int = field(default_factory=now_ms)
    bid_size_usd: float = 0.0
    ask_size_usd: float = 0.0
    # ATR in bps auf dem Ausfuehrungs-Zeitrahmen (z.B. 1m), fuer Stops.
    atr_bps: float = 0.0
    # depth_usd: handelbares Volumen innerhalb unseres Slippage-Budgets.
    depth_usd: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        m = self.mid
        return 0.0 if m <= 0 else (self.ask - self.bid) / m * 10_000.0

    @property
    def age_ms(self) -> int:
        return max(0, now_ms() - self.ts_ms)


# --------------------------------------------------------------------------
# Stufe 3: Handelssignal
# --------------------------------------------------------------------------
@dataclass(slots=True)
class TradeSignal:
    """Konkreter Handelsvorschlag fuer genau ein Instrument."""

    event: AnalyzedEvent
    asset: Asset
    direction: Direction
    # Erwartete Bewegung in bps (Basis fuer Take-Profit und Edge-Rechnung).
    expected_move_bps: float
    # Erwartete Kosten in bps (Spread + Slippage + Gebuehren, Roundtrip).
    expected_cost_bps: float
    conviction: float
    stop_bps: float
    targets_bps: list[float] = field(default_factory=list)
    time_stop_s: int = 900
    # max_chase_bps: Wie weit darf der Preis seit Referenz gelaufen sein,
    # bevor wir den Einstieg als "zu spaet" verwerfen.
    max_chase_bps: float = 0.0
    reference_px: float = 0.0
    state: SignalState = SignalState.PROPOSED
    id: str = field(default_factory=lambda: new_id("sig"))
    created_ms: int = field(default_factory=now_ms)
    notes: list[str] = field(default_factory=list)

    @property
    def net_edge_bps(self) -> float:
        return self.expected_move_bps - self.expected_cost_bps

    @property
    def rr(self) -> float:
        """Chance-Risiko-Verhaeltnis auf Basis Netto-Edge und Stopweite."""
        return 0.0 if self.stop_bps <= 0 else self.net_edge_bps / self.stop_bps


# --------------------------------------------------------------------------
# Stufe 4: Order und Position
# --------------------------------------------------------------------------
@dataclass(slots=True)
class OrderIntent:
    """Auszufuehrende Order (immer limitiert - nie blind Market in die Spitze)."""

    signal_id: str
    symbol: str
    venue: str
    side: Direction
    qty: float
    limit_px: float
    tif: str = "IOC"                    # IOC | GTC | POST_ONLY
    reduce_only: bool = False
    slice_idx: int = 0
    tag: str = "entry"                  # entry | add | tp | stop | flatten
    id: str = field(default_factory=lambda: new_id("ord"))
    created_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class Fill:
    order_id: str
    symbol: str
    side: Direction
    qty: float
    px: float
    fee_usd: float = 0.0
    ts_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class Position:
    """Offene Position inkl. aller Exit-Parameter."""

    symbol: str
    venue: str
    side: Direction
    qty: float
    entry_px: float
    signal_id: str
    event_class: EventClass
    cluster: str
    stop_px: float
    time_stop_ms: int
    targets: list[tuple[float, float]] = field(default_factory=list)  # (px, anteil)
    risk_usd: float = 0.0
    opened_ms: int = field(default_factory=now_ms)
    peak_px: float = 0.0
    # Ursprungsmenge - Basis fuer die Anteile der Teilverkaufsleiter.
    initial_qty: float = 0.0
    # True, sobald der Stop nachgezogen wurde (Einstand oder Trailing).
    trailed: bool = False
    # Urspruenglicher Stop - Bezugsgroesse fuer alle R-Berechnungen. Ohne ihn
    # wuerde ein nachgezogener Stop die Kennzahlen im Nachhinein schoenrechnen.
    initial_stop_px: float = 0.0
    # Erwartete Gesamtbewegung des Ereignisses - steuert die Weite des Trailings.
    expected_move_bps: float = 0.0
    realized_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    id: str = field(default_factory=lambda: new_id("pos"))

    @property
    def notional_usd(self) -> float:
        return self.qty * self.entry_px

    def unrealized_pnl_usd(self, px: float) -> float:
        d = 1.0 if self.side is Direction.LONG else -1.0
        return (px - self.entry_px) * self.qty * d

    def r_multiple(self, px: float) -> float:
        """Gewinn/Verlust in Vielfachen des anfaenglichen Risikos."""
        if self.risk_usd <= 0:
            return 0.0
        return self.unrealized_pnl_usd(px) / self.risk_usd
