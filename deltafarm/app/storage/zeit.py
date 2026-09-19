"""Zeitstempel aus der Datenbank normalisieren.

SQLite kennt keine Zeitzonen. Ein zeitzonenbewusster Zeitstempel geht hinein
und kommt naiv zurueck - und eine Subtraktion aus beidem wirft. Weil alles im
Projekt in UTC geschrieben wird, ist ein naiver Wert aus der Datenbank immer
UTC; das wird hier beim Lesen wieder angeheftet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def als_utc(wert: Optional[datetime]) -> Optional[datetime]:
    """Haengt UTC an, wenn der Zeitstempel keine Zeitzone traegt."""
    if wert is None:
        return None
    return wert if wert.tzinfo is not None else wert.replace(tzinfo=timezone.utc)


def jetzt() -> datetime:
    return datetime.now(timezone.utc)
