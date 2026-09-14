"""Asset-Auswahl: welches Instrument profitiert am meisten von dieser Nachricht.

Vorgehen
--------
1. Kandidaten: alle Instrumente mit relevantem Beta zur Ereignisklasse.
2. Erwartete Bewegung je Kandidat (Vorzeichen inklusive).
3. Erwartete Kosten je Kandidat (Spread bei News, Markteinfluss, Gebuehren).
4. Ranking nach RISIKOADJUSTIERTEM Netto-Edge, nicht nach Rohbewegung -
   sonst gewinnt immer der duennste Schrottcoin.
5. Harte Liquiditaets- und Qualitaets-Gates.

Erwartungswert-Modell
---------------------
    move_bps = |sentiment| * impact(klasse) * |beta| * tagesvol_bps * nennungsbonus
    richtung = sign(sentiment * beta)

`impact` sagt, wie viele Tagesvolatilitaeten eine maximale Ueberraschung dieser
Klasse im Leitinstrument bewegt. Aus Ereignisstudien geschaetzt, nachkalibrierbar.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..config import ExecutionConfig, RiskConfig, SignalConfig
from ..models import (Asset, AnalyzedEvent, Direction, EventClass, Quote,
                      TradeSignal)
from .universe import Universe

log = logging.getLogger(__name__)

# Wie viele Tagesvolatilitaeten bewegt eine maximale Ueberraschung (|s| = 1)?
IMPACT_SCALE: dict[EventClass, float] = {
    EventClass.MONETARY_POLICY: 0.55,
    EventClass.GEOPOLITICS: 0.45,
    EventClass.TRADE_POLICY: 0.40,
    EventClass.ENERGY_POLICY: 0.40,
    EventClass.MACRO_DATA: 0.35,
    EventClass.CRYPTO_POLICY: 0.35,
    EventClass.FISCAL_POLICY: 0.30,
    EventClass.CORPORATE: 0.50,
    EventClass.UNKNOWN: 0.12,
}

# Waehrend einer Nachricht weitet sich der Spread. Faktor auf den Normalspread.
NEWS_SPREAD_MULT = 3.0
# Volatilitaet im Ereignisfenster gegenueber Normalzustand - steuert die Stopweite.
NEWS_VOL_MULT = 3.0
# Koeffizient des Wurzel-Markteinflussgesetzes: impact = Y * sigma * sqrt(Q/ADV).
IMPACT_COEFF = 0.6


@dataclass(slots=True)
class Candidate:
    asset: Asset
    direction: Direction
    expected_move_bps: float
    expected_cost_bps: float
    stop_bps: float
    mention: float
    quote: Quote | None
    rejected: str | None = None

    @property
    def net_edge_bps(self) -> float:
        return self.expected_move_bps - self.expected_cost_bps

    @property
    def rr(self) -> float:
        return 0.0 if self.stop_bps <= 0 else self.net_edge_bps / self.stop_bps


class AssetSelector:
    def __init__(self, universe: Universe, signal_cfg: SignalConfig,
                 risk_cfg: RiskConfig, exec_cfg: ExecutionConfig) -> None:
        self.universe = universe
        self.scfg = signal_cfg
        self.rcfg = risk_cfg
        self.ecfg = exec_cfg

    # ------------------------------------------------------------------
    def estimate_move_bps(self, event: AnalyzedEvent, asset: Asset,
                          mention: float) -> float:
        """Erwartete Bewegung in Basispunkten (immer positiv, Betrag)."""
        impact = IMPACT_SCALE.get(event.event_class, 0.12)
        beta = abs(asset.beta_for(event.event_class))
        if beta == 0.0:
            return 0.0
        # Direkte namentliche Nennung hebt die Reaktion deutlich an: der Markt
        # handelt die Schlagzeile woertlich, bevor er ueber Zusammenhaenge nachdenkt.
        mention_bonus = 1.0 + 0.6 * mention
        return abs(event.sentiment) * impact * beta * asset.daily_vol_bps * mention_bonus

    def estimate_cost_bps(self, asset: Asset, notional_usd: float,
                          quote: Quote | None) -> float:
        """Roundtrip-Kosten: Spread + Markteinfluss + Gebuehren."""
        spread = quote.spread_bps if quote and quote.spread_bps > 0 else asset.typical_spread_bps
        # Bei Nachrichten ist der Spread breiter als der Normalwert; wenn wir
        # bereits eine Live-Quote haben, ist die Weitung darin enthalten.
        if quote is None:
            spread *= NEWS_SPREAD_MULT
        adv = max(asset.adv_usd, 1.0)
        impact_bps = IMPACT_COEFF * asset.daily_vol_bps * math.sqrt(
            max(0.0, notional_usd) / adv
        )
        # Einstieg zahlt Spread + Einfluss, Ausstieg noch einmal (etwas guenstiger,
        # weil ohne Zeitdruck teilweise passiv).
        return (spread / 2.0 + impact_bps) * 1.75 + asset.taker_fee_bps * 2.0

    def estimate_stop_bps(self, asset: Asset, quote: Quote | None) -> float:
        """Stopweite aus der zu ERWARTENDEN Ereignis-Volatilitaet.

        Entscheidend ist das Maximum aus zwei Schaetzern:
          * der gemeldeten ATR (rueckwaertsgewandt - im Ruhezustand zu klein),
          * der aus der Tagesvolatilitaet abgeleiteten Ereignis-ATR.

        Nur die ATR zu nehmen waere ein teurer Fehler: ein Instrument mit
        780 bps Tagesvolatilitaet meldet im ruhigen Markt vielleicht 20 bps
        ATR. Ein daraus abgeleiteter 32-bps-Stop wird von der ersten
        Nachrichtenkerze abgeraeumt - und weil die Stopweite im Nenner des
        Chance-Risiko-Verhaeltnisses steht, wuerde genau dieses illiquide
        Instrument faelschlich an die Spitze der Rangliste rutschen.
        """
        atr_quiet = asset.daily_vol_bps / math.sqrt(1440.0)
        atr_news = atr_quiet * NEWS_VOL_MULT
        atr = max(atr_news, quote.atr_bps if quote is not None else 0.0)
        stop = self.rcfg.stop_atr_mult * atr
        return max(self.rcfg.min_stop_bps, min(self.rcfg.max_stop_bps, stop))

    # ------------------------------------------------------------------
    def build_candidates(self, event: AnalyzedEvent,
                         quotes: dict[str, Quote] | None = None) -> list[Candidate]:
        quotes = quotes or {}
        mentions = self.universe.extract_entities(event.news.text)
        # Vom Sprachmodell genannte Ziele zaehlen als starke Nennung.
        for sym in event.target_hints:
            mentions[sym] = max(mentions.get(sym, 0.0), 0.85)

        # Grobe Vorabgroesse fuer die Kostenschaetzung (das echte Sizing
        # kommt spaeter aus dem Risikomodul).
        probe_notional = self.rcfg.equity_usd * self.rcfg.max_notional_per_trade_pct * 0.5

        out: list[Candidate] = []
        for asset in self.universe.candidates(event.event_class):
            beta = asset.beta_for(event.event_class)
            signed = event.sentiment * beta
            if signed == 0.0:
                continue
            direction = Direction.LONG if signed > 0 else Direction.SHORT
            mention = mentions.get(asset.symbol, 0.0)
            q = quotes.get(asset.symbol)

            cand = Candidate(
                asset=asset,
                direction=direction,
                expected_move_bps=self.estimate_move_bps(event, asset, mention),
                expected_cost_bps=self.estimate_cost_bps(asset, probe_notional, q),
                stop_bps=self.estimate_stop_bps(asset, q),
                mention=mention,
                quote=q,
            )
            cand.rejected = self._gate(cand, q)
            out.append(cand)

        out.sort(key=lambda c: (c.rejected is not None, -c.rr))
        return out

    # ------------------------------------------------------------------
    def _gate(self, c: Candidate, q: Quote | None) -> str | None:
        """Harte Ausschlusskriterien. Gibt den Grund zurueck oder None."""
        if c.net_edge_bps < self.scfg.min_net_edge_bps:
            return f"netto-edge {c.net_edge_bps:.1f}bps < {self.scfg.min_net_edge_bps}"
        if c.rr < self.scfg.min_rr:
            return f"CRV {c.rr:.2f} < {self.scfg.min_rr}"
        if q is not None:
            if q.age_ms > self.ecfg.max_quote_age_ms:
                return f"Kurs veraltet ({q.age_ms} ms)"
            if q.spread_bps > self.ecfg.max_spread_bps:
                return f"Spread {q.spread_bps:.1f}bps > {self.ecfg.max_spread_bps}"
            if q.spread_bps > c.asset.typical_spread_bps * 6.0:
                return f"Spread-Explosion ({q.spread_bps:.1f} vs {c.asset.typical_spread_bps:.1f})"
            depth = q.depth_usd or (q.bid_size_usd + q.ask_size_usd)
            if depth and depth < self.ecfg.min_depth_usd:
                return f"Buchtiefe {depth:,.0f} USD < {self.ecfg.min_depth_usd:,.0f}"
        return None

    # ------------------------------------------------------------------
    def select(self, event: AnalyzedEvent,
               quotes: dict[str, Quote] | None = None) -> list[TradeSignal]:
        """Erzeugt bis zu `max_assets_per_event` Handelssignale."""
        cands = self.build_candidates(event, quotes)
        usable = [c for c in cands if c.rejected is None]
        if not usable:
            if cands:
                log.info("kein handelbares Instrument: bestes %s abgelehnt (%s)",
                         cands[0].asset.symbol, cands[0].rejected)
            return []

        picked = usable[: self.scfg.max_assets_per_event]
        signals: list[TradeSignal] = []
        for c in picked:
            ref_px = c.quote.mid if c.quote else 0.0
            # Take-Profit-Leiter. Zwei Anker, es gilt der groessere:
            #   * R-Vielfache aus der Risikokonfiguration (Untergrenze),
            #   * Anteile der erwarteten Bewegung (bei grossen Chancen massgeblich).
            # Ohne den zweiten Anker wuerde ein 8R-Ereignis bei 2R abgeschnitten.
            n = max(1, len(self.rcfg.scale_out))
            targets = []
            for i, (r_mult, _share) in enumerate(self.rcfg.scale_out):
                share_of_move = (i + 1) / (n + 1) * 1.05
                targets.append(max(r_mult * c.stop_bps,
                                   c.expected_move_bps * share_of_move))
            sig = TradeSignal(
                event=event,
                asset=c.asset,
                direction=c.direction,
                expected_move_bps=round(c.expected_move_bps, 2),
                expected_cost_bps=round(c.expected_cost_bps, 2),
                conviction=round(event.conviction, 4),
                stop_bps=round(c.stop_bps, 2),
                targets_bps=[round(t, 2) for t in targets],
                # Time-Stop: eine Halbwertszeit, gedeckelt auf 2 Stunden.
                time_stop_s=min(7200, max(120, event.half_life_s)),
                max_chase_bps=round(c.expected_move_bps * self.ecfg.max_chase_ratio, 2),
                reference_px=ref_px,
                notes=[f"beta={c.asset.beta_for(event.event_class):+.2f}",
                       f"nennung={c.mention:.2f}",
                       f"CRV={c.rr:.2f}"],
            )
            signals.append(sig)
        return signals
