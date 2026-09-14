"""Auswertungs-Pipeline: RawNews -> AnalyzedEvent.

Zwei-Geschwindigkeiten-Modell
-----------------------------
    T+0 ... 5 ms     Lexikon (regelbasiert)  -> ggf. PROVISORISCHES Ereignis
    T+5 ... 1200 ms  Sprachmodell            -> BESTAETIGTES Ereignis (Fusion)

Ein provisorisches Ereignis entsteht nur, wenn die regelbasierte Ueberzeugung
`min_conviction_instant` erreicht UND die Quelle vertrauenswuerdig genug ist.
Alles andere wartet auf die Bestaetigung. Damit ist der Bot bei eindeutigen
Meldungen ("Fed senkt Zinsen um 50 Basispunkte") in Millisekunden im Markt,
bei mehrdeutigen ("Trump erwaegt moeglicherweise...") aber geduldig.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..config import SignalConfig
from ..models import AnalyzedEvent, EventClass, RawNews, now_ms
from .dedup import NoveltyTracker
from .entity_sentiment import EntitySentimentResult, score_entity_sentiment
from .lexicon import HALF_LIFE_S, LexiconResult, score_text
from .llm import LLMAnalyzer, LLMVerdict
from .surprise import SurpriseResult, parse_surprise

log = logging.getLogger(__name__)

# Ab dieser Nennungsstaerke gilt ein Instrument als direkt benannt.
DIRECT_MENTION = 0.8


@dataclass(slots=True)
class _Component:
    """Ein Bewertungsbeitrag mit eigenem Gewicht und Selbstvertrauen."""

    name: str
    sentiment: float
    confidence: float
    weight: float


def fuse(components: list[_Component]) -> tuple[float, float]:
    """Gewichtete Fusion -> (sentiment, confidence).

    Regeln:
      * Ein Baustein, der nichts erkannt hat (confidence 0), verwaessert das
        Ergebnis NICHT - sein Gewicht wird auf die aktiven Bausteine verteilt.
        Sonst wuerde eine Meldung, die nur der Entitaets-Pfad versteht,
        systematisch unterbewertet.
      * Innerhalb der aktiven Bausteine zaehlt `weight * confidence`.
      * Bestaetigen sich mindestens zwei aktive Bausteine im Vorzeichen, steigt
        das Vertrauen um 15 % - unabhaengige Bestaetigung ist etwas wert.
    """
    active = [c for c in components if c.weight > 0 and c.confidence > 0]
    if not active:
        return 0.0, 0.0

    active_weight = sum(c.weight for c in active)
    idle_weight = sum(c.weight for c in components if c.weight > 0) - active_weight
    # Gewicht der stummen Bausteine proportional umlegen.
    scale = (active_weight + idle_weight) / active_weight if active_weight > 0 else 1.0

    num = sum(c.weight * scale * c.confidence * c.sentiment for c in active)
    den = sum(c.weight * scale * c.confidence for c in active)
    total = sum(c.weight * scale for c in active)
    sentiment = num / den if den else 0.0
    confidence = den / total if total else 0.0

    signs = {1 if c.sentiment > 0 else -1 for c in active if abs(c.sentiment) > 0.05}
    if len(active) >= 2 and len(signs) == 1:
        confidence = min(1.0, confidence * 1.15)

    return max(-1.0, min(1.0, sentiment)), max(0.0, min(1.0, confidence))


class AnalysisPipeline:
    def __init__(self, cfg: SignalConfig, llm: LLMAnalyzer | None = None,
                 novelty: NoveltyTracker | None = None,
                 universe=None) -> None:
        self.cfg = cfg
        self.llm = llm
        self.novelty = novelty or NoveltyTracker(
            window_s=cfg.dedup_window_s, similarity_threshold=cfg.dedup_similarity
        )
        # Fuer den Entitaets-Pfad: Namenserkennung und Cluster-Zuordnung.
        self.universe = universe
        self._cluster_of: dict[str, str] = (
            {a.symbol: a.cluster for a in universe.all()} if universe else {})

    # ------------------------------------------------------------------
    async def process(self, news: RawNews) -> AsyncIterator[AnalyzedEvent]:
        """Liefert 0-2 Ereignisse: erst provisorisch, dann bestaetigt."""
        text = news.text

        # ---- Stufe 0: Altersfilter ------------------------------------
        if news.source_latency_ms > self.cfg.max_news_age_ms:
            log.debug("verworfen (zu alt: %d ms): %s", news.source_latency_ms, news.headline[:70])
            return

        # ---- Stufe 1: Lexikon (Fast Path) -----------------------------
        lex = score_text(text)
        sur = parse_surprise(text)
        ent = self._entity_pass(text)

        # Neuigkeit: gleiche Aussage schon gesehen?
        claim = self._claim_signature(text, lex, ent)
        novelty = self.novelty.assess_and_register(text, claim=claim)

        fast_event = self._build_event(
            news, lex, sur, ent, llm=None, novelty=novelty, analyzer="lexicon"
        )

        instant_ok = (
            fast_event.conviction >= self.cfg.min_conviction_instant
            and int(news.tier) < self.cfg.confirm_required_from_tier
        )
        if instant_ok:
            fast_event.meta["provisional"] = True
            log.info("PROVISORISCH conv=%.2f %s | %s",
                     fast_event.conviction, fast_event.event_class.value, news.headline[:80])
            yield fast_event

        # ---- Stufe 2: Sprachmodell (Slow Path) ------------------------
        verdict: LLMVerdict | None = None
        if self.llm is not None and self.llm.available:
            hint = (f"class={lex.event_class.value} sentiment={lex.sentiment:+.2f} "
                    f"conf={lex.confidence:.2f} matched={lex.matched[:5]} "
                    f"modifiers={lex.modifiers}")
            verdict = await self.llm.analyze(news, hint)

        if verdict is None:
            # Kein Slow Path verfuegbar. Ohne provisorischen Trade jetzt
            # entscheiden, ob das Lexikon allein reicht.
            if not instant_ok and fast_event.conviction >= self.cfg.min_conviction:
                if int(news.tier) >= self.cfg.confirm_required_from_tier:
                    log.debug("verworfen (Quelle braucht Bestaetigung): %s", news.headline[:70])
                    return
                yield fast_event
            return

        # Widerspruch: das Modell erkennt ein Dementi / eine Ruecknahme.
        if verdict.is_contradiction:
            ev = self._build_event(news, lex, sur, ent, verdict, novelty, analyzer="llm")
            ev.meta["contradiction"] = True
            log.warning("WIDERSPRUCH erkannt: %s", news.headline[:80])
            yield ev
            return

        if verdict.is_restatement:
            novelty = min(novelty, 0.25)

        # Analysten-Dissens: Lexikon und Modell sind sich uneinig -> nicht handeln.
        if lex.confidence > 0.4 and verdict.confidence > 0.4:
            gap = abs(lex.sentiment - verdict.sentiment)
            if gap > self.cfg.max_analyzer_disagreement:
                log.warning("DISSENS lex=%+.2f llm=%+.2f (%.2f) -> kein Trade: %s",
                            lex.sentiment, verdict.sentiment, gap, news.headline[:70])
                if instant_ok:
                    ev = self._build_event(news, lex, sur, ent, verdict, 0.0, analyzer="fusion")
                    ev.meta["disagreement"] = round(gap, 3)
                    ev.meta["abort"] = True
                    yield ev
                return

        confirmed = self._build_event(news, lex, sur, ent, verdict, novelty, analyzer="fusion")
        confirmed.meta["confirms"] = fast_event.news.id if instant_ok else None
        if confirmed.conviction < self.cfg.min_conviction:
            if instant_ok:
                # Der schnelle Einstieg wird durch die Bestaetigung nicht getragen.
                confirmed.meta["abort"] = True
                log.info("BESTAETIGUNG SCHWACH conv=%.2f -> Abbruch: %s",
                         confirmed.conviction, news.headline[:70])
                yield confirmed
            return

        log.info("BESTAETIGT conv=%.2f %s s=%+.2f | %s", confirmed.conviction,
                 confirmed.event_class.value, confirmed.sentiment, news.headline[:80])
        yield confirmed

    # ------------------------------------------------------------------
    def _claim_signature(self, text: str, lex: LexiconResult,
                         ent: EntitySentimentResult | None) -> str:
        """Baut die inhaltliche Signatur der Aussage."""
        claim_class = (lex.event_class if lex.confidence > 0
                       else (ent.event_class if ent else lex.event_class))
        claim_sent = lex.sentiment if lex.confidence > 0 else (ent.sentiment if ent else 0.0)

        # Nur DIREKT genannte Instrumente gehen in die Signatur. Eine generische
        # Klassennennung ("Krypto") darf die spaetere, konkrete Nennung
        # ("Hyperliquid") nicht als Duplikat blockieren - der konkrete Satz ist
        # gerade der handelbare.
        targets = set(lex.target_hints)
        if self.universe is not None:
            targets.update(sym for sym, strength
                           in self.universe.extract_entities(text).items()
                           if strength >= DIRECT_MENTION)

        mods = set(lex.modifiers)
        if {"hedge", "conditional"} & mods:
            decisiveness = "spekulation"
        elif "intensifier" in mods or lex.specificity >= 0.55:
            decisiveness = "beschluss"
        else:
            decisiveness = "neutral"

        return NoveltyTracker.claim_signature(
            claim_class.value,
            1 if claim_sent > 0 else (-1 if claim_sent < 0 else 0),
            sorted(targets), decisiveness)

    def _entity_pass(self, text: str) -> EntitySentimentResult | None:
        """Lob/Kritik zu einem namentlich genannten Instrument."""
        if self.universe is None:
            return None
        mentions = self.universe.extract_entities(text)
        return score_entity_sentiment(text, mentions, self._cluster_of)

    def _build_event(self, news: RawNews, lex: LexiconResult,
                     sur: SurpriseResult | None, ent: EntitySentimentResult | None,
                     llm: LLMVerdict | None, novelty: float,
                     analyzer: str) -> AnalyzedEvent:
        comps = [
            _Component("lexicon", lex.sentiment, lex.confidence, self.cfg.w_lexicon),
            _Component("surprise", sur.sentiment if sur else 0.0,
                       sur.confidence if sur else 0.0, self.cfg.w_surprise),
            _Component("entity", ent.sentiment if ent else 0.0,
                       ent.confidence if ent else 0.0, self.cfg.w_entity),
        ]
        if llm is not None:
            comps.append(_Component("llm", llm.sentiment, llm.confidence, self.cfg.w_llm))

        sentiment, confidence = fuse(comps)

        # Ereignisklasse: das Modell darf das Lexikon ueberstimmen, wenn es
        # sicherer ist - typisch bei Themen ohne hinterlegte Regel.
        event_class = lex.event_class
        if ent is not None and ent.confidence > lex.confidence:
            event_class = ent.event_class
        if (llm is not None and llm.event_class is not EventClass.UNKNOWN
                and llm.confidence > max(lex.confidence, ent.confidence if ent else 0.0)):
            event_class = llm.event_class
        if event_class is EventClass.UNKNOWN and sur is not None:
            event_class = EventClass.MACRO_DATA

        specificity = max(lex.specificity, ent.specificity if ent else 0.0,
                          llm.specificity if llm else 0.0)
        half_life = llm.half_life_s if llm else lex.half_life_s
        if half_life <= 0:
            half_life = HALF_LIFE_S.get(event_class, 900)

        hints: list[str] = list(lex.target_hints)
        if ent:
            for t in ent.symbols:
                if t not in hints:
                    hints.append(t)
        if llm:
            for t in llm.targets:
                if t not in hints:
                    hints.append(t)

        return AnalyzedEvent(
            news=news,
            event_class=event_class,
            sentiment=round(sentiment, 4),
            confidence=round(confidence, 4),
            specificity=round(specificity, 4),
            novelty=round(novelty, 4),
            half_life_s=int(half_life),
            entities=lex.matched,
            target_hints=hints,
            rationale=(llm.rationale if llm else _local_rationale(lex, ent)),
            analyzer=analyzer,
            analyzed_ms=now_ms(),
            meta={
                "lex_sentiment": lex.sentiment,
                "lex_confidence": lex.confidence,
                "lex_modifiers": lex.modifiers,
                "entity_sentiment": ent.sentiment if ent else None,
                "entity_confidence": ent.confidence if ent else None,
                "entity_symbols": ent.symbols if ent else None,
                "llm_sentiment": llm.sentiment if llm else None,
                "llm_confidence": llm.confidence if llm else None,
                "llm_latency_ms": llm.latency_ms if llm else None,
                "surprise_z": sur.z if sur else None,
                "surprise_indicator": sur.indicator if sur else None,
                "pipeline_latency_ms": now_ms() - news.received_ms,
            },
        )


def _local_rationale(lex: LexiconResult, ent: EntitySentimentResult | None) -> str:
    """Kurzbegruendung ohne Sprachmodell - fuer Protokoll und Nachvollziehbarkeit."""
    parts: list[str] = []
    if lex.matched:
        parts.append(f"lexikon: {', '.join(lex.matched[:4])}")
    if lex.modifiers:
        parts.append(f"modifikatoren: {', '.join(lex.modifiers)}")
    if ent:
        parts.append(f"entitaet {'/'.join(ent.symbols[:2])}: {', '.join(ent.matched[:4])}")
    return " | ".join(parts) or "keine Treffer"
