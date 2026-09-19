"""API-Routen, Phase 1: Marktdaten und Paarvorschlaege."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.schemas import (
    BalanceOut,
    CheckOut,
    ExecutionOut,
    OpenRequestIn,
    PairOut,
    PanicOut,
    PreviewOut,
    PreviewRequestIn,
    RebalanceLegOut,
    RebalancePlanOut,
    RebalanceRequestIn,
    ResolveRequestIn,
    SizingOut,
    SummaryOut,
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
from app.core.preview import PreviewError
from app.core.rebalance import RebalanceError
from app.core.service import MarketDataService, Snapshot
from app.core.trading import PreviewRequest, TradingService
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


# --- Phase 3: Vorschau, Ausfuehrung, Kill Switch -------------------------


def _trading(request: Request) -> TradingService:
    dienst = getattr(request.app.state, "trading", None)
    if dienst is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Handelsdienst nicht bereit.")
    return dienst


def _engine(request: Request):
    motor = getattr(request.app.state, "engine", None)
    if motor is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Ausfuehrungsengine nicht bereit.")
    return motor


def _store(request: Request):
    speicher = getattr(request.app.state, "pairs", None)
    if speicher is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Paarspeicher nicht bereit.")
    return speicher


@router.post("/pairs/preview", response_model=PreviewOut)
async def pairs_preview(request: Request, body: PreviewRequestIn) -> PreviewOut:
    """Sizing und Preflight, ohne dass etwas gesendet wird."""
    dienst = _trading(request)
    antwort = await dienst.preview(
        PreviewRequest(
            symbol=body.symbol,
            notional_usd=body.notional_usd,
            long_venue=body.long_venue,
            short_venue=body.short_venue,
            max_slippage=body.max_slippage,
            long_leverage=body.long_leverage,
            short_leverage=body.short_leverage,
            first_venue=body.first_venue,
            hedge_timeout_seconds=body.hedge_timeout_seconds,
            auto_rollback=body.auto_rollback,
        )
    )

    if antwort.error is not None or antwort.sizing is None or antwort.preflight is None:
        return PreviewOut(token=None, ok=False, error=antwort.error or "Vorschau nicht moeglich")

    s = antwort.sizing
    p = antwort.preflight
    return PreviewOut(
        token=antwort.token,
        ok=p.ok,
        sizing=SizingOut(
            size=s.size,
            lot_size=s.lot_size,
            long_venue=s.long_venue,
            short_venue=s.short_venue,
            long_mark=s.long_mark,
            short_mark=s.short_mark,
            long_notional=s.long_notional,
            short_notional=s.short_notional,
            residual_delta_usd=s.residual_delta_usd,
            residual_delta_pct=s.residual_delta_pct,
            estimated_open_fees=s.estimated_open_fees,
            estimated_round_trip_fees=s.estimated_round_trip_fees,
        ),
        checks=[
            CheckOut(key=c.key, label=c.label, status=c.status.value, detail=c.detail)
            for c in p.checks
        ],
        net_rate_hourly=p.net_rate_hourly,
        net_apr=p.net_apr,
        breakeven_hours=p.breakeven_hours,
        blocking_reasons=p.blocking_reasons,
        valid_for_seconds=request.app.state.previews.max_age_seconds,
    )


@router.post("/pairs/open", response_model=ExecutionOut)
async def pairs_open(request: Request, body: OpenRequestIn) -> ExecutionOut:
    """Fuehrt eine Vorschau aus - nur, wenn sie noch gueltig ist."""
    try:
        ergebnis = await _trading(request).open_from_token(body.token)
    except PreviewError as exc:
        # 409: die Vorschau ist nicht mehr gueltig, der Zustand hat sich bewegt.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ExecutionOut(
        pair_id=ergebnis.pair_id,
        state=ergebnis.state.value,
        detail=ergebnis.detail,
        dry_run=get_settings().dry_run,
    )


@router.get("/pairs", response_model=list[PairOut])
async def pairs(request: Request) -> list[PairOut]:
    """Alle Paare - offene mit Live-Kennzahlen, geschlossene mit Ergebnis.

    Positionen, die ein Paar bilden und noch keinen Eintrag haben, werden
    dabei uebernommen. Danach gibt es nur einen Codepfad: ein von Hand
    eroeffnetes Paar verhaelt sich wie ein geklicktes.
    """
    speicher = _store(request)
    konto = _account(request)

    positionen = await konto.positions()
    konto.adopt_open_pairs(speicher, positionen)

    # Aktuelle Raten aus dem zwischengespeicherten Marktdatenstand.
    snapshot = _service(request).last_snapshot
    raten = (
        {(f.venue, f.symbol): f.rate_hourly for f in snapshot.funding} if snapshot else {}
    )
    ansichten = {
        a.pair_id: a
        for a in konto.build_pair_views(
            speicher, positionen, funding_rates=raten, balances=await konto.balances()
        )
    }

    ergebnis: list[PairOut] = []
    for paar in speicher.all_pairs():
        a = ansichten.get(paar.id)
        ergebnis.append(
            PairOut(
                id=paar.id,
                symbol=paar.symbol,
                long_venue=paar.long_venue,
                short_venue=paar.short_venue,
                status=paar.status,
                source=paar.source,
                notional_usd=paar.notional_usd,
                opened_at=paar.opened_at,
                closed_at=paar.closed_at,
                legs=[
                    {
                        "venue": b.venue,
                        "side": b.side,
                        "target_size": str(b.target_size) if b.target_size is not None else None,
                        "filled_size": str(b.filled_size) if b.filled_size is not None else None,
                        "avg_price": str(b.avg_price) if b.avg_price is not None else None,
                        "status": b.status,
                        # Live-Werte, sofern die Position offen ist
                        **_bein_live(a, b.venue),
                    }
                    for b in speicher.legs(paar.id)
                ],
                net_delta_usd=a.net_delta_usd if a else None,
                net_delta_pct=a.net_delta_pct if a else None,
                combined_pnl=a.combined_pnl if a else None,
                funding_received=a.funding_received if a else paar.funding_received,
                fees_paid=a.fees_paid if a else paar.fees_paid,
                fees_known=a.fees_known if a else paar.fees_paid is not None,
                net_funding_hourly=a.net_funding_hourly if a else None,
                net_funding_apr=a.net_funding_apr if a else None,
                holding_hours=a.holding_hours if a else paar.holding_hours,
                level=a.level.value if a else None,
                hedged=a.hedged if a else None,
                funding_negative=a.funding_negative if a else False,
                costs_recovered=a.costs_recovered if a else None,
                positions_missing=a.positions_missing if a else False,
                price_pnl=paar.price_pnl,
                net_result=paar.net_result,
                realized_apr=paar.realized_apr,
            )
        )
    return ergebnis


def _bein_live(ansicht, venue: str) -> dict:
    """Live-Werte eines Beins, falls die Position offen ist."""
    if ansicht is None:
        return {}
    for a in ansicht.legs:
        if a.position.venue != venue:
            continue
        return {
            "mark_price": str(a.position.mark_price),
            "entry_price": str(a.position.entry_price),
            "notional": str(a.position.notional),
            "unrealised_pnl": str(a.position.unrealised_pnl),
            "liquidation_price": (
                str(a.position.liquidation_price)
                if a.position.liquidation_price is not None
                else None
            ),
            "liquidation_distance": (
                str(a.health.liquidation_distance)
                if a.health.liquidation_distance is not None
                else None
            ),
            "health": a.health.level.value,
        }
    return {}


@router.post("/pairs/{pair_id}/close", response_model=ExecutionOut)
async def pairs_close(request: Request, pair_id: int) -> ExecutionOut:
    try:
        ergebnis = await _engine(request).close_pair(pair_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExecutionOut(
        pair_id=ergebnis.pair_id,
        state=ergebnis.state.value,
        detail=ergebnis.detail,
        dry_run=get_settings().dry_run,
    )


@router.post("/pairs/{pair_id}/resolve", response_model=ExecutionOut)
async def pairs_resolve(request: Request, pair_id: int, body: ResolveRequestIn) -> ExecutionOut:
    """Loest einen UNHEDGED-Zustand auf: Gegenseite nachziehen oder zurueckrollen."""
    try:
        ergebnis = await _engine(request).resolve_unhedged(pair_id, action=body.action)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExecutionOut(
        pair_id=ergebnis.pair_id,
        state=ergebnis.state.value,
        detail=ergebnis.detail,
        dry_run=get_settings().dry_run,
    )


@router.post("/panic", response_model=PanicOut)
async def panic(request: Request) -> PanicOut:
    """Kill Switch: alles stornieren, alles schliessen."""
    ergebnis = await _engine(request).panic()
    return PanicOut(
        cancelled_orders=ergebnis.cancelled_orders,
        closed_positions=ergebnis.closed_positions,
        errors=ergebnis.errors,
        dry_run=get_settings().dry_run,
    )


def _rebalance_leg(bein) -> Optional[RebalanceLegOut]:
    if bein is None:
        return None
    return RebalanceLegOut(
        venue=bein.venue,
        side=bein.side.value,
        size=bein.size,
        reduce_only=bein.reduce_only,
        estimated_fee=bein.estimated_fee,
        resulting_size=bein.resulting_size,
        resulting_notional=bein.resulting_notional,
    )


@router.get("/pairs/{pair_id}/rebalance", response_model=RebalancePlanOut)
async def rebalance_preview(request: Request, pair_id: int) -> RebalancePlanOut:
    """Beide Wege durchgerechnet - aufstocken oder verkleinern."""
    try:
        plan = await _trading(request).rebalance_plan(pair_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RebalanceError as exc:
        return RebalancePlanOut(
            symbol="", difference=Decimal(0), balanced=True, error=str(exc)
        )

    return RebalancePlanOut(
        symbol=plan.symbol,
        difference=plan.difference,
        balanced=plan.balanced,
        increase=_rebalance_leg(plan.increase),
        decrease=_rebalance_leg(plan.decrease),
    )


@router.post("/pairs/{pair_id}/rebalance", response_model=ExecutionOut)
async def rebalance_execute(
    request: Request, pair_id: int, body: RebalanceRequestIn
) -> ExecutionOut:
    dienst = _trading(request)
    try:
        await dienst.rebalance(pair_id, action=body.action)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RebalanceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ExecutionOut(
        pair_id=pair_id,
        state=_store(request).get_state(pair_id).value,
        detail="neu ausgerichtet",
        dry_run=get_settings().dry_run,
    )


@router.get("/summary", response_model=SummaryOut)
async def summary(request: Request) -> SummaryOut:
    """Gesamtkapital und Netto-Funding des laufenden UTC-Tages."""
    from datetime import datetime, time, timezone

    tagesbeginn = datetime.combine(
        datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc
    )

    tagebuch = _journal(request)
    kontostaende = await _account(request).balances()

    return SummaryOut(
        total_equity=sum((b.equity for b in kontostaende), Decimal(0)),
        funding_today=tagebuch.funding_total(since=tagesbeginn),
        # Getrennt ausgewiesen: gerechnete Betraege sind keine bestaetigten.
        funding_today_confirmed=tagebuch.funding_total(
            since=tagesbeginn, confirmed_only=True
        ),
        day_start=tagesbeginn,
        open_pairs=len(_store(request).open_pairs()),
    )
