"""Preflight: die sechs Pruefungen aus Auftrag 6.2.

Alle muessen bestanden sein, sonst gibt es keinen Ausfuehren-Button. Eine
Pruefung, die bei fehlenden Daten stillschweigend durchgeht, waere schlimmer
als gar keine.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.funding import to_apr
from app.core.preflight import CheckStatus, PreflightInput, run_preflight
from app.core.sizing import compute_sizing
from app.models.domain import (
    Balance,
    FundingInfo,
    OrderBook,
    OrderBookLevel,
    SymbolRules,
)

JETZT = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _rules(venue: str, *, taker: str | None = "0.0005") -> SymbolRules:
    return SymbolRules(
        venue=venue,
        symbol="BTC-PERP",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.001"),
        min_notional=Decimal(10),
        max_leverage=Decimal(20),
        taker_fee=Decimal(taker) if taker is not None else None,
    )


def _funding(venue: str, rate: str) -> FundingInfo:
    r = Decimal(rate)
    return FundingInfo(
        venue=venue,
        symbol="BTC-PERP",
        native_rate=r,
        native_interval_hours=Decimal(1),
        rate_hourly=r,
        apr=to_apr(r),
        as_of=JETZT,
    )


def _book(venue: str, *, tiefe: str = "10") -> OrderBook:
    return OrderBook(
        venue=venue,
        symbol="BTC-PERP",
        bids=tuple(
            OrderBookLevel(price=Decimal(64000) - Decimal(i * 10), size=Decimal(tiefe))
            for i in range(1, 6)
        ),
        asks=tuple(
            OrderBookLevel(price=Decimal(64000) + Decimal(i * 10), size=Decimal(tiefe))
            for i in range(1, 6)
        ),
        as_of=JETZT,
    )


def _balance(venue: str, available: str = "100000") -> Balance:
    return Balance(
        venue=venue,
        collateral="USD",
        equity=Decimal(available),
        available=Decimal(available),
        as_of=JETZT,
    )


def _eingabe(**ueberschreiben) -> PreflightInput:
    sizing = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("lighter"),
        short_rules=_rules("extended"),
        long_mark=Decimal(64000),
        short_mark=Decimal(64000),
    )
    vorgabe = dict(
        sizing=sizing,
        long_rules=_rules("lighter"),
        short_rules=_rules("extended"),
        long_funding=_funding("lighter", "-0.00001"),
        short_funding=_funding("extended", "0.00002"),
        long_book=_book("lighter"),
        short_book=_book("extended"),
        long_balance=_balance("lighter"),
        short_balance=_balance("extended"),
        reachable={"lighter": True, "extended": True},
        clock_skew_seconds={"lighter": Decimal("0.4"), "extended": Decimal("0.2")},
        max_slippage=Decimal("0.002"),
        long_leverage=Decimal(5),
        short_leverage=Decimal(5),
    )
    vorgabe.update(ueberschreiben)
    return PreflightInput(**vorgabe)


def _status(ergebnis, schluessel: str) -> CheckStatus:
    return next(p for p in ergebnis.checks if p.key == schluessel).status


# --- Alles in Ordnung -----------------------------------------------------

def test_sauberer_fall_besteht_alle_pruefungen():
    ergebnis = run_preflight(_eingabe())
    assert ergebnis.ok is True
    assert all(p.status is not CheckStatus.FAIL for p in ergebnis.checks)


def test_alle_sechs_pruefungen_sind_vorhanden():
    schluessel = {p.key for p in run_preflight(_eingabe()).checks}
    assert schluessel == {
        "erreichbarkeit",
        "margin",
        "preisabweichung",
        "tiefe_slippage",
        "funding_vorzeichen",
        "breakeven",
    }


# --- 1) Erreichbarkeit und Zeitversatz ------------------------------------

def test_nicht_erreichbare_boerse_blockiert():
    ergebnis = run_preflight(_eingabe(reachable={"lighter": False, "extended": True}))
    assert _status(ergebnis, "erreichbarkeit") is CheckStatus.FAIL
    assert ergebnis.ok is False


def test_zu_grosser_zeitversatz_blockiert():
    # Aster lehnt Orders ab, deren Nonce mehr als 10 s abweicht - der
    # Zeitversatz ist kein kosmetisches Problem.
    ergebnis = run_preflight(
        _eingabe(clock_skew_seconds={"lighter": Decimal(12), "extended": Decimal("0.2")})
    )
    assert _status(ergebnis, "erreichbarkeit") is CheckStatus.FAIL


def test_unbekannter_zeitversatz_warnt_statt_durchzuwinken():
    ergebnis = run_preflight(
        _eingabe(clock_skew_seconds={"lighter": None, "extended": Decimal("0.2")})
    )
    assert _status(ergebnis, "erreichbarkeit") is CheckStatus.WARN
    assert ergebnis.ok is True


# --- 2) Margin ------------------------------------------------------------

def test_ausreichende_margin_besteht():
    assert _status(run_preflight(_eingabe()), "margin") is CheckStatus.PASS


def test_zu_wenig_freie_margin_blockiert():
    # 6400 bei Hebel 5 = 1280 Margin; bei 2000 frei und 50 % Puffer sind nur
    # 1000 nutzbar.
    ergebnis = run_preflight(_eingabe(long_balance=_balance("lighter", "2000")))
    assert _status(ergebnis, "margin") is CheckStatus.FAIL


def test_margin_puffer_ist_konfigurierbar():
    ergebnis = run_preflight(
        _eingabe(long_balance=_balance("lighter", "2000"), margin_share=Decimal("0.9"))
    )
    # 90 % von 2000 = 1800 > 1280
    assert _status(ergebnis, "margin") is CheckStatus.PASS


def test_ohne_kontostand_wird_nicht_durchgewunken():
    ergebnis = run_preflight(_eingabe(long_balance=None))
    assert _status(ergebnis, "margin") is CheckStatus.FAIL


def test_hebel_ueber_dem_maximum_der_boerse_blockiert():
    ergebnis = run_preflight(_eingabe(long_leverage=Decimal(50)))
    assert _status(ergebnis, "margin") is CheckStatus.FAIL


# --- 3) Preisabweichung ---------------------------------------------------

def test_kleine_preisabweichung_besteht():
    assert _status(run_preflight(_eingabe()), "preisabweichung") is CheckStatus.PASS


def test_grosse_preisabweichung_blockiert():
    sizing = compute_sizing(
        notional_usd=Decimal(6400),
        long_rules=_rules("lighter"),
        short_rules=_rules("extended"),
        long_mark=Decimal(64000),
        short_mark=Decimal(66000),  # 3,1 % auseinander
    )
    ergebnis = run_preflight(_eingabe(sizing=sizing))
    assert _status(ergebnis, "preisabweichung") is CheckStatus.FAIL


# --- 4) Tiefe und Slippage ------------------------------------------------

def test_ausreichende_tiefe_besteht():
    assert _status(run_preflight(_eingabe()), "tiefe_slippage") is CheckStatus.PASS


def test_zu_duennes_orderbuch_blockiert():
    ergebnis = run_preflight(_eingabe(long_book=_book("lighter", tiefe="0.001")))
    assert _status(ergebnis, "tiefe_slippage") is CheckStatus.FAIL


def test_zu_hoher_slippage_blockiert():
    """Ein Buch, das die Order ueber mehrere Stufen zwingt.

    Bei 0,05 BTC je Stufe laeuft eine Order ueber 0,1 BTC durch zwei Stufen
    und erzeugt messbare Slippage - erst dann greift die Schwelle ueberhaupt.
    """
    duenn = _book("lighter", tiefe="0.05")
    ergebnis = run_preflight(_eingabe(long_book=duenn, max_slippage=Decimal("0.0000001")))
    assert ergebnis.long_slippage > 0
    assert _status(ergebnis, "tiefe_slippage") is CheckStatus.FAIL


def test_slippage_knapp_unter_der_schwelle_besteht():
    duenn = _book("lighter", tiefe="0.05")
    ergebnis = run_preflight(_eingabe(long_book=duenn, max_slippage=Decimal("0.001")))
    assert _status(ergebnis, "tiefe_slippage") is CheckStatus.PASS


def test_fehlendes_orderbuch_blockiert():
    ergebnis = run_preflight(_eingabe(long_book=None))
    assert _status(ergebnis, "tiefe_slippage") is CheckStatus.FAIL


# --- 5) Funding-Vorzeichen ------------------------------------------------

def test_richtiges_vorzeichen_besteht():
    assert _status(run_preflight(_eingabe()), "funding_vorzeichen") is CheckStatus.PASS


def test_falsche_richtung_blockiert():
    """Wir wollen auf der Seite stehen, die Funding empfaengt."""
    ergebnis = run_preflight(
        _eingabe(
            long_funding=_funding("lighter", "0.00002"),
            short_funding=_funding("extended", "-0.00001"),
        )
    )
    assert _status(ergebnis, "funding_vorzeichen") is CheckStatus.FAIL


def test_netto_funding_null_blockiert():
    ergebnis = run_preflight(
        _eingabe(
            long_funding=_funding("lighter", "0.00002"),
            short_funding=_funding("extended", "0.00002"),
        )
    )
    assert _status(ergebnis, "funding_vorzeichen") is CheckStatus.FAIL


# --- 6) Break-even --------------------------------------------------------

def test_breakeven_wird_berechnet():
    ergebnis = run_preflight(_eingabe())
    assert ergebnis.breakeven_hours is not None
    # Kosten: (0,0005 + 0,0005) * 2 = 0,002 ; Netto 0,00003/h -> 66,67 h
    assert ergebnis.breakeven_hours.quantize(Decimal("0.01")) == Decimal("66.67")
    assert _status(ergebnis, "breakeven") is CheckStatus.PASS


def test_unbekannte_gebuehren_warnen_ohne_zu_blockieren():
    """Ohne Gebuehren ist der Break-even nicht zu rechnen.

    Das blockiert nicht, muss aber sichtbar sein - sonst handelt man ohne zu
    wissen, ab wann sich die Position traegt.
    """
    ergebnis = run_preflight(_eingabe(long_rules=_rules("lighter", taker=None)))
    assert _status(ergebnis, "breakeven") is CheckStatus.WARN
    assert ergebnis.breakeven_hours is None
    assert ergebnis.ok is True


# --- Gesamturteil ---------------------------------------------------------

def test_eine_einzige_rote_pruefung_verhindert_die_ausfuehrung():
    ergebnis = run_preflight(_eingabe(long_book=None))
    assert ergebnis.ok is False
    assert ergebnis.blocking_reasons


def test_warnungen_allein_verhindern_nichts():
    ergebnis = run_preflight(_eingabe(long_rules=_rules("lighter", taker=None)))
    assert ergebnis.ok is True
    assert ergebnis.blocking_reasons == []


def test_funding_detail_ist_lesbar():
    """Rohe Decimals mit 30 Stellen sind in der Oberflaeche unbrauchbar."""
    ergebnis = run_preflight(_eingabe())
    detail = next(p for p in ergebnis.checks if p.key == "funding_vorzeichen").detail

    assert "%" in detail
    assert "0.00003000" not in detail  # keine Rohdarstellung
    assert len(detail) < 160
