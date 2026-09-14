"""Konfiguration.

Alle Defaults leben im Code, damit der Bot ohne externe Dateien und ohne
Zusatzpakete startet. `config/config.json` ueberschreibt einzelne Felder
(tiefes Merge), YAML wird zusaetzlich unterstuetzt, wenn PyYAML installiert ist.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
@dataclass
class SignalConfig:
    """Schwellen, ab denen aus einer Nachricht ein Handelssignal wird."""

    # Grund-Gate: darunter wird gar nichts erzeugt.
    min_conviction: float = 0.35
    # Sofort-Gate: Nur wer das erreicht, darf allein auf dem Fast Path (Lexikon,
    # < 150 ms) handeln. Alles darunter wartet auf LLM-/Zweitquellen-Bestaetigung.
    min_conviction_instant: float = 0.62
    # Quellen ab dieser Stufe brauchen IMMER eine Zweitbestaetigung.
    confirm_required_from_tier: int = 4
    # Zeitfenster, in dem eine Zweitquelle eintreffen muss.
    confirmation_window_s: int = 45
    # Netto-Edge nach Kosten, sonst lohnt der Trade nicht.
    min_net_edge_bps: float = 12.0
    min_rr: float = 1.2
    # Aelter als das -> Impuls wahrscheinlich eingepreist, nicht mehr handeln.
    max_news_age_ms: int = 20_000
    # Duplikatsfenster: identische/aehnliche Meldung wird verworfen.
    dedup_window_s: int = 1800
    dedup_similarity: float = 0.82
    # Gewichte der Fusion aus Lexikon, LLM und Datenueberraschung.
    w_lexicon: float = 0.40
    w_llm: float = 0.45
    w_surprise: float = 0.15
    w_entity: float = 0.25
    # Widersprechen sich Lexikon und LLM staerker als das -> kein Trade.
    max_analyzer_disagreement: float = 0.9
    # Wie viele Instrumente pro Ereignis maximal gehandelt werden.
    max_assets_per_event: int = 2
    # Anteil der Normalgroesse fuer den provisorischen Vorhut-Einstieg,
    # bevor die Bestaetigung vorliegt.
    provisional_size_factor: float = 0.5


@dataclass
class RiskConfig:
    """Harte Risikogrenzen. Werden vor JEDER Order geprueft."""

    equity_usd: float = 100_000.0
    # Anteil des Kapitals, der pro Trade maximal verloren werden darf.
    risk_per_trade_pct: float = 0.005
    # Obergrenze auch bei maximaler Conviction.
    max_risk_per_trade_pct: float = 0.012
    # Tages-/Wochenverlust, ab dem der Bot abschaltet.
    daily_loss_limit_pct: float = 0.02
    weekly_loss_limit_pct: float = 0.05
    # Nach so vielen Verlusttrades in Folge: Pause.
    consecutive_loss_halt: int = 3
    cooldown_after_halt_s: int = 3600
    # Gleichzeitig offene Event-Trades.
    max_concurrent_positions: int = 3
    # Pro Korrelationsgruppe (crypto_beta, rates, equity_us, ...).
    max_positions_per_cluster: int = 1
    # Summe aller Positionen relativ zum Kapital.
    max_gross_exposure_pct: float = 0.60
    max_leverage: float = 3.0
    max_notional_per_trade_pct: float = 0.25
    # Liquiditaetsbremsen: nie mehr als dieser Anteil vom Tagesvolumen bzw.
    # von der sichtbaren Orderbuchtiefe innerhalb des Slippage-Budgets.
    max_adv_participation: float = 0.005
    max_book_participation: float = 0.15
    # Stop-Konstruktion.
    stop_atr_mult: float = 1.6
    min_stop_bps: float = 25.0
    max_stop_bps: float = 400.0
    # Teilverkaufsleiter in R-Vielfachen: (R-Multiple, Anteil der Position).
    scale_out: list[tuple[float, float]] = field(
        default_factory=lambda: [(1.0, 0.4), (2.0, 0.3)]
    )
    # Ab diesem R wird der Stop auf Einstand nachgezogen.
    breakeven_at_r: float = 0.8
    # Chandelier-Trailing fuer den Rest. Der Abstand ist das MAXIMUM aus
    # ATR-Vielfachem und einem Anteil der erwarteten Bewegung: bei einem
    # Ereignis, das 800 bps traegt, wuerde ein 90-bps-Trailing schon beim
    # ersten normalen Ruecksetzer ausloesen und den Grossteil verschenken.
    trail_atr_mult: float = 2.0
    trail_move_ratio: float = 0.30


@dataclass
class ExecutionConfig:
    """Wie eingestiegen wird. Grundsatz: nie ungeschuetzt Market."""

    mode: str = "paper"                 # paper | live
    venue: str = "paper"
    # Slippage-Budget. Es skaliert mit der CHANCE: fuer eine erwartete
    # Bewegung von 800 bps darf der Einstieg mehr kosten als fuer 90 bps.
    # Ein fester bps-Wert wuerde breitspreadige Instrumente wie HYPE
    # strukturell aussperren - genau die, bei denen die Bewegung gross ist.
    max_slippage_ratio: float = 0.06        # Anteil der erwarteten Bewegung
    min_slippage_bps: float = 8.0           # Untergrenze des Budgets
    max_slippage_bps: float = 60.0          # harte Obergrenze
    # Ist der Preis schon um mehr als diesen Anteil der erwarteten Bewegung
    # gelaufen, ist die Kante weg -> kein Einstieg mehr.
    max_chase_ratio: float = 0.40
    # Gestaffelter Einstieg: (Anteil, Verzoegerung in ms, Limit-Offset in bps).
    # Scheibe 1 sofort aggressiv, Scheibe 2 auf den ersten Ruecksetzer,
    # Scheibe 3 nur bei Fortsetzung.
    entry_slices: list[tuple[float, int, float]] = field(
        default_factory=lambda: [(0.5, 0, 12.0), (0.3, 4_000, -15.0), (0.2, 20_000, 10.0)]
    )
    slice_expiry_ms: int = 45_000
    # Marktqualitaets-Gates.
    max_spread_bps: float = 25.0
    max_quote_age_ms: int = 2_500
    min_depth_usd: float = 25_000.0
    # Nach Signalerzeugung: spaetestens dann muss der Einstieg stehen.
    signal_ttl_ms: int = 60_000


@dataclass
class GuardConfig:
    """Not-Aus-Bedingungen."""

    enabled: bool = True
    # Feed-Ausfall: keine Nachricht aus einer Primaerquelle seit X s -> Warnung.
    feed_stale_s: int = 900
    # Preis-Feed aelter als X ms -> keine neuen Orders.
    quote_stale_ms: int = 5_000
    # Spread-Explosion relativ zum Normalwert -> pausieren.
    spread_blowout_mult: float = 4.0
    # Abweichung Fill vs. erwarteter Preis, ab der wir den Venue sperren.
    fill_anomaly_bps: float = 80.0
    # Aufeinanderfolgende Order-Fehler bis Venue-Sperre.
    max_consecutive_order_errors: int = 3
    # Handelsverbot rund um bekannte Illiquiditaet (UTC-Stunden).
    blackout_hours_utc: list[int] = field(default_factory=list)
    # Dementi-Erkennung: widersprechende Meldung -> sofort glattstellen.
    flatten_on_contradiction: bool = True


@dataclass
class LLMConfig:
    """Slow Path - semantische Auswertung."""

    enabled: bool = True
    # Fast Model: entscheidet den Trade, muss unter einer Sekunde antworten.
    model: str = "claude-haiku-4-5-20251001"
    # Deep Model: Nachkontrolle, Playbook-Pflege, Post-Trade-Review (nicht im
    # Latenzpfad).
    deep_model: str = "claude-opus-5"
    # Harte Obergrenze; laeuft sie ab, gilt allein das Lexikon-Ergebnis.
    timeout_ms: int = 1_200
    max_tokens: int = 700
    # Kandidaten-Kontext, den das Modell zur Asset-Auswahl bekommt.
    universe_hint_size: int = 25
    # Wiederholung bei Netzwerkfehler - im Latenzpfad hoechstens einmal.
    max_retries: int = 1


@dataclass
class Config:
    signal: SignalConfig = field(default_factory=SignalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    guards: GuardConfig = field(default_factory=GuardConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    data_dir: str = "data"
    log_level: str = "INFO"

    # ---------------------------------------------------------------- laden
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg = cls()
        if path is None:
            for cand in ("config/config.json", "config/config.yaml", "config/config.yml"):
                if Path(cand).exists():
                    path = cand
                    break
        if path and Path(path).exists():
            _deep_apply(cfg, _read_file(Path(path)))
        cfg.execution.mode = os.getenv("NEWSBOT_MODE", cfg.execution.mode)
        cfg.log_level = os.getenv("NEWSBOT_LOG_LEVEL", cfg.log_level)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
def _read_file(p: Path) -> dict[str, Any]:
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml  # optional
        except ImportError as exc:  # pragma: no cover - reine Bedienhilfe
            raise RuntimeError(
                f"{p} ist YAML, aber PyYAML fehlt. "
                "Entweder `pip install pyyaml` oder config/config.json benutzen."
            ) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)


def _deep_apply(obj: Any, data: dict[str, Any]) -> None:
    """Uebertraegt ein verschachteltes Dict auf eine Dataclass-Instanz."""
    valid = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in valid:
            continue
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _deep_apply(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            # Listen von (a, b)-Paaren aus JSON kommen als Listen an.
            setattr(obj, key, [tuple(v) if isinstance(v, list) else v for v in value])
        else:
            setattr(obj, key, value)
