"""Positionsgroesse.

Grundsatz: Die Groesse folgt aus dem ERLAUBTEN VERLUST, nicht aus der
erhofften Chance.

    risiko_usd = kapital * risiko_pro_trade * ueberzeugungsfaktor
    stueck     = risiko_usd / (stopweite_in_preis)

Danach greifen vier unabhaengige Deckel, von denen der kleinste gewinnt:
  1. maximaler Nominalwert je Trade,
  2. Anteil am Tagesvolumen (wir wollen den Markt nicht selbst bewegen),
  3. Anteil an der sichtbaren Buchtiefe (Ausfuehrbarkeit),
  4. Hebelgrenze (Konto und Instrument).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import RiskConfig
from ..models import Quote, TradeSignal


@dataclass(slots=True)
class SizingResult:
    qty: float
    notional_usd: float
    risk_usd: float
    stop_px: float
    entry_ref_px: float
    binding: str = "risk"                 # welcher Deckel gegriffen hat
    caps: dict[str, float] = field(default_factory=dict)
    ok: bool = True
    reason: str = ""


def conviction_multiplier(conviction: float, *, reference: float = 0.55,
                          lo: float = 0.35, hi: float = 2.0) -> float:
    """Skaliert das Risiko mit der Ueberzeugung.

    `reference` ist die Conviction, bei der volles Standardrisiko gilt.
    Gedeckelt, damit ein einzelnes Ereignis nie das Konto dominiert.
    """
    if conviction <= 0:
        return 0.0
    return max(lo, min(hi, conviction / reference))


def size_position(signal: TradeSignal, quote: Quote, cfg: RiskConfig,
                  *, equity_usd: float | None = None,
                  gross_exposure_usd: float = 0.0) -> SizingResult:
    equity = cfg.equity_usd if equity_usd is None else equity_usd
    px = quote.mid
    if px <= 0:
        return SizingResult(0, 0, 0, 0, 0, ok=False, reason="kein gueltiger Kurs")

    # ---- 1. Risikobudget -------------------------------------------------
    mult = conviction_multiplier(signal.conviction)
    risk_pct = min(cfg.risk_per_trade_pct * mult, cfg.max_risk_per_trade_pct)
    risk_usd = equity * risk_pct

    stop_frac = signal.stop_bps / 10_000.0
    if stop_frac <= 0:
        return SizingResult(0, 0, 0, 0, px, ok=False, reason="Stopweite ist null")

    qty = risk_usd / (stop_frac * px)
    notional = qty * px

    # ---- 2. Deckel -------------------------------------------------------
    caps: dict[str, float] = {}
    caps["notional"] = equity * cfg.max_notional_per_trade_pct
    caps["adv"] = signal.asset.adv_usd * cfg.max_adv_participation
    depth = quote.depth_usd or (quote.bid_size_usd + quote.ask_size_usd)
    caps["book"] = depth * cfg.max_book_participation if depth > 0 else math.inf
    # Freier Hebelspielraum des Kontos abzueglich bereits offener Positionen.
    caps["leverage"] = max(0.0, equity * cfg.max_leverage - gross_exposure_usd)
    caps["asset_leverage"] = equity * signal.asset.max_leverage

    binding = "risk"
    limit = notional
    for name, cap in caps.items():
        if cap < limit:
            limit, binding = cap, name

    if limit < notional:
        notional = limit
        qty = notional / px
        # Bei gekappter Groesse sinkt auch das tatsaechliche Risiko.
        risk_usd = qty * stop_frac * px

    # ---- 3. Lotgroesse und Mindestordergroesse ---------------------------
    lot = signal.asset.lot_size or 0.0
    if lot > 0:
        qty = math.floor(qty / lot) * lot
    notional = qty * px
    risk_usd = qty * stop_frac * px

    if qty <= 0 or notional < signal.asset.min_notional_usd:
        return SizingResult(0, 0, 0, 0, px, binding=binding, caps=caps, ok=False,
                            reason=f"Groesse zu klein nach Deckel '{binding}' "
                                   f"({notional:.2f} USD)")

    # ---- 4. Stop-Preis ---------------------------------------------------
    d = 1.0 if signal.direction.value == "long" else -1.0
    stop_px = px * (1.0 - d * stop_frac)

    return SizingResult(
        qty=round(qty, 10),
        notional_usd=round(notional, 2),
        risk_usd=round(risk_usd, 2),
        stop_px=round(stop_px, 10),
        entry_ref_px=px,
        binding=binding,
        caps={k: (None if v == math.inf else round(v, 2)) for k, v in caps.items()},
        ok=True,
    )
