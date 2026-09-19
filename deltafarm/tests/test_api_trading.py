"""API der Phase 3: Vorschau, Ausfuehrung, Kill Switch.

Jede Order geht auf einen Klick zurueck - und jeder Klick auf eine Vorschau,
die noch gueltig ist.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app
from app.models.domain import Balance, PairState

JETZT = datetime(2026, 9, 19, tzinfo=timezone.utc)


class HandelsAdapter(MockAdapter):
    """MockAdapter mit Kontostand, damit der Margin-Check durchlaeuft."""

    has_account_access = True

    async def get_balance(self) -> Balance:
        return Balance(
            venue=self.name,
            collateral="USD",
            equity=Decimal(100000),
            available=Decimal(100000),
            as_of=JETZT,
        )


def _adapters(**optionen):
    return [
        HandelsAdapter(
            "lighter",
            supports_trading=True,
            rates={"BTC-PERP": Decimal("-0.00001")},
            mark_prices={"BTC-PERP": Decimal(64000)},
            taker_fee=Decimal("0.0001"),
            min_notional=Decimal(10),
            lot_size=Decimal("0.001"),
            **optionen.get("lighter", {}),
        ),
        HandelsAdapter(
            "extended",
            supports_trading=True,
            rates={"BTC-PERP": Decimal("0.00002")},
            mark_prices={"BTC-PERP": Decimal(64000)},
            taker_fee=Decimal("0.0005"),
            min_notional=Decimal(10),
            lot_size=Decimal("0.001"),
            **optionen.get("extended", {}),
        ),
    ]


def _client(tmp_path, **optionen) -> TestClient:
    app = create_app(
        adapters=_adapters(**optionen),
        start_background=False,
        db_path=str(tmp_path / "handel.db"),
    )
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with _client(tmp_path) as c:
        yield c


VORSCHAU = {
    "symbol": "BTC-PERP",
    "notional_usd": "6400",
    "long_venue": "lighter",
    "short_venue": "extended",
    "long_leverage": "5",
    "short_leverage": "5",
    "first_venue": "lighter",
    "hedge_timeout_seconds": 1.0,
}


# --- Vorschau -------------------------------------------------------------

def test_vorschau_liefert_groessen_und_pruefliste(client: TestClient):
    daten = client.post("/api/pairs/preview", json=VORSCHAU).json()

    assert daten["ok"] is True
    assert daten["token"]
    assert Decimal(daten["sizing"]["size"]) == Decimal("0.1")
    assert {c["key"] for c in daten["checks"]} == {
        "erreichbarkeit",
        "margin",
        "preisabweichung",
        "tiefe_slippage",
        "funding_vorzeichen",
        "breakeven",
    }


def test_vorschau_sendet_keine_order(client: TestClient):
    client.post("/api/pairs/preview", json=VORSCHAU)
    adapter = client.app.state.trading._adapters["lighter"]
    assert adapter.placed_orders == []


def test_vorschau_nennt_rest_delta_und_gebuehren(client: TestClient):
    s = client.post("/api/pairs/preview", json=VORSCHAU).json()["sizing"]
    assert Decimal(s["residual_delta_usd"]) == Decimal(0)  # gleiche Mark-Preise
    assert Decimal(s["estimated_round_trip_fees"]) > 0


def test_falsche_richtung_blockiert_die_vorschau(client: TestClient):
    verdreht = {**VORSCHAU, "long_venue": "extended", "short_venue": "lighter"}
    daten = client.post("/api/pairs/preview", json=verdreht).json()

    assert daten["ok"] is False
    assert any("Funding" in g for g in daten["blocking_reasons"])


def test_zu_kleiner_betrag_liefert_einen_verstaendlichen_fehler(client: TestClient):
    winzig = {**VORSCHAU, "notional_usd": "5"}
    daten = client.post("/api/pairs/preview", json=winzig).json()

    assert daten["ok"] is False
    assert daten["error"]
    assert daten["token"] is None


# --- Ausfuehrung ----------------------------------------------------------

def test_ausfuehrung_oeffnet_beide_beine(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    daten = client.post("/api/pairs/open", json={"token": token}).json()

    assert daten["state"] == PairState.OPEN.value
    assert daten["dry_run"] is True


def test_unbekannter_token_wird_abgelehnt(client: TestClient):
    antwort = client.post("/api/pairs/open", json={"token": "gibtsnicht"})
    assert antwort.status_code == 409


def test_token_ist_nur_einmal_gueltig(client: TestClient):
    """Ein zweiter Klick darf keine zweite Position eroeffnen."""
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    assert client.post("/api/pairs/open", json={"token": token}).status_code == 200

    zweiter = client.post("/api/pairs/open", json={"token": token})
    assert zweiter.status_code == 409


def test_abgelaufene_vorschau_wird_abgelehnt(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]

    # Die Vorschau kuenstlich altern lassen.
    speicher = client.app.state.previews
    vorschau = speicher.get(token)
    from dataclasses import replace
    from datetime import timedelta

    speicher._previews[token] = replace(
        vorschau, created_at=vorschau.created_at - timedelta(seconds=120)
    )

    antwort = client.post("/api/pairs/open", json={"token": token})
    assert antwort.status_code == 409
    assert "alt" in antwort.json()["detail"]


def test_blockierte_vorschau_wird_nicht_ausgefuehrt(client: TestClient):
    verdreht = {**VORSCHAU, "long_venue": "extended", "short_venue": "lighter"}
    token = client.post("/api/pairs/preview", json=verdreht).json()["token"]

    antwort = client.post("/api/pairs/open", json={"token": token})
    assert antwort.status_code == 400
    assert "Preflight" in antwort.json()["detail"]


def test_preisbewegung_zwischen_vorschau_und_klick_bricht_ab(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]

    # Der Markt laeuft weg.
    client.app.state.trading._adapters["lighter"]._marks["BTC-PERP"] = Decimal(70000)

    antwort = client.post("/api/pairs/open", json={"token": token})
    assert antwort.status_code == 409
    assert "bewegt" in antwort.json()["detail"]


# --- Paare, Schliessen, Aufloesen -----------------------------------------

def test_paarliste_zeigt_beide_beine(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    client.post("/api/pairs/open", json={"token": token})

    paare = client.get("/api/pairs").json()
    assert len(paare) == 1
    assert paare[0]["status"] == PairState.OPEN.value
    assert {b["venue"] for b in paare[0]["legs"]} == {"lighter", "extended"}


def test_paar_schliessen(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    pair_id = client.post("/api/pairs/open", json={"token": token}).json()["pair_id"]

    daten = client.post(f"/api/pairs/{pair_id}/close").json()
    assert daten["state"] == PairState.CLOSED.value


def test_unbekanntes_paar_ergibt_404(client: TestClient):
    assert client.post("/api/pairs/9999/close").status_code == 404


def test_unhedged_kann_zurueckgerollt_werden(tmp_path):
    with _client(tmp_path, extended={"reject_orders": True}) as c:
        token = c.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
        ergebnis = c.post("/api/pairs/open", json={"token": token}).json()
        assert ergebnis["state"] == PairState.UNHEDGED.value

        nachher = c.post(
            f"/api/pairs/{ergebnis['pair_id']}/resolve", json={"action": "rollback"}
        ).json()
        assert nachher["state"] == PairState.CLOSED.value


def test_unbekannte_aufloesung_wird_abgelehnt(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    pair_id = client.post("/api/pairs/open", json={"token": token}).json()["pair_id"]

    antwort = client.post(f"/api/pairs/{pair_id}/resolve", json={"action": "quatsch"})
    assert antwort.status_code == 400


# --- Kill Switch ----------------------------------------------------------

def test_kill_switch_schliesst_alles(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    pair_id = client.post("/api/pairs/open", json={"token": token}).json()["pair_id"]

    daten = client.post("/api/panic").json()

    assert daten["closed_positions"] == 2
    assert daten["dry_run"] is True
    assert client.get("/api/pairs").json()[0]["status"] == PairState.CLOSED.value
    assert pair_id


def test_kill_switch_ohne_positionen_ist_folgenlos(client: TestClient):
    daten = client.post("/api/panic").json()
    assert daten["closed_positions"] == 0


# --- Journal --------------------------------------------------------------

def test_vorschau_und_ausfuehrung_stehen_im_journal(client: TestClient):
    token = client.post("/api/pairs/preview", json=VORSCHAU).json()["token"]
    client.post("/api/pairs/open", json={"token": token})

    arten = {e["kind"] for e in client.get("/api/journal").json()}
    assert "vorschau" in arten
    assert "zustand" in arten
