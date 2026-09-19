"""API der Phase 2: Kontostaende, Positionen, Journal - und die Asset-Pruefung."""

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app
from app.models.domain import Balance, FundingPayment, Position, Side

JETZT = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


class KontoAdapter(MockAdapter):
    """MockAdapter mit Account-Zugang."""

    has_account_access = True

    def __init__(self, name, *, position=None, equity="10000", **kwargs):
        super().__init__(name, **kwargs)
        self._position = position
        self._equity = Decimal(equity)

    async def get_balance(self):
        return Balance(
            venue=self.name,
            collateral="USD",
            equity=self._equity,
            available=self._equity / 2,
            initial_margin=self._equity / 5,
            as_of=JETZT,
        )

    async def get_positions(self):
        return [self._position] if self._position else []

    async def get_funding_payments(self, since):
        return [
            FundingPayment(
                venue=self.name,
                symbol="BTC-PERP",
                amount=Decimal("1.25"),
                timestamp=JETZT,
                external_id=f"{self.name}-1",
            )
        ]


def _position(venue: str, side: Side, liq: str) -> Position:
    return Position(
        venue=venue,
        symbol="BTC-PERP",
        side=side,
        size=Decimal("0.5"),
        entry_price=Decimal(64000),
        mark_price=Decimal(64000),
        notional=Decimal(32000),
        unrealised_pnl=Decimal(25),
        liquidation_price=Decimal(liq),
        as_of=JETZT,
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    adapters = [
        KontoAdapter(
            "extended",
            rates={"BTC-PERP": Decimal("0.00002")},
            mark_prices={"BTC-PERP": Decimal(64000)},
            position=_position("extended", Side.LONG, "40000"),
        ),
        KontoAdapter(
            "lighter",
            rates={"BTC-PERP": Decimal("-0.00001")},
            mark_prices={"BTC-PERP": Decimal(64010)},
            position=_position("lighter", Side.SHORT, "90000"),
        ),
    ]
    app = create_app(
        adapters=adapters, start_background=False, db_path=str(tmp_path / "test.db")
    )
    with TestClient(app) as c:
        yield c


# --- Kontostaende ---------------------------------------------------------

def test_balances(client: TestClient):
    daten = client.get("/api/balances").json()
    assert {b["venue"] for b in daten} == {"extended", "lighter"}
    assert Decimal(daten[0]["equity"]) == Decimal(10000)
    assert Decimal(daten[0]["margin_usage"]) == Decimal("0.2")


def test_balances_als_string_ueber_die_leitung(client: TestClient):
    assert isinstance(client.get("/api/balances").json()[0]["equity"], str)


def test_ohne_zugang_bleibt_die_liste_leer(tmp_path):
    ohne = [MockAdapter("extended"), MockAdapter("lighter")]
    with TestClient(
        create_app(adapters=ohne, start_background=False, db_path=str(tmp_path / "o.db"))
    ) as c:
        assert c.get("/api/balances").json() == []


# --- Positionen als Paare -------------------------------------------------

def test_gegenlaeufige_positionen_erscheinen_als_gehedgtes_paar(client: TestClient):
    daten = client.get("/api/positions").json()
    assert len(daten) == 1
    p = daten[0]
    assert p["symbol"] == "BTC-PERP"
    assert p["hedged"] is True
    assert len(p["legs"]) == 2
    assert Decimal(p["net_delta_usd"]) == Decimal(0)
    assert p["level"] == "GREEN"


def test_beine_tragen_ampel_und_liquidationsabstand(client: TestClient):
    p = client.get("/api/positions").json()[0]
    lang = next(b for b in p["legs"] if b["side"] == "LONG")
    # Mark 64000, Liquidation 40000 -> 37,5 % Puffer
    assert Decimal(lang["health"]["liquidation_distance"]) == Decimal("0.375")
    assert lang["health"]["level"] == "GREEN"


def test_einzelnes_bein_wird_als_ungehedgt_gemeldet(tmp_path):
    adapters = [
        KontoAdapter(
            "extended",
            rates={"BTC-PERP": Decimal("0.00002")},
            position=_position("extended", Side.LONG, "40000"),
        ),
        KontoAdapter("lighter", rates={"BTC-PERP": Decimal("-0.00001")}, position=None),
    ]
    with TestClient(
        create_app(adapters=adapters, start_background=False, db_path=str(tmp_path / "u.db"))
    ) as c:
        p = c.get("/api/positions").json()[0]
        assert p["hedged"] is False
        assert p["level"] == "RED"


# --- Journal --------------------------------------------------------------

def test_journal_enthaelt_den_start(client: TestClient):
    eintraege = client.get("/api/journal").json()
    assert any(e["kind"] == "start" for e in eintraege)


def test_journal_als_csv(client: TestClient):
    antwort = client.get("/api/journal", params={"format": "csv"})
    assert antwort.status_code == 200
    assert "text/csv" in antwort.headers["content-type"]
    assert "attachment" in antwort.headers["content-disposition"]

    zeilen = list(csv.DictReader(io.StringIO(antwort.text)))
    assert any(z["art"] == "ereignis" for z in zeilen)


def test_unbekanntes_format_wird_abgelehnt(client: TestClient):
    assert client.get("/api/journal", params={"format": "xml"}).status_code == 422


# --- Asset-Pruefung -------------------------------------------------------

def test_gleicher_ticker_bei_unterschiedlichen_preisen_ergibt_kein_paar(tmp_path):
    """Der Fall, der auf einem fremden Werkzeug falsch lief.

    Zwei Boersen fuehren ein LIT-PERP, aber zu Preisen, die nicht dasselbe
    Asset sein koennen. Dann darf kein Vergleich stattfinden.
    """
    adapters = [
        MockAdapter(
            "extended",
            rates={"BTC-PERP": Decimal("0.00009")},
            mark_prices={"BTC-PERP": Decimal("1.50")},
        ),
        MockAdapter(
            "lighter",
            rates={"BTC-PERP": Decimal("-0.00009")},
            mark_prices={"BTC-PERP": Decimal("18.30")},
        ),
    ]
    with TestClient(
        create_app(adapters=adapters, start_background=False, db_path=str(tmp_path / "a.db"))
    ) as c:
        matrix = c.get("/api/funding").json()

        zeile = matrix["rows"][0]
        assert zeile["best"] is None, "Ein nicht bestaetigtes Asset darf kein Vorschlag sein"
        assert any("verschiedene Assets" in w for w in matrix["warnings"])

        chancen = c.get("/api/opportunities").json()
        assert chancen[0]["asset_match"] is False
        assert Decimal(chancen[0]["price_deviation"]) > Decimal(11)


def test_aehnliche_preise_ergeben_ein_gueltiges_paar(client: TestClient):
    matrix = client.get("/api/funding").json()
    zeile = matrix["rows"][0]
    assert zeile["best"] is not None
    assert zeile["best"]["asset_match"] is True
    assert not any("verschiedene Assets" in w for w in matrix["warnings"])
