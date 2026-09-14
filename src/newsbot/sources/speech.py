"""Live-Auswertung gesprochener Sprache (Reden, Pressekonferenzen, Anhoerungen).

Das ist der Kanal mit dem groessten Vorsprung: Waehrend Agenturen erst
formulieren, liegt der Wortlaut bereits vor. Eine Bemerkung in einer Rede
("wir machen Amerika zur Krypto-Hauptstadt", "Hyperliquid ist beeindruckend")
bewegt Maerkte, bevor irgendein Feed sie meldet.

Architektur
-----------
    Audio-Stream (HLS/RTMP/Mikrofon)
        -> ASR (Whisper-Streaming, Deepgram, AssemblyAI, ...)
        -> TranscriptChunk-Strom            <-- hier setzt dieses Modul an
        -> Satzsegmentierung + Kontextfenster
        -> RawNews

Die ASR-Anbindung ist bewusst austauschbar: dieses Modul konsumiert nur einen
asynchronen Strom von Textstuecken. Damit laeuft derselbe Code gegen einen
Live-Dienst wie gegen eine aufgezeichnete Datei (Replay).

Besonderheiten gegenueber Text-Feeds
-----------------------------------
  * Teilsaetze: Ein Satz darf nicht erst nach seinem Ende ausgewertet werden,
    sonst verliert man Sekunden. Deshalb ein Zwangs-Flush nach `max_wait_ms`.
  * Kontext: "Das werden wir sofort machen" ist ohne die beiden Vorsaetze
    wertlos. Deshalb ein gleitendes Fenster.
  * ASR-Fehler: Eigennamen wie "Hyperliquid" werden oft falsch erkannt.
    `vocabulary_hints` korrigiert bekannte Verwechslungen vor der Auswertung.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ..models import RawNews, SourceTier, now_ms
from .base import NewsSource

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"[.!?](?:\s|$)|[.!?]['\"]?\s")

# Haeufige Fehlerkennungen der Spracherkennung bei Krypto-Eigennamen.
DEFAULT_VOCABULARY_HINTS: dict[str, str] = {
    r"\bhyper liquid\b|\bhyperliquid\b|\bhyper-liquid\b|\bhigher liquid\b": "Hyperliquid",
    r"\bbit coin\b|\bbitcoin\b": "Bitcoin",
    r"\bether(?:e|ium)\b|\betherium\b": "Ethereum",
    r"\bsalana\b|\bsolana\b": "Solana",
    r"\bdodge coin\b|\bdogecoin\b": "Dogecoin",
    r"\bbasis points?\b|\bbips\b|\bbeeps\b": "basis points",
    r"\btariffs?\b|\bterrifs?\b|\btariff's\b": "tariffs",
}


@dataclass(slots=True)
class TranscriptChunk:
    """Ein Stueck Transkript, wie es die Spracherkennung liefert."""

    text: str
    speaker: str = ""
    is_final: bool = True          # False = vorlaeufige Hypothese
    ts_ms: int = field(default_factory=now_ms)
    confidence: float = 1.0


class SpeechStreamSource(NewsSource):
    """Verwandelt einen Transkript-Strom in auswertbare Meldungen."""

    def __init__(self, source_id: str, chunks: AsyncIterator[TranscriptChunk],
                 tier: SourceTier = SourceTier.PRIMARY, *,
                 speaker: str = "", max_wait_ms: int = 1200,
                 min_chars: int = 25, context_sentences: int = 2,
                 vocabulary_hints: dict[str, str] | None = None,
                 min_confidence: float = 0.55) -> None:
        super().__init__(source_id, tier)
        self._chunks = chunks
        self.speaker = speaker
        self.max_wait_ms = max_wait_ms
        self.min_chars = min_chars
        self.min_confidence = min_confidence
        self._context: deque[str] = deque(maxlen=context_sentences)
        self._buffer = ""
        self._buffer_started_ms = 0
        hints = DEFAULT_VOCABULARY_HINTS if vocabulary_hints is None else vocabulary_hints
        self._hints = [(re.compile(p, re.I), r) for p, r in hints.items()]

    # ------------------------------------------------------------------
    def normalize(self, text: str) -> str:
        """Korrigiert bekannte Fehlerkennungen vor der Auswertung."""
        for rx, repl in self._hints:
            text = rx.sub(repl, text)
        return re.sub(r"\s+", " ", text).strip()

    # ------------------------------------------------------------------
    async def stream(self) -> AsyncIterator[RawNews]:
        async for chunk in self._chunks:
            # Vorlaeufige Hypothesen nur puffern, nie handeln: die Erkennung
            # revidiert sie oft, und ein Trade auf einem revidierten Wort ist
            # der teuerste denkbare Fehler.
            if not chunk.is_final:
                continue
            if chunk.confidence < self.min_confidence:
                log.debug("%s: Chunk verworfen (Konfidenz %.2f)",
                          self.source_id, chunk.confidence)
                continue

            if not self._buffer:
                self._buffer_started_ms = chunk.ts_ms
            self._buffer = (self._buffer + " " + chunk.text).strip()

            for sentence in self._drain(chunk.ts_ms):
                news = self._make_news(sentence, chunk)
                if news is not None:
                    yield news

        # Reststueck am Ende des Streams.
        if self._buffer.strip():
            news = self._make_news(self._buffer.strip(), None)
            self._buffer = ""
            if news is not None:
                yield news

    # ------------------------------------------------------------------
    def _drain(self, now: int) -> list[str]:
        """Zieht abgeschlossene Saetze aus dem Puffer."""
        out: list[str] = []
        while True:
            m = _SENTENCE_END.search(self._buffer)
            if not m:
                break
            sentence = self._buffer[:m.end()].strip()
            self._buffer = self._buffer[m.end():].lstrip()
            if len(sentence) >= self.min_chars:
                out.append(sentence)
            if self._buffer:
                self._buffer_started_ms = now

        # Zwangs-Flush: lieber einen Halbsatz auswerten als Sekunden verlieren.
        waited = now - self._buffer_started_ms
        if (not out and self._buffer and waited >= self.max_wait_ms
                and len(self._buffer) >= self.min_chars):
            out.append(self._buffer.strip())
            self._buffer = ""
            self._buffer_started_ms = now
        return out

    def _make_news(self, sentence: str, chunk: TranscriptChunk | None) -> RawNews | None:
        clean = self.normalize(sentence)
        if len(clean) < self.min_chars:
            self._context.append(clean)
            return None
        context = " ".join(self._context)
        self._context.append(clean)
        speaker = (chunk.speaker if chunk and chunk.speaker else self.speaker)
        return self.emit(
            clean,
            body=context,
            author=speaker,
            key=f"{self.source_id}:{clean[:80]}",
            published_ms=chunk.ts_ms if chunk else now_ms(),
            channel="speech", speaker=speaker)


async def transcript_from_lines(lines: list[tuple[int, str]], *, speaker: str = "",
                                realtime: bool = False
                                ) -> AsyncIterator[TranscriptChunk]:
    """Simulierter Transkript-Strom aus (offset_ms, text)-Paaren.

    Fuer Replay und Tests: dieselbe Redemechanik ohne ASR-Dienst.
    """
    import asyncio
    base = now_ms()
    prev = 0
    for offset_ms, text in lines:
        if realtime:
            await asyncio.sleep(max(0, offset_ms - prev) / 1000.0)
        prev = offset_ms
        yield TranscriptChunk(text=text, speaker=speaker, is_final=True,
                              ts_ms=base + offset_ms)
