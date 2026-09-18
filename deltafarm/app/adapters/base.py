"""Adapter-Fundament: Interface, Fehlerklassen, Rate Limiting, Retry.

Die Engine kennt keine boersenspezifischen Eigenheiten. Alles, was eine Boerse
besonders macht, endet hier hinter dem Protocol.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable, Optional, Protocol, Sequence, TypeVar, runtime_checkable

import httpx
import structlog

from app.models.domain import (
    FundingInfo,
    Market,
    OrderBook,
    SymbolRules,
)

LOG = structlog.get_logger(__name__)
T = TypeVar("T")


# --- Fehlerklassen --------------------------------------------------------


class AdapterError(Exception):
    """Basis aller Adapterfehler."""

    def __init__(self, message: str, *, venue: Optional[str] = None) -> None:
        super().__init__(message)
        self.venue = venue


class RetryableError(AdapterError):
    """Netzwerk, 429, 5xx - ein zweiter Versuch kann helfen."""


class FatalError(AdapterError):
    """Ungueltige Signatur, zu wenig Margin, unbekanntes Symbol.

    Ein Retry wuerde daran nichts aendern und nur Rate Limit verbrennen.
    """


class TradingNotSupported(FatalError):
    """Die Boerse bietet keine Handels-API oder der Adapter ist read-only."""


def classify_http_status(status_code: int) -> Optional[type[AdapterError]]:
    """Ordnet einen HTTP-Status einer Fehlerklasse zu. None heisst: kein Fehler."""
    if status_code < 400:
        return None
    if status_code == 429 or status_code >= 500:
        return RetryableError
    return FatalError


def classify_transport_error(exc: Exception) -> type[AdapterError]:
    """Transportfehler sind fast immer voruebergehend."""
    if isinstance(exc, httpx.TransportError):
        return RetryableError
    return FatalError


# --- Rate Limiting --------------------------------------------------------


class TokenBucket:
    """Token-Bucket je Adapter, konfigurierbar.

    Umgesetzt ueber eine theoretische Ankunftszeit statt ueber einen laufenden
    Token-Zaehler. Grund: ein Zaehler sammelt bei jeder Auffuellung
    Float-Restbetraege ein. Faellt er dadurch auf 0,999... statt 1,0, wird die
    errechnete Wartezeit verschwindend klein - und eine Wartezeit von 1e-17
    laesst sich auf eine Float-Uhr nicht mehr aufaddieren. Das Ergebnis war
    eine Endlosschleife. Die Ankunftszeit ist absolut und korrigiert sich
    deshalb bei jedem Durchlauf selbst.

    Zeit bleibt hier bewusst float - time.monotonic() liefert float, und Zeit
    ist kein Geldbetrag. Die Decimal-Vorgabe gilt fuer Betraege, Groessen und
    Preise.

    Uhr und Schlafbefehl sind injizierbar, damit die Tests die Wartezeiten
    messen koennen, ohne sie abzuwarten.
    """

    # Toleranz beim Vergleich zweier Zeitpunkte. Eine Nanosekunde ist fuer
    # Rate Limiting bedeutungslos, verhindert aber, dass eine Restdifferenz
    # von einem Bit eine weitere Warterunde ausloest.
    _EPS = 1e-9

    def __init__(
        self,
        *,
        rate_per_second: float | Decimal,
        capacity: int,
        clock: Optional[Callable[[], float]] = None,
        sleeper: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        rate = float(rate_per_second)
        if rate <= 0:
            raise ValueError(f"rate_per_second muss positiv sein, war {rate_per_second}.")
        if capacity <= 0:
            raise ValueError(f"capacity muss positiv sein, war {capacity}.")

        self._interval = 1.0 / rate          # Zeit, die ein Token kostet
        self._burst = capacity * self._interval  # erlaubter Vorlauf
        self._capacity = capacity
        self._clock = clock or time.monotonic
        self._sleep = sleeper or asyncio.sleep
        self._tat = self._clock()            # theoretische Ankunftszeit
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Wartet, bis die Anfrage erlaubt ist, und bucht sie ein."""
        if tokens > self._capacity:
            raise ValueError(
                f"Anforderung von {tokens} Token uebersteigt die Kapazitaet "
                f"{self._capacity} - das koennte nie erfuellt werden."
            )
        async with self._lock:
            while True:
                jetzt = self._clock()
                tat = max(self._tat, jetzt)
                erlaubt_ab = tat + tokens * self._interval - self._burst
                if erlaubt_ab <= jetzt + self._EPS:
                    self._tat = tat + tokens * self._interval
                    return
                await self._sleep(erlaubt_ab - jetzt)


