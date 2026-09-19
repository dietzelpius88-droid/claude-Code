"""Extended-Adapter, Phase 1: nur oeffentliche Marktdaten.

Alle Endpunkte und Feldnamen sind aus dem offiziellen SDK belegt
(x10xchange/python_sdk, Branch starknet, Stand 2026-09-04):
  GET /info/markets                     -> x10/clients/rest/modules/info_module.py
  GET /info/markets/<market>/orderbook
  GET /info/<market>/funding
Antworten sind in {"status": ..., "data": ...} verpackt (x10/models/http.py),
Feldnamen auf der Leitung sind camelCase (x10/models/base.py, to_camel).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

import structlog

from app.adapters._parse import dec, dec_or_none, pick, req, ts_from_millis
from app.adapters.base import FatalError, HttpAdapter, TradingNotSupported
from app.core import symbols as sym
from app.core.funding import RateConvention, to_apr, to_hourly_fraction
from app.models.domain import (
    Balance,
    FundingInfo,
    FundingPayment,
    Market,
    OrderBook,
    OrderBookLevel,
    Position,
    Side,
    SymbolRules,
)

LOG = structlog.get_logger(__name__)


class ExtendedAdapter(HttpAdapter):
    name = sym.EXTENDED
    supports_trading = False  # Phase 1 ist read-only

    # Extended zahlt Funding stuendlich; die Formel traegt das 8h-Fenster
    # bereits in sich (RESEARCH.md 2.2). Die Doku war von hier aus nicht
    # abrufbar, deshalb wird dieser Wert beim Start gegen die tatsaechlichen
    # Zeitstempel geprueft (ADR-004) statt geglaubt.
    declared_interval_hours = Decimal(1)
    # Extended liefert einen Bruch je Zahlungsintervall.
    rate_convention = RateConvention.INTERVAL_FRACTION

    def __init__(self, *, api_key: Optional[str] = None, **kwargs: Any) -> None:
        # Der Schluessel geht ausschliesslich in den Header, nie in eine URL
        # oder eine Logzeile. HttpAdapter maskiert ihn beim Loggen nicht, weil
        # er dort gar nicht erst auftaucht.
        kopfzeilen = dict(kwargs.pop("headers", None) or {})
        if api_key:
            kopfzeilen["X-Api-Key"] = api_key
        super().__init__(headers=kopfzeilen, **kwargs)
        self._has_api_key = bool(api_key)
        self._fee_cache: dict[str, tuple[Decimal, Decimal]] = {}

    @property
    def has_account_access(self) -> bool:
        return self._has_api_key

    def _require_key(self, was: str) -> None:
        if not self._has_api_key:
            raise TradingNotSupported(
                f"{self.name}: {was} braucht EXTENDED_API_KEY in der .env.",
                venue=self.name,
            )

    def _unwrap(self, antwort: Any, *, kontext: str) -> Any:
        """Schaelt {"status": "OK", "data": ...} aus."""
        if not isinstance(antwort, dict):
            raise FatalError(f"{kontext}: unerwartetes Antwortformat {type(antwort).__name__}.")
        status = antwort.get("status")
        if status is not None and str(status).upper() != "OK":
            fehler = antwort.get("error") or {}
            raise FatalError(
                f"{kontext}: Boerse meldet {status} "
                f"(code={fehler.get('code')}, message={fehler.get('message')})."
            )
        if "data" not in antwort:
            raise FatalError(f"{kontext}: kein 'data' in der Antwort. Felder: {sorted(antwort)[:12]}")
        return antwort["data"]

    async def _markets_raw(self, native: Optional[str] = None) -> list[dict]:
        kontext = f"{self.name} /info/markets"
        daten = self._unwrap(await self._get("/info/markets", market=native), kontext=kontext)
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'data' ist keine Liste.")
        return daten

    async def _market_raw(self, symbol: str) -> dict:
        native = sym.to_native(self.name, symbol)
        maerkte = await self._markets_raw(native)
        for m in maerkte:
            if m.get("name") == native:
                return m
        raise FatalError(
            f"{self.name}: Markt {native} (fuer {symbol}) nicht in /info/markets enthalten.",
            venue=self.name,
        )

    # --- Marktdaten -------------------------------------------------------

    async def list_markets(self) -> list[Market]:
        maerkte: list[Market] = []
        for m in await self._markets_raw():
            native = m.get("name")
            kanonisch = sym.to_canonical(self.name, native) if native else None
            if kanonisch is None:
                continue  # Listing, das wir nicht fuehren - kein Rateversuch
            stats = m.get("marketStats") or m.get("market_stats") or {}
            kontext = f"{self.name} /info/markets {native}"
            maerkte.append(
                Market(
                    venue=self.name,
                    symbol=kanonisch,
                    native_symbol=native,
                    active=bool(m.get("active", True)),
                    mark_price=dec_or_none(pick(stats, "markPrice", "mark_price"), kontext=kontext),
                    index_price=dec_or_none(pick(stats, "indexPrice", "index_price"), kontext=kontext),
                    open_interest=dec_or_none(pick(stats, "openInterest", "open_interest"), kontext=kontext),
                    daily_volume=dec_or_none(pick(stats, "dailyVolume", "daily_volume"), kontext=kontext),
                )
            )
        return maerkte

    async def get_funding(self, symbol: str) -> FundingInfo:
        m = await self._market_raw(symbol)
        stats = m.get("marketStats") or m.get("market_stats") or {}
        kontext = f"{self.name} marketStats {symbol}"

        native_rate = dec(req(stats, "fundingRate", "funding_rate", kontext=kontext), kontext=kontext)
        hourly = to_hourly_fraction(
            native_rate,
            convention=self.rate_convention,
            interval_hours=self.declared_interval_hours,
        )

        # nextFundingRate ist laut SDK-Modell ein Zeitstempel (int), kein Betrag.
        naechste = pick(stats, "nextFundingRate", "next_funding_rate")
        next_time = ts_from_millis(naechste, kontext=kontext) if naechste is not None else None

        return FundingInfo(
            venue=self.name,
            symbol=symbol,
            native_rate=native_rate,
            native_convention=self.rate_convention.value,
            native_interval_hours=self.declared_interval_hours,
            rate_hourly=hourly,
            apr=to_apr(hourly),
            as_of=datetime.now(timezone.utc),
            next_funding_time=next_time,
        )

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        native = sym.to_native(self.name, symbol)
        kontext = f"{self.name} /info/markets/{native}/orderbook"
        daten = self._unwrap(
            await self._get(f"/info/markets/{native}/orderbook"), kontext=kontext
        )

        def seite(schluessel: tuple[str, ...]) -> tuple[OrderBookLevel, ...]:
            roh = pick(daten, *schluessel) or []
            stufen = [
                OrderBookLevel(
                    price=dec(req(e, "price", "p", kontext=kontext), kontext=kontext),
                    size=dec(req(e, "qty", "q", kontext=kontext), kontext=kontext),
                )
                for e in roh[:depth]
            ]
            return tuple(stufen)

        return OrderBook(
            venue=self.name,
            symbol=symbol,
            bids=seite(("bid", "b", "bids")),
            asks=seite(("ask", "a", "asks")),
            as_of=datetime.now(timezone.utc),
        )

    async def get_mark_price(self, symbol: str) -> Decimal:
        m = await self._market_raw(symbol)
        stats = m.get("marketStats") or m.get("market_stats") or {}
        kontext = f"{self.name} markPrice {symbol}"
        return dec(req(stats, "markPrice", "mark_price", kontext=kontext), kontext=kontext)

    async def get_symbol_rules(self, symbol: str) -> SymbolRules:
        m = await self._market_raw(symbol)
        cfg = m.get("tradingConfig") or m.get("trading_config") or {}
        stats = m.get("marketStats") or m.get("market_stats") or {}
        kontext = f"{self.name} tradingConfig {symbol}"

        lot = dec(req(cfg, "minOrderSizeChange", "min_order_size_change", kontext=kontext), kontext=kontext)
        tick = dec(req(cfg, "minPriceChange", "min_price_change", kontext=kontext), kontext=kontext)
        min_size = dec(req(cfg, "minOrderSize", "min_order_size", kontext=kontext), kontext=kontext)
        mark = dec(req(stats, "markPrice", "mark_price", kontext=kontext), kontext=kontext)

        # Extended nennt keine Mindest-Notional, sondern eine Mindestgroesse in
        # der Basiswaehrung. Wir rechnen sie ueber den Mark-Preis um.
        min_notional = min_size * mark

        stufen: list[tuple[Decimal, Decimal]] = []
        for eintrag in cfg.get("riskFactorConfig") or cfg.get("risk_factor_config") or []:
            grenze = dec(req(eintrag, "upperBound", "upper_bound", kontext=kontext), kontext=kontext)
            faktor = dec(req(eintrag, "riskFactor", "risk_factor", kontext=kontext), kontext=kontext)
            if faktor > 0:
                stufen.append((grenze, (Decimal(1) / faktor).quantize(Decimal("0.01"))))
        stufen.sort(key=lambda p: p[0])

        max_leverage = dec_or_none(pick(cfg, "maxLeverage", "max_leverage"), kontext=kontext)
        if max_leverage is None:
            max_leverage = stufen[0][1] if stufen else Decimal(1)

        return SymbolRules(
            venue=self.name,
            symbol=symbol,
            tick_size=tick,
            lot_size=lot,
            min_notional=min_notional,
            max_leverage=max_leverage,
            # Gebuehren sind bei Extended accountabhaengig und kommen aus
            # GET /user/fees - also erst ab Phase 2 (ADR-005).
            maker_fee=None,
            taker_fee=None,
            leverage_tiers=tuple(stufen),
        )

    # --- Selbstpruefung ---------------------------------------------------

    async def funding_timestamps(self, symbol: str, lookback_hours: int = 48) -> Sequence[datetime]:
        """Zeitstempel der letzten Funding-Saetze, fuer die Intervallpruefung."""
        native = sym.to_native(self.name, symbol)
        ende = datetime.now(timezone.utc)
        start = ende - timedelta(hours=lookback_hours)
        kontext = f"{self.name} /info/{native}/funding"

        daten = self._unwrap(
            await self._get(
                f"/info/{native}/funding",
                startTime=int(start.timestamp() * 1000),
                endTime=int(ende.timestamp() * 1000),
            ),
            kontext=kontext,
        )
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'data' ist keine Liste.")
        # Das SDK akzeptiert fuer diese Felder "timestamp" und "T".
        return [
            ts_from_millis(req(e, "timestamp", "T", kontext=kontext), kontext=kontext)
            for e in daten
        ]

    # --- Account (Phase 2, nur lesend) -----------------------------------

    async def get_balance(self) -> Balance:
        """GET /user/balance - belegt aus x10/models/balance.py."""
        self._require_key("Kontostand")
        kontext = f"{self.name} /user/balance"
        d = self._unwrap(await self._get("/user/balance"), kontext=kontext)

        return Balance(
            venue=self.name,
            collateral=str(pick(d, "collateralName", "collateral_name") or "USD"),
            equity=dec(req(d, "equity", kontext=kontext), kontext=kontext),
            available=dec(
                req(d, "availableForTrade", "available_for_trade", kontext=kontext), kontext=kontext
            ),
            unrealised_pnl=dec_or_none(pick(d, "unrealisedPnl", "unrealised_pnl"), kontext=kontext)
            or Decimal(0),
            initial_margin=dec_or_none(pick(d, "initialMargin", "initial_margin"), kontext=kontext)
            or Decimal(0),
            as_of=datetime.now(timezone.utc),
        )

    async def get_positions(self) -> list[Position]:
        """GET /user/positions - belegt aus x10/models/position.py."""
        self._require_key("Positionen")
        kontext = f"{self.name} /user/positions"
        daten = self._unwrap(await self._get("/user/positions"), kontext=kontext)
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'data' ist keine Liste.")

        positionen: list[Position] = []
        for p in daten:
            native = p.get("market")
            kanonisch = sym.to_canonical(self.name, native) if native else None
            if kanonisch is None:
                continue  # Markt, den wir nicht fuehren
            groesse = abs(dec(req(p, "size", kontext=kontext), kontext=kontext))
            if groesse == 0:
                continue
            seite = Side.LONG if str(p.get("side", "LONG")).upper() == "LONG" else Side.SHORT

            positionen.append(
                Position(
                    venue=self.name,
                    symbol=kanonisch,
                    side=seite,
                    size=groesse,
                    entry_price=dec(req(p, "openPrice", "open_price", kontext=kontext), kontext=kontext),
                    mark_price=dec(req(p, "markPrice", "mark_price", kontext=kontext), kontext=kontext),
                    notional=abs(dec(req(p, "value", kontext=kontext), kontext=kontext)),
                    unrealised_pnl=dec_or_none(pick(p, "unrealisedPnl", "unrealised_pnl"), kontext=kontext)
                    or Decimal(0),
                    realised_pnl=dec_or_none(pick(p, "realisedPnl", "realised_pnl"), kontext=kontext)
                    or Decimal(0),
                    liquidation_price=dec_or_none(
                        pick(p, "liquidationPrice", "liquidation_price"), kontext=kontext
                    ),
                    leverage=dec_or_none(p.get("leverage"), kontext=kontext),
                    # Extended nennt fuer offene Positionen keine Funding-Summe.
                    funding_paid=None,
                    opened_at=(
                        ts_from_millis(p["createdAt"], kontext=kontext)
                        if p.get("createdAt") is not None
                        else None
                    ),
                    as_of=datetime.now(timezone.utc),
                )
            )
        return positionen

    async def get_fees(self, symbol: str) -> tuple[Decimal, Decimal]:
        """GET /user/fees - (maker, taker). Bei Extended accountabhaengig (ADR-005)."""
        self._require_key("Gebuehren")
        if symbol in self._fee_cache:
            return self._fee_cache[symbol]

        native = sym.to_native(self.name, symbol)
        kontext = f"{self.name} /user/fees"
        daten = self._unwrap(await self._get("/user/fees", market=native), kontext=kontext)
        if not isinstance(daten, list) or not daten:
            raise FatalError(f"{kontext}: keine Gebuehren fuer {native}.", venue=self.name)

        eintrag = next((e for e in daten if e.get("market") == native), daten[0])
        maker = dec(req(eintrag, "makerFeeRate", "maker_fee_rate", kontext=kontext), kontext=kontext)
        taker = dec(req(eintrag, "takerFeeRate", "taker_fee_rate", kontext=kontext), kontext=kontext)
        self._fee_cache[symbol] = (maker, taker)
        return maker, taker

    async def get_funding_payments(self, since: datetime) -> list[FundingPayment]:
        """Funding-Zahlungen geschlossener Positionen.

        Extended bietet keinen Endpunkt fuer einzelne Funding-Zahlungen
        (RESEARCH.md, OFFEN-6). Was es gibt, ist die Summe je geschlossener
        Position in `realisedPnlBreakdown.fundingFees` aus
        GET /user/positions/history. Die wird hier als **eine** Zahlung je
        Position gemeldet und ist `confirmed=True`, weil sie von der Boerse
        stammt - sie ist nur nicht nach Zeitpunkten aufgeloest.

        Fuer offene Positionen rechnet die Dienstschicht aus Rate und Groesse
        hoch und kennzeichnet das Ergebnis als `confirmed=False`.
        """
        self._require_key("Funding-Zahlungen")
        kontext = f"{self.name} /user/positions/history"
        daten = self._unwrap(
            await self._get("/user/positions/history", startTime=int(since.timestamp() * 1000)),
            kontext=kontext,
        )
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'data' ist keine Liste.")

        zahlungen: list[FundingPayment] = []
        for p in daten:
            native = p.get("market")
            kanonisch = sym.to_canonical(self.name, native) if native else None
            if kanonisch is None:
                continue
            aufschluesselung = (
                p.get("realisedPnlBreakdown") or p.get("realised_pnl_breakdown") or {}
            )
            betrag = dec_or_none(
                pick(aufschluesselung, "fundingFees", "funding_fees"), kontext=kontext
            )
            if betrag is None:
                continue
            geschlossen = pick(p, "closedTime", "closed_time") or pick(p, "createdTime", "created_time")
            zahlungen.append(
                FundingPayment(
                    venue=self.name,
                    symbol=kanonisch,
                    amount=betrag,
                    timestamp=(
                        ts_from_millis(geschlossen, kontext=kontext)
                        if geschlossen is not None
                        else datetime.now(timezone.utc)
                    ),
                    confirmed=True,
                    external_id=str(p.get("id")) if p.get("id") is not None else None,
                )
            )
        return zahlungen
