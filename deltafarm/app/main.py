"""Anwendungsstart. Bindet ausschliesslich an 127.0.0.1."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Sequence

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.extended import ExtendedAdapter
from app.adapters.lighter import LighterAdapter
from app.api.routes import router
from app.config import Settings, get_settings
from app.core import symbols as sym
from app.core.service import MarketDataService

LOG = structlog.get_logger(__name__)


def build_adapters(settings: Settings) -> list:
    return [
        ExtendedAdapter(
            base_url=settings.extended_rest_url,
            rate_per_second=settings.extended_rate_per_second,
            burst=settings.extended_burst,
        ),
        LighterAdapter(
            base_url=settings.lighter_rest_url,
            rate_per_second=settings.lighter_rate_per_second,
            burst=settings.lighter_burst,
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

    try:
        await run_startup_checks(dienst)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.error("startpruefung_fehlgeschlagen", fehler=str(exc))

    while True:
        try:
            snapshot = await dienst.refresh(symbole)
            for warnung in snapshot.warnings:
                LOG.warning("plausibilitaet", meldung=warnung)
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
) -> FastAPI:
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = MarketDataService(
            adapters if adapters is not None else build_adapters(s),
            environment=s.environment.value,
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
