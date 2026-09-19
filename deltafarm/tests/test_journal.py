"""Journal: Ereignisse, Funding-Zahlungen, CSV-Export."""

import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.domain import Balance, FundingPayment
from app.storage.db import create_all, make_engine, make_session_factory
from app.storage.journal import Journal

JETZT = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def journal() -> Journal:
    engine = make_engine(":memory:")
    create_all(engine)
    return Journal(make_session_factory(engine))


def _zahlung(betrag: str, *, external_id: str = "1", minuten: int = 0, confirmed: bool = True):
    return FundingPayment(
        venue="lighter",
        symbol="BTC-PERP",
        amount=Decimal(betrag),
        rate=Decimal("10.95"),
        position_size=Decimal("0.5"),
        timestamp=JETZT + timedelta(minutes=minuten),
        confirmed=confirmed,
        external_id=external_id,
    )


# --- Decimal-Treue --------------------------------------------------------

def test_decimal_ueberlebt_die_datenbank_exakt(journal: Journal):
    """Der Grund, warum Betraege als Text gespeichert werden.

    Ueber SQLAlchemys Numeric liefe der Wert auf SQLite durch float und kaeme
    veraendert zurueck.
    """
    journal.record_funding_payments([_zahlung("0.000000000000000001")])
    gelesen = journal.funding_payments()[0]

    assert isinstance(gelesen.amount, Decimal)
    assert gelesen.amount == Decimal("0.000000000000000001")
    # Auch die Stelligkeit bleibt erhalten - nicht nur der Zahlenwert.
    assert gelesen.amount.as_tuple() == Decimal("0.000000000000000001").as_tuple()


def test_grosse_und_kleine_betraege(journal: Journal):
    journal.record_funding_payments([
        _zahlung("123456789.123456789", external_id="gross"),
        _zahlung("-0.00000001", external_id="klein"),
    ])
    betraege = {z.external_id: z.amount for z in journal.funding_payments()}
    assert betraege["gross"] == Decimal("123456789.123456789")
    assert betraege["klein"] == Decimal("-0.00000001")


# --- Funding-Zahlungen ----------------------------------------------------

def test_zahlungen_werden_gespeichert(journal: Journal):
    neu = journal.record_funding_payments([_zahlung("1.25", external_id="a")])
    assert neu == 1
    assert len(journal.funding_payments()) == 1


def test_wiederholtes_nachladen_zaehlt_nicht_doppelt(journal: Journal):
    """Die Zahlungen werden periodisch nachgeladen.

    Ohne diese Sperre stuende dieselbe Zahlung nach jedem Durchlauf erneut im
    Journal und die Summe waere falsch.
    """
    journal.record_funding_payments([_zahlung("1.25", external_id="a")])
    neu = journal.record_funding_payments([_zahlung("1.25", external_id="a")])

    assert neu == 0
    assert len(journal.funding_payments()) == 1


def test_gleiche_id_auf_verschiedenen_boersen_ist_kein_duplikat(journal: Journal):
    a = _zahlung("1.25", external_id="1")
    b = a.model_copy(update={"venue": "extended"})
    journal.record_funding_payments([a, b])
    assert len(journal.funding_payments()) == 2


def test_zahlung_ohne_id_wird_trotzdem_aufgenommen(journal: Journal):
    # Extended liefert Summen ohne eigene Zahlungs-ID.
    journal.record_funding_payments([_zahlung("2.0", external_id=None)])
    assert len(journal.funding_payments()) == 1


def test_gerechnete_zahlung_bleibt_als_solche_erkennbar(journal: Journal):
    journal.record_funding_payments([_zahlung("1.0", external_id="x", confirmed=False)])
    assert journal.funding_payments()[0].confirmed is False


def test_zahlungen_nach_zeitraum(journal: Journal):
    journal.record_funding_payments([
        _zahlung("1", external_id="frueh", minuten=0),
        _zahlung("2", external_id="spaet", minuten=120),
    ])
    spaet = journal.funding_payments(since=JETZT + timedelta(minutes=60))
    assert [z.external_id for z in spaet] == ["spaet"]


def test_summe_je_boerse_und_symbol(journal: Journal):
    journal.record_funding_payments([
        _zahlung("1.25", external_id="a"),
        _zahlung("-0.75", external_id="b"),
    ])
    assert journal.funding_total(venue="lighter", symbol="BTC-PERP") == Decimal("0.50")


# --- Ereignisse -----------------------------------------------------------

def test_ereignis_wird_protokolliert(journal: Journal):
    journal.record_event(kind="start", message="Anwendung gestartet", venue="extended")
    eintraege = journal.events()
    assert len(eintraege) == 1
    assert eintraege[0].kind == "start"


def test_ereignis_mit_nutzdaten(journal: Journal):
    journal.record_event(
        kind="preflight",
        message="Pruefung",
        payload={"abstand": Decimal("0.25"), "ok": True},
    )
    e = journal.events()[0]
    assert '"abstand": "0.25"' in (e.payload or "")


def test_ereignisse_nach_art_filterbar(journal: Journal):
    journal.record_event(kind="start", message="a")
    journal.record_event(kind="fehler", message="b", level="ERROR")
    assert [e.kind for e in journal.events(kinds=["fehler"])] == ["fehler"]


# --- Momentaufnahmen ------------------------------------------------------

def test_snapshot_wird_gespeichert(journal: Journal):
    journal.record_snapshot(
        Balance(
            venue="extended",
            collateral="USD",
            equity=Decimal("10250.5"),
            available=Decimal("8000"),
            as_of=JETZT,
        )
    )
    s = journal.snapshots()[0]
    assert s.equity == Decimal("10250.5")


# --- CSV ------------------------------------------------------------------

def test_csv_enthaelt_ereignisse_und_zahlungen(journal: Journal):
    journal.record_event(kind="start", message="Anwendung gestartet")
    journal.record_funding_payments([_zahlung("1.25", external_id="a")])

    zeilen = list(csv.DictReader(io.StringIO(journal.to_csv())))
    arten = {z["art"] for z in zeilen}
    assert arten == {"ereignis", "funding"}


def test_csv_schreibt_betraege_aus_statt_wissenschaftlich(journal: Journal):
    """str(Decimal) ergaebe hier "1E-18" - in einer Tabellenkalkulation
    schlecht lesbar und je nach Gebietsschema Text statt Zahl."""
    journal.record_funding_payments([_zahlung("0.000000000000000001", external_id="a")])
    text = journal.to_csv()

    assert "0.000000000000000001" in text
    assert "1E-18" not in text


def test_csv_rundet_nicht(journal: Journal):
    journal.record_funding_payments([_zahlung("1.234567890123456789", external_id="b")])
    assert "1.234567890123456789" in journal.to_csv()


def test_csv_ist_chronologisch(journal: Journal):
    journal.record_funding_payments([
        _zahlung("1", external_id="spaet", minuten=120),
        _zahlung("2", external_id="frueh", minuten=0),
    ])
    zeilen = list(csv.DictReader(io.StringIO(journal.to_csv())))
    zeitpunkte = [z["zeitpunkt"] for z in zeilen]
    assert zeitpunkte == sorted(zeitpunkte)


def test_csv_kopfzeile_ist_stabil(journal: Journal):
    journal.record_event(kind="start", message="x")
    kopf = journal.to_csv().splitlines()[0]
    assert kopf.startswith("zeitpunkt,art,boerse,symbol")
