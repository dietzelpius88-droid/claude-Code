"""Migration und Modelle duerfen nicht auseinanderlaufen.

Ohne diesen Test faellt erst beim Anwender auf, dass eine Spalte im Modell
steht, die keine Migration je angelegt hat.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.storage.db import create_all, make_engine

WURZEL = Path(__file__).resolve().parent.parent


def _schema(engine) -> dict[str, set[str]]:
    pruefer = inspect(engine)
    return {
        tabelle: {spalte["name"] for spalte in pruefer.get_columns(tabelle)}
        for tabelle in pruefer.get_table_names()
        if tabelle != "alembic_version"
    }


@pytest.fixture
def migrierte_datenbank(tmp_path: Path):
    ziel = tmp_path / "migriert.db"
    umgebung = {**os.environ, "ALEMBIC_DB_URL": f"sqlite:///{ziel}"}
    ergebnis = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=WURZEL,
        env=umgebung,
        capture_output=True,
        text=True,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    return make_engine(ziel)


def test_migration_laeuft_auf_leerer_datenbank_durch(migrierte_datenbank):
    assert "funding_payments" in _schema(migrierte_datenbank)


def test_migration_erzeugt_dasselbe_schema_wie_die_modelle(migrierte_datenbank, tmp_path: Path):
    aus_modellen = make_engine(tmp_path / "modelle.db")
    create_all(aus_modellen)

    assert _schema(migrierte_datenbank) == _schema(aus_modellen)


def test_alle_geforderten_tabellen_sind_vorhanden(migrierte_datenbank):
    # Auftrag Abschnitt 9.
    erwartet = {
        "venues",
        "pairs",
        "legs",
        "orders",
        "fills",
        "funding_payments",
        "events",
        "snapshots",
    }
    assert erwartet <= set(_schema(migrierte_datenbank))
