"""API-Routen gegen MockAdapter."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter, failing_adapter
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    # Eigene Datenbankdatei je Test, damit das Projektverzeichnis sauber
    # bleibt und Tests sich nicht gegenseitig sehen.
    adapters = [
        MockAdapter(
            "extended",
            rates={"BTC-PERP": Decimal("0.00002"), "ETH-PERP": Decimal("0.00001")},
            taker_fee=Decimal("0.0004"),
        ),
        MockAdapter(
            "lighter",
            rates={"BTC-PERP": Decimal("-0.00001"), "ETH-PERP": Decimal("0.00001")},
            taker_fee=Decimal("0.0002"),
        ),
    ]
    app = create_app(adapters=adapters, start_background=False, db_path=str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    antwort = client.get("/api/health")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["status"] == "ok"
    assert daten["dry_run"] is True          # Trockenlauf ist Standard
    assert daten["environment"] == "testnet"  # Testnet ist Standard


def test_venues(client: TestClient):
    daten = client.get("/api/venues").json()
    assert {v["name"] for v in daten} == {"extended", "lighter"}
    assert all(v["reachable"] for v in daten)
    assert all(v["supports_trading"] is False for v in daten)  # Phase 1


def test_markets(client: TestClient):
    daten = client.get("/api/markets").json()
    assert {m["venue"] for m in daten} == {"extended", "lighter"}


def test_funding_matrix(client: TestClient):
    daten = client.get("/api/funding").json()
    assert daten["venues"] == ["extended", "lighter"]

    zeilen = {z["symbol"]: z for z in daten["rows"]}
    assert set(zeilen) == {"BTC-PERP", "ETH-PERP"}

    btc = zeilen["BTC-PERP"]
    assert btc["best"]["short_venue"] == "extended"
    assert btc["best"]["long_venue"] == "lighter"


def test_decimalwerte_gehen_als_string_ueber_die_leitung(client: TestClient):
    """Sonst waere die Rate im Browser wieder ein double.

    Die ganze Decimal-Vorgabe waere an der Schnittstelle aufgegeben, ohne dass
    man es der Anzeige ansieht.
    """
    roh = client.get("/api/funding").text
    daten = client.get("/api/funding").json()
    rate = daten["rows"][0]["rates"]["extended"]["rate_hourly"]

    assert isinstance(rate, str), f"rate_hourly kam als {type(rate).__name__} statt als String"
    assert '"rate_hourly":"0.00002"' in roh.replace(" ", "")


def test_opportunities_sortiert_und_mit_breakeven(client: TestClient):
    daten = client.get("/api/opportunities").json()
    assert [o["symbol"] for o in daten] == ["BTC-PERP", "ETH-PERP"]

    btc = daten[0]
    assert btc["cost_basis_known"] is True
    # (0,0004 + 0,0002) * 2 / 0,00003 = 40 Stunden
    assert Decimal(btc["breakeven_hours"]) == Decimal(40)


def test_symbol_kann_gefiltert_werden(client: TestClient):
    daten = client.get("/api/funding", params={"symbol": "BTC-PERP"}).json()
    assert [z["symbol"] for z in daten["rows"]] == ["BTC-PERP"]


def test_ausgefallene_boerse_laesst_die_api_stehen(tmp_path):
    adapters = [
        MockAdapter("extended", rates={"BTC-PERP": Decimal("0.00002")}),
        failing_adapter("lighter"),
    ]
    with TestClient(
        create_app(adapters=adapters, start_background=False, db_path=str(tmp_path / "t.db"))
    ) as c:
        venues = c.get("/api/venues").json()
        assert {v["name"]: v["reachable"] for v in venues} == {
            "extended": True,
            "lighter": False,
        }
        assert c.get("/api/funding").json()["rows"][0]["best"] is None


def test_gefilterte_abfrage_nutzt_den_zwischenspeicher(client: TestClient):
    """Ein Filter darf keinen neuen Boersenabruf ausloesen.

    Sonst wuerde jede Eingabe im Suchfeld beide Boersen erneut befragen - und
    bei einer langsamen Boerse die Oberflaeche blockieren.
    """
    client.get("/api/funding")  # fuellt den Zwischenspeicher
    adapter = client.app.state.service.adapters[0]
    vorher = len(adapter.calls)

    daten = client.get("/api/funding", params={"symbol": "BTC-PERP"}).json()

    assert [z["symbol"] for z in daten["rows"]] == ["BTC-PERP"]
    assert len(adapter.calls) == vorher  # kein weiterer Aufruf


def test_venues_bleiben_bei_gefilterter_abfrage_vollstaendig(client: TestClient):
    daten = client.get("/api/funding", params={"symbol": "BTC-PERP"}).json()
    assert daten["venues"] == ["extended", "lighter"]


def test_ohne_daten_werden_die_boersen_trotzdem_aufgefuehrt(tmp_path):
    """Die Warnung verweist auf die Kopfzeile - also muss dort etwas stehen.

    Antwortet beim Erstabruf keine Boerse rechtzeitig, zeigt die Oberflaeche
    beide als nicht erreichbar an, statt eine leere Seite.
    """
    import app.api.routes as routes

    original = routes.FIRST_LOAD_TIMEOUT_SECONDS
    routes.FIRST_LOAD_TIMEOUT_SECONDS = 0.01
    try:
        langsam = [MockAdapter("extended"), MockAdapter("lighter")]
        for a in langsam:
            a._fail_with = None

        async def nie_fertig(*_args, **_kwargs):
            import asyncio

            await asyncio.sleep(5)

        for a in langsam:
            a.list_markets = nie_fertig  # type: ignore[method-assign]
            a.get_funding = nie_fertig  # type: ignore[method-assign]

        with TestClient(
            create_app(adapters=langsam, start_background=False, db_path=str(tmp_path / "l.db"))
        ) as c:
            daten = c.get("/api/funding").json()
            assert daten["rows"] == []
            assert "Kopfzeile" in daten["warnings"][0]

            venues = c.get("/api/venues").json()
            assert {v["name"] for v in venues} == {"extended", "lighter"}
            assert all(v["reachable"] is False for v in venues)
    finally:
        routes.FIRST_LOAD_TIMEOUT_SECONDS = original
