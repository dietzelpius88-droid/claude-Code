"""Funding-Mathematik.

Die Boersen liefern vier verschiedene Mechaniken (RESEARCH.md Abschnitt 3):
stuendliche Zahlung mit Stundenrate, stuendliche Zahlung als Achtel einer
8h-Rate, kontinuierliche Berechnung und feste Intervalle, die je Symbol
verschieden sein koennen. Verglichen wird deshalb ausschliesslich ueber die
hier normalisierte Stundenrate.

Vorzeichenkonvention im gesamten Projekt: rate > 0 heisst, Long zahlt an Short.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from app.models.domain import Side

HOURS_PER_YEAR = Decimal(24 * 365)


def normalize_to_hourly(native_rate: Decimal, interval_hours: Decimal) -> Decimal:
    """Rechnet die native Rate einer Boerse auf eine Stundenrate um.

    Das ist die wichtigste Funktion des Projekts: eine 8h-Rate, die
    unnormalisiert neben einer 1h-Rate steht, verschiebt den Netto-APR um
    Faktor 8.
    """
    if interval_hours <= 0:
        raise ValueError(
            f"Funding-Intervall muss positiv sein, war {interval_hours}. "
            "Ein unbekanntes Intervall wird nicht geraten."
        )
    return native_rate / interval_hours


def to_apr(rate_hourly: Decimal) -> Decimal:
    """Stundenrate auf ein Jahr hochgerechnet, ohne Zinseszins.

    Ohne Zinseszins, weil das Funding laufend ausgezahlt und nicht
    reinvestiert wird - eine APY-Rechnung waere hier zu optimistisch.
    """
    return rate_hourly * HOURS_PER_YEAR


def preferred_direction(rate_hourly: Decimal) -> Side:
    """Die Seite, die bei dieser Rate Funding empfaengt.

    Bei exakt null ist die Richtung wirtschaftlich gleichgueltig; wir geben
    per Konvention SHORT zurueck, damit die Ausgabe deterministisch bleibt.
    """
    return Side.SHORT if rate_hourly >= 0 else Side.LONG


def net_hourly_rate(*, long_rate_hourly: Decimal, short_rate_hourly: Decimal) -> Decimal:
    """Netto-Funding eines delta-neutralen Paares, pro Stunde.

    Auf der Long-Seite zahlt man die dortige Rate, auf der Short-Seite
    empfaengt man sie. Positiv bedeutet: das Paar verdient.
    """
    return short_rate_hourly - long_rate_hourly


def infer_interval_hours(timestamps: Sequence[datetime] | Iterable[datetime]) -> Optional[Decimal]:
    """Misst das tatsaechliche Funding-Intervall aus einer Zeitstempelreihe.

    Grundlage der Startpruefung aus ADR-004: passt der gemessene Abstand nicht
    zum deklarierten Intervall, startet der Adapter nicht. Der Median statt des
    Mittelwerts, damit eine einzelne Luecke (Wartung der Boerse) das Ergebnis
    nicht verzieht.

    Gibt None zurueck, wenn sich aus zu wenigen Punkten nichts ableiten laesst.
    """
    stamps = sorted(timestamps)
    if len(stamps) < 2:
        return None

    deltas = [
        Decimal((b - a).total_seconds()) / Decimal(3600)
        for a, b in zip(stamps, stamps[1:])
        if (b - a).total_seconds() > 0
    ]
    if not deltas:
        return None

    deltas.sort()
    mid = len(deltas) // 2
    median = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2
    return median.quantize(Decimal("0.0001"))


def interval_matches(
    declared_hours: Decimal,
    observed_hours: Optional[Decimal],
    *,
    tolerance: Decimal = Decimal("0.2"),
) -> bool:
    """Prueft das deklarierte Intervall gegen das gemessene (ADR-004).

    tolerance ist ein relativer Anteil: 0,2 laesst 20 % Abweichung zu. Das
    deckt verspaetete Zahlungen ab, aber nicht den Faktor 8, um den es geht.
    """
    if observed_hours is None:
        return True  # Keine Messgrundlage ist kein Widerspruch.
    if declared_hours <= 0:
        return False
    abweichung = abs(observed_hours - declared_hours) / declared_hours
    return abweichung <= tolerance
