"""Hilfen zum Lesen von Boersenantworten.

Zwei Regeln: Zahlen werden nie durch float geschleust, und ein fehlendes Feld
fuehrt zu einem sprechenden Fehler statt zu einem stillen Standardwert. Ein
stillschweigendes 0 an der falschen Stelle waere in diesem Projekt teuer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from app.adapters.base import FatalError


def pick(daten: Mapping[str, Any], *namen: str) -> Any:
    """Erstes vorhandenes Feld aus einer Reihe erlaubter Schreibweisen.

    Die offiziellen SDKs akzeptieren je Feld mehrere Schreibweisen (Extended
    etwa "funding_rate" und "f"). Wir akzeptieren dieselbe Menge, statt uns auf
    eine festzulegen, die wir nicht live gegenpruefen konnten.
    """
    for name in namen:
        if name in daten and daten[name] is not None:
            return daten[name]
    return None


def req(daten: Mapping[str, Any], *namen: str, kontext: str) -> Any:
    wert = pick(daten, *namen)
    if wert is None:
        raise FatalError(
            f"{kontext}: keines der erwarteten Felder {namen} in der Antwort. "
            f"Vorhanden: {sorted(daten)[:12]}"
        )
    return wert


def dec(wert: Any, *, kontext: str) -> Decimal:
    """Wandelt einen Wert verlustfrei in Decimal."""
    if isinstance(wert, Decimal):
        return wert
    if isinstance(wert, float):
        # Sollte durch parse_float=Decimal nicht vorkommen; wenn doch, ist der
        # Wert bereits ungenau - wir retten, was zu retten ist, und sagen es.
        return Decimal(repr(wert))
    try:
        return Decimal(str(wert))
    except (InvalidOperation, ValueError) as exc:
        raise FatalError(f"{kontext}: {wert!r} ist keine Zahl.") from exc


def dec_or_none(wert: Any, *, kontext: str) -> Optional[Decimal]:
    return None if wert is None else dec(wert, kontext=kontext)


def ts_from_millis(wert: Any, *, kontext: str) -> datetime:
    return datetime.fromtimestamp(int(dec(wert, kontext=kontext)) / 1000, tz=timezone.utc)


def ts_from_seconds(wert: Any, *, kontext: str) -> datetime:
    return datetime.fromtimestamp(int(dec(wert, kontext=kontext)), tz=timezone.utc)
