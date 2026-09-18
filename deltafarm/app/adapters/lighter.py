"""Lighter-Adapter, Phase 1: nur oeffentliche Marktdaten.

Alle Endpunkte und Feldnamen sind aus der offiziellen openapi.json des SDK
belegt (elliottech/lighter-python, v1.1.4, Stand 2026-09-15):
  GET /api/v1/orderBookDetails  -> Regeln, Gebuehren, Mark-Preis
  GET /api/v1/orderBookOrders   -> Orderbuch (asks/bids mit price und
                                   remaining_base_amount)
  GET /api/v1/funding-rates     -> aktuelle Raten, je Eintrag mit exchange
  GET /api/v1/fundings          -> Historie, resolution 1h oder 1d
Antworten tragen ein Feld "code" (200 = ok) und die Nutzlast unter einem
endpunktspezifischen Schluessel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

import structlog

from app.adapters._parse import dec, dec_or_none, pick, req, ts_from_seconds
from app.adapters.base import FatalError, HttpAdapter
from app.core import symbols as sym
from app.core.funding import normalize_to_hourly, to_apr
from app.models.domain import FundingInfo, Market, OrderBook, OrderBookLevel, SymbolRules

LOG = structlog.get_logger(__name__)


class LighterAdapter(HttpAdapter):
    name = sym.LIGHTER
    supports_trading = False  # Phase 1 ist read-only

    # Die Historie kennt nur die Aufloesungen 1h und 1d (openapi.json), was
    # fuer stuendliches Funding spricht. Belegt ist es nicht - deshalb wird der
    # Wert beim Start gegen die tatsaechlichen Zeitstempel geprueft (ADR-004).
    declared_interval_hours = Decimal(1)

    # Welcher Wert im Feld "exchange" fuer Lighter selbst steht, ist nicht
    # dokumentiert. Der Endpunkt liefert offenbar auch fremde Boersen, und eine
    # fremde Rate als Lighter-Rate zu verbuchen waere der teuerste Fehler
    # ueberhaupt. Deshalb wird hier gefiltert und bei Nichttreffer laut
    # abgebrochen - nie stillschweigend der erste Treffer genommen.
    exchange_tag_candidates = ("lighter", "zklighter", "LIGHTER")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._market_ids: dict[str, int] = {}  # natives Symbol -> market_id

    def _unwrap(self, antwort: Any, schluessel: str, *, kontext: str) -> Any:
        if not isinstance(antwort, dict):
            raise FatalError(f"{kontext}: unerwartetes Antwortformat {type(antwort).__name__}.")
        code = antwort.get("code")
        if code is not None and int(code) != 200:
            raise FatalError(f"{kontext}: Boerse meldet code={code}, message={antwort.get('message')}.")
        if schluessel not in antwort:
            raise FatalError(
                f"{kontext}: kein '{schluessel}' in der Antwort. Felder: {sorted(antwort)[:12]}"
            )
        return antwort[schluessel]

    async def _details_raw(self) -> list[dict]:
        kontext = f"{self.name} /api/v1/orderBookDetails"
        daten = self._unwrap(
            await self._get("/api/v1/orderBookDetails"), "order_book_details", kontext=kontext
        )
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'order_book_details' ist keine Liste.")
        # market_id gleich mitnehmen: das ist das Nachschlagen des deklarierten
        # Symbols in den Daten der Boerse, kein Raten.
        for d in daten:
            symbol, mid = d.get("symbol"), d.get("market_id")
            if symbol is not None and mid is not None:
                self._market_ids[str(symbol)] = int(mid)
        return daten

    async def _detail_for(self, symbol: str) -> dict:
        native = sym.to_native(self.name, symbol)
        for d in await self._details_raw():
            if d.get("symbol") == native:
                return d
        raise FatalError(
            f"{self.name}: Markt {native} (fuer {symbol}) nicht in orderBookDetails enthalten.",
            venue=self.name,
        )

    async def _market_id(self, symbol: str) -> int:
        native = sym.to_native(self.name, symbol)
        if native not in self._market_ids:
            await self._details_raw()
        if native not in self._market_ids:
            raise FatalError(f"{self.name}: keine market_id fuer {native}.", venue=self.name)
        return self._market_ids[native]

    # --- Marktdaten -------------------------------------------------------

    async def list_markets(self) -> list[Market]:
        maerkte: list[Market] = []
        for d in await self._details_raw():
            native = d.get("symbol")
            kanonisch = sym.to_canonical(self.name, native) if native else None
            if kanonisch is None:
                continue
            kontext = f"{self.name} orderBookDetails {native}"
            maerkte.append(
                Market(
                    venue=self.name,
                    symbol=kanonisch,
                    native_symbol=native,
                    active=str(d.get("status", "active")).lower() == "active",
                    mark_price=dec_or_none(d.get("mark_price"), kontext=kontext),
                    index_price=dec_or_none(d.get("index_price"), kontext=kontext),
                    open_interest=dec_or_none(d.get("open_interest"), kontext=kontext),
                    daily_volume=dec_or_none(d.get("daily_quote_token_volume"), kontext=kontext),
                )
            )
        return maerkte

    async def get_funding(self, symbol: str) -> FundingInfo:
        native = sym.to_native(self.name, symbol)
        kontext = f"{self.name} /api/v1/funding-rates"
        eintraege = self._unwrap(
            await self._get("/api/v1/funding-rates"), "funding_rates", kontext=kontext
        )
        if not isinstance(eintraege, list):
            raise FatalError(f"{kontext}: 'funding_rates' ist keine Liste.")

        passend = [e for e in eintraege if e.get("symbol") == native]
        if not passend:
            raise FatalError(f"{kontext}: kein Eintrag fuer {native}.", venue=self.name)

        eigene = [
            e
            for e in passend
            if str(e.get("exchange", "")).lower()
            in {t.lower() for t in self.exchange_tag_candidates}
        ]
        if not eigene:
            # Lieber gar keine Rate als die einer fremden Boerse.
            raise FatalError(
                f"{kontext}: kein Eintrag fuer {native} mit einer als Lighter erkennbaren "
                f"Boersenkennung. Gefundene Kennungen: "
                f"{sorted({str(e.get('exchange')) for e in passend})}. "
                "Bitte die richtige Kennung in exchange_tag_candidates ergaenzen.",
                venue=self.name,
            )

        native_rate = dec(req(eigene[0], "rate", kontext=kontext), kontext=kontext)
        hourly = normalize_to_hourly(native_rate, self.declared_interval_hours)

        return FundingInfo(
            venue=self.name,
            symbol=symbol,
            native_rate=native_rate,
            native_interval_hours=self.declared_interval_hours,
            rate_hourly=hourly,
            apr=to_apr(hourly),
            as_of=datetime.now(timezone.utc),
        )

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        market_id = await self._market_id(symbol)
        kontext = f"{self.name} /api/v1/orderBookOrders"
        antwort = await self._get("/api/v1/orderBookOrders", market_id=market_id, limit=depth)
        if not isinstance(antwort, dict):
            raise FatalError(f"{kontext}: unerwartetes Antwortformat.")
        code = antwort.get("code")
        if code is not None and int(code) != 200:
            raise FatalError(f"{kontext}: code={code}, message={antwort.get('message')}.")

        def seite(schluessel: str) -> tuple[OrderBookLevel, ...]:
            return tuple(
                OrderBookLevel(
                    price=dec(req(e, "price", kontext=kontext), kontext=kontext),
                    size=dec(
                        req(e, "remaining_base_amount", "initial_base_amount", kontext=kontext),
                        kontext=kontext,
                    ),
                )
                for e in (antwort.get(schluessel) or [])[:depth]
            )

        return OrderBook(
            venue=self.name,
            symbol=symbol,
            bids=seite("bids"),
            asks=seite("asks"),
            as_of=datetime.now(timezone.utc),
        )

    async def get_mark_price(self, symbol: str) -> Decimal:
        d = await self._detail_for(symbol)
        kontext = f"{self.name} mark_price {symbol}"
        return dec(req(d, "mark_price", kontext=kontext), kontext=kontext)

    async def get_symbol_rules(self, symbol: str) -> SymbolRules:
        d = await self._detail_for(symbol)
        kontext = f"{self.name} orderBookDetails {symbol}"

        # Lighter gibt Dezimalstellen statt Schrittweiten an.
        size_dec = int(dec(req(d, "supported_size_decimals", "size_decimals", kontext=kontext), kontext=kontext))
        price_dec = int(dec(req(d, "supported_price_decimals", "price_decimals", kontext=kontext), kontext=kontext))

        margin = dec_or_none(d.get("default_initial_margin_fraction"), kontext=kontext)
        max_leverage = (Decimal(1) / margin).quantize(Decimal("0.01")) if margin and margin > 0 else Decimal(1)

        return SymbolRules(
            venue=self.name,
            symbol=symbol,
            tick_size=Decimal(1).scaleb(-price_dec),
            lot_size=Decimal(1).scaleb(-size_dec),
            min_notional=dec(req(d, "min_quote_amount", kontext=kontext), kontext=kontext),
            max_leverage=max_leverage,
            maker_fee=dec_or_none(d.get("maker_fee"), kontext=kontext),
            taker_fee=dec_or_none(d.get("taker_fee"), kontext=kontext),
        )

    # --- Selbstpruefung ---------------------------------------------------

    async def funding_timestamps(self, symbol: str, lookback_hours: int = 48) -> Sequence[datetime]:
        market_id = await self._market_id(symbol)
        ende = datetime.now(timezone.utc)
        start = ende - timedelta(hours=lookback_hours)
        kontext = f"{self.name} /api/v1/fundings"

        daten = self._unwrap(
            await self._get(
                "/api/v1/fundings",
                market_id=market_id,
                resolution="1h",
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(ende.timestamp()),
                count_back=lookback_hours,
            ),
            "fundings",
            kontext=kontext,
        )
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'fundings' ist keine Liste.")
        return [
            ts_from_seconds(req(e, "timestamp", kontext=kontext), kontext=kontext) for e in daten
        ]
