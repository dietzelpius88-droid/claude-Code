"""Entitaets-Sentiment: Lob und Kritik zu einem namentlich genannten Instrument.

Warum das noetig ist
--------------------
Ein Begriffslexikon kennt nur, was jemand eingetragen hat. Der Satz

    "Ich habe mir Hyperliquid angesehen, diese Plattform ist beeindruckend,
     grossartige Technologie, wir unterstuetzen das."

enthaelt KEINEN politischen Fachbegriff - und bewegt den Kurs trotzdem
zweistellig, weil ein Amtstraeger ein kleines, duennes Asset namentlich lobt.

Diese Stufe kombiniert deshalb zwei Signale:
    1. Wird ein Instrument des Universums NAMENTLICH genannt?
    2. Ist die Aussage darueber wertend positiv oder negativ?

Nur beides zusammen ergibt ein Signal. Lob ohne Bezug ist Rauschen, ein Name
ohne Wertung ist eine Erwaehnung.

Bewusste Einschraenkung
-----------------------
Der Pfad ist nur fuer Anlageklassen freigegeben, bei denen die blosse
Aeusserung einer Autoritaet nachweislich den Preis bewegt (Krypto, Energie).
Bei Aktienindizes oder Anleihen bewegt nicht das Lob den Kurs, sondern die
dahinterstehende Politik - dort greift das Begriffslexikon.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..models import EventClass

# Cluster -> Ereignisklasse, fuer die dieser Pfad zugelassen ist.
ALLOWED_CLUSTERS: dict[str, EventClass] = {
    "crypto_major": EventClass.CRYPTO_POLICY,
    "crypto_beta": EventClass.CRYPTO_POLICY,
    "energy": EventClass.ENERGY_POLICY,
}

# (Muster, Polaritaet, Gewicht)
VALENCE: tuple[tuple[str, float, float], ...] = (
    # --- positiv ---
    (r"\bimpressive\b|\bbeeindruckend\b", +0.70, 1.0),
    (r"\btremendous\b|\bincredible\b|\bamazing\b|\bfantastic\b|\bphenomenal\b|"
     r"\bgrossartig\b|\bfantastisch\b", +0.80, 1.0),
    (r"\bwe (?:will )?support\b|\bi support\b|\bwe back\b|\bwir unterst(?:ue|ü)tzen\b|"
     r"\bendorse[sd]?\b", +0.85, 1.3),
    (r"\bembrace[sd]?\b|\bwe love\b|\bbig fan\b|\bi like\b|\bwir setzen auf\b", +0.70, 1.1),
    (r"\bthe future\b|\bfuture of (?:money|finance)\b|\bzukunft (?:des|der)\b", +0.60, 0.9),
    (r"\bwill (?:buy|hold|accumulate)\b|\breserve\b|\bstockpile\b|\bkaufen wir\b", +0.85, 1.4),
    (r"\bworld[- ]class\b|\bbest in\b|\brevolution(?:ary|aer|är)\b|\bgame[- ]chang", +0.70, 1.0),
    (r"\bgreen ?light\b|\bapprove[sd]?\b|\bgenehmigt\b|\bfreigegeben\b", +0.80, 1.2),
    # --- negativ ---
    (r"\bfraud\b|\bscam\b|\bponzi\b|\bbetrug\b|\bschneeballsystem\b", -0.95, 1.5),
    (r"\bworthless\b|\bwertlos\b|\bthin air\b|\bnothing behind it\b", -0.85, 1.3),
    (r"\bdangerous\b|\bgef(?:ae|ä)hrlich\b|\bthreat to\b|\bbedrohung\b", -0.75, 1.1),
    (r"\bbubble\b|\bblase\b|\boverva?lued\b|\b(?:ue|ü)berbewertet\b", -0.65, 1.0),
    (r"\bdisaster\b|\bterrible\b|\bkatastroph", -0.80, 1.1),
    (r"\bshut (?:it |them )?down\b|\bcrack down\b|\bwe will stop\b|\bstoppen wir\b", -0.90, 1.4),
    (r"\bi (?:do not|don'?t) like\b|\bnot a fan\b|\bhalte nichts von\b", -0.70, 1.1),
    (r"\bdo not (?:buy|touch)\b|\bavoid\b|\bfinger weg\b", -0.80, 1.2),
)

_VALENCE_RX = tuple((re.compile(p, re.I), pol, w) for p, pol, w in VALENCE)

# Verneinung und Abschwaechung wie im Hauptlexikon.
_NEG = re.compile(r"\b(not|no|never|isn'?t|doesn'?t|nicht|kein|keine)\b", re.I)
_HEDGE = re.compile(r"\b(maybe|perhaps|some(?:what|people)|they say|angeblich|"
                    r"vielleicht|manche)\b", re.I)
# Distanzierung: Lob, das jemand anderem zugeschrieben wird.
_THIRD_PARTY = re.compile(r"\b(?:they|people|critics|analysts|some) (?:say|think|believe|call)\b"
                          r"|\blaut\b|\bangeblich\b", re.I)

_TANH_SCALE = 1.5
_CONF_SCALE = 1.2
# Wertende Aussagen sind weicher als politische Fachbegriffe.
_SOFTNESS = 0.80


@dataclass(slots=True)
class EntitySentimentResult:
    event_class: EventClass
    sentiment: float
    confidence: float
    specificity: float
    symbols: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    mention_strength: float = 0.0


def score_entity_sentiment(text: str, mentions: dict[str, float],
                           cluster_of: dict[str, str]) -> EntitySentimentResult | None:
    """`mentions` ist {Symbol: Nennungsstaerke}, `cluster_of` {Symbol: Cluster}."""
    if not text or not mentions:
        return None

    # Nur zugelassene Cluster, und nur ausreichend direkte Nennungen.
    eligible: list[tuple[str, float, EventClass]] = []
    for sym, strength in mentions.items():
        ec = ALLOWED_CLUSTERS.get(cluster_of.get(sym, ""))
        if ec is not None and strength >= 0.5:
            eligible.append((sym, strength, ec))
    if not eligible:
        return None

    raw = 0.0
    total_weight = 0.0
    matched: list[str] = []
    for rx, pol, w in _VALENCE_RX:
        m = rx.search(text)
        if not m:
            continue
        matched.append(m.group(0).lower())
        left = text[max(0, m.start() - 45): m.start()]
        mult = 1.0
        if _NEG.search(left):
            mult *= -0.85
        if _HEDGE.search(left):
            mult *= 0.5
        if _THIRD_PARTY.search(left):
            mult *= 0.45
        raw += pol * w * mult
        total_weight += w * abs(mult)

    if not matched:
        return None

    best_sym, best_strength, ec = max(eligible, key=lambda t: t[1])
    symbols = [s for s, _, _ in sorted(eligible, key=lambda t: -t[1])]

    sentiment = math.tanh(raw / _TANH_SCALE)
    # Vertrauen haengt an BEIDEM: Klarheit der Wertung und Direktheit der Nennung.
    confidence = (1.0 - math.exp(-total_weight / _CONF_SCALE)) * best_strength * _SOFTNESS
    # Eine namentliche Nennung ist konkret - das ist der Kern dieses Pfades.
    specificity = 0.18 + 0.38 * best_strength

    return EntitySentimentResult(
        event_class=ec,
        sentiment=round(sentiment, 4),
        confidence=round(confidence, 4),
        specificity=round(min(1.0, specificity), 4),
        symbols=symbols,
        matched=matched,
        mention_strength=best_strength,
    )
