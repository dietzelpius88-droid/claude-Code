"""Ausfuehrungsengine gegen den MockAdapter.

Die Faelle aus Auftrag Abschnitt 10: zweites Bein abgelehnt, zweites Bein im
Timeout, Teilfuellung, Absturz zwischen den Beinen mit Wiederanlauf aus der
Datenbank.

Der gefaehrliche Zustand ist UNHEDGED - ein Bein steht, das andere nicht. Er
darf nie stillschweigend entstehen und nie stillschweigend vergehen.
"""

from decimal import Decimal

import pytest

from app.adapters.mock import MockAdapter
from app.core.execution import ExecutionEngine, ExecutionPlan
from app.models.domain import OrderStatus, PairState, Side
from app.storage.db import create_all, make_engine, make_session_factory
from app.storage.journal import Journal
from app.storage.pairs import PairStore


def _umgebung(**adapter_optionen):
    """Baut Engine, Speicher und zwei Adapter."""
    engine = make_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    store = PairStore(factory)
    journal = Journal(factory)

    lang = MockAdapter(
        "lighter",
        supports_trading=True,
        mark_prices={"BTC-PERP": Decimal(64000)},
        **adapter_optionen.get("lighter", {}),
    )
    kurz = MockAdapter(
        "extended",
        supports_trading=True,
        mark_prices={"BTC-PERP": Decimal(64000)},
        **adapter_optionen.get("extended", {}),
    )
    motor = ExecutionEngine(
        adapters={"lighter": lang, "extended": kurz},
        store=store,
        journal=journal,
        dry_run=True,
    )
    return motor, store, lang, kurz


def _plan(**ueberschreiben) -> ExecutionPlan:
    vorgabe = dict(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        size=Decimal("0.1"),
        notional_usd=Decimal(6400),
        first_venue="lighter",
        hedge_timeout_seconds=0.5,
    )
    vorgabe.update(ueberschreiben)
    return ExecutionPlan(**vorgabe)


# --- Erfolgsfall ----------------------------------------------------------

async def test_beide_beine_gefuellt_ergibt_ein_offenes_paar():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    assert ergebnis.state is PairState.OPEN
    assert store.get_state(ergebnis.pair_id) is PairState.OPEN
    assert len(lang.placed_orders) == 1
    assert len(kurz.placed_orders) == 1


async def test_konfigurierte_reihenfolge_wird_eingehalten():
    """Das langsamere Bein zuerst - nicht geraten, sondern konfiguriert."""
    motor, store, lang, kurz = _umgebung()
    await motor.open_pair(_plan(first_venue="extended"))

    # Der Zustandsverlauf zeigt, welche Boerse zuerst dran war.
    assert kurz.placed_orders[0].venue == "extended"
    assert lang.placed_orders[0].venue == "lighter"


async def test_richtungen_stimmen():
    motor, store, lang, kurz = _umgebung()
    await motor.open_pair(_plan())

    assert lang.placed_orders[0].side is Side.LONG
    assert kurz.placed_orders[0].side is Side.SHORT


async def test_jede_order_bekommt_eine_eigene_client_order_id():
    motor, store, lang, kurz = _umgebung()
    await motor.open_pair(_plan())

    ids = {lang.placed_orders[0].client_order_id, kurz.placed_orders[0].client_order_id}
    assert len(ids) == 2
    assert all(i for i in ids)


# --- Erstes Bein scheitert ------------------------------------------------

async def test_abgelehntes_erstes_bein_endet_ohne_position():
    motor, store, lang, kurz = _umgebung(lighter={"reject_orders": True})
    ergebnis = await motor.open_pair(_plan())

    assert ergebnis.state is PairState.FAILED
    # Das zweite Bein darf gar nicht erst versucht werden.
    assert kurz.placed_orders == []


# --- Zweites Bein abgelehnt -----------------------------------------------

