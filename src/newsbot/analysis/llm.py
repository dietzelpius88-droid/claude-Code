"""Slow Path: semantische Auswertung durch ein Sprachmodell.

Rolle in der Pipeline
---------------------
Das Lexikon ist schnell, aber stumpf: es kennt nur die Begriffe, die jemand
eingetragen hat. Das Modell erkennt Umschreibungen ("wir werden Amerika zur
Heimat der digitalen Vermoegenswerte machen"), Ironie, Zitate und vor allem
NEUE Themen, fuer die noch keine Regel existiert.

Harte Regeln:
  * Hartes Timeout. Antwortet das Modell nicht rechtzeitig, gilt allein das
    Lexikon-Ergebnis - der Bot blockiert nie auf einer API.
  * Strukturierte Ausgabe ueber Tool-Use, nie freier Text.
  * Das Modell darf NICHT ueber Positionsgroesse oder Risiko entscheiden.
    Es liefert ausschliesslich eine Einschaetzung; Sizing und Limits bleiben
    deterministischer Code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import LLMConfig
from ..models import EventClass, RawNews

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the analysis stage of an event-driven trading system.
You receive a single news item (English or German) and must judge its immediate
market impact. Speed and calibration matter more than nuance.

Rules:
- `sentiment` is signed relative to the NATURAL leading instrument of the event class:
  monetary_policy -> risk assets (rate cut = +1)
  trade_policy    -> risk assets (new tariffs = -1)
  crypto_policy   -> crypto      (pro-crypto policy = +1)
  energy_policy   -> crude oil   (OPEC output cut = +1)
  macro_data      -> risk assets (hot inflation = -1)
  fiscal_policy   -> risk assets (tax cuts = +1)
  geopolitics     -> risk assets (escalation = -1)
- Distinguish ANNOUNCEMENT from SPECULATION. "signed an order" is decisive;
  "is considering" is weak; "critics warn he might" is near zero.
- A restatement of something already known is NOT tradable: set `is_restatement`
  true and keep confidence low.
- If the item contradicts or walks back an earlier headline, set
  `is_contradiction` true - the system will flatten open positions.
- `confidence` is your calibrated probability that the stated direction is the
  correct immediate market reaction. Be honest; 0.5 means coin flip.
- `specificity` measures concreteness: named amounts, dates, "effective
  immediately", signed documents = high. Vague sentiment = low.
- `targets` must come from the provided candidate symbols only, ordered by how
  directly the news hits them. Name an asset only if the news genuinely moves it.
- `half_life_s` is how long the impulse should dominate price before mean
  reversion: a speech remark 600-1200s, a signed tariff order 1800-3600s,
  an unexpected central bank decision 2400-5400s.

Never explain outside the tool call. Never suggest position size."""

TOOL_SCHEMA: dict[str, Any] = {
    "name": "submit_assessment",
    "description": "Return the structured market assessment for the news item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "event_class": {
                "type": "string",
                "enum": [e.value for e in EventClass],
            },
            "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "specificity": {"type": "number", "minimum": 0, "maximum": 1},
            "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "half_life_s": {"type": "integer", "minimum": 60, "maximum": 21600},
            "is_restatement": {"type": "boolean"},
            "is_contradiction": {"type": "boolean"},
            "rationale": {"type": "string", "maxLength": 240},
        },
        "required": ["event_class", "sentiment", "confidence", "specificity",
                     "targets", "half_life_s", "is_restatement",
                     "is_contradiction", "rationale"],
    },
}


@dataclass(slots=True)
class LLMVerdict:
    event_class: EventClass
    sentiment: float
    confidence: float
    specificity: float
    targets: list[str] = field(default_factory=list)
    half_life_s: int = 900
    is_restatement: bool = False
    is_contradiction: bool = False
    rationale: str = ""
    latency_ms: int = 0


# Signatur eines austauschbaren Backends (erleichtert Tests und Anbieterwechsel).
LLMBackend = Callable[[str, str, int], Awaitable[dict[str, Any]]]


