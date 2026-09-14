"""Kommandozeile.

    newsbot quellen                     Quellenkatalog anzeigen
    newsbot analyse "Schlagzeile"       einzelne Meldung auswerten
    newsbot replay <datei.jsonl>        Szenario abspielen (Paper-Trading)
    newsbot demo                        mitgeliefertes Hyperliquid-Szenario
    newsbot live                        Livebetrieb (Standard: Paper)
    newsbot selbsttest                  End-to-End-Pruefung ohne Netz
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .analysis.llm import LLMAnalyzer
from .analysis.pipeline import AnalysisPipeline
from .config import Config
from .execution.paper import PaperBroker, PaperFillModel, StaticMarket
from .models import EventClass, RawNews, SourceTier
from .risk.limits import Portfolio
from .strategy.engine import NewsTradingEngine
from .strategy.universe import Universe

LOG_FMT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s"


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=LOG_FMT, datefmt="%H:%M:%S")
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def build_engine(cfg: Config, market: StaticMarket | None = None,
                 *, seed: int | None = None) -> tuple[NewsTradingEngine, StaticMarket]:
    universe = Universe.load()
    market = market or StaticMarket()
    llm = LLMAnalyzer(cfg.llm, [a.symbol for a in universe.all()])
    pipeline = AnalysisPipeline(cfg.signal, llm=llm, universe=universe)
    broker = PaperBroker(market, universe,
                         PaperFillModel(latency_ms=350, seed=seed))
    engine = NewsTradingEngine(cfg, universe, pipeline, market, broker,
                               Portfolio(cfg.risk.equity_usd))
    return engine, market


# --------------------------------------------------------------------------
def cmd_quellen(args: argparse.Namespace) -> int:
    from .sources.catalog import build_sources, load_specs, summary
    specs = load_specs(args.katalog)
    if not specs:
        print("Kein Katalog gefunden.", file=sys.stderr)
        return 1
    print(summary(specs))
    auto = build_sources(specs)
    print(f"\n{len(specs)} Quellen im Katalog, {len(auto)} davon ohne Zugangsdaten lauffaehig.")
    manual = [s for s in specs if s.type in ("social", "speech", "websocket", "poll_html")]
    if manual:
        print(f"\n{len(manual)} Quellen brauchen eigene Verdrahtung (Zugangsdaten, Audiostrom):")
        for s in manual:
            print(f"  - {s.id:32s} [{s.type}] {s.note or ''}")
    return 0


def cmd_analyse(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    universe = Universe.load()
    llm = LLMAnalyzer(cfg.llm, [a.symbol for a in universe.all()])
    pipeline = AnalysisPipeline(cfg.signal, llm=llm, universe=universe)
    news = RawNews(source_id=args.quelle, tier=SourceTier(args.tier),
                   headline=args.text, body=args.body or "")

    from .strategy.selector import AssetSelector
    selector = AssetSelector(universe, cfg.signal, cfg.risk, cfg.execution)

    async def run() -> int:
        found = False
        async for ev in pipeline.process(news):
            found = True
            print(f"\n--- Auswertung [{ev.analyzer}] "
                  f"{'(provisorisch)' if ev.meta.get('provisional') else ''}")
            print(f"  Klasse       : {ev.event_class.value}")
            print(f"  Sentiment    : {ev.sentiment:+.3f}   ({ev.direction.value})")
            print(f"  Vertrauen    : {ev.confidence:.3f}")
            print(f"  Konkretheit  : {ev.specificity:.3f}")
            print(f"  Neuigkeit    : {ev.novelty:.3f}")
            print(f"  CONVICTION   : {ev.conviction:.3f} "
                  f"(Schwelle {cfg.signal.min_conviction}, sofort ab "
                  f"{cfg.signal.min_conviction_instant})")
            print(f"  Halbwertszeit: {ev.half_life_s}s")
            print(f"  Begriffe     : {', '.join(ev.entities[:6]) or '-'}")
            print(f"  Begruendung  : {ev.rationale}")
            if ev.meta.get("abort"):
                print("  ABBRUCH: Bestaetigung traegt nicht")
                continue

            cands = selector.build_candidates(ev)
            if not cands:
                print("  -> kein Instrument mit relevantem Beta")
                continue
            print(f"\n  {'SYM':6s} {'RICHT':6s} {'ERW.BEWEG':>10s} {'KOSTEN':>8s} "
                  f"{'STOP':>8s} {'CRV':>6s}  STATUS")
            for c in cands[:8]:
                print(f"  {c.asset.symbol:6s} {c.direction.value:6s} "
                      f"{c.expected_move_bps:9.0f}b {c.expected_cost_bps:7.1f}b "
                      f"{c.stop_bps:7.1f}b {c.rr:6.2f}  {c.rejected or 'handelbar'}")
        if not found:
            print("\nKein Signal: die Meldung erreicht keine der Schwellen "
                  "(zu vage, zu alt, Quelle zu schwach oder bereits gesehen).")
        return 0

    return asyncio.run(run())


def cmd_replay(args: argparse.Namespace) -> int:
    from .backtest.scenario import load_scenario, run_scenario
    cfg = Config.load(args.config)
    if args.kapital:
        cfg.risk.equity_usd = args.kapital
    engine, market = build_engine(cfg, seed=args.seed)
    records = load_scenario(args.datei)

    report = asyncio.run(run_scenario(engine, market, records,
                                      tick_every_ms=args.tick_ms))
    print("\n" + "=" * 62)
    print("ERGEBNIS")
    print("=" * 62)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if engine.tm.closed:
        print("\nEinzelne Teilgeschaefte:")
        print(f"  {'GRUND':12s} {'SYM':6s} {'SEITE':6s} {'MENGE':>12s} "
              f"{'EIN':>12s} {'AUS':>12s} {'PnL USD':>10s} {'R':>7s} {'DAUER':>8s}")
        for t in engine.tm.closed:
            print(f"  {t.reason.value:12s} {t.symbol:6s} {t.side.value:6s} "
                  f"{t.qty:12.4f} {t.entry_px:12.4f} {t.exit_px:12.4f} "
                  f"{t.pnl_usd:+10.2f} {t.r_multiple:+7.2f} {t.held_s:7.0f}s")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    here = Path(__file__).resolve().parent.parent.parent
    datei = here / "examples" / "szenario_trump_hyperliquid.jsonl"
    if not datei.exists():
        print(f"Demo-Szenario fehlt: {datei}", file=sys.stderr)
        return 1
    args.datei = str(datei)
    return cmd_replay(args)


def cmd_live(args: argparse.Namespace) -> int:
    from .sources.catalog import build_sources, load_specs
    cfg = Config.load(args.config)
    if cfg.execution.mode != "paper" and not args.echt:
        print("Livebetrieb mit echtem Geld erfordert --echt.", file=sys.stderr)
        return 2
    engine, market = build_engine(cfg)
    specs = load_specs(args.katalog)
    sources = build_sources(specs, max_latency_class=args.max_latenz)
    if not sources:
        print("Keine lauffaehigen Quellen. Fehlt 'pip install \".[live]\"'?",
              file=sys.stderr)
        return 1
    print(f"{len(sources)} Quellen aktiv, Modus={cfg.execution.mode}, "
          f"Kapital={cfg.risk.equity_usd:,.0f} USD")
    print("HINWEIS: Ohne angebundene Kursversorgung erzeugt der Bot keine Orders "
          "- siehe docs/05_BETRIEB.md.")

    async def run() -> None:
        try:
            await engine.run(sources)
        except KeyboardInterrupt:
            pass
        finally:
            await engine.shutdown()
            print(json.dumps(engine.report(), indent=2, ensure_ascii=False))

    asyncio.run(run())
    return 0


def cmd_selbsttest(args: argparse.Namespace) -> int:
    """Prueft die Kette Lexikon -> Auswahl -> Sizing -> Risiko ohne Netzzugang."""
    from .analysis.lexicon import score_text
    from .strategy.selector import AssetSelector

    cfg = Config.load(args.config)
    universe = Universe.load()
    selector = AssetSelector(universe, cfg.signal, cfg.risk, cfg.execution)
    faelle = [
        ("Fed announces emergency rate cut of 50 basis points, effective immediately",
         EventClass.MONETARY_POLICY, "+"),
        ("President signs executive order imposing 25% tariffs on all Chinese imports",
         EventClass.TRADE_POLICY, "-"),
        ("Trump says he is considering tariffs on European cars", EventClass.TRADE_POLICY, "-"),
        ("SEC files enforcement action over unregistered crypto securities",
         EventClass.CRYPTO_POLICY, "-"),
        ("OPEC cuts output by 2 million barrels per day", EventClass.ENERGY_POLICY, "+"),
        ("Ceasefire agreed in the region, both sides confirm", EventClass.GEOPOLITICS, "+"),
    ]
    fehler = 0
    print(f"{'TEXT':66s} {'KLASSE':17s} {'S':>7s} {'CONF':>6s}  OK")
    for text, erwartete_klasse, vorzeichen in faelle:
        r = score_text(text)
        ok_klasse = r.event_class is erwartete_klasse
        ok_sign = (r.sentiment > 0) if vorzeichen == "+" else (r.sentiment < 0)
        ok = ok_klasse and ok_sign
        fehler += 0 if ok else 1
        print(f"{text[:64]:66s} {r.event_class.value:17s} {r.sentiment:+7.3f} "
              f"{r.confidence:6.2f}  {'ja' if ok else 'NEIN'}")

    print(f"\nInstrumente im Universum: {len(universe.all())}")
    for ec in EventClass:
        n = len(universe.candidates(ec))
        if n:
            print(f"  {ec.value:18s} {n:2d} Kandidaten")
    print(f"\n{'bestanden' if fehler == 0 else str(fehler) + ' FEHLER'}")
    return 1 if fehler else 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="newsbot",
                                description="Ereignisgetriebener News-Trading-Bot")
    p.add_argument("--config", default=None, help="Pfad zu config/config.json")
    p.add_argument("--log", default="INFO", help="DEBUG, INFO, WARNING, ERROR")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("quellen", help="Quellenkatalog anzeigen")
    s.add_argument("--katalog", default="config/sources.json")
    s.set_defaults(func=cmd_quellen)

    s = sub.add_parser("analyse", help="einzelne Meldung auswerten")
    s.add_argument("text")
    s.add_argument("--body", default="")
    s.add_argument("--quelle", default="manuell")
    s.add_argument("--tier", type=int, default=2, choices=[1, 2, 3, 4, 5])
    s.set_defaults(func=cmd_analyse)

    s = sub.add_parser("replay", help="Szenario abspielen")
    s.add_argument("datei")
    s.add_argument("--kapital", type=float, default=None)
    s.add_argument("--tick-ms", type=int, default=2000, dest="tick_ms")
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(func=cmd_replay)

    s = sub.add_parser("demo", help="mitgeliefertes Szenario abspielen")
    s.add_argument("--kapital", type=float, default=None)
    s.add_argument("--tick-ms", type=int, default=2000, dest="tick_ms")
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("live", help="Livebetrieb")
    s.add_argument("--katalog", default="config/sources.json")
    s.add_argument("--max-latenz", default="C", dest="max_latenz",
                   choices=["A", "B", "C", "D"])
    s.add_argument("--echt", action="store_true",
                   help="echtes Geld zulassen (sonst immer Paper)")
    s.set_defaults(func=cmd_live)

    s = sub.add_parser("selbsttest", help="Kette ohne Netzzugang pruefen")
    s.set_defaults(func=cmd_selbsttest)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
