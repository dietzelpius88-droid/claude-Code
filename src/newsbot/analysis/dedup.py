"""Duplikat- und Neuigkeitserkennung.

Zweck: Der teuerste Fehler im News-Trading ist der Einstieg in eine Meldung,
die der Markt schon vor 20 Minuten gesehen hat. Dieses Modul liefert einen
`novelty`-Wert 0..1, der multiplikativ in die Conviction eingeht.

Zwei Ebenen:
  1. Textaehnlichkeit  - dieselbe Meldung ueber mehrere Feeds (Reuters -> Aggregator).
  2. Claim-Signatur    - inhaltlich gleiche Aussage in anderen Worten
                         (Klasse + Richtung + Kernentitaeten).
"""
from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass

from ..clock import now_ms

_WORD = re.compile(r"[a-z0-9äöüß]+")
_STOP = frozenset(
    "the a an of to in on for and or is are was were be been will would with at by from "
    "that this it as he she they we you i said says der die das ein eine und oder ist sind "
    "war den dem des im am auf fuer für mit von zu er sie es wir".split()
)


def normalize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def shingles(tokens: list[str], n: int = 3) -> set[str]:
    if len(tokens) < n:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


@dataclass(slots=True)
class _Entry:
    ts: float
    sig: str
    shing: set[str]
    claim: str


class NoveltyTracker:
    """Haelt ein Zeitfenster gesehener Meldungen und bewertet Neuigkeit."""

    def __init__(self, window_s: int = 1800, similarity_threshold: float = 0.82,
                 max_entries: int = 4000) -> None:
        self.window_s = window_s
        self.threshold = similarity_threshold
        self._entries: deque[_Entry] = deque(maxlen=max_entries)

    # ------------------------------------------------------------------
    def _prune(self, now: float) -> None:
        while self._entries and now - self._entries[0].ts > self.window_s:
            self._entries.popleft()

    @staticmethod
    def _exact_sig(text: str) -> str:
        return hashlib.sha1(" ".join(normalize(text)).encode()).hexdigest()

    @staticmethod
    def claim_signature(event_class: str, direction: int, entities: list[str],
                        decisiveness: str = "neutral") -> str:
        """Inhaltliche Signatur unabhaengig von der Formulierung.

        `decisiveness` trennt Erwaegung von Beschluss: "Trump erwaegt Zoelle"
        und "Trump unterzeichnet Zoelle" sind NICHT dieselbe Nachricht, auch
        wenn Klasse, Richtung und betroffene Maerkte uebereinstimmen. Ohne
        diese Unterscheidung wuerde die Spekulation den spaeteren Beschluss
        als Duplikat blockieren - und genau der ist der handelbare.
        """
        ents = ",".join(sorted(set(entities))[:6])
        return f"{event_class}|{direction:+d}|{ents}|{decisiveness}"

    # ------------------------------------------------------------------
    def assess(self, text: str, *, claim: str = "", now: float | None = None) -> float:
        """Gibt novelty 0..1 zurueck, OHNE die Meldung zu registrieren."""
        now = now_ms() / 1000.0 if now is None else now
        self._prune(now)
        if not self._entries:
            return 1.0

        sig = self._exact_sig(text)
        sh = shingles(normalize(text))
        worst = 0.0        # hoechste gefundene Aehnlichkeit
        worst_age = 0.0

        for e in self._entries:
            if e.sig == sig:
                sim = 1.0
            elif claim and e.claim and e.claim == claim:
                # Gleiche Aussage, andere Worte: als stark aehnlich werten.
                sim = max(self.threshold, jaccard(sh, e.shing))
            else:
                sim = jaccard(sh, e.shing)
            if sim > worst:
                worst, worst_age = sim, now - e.ts

        if worst < self.threshold:
            return 1.0

        # Je frischer das Duplikat, desto staerker der Abschlag: der Markt hat
        # gerade erst reagiert. Nach Ablauf des Fensters erholt sich novelty.
        recency = 1.0 - min(1.0, worst_age / max(1.0, self.window_s))
        return round(max(0.0, 1.0 - worst * (0.45 + 0.55 * recency)), 4)

    def register(self, text: str, *, claim: str = "", now: float | None = None) -> None:
        now = now_ms() / 1000.0 if now is None else now
        self._prune(now)
        self._entries.append(
            _Entry(ts=now, sig=self._exact_sig(text), shing=shingles(normalize(text)), claim=claim)
        )

    def assess_and_register(self, text: str, *, claim: str = "",
                            now: float | None = None) -> float:
        n = self.assess(text, claim=claim, now=now)
        self.register(text, claim=claim, now=now)
        return n

    def __len__(self) -> int:
        return len(self._entries)
