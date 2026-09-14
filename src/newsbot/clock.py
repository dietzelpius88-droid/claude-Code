"""Umschaltbare Zeitquelle.

Im Livebetrieb die Systemuhr, im Backtest eine virtuelle Uhr. Ohne diese
Trennung waeren Zeit-Stops im Replay nicht pruefbar: eine Halbwertszeit von
25 Minuten laesst sich nicht testen, wenn der Test in 30 ms durchlaeuft.
"""
from __future__ import annotations

import time

_virtual_ms: int | None = None


def now_ms() -> int:
    if _virtual_ms is not None:
        return _virtual_ms
    return int(time.time() * 1000)


def set_virtual(ms: int) -> None:
    """Schaltet auf virtuelle Zeit um und setzt sie auf `ms`."""
    global _virtual_ms
    _virtual_ms = int(ms)


def advance(delta_ms: int) -> int:
    global _virtual_ms
    if _virtual_ms is None:
        raise RuntimeError("advance() nur im virtuellen Modus - zuerst set_virtual()")
    _virtual_ms += int(delta_ms)
    return _virtual_ms


def reset() -> None:
    """Zurueck zur Systemuhr."""
    global _virtual_ms
    _virtual_ms = None


def is_virtual() -> bool:
    return _virtual_ms is not None
