"""Preflight-Pruefungen vor dem Oeffnen eines Paares (Auftrag 6.2).

Grundsatz: fehlende Daten fuehren nie zu einem stillschweigenden Bestehen.
Wer nicht weiss, ob genug Margin da ist, weiss nicht, ob genug Margin da ist -
und darf nicht ausfuehren.

Drei Zustaende je Pruefung:
  PASS - bestanden
  WARN - nicht bestanden, aber kein Grund zum Abbruch (sichtbar in der UI)
  FAIL - blockiert die Ausfuehrung
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Mapping, Optional

from app.core.breakeven import breakeven_hours, round_trip_cost
from app.core.funding import net_hourly_rate
from app.core.opportunities import price_deviation
from app.core.sizing import Sizing
from app.core.slippage import SlippageError, estimate_fill
from app.models.domain import Balance, FundingInfo, OrderBook, Side, SymbolRules

# Vorgaben, alle ueberschreibbar.
DEFAULT_MARGIN_SHARE = Decimal("0.5")  # Position belegt hoechstens die Haelfte
DEFAULT_MAX_PRICE_DEVIATION = Decimal("0.01")
DEFAULT_MAX_CLOCK_SKEW_SECONDS = Decimal(5)


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: CheckStatus
    detail: str = ""


@dataclass(frozen=True)
class PreflightInput:
    sizing: Sizing
    long_rules: SymbolRules
    short_rules: SymbolRules
    long_funding: FundingInfo
    short_funding: FundingInfo
    long_book: Optional[OrderBook] = None
    short_book: Optional[OrderBook] = None
    long_balance: Optional[Balance] = None
    short_balance: Optional[Balance] = None
    reachable: Mapping[str, bool] = field(default_factory=dict)
    clock_skew_seconds: Mapping[str, Optional[Decimal]] = field(default_factory=dict)
    max_slippage: Decimal = Decimal("0.002")
    long_leverage: Decimal = Decimal(1)
    short_leverage: Decimal = Decimal(1)
    margin_share: Decimal = DEFAULT_MARGIN_SHARE
    max_price_deviation: Decimal = DEFAULT_MAX_PRICE_DEVIATION
    max_clock_skew_seconds: Decimal = DEFAULT_MAX_CLOCK_SKEW_SECONDS


@dataclass(frozen=True)
class PreflightResult:
    checks: tuple[Check, ...]
    net_rate_hourly: Decimal
    net_apr: Decimal
    breakeven_hours: Optional[Decimal] = None
    estimated_cost: Optional[Decimal] = None
    long_slippage: Optional[Decimal] = None
    short_slippage: Optional[Decimal] = None

    @property
    def ok(self) -> bool:
        return all(p.status is not CheckStatus.FAIL for p in self.checks)

    @property
    def blocking_reasons(self) -> list[str]:
        return [f"{p.label}: {p.detail}" for p in self.checks if p.status is CheckStatus.FAIL]


def _erreichbarkeit(e: PreflightInput) -> Check:
    boersen = (e.sizing.long_venue, e.sizing.short_venue)

    nicht_erreichbar = [v for v in boersen if not e.reachable.get(v, False)]
    if nicht_erreichbar:
        return Check(
            key="erreichbarkeit",
            label="Boersen erreichbar, Zeitversatz",
            status=CheckStatus.FAIL,
            detail=f"nicht erreichbar: {', '.join(nicht_erreichbar)}",
        )

    unbekannt = [v for v in boersen if e.clock_skew_seconds.get(v) is None]
    zu_gross = [
        v
        for v in boersen
        if (s := e.clock_skew_seconds.get(v)) is not None and abs(s) > e.max_clock_skew_seconds
    ]
    if zu_gross:
        werte = ", ".join(f"{v}: {e.clock_skew_seconds[v]} s" for v in zu_gross)
        return Check(
            key="erreichbarkeit",
            label="Boersen erreichbar, Zeitversatz",
            status=CheckStatus.FAIL,
            detail=f"Zeitversatz ueber {e.max_clock_skew_seconds} s ({werte})",
        )
    if unbekannt:
        return Check(
            key="erreichbarkeit",
            label="Boersen erreichbar, Zeitversatz",
            status=CheckStatus.WARN,
            detail=f"Zeitversatz unbekannt fuer {', '.join(unbekannt)}",
        )

    return Check(
        key="erreichbarkeit",
        label="Boersen erreichbar, Zeitversatz",
        status=CheckStatus.PASS,
        detail="beide erreichbar, Zeitversatz im Rahmen",
    )


def _margin(e: PreflightInput) -> Check:
    label = "Freie Margin mit Puffer"
    seiten = (
        (e.sizing.long_venue, e.sizing.long_notional, e.long_balance, e.long_leverage, e.long_rules),
        (e.sizing.short_venue, e.sizing.short_notional, e.short_balance, e.short_leverage, e.short_rules),
    )

    fehlend = [v for v, _, b, _, _ in seiten if b is None]
    if fehlend:
        return Check(
            key="margin",
            label=label,
            status=CheckStatus.FAIL,
            detail=f"kein Kontostand fuer {', '.join(fehlend)} - ohne Zugangsdaten nicht pruefbar",
        )

    for venue, notional, _, hebel, regeln in seiten:
        if hebel <= 0:
            return Check(key="margin", label=label, status=CheckStatus.FAIL,
                         detail=f"{venue}: Hebel muss positiv sein, war {hebel}")
        erlaubt = regeln.max_leverage_for_notional(notional) or regeln.max_leverage
        if hebel > erlaubt:
            return Check(
                key="margin",
                label=label,
                status=CheckStatus.FAIL,
                detail=f"{venue}: Hebel {hebel} ueber dem Maximum {erlaubt} fuer {notional:.0f} USD",
            )

    knapp: list[str] = []
    for venue, notional, balance, hebel, _ in seiten:
        benoetigt = notional / hebel
        nutzbar = balance.available * e.margin_share
        if benoetigt > nutzbar:
            knapp.append(
                f"{venue}: braucht {benoetigt:.2f} USD, nutzbar {nutzbar:.2f} USD "
                f"({e.margin_share:.0%} von {balance.available:.2f})"
            )
    if knapp:
        return Check(key="margin", label=label, status=CheckStatus.FAIL, detail="; ".join(knapp))

    return Check(key="margin", label=label, status=CheckStatus.PASS,
                 detail=f"beide Seiten unter {e.margin_share:.0%} der freien Margin")


def _preisabweichung(e: PreflightInput) -> Check:
    label = "Mark-Preise beider Boersen"
    abweichung = price_deviation(e.sizing.long_mark, e.sizing.short_mark)
    if abweichung is None:
        return Check(key="preisabweichung", label=label, status=CheckStatus.FAIL,
                     detail="Mark-Preise nicht verfuegbar")
    if abweichung > e.max_price_deviation:
        return Check(
            key="preisabweichung",
            label=label,
            status=CheckStatus.FAIL,
            detail=f"{abweichung:.2%} Abweichung, erlaubt sind {e.max_price_deviation:.2%}",
        )
    return Check(key="preisabweichung", label=label, status=CheckStatus.PASS,
                 detail=f"{abweichung:.3%} Abweichung")


def _tiefe_slippage(e: PreflightInput) -> tuple[Check, Optional[Decimal], Optional[Decimal]]:
    label = "Orderbuchtiefe und Slippage"
    fehlend = [
        v for v, b in ((e.sizing.long_venue, e.long_book), (e.sizing.short_venue, e.short_book))
        if b is None
    ]
    if fehlend:
        return (
            Check(key="tiefe_slippage", label=label, status=CheckStatus.FAIL,
                  detail=f"kein Orderbuch fuer {', '.join(fehlend)}"),
            None,
            None,
        )

    probleme: list[str] = []
    slippages: dict[str, Decimal] = {}
    for venue, buch, seite in (
        (e.sizing.long_venue, e.long_book, Side.LONG),
        (e.sizing.short_venue, e.short_book, Side.SHORT),
    ):
        try:
            schaetzung = estimate_fill(buch, seite, e.sizing.size)
        except SlippageError as exc:
            probleme.append(str(exc))
            continue
        slippages[venue] = schaetzung.slippage
        if not schaetzung.fully_filled:
            probleme.append(
                f"{venue}: Buch reicht nur fuer {schaetzung.filled_size} von {e.sizing.size}"
            )
        elif schaetzung.slippage > e.max_slippage:
            probleme.append(
                f"{venue}: Slippage {schaetzung.slippage:.3%} ueber dem Maximum "
                f"{e.max_slippage:.3%}"
            )

    lang = slippages.get(e.sizing.long_venue)
    kurz = slippages.get(e.sizing.short_venue)

    if probleme:
        return (
            Check(key="tiefe_slippage", label=label, status=CheckStatus.FAIL,
                  detail="; ".join(probleme)),
            lang,
            kurz,
        )

    return (
        Check(key="tiefe_slippage", label=label, status=CheckStatus.PASS,
              detail=f"Slippage long {lang:.3%}, short {kurz:.3%}"),
        lang,
        kurz,
    )


def _funding_vorzeichen(e: PreflightInput, netto: Decimal) -> Check:
    label = "Funding-Richtung"
    if netto <= 0:
        return Check(
            key="funding_vorzeichen",
            label=label,
            status=CheckStatus.FAIL,
            detail=(
                f"Netto {netto:+.4%} pro Stunde - in dieser Richtung zahlt das Paar drauf. "
                f"Long {e.sizing.long_venue} ({e.long_funding.rate_hourly:+.4%}/h), "
                f"Short {e.sizing.short_venue} ({e.short_funding.rate_hourly:+.4%}/h)"
            ),
        )
    return Check(
        key="funding_vorzeichen",
        label=label,
        status=CheckStatus.PASS,
        detail=(
            f"Netto {netto:+.4%} pro Stunde zugunsten des Paares "
            f"({netto * Decimal(24 * 365):+.1%} p. a.)"
        ),
    )


def run_preflight(e: PreflightInput) -> PreflightResult:
    """Fuehrt alle sechs Pruefungen aus und liefert ein Gesamturteil."""
    netto = net_hourly_rate(
        long_rate_hourly=e.long_funding.rate_hourly,
        short_rate_hourly=e.short_funding.rate_hourly,
    )

    tiefe_check, slip_long, slip_short = _tiefe_slippage(e)

    # Break-even: Gebuehren beider Seiten plus geschaetzter Spread.
    kosten: Optional[Decimal] = None
    stunden: Optional[Decimal] = None
    if e.long_rules.taker_fee is not None and e.short_rules.taker_fee is not None:
        spread = (slip_long or Decimal(0)) + (slip_short or Decimal(0))
        kosten = round_trip_cost(
            taker_fee_a=e.long_rules.taker_fee,
            taker_fee_b=e.short_rules.taker_fee,
            spread=spread,
        )
        stunden = breakeven_hours(cost=kosten, net_rate_hourly=netto)
        breakeven_check = Check(
            key="breakeven",
            label="Break-even",
            status=CheckStatus.PASS if stunden is not None else CheckStatus.WARN,
            detail=(
                f"{stunden:.1f} Stunden Mindesthaltedauer bei Kosten von {kosten:.4%}"
                if stunden is not None
                else "nicht bestimmbar, weil das Netto-Funding nicht positiv ist"
            ),
        )
    else:
        ohne = [
            r.venue for r in (e.long_rules, e.short_rules) if r.taker_fee is None
        ]
        breakeven_check = Check(
            key="breakeven",
            label="Break-even",
            status=CheckStatus.WARN,
            detail=(
                f"Gebuehren von {', '.join(ohne)} unbekannt - Mindesthaltedauer nicht "
                "berechenbar (Extended liefert sie erst mit Account-Zugang)"
            ),
        )

    checks = (
        _erreichbarkeit(e),
        _margin(e),
        _preisabweichung(e),
        tiefe_check,
        _funding_vorzeichen(e, netto),
        breakeven_check,
    )

    return PreflightResult(
        checks=checks,
        net_rate_hourly=netto,
        net_apr=netto * Decimal(24 * 365),
        breakeven_hours=stunden,
        estimated_cost=kosten,
        long_slippage=slip_long,
        short_slippage=slip_short,
    )