class LLMAnalyzer:
    """Kapselt den Modellaufruf inklusive Timeout und Degradierung."""

    def __init__(self, cfg: LLMConfig, candidate_symbols: list[str],
                 backend: LLMBackend | None = None) -> None:
        self.cfg = cfg
        self.symbols = candidate_symbols
        self._backend = backend or self._default_backend()
        self.available = self._backend is not None
        self.stats = {"calls": 0, "timeouts": 0, "errors": 0}

    # ------------------------------------------------------------------
    def _default_backend(self) -> LLMBackend | None:
        if not self.cfg.enabled or not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:
            from anthropic import AsyncAnthropic  # optionale Abhaengigkeit
        except ImportError:
            log.warning("anthropic-Paket fehlt - Slow Path deaktiviert, nur Lexikon aktiv")
            return None

        client = AsyncAnthropic()

        async def _call(system: str, user: str, max_tokens: int) -> dict[str, Any]:
            resp = await client.messages.create(
                model=self.cfg.model,
                max_tokens=max_tokens,
                system=system,
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "submit_assessment"},
                messages=[{"role": "user", "content": user}],
            )
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use":
                    return dict(block.input)
            raise ValueError("Modellantwort enthielt keinen Tool-Aufruf")

        return _call

    # ------------------------------------------------------------------
    def build_prompt(self, news: RawNews, lexicon_hint: str = "") -> str:
        syms = ", ".join(self.symbols[: self.cfg.universe_hint_size])
        parts = [
            f"Source: {news.source_id} (trust tier {int(news.tier)}/5)",
            f"Author: {news.author or 'n/a'}",
            f"Age at receipt: {news.source_latency_ms} ms",
            f"Candidate symbols: {syms}",
        ]
        if lexicon_hint:
            parts.append(f"Rule-engine first pass: {lexicon_hint}")
        parts.append("")
        parts.append("HEADLINE: " + news.headline)
        if news.body:
            parts.append("BODY: " + news.body[:1800])
        return "\n".join(parts)

    # ------------------------------------------------------------------
    async def analyze(self, news: RawNews, lexicon_hint: str = "") -> LLMVerdict | None:
        """Gibt None zurueck, wenn nicht verfuegbar, zu langsam oder fehlerhaft."""
        if self._backend is None:
            return None

        prompt = self.build_prompt(news, lexicon_hint)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        attempts = max(1, self.cfg.max_retries + 1)

        # Ein Versuch lohnt nur, wenn noch nennenswert Budget uebrig ist.
        # Die Untergrenze muss am konfigurierten Timeout haengen, sonst wuerde
        # ein bewusst sehr knappes Budget das Modell stillschweigend abschalten.
        min_budget = min(0.05, self.cfg.timeout_ms / 1000.0 * 0.5)
        for attempt in range(attempts):
            remaining = self.cfg.timeout_ms / 1000.0 - (loop.time() - t0)
            if remaining <= min_budget:
                break
            try:
                self.stats["calls"] += 1
                raw = await asyncio.wait_for(
                    self._backend(SYSTEM_PROMPT, prompt, self.cfg.max_tokens),
                    timeout=remaining,
                )
                return self._parse(raw, int((loop.time() - t0) * 1000))
            except asyncio.TimeoutError:
                self.stats["timeouts"] += 1
                log.warning("LLM-Timeout nach %d ms - weiter nur mit Lexikon",
                            self.cfg.timeout_ms)
                return None
            except Exception as exc:  # Netzwerk, Rate-Limit, Parsefehler
                self.stats["errors"] += 1
                log.warning("LLM-Fehler (Versuch %d/%d): %s", attempt + 1, attempts, exc)
        return None

    # ------------------------------------------------------------------
    def _parse(self, raw: dict[str, Any], latency_ms: int) -> LLMVerdict:
        def _f(key: str, lo: float, hi: float, default: float) -> float:
            try:
                return max(lo, min(hi, float(raw.get(key, default))))
            except (TypeError, ValueError):
                return default

        try:
            ec = EventClass(str(raw.get("event_class", "unknown")))
        except ValueError:
            ec = EventClass.UNKNOWN

        targets = [str(t).upper() for t in raw.get("targets", []) if isinstance(t, (str, int))]
        # Halluzinierte Symbole verwerfen - nur bekanntes Universum zaehlt.
        targets = [t for t in targets if t in set(self.symbols)]

        return LLMVerdict(
            event_class=ec,
            sentiment=_f("sentiment", -1.0, 1.0, 0.0),
            confidence=_f("confidence", 0.0, 1.0, 0.0),
            specificity=_f("specificity", 0.0, 1.0, 0.3),
            targets=targets,
            half_life_s=int(_f("half_life_s", 60, 21_600, 900)),
            is_restatement=bool(raw.get("is_restatement", False)),
            is_contradiction=bool(raw.get("is_contradiction", False)),
            rationale=str(raw.get("rationale", ""))[:240],
            latency_ms=latency_ms,
        )


def make_stub_backend(responses: dict[str, dict[str, Any]],
                      delay_ms: int = 0) -> LLMBackend:
    """Deterministisches Backend fuer Tests und Replay ohne API-Zugang.

    Zuordnung ueber Teilstring der Schlagzeile im Prompt.
    """
    async def _call(system: str, user: str, max_tokens: int) -> dict[str, Any]:
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000.0)
        for key, payload in responses.items():
            if key.lower() in user.lower():
                return payload
        raise ValueError("Kein Stub-Treffer fuer diesen Prompt")
    return _call
