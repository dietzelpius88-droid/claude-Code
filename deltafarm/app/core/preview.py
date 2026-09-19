"""Vorschau und ihre Gueltigkeit.

POST /api/pairs/open nimmt nur eine Vorschau an, die wenige Sekunden alt ist
und deren Grundlage sich seither nicht bewegt hat. Damit kann ein alter
Browser-Tab keine ungewollte Order ausloesen - und eine Richtung, die zwischen
Anzeige und Klick kippt, fuehrt zum Abbruch statt zu einem Verlustgeschaeft.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional

import structlog

LOG = structlog.get_logger(__name__)

DEFAULT_MAX_AGE_SECONDS = 15.0
DEFAULT_PRICE_TOLERANCE = Decimal("0.002")  # 0,2 %


class PreviewError(ValueError):
    """Die Vorschau taugt nicht mehr als Grundlage einer Order."""


class PreviewUnknown(PreviewError):
    """Token unbekannt oder bereits verbraucht."""


class PreviewExpired(PreviewError):
    """Die Vorschau ist zu alt."""


class PreviewMoved(PreviewError):
    """Preise oder Funding haben sich seit der Vorschau zu stark bewegt."""


@dataclass(frozen=True)
class Preview:
    token: str
    created_at: datetime
    payload: dict[str, Any]
    marks: Mapping[str, Decimal]
    funding: Mapping[str, Decimal]


class PreviewStore:
    """Haelt ausgestellte Vorschauen im Arbeitsspeicher.

    Serverseitig, damit ein Client keine Vorschau erfinden kann. Der Token ist
    zufaellig und nicht aus den Daten abgeleitet - sonst liesse sich eine
    abgelaufene Vorschau durch Nachbauen derselben Eingaben wiederbeleben.
    """

    def __init__(
        self,
        *,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        price_tolerance: Decimal = DEFAULT_PRICE_TOLERANCE,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._max_age = max_age_seconds
        self._tolerance = price_tolerance
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._previews: dict[str, Preview] = {}

    @property
    def max_age_seconds(self) -> float:
        return self._max_age

    def put(
        self,
        *,
        payload: dict[str, Any],
        marks: Mapping[str, Decimal],
        funding: Mapping[str, Decimal],
    ) -> str:
        token = secrets.token_urlsafe(16)
        self._previews[token] = Preview(
            token=token,
            created_at=self._clock(),
            payload=dict(payload),
            marks=dict(marks),
            funding=dict(funding),
        )
        return token

    def get(self, token: str) -> Optional[Preview]:
        return self._previews.get(token)

    def purge(self) -> int:
        """Entfernt abgelaufene Vorschauen."""
        jetzt = self._clock()
        alt = [
            t
            for t, v in self._previews.items()
            if (jetzt - v.created_at).total_seconds() > self._max_age
        ]
        for t in alt:
            del self._previews[t]
        return len(alt)

    def validate(
        self,
        token: str,
        *,
        marks: Mapping[str, Decimal],
        funding: Mapping[str, Decimal],
    ) -> Preview:
        """Prueft Alter, Preisbewegung und Funding-Richtung. Wirft bei Verstoss."""
        vorschau = self._previews.get(token)
        if vorschau is None:
            raise PreviewUnknown(
                "Diese Vorschau ist unbekannt oder bereits verwendet. Bitte neu erzeugen."
            )

        alter = (self._clock() - vorschau.created_at).total_seconds()
        if alter > self._max_age:
            raise PreviewExpired(
                f"Die Vorschau ist {alter:.0f} s alt, erlaubt sind {self._max_age:.0f} s. "
                "Bitte neu erzeugen."
            )

        for venue, damals in vorschau.marks.items():
            jetzt = marks.get(venue)
            if jetzt is None:
                raise PreviewMoved(
                    f"Kein aktueller Mark-Preis fuer {venue} - ohne Vergleich wird nicht "
                    "ausgefuehrt."
                )
            if damals <= 0:
                raise PreviewMoved(f"Ungueltiger Mark-Preis in der Vorschau fuer {venue}.")
            bewegung = abs(jetzt - damals) / damals
            if bewegung > self._tolerance:
                raise PreviewMoved(
                    f"{venue}: Der Preis hat sich seit der Vorschau um {bewegung:.2%} bewegt "
                    f"({damals} -> {jetzt}), erlaubt sind {self._tolerance:.2%}."
                )

        # Funding: das Vorzeichen je Boerse und das Netto muessen halten.
        for venue, damals in vorschau.funding.items():
            jetzt = funding.get(venue)
            if jetzt is None:
                raise PreviewMoved(f"Keine aktuelle Funding-Rate fuer {venue}.")
            if (damals > 0) != (jetzt > 0) and damals != 0 and jetzt != 0:
                raise PreviewMoved(
                    f"{venue}: Das Funding-Vorzeichen hat sich seit der Vorschau gedreht "
                    f"({damals} -> {jetzt}). Die geplante Richtung stimmt nicht mehr."
                )

        netto_damals = self._netto(vorschau.payload, vorschau.funding)
        netto_jetzt = self._netto(vorschau.payload, funding)
        if netto_damals is not None and netto_jetzt is not None and netto_jetzt <= 0:
            raise PreviewMoved(
                f"Das Netto-Funding ist seit der Vorschau von {netto_damals} auf {netto_jetzt} "
                "gefallen - das Paar wuerde nicht mehr verdienen."
            )

        return vorschau

    def consume(
        self,
        token: str,
        *,
        marks: Mapping[str, Decimal],
        funding: Mapping[str, Decimal],
    ) -> Preview:
        """Prueft und verbraucht die Vorschau.

        Nach der Ausfuehrung ist sie weg: ein zweiter Klick auf denselben Knopf
        darf nicht zu einer zweiten Position fuehren.
        """
        vorschau = self.validate(token, marks=marks, funding=funding)
        self._previews.pop(token, None)
        return vorschau

    @staticmethod
    def _netto(payload: Mapping[str, Any], funding: Mapping[str, Decimal]) -> Optional[Decimal]:
        lang, kurz = payload.get("long_venue"), payload.get("short_venue")
        if lang is None or kurz is None:
            return None
        if lang not in funding or kurz not in funding:
            return None
        return funding[kurz] - funding[lang]
