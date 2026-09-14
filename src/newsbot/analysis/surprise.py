"""Auswertung geplanter Konjunkturdaten: Ueberraschung statt Wortlaut.

Bei CPI, NFP, PCE & Co. zaehlt nicht der Text, sondern die Abweichung vom
Konsens. Der Bot bewertet die standardisierte Ueberraschung

    z = (actual - consensus) / sigma

und bildet sie ueber tanh auf ein Sentiment ab. `risk_sign` legt fest, ob ein
hoeherer Wert gut (+1) oder schlecht (-1) fuer Risiko-Assets ist.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

# sigma = typische Prognoseabweichung der jeweiligen Kennzahl (historisch).
INDICATORS: dict[str, dict[str, float | str]] = {
    "cpi_yoy":      {"sigma": 0.15, "risk_sign": -1, "label": "CPI YoY"},
    "core_cpi_yoy": {"sigma": 0.12, "risk_sign": -1, "label": "Kern-CPI YoY"},
    "cpi_mom":      {"sigma": 0.10, "risk_sign": -1, "label": "CPI MoM"},
    "pce_yoy":      {"sigma": 0.12, "risk_sign": -1, "label": "PCE YoY"},
    "ppi_yoy":      {"sigma": 0.25, "risk_sign": -1, "label": "PPI YoY"},
    "nfp":          {"sigma": 60.0, "risk_sign": +1, "label": "Nonfarm Payrolls (k)"},
    "unemployment": {"sigma": 0.15, "risk_sign": -1, "label": "Arbeitslosenquote"},
    "gdp_qoq":      {"sigma": 0.40, "risk_sign": +1, "label": "BIP QoQ"},
    "ism_mfg":      {"sigma": 1.50, "risk_sign": +1, "label": "ISM Industrie"},
    "retail_sales": {"sigma": 0.40, "risk_sign": +1, "label": "Einzelhandel MoM"},
    "fed_funds":    {"sigma": 0.10, "risk_sign": -1, "label": "Fed Funds Zielsatz"},
}

# Zuordnung Schlagzeilen-Stichwort -> Indikator.
_ALIASES: tuple[tuple[str, str], ...] = (
    (r"core cpi|kern[- ]?cpi|core consumer price", "core_cpi_yoy"),
    (r"\bcpi\b|consumer price index|verbraucherpreis", "cpi_yoy"),
    (r"core pce|\bpce\b", "pce_yoy"),
    (r"\bppi\b|producer price", "ppi_yoy"),
    (r"nonfarm payroll|non[- ]farm payroll|\bnfp\b", "nfp"),
    (r"unemployment rate|arbeitslosenquote|jobless rate", "unemployment"),
    (r"\bgdp\b|gross domestic|bruttoinlandsprodukt", "gdp_qoq"),
    (r"\bism\b manufacturing|ism industrie", "ism_mfg"),
    (r"retail sales|einzelhandelsums(?:ae|ä)tze", "retail_sales"),
    (r"fed funds|federal funds (?:rate|target)|leitzins", "fed_funds"),
)
_ALIAS_RX = tuple((re.compile(p, re.I), key) for p, key in _ALIASES)

# "3.2% vs 3.1% expected", "actual 250k, forecast 190k", "3,2 % erwartet 3,1 %"
_PAIR_RX = re.compile(
    r"(-?\d+(?:[.,]\d+)?)\s*(?:%|k|thousand|tausend)?\s*"
    r"(?:vs\.?|versus|gegen(?:ueber|über)?|against|,?\s*(?:est\.?|exp\.?|expected|forecast|"
    r"consensus|erwartet|prognose)\s*(?:of|:)?)\s*"
    r"(-?\d+(?:[.,]\d+)?)",
    re.I,
)


@dataclass(slots=True)
class SurpriseResult:
    indicator: str
    label: str
    actual: float
    consensus: float
    z: float
    sentiment: float        # -1..+1, bereits mit risk_sign ausgerichtet
    confidence: float

    @property
    def magnitude(self) -> float:
        return abs(self.z)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def detect_indicator(text: str) -> str | None:
    for rx, key in _ALIAS_RX:
        if rx.search(text):
            return key
    return None


def parse_surprise(text: str, *, consensus_override: float | None = None,
                   indicator: str | None = None) -> SurpriseResult | None:
    """Extrahiert Ist/Erwartung aus einer Schlagzeile und bewertet sie."""
    key = indicator or detect_indicator(text)
    if key is None or key not in INDICATORS:
        return None

    spec = INDICATORS[key]
    sigma = float(spec["sigma"])
    risk_sign = float(spec["risk_sign"])

    m = _PAIR_RX.search(text)
    if m:
        actual, consensus = _num(m.group(1)), _num(m.group(2))
    elif consensus_override is not None:
        nums = re.findall(r"-?\d+(?:[.,]\d+)?", text)
        if not nums:
            return None
        actual, consensus = _num(nums[0]), consensus_override
    else:
        return None

    z = (actual - consensus) / sigma if sigma > 0 else 0.0
    # tanh(z/2): +-2 sigma erreicht ~0.76 - starke, aber nicht maximale Reaktion.
    sentiment = risk_sign * math.tanh(z / 2.0)
    # Vertrauen steigt mit der Groesse der Ueberraschung; Rauschen unter 0.5 sigma
    # traegt praktisch nichts bei.
    confidence = min(1.0, abs(z) / 1.5)

    return SurpriseResult(
        indicator=key,
        label=str(spec["label"]),
        actual=actual,
        consensus=consensus,
        z=round(z, 3),
        sentiment=round(sentiment, 4),
        confidence=round(confidence, 4),
    )
