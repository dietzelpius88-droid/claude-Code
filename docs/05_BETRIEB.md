# Betrieb: Installation, Konfiguration, Kalibrierung, Livegang

## 1. Installation

```bash
git clone <repo> && cd claude-Code
python3 -m pip install -e ".[dev]"        # Kern + Tests
python3 -m pip install -e ".[live,llm]"   # zusätzlich Feeds und Sprachmodell
```

Der **Kern läuft ohne Zusatzpakete** – nur mit der Standardbibliothek. Replay,
Backtest, Paper-Trading und die gesamte Auswertungslogik funktionieren sofort.
Erst Live-Feeds (`aiohttp`, `feedparser`, `websockets`) und das Sprachmodell
(`anthropic`) brauchen Installationen.

```bash
cp .env.example .env     # Schlüssel eintragen; ohne Schlüssel läuft nur der regelbasierte Pfad
```

## 2. Erste Schritte

```bash
newsbot selbsttest                 # Kette ohne Netzzugang prüfen
newsbot quellen                    # Quellenkatalog anzeigen
newsbot demo                       # Hyperliquid-Szenario nachspielen
newsbot replay examples/szenario_gemischt.jsonl
newsbot analyse --tier 1 "Fed announces emergency rate cut of 50 basis points"
newsbot live --max-latenz C        # Paper-Betrieb mit echten RSS-Quellen
```

Ohne installiertes Paket funktioniert auch `PYTHONPATH=src python3 -m newsbot.cli …`.

### Was `newsbot analyse` zeigt

```
  Klasse       : trade_policy
  Sentiment    : -0.983   (short)
  Vertrauen    : 0.972
  Konkretheit  : 0.600
  Neuigkeit    : 1.000
  CONVICTION   : 0.783 (Schwelle 0.35, sofort ab 0.62)
  Halbwertszeit: 1944s

  SYM    RICHT   ERW.BEWEG   KOSTEN     STOP    CRV  STATUS
  HG     short         97b     6.4b    25.0b   3.61  handelbar
  NQ     short         83b     2.2b    25.0b   3.23  handelbar
  ...
```

Das ist das Werkzeug, um Schwellen zu justieren: Man sieht genau, an welchem
Faktor eine Meldung scheitert.

## 3. Konfiguration

`config/config.json` überschreibt einzelne Felder der Standardwerte aus
`src/newsbot/config.py`. Was fehlt, bleibt auf dem Standard.

### Die Felder, die man wirklich anfasst

| Feld | Standard | Wirkung |
|---|---|---|
| `risk.equity_usd` | 100 000 | Bezugsgröße für alle Deckel |
| `risk.risk_per_trade_pct` | 0,005 | **der wichtigste Schalter überhaupt** |
| `risk.daily_loss_limit_pct` | 0,02 | Tagesstopp |
| `risk.max_positions_per_cluster` | 1 | Schutz vor versteckter Häufung |
| `signal.min_conviction` | 0,35 | Grundschwelle; höher = weniger, sicherere Trades |
| `signal.min_conviction_instant` | 0,62 | Schwelle für den Sofort-Einstieg ohne Bestätigung |
| `signal.confirm_required_from_tier` | 4 | ab welcher Quellenstufe Zweitbestätigung Pflicht ist |
| `execution.max_chase_ratio` | 0,40 | wie weit der Preis schon gelaufen sein darf |
| `execution.max_slippage_ratio` | 0,06 | Slippage-Budget als Anteil der erwarteten Bewegung |
| `llm.timeout_ms` | 1200 | harte Obergrenze für den Slow Path |
| `guards.blackout_hours_utc` | `[]` | Handelssperre zu illiquiden Stunden |

### Wahl des Sprachmodells

Der Slow Path liegt **im Latenzpfad** und muss unter einer Sekunde antworten.
Deshalb ist `llm.model` auf ein schnelles Modell gesetzt
(`claude-haiku-4-5-20251001`), während `llm.deep_model` (`claude-opus-5`) für
Nachkontrolle, Playbook-Pflege und Post-Trade-Review außerhalb des Latenzpfads
gedacht ist.

