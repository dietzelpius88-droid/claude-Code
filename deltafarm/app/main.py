"""Anwendungsstart. Bindet ausschliesslich an 127.0.0.1."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Sequence

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.extended import ExtendedAdapter
from app.adapters.lighter import LighterAdapter
from app.api.routes import router
from app.config import Settings, get_settings
from app.core import symbols as sym
from app.core.account import AccountService
from app.core.execution import ExecutionEngine
from app.core.preview import PreviewStore
from app.core.service import MarketDataService
from app.core.trading import TradingService
from app.storage.db import create_all, make_engine, make_session_factory
from app.storage.journal import Journal
from app.storage.pairs import PairStore

LOG = structlog.get_logger(__name__)


def build_adapters(settings: Settings) -> list:
    # Die Schluessel werden hier aus den Einstellungen geholt und wandern
    # direkt in den Adapter. Sie erscheinen in keiner Logzeile und in keiner
    # API-Antwort.
    api_key = (
        settings.extended_api_key.get_secret_value() if settings.extended_api_key else None
    )
    return [
        ExtendedAdapter(
            base_url=settings.extended_rest_url,
            rate_per_second=settings.extended_rate_per_second,
            burst=settings.extended_burst,
            api_key=api_key,
            dry_run=settings.dry_run,
        ),
        LighterAdapter(
            base_url=settings.lighter_rest_url,
            rate_per_second=settings.lighter_rate_per_second,
            burst=settings.lighter_burst,
            account_index=settings.lighter_account_index,
            dry_run=settings.dry_run,
        ),
    ]


async def run_startup_checks(service: MarketDataService) -> None:
    """Intervallpruefung nach ADR-004, beim Start, auf einem Leitsymbol.

    Schlaegt sie fehl, laeuft die Anwendung weiter - aber die Warnung steht im
    Log und der Betreiber weiss, dass die Vergleichsansicht nicht belastbar
    ist. Ein harter Abbruch waere hier falsch: eine voruebergehend nicht
    erreichbare Boerse darf das Werkzeug nicht unbenutzbar machen.
    """
    pruefungen = await service.verify_intervals(["BTC-PERP"])
    for p in pruefungen:
        if p.ok:
            LOG.info(
                "funding_intervall_bestaetigt",
                venue=p.venue,
                symbol=p.symbol,
                stunden=str(p.declared_hours),
            )
        else:
            LOG.error(
                "funding_intervall_stimmt_nicht",
                venue=p.venue,
                symbol=p.symbol,
                deklariert=str(p.declared_hours),
                gemessen=str(p.observed_hours),
                detail=p.detail,
            )


async def background_worker(app: FastAPI, interval_seconds: int) -> None:
    """Startpruefung und danach die laufende Aktualisierung.

    Beides laeuft bewusst im Hintergrund: eine nicht erreichbare Boerse laesst
    die Pruefung in den Retry laufen, und solange darf die Oberflaeche nicht
    warten. Sie soll die Boerse gerade dann als "nicht erreichbar" anzeigen
    koennen.
    """
    dienst: MarketDataService = app.state.service
    symbole = list(sym.canonical_symbols())

    konto: Optional[AccountService] = getattr(app.state, "account", None)

    try:
        await run_startup_checks(dienst)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.error("startpruefung_fehlgeschlagen", fehler=str(exc))

    # Wiederanlauf: haengengebliebene Paare gegen den Boersenzustand halten.
    motor = getattr(app.state, "engine", None)
    if motor is not None:
        try:
            for bericht in await motor.recover():
                LOG.error(
                    "wiederanlauf",
                    pair_id=bericht.pair_id,
                    symbol=bericht.symbol,
                    zustand=bericht.state.value,
                    abweichung=bericht.discrepancy,
                    detail=bericht.detail,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("wiederanlauf_fehlgeschlagen", fehler=str(exc))

    while True:
        try:
            snapshot = await dienst.refresh(symbole)
            for warnung in snapshot.warnings:
                LOG.warning("plausibilitaet", meldung=warnung)

            # Funding-Zahlungen und Kontostaende ins Journal nachziehen.
            # Ohne Zugangsdaten passiert hier nichts - das ist kein Fehler.
            if konto is not None and konto.has_any_account_access:
                ergebnis = await konto.sync()
                if ergebnis["funding_neu"]:
                    LOG.info("funding_nachgeladen", **ergebnis)

            # Abgelaufene Vorschauen entfernen.
            vorschauen = getattr(app.state, "previews", None)
            if vorschauen is not None:
                vorschauen.purge()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # der Hintergrundlauf darf nie sterben
            LOG.error("aktualisierung_fehlgeschlagen", fehler=str(exc))
        await asyncio.sleep(interval_seconds)


def create_app(
    *,
    settings: Optional[Settings] = None,
    adapters: Optional[Sequence] = None,
    start_background: bool = True,
    db_path: Optional[str] = None,
) -> FastAPI:
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        verwendete = list(adapters) if adapters is not None else build_adapters(s)

        # Datenbank und Journal. Eine Datei, kein Server.
        engine = make_engine(Path(db_path) if db_path else None)
        create_all(engine)
        app.state.journal = Journal(make_session_factory(engine))

        app.state.service = MarketDataService(verwendete, environment=s.environment.value)
        app.state.account = AccountService(verwendete, journal=app.state.journal)

        # Ausfuehrung (Phase 3). Im Trockenlauf laufen alle Pfade durch, es
        # geht aber nichts an die Boersen.
        nach_name = {getattr(a, "name", f"adapter{i}"): a for i, a in enumerate(verwendete)}
        app.state.pairs = PairStore(make_session_factory(engine))
        app.state.engine = ExecutionEngine(
            adapters=nach_name,
            store=app.state.pairs,
            journal=app.state.journal,
            dry_run=s.dry_run,
        )
        app.state.previews = PreviewStore(max_age_seconds=s.preview_max_age_seconds)
        app.state.trading = TradingService(
            adapters=nach_name,
            engine=app.state.engine,
            previews=app.state.previews,
            journal=app.state.journal,
            margin_share=s.margin_share,
        )

        app.state.journal.record_event(
            kind="start",
            message=f"Anwendung gestartet ({s.environment.value}, "
            f"{'Trockenlauf' if s.dry_run else 'LIVE'})",
            payload={"umgebung": s.environment.value, "dry_run": s.dry_run},
        )
        LOG.info(
            "start",
            umgebung=s.environment.value,
            dry_run=s.dry_run,
            adresse=f"{s.host}:{s.port}",
        )
        aufgabe = None
        if start_background:
            aufgabe = asyncio.create_task(background_worker(app, s.poll_seconds))
        try:
            yield
        finally:
            if aufgabe is not None:
                aufgabe.cancel()
                try:
                    await aufgabe
                except asyncio.CancelledError:
                    pass
            await app.state.service.aclose()

    app = FastAPI(title="deltafarm", version="0.1.0", lifespan=lifespan)
    # Nur der lokale Vite-Entwicklungsserver darf zugreifen.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


def main() -> None:  # pragma: no cover
    import uvicorn

    s = get_settings()
    uvicorn.run(create_app(settings=s), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