async def test_abgelehntes_zweites_bein_fuehrt_zu_unhedged():
    motor, store, lang, kurz = _umgebung(extended={"reject_orders": True})
    ergebnis = await motor.open_pair(_plan())

    assert ergebnis.state is PairState.UNHEDGED
    assert store.get_state(ergebnis.pair_id) is PairState.UNHEDGED
    assert len(lang.placed_orders) == 1  # das erste Bein steht


async def test_unhedged_wird_nicht_stillschweigend_aufgeloest():
    motor, store, lang, kurz = _umgebung(extended={"reject_orders": True})
    ergebnis = await motor.open_pair(_plan())

    # Ohne ausdrueckliche Entscheidung bleibt das Paar UNHEDGED.
    assert store.get_state(ergebnis.pair_id) is PairState.UNHEDGED
    assert lang.closed_positions == []


# --- Zweites Bein im Timeout ----------------------------------------------

async def test_timeout_des_zweiten_beins_fuehrt_zu_unhedged():
    motor, store, lang, kurz = _umgebung(extended={"order_delay_seconds": 0.4})
    ergebnis = await motor.open_pair(_plan(hedge_timeout_seconds=0.05))

    assert ergebnis.state is PairState.UNHEDGED
    assert "keine Antwort innerhalb" in (ergebnis.detail or "")


async def test_timeout_vermerkt_dass_die_order_trotzdem_gefuellt_sein_koennte():
    """Nach einem Timeout ist der Zustand der Order unbekannt, nicht 'nicht gefuellt'.

    Die Boerse kann sie trotzdem ausgefuehrt haben. Das muss im Protokoll
    stehen, sonst wird beim Aufloesen doppelt gehedgt.
    """
    motor, store, lang, kurz = _umgebung(extended={"order_delay_seconds": 0.4})
    ergebnis = await motor.open_pair(_plan(hedge_timeout_seconds=0.05))

    assert "unbekannt" in (ergebnis.detail or "").lower()


# --- Teilfuellung ---------------------------------------------------------

async def test_teilfuellung_des_ersten_beins_passt_das_zweite_an():
    """Die Gegenseite folgt der tatsaechlich gefuellten Menge, nicht der geplanten."""
    motor, store, lang, kurz = _umgebung(lighter={"fill_ratio": Decimal("0.6")})
    await motor.open_pair(_plan())

    assert lang.placed_orders[0].size == Decimal("0.1")
    assert kurz.placed_orders[0].size == Decimal("0.06")


async def test_teilfuellung_des_zweiten_beins_ergibt_unhedged():
    motor, store, lang, kurz = _umgebung(extended={"fill_ratio": Decimal("0.5")})
    ergebnis = await motor.open_pair(_plan())

    # Die Beine sind unterschiedlich gross - das Paar ist nicht neutral.
    assert ergebnis.state is PairState.UNHEDGED


# --- Aufloesung eines UNHEDGED --------------------------------------------

async def test_nachziehen_der_gegenseite():
    motor, store, lang, kurz = _umgebung(extended={"reject_after": 0})
    ergebnis = await motor.open_pair(_plan())
    assert ergebnis.state is PairState.UNHEDGED

    # Die Ablehnung war einmalig; der zweite Versuch gelingt.
    kurz._reject_after = 5
    nachher = await motor.resolve_unhedged(ergebnis.pair_id, action="hedge")

    assert nachher.state is PairState.OPEN
    assert len(kurz.placed_orders) == 2


async def test_rollback_schliesst_das_erste_bein():
    motor, store, lang, kurz = _umgebung(extended={"reject_orders": True})
    ergebnis = await motor.open_pair(_plan())

    nachher = await motor.resolve_unhedged(ergebnis.pair_id, action="rollback")

    assert nachher.state is PairState.CLOSED
    # Geschlossen wird ueber eine Reduce-Only-Gegenorder, nicht ueber
    # close_position - nur so entsteht ein Fill fuer die Ergebnisrechnung.
    glattstellung = lang.placed_orders[-1]
    assert glattstellung.reduce_only is True
    assert glattstellung.side is Side.SHORT  # Gegenseite zum Long
    assert glattstellung.size == Decimal("0.1")