# --- Retry ----------------------------------------------------------------


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleeper: Optional[Callable[[float], Awaitable[None]]] = None,
    context: str = "",
) -> T:
    """Wiederholt nur RetryableError, mit exponentiellem Backoff und Jitter.

    FatalError wird sofort durchgereicht - genau dafuer ist die Trennung da.
    """
    sleep = sleeper or asyncio.sleep
    letzter: Optional[Exception] = None

    for versuch in range(1, attempts + 1):
        try:
            return await operation()
        except FatalError:
            raise
        except RetryableError as exc:
            letzter = exc
            if versuch == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (versuch - 1)))
            delay += random.uniform(0, delay * 0.1)  # Jitter gegen Gleichtakt
            LOG.warning(
                "wiederhole_nach_fehler",
                context=context,
                versuch=versuch,
                von=attempts,
                wartezeit=round(delay, 3),
                fehler=str(exc),
            )
            await sleep(delay)

    assert letzter is not None
    raise letzter


# --- Interface ------------------------------------------------------------


@runtime_checkable
class ExchangeAdapter(Protocol):
    """Das eine Interface, gegen das alle Boersen laufen.

    Neue Boerse anbinden heisst: eine Datei in adapters/ schreiben und
    registrieren, sonst nichts.
    """

    name: str
    supports_trading: bool

    # --- Marktdaten ---
    async def list_markets(self) -> list[Market]: ...
    async def get_funding(self, symbol: str) -> FundingInfo: ...
    async def get_orderbook(self, symbol: str, depth: int) -> OrderBook: ...
    async def get_mark_price(self, symbol: str) -> Decimal: ...

    # --- Metadaten fuer korrektes Sizing ---
    async def get_symbol_rules(self, symbol: str) -> SymbolRules: ...

    # --- Selbstpruefung ---
    async def funding_timestamps(self, symbol: str, lookback_hours: int) -> Sequence[datetime]: ...


class ReadOnlyAdapter:
    """Gemeinsame Basis fuer Adapter, die (noch) nicht handeln.

    Die Handelsmethoden sind vorhanden, damit das Interface vollstaendig
    bleibt, werfen aber TradingNotSupported. Phase 3 ersetzt sie.
    """

    name: str = "unbenannt"
    supports_trading: bool = False

    async def get_balance(self):  # pragma: no cover - Phase 2
        raise TradingNotSupported(f"{self.name}: Account-Zugriff kommt in Phase 2.", venue=self.name)

    async def get_positions(self):  # pragma: no cover - Phase 2
        raise TradingNotSupported(f"{self.name}: Account-Zugriff kommt in Phase 2.", venue=self.name)

    async def get_funding_payments(self, since):  # pragma: no cover - Phase 2
        raise TradingNotSupported(f"{self.name}: Account-Zugriff kommt in Phase 2.", venue=self.name)

    async def place_order(self, req):  # pragma: no cover - Phase 3
        raise TradingNotSupported(f"{self.name}: Orderversand kommt in Phase 3.", venue=self.name)

    async def cancel_order(self, order_id: str):  # pragma: no cover - Phase 3
        raise TradingNotSupported(f"{self.name}: Orderversand kommt in Phase 3.", venue=self.name)

    async def close_position(self, symbol: str):  # pragma: no cover - Phase 3
        raise TradingNotSupported(f"{self.name}: Orderversand kommt in Phase 3.", venue=self.name)


class HttpAdapter(ReadOnlyAdapter):
    """Basis fuer REST-Adapter: ein Client, ein Token-Bucket, eine Fehlerabbildung."""

    def __init__(
        self,
        *,
        base_url: str,
        rate_per_second: float,
        burst: int,
        timeout: float = 10.0,
        headers: Optional[dict[str, str]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._bucket = TokenBucket(rate_per_second=rate_per_second, capacity=burst)
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers or {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> object:
        """Ein GET mit Rate Limiting, Fehlerklassifikation und Retry."""

        async def einmal() -> object:
            await self._bucket.acquire()
            try:
                antwort = await self._client.get(path, params={k: v for k, v in params.items() if v is not None})
            except Exception as exc:  # Transportfehler
                raise classify_transport_error(exc)(
                    f"{self.name}: {path} nicht erreichbar ({exc.__class__.__name__}).",
                    venue=self.name,
                ) from exc

            klasse = classify_http_status(antwort.status_code)
            if klasse is not None:
                # Der Antworttext kann Kontext enthalten, aber keine Schluessel -
                # Schluessel stehen bei uns ausschliesslich in Headern.
                raise klasse(
                    f"{self.name}: {path} antwortete {antwort.status_code}: "
                    f"{antwort.text[:200]}",
                    venue=self.name,
                )
            # parse_float=Decimal: Zahlen aus der Antwort werden nie durch float
            # geschleust. Sonst waere 0.0001 aus dem JSON schon vor der ersten
            # Rechnung ungenau - und genau das soll dieses Projekt vermeiden.
            return json.loads(antwort.text, parse_float=Decimal)

        return await with_retries(einmal, context=f"{self.name}{path}")
