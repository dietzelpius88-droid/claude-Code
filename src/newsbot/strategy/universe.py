"""Handelsuniversum: welches Instrument reagiert wie stark auf welche Ereignisklasse.

Beta-Konvention
---------------
`signed_move = sentiment * beta`.  Ein NEGATIVES Beta bedeutet gegenlaeufige
Reaktion (Gold bei Zoellen, Anleihen bei Risk-off, Oel bei Krieg).  Dadurch
braucht der Code keine Sonderfaelle: die Richtung ist immer das Vorzeichen
des Produkts.

Die Betas sind Startwerte aus Ereignisstudien (siehe `backtest/event_study.py`).
Sie gehoeren regelmaessig neu geschaetzt - `scripts/recalibrate_betas.py`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import fields, replace
from pathlib import Path

from ..models import Asset, EventClass

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Startuniversum. adv_usd / daily_vol_bps / spread sind Groessenordnungen und
# werden im Betrieb aus Live-Marktdaten fortgeschrieben.
# --------------------------------------------------------------------------
DEFAULT_ASSETS: tuple[Asset, ...] = (
    # ---------------- Krypto-Perpetuals ----------------------------------
    Asset("BTC", "hyperliquid", "perp", cluster="crypto_major",
          adv_usd=20_000_000_000, typical_spread_bps=1.0, daily_vol_bps=280,
          taker_fee_bps=2.5, max_leverage=10, lot_size=0.0001,
          betas={"crypto_policy": 1.00, "monetary_policy": 1.15, "trade_policy": 0.65,
                 "geopolitics": 0.50, "macro_data": 1.00, "fiscal_policy": 0.55}),
    Asset("ETH", "hyperliquid", "perp", cluster="crypto_major",
          adv_usd=9_000_000_000, typical_spread_bps=1.2, daily_vol_bps=350,
          taker_fee_bps=2.5, max_leverage=10, lot_size=0.001,
          betas={"crypto_policy": 1.30, "monetary_policy": 1.30, "trade_policy": 0.75,
                 "geopolitics": 0.55, "macro_data": 1.15, "fiscal_policy": 0.60}),
    Asset("SOL", "hyperliquid", "perp", cluster="crypto_beta",
          adv_usd=3_500_000_000, typical_spread_bps=2.0, daily_vol_bps=500,
          taker_fee_bps=2.5, max_leverage=10, lot_size=0.01,
          betas={"crypto_policy": 1.75, "monetary_policy": 1.60, "trade_policy": 0.85,
                 "geopolitics": 0.60, "macro_data": 1.30}),
    # Hohe Beta, duennes Buch: grosse Bewegung, aber Liquiditaets-Gates greifen frueh.
    Asset("HYPE", "hyperliquid", "perp", cluster="crypto_beta",
          adv_usd=350_000_000, typical_spread_bps=6.0, daily_vol_bps=780,
          taker_fee_bps=2.5, max_leverage=5, lot_size=0.1,
          betas={"crypto_policy": 2.40, "monetary_policy": 1.70, "trade_policy": 0.80,
                 "geopolitics": 0.50, "macro_data": 1.30}),
    Asset("DOGE", "hyperliquid", "perp", cluster="crypto_beta",
          adv_usd=1_200_000_000, typical_spread_bps=3.0, daily_vol_bps=650,
          taker_fee_bps=2.5, max_leverage=5, lot_size=10,
          betas={"crypto_policy": 2.00, "monetary_policy": 1.55, "macro_data": 1.20}),
    Asset("XRP", "hyperliquid", "perp", cluster="crypto_beta",
          adv_usd=1_800_000_000, typical_spread_bps=2.5, daily_vol_bps=520,
          taker_fee_bps=2.5, max_leverage=5, lot_size=1,
          betas={"crypto_policy": 2.10, "monetary_policy": 1.35, "macro_data": 1.05}),

    # ---------------- Aktienindizes ---------------------------------------
    Asset("ES", "cme", "future", cluster="equity_us",
          adv_usd=350_000_000_000, typical_spread_bps=0.5, daily_vol_bps=95,
          taker_fee_bps=0.3, max_leverage=20, lot_size=1,
          betas={"monetary_policy": 1.00, "trade_policy": 1.00, "fiscal_policy": 1.00,
                 "macro_data": 1.00, "geopolitics": 0.85, "crypto_policy": 0.10,
                 "energy_policy": -0.25}),
    Asset("NQ", "cme", "future", cluster="equity_us",
          adv_usd=250_000_000_000, typical_spread_bps=0.6, daily_vol_bps=130,
          taker_fee_bps=0.3, max_leverage=20, lot_size=1,
          betas={"monetary_policy": 1.35, "trade_policy": 1.25, "fiscal_policy": 1.10,
                 "macro_data": 1.30, "geopolitics": 0.90, "crypto_policy": 0.15,
                 "energy_policy": -0.30}),

    # ---------------- Zinsen / Devisen ------------------------------------
    # Rendite faellt bei Zinssenkung -> Anleihe steigt: positives Beta.
    Asset("ZN", "cme", "future", cluster="rates",
          adv_usd=120_000_000_000, typical_spread_bps=0.4, daily_vol_bps=35,
          taker_fee_bps=0.3, max_leverage=20, lot_size=1,
          betas={"monetary_policy": 1.20, "macro_data": 0.90, "trade_policy": -0.20,
                 "geopolitics": -0.45, "fiscal_policy": 0.35}),
    # Dollar faellt bei Zinssenkung, steigt bei Zoellen und Risk-off.
    Asset("DXY", "fx", "cfd", cluster="fx",
          adv_usd=200_000_000_000, typical_spread_bps=0.8, daily_vol_bps=40,
          taker_fee_bps=0.5, max_leverage=20, lot_size=0.1,
          betas={"monetary_policy": -0.55, "trade_policy": -0.40, "macro_data": -0.60,
                 "geopolitics": -0.35}),

    # ---------------- Rohstoffe -------------------------------------------
    # Gold: steigt bei Zinssenkung, Zoellen und Krieg.
    Asset("GC", "cme", "future", cluster="metals",
          adv_usd=80_000_000_000, typical_spread_bps=1.0, daily_vol_bps=90,
          taker_fee_bps=0.4, max_leverage=15, lot_size=1,
          betas={"monetary_policy": 0.95, "trade_policy": -0.55, "geopolitics": -0.85,
                 "macro_data": 0.40, "fiscal_policy": -0.30}),
    Asset("CL", "cme", "future", cluster="energy",
          adv_usd=90_000_000_000, typical_spread_bps=1.5, daily_vol_bps=180,
          taker_fee_bps=0.4, max_leverage=15, lot_size=1,
          betas={"energy_policy": 1.00, "geopolitics": -0.90, "trade_policy": 0.45,
                 "monetary_policy": 0.35, "macro_data": 0.55}),
    # Kupfer als Konjunktur-/Zollproxy.
    Asset("HG", "cme", "future", cluster="metals",
          adv_usd=25_000_000_000, typical_spread_bps=2.0, daily_vol_bps=140,
          taker_fee_bps=0.5, max_leverage=10, lot_size=1,
          betas={"trade_policy": 1.35, "macro_data": 1.00, "monetary_policy": 0.70,
                 "geopolitics": 0.40}),
)


# --------------------------------------------------------------------------
# Entitaets-Erkennung: Wort im Text -> betroffene Symbole.
# `strength` ist der Bonusfaktor fuer direkte Nennung (siehe selector.py).
# --------------------------------------------------------------------------
ENTITY_PATTERNS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    (r"\bhyperliquid\b|\bhype\b", ("HYPE",), 1.0),
    (r"\bbitcoin\b|\bbtc\b", ("BTC",), 1.0),
    (r"\bethereum\b|\beth\b|\bether\b", ("ETH",), 1.0),
    (r"\bsolana\b|\bsol\b", ("SOL",), 1.0),
    (r"\bdogecoin\b|\bdoge\b", ("DOGE",), 1.0),
    (r"\bripple\b|\bxrp\b", ("XRP",), 1.0),
    (r"\bcrypto(?:currenc(?:y|ies))?\b|\bdigital assets?\b|\bkryptow(?:ae|ä)hrung",
     ("BTC", "ETH", "SOL", "HYPE"), 0.55),
    (r"\bdefi\b|\bperpetuals?\b|\bdecentrali[sz]ed exchange\b|\bdex\b", ("HYPE", "SOL"), 0.7),
    (r"\bstablecoins?\b|\busdc\b|\busdt\b|\btether\b", ("ETH", "SOL", "BTC"), 0.6),
    (r"\bs&p\b|\bsp500\b|\bstock market\b|\baktienmarkt\b|\bwall street\b", ("ES",), 0.8),
    (r"\bnasdaq\b|\btech stocks?\b|\bsemiconductors?\b|\bchips?\b|\bhalbleiter\b", ("NQ",), 0.8),
    (r"\btreasur(?:y|ies)\b|\bbonds?\b|\byields?\b|\banleihen\b|\brenditen\b", ("ZN",), 0.8),
    (r"\bdollar\b|\bgreenback\b|\bdxy\b", ("DXY",), 0.8),
    (r"\bgold\b", ("GC",), 1.0),
    (r"\bcopper\b|\bkupfer\b", ("HG",), 0.9),
    (r"\boil\b|\bcrude\b|\bopec\b|\bpetroleum\b|\b(?:oe|ö)l\b", ("CL",), 0.9),
    # Laender-/Sektorbezug bei Zoellen: trifft Konjunkturproxys.
    (r"\bchina\b|\bchinese\b|\bbeijing\b", ("HG", "NQ", "ES"), 0.5),
    (r"\bmexico\b|\bcanada\b|\beuropean\b|\beurope\b|\beu\b|\bgermany\b|\beuropaeisch|\beuropäisch", ("ES", "HG"), 0.4),
)

_ENTITY_RX = tuple((re.compile(p, re.I), syms, w) for p, syms, w in ENTITY_PATTERNS)


class Universe:
    """Nachschlagewerk fuer Instrumente und Entitaeten."""

    def __init__(self, assets: list[Asset] | None = None) -> None:
        self._assets: dict[str, Asset] = {
            a.symbol: a for a in (assets if assets is not None else list(DEFAULT_ASSETS))
        }

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Universe":
        """Laedt das Universum; eine JSON-Datei ueberschreibt einzelne Felder."""
        uni = cls()
        if path is None:
            cand = Path("config/universe.json")
            path = cand if cand.exists() else None
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            valid = {f.name for f in fields(Asset)}
            for entry in data.get("assets", []):
                sym = entry.get("symbol")
                if not sym:
                    continue
                # Unbekannte Schluessel (z.B. "_kommentar") ignorieren, statt
                # den Start an einer Konfigurationsnotiz scheitern zu lassen.
                clean = {k: v for k, v in entry.items() if k in valid}
                unbekannt = set(entry) - valid
                if unbekannt:
                    log.debug("universe.json: ignoriere Felder %s bei %s",
                              sorted(unbekannt), sym)
                base = uni._assets.get(sym)
                if base is None:
                    uni._assets[sym] = Asset(**clean)
                else:
                    merged = {k: v for k, v in clean.items() if k != "symbol"}
                    if "betas" in merged:
                        merged["betas"] = {**base.betas, **merged["betas"]}
                    uni._assets[sym] = replace(base, **merged)
        return uni

    # ------------------------------------------------------------------
    def get(self, symbol: str) -> Asset | None:
        return self._assets.get(symbol)

    def all(self) -> list[Asset]:
        return [a for a in self._assets.values() if a.enabled]

    def candidates(self, ec: EventClass, min_abs_beta: float = 0.25) -> list[Asset]:
        """Alle Instrumente, die auf diese Ereignisklasse ueberhaupt reagieren."""
        return [a for a in self.all() if abs(a.beta_for(ec)) >= min_abs_beta]

    def update_market_stats(self, symbol: str, *, spread_bps: float | None = None,
                            daily_vol_bps: float | None = None,
                            adv_usd: float | None = None) -> None:
        """Rollierende Fortschreibung aus Live-Marktdaten."""
        a = self._assets.get(symbol)
        if a is None:
            return
        if spread_bps is not None:
            a.typical_spread_bps = 0.8 * a.typical_spread_bps + 0.2 * spread_bps
        if daily_vol_bps is not None:
            a.daily_vol_bps = 0.9 * a.daily_vol_bps + 0.1 * daily_vol_bps
        if adv_usd is not None:
            a.adv_usd = 0.9 * a.adv_usd + 0.1 * adv_usd

    # ------------------------------------------------------------------
    @staticmethod
    def extract_entities(text: str) -> dict[str, float]:
        """Text -> {Symbol: Nennungsstaerke 0..1}. Staerkste Nennung gewinnt."""
        hits: dict[str, float] = {}
        for rx, symbols, strength in _ENTITY_RX:
            if rx.search(text):
                for s in symbols:
                    hits[s] = max(hits.get(s, 0.0), strength)
        return hits
