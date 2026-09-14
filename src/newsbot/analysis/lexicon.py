"""Fast Path: regelbasierte Bewertung einer Meldung in < 5 ms.

Vorzeichen-Konvention
---------------------
`polarity` ist IMMER "bullish fuer das natuerliche Leitinstrument der
Ereignisklasse":

    monetary_policy  -> Risiko-Assets (Zinssenkung = +1)
    trade_policy     -> Risiko-Assets (neue Zoelle = -1)
    crypto_policy    -> Krypto        (Bitcoin-Reserve = +1)
    energy_policy    -> Oel           (OPEC kuerzt = +1)
    macro_data       -> Risiko-Assets (heisse Inflation = -1)

Gegenlaeufige Instrumente (Gold, Anleihen, VIX, Volatilitaets-Hedges) tragen
ein NEGATIVES Beta in `universe`. Die endgueltige Richtung ist damit immer
`sign(sentiment * beta)` - keine Sonderfaelle im Code.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..models import EventClass


@dataclass(frozen=True, slots=True)
class Term:
    pattern: str
    event_class: EventClass
    polarity: float           # -1 .. +1
    weight: float = 1.0       # Relevanz des Begriffs
    targets: tuple[str, ...] = ()   # direkt implizierte Symbole
    specificity: float = 0.0        # eigener Konkretheitsbeitrag


# --------------------------------------------------------------------------
# Begriffstabelle. Pattern sind Regex-Fragmente (case-insensitive, \b umrahmt).
# --------------------------------------------------------------------------
TERMS: tuple[Term, ...] = (
    # ---------------- Geldpolitik -----------------------------------------
    Term(r"rate cut|cut(?:s|ting)? (?:the )?(?:interest )?rates?|zinssenkung|senkt die zinsen",
         EventClass.MONETARY_POLICY, +1.0, 1.4, ("ES", "NQ", "BTC"), 0.3),
    Term(r"rate hike|raise(?:s|d)? rates?|hik(?:e|es|ing) rates?|zinserh(?:oe|ö)hung",
         EventClass.MONETARY_POLICY, -1.0, 1.4, ("ES", "NQ", "BTC"), 0.3),
    Term(r"\bdovish\b|accommodative|easing cycle|lockere geldpolitik",
         EventClass.MONETARY_POLICY, +0.8, 1.0),
    Term(r"\bhawkish\b|higher for longer|restrictive stance|straffere geldpolitik",
         EventClass.MONETARY_POLICY, -0.8, 1.0),
    Term(r"quantitative easing|\bqe\b|asset purchases|liquidity injection|bilanzausweitung",
         EventClass.MONETARY_POLICY, +0.9, 1.2),
    Term(r"quantitative tightening|\bqt\b|balance sheet runoff|bilanzabbau",
         EventClass.MONETARY_POLICY, -0.8, 1.1),
    Term(r"emergency (?:rate )?cut|intermeeting cut|notfallzinssenkung",
         EventClass.MONETARY_POLICY, +1.0, 2.0, (), 0.5),
    Term(r"pause (?:the )?(?:rate )?hikes?|hold rates steady|keine weiteren erh(?:oe|ö)hungen",
         EventClass.MONETARY_POLICY, +0.4, 0.8),
    Term(r"inflation (?:is|remains|stays) (?:still )?(?:too )?high|more work to do|"
         r"inflation nicht besiegt|inflation (?:ist|bleibt) zu hoch",
         EventClass.MONETARY_POLICY, -0.6, 0.9),
    Term(r"fire (?:the )?fed chair|replace (?:the )?fed chair|remove powell|powell entlassen",
         EventClass.MONETARY_POLICY, +0.5, 1.3, ("BTC", "GC"), 0.4),

    # ---------------- Handelspolitik / Zoelle ------------------------------
    Term(r"tariffs?|z(?:oe|ö)lle|z(?:oe|ö)llen|import dut(?:y|ies)|einfuhrz(?:oe|ö)lle",
         EventClass.TRADE_POLICY, -0.8, 1.3, (), 0.2),
    Term(r"impos(?:e|es|ing) (?:new )?tariffs?|slap(?:s|ping)? tariffs?|tariffs? on|"
         r"verh(?:ae|ä)ngt z(?:oe|ö)lle",
         EventClass.TRADE_POLICY, -1.0, 1.6, (), 0.4),
    Term(r"trade war|handelskrieg|retaliat(?:e|es|ion|ory)|vergeltungsz(?:oe|ö)lle",
         EventClass.TRADE_POLICY, -0.9, 1.3),
    Term(r"export (?:ban|controls?|restrictions?)|exportverbot|exportkontrollen",
         EventClass.TRADE_POLICY, -0.9, 1.3),
    Term(r"sanctions? on|new sanctions|sanktionen gegen",
         EventClass.TRADE_POLICY, -0.8, 1.2),
    Term(r"section 232|section 301|ieepa",
         EventClass.TRADE_POLICY, -0.6, 1.0, (), 0.5),
    Term(r"tariff (?:exemption|relief|carve[- ]out|rollback)|lift(?:s|ing)? (?:the )?tariffs?|"
         r"z(?:oe|ö)lle (?:aufgehoben|ausgesetzt)",
         EventClass.TRADE_POLICY, +1.0, 1.5, (), 0.4),
    Term(r"trade deal|trade agreement|handelsabkommen|framework agreement",
         EventClass.TRADE_POLICY, +0.9, 1.3),
    Term(r"lift(?:s|ing)? sanctions|remove(?:s|d)? sanctions|sanktionen aufgehoben",
         EventClass.TRADE_POLICY, +0.8, 1.2),

    # ---------------- Krypto-Politik / Regulierung --------------------------
    Term(r"strategic (?:bitcoin|crypto|digital asset) reserve|bitcoin reserve|"
         r"digital asset stockpile|strategische bitcoin[- ]reserve",
         EventClass.CRYPTO_POLICY, +1.0, 2.2, ("BTC",), 0.6),
    Term(r"crypto capital of the world|pro[- ]crypto|embrace (?:crypto|bitcoin|digital assets)|"
         r"krypto[- ]hauptstadt",
         EventClass.CRYPTO_POLICY, +0.85, 1.4),
    Term(r"(?:approve[sd]?|approval of) (?:a |the )?(?:spot )?(?:\w+ )?etf|etf (?:approved|genehmigt)",
         EventClass.CRYPTO_POLICY, +1.0, 1.8, (), 0.5),
    Term(r"clarity act|market structure bill|genius act|stablecoin (?:bill|act)|mica",
         EventClass.CRYPTO_POLICY, +0.7, 1.4, (), 0.5),
    Term(r"operation chokepoint|debank(?:ing)?|entbankung",
         EventClass.CRYPTO_POLICY, +0.5, 1.0),
    Term(r"crypto (?:crackdown|ban)|ban (?:on )?(?:crypto|bitcoin|stablecoins?)|krypto[- ]verbot",
         EventClass.CRYPTO_POLICY, -1.0, 1.9, (), 0.4),
    Term(r"enforcement action|wells notice|unregistered securit(?:y|ies)|sues? \w+ over crypto|"
         r"klage gegen",
         EventClass.CRYPTO_POLICY, -0.85, 1.4),
    Term(r"capital gains tax on crypto|tax(?:ing)? crypto|krypto[- ]steuer",
         EventClass.CRYPTO_POLICY, -0.6, 1.0),
    Term(r"cbdc|digital (?:dollar|euro)|zentralbankw(?:ae|ä)hrung",
         EventClass.CRYPTO_POLICY, -0.3, 0.7),

    # ---------------- Fiskalpolitik ---------------------------------------
    Term(r"tax cut|steuersenkung|lower(?:s|ing)? taxes",
         EventClass.FISCAL_POLICY, +0.9, 1.2, ("ES", "NQ")),
    Term(r"stimulus|konjunkturpaket|spending package|infrastructure bill|rebate checks?",
         EventClass.FISCAL_POLICY, +0.85, 1.2),
    Term(r"tax (?:hike|increase)|steuererh(?:oe|ö)hung|windfall tax",
         EventClass.FISCAL_POLICY, -0.8, 1.1),
    Term(r"government shutdown|debt ceiling|schuldenobergrenze|default on (?:the )?debt",
         EventClass.FISCAL_POLICY, -0.9, 1.4),

    # ---------------- Geopolitik ------------------------------------------
    Term(r"ceasefire|peace (?:deal|agreement|plan)|waffenruhe|friedensabkommen|de[- ]escalat",
         EventClass.GEOPOLITICS, +1.0, 1.6, (), 0.4),
    Term(r"invasion|invades?|airstrike|air strike|missile (?:attack|strike)|declares? war|"
         r"mobilization|luftangriff|angriff auf",
         EventClass.GEOPOLITICS, -1.0, 1.8, (), 0.4),
    Term(r"strait of hormuz|blockade|closes? the strait|seeblockade",
         EventClass.GEOPOLITICS, -0.9, 1.6, ("CL",), 0.5),
    Term(r"nuclear (?:test|threat)|atomwaffen",
         EventClass.GEOPOLITICS, -0.9, 1.5),

    # ---------------- Energie ---------------------------------------------
    Term(r"opec\+? (?:cut|cuts|reduces?) (?:output|production)|f(?:oe|ö)rderk(?:ue|ü)rzung|supply cut",
         EventClass.ENERGY_POLICY, +1.0, 1.5, ("CL",), 0.4),
    Term(r"opec\+? (?:raise|raises|increases?|boosts?) (?:output|production)|"
         r"f(?:oe|ö)rdermenge erh(?:oe|ö)ht",
         EventClass.ENERGY_POLICY, -1.0, 1.5, ("CL",), 0.4),
    Term(r"strategic petroleum reserve|spr release|drill baby drill|"
         r"strategische (?:oe|ö)lreserve",
         EventClass.ENERGY_POLICY, -0.7, 1.2, ("CL",)),
    Term(r"oil embargo|(?:oe|ö)lembargo|ban on (?:russian )?oil",
         EventClass.ENERGY_POLICY, +0.8, 1.3, ("CL",)),

    # ---------------- Makrodaten (Feinwertung via surprise.py) -------------
    Term(r"\bcpi\b|consumer price index|verbraucherpreis|inflation rate",
         EventClass.MACRO_DATA, 0.0, 1.2, (), 0.4),
    Term(r"nonfarm payrolls?|non[- ]farm|jobs report|arbeitsmarktbericht",
         EventClass.MACRO_DATA, 0.0, 1.2, (), 0.4),
    Term(r"\bpce\b|core pce|personal consumption expenditures",
         EventClass.MACRO_DATA, 0.0, 1.1, (), 0.4),
    Term(r"hotter than expected|above (?:forecast|estimates?)|(?:ue|ü)ber den erwartungen",
         EventClass.MACRO_DATA, -0.8, 1.3, (), 0.3),
    Term(r"cooler than expected|below (?:forecast|estimates?)|unter den erwartungen|"
         r"misses? (?:forecast|estimates?)",
         EventClass.MACRO_DATA, +0.8, 1.3, (), 0.3),
)


# --------------------------------------------------------------------------
# Modifikatoren. Sie werden im LINKEN Kontextfenster eines Treffers gesucht.
# --------------------------------------------------------------------------
NEGATION = re.compile(
    r"\b(not|no|never|won'?t|will not|doesn'?t|isn'?t|denie[sd]|denying|rules? out|"
    r"ruled out|reject(?:s|ed)?|rul(?:e|es|ed) out|dementiert|kein|keine|nicht|ausgeschlossen)\b",
    re.I,
)
HEDGE = re.compile(
    r"\b(could|may|might|considering|weighing|mulling|open to|exploring|studying|"
    r"potential(?:ly)?|possible|possibly|reportedly|sources say|rumou?r|hinted|suggest(?:s|ed)?|"
    r"would (?:consider|like)|plans? to|koennte|könnte|erwaegt|erwägt|angeblich|pr(?:ue|ü)ft)\b",
    re.I,
)
INTENSIFIER = re.compile(
    r"\b(immediately|effective immediately|signed?|signs|executive order|proclamation|"
    r"announc(?:e|es|ed|ing)|confirm(?:s|ed)?|official(?:ly)?|unanimous(?:ly)?|emergency|"
    r"massive|unprecedented|historic|sofort|unterzeichnet|verk(?:ue|ü)ndet|beschlossen|"
    r"in kraft)\b",
    re.I,
)
CONDITIONAL = re.compile(r"\b(if|unless|should we|in case|provided that|falls|sofern|wenn)\b", re.I)
RETROSPECTIVE = re.compile(
    r"\b(last (?:week|month|year)|yesterday|had (?:said|announced)|previously|earlier|"
    r"reiterat(?:e|es|ed)|repeat(?:s|ed)|as expected|in line with|wie erwartet|erneut|"
    r"bekr(?:ae|ä)ftigt|letzte woche)\b",
    re.I,
)
ATTRIBUTION = re.compile(
    r"\b(critics?|opponents?|accus(?:e|es|ed)|slammed|warn(?:s|ed) that|oppos(?:e|es|ed)|"
    r"kritisiert|wirft .* vor)\b",
    re.I,
)
QUESTION = re.compile(r"\?\s*$|^\s*(?:will|would|could|should|is|are|does)\b", re.I)

# Konkretheitssignale: Zahlen, Prozente, Basispunkte, Daten, Betraege.
SPECIFIC_NUM = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:%|percent|prozent|bps|basis points?|basispunkte)\b|"
    r"\$\s?\d+(?:[.,]\d+)?\s?(?:billion|million|trillion|mrd|mio)?|"
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"januar|februar|m(?:ae|ä)rz|mai|juni|juli|oktober|dezember)\s+\d{1,2}\b|"
    r"\b\d{1,2}\.\s?(?:januar|februar|m(?:ae|ä)rz|april|mai|juni|juli|august|september|oktober|"
    r"november|dezember)\b",
    re.I,
)

# Halbwertszeit des Preisimpulses je Klasse (Sekunden) -> Time-Stop.
HALF_LIFE_S: dict[EventClass, int] = {
    EventClass.MONETARY_POLICY: 2_700,
    EventClass.TRADE_POLICY: 1_800,
    EventClass.FISCAL_POLICY: 1_800,
    EventClass.CRYPTO_POLICY: 1_200,
    EventClass.GEOPOLITICS: 2_400,
    EventClass.MACRO_DATA: 900,
    EventClass.ENERGY_POLICY: 1_800,
    EventClass.CORPORATE: 1_200,
    EventClass.UNKNOWN: 600,
}

_MOD_WINDOW = 60          # Zeichen links vom Treffer
_TANH_SCALE = 1.5         # Saettigung der Rohsumme (ein starker Treffer ~0.6)
_CONF_SCALE = 1.1         # Saettigung des Vertrauens


# --------------------------------------------------------------------------
@dataclass(slots=True)
class LexiconResult:
    event_class: EventClass
    sentiment: float          # -1 .. +1
    confidence: float         # 0 .. 1
    specificity: float        # 0 .. 1
    half_life_s: int
    matched: list[str] = field(default_factory=list)
    target_hints: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    raw_score: float = 0.0
    class_weights: dict[str, float] = field(default_factory=dict)


_COMPILED: list[tuple[re.Pattern[str], Term]] = [
    (re.compile(rf"(?:\b|(?<=\W))(?:{t.pattern})", re.I), t) for t in TERMS
]


def score_text(text: str) -> LexiconResult:
    """Bewertet einen Text regelbasiert. Reine CPU-Arbeit, kein I/O."""
    if not text:
        return LexiconResult(EventClass.UNKNOWN, 0.0, 0.0, 0.0, HALF_LIFE_S[EventClass.UNKNOWN])

    raw = 0.0
    class_weights: dict[EventClass, float] = {}
    matched: list[str] = []
    targets: list[str] = []
    mods: list[str] = []
    spec_bonus = 0.0
    hedge_hits = 0
    total_weight = 0.0

    for rx, term in _COMPILED:
        m = rx.search(text)
        if not m:
            continue

        matched.append(m.group(0).lower())
        left = text[max(0, m.start() - _MOD_WINDOW): m.start()]
        # Auch ein kleines Fenster rechts, fuer "tariffs ... will not be imposed".
        right = text[m.end(): m.end() + _MOD_WINDOW]
        ctx = left + " " + right

        mult = 1.0
        if NEGATION.search(ctx):
            mult *= -0.85            # Vorzeichenumkehr, leicht gedaempft
            mods.append("negation")
        if HEDGE.search(left):
            mult *= 0.45             # "erwaegt" ist weit weniger wert als "unterzeichnet"
            hedge_hits += 1
            mods.append("hedge")
        if CONDITIONAL.search(left):
            mult *= 0.55
            mods.append("conditional")
        if RETROSPECTIVE.search(ctx):
            mult *= 0.30             # alte Nachricht = bereits eingepreist
            mods.append("retrospective")
        if ATTRIBUTION.search(left):
            mult *= 0.40             # Zitat eines Kritikers, keine Ankuendigung
            mods.append("attribution")
        if INTENSIFIER.search(ctx):
            mult *= 1.35
            mods.append("intensifier")

        contrib = term.polarity * term.weight * mult
        raw += contrib
        total_weight += term.weight * abs(mult)
        ec_w = class_weights.get(term.event_class, 0.0)
        class_weights[term.event_class] = ec_w + term.weight * abs(mult)
        targets.extend(term.targets)
        spec_bonus += term.specificity

    if not matched:
        return LexiconResult(EventClass.UNKNOWN, 0.0, 0.0, 0.0, HALF_LIFE_S[EventClass.UNKNOWN])

    event_class = max(class_weights.items(), key=lambda kv: kv[1])[0]
    sentiment = math.tanh(raw / _TANH_SCALE)

    # Vertrauen: saettigt exponentiell. Ein starker Treffer (~1.4) gibt 0.72,
    # zwei geben 0.92 - lineares Skalieren waere hier zu pessimistisch.
    confidence = 1.0 - math.exp(-total_weight / _CONF_SCALE)
    if hedge_hits:
        confidence *= 0.75 ** hedge_hits
    if "negation" in mods:
        # Verneinungen sind die haeufigste Fehlerquelle regelbasierter Auswertung
        # -> Vertrauen senken und damit die LLM-Bestaetigung erzwingen.
        confidence *= 0.70
    if QUESTION.search(text.strip()[-120:]):
        confidence *= 0.6
        mods.append("question")

    # Konkretheit: Zahlen/Daten + termeigene Beitraege.
    specificity = 0.18 + min(0.42, spec_bonus)
    n_spec = len(SPECIFIC_NUM.findall(text))
    specificity = min(1.0, specificity + min(0.40, 0.20 * n_spec))

    half_life = int(HALF_LIFE_S[event_class] * (0.6 + 0.8 * specificity))

    # Reihenfolge der Ziel-Hinweise stabil halten, Duplikate entfernen.
    seen: set[str] = set()
    uniq_targets = [t for t in targets if not (t in seen or seen.add(t))]

    return LexiconResult(
        event_class=event_class,
        sentiment=round(sentiment, 4),
        confidence=round(confidence, 4),
        specificity=round(specificity, 4),
        half_life_s=half_life,
        matched=matched,
        target_hints=uniq_targets,
        modifiers=sorted(set(mods)),
        raw_score=round(raw, 4),
        class_weights={k.value: round(v, 3) for k, v in class_weights.items()},
    )
