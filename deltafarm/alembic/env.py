"""Alembic-Umgebung.

Die Datenbank-URL kommt aus dem Projekt, nicht aus alembic.ini - damit
Migrationen dieselbe Datei treffen wie die Anwendung.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.storage.db import DEFAULT_PATH
from app.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Reihenfolge: ausdrueckliche Umgebungsvariable (fuer Tests), dann
    # alembic.ini, dann die Standarddatei der Anwendung.
    aus_umgebung = os.environ.get("ALEMBIC_DB_URL")
    if aus_umgebung:
        return aus_umgebung
    gesetzt = config.get_main_option("sqlalchemy.url", None)
    return gesetzt or f"sqlite:///{DEFAULT_PATH}"


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite kann Spalten nicht direkt aendern - Alembic baut die Tabelle
        # dafuer neu. Ohne diese Zeile schlagen solche Migrationen fehl.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    abschnitt = config.get_section(config.config_ini_section, {})
    abschnitt["sqlalchemy.url"] = _url()
    verbindung = engine_from_config(abschnitt, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with verbindung.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
