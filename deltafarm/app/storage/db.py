"""Datenbankzugang. Eine SQLite-Datei, kein Server."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.storage.models import Base

DEFAULT_PATH = Path("deltafarm.db")


def make_engine(path: Optional[Path] = None, *, echo: bool = False) -> Engine:
    ziel = path or DEFAULT_PATH
    url = "sqlite://" if str(ziel) == ":memory:" else f"sqlite:///{ziel}"
    engine = create_engine(url, echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # pragma: no cover - Treiberdetail
        cur = dbapi_connection.cursor()
        # Fremdschluessel sind in SQLite standardmaessig aus.
        cur.execute("PRAGMA foreign_keys=ON")
        # WAL, damit Lesen waehrend eines Schreibvorgangs nicht blockiert.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def create_all(engine: Engine) -> None:
    """Schema anlegen.

    Fuer den Erststart und fuer Tests. Aenderungen am Schema laufen ueber
    Alembic, damit sich eine bestehende Datei migrieren laesst.
    """
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaktionsklammer: bei einem Fehler wird zurueckgerollt."""
    sitzung = factory()
    try:
        yield sitzung
        sitzung.commit()
    except Exception:
        sitzung.rollback()
        raise
    finally:
        sitzung.close()
