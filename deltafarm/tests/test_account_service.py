"""Account-Dienstschicht: Paarerkennung, Ampel, Journalabgleich."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.adapters.mock import MockAdapter
from app.core.account import AccountService
from app.models.domain import Balance, FundingPayment, HealthLevel, Position, Side
from app.storage.db import create_all, make_engine, make_session_factory
from app.storage.journal import Journal

JETZT = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def _pos(venue: str, side: Side, notional: str, *, liq: str | None = None, opened=None) -> Position:
    return Position(
        venue=venue,
        symbol="BTC-PERP",
        side=side,
        size=Decimal(notional) / Decimal(64000),
        entry_price=Decimal(64000),
        mark_price=Decimal(64000),
        notional=Decimal(notional),
        liquidation_price=Decimal(liq) if liq else None,
        opened_at=opened,
        as_of=JETZT,
    )


def _journal() -> Journal:
    engine = make_engine(":memory:")
    create_all(engine)
    return Journal(make_session_factory(engine))


# --- Paarerkennung --------------------------------------------------------

def test_gegenlaeufige_positionen_werden_als_paar_erkannt():
    dienst = AccountService([])
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "32000", liq="40000"),
        _pos("lighter", Side.SHORT, "32000", liq="90000"),
    ])

    assert len(paare) == 1
    p = paare[0]
    assert p.hedged is True
    assert p.net_delta_usd == Decimal(0)
    assert p.level is HealthLevel.GREEN


def test_einzelnes_bein_ist_nie_gruen():
    """Eine Position ohne Gegenseite ist eine ungesicherte Richtungswette.

    Genau das soll in der Oberflaeche sofort auffallen.
    """
    dienst = AccountService([])
    paare = dienst.detect_pairs([_pos("extended", Side.LONG, "32000", liq="40000")])

    assert paare[0].hedged is False
    assert paare[0].level is HealthLevel.RED


def test_zwei_gleichgerichtete_beine_gelten_nicht_als_gehedgt():
    dienst = AccountService([])
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "32000", liq="40000"),
        _pos("lighter", Side.LONG, "32000", liq="40000"),
    ])
    assert paare[0].hedged is False
    assert paare[0].net_delta_usd == Decimal(64000)


def test_rest_delta_ueber_der_schwelle_faerbt_gelb():
    dienst = AccountService([], delta_warn_pct=Decimal("0.02"))
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "33000", liq="40000"),
        _pos("lighter", Side.SHORT, "32000", liq="90000"),
    ])
    # 1000 auf 32000 = 3,1 % Rest-Delta
    assert paare[0].hedged is True
    assert paare[0].level is HealthLevel.YELLOW


def test_knapper_liquidationsabstand_schlaegt_auf_das_ganze_paar_durch():
    dienst = AccountService([])
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "32000", liq="63000"),  # nur 1,6 % Puffer
        _pos("lighter", Side.SHORT, "32000", liq="90000"),
    ])
    assert paare[0].level is HealthLevel.RED


def test_margin_auslastung_fliesst_aus_dem_kontostand_ein():
    dienst = AccountService([])
    kontostaende = [
        Balance(
            venue="extended",
            collateral="USD",
            equity=Decimal(10000),
            available=Decimal(500),
            initial_margin=Decimal(9500),  # 95 % Auslastung
            as_of=JETZT,
        )
    ]
    paare = dienst.detect_pairs(
        [
            _pos("extended", Side.LONG, "32000", liq="40000"),
            _pos("lighter", Side.SHORT, "32000", liq="90000"),
        ],
        kontostaende,
    )
    assert paare[0].level is HealthLevel.RED


def test_verschiedene_symbole_ergeben_verschiedene_paare():
    dienst = AccountService([])
    a = _pos("extended", Side.LONG, "32000", liq="40000")
    b = _pos("lighter", Side.SHORT, "32000", liq="90000")
    c = b.model_copy(update={"symbol": "ETH-PERP"})
    paare = dienst.detect_pairs([a, b, c])
    assert {p.symbol for p in paare} == {"BTC-PERP", "ETH-PERP"}


def test_haltedauer_wenn_die_boerse_den_zeitpunkt_nennt():
    dienst = AccountService([])
    vor_drei_stunden = datetime.now(timezone.utc) - timedelta(hours=3)
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "32000", liq="40000", opened=vor_drei_stunden),
        _pos("lighter", Side.SHORT, "32000", liq="90000"),
    ])
    assert paare[0].holding_hours is not None
    assert Decimal("2.9") <= paare[0].holding_hours <= Decimal("3.1")


def test_ohne_zeitpunkt_keine_erfundene_haltedauer():
    dienst = AccountService([])
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "32000", liq="40000"),
        _pos("lighter", Side.SHORT, "32000", liq="90000"),
    ])
    assert paare[0].holding_hours is None


# --- Journal --------------------------------------------------------------

def test_erhaltenes_funding_kommt_aus_dem_journal():
    j = _journal()
    j.record_funding_payments([
        FundingPayment(
            venue="lighter",
            symbol="BTC-PERP",
            amount=Decimal("4.25"),
            timestamp=JETZT,
            external_id="a",
        )
    ])
    dienst = AccountService([], journal=j)
    paare = dienst.detect_pairs([
        _pos("extended", Side.LONG, "32000", liq="40000"),
        _pos("lighter", Side.SHORT, "32000", liq="90000"),
    ])
    assert paare[0].funding_received == Decimal("4.25")


# --- Abrufe gegen Adapter -------------------------------------------------

async def test_ohne_account_zugang_bleibt_die_liste_leer_statt_zu_werfen():
    # MockAdapter ohne Account-Methoden - ReadOnlyAdapter wirft dort
    # TradingNotSupported, was hier bewusst geschluckt wird.
    dienst = AccountService([MockAdapter("extended")])
    assert await dienst.balances() == []
    assert await dienst.positions() == []


async def test_zugangsstatus_wird_gemeldet():
    class MitZugang(MockAdapter):
        has_account_access = True

    assert AccountService([MockAdapter("a")]).has_any_account_access is False
    assert AccountService([MitZugang("b")]).has_any_account_access is True


async def test_sync_schreibt_zahlungen_und_snapshots_ins_journal():
    class MitDaten(MockAdapter):
        has_account_access = True

        async def get_balance(self):
            return Balance(
                venue=self.name,
                collateral="USD",
                equity=Decimal(10000),
                available=Decimal(8000),
                as_of=JETZT,
            )

        async def get_funding_payments(self, since):
            return [
                FundingPayment(
                    venue=self.name,
                    symbol="BTC-PERP",
                    amount=Decimal("1.25"),
                    timestamp=JETZT,
                    external_id="z1",
                )
            ]

    j = _journal()
    dienst = AccountService([MitDaten("lighter")], journal=j)

    ergebnis = await dienst.sync()
    assert ergebnis == {"funding_neu": 1, "snapshots": 1}

    # Zweiter Lauf darf nicht doppelt zaehlen.
    ergebnis = await dienst.sync()
    assert ergebnis["funding_neu"] == 0
    assert j.funding_total(venue="lighter") == Decimal("1.25")
