"""API-Routen, Phase 1: Marktdaten und Paarvorschlaege."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.schemas import (
    BalanceOut,
    BestPairOut,
    DetectedPairOut,
    HealthOutModel,
    JournalEntryOut,
    LegOut,
    FundingMatrixOut,
    FundingRowOut,
    HealthOut,
    MarketOut,
    OpportunityOut,
    RateOut,
    VenueOut,
)
from app.config import get_settings
from app.core import symbols as sym
from app.core.account import AccountService
from app.core.service import MarketDataService, Snapshot
from app.models.domain import VenueStatus

router = APIRouter(prefix="/api")

VERSION = "0.1.0"


def _service(request: Request) -> MarketDataService:
    dienst = getattr(request.app.state, "service", None)
    if dienst is None:  # pragma: no cover - nur bei Fehlkonfiguration
        raise HTTPException(status_code=503, detail="Dienst nicht bereit.")
    return dienst


# Wie lange ein Aufruf hoechstens auf den allerersten Datenabruf wartet. Ist
# eine Boerse nicht erreichbar, laeuft deren Abruf in Wiederholungen mit
# Backoff - darauf darf die Oberflaeche nicht warten. Sie soll die Boerse
# gerade dann als nicht erreichbar anzeigen koennen.
FIRST_LOAD_TIMEOUT_SECONDS = 8.0


def _filter(snapshot: Snapshot, symbols: Optional[list[str]]) -> Snapshot:
    if not symbols:
        return snapshot
    gewuenscht = set(symbols)
    return Snapshot(
        funding=tuple(f for f in snapshot.funding if f.symbol in gewuenscht),
        opportunities=tuple(o for o in snapshot.opportunities if o.symbol in gewuenscht),
        venues=snapshot.venues,
        warnings=snapshot.warnings,
        as_of=snapshot.as_of,
    )


async def _snapshot(request: Request, symbols: Optional[list[str]] = None) -> Snapshot:
    """Liefert den zwischengespeicherten Stand.

    Der Hintergrundlauf haelt ihn aktuell. Nur wenn es noch gar keinen gibt,
    wird einmal nachgeladen - und auch das mit Zeitlimit.
    """
    dienst = _service(request)
    vorhanden = dienst.last_snapshot

    if vorhanden is None:
        try:
            vorhanden = await asyncio.wait_for(
                dienst.refresh(list(sym.canonical_symbols())),
                timeout=FIRST_LOAD_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # Die Boersen werden trotzdem aufgefuehrt, sonst zeigt die
            # Kopfzeile gar nichts an - und genau dort soll ablesbar sein,
            # welche Boerse klemmt.
            einstellungen = get_settings()
            return Snapshot(
                funding=(),
                opportunities=(),
                venues=tuple(
                    VenueStatus(
                        name=a.name,
                        supports_trading=bool(getattr(a, "supports_trading", False)),
                        reachable=False,
                        environment=einstellungen.environment.value,
                        detail="Erstabruf laeuft noch oder die Boerse antwortet nicht.",
                    )
                    for a in dienst.adapters
                ),
                warnings=(
                    "Noch keine Marktdaten. Mindestens eine Boerse antwortet nicht - "
                    "der Verbindungsstatus in der Kopfzeile zeigt, welche.",
                ),
            )

    return _filter(vorhanden, symbols)


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    s = get_settings()
    return HealthOut(
        status="ok",
        dry_run=s.dry_run,
        environment=s.environment.value,
        version=VERSION,
    )


@router.get("/venues", response_model=list[VenueOut])
async def venues(request: Request) -> list[VenueOut]:
    snapshot = await _snapshot(request)
    return [
        VenueOut(
            name=v.name,
            supports_trading=v.supports_trading,
            reachable=v.reachable,
            environment=v.environment,
            detail=v.detail,
        )
        for v in snapshot.venues
    ]


@router.get("/markets", response_model=list[MarketOut])
async def markets(request: Request) -> list[MarketOut]:
    dienst = _service(request)
    ergebnis: list[MarketOut] = []
    for adapter in dienst.adapters:
        try:
            for m in await adapter.list_markets():
                ergebnis.append(
                    MarketOut(
                        venue=m.venue,
                        symbol=m.symbol,
                        native_symbol=m.native_symbol,
                        active=m.active,
                        mark_price=m.mark_price,
                    )
                )
        except Exception:  # eine unerreichbare Boerse darf die Liste nicht kippen
            continue
    return ergebnis


@router.get("/funding", response_model=FundingMatrixOut)
async def funding(
    request: Request,
    symbol: Optional[list[str]] = Query(default=None),
) -> FundingMatrixOut:
    """Vergleichsmatrix, auf die Stundenrate normalisiert."""
    snapshot = await _snapshot(request, symbol)

    venues = sorted({f.venue for f in snapshot.funding})
    je_symbol: dict[str, dict[str, RateOut]] = {}
    for f in snapshot.funding:
        je_symbol.setdefault(f.symbol, {})[f.venue] = RateOut(
            rate_hourly=f.rate_hourly,
            apr=f.apr,
            native_rate=f.native_rate,
            native_convention=f.native_convention,
            native_interval_hours=f.native_interval_hours,
            next_funding_time=f.next_funding_time,
            as_of=f.as_of,
        )

    bestes: dict[str, BestPairOut] = {}
    for op in snapshot.opportunities:
        # Sortiert: bestaetigte Assets zuerst, der erste je Symbol gewinnt.
        # Ein Paar, dessen Preise zwei verschiedene Assets nahelegen, wird
        # nie als bester Vorschlag ausgewiesen.
        if op.asset_match is False:
            continue
        if op.symbol not in bestes:
            bestes[op.symbol] = BestPairOut(
                long_venue=op.long_venue,
                short_venue=op.short_venue,
                net_rate_hourly=op.net_rate_hourly,
                net_apr=op.net_apr,
                breakeven_hours=op.breakeven_hours,
                cost_basis_known=op.cost_basis_known,
                price_deviation=op.price_deviation,
                asset_match=op.asset_match,
            )

    zeilen = [
        FundingRowOut(symbol=s, rates=raten, best=bestes.get(s))
        for s, raten in sorted(je_symbol.items())
    ]
    return FundingMatrixOut(
        as_of=snapshot.as_of,
        venues=venues,
        rows=zeilen,
        warnings=list(snapshot.warnings),
    )


@router.get("/opportunities", response_model=list[OpportunityOut])
async def opportunities(request: Request) -> list[OpportunityOut]:
    """Paare nach Netto-Funding-APR sortiert, inklusive Break-even."""
    snapshot = await _snapshot(request)
    return [
        OpportunityOut(
            symbol=o.symbol,
            long_venue=o.long_venue,
            short_venue=o.short_venue,
            long_rate_hourly=o.long_rate_hourly,
            short_rate_hourly=o.short_rate_hourly,
            net_rate_hourly=o.net_rate_hourly,
            net_apr=o.net_apr,
            breakeven_hours=o.breakeven_hours,
            cost_basis_known=o.cost_basis_known,
            price_deviation=o.price_deviation,
            asset_match=o.asset_match,
            as_of=o.as_of,
        )
        for o in snapshot.opportunities
    ]


# --- Phase 2: Account, Positionen, Journal -------------------------------


def _account(request: Request) -> AccountService:
    dienst = getattr(request.app.state, "account", None)
    if dienst is None:  # pragma: no cover - nur bei Fehlkonfiguration
        raise HTTPException(status_code=503, detail="Account-Dienst nicht bereit.")
    return dienst


def _journal(request: Request):
    tagebuch = getattr(request.app.state, "journal", None)
    if tagebuch is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Journal nicht bereit.")
    return tagebuch


@router.get("/balances", response_model=list[BalanceOut])
async def balances(request: Request) -> list[BalanceOut]:
    """Kontostaende. Leer, solange keine Zugangsdaten hinterlegt sind."""
    return [
        BalanceOut(
            venue=b.venue,
            collateral=b.collateral,
            equity=b.equity,
            available=b.available,
            unrealised_pnl=b.unrealised_pnl,
            initial_margin=b.initial_margin,
            margin_usage=b.margin_usage,
            as_of=b.as_of,
        )
        for b in await _account(request).balances()
    ]


@router.get("/positions", response_model=list[DetectedPairOut])
async def positions(request: Request) -> list[DetectedPairOut]:
    """Offene Positionen, zu Paaren gruppiert.

    Phase 2 zeigt damit auch Paare an, die von Hand eroeffnet wurden. Ein
    Symbol mit nur einem Bein erscheint als nicht gehedgt.
    """
    dienst = _account(request)
    positionen, kontostaende = await dienst.positions(), await dienst.balances()
    paare = dienst.detect_pairs(positionen, kontostaende)

    return [
        DetectedPairOut(
            symbol=p.symbol,
            legs=[
                LegOut(
                    venue=a.position.venue,
                    symbol=a.position.symbol,
                    side=a.position.side.value,
                    size=a.position.size,
                    entry_price=a.position.entry_price,
                    mark_price=a.position.mark_price,
                    notional=a.position.notional,
                    unrealised_pnl=a.position.unrealised_pnl,
                    liquidation_price=a.position.liquidation_price,
                    funding_paid=a.position.funding_paid,
                    funding_received=a.funding_received,
                    health=HealthOutModel(
                        liquidation_distance=a.health.liquidation_distance,
                        margin_usage=a.health.margin_usage,
                        level=a.health.level.value,
                        detail=a.health.detail,
                    ),
                )
                for a in p.legs
            ],
            net_delta_usd=p.net_delta_usd,
            net_delta_pct=p.net_delta_pct,
            combined_pnl=p.combined_pnl,
            funding_received=p.funding_received,
            hedged=p.hedged,
            level=p.level.value,
            holding_hours=p.holding_hours,
        )
        for p in paare
    ]


@router.get("/journal")
async def journal(
    request: Request,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    limit: int = Query(default=500, ge=1, le=10000),
):
    """Chronologisches Journal. Mit format=csv als Datei zum Herunterladen."""
    tagebuch = _journal(request)

    if format == "csv":
        return PlainTextResponse(
            tagebuch.to_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="deltafarm-journal.csv"'},
        )

    eintraege: list[JournalEntryOut] = [
        JournalEntryOut(
            timestamp=e.timestamp,
            kind=e.kind,
            venue=e.venue,
            symbol=e.symbol,
            message=e.message,
        )
        for e in tagebuch.events(limit=limit)
    ]
    eintraege += [
        JournalEntryOut(
            timestamp=z.timestamp,
            kind="funding",
            venue=z.venue,
            symbol=z.symbol,
            amount=z.amount,
            confirmed=z.confirmed,
            message="Funding-Zahlung" if z.confirmed else "Funding-Zahlung (gerechnet)",
        )
        for z in tagebuch.funding_payments()
    ]
    eintraege.sort(key=lambda e: e.timestamp)
    return eintraege
