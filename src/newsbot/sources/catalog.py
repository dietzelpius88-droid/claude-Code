"""Quellenkatalog: laedt `config/sources.json` und baut lauffaehige Quellen.

Latenzklassen
-------------
    A  < 1 s      kommerzielle Wire-Feeds, Direktabfragen, Live-Spracherkennung,
                  verifizierte Originalaccounts
    B  1-15 s     schnelle Social-Abfragen, gute Behoerden-Feeds
    C  15-120 s   uebliche RSS-Feeds von Behoerden und Medien
    D  > 2 min    langsame Pflichtveroeffentlichungen

Praktische Folgerung: Mit Klasse C allein ist der erste Preisimpuls vorbei,
bevor man ueberhaupt liest. Klasse C ist Bestaetigungs- und Kontextmaterial,
nicht die Handelsgrundlage. Wer den ersten Impuls handeln will, braucht A.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..models import SourceTier
from .base import NewsSource
from .poller import JSONPollSource, federal_register_extract
from .rss import RSSSource

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceSpec:
    id: str
    type: str
    tier: int
    latency: str
    url: str = ""
    poll_s: float = 5.0
    category: str = ""
    note: str = ""


def load_specs(path: str | Path = "config/sources.json") -> list[SourceSpec]:
    p = Path(path)
    if not p.exists():
        log.warning("Quellenkatalog %s nicht gefunden", p)
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[SourceSpec] = []
    for category, entries in data.items():
        if category.startswith("_") or not isinstance(entries, list):
            continue
        for e in entries:
            out.append(SourceSpec(
                id=e.get("id", ""), type=e.get("type", "rss"),
                tier=int(e.get("tier", 3)), latency=e.get("latency", "C"),
                url=e.get("url", ""), poll_s=float(e.get("poll_s", 5.0)),
                category=category, note=e.get("note", "")))
    return out


def build_sources(specs: list[SourceSpec], *,
                  only_types: set[str] | None = None,
                  max_latency_class: str = "D") -> list[NewsSource]:
    """Erzeugt die Quellen, die ohne Zusatzzugang lauffaehig sind.

    `social`, `speech` und `websocket` brauchen Zugangsdaten bzw. einen
    Audiostrom und werden hier bewusst NICHT automatisch erzeugt - sie werden
    in `cli.py` bzw. im eigenen Betriebscode verdrahtet.
    """
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    limit = order.get(max_latency_class.upper(), 3)
    out: list[NewsSource] = []
    for s in specs:
        if order.get(s.latency.upper(), 3) > limit:
            continue
        if only_types and s.type not in only_types:
            continue
        if s.type == "rss" and s.url:
            out.append(RSSSource(s.id, s.url, SourceTier(s.tier), poll_s=s.poll_s))
        elif s.type == "json" and s.url and "federalregister" in s.url:
            out.append(JSONPollSource(s.id, s.url, SourceTier(s.tier),
                                      federal_register_extract, poll_s=s.poll_s))
        else:
            log.debug("Quelle %s (%s) braucht eigene Verdrahtung", s.id, s.type)
    return out


def summary(specs: list[SourceSpec]) -> str:
    """Uebersichtstabelle fuer die Kommandozeile."""
    rows = ["KATEGORIE                 ID                              TYP        TIER LAT  INTERVALL",
            "-" * 96]
    for s in sorted(specs, key=lambda x: (x.category, x.latency, x.id)):
        rows.append(f"{s.category[:24]:24s}  {s.id[:30]:30s}  {s.type:9s}  {s.tier:^4d} {s.latency:^3s}  "
                    f"{s.poll_s:>6.1f}s")
    return "\n".join(rows)