async def test_unbekannte_aktion_wird_abgelehnt():
    motor, store, lang, kurz = _umgebung(extended={"reject_orders": True})
    ergebnis = await motor.open_pair(_plan())

    with pytest.raises(ValueError):
        await motor.resolve_unhedged(ergebnis.pair_id, action="irgendwas")


async def test_auto_rollback_greift_nur_wenn_eingeschaltet():
    motor, store, lang, kurz = _umgebung(extended={"reject_orders": True})
    ergebnis = await motor.open_pair(_plan(auto_rollback=True))

    assert ergebnis.state is PairState.CLOSED
    assert lang.placed_orders[-1].reduce_only is True


# --- Schliessen -----------------------------------------------------------

async def test_paar_schliessen_schliesst_beide_beine():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    nachher = await motor.close_pair(ergebnis.pair_id)

    assert nachher.state is PairState.CLOSED
    # Je Bein eine Eroeffnungs- und eine Glattstellungsorder.
    assert len(lang.placed_orders) == 2
    assert len(kurz.placed_orders) == 2
    assert lang.placed_orders[1].reduce_only is True
    assert kurz.placed_orders[1].reduce_only is True


async def test_schliessen_eines_unbekannten_paares_wirft():
    motor, store, lang, kurz = _umgebung()
    with pytest.raises(KeyError):
        await motor.close_pair(9999)


# --- Absturz zwischen den Beinen ------------------------------------------

async def test_wiederanlauf_erkennt_ein_haengengebliebenes_paar():
    """Absturz mitten in der Ausfuehrung.

    Der Zustand steht vor dem Netzwerkaufruf in der Datenbank. Beim Start
    prueft der Wiederanlauf, welche Paare nicht sauber zu Ende gefuehrt wurden.
    """
    motor, store, lang, kurz = _umgebung()
    pair_id = store.create_pair(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        notional_usd=Decimal(6400),
        size=Decimal("0.1"),
        first_venue="lighter",
    )
    store.set_state(pair_id, PairState.OPENING_SECOND, message="Absturz simuliert")

    berichte = await motor.recover()

    assert len(berichte) == 1
    assert berichte[0].pair_id == pair_id
    assert berichte[0].state is PairState.OPENING_SECOND


async def test_wiederanlauf_meldet_abweichung_zum_boersenzustand():
    motor, store, lang, kurz = _umgebung()
    pair_id = store.create_pair(
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="extended",
        notional_usd=Decimal(6400),
        size=Decimal("0.1"),
        first_venue="lighter",
    )
    store.set_state(pair_id, PairState.OPENING_SECOND)

    bericht = (await motor.recover())[0]
    # Der MockAdapter meldet keine Positionen - die Datenbank erwartet aber
    # zwei Beine. Genau diese Abweichung soll auffallen.
    assert bericht.discrepancy is True
    assert "Position" in bericht.detail


async def test_sauber_geschlossene_paare_tauchen_im_wiederanlauf_nicht_auf():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())
    await motor.close_pair(ergebnis.pair_id)

    assert await motor.recover() == []


# --- Kill Switch ----------------------------------------------------------

async def test_kill_switch_schliesst_alles_auf_beiden_boersen():
    motor, store, lang, kurz = _umgebung()
    await motor.open_pair(_plan())

    ergebnis = await motor.panic()

    assert lang.closed_positions == ["BTC-PERP"]
    assert kurz.closed_positions == ["BTC-PERP"]
    assert ergebnis.closed_positions == 2


async def test_kill_switch_setzt_offene_paare_auf_geschlossen():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    await motor.panic()

    assert store.get_state(ergebnis.pair_id) is PairState.CLOSED


async def test_kill_switch_laeuft_auch_ohne_offene_paare():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.panic()
    assert ergebnis.closed_positions == 0
    assert ergebnis.errors == []