### Universum anpassen

`config/universe.json` überschreibt einzelne Felder je Instrument; `betas`
werden zusammengeführt, nicht ersetzt. Unbekannte Schlüssel (etwa Kommentare)
werden ignoriert.

## 4. Kalibrierung – der Schritt, den man nicht überspringen darf

Die mitgelieferten Betas und Impact-Skalen sind **plausible Startwerte**, keine
Messungen. Ohne Nachkalibrierung handelt der Bot eine Wunschvorstellung.

### Vorgehen

1. **Historische Ereignisse sammeln.** Pro Ereignis: Zeitstempel,
   Ereignisklasse und das Sentiment, das der Bot vergeben *hätte*. Letzteres
   bekommt man, indem man die archivierte Schlagzeile durch `score_text()`
   bzw. die Pipeline schickt – nicht durch nachträgliche Handbewertung, sonst
   misst man den eigenen Rückblick.
2. **Reaktionen messen.** Für jedes Instrument die Rendite in bps über ein
   festes Fenster nach dem Ereignis.
3. **Regression rechnen:**

```python
from newsbot.backtest.event_study import (EventObservation, estimate_betas,
                                          estimate_half_life, summarize)

obs = [EventObservation(event_class="crypto_policy", sentiment=0.8,
                        ts_ms=..., returns_bps={"HYPE": 940.0, "BTC": 130.0})
       # ... mindestens 8 Beobachtungen je Instrument und Klasse
      ]
schaetzung = estimate_betas(obs, daily_vol_bps={"HYPE": 780, "BTC": 280},
                            impact_scale={"crypto_policy": 0.35})
print(summarize(schaetzung))
print(estimate_half_life(obs, "HYPE", "crypto_policy"))   # setzt den Zeit-Stop
```

4. **Nur belastbare Schätzungen übernehmen.** `BetaEstimate.reliable` verlangt
   mindestens 8 Beobachtungen und 55 % Trefferquote. Alles darunter gehört
   nicht ins Universum.
5. Ergebnisse in `config/universe.json` eintragen.

**Kadenz:** monatlich, und immer nach einem erkennbaren Regimewechsel.

## 5. Latenzbudget

Jede Stufe messen und protokollieren:

```
Ereignis  →  Quelle      : 0,2 – 60 s   (Klasse A bis D; der größte Hebel)
Quelle    →  Bot         : 5 – 50 ms    (Netz, Parsing)
Lexikon + Entitäten      : 2 – 5 ms
Sprachmodell (optional)  : 300 – 1200 ms
Auswahl + Sizing + Risiko: < 2 ms
Order    →  Börse        : 20 – 200 ms
```

`RawNews.source_latency_ms` und `AnalyzedEvent.meta["pipeline_latency_ms"]`
liefern die Messwerte. Der Hebel liegt fast immer bei der Quelle, nicht im Code.
Eine Verbesserung von RSS (30 s) auf einen Wire-Feed (0,3 s) bringt mehr als
jede Optimierung der Auswertung.

## 6. Eine Börse anbinden

`execution/paper.py` zeigt das Muster. Für den Livebetrieb:

```python
from newsbot.execution.base import Broker

class MeinBroker(Broker):
    name = "meine_boerse"

    async def submit(self, intent):
        # 1. Order senden (IOC-Limit, reduce_only für Ausstiege beachten)
        # 2. Antwort in Fill(order_id, symbol, side, qty, px, fee_usd) übersetzen
        # 3. Bei Teilausführung die TATSÄCHLICHE Menge zurückgeben
        # 4. Bei Ablehnung None zurückgeben und guards.note_order_error() rufen
        ...

    async def cancel_all(self, symbol): ...
```

Pflichtprüfungen vor dem ersten echten Auftrag:

