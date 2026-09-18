"""Fehlerklassifikation: nur wiederholbare Fehler duerfen wiederholt werden."""

import httpx
import pytest

from app.adapters.base import (
    FatalError,
    RetryableError,
    classify_http_status,
    classify_transport_error,
)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_netz_und_ueberlastfehler_sind_wiederholbar(status):
    assert classify_http_status(status) is RetryableError


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_fachliche_fehler_sind_endgueltig(status):
    # Ungueltige Signatur, zu wenig Margin, unbekanntes Symbol: ein Retry
    # wuerde daran nichts aendern und nur Rate Limit verbrennen.
    assert classify_http_status(status) is FatalError


def test_erfolgsstatus_wird_nicht_klassifiziert():
    assert classify_http_status(200) is None


def test_zeitueberschreitung_ist_wiederholbar():
    assert classify_transport_error(httpx.ConnectTimeout("zu langsam")) is RetryableError


def test_verbindungsfehler_ist_wiederholbar():
    assert classify_transport_error(httpx.ConnectError("kein Netz")) is RetryableError