# --- Protokoll ------------------------------------------------------------

async def test_jeder_zustandsuebergang_steht_in_der_datenbank():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    zustaende = [
        e for e in motor.journal.events() if e.kind == "zustand" and e.pair_id == ergebnis.pair_id
    ]
    # PLANNED -> OPENING_FIRST -> FIRST_FILLED -> OPENING_SECOND -> OPEN
    assert len(zustaende) >= 4


async def test_orders_werden_vor_dem_senden_protokolliert():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    orders = store.orders(ergebnis.pair_id)
    assert len(orders) == 2
    assert all(o.dry_run is True for o in orders)
    assert all(o.status == OrderStatus.FILLED.value for o in orders)


async def test_kill_switch_greift_auch_ohne_funktionierende_positionsabfrage():
    """Im Notfall darf der Kill Switch nicht an einer Abfrage haengen.

    Die Symbole offener Paare stehen in der Datenbank. Antwortet die Boerse
    nicht auf die Positionsabfrage, wird trotzdem geschlossen, was bekannt ist.
    """
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    # Positionsabfrage faellt aus.
    async def keine_antwort():
        raise RuntimeError("Boerse antwortet nicht")

    lang.get_positions = keine_antwort  # type: ignore[method-assign]
    kurz.get_positions = keine_antwort  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await lang.get_positions()

    # Trotzdem wird geschlossen, weil das Symbol aus der Datenbank kommt.
    motor._adapters["lighter"].get_positions = keine_antwort  # type: ignore[method-assign]
    try:
        nachher = await motor.panic()
    except RuntimeError:
        pytest.fail("Der Kill Switch darf an einer fehlgeschlagenen Abfrage nicht scheitern")

    assert "BTC-PERP" in lang.closed_positions
    assert "BTC-PERP" in kurz.closed_positions
    assert nachher.closed_positions == 2
    assert store.get_state(ergebnis.pair_id) is PairState.CLOSED


# --- Ergebnisrechnung beim Schliessen (Auftrag 6.5) ----------------------

async def test_schliessen_erzeugt_eine_ergebniszeile():
    """Ohne diese Zeile laesst sich nicht sagen, was ein Punkt gekostet hat."""
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())
    await motor.close_pair(ergebnis.pair_id)

    paar = store.get_pair(ergebnis.pair_id)
    assert paar.net_result is not None
    assert paar.fees_paid is not None
    assert paar.holding_hours is not None

    eintraege = [e for e in motor.journal.events(kinds=["paar_ergebnis"])]
    assert len(eintraege) == 1
    assert "Netto" in eintraege[0].message


async def test_gegenbuchung_landet_als_fill_in_der_datenbank():
    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())
    await motor.close_pair(ergebnis.pair_id)

    fills = store.fills_for(ergebnis.pair_id)
    assert len(fills) == 4  # je Bein auf und zu
    assert sum(1 for f in fills if f["closing"]) == 2


async def test_funding_wird_dem_paar_zugeordnet():
    """Ohne Zuordnung bleibt funding_payments.pair_id leer.

    Dann laesst sich nicht sagen, welches Paar welches Funding gebracht hat.
    """
    from datetime import datetime, timezone

    from app.models.domain import FundingPayment

    motor, store, lang, kurz = _umgebung()
    ergebnis = await motor.open_pair(_plan())

    motor.journal.record_funding_payments([
        FundingPayment(
            venue="lighter",
            symbol="BTC-PERP",
            amount=Decimal("3.25"),
            timestamp=datetime.now(timezone.utc),
            external_id="f1",
        )
    ])

    await motor.close_pair(ergebnis.pair_id)

    zugeordnet = store.funding_for(ergebnis.pair_id)
    assert [z.amount for z in zugeordnet] == [Decimal("3.25")]
    assert store.get_pair(ergebnis.pair_id).funding_received == Decimal("3.25")