- [ ] Idempotenz: Doppelt gesendete Order darf nicht doppelt ausgeführt werden
      (Client-Order-ID verwenden)
- [ ] `reduce_only` wird respektiert – sonst dreht ein Ausstieg die Position
- [ ] Teilausführungen werden korrekt zurückgemeldet
- [ ] Mengen- und Preisrundung entspricht den Börsenregeln
- [ ] Zeitüberschreitung bei der Orderantwort führt zu einer Statusabfrage,
      nicht zu einer zweiten Order

## 7. Überwachung

`engine.report()` liefert nach jedem Lauf:

```json
{
  "meldungen": 2, "ereignisse": 1, "signale": 2, "eroeffnet": 1,
  "abgelehnt": 1, "abbrueche": 0,
  "ablehnungsgruende": {"Gruppe 'crypto_beta' bereits mit 1 Position": 1},
  "trefferquote": 1.0, "summe_R": 22.78, "pnl_usd": 1238.12,
  "rendite_pct": 1.238, "wachen": {...}
}
```

Worauf man täglich schaut:

| Kennzahl | Warnsignal |
|---|---|
| `ablehnungsgruende` | dominiert ein Grund, ist eine Schwelle falsch gesetzt |
| `summe_R` vs. `pnl_usd` | starke Abweichung = Ausführungsproblem |
| `abbrueche` | viele = Fast Path zu aggressiv, `min_conviction_instant` anheben |
| `wachen.stale_feeds` | ein ausgefallener Feed ist gefährlicher als ein fehlender |
| gemessene vs. modellierte Slippage | Modell nachziehen, sonst ist jede Edge-Rechnung falsch |

## 8. Eigene Szenarien bauen

JSON Lines, nach `t_ms` sortiert:

```json
{"t_ms": 0,     "type": "price", "symbol": "HYPE", "mid": 42.0, "spread_bps": 6, "depth_usd": 900000, "atr_bps": 20}
{"t_ms": 60000, "type": "news",  "source_id": "speech_potus", "tier": 1, "headline": "…", "body": "…", "expect": "long HYPE"}
{"t_ms": 80000, "type": "price", "symbol": "HYPE", "mid": 44.1, "spread_bps": 12, "depth_usd": 400000, "atr_bps": 45}
```

Der Runner setzt eine **virtuelle Uhr** (`newsbot/clock.py`), deshalb sind
Zeit-Stops und Trailing exakt reproduzierbar – ein Szenario über 30 Minuten
läuft in Millisekunden durch. `scripts/make_scenarios.py` zeigt, wie man
realistische Impulsformen erzeugt.

## 9. Ablauf beim Livegang

1. **Woche 1–2:** Paper mit Replay-Szenarien. Schwellen justieren.
2. **Woche 3–6:** Paper mit echten Feeds. Latenz je Quelle messen, gemeldete
   gegen tatsächliche Ausführung vergleichen.
3. **Woche 7+:** Live mit 10 % des geplanten Kapitals. Jeden Trade einzeln
   nachbesprechen.
4. Erst danach schrittweise hochskalieren – und nach jeder Erhöhung wieder
   beobachten, ob die Ausführungsqualität hält. Bei dünnen Werten sinkt sie
   überproportional.

## 10. Rechtliches und Sorgfaltspflichten

* Feed-Lizenzen prüfen: Kommerzielle Nachrichtenfeeds haben Bedingungen zur
  automatisierten Verarbeitung und Weiterverwendung.
* Automatisiertes Abgreifen ist nicht auf jeder Website erlaubt.
* Je nach Land und Umfang können Erlaubnispflichten bestehen.
* Steuerliche Behandlung häufiger Geschäfte unterscheidet sich erheblich nach
  Land und Rechtsform.

**Dieses Projekt ist Software, keine Anlageberatung.** Die gezeigten
Backtest-Zahlen stammen aus nachgebildeten Szenarien und sind keine Aussage
über künftige Ergebnisse. Ereignisgetriebenes Handeln kann zum Totalverlust
führen.
