"""Broker- und Marktdaten-Schnittstellen."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Fill, OrderIntent, Quote


class MarketData(ABC):
    """Kursversorgung. Muss synchron und sehr schnell antworten."""

    @abstractmethod
    def quote(self, symbol: str) -> Quote | None: ...

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for s in symbols:
            q = self.quote(s)
            if q is not None:
                out[s] = q
        return out


class Broker(ABC):
    """Ausfuehrung. Alle Methoden muessen idempotent gegen Doppelaufrufe sein."""

    name: str = "base"

    @abstractmethod
    async def submit(self, intent: OrderIntent) -> Fill | None:
        """Gibt den Fill zurueck oder None, wenn nichts ausgefuehrt wurde."""

    @abstractmethod
    async def cancel_all(self, symbol: str) -> int: ...

    async def close(self) -> None:
        return None
