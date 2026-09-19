"""Ergebnisrechnung eines abgeschlossenen Paares (Auftrag 6.5).

Funding erhalten, Gebuehren gezahlt, Preis-PnL, Netto, Haltedauer, realisierte
APR. Das ist die Grundlage fuer die Frage, was ein Airdrop-Punkt gekostet hat -
und damit der eigentliche Zweck des Journals.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.results import PairResult, ResultInput, compute_pair_result
from app.models.domain import Side

AUF = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
ZU = AUF + timedelta(hours=24)


def _fill(venue: str, side: Side, size: str, price: str, fee: str, *, closing: bool = False):
    return {
        "venue": venue,
        "side": side,
        "size": Decimal(size),
        "price": Decimal(price),
        "fee": Decimal(fee),
        "closing": closing,
    }


def _eingabe(**ueberschreiben) -> ResultInput:
    vorgabe = dict(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        opened_at=AUF,
        closed_at=ZU,
        # Long auf lighter zu 64000 auf, zu 64500 zu -> +50 USD
        # Short auf extended zu 64000 auf, zu 64500 zu -> -50 USD
        fills=[
            _fill("lighter", Side.LONG, "0.1", "64000", "6.40"),
            _fill("extended", Side.SHORT, "0.1", "64000", "32.00"),
            _fill("lighter", Side.LONG, "0.1", "64500", "6.45", closing=True),
            _fill("extended", Side.SHORT, "0.1", "64500", "32.25", closing=True),
        ],
        funding_payments=[Decimal("12.50"), Decimal("-2.00")],
        notional_usd=Decimal(6400),
    )
    vorgabe.update(ueberschreiben)
    return ResultInput(**vorgabe)


# --- Einzelposten ---------------------------------------------------------

def test_funding_wird_summiert():
    e = compute_pair_result(_eingabe())
    assert e.funding_received == Decimal("10.50")


def test_gebuehren_werden_summiert():
    # 6,40 + 32,00 + 6,45 + 32,25
    e = compute_pair_result(_eingabe())
    assert e.fees_paid == Decimal("77.10")


def test_preis_pnl_hebt_sich_bei_perfektem_hedge_auf():
    """Der Kern der Delta-Neutralitaet.

    Der Long gewinnt 50 USD, der Short verliert 50 USD - unterm Strich null.
    Verdient wird am Funding, nicht an der Bewegung.
    """
    e = compute_pair_result(_eingabe())
    assert e.price_pnl == Decimal(0)


def test_preis_pnl_bei_ungleichen_beinen():
    # Long 0,12 statt 0,1 - das Paar war nicht neutral.
    e = compute_pair_result(
        _eingabe(
            fills=[
                _fill("lighter", Side.LONG, "0.12", "64000", "0"),
                _fill("extended", Side.SHORT, "0.10", "64000", "0"),
                _fill("lighter", Side.LONG, "0.12", "64500", "0", closing=True),
                _fill("extended", Side.SHORT, "0.10", "64500", "0", closing=True),
            ]
        )
    )
    # Long: 0,12 * 500 = +60 ; Short: 0,10 * -500 = -50
    assert e.price_pnl == Decimal(10)


def test_netto_ist_funding_plus_preis_minus_gebuehren():
    e = compute_pair_result(_eingabe())
    # 10,50 + 0 - 77,10
    assert e.net_result == Decimal("-66.60")


def test_haltedauer_in_stunden():
    e = compute_pair_result(_eingabe())
    assert e.holding_hours == Decimal(24)


# --- Realisierte APR ------------------------------------------------------

def test_realisierte_apr_wird_hochgerechnet():
    """Netto auf das Notional, auf ein Jahr hochgerechnet.

    -66,60 auf 6400 in 24 Stunden = -1,040625 % ; mal 365 = -379,83 % p. a.
    """
    e = compute_pair_result(_eingabe())
    assert e.realized_apr.quantize(Decimal("0.0001")) == Decimal("-3.7983")


def test_positive_apr_bei_gewinn():
    e = compute_pair_result(_eingabe(funding_payments=[Decimal("200")]))
    # 200 - 77,10 = 122,90 auf 6400 in 24 h
    assert e.net_result == Decimal("122.90")
    assert e.realized_apr > 0


def test_ohne_haltedauer_keine_apr():
    # Sofort wieder geschlossen - eine Hochrechnung waere sinnlos.
    e = compute_pair_result(_eingabe(closed_at=AUF))
    assert e.holding_hours == Decimal(0)
    assert e.realized_apr is None


def test_ohne_notional_keine_apr():
    e = compute_pair_result(_eingabe(notional_usd=Decimal(0)))
    assert e.realized_apr is None


def test_noch_offenes_paar_hat_kein_endergebnis():
    e = compute_pair_result(_eingabe(closed_at=None))
    assert e.closed is False
    assert e.realized_apr is None
    # Die Zwischenstaende gibt es trotzdem.
    assert e.funding_received == Decimal("10.50")


# --- Grenzfaelle ----------------------------------------------------------

def test_ohne_fills_bleibt_alles_bei_null():
    e = compute_pair_result(_eingabe(fills=[], funding_payments=[]))
    assert e.fees_paid == Decimal(0)
    assert e.price_pnl == Decimal(0)
    assert e.net_result == Decimal(0)


def test_nur_eroeffnet_noch_nicht_geschlossen():
    """Ohne Gegenbuchung laesst sich kein Preis-PnL bilden."""
    e = compute_pair_result(
        _eingabe(
            fills=[
                _fill("lighter", Side.LONG, "0.1", "64000", "6.40"),
                _fill("extended", Side.SHORT, "0.1", "64000", "32.00"),
            ],
            closed_at=None,
        )
    )
    assert e.price_pnl == Decimal(0)
    assert e.fees_paid == Decimal("38.40")


def test_fehlende_gebuehr_zaehlt_als_null_und_wird_vermerkt():
    """Eine unbekannte Gebuehr darf die Summe nicht stillschweigend verfaelschen."""
    e = compute_pair_result(
        _eingabe(
            fills=[
                {"venue": "lighter", "side": Side.LONG, "size": Decimal("0.1"),
                 "price": Decimal(64000), "fee": None, "closing": False},
            ]
        )
    )
    assert e.fees_paid == Decimal(0)
    assert e.fees_incomplete is True


def test_vollstaendige_gebuehren_werden_nicht_als_unvollstaendig_gemeldet():
    assert compute_pair_result(_eingabe()).fees_incomplete is False
