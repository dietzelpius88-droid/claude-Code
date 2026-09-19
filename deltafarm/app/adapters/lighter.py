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


class LighterAdapter(HttpAdapter):
    name = sym.LIGHTER
    supports_trading = False  # Phase 1 ist read-only

    # Zahlungsintervall. Die Historie kennt nur die Aufloesungen 1h und 1d
    # (openapi.json), was fuer stuendliches Funding spricht. Belegt ist es
    # nicht - deshalb wird der Wert beim Start gegen die tatsaechlichen
    # Zeitstempel geprueft (ADR-004).
    declared_interval_hours = Decimal(1)

    # Das Feld "rate" in /api/v1/funding-rates ist Prozent pro Jahr, nicht ein
    # Bruch je Intervall. Die openapi.json sagt dazu nichts; die Angabe stammt
    # vom Betreiber dieses Werkzeugs. Achtung: das Feld "rate" in der Historie
    # (/api/v1/fundings) traegt dort ein Beispiel von "0.0001" und koennte einer
    # anderen Konvention folgen - es wird hier nur fuer die Zeitstempel genutzt,
    # nie fuer eine Rate.
    rate_convention = RateConvention.ANNUALIZED_PERCENT

    # Welcher Wert im Feld "exchange" fuer Lighter selbst steht, ist nicht
    # dokumentiert. Der Endpunkt liefert offenbar auch fremde Boersen, und eine
    # fremde Rate als Lighter-Rate zu verbuchen waere der teuerste Fehler
    # ueberhaupt. Deshalb wird hier gefiltert und bei Nichttreffer laut
    # abgebrochen - nie stillschweigend der erste Treffer genommen.
    exchange_tag_candidates = ("lighter", "zklighter", "lit")

    def __init__(self, *, account_index: Optional[int] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._market_ids: dict[str, int] = {}  # natives Symbol -> market_id
        # Lighters Account-Endpunkte sind oeffentlich lesbar: /api/v1/account
        # verlangt nur by=index&value=<account_index>, keine Signatur
        # (openapi.json). Fuer Phase 2 genuegt damit der Account-Index.
        self._account_index = account_index

    @property
    def has_account_access(self) -> bool:
        return self._account_index is not None

    def _require_account(self, was: str) -> int:
        if self._account_index is None:
            raise TradingNotSupported(
                f"{self.name}: {was} braucht LIGHTER_ACCOUNT_INDEX in der .env.",
                venue=self.name,
            )
        return self._account_index

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
        hourly = to_hourly_fraction(native_rate, convention=self.rate_convention)

        return FundingInfo(
            venue=self.name,
            symbol=symbol,
            native_rate=native_rate,
            native_convention=self.rate_convention.value,
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

    # --- Account (Phase 2, nur lesend) -----------------------------------

    async def _account_raw(self) -> dict:
        index = self._require_account("Kontodaten")
        kontext = f"{self.name} /api/v1/account"
        antwort = await self._get("/api/v1/account", by="index", value=index)
        if not isinstance(antwort, dict):
            raise FatalError(f"{kontext}: unerwartetes Antwortformat.")
        code = antwort.get("code")
        if code is not None and int(code) != 200:
            raise FatalError(f"{kontext}: code={code}, message={antwort.get('message')}.")
        return antwort

    async def get_balance(self) -> Balance:
        """Kontostand aus /api/v1/account (Schema DetailedAccount)."""
        d = await self._account_raw()
        kontext = f"{self.name} /api/v1/account"

        # total_asset_value ist das Eigenkapital inklusive offener Positionen;
        # collateral waere nur die Sicherheit ohne deren Bewertung.
        eigenkapital = dec_or_none(d.get("total_asset_value"), kontext=kontext)
        if eigenkapital is None:
            eigenkapital = dec(req(d, "collateral", kontext=kontext), kontext=kontext)

        return Balance(
            venue=self.name,
            collateral="USDC",
            equity=eigenkapital,
            available=dec(req(d, "available_balance", kontext=kontext), kontext=kontext),
            initial_margin=dec_or_none(d.get("cross_initial_margin_requirement"), kontext=kontext)
            or Decimal(0),
            maintenance_margin=dec_or_none(
                d.get("cross_maintenance_margin_requirement"), kontext=kontext
            ),
            as_of=datetime.now(timezone.utc),
        )

    async def get_positions(self) -> list[Position]:
        """Positionen aus /api/v1/account (Schema AccountPosition)."""
        d = await self._account_raw()
        kontext = f"{self.name} AccountPosition"

        positionen: list[Position] = []
        for p in d.get("positions") or []:
            native = p.get("symbol")
            kanonisch = sym.to_canonical(self.name, native) if native else None
            if kanonisch is None:
                continue
            groesse = abs(dec(req(p, "position", kontext=kontext), kontext=kontext))
            if groesse == 0:
                continue
            # sign: 1 = long, -1 = short
            vorzeichen = int(dec(req(p, "sign", kontext=kontext), kontext=kontext))
            seite = Side.LONG if vorzeichen >= 0 else Side.SHORT
            mark = dec_or_none(p.get("mark_price"), kontext=kontext)
            wert = abs(dec(req(p, "position_value", kontext=kontext), kontext=kontext))
            if mark is None:
                # Aus Wert und Groesse ableiten statt einen Preis zu erfinden.
                mark = (wert / groesse) if groesse > 0 else Decimal(0)

            positionen.append(
                Position(
                    venue=self.name,
                    symbol=kanonisch,
                    side=seite,
                    size=groesse,
                    entry_price=dec(req(p, "avg_entry_price", kontext=kontext), kontext=kontext),
                    mark_price=mark,
                    notional=wert,
                    unrealised_pnl=dec_or_none(p.get("unrealized_pnl"), kontext=kontext) or Decimal(0),
                    realised_pnl=dec_or_none(p.get("realized_pnl"), kontext=kontext) or Decimal(0),
                    liquidation_price=dec_or_none(p.get("liquidation_price"), kontext=kontext),
                    # Lighter nennt die Funding-Summe je Position mit.
                    funding_paid=dec_or_none(p.get("total_funding_paid_out"), kontext=kontext),
                    as_of=datetime.now(timezone.utc),
                )
            )
        return positionen

    async def get_funding_payments(self, since: datetime, *, limit: int = 100) -> list[FundingPayment]:
        """Echte Einzelzahlungen aus /api/v1/positionFunding.

        Der Endpunkt liefert je Zahlung Zeitstempel, Markt, Betrag, Rate und
        Positionsgroesse - damit ist Anforderung 6.4 bei Lighter erfuellt.
        """
        index = self._require_account("Funding-Zahlungen")
        kontext = f"{self.name} /api/v1/positionFunding"
        daten = self._unwrap(
            await self._get(
                "/api/v1/positionFunding",
                account_index=index,
                limit=limit,
                start_timestamp=int(since.timestamp()),
            ),
            "position_fundings",
            kontext=kontext,
        )
        if not isinstance(daten, list):
            raise FatalError(f"{kontext}: 'position_fundings' ist keine Liste.")

        # market_id -> Symbol, damit die Zahlungen zuordenbar sind.
        if not self._market_ids:
            await self._details_raw()
        nach_id = {mid: nativ for nativ, mid in self._market_ids.items()}

        zahlungen: list[FundingPayment] = []
        for e in daten:
            mid = e.get("market_id")
            nativ = nach_id.get(int(mid)) if mid is not None else None
            kanonisch = sym.to_canonical(self.name, nativ) if nativ else None
            if kanonisch is None:
                continue
            zahlungen.append(
                FundingPayment(
                    venue=self.name,
                    symbol=kanonisch,
                    amount=dec(req(e, "change", kontext=kontext), kontext=kontext),
                    rate=dec_or_none(e.get("rate"), kontext=kontext),
                    position_size=dec_or_none(e.get("position_size"), kontext=kontext),
                    timestamp=ts_from_seconds(req(e, "timestamp", kontext=kontext), kontext=kontext),
                    confirmed=True,
                    external_id=str(e.get("funding_id")) if e.get("funding_id") is not None else None,
                )
            )
        return zahlungen
