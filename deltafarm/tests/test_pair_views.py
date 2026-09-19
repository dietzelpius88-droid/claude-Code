"""Gemeinsame Paaransicht: Uebernahme, Kennzahlen, Warnungen.

Von Hand eroeffnete Paare und ueber die Vorschau geoeffnete sollen in der
Oberflaeche nicht zwei verschiedene Dinge sein. Nach der Uebernahme gibt es
nur noch einen Codepfad.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.account import AccountService
from app.models.domain import Balance, FundingPayment, HealthLevel, PairState, Position, Side
from app.storage.db import create_all, make_engine, make_session_factory
from app.storage.journal import Journal
from app.storage.models import PairSource
from app.storage.pairs import PairStore

JETZT = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _umgebung():
    engine = make_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    return PairStore(factory), Journal(factory)


def _pos(venue: str, side: Side, notional: str = "32000", liq: str | None = "40000") -> Position:
    return Position(
        venue=venue,
        symbol="BTC-PERP",
        side=side,
        size=Decimal(notional) / Decimal(64000),
        entry_price=Decimal(64000),
        mark_price=Decimal(64000),
        notional=Decimal(notional),
        unrealised_pnl=Decimal(25),
        liquidation_price=Decimal(liq) if liq else None,
        as_of=JETZT,
    )


RATEN = {("lighter", "BTC-PERP"): Decimal("-0.00001"), ("extended", "BTC-PERP"): Decimal("0.00002")}


# --- Uebernahme -----------------------------------------------------------

def test_offene_positionen_werden_als_paar_uebernommen():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)

    dienst.adopt_open_pairs(
        store,
        [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")],
    )

    paare = store.all_pairs()
    assert len(paare) == 1
    assert paare[0].source == PairSource.ADOPTED.value
    assert paare[0].status == PairState.OPEN.value


def test_uebernahme_geschieht_nur_einmal():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]

    dienst.adopt_open_pairs(store, positionen)
    dienst.adopt_open_pairs(store, positionen)

    assert len(store.all_pairs()) == 1


def test_ein_bereits_gefuehrtes_paar_wird_nicht_uebernommen():
    store, journal = _umgebung()
    pair_id = store.create_pair(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        notional_usd=Decimal(6400),
        size=Decimal("0.1"),
        first_venue="lighter",
    )
    store.set_state(pair_id, PairState.OPEN)

    AccountService([], journal=journal).adopt_open_pairs(
        store, [_pos("lighter", Side.LONG), _pos("extended", Side.SHORT)]
    )

    paare = store.all_pairs()
    assert len(paare) == 1
    assert paare[0].source == PairSource.ENGINE.value


def test_einzelnes_bein_wird_nicht_uebernommen():
    """Eine ungesicherte Position ist kein Paar - sie soll auffallen, nicht
    stillschweigend zu einem Paar erklaert werden."""
    store, journal = _umgebung()
    AccountService([], journal=journal).adopt_open_pairs(store, [_pos("lighter", Side.LONG)])
    assert store.all_pairs() == []


# --- Kennzahlen -----------------------------------------------------------

def test_ansicht_nennt_netto_funding_pro_stunde_und_apr():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    dienst.adopt_open_pairs(store, positionen)

    ansichten = dienst.build_pair_views(store, positionen, funding_rates=RATEN)

    assert len(ansichten) == 1
    a = ansichten[0]
    assert a.net_funding_hourly == Decimal("0.00003")
    assert a.net_funding_apr == Decimal("0.26280")


def test_ansicht_nennt_gezahlte_gebuehren():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    pair_id = dienst.adopt_open_pairs(store, positionen)[0]

    # Uebernommene Paare haben keine eigenen Orders - die Gebuehren sind
    # deshalb unbekannt, nicht null.
    a = dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0]
    assert a.fees_paid == Decimal(0)
    assert a.fees_known is False
    assert pair_id


def test_uebernommenes_paar_hat_keine_erfundene_startzeit():
    """Nennt die Boerse keinen Eroeffnungszeitpunkt, bleibt er leer.

    Auf "jetzt" gesetzt fiele alles Funding von vor der Uebernahme aus der
    Zuordnung - obwohl es zu dieser Position gehoert.
    """
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    pair_id = dienst.adopt_open_pairs(store, positionen)[0]

    assert store.get_pair(pair_id).opened_at is None
    assert dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0].holding_hours is None


def test_ansicht_zaehlt_zugeordnetes_funding():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    pair_id = dienst.adopt_open_pairs(store, positionen)[0]

    journal.record_funding_payments([
        FundingPayment(
            venue="lighter", symbol="BTC-PERP", amount=Decimal("4.25"),
            timestamp=JETZT, external_id="a",
        )
    ])
    # Ohne bekannte Startzeit wird alles passende Funding zugeordnet.
    assert store.attribute_funding(pair_id) == 1

    a = dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0]
    assert a.funding_received == Decimal("4.25")


# --- Warnungen (Auftrag 6.4) ---------------------------------------------

def test_warnung_wenn_das_netto_funding_das_vorzeichen_dreht():
    """Genau dann muesste man schliessen - und genau dann sagt die alte
    Ansicht nichts."""
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    dienst.adopt_open_pairs(store, positionen)

    gedreht = {
        ("lighter", "BTC-PERP"): Decimal("0.00002"),
        ("extended", "BTC-PERP"): Decimal("-0.00001"),
    }
    a = dienst.build_pair_views(store, positionen, funding_rates=gedreht)[0]

    assert a.funding_negative is True
    assert a.level is HealthLevel.RED


def test_keine_warnung_bei_positivem_netto_funding():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    dienst.adopt_open_pairs(store, positionen)

    a = dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0]
    assert a.funding_negative is False


def test_warnung_solange_die_kosten_nicht_eingespielt_sind():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    pair_id = dienst.adopt_open_pairs(store, positionen)[0]

    a = dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0]
    # Ohne bekannte Gebuehren wird nichts behauptet.
    assert a.costs_recovered is None
    assert pair_id


def test_fehlende_positionen_zu_einem_gefuehrten_paar_fallen_auf():
    """Das Paar steht in der Datenbank, an der Boerse ist nichts mehr offen."""
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    pair_id = store.create_pair(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        notional_usd=Decimal(6400),
        size=Decimal("0.1"),
        first_venue="lighter",
    )
    store.set_state(pair_id, PairState.OPEN)

    a = dienst.build_pair_views(store, [], funding_rates=RATEN)[0]

    assert a.positions_missing is True
    assert a.level is HealthLevel.RED


def test_geschlossene_paare_erscheinen_nicht_in_der_ansicht():
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    pair_id = store.create_pair(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        notional_usd=Decimal(6400),
        size=Decimal("0.1"),
        first_venue="lighter",
    )
    store.set_state(pair_id, PairState.CLOSED)

    assert dienst.build_pair_views(store, [], funding_rates=RATEN) == []


def test_haltedauer_ueberlebt_den_weg_durch_die_datenbank():
    """Regressionstest fuer einen Zeitzonenfehler.

    Geschrieben wird ein zeitzonenbewusster Zeitstempel, SQLite gibt ihn naiv
    zurueck. Die Subtraktion warf deshalb - und zwar nicht nur im Test,
    sondern bei jedem Aufruf von /api/pairs mit einem offenen Paar.
    """
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)

    pair_id = store.create_pair(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        notional_usd=Decimal(6400),
        size=Decimal("0.1"),
        first_venue="lighter",
    )
    store.set_state(pair_id, PairState.OPEN)  # setzt opened_at auf jetzt

    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    ansicht = dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0]

    assert ansicht.holding_hours is not None
    assert ansicht.holding_hours >= 0


def test_funding_wird_auch_bei_offenem_paar_zugeordnet():
    """Sonst steht auf der Karte eines offenen Paares immer null Funding.

    Die Zahlungen liegen laengst im Journal - sie waren nur keinem Paar
    zugeordnet, weil das bisher erst beim Schliessen geschah.
    """
    store, journal = _umgebung()
    dienst = AccountService([], journal=journal)
    positionen = [_pos("lighter", Side.LONG, liq="40000"), _pos("extended", Side.SHORT, liq="90000")]
    dienst.adopt_open_pairs(store, positionen)

    journal.record_funding_payments([
        FundingPayment(
            venue="extended", symbol="BTC-PERP", amount=Decimal("2.75"),
            timestamp=JETZT, external_id="offen-1",
        )
    ])

    # Ohne ausdruecklichen attribute_funding-Aufruf von aussen.
    ansicht = dienst.build_pair_views(store, positionen, funding_rates=RATEN)[0]
    assert ansicht.funding_received == Decimal("2.75")
