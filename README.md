# newsbot – ereignisgetriebener News- und Politik-Trading-Bot

Reagiert auf politische und marktbewegende Ereignisse – Zinsentscheide, Zölle,
Krypto-Regulierung, Konjunkturdaten, Reden – und sucht in Sekunden das
Instrument, das am stärksten davon profitiert.

```
Nachricht  ──▶  4 Auswertungspfade  ──▶  Asset-Auswahl  ──▶  Risikoprüfung  ──▶  Order
              Lexikon · Entitäten          Beta × Impact       4 Deckel          3 Scheiben
              Überraschung · LLM           Kostenmodell        Gruppenlimits     Stop/Ziel/Zeit
```

## Schnellstart

```bash
python3 -m pip install -e ".[dev]"

newsbot selbsttest     # Kette ohne Netzzugang prüfen
newsbot demo           # Rede mit Hyperliquid-Erwähnung nachspielen
newsbot quellen        # 37 Quellen mit Latenzklassen
```

Der Kern läuft **ohne Zusatzpakete**. Live-Feeds und Sprachmodell sind optional:
`pip install -e ".[live,llm]"`.

## Was der Bot im Demo-Szenario tut

```
Satz 1   „We are going to make America the crypto capital of the world."
         → Conviction 0,300 · unter der Schwelle 0,35 · KEIN Trade
           (qualitativ, keine Zahlen, kein konkretes Asset)

Satz 2   „And I looked at Hyperliquid, this platform, it is very impressive,
          tremendous technology, we support it."
         → HYPE namentlich genannt + klare Wertung
         → Conviction 0,519 · Rangliste: HYPE (CRV 7,9) ▸ SOL (5,4) ▸ XRP (4,3)
         → long HYPE, erwartet 808 bps, Stop 99 bps, Zeit-Stop 25 min

nach  80 s   Ziel 1  → 40 % raus   +311 USD   (+3,14 R)
nach 140 s   Ziel 2  → 30 % raus   +420 USD   (+5,66 R)
nach 892 s   Zeit-Stop → Rest     +1.063 USD  (+14,32 R)

Ergebnis: +1.795 USD auf 100.000 USD Kapital, Summe 23,1 R
```

Das zweite Szenario (`newsbot replay examples/szenario_gemischt.jsonl`) enthält
drei Fallen – eine gehedgte Spekulation, eine aufgewärmte alte Meldung und eine
unbestätigte Eilmeldung von einer unsicheren Quelle. Der Bot handelt keine davon
und nur das echte, unterzeichnete Zolldekret.

## Aufbau

| Modul | Aufgabe |
|---|---|
| `sources/` | RSS, Behördenendpunkte, WebSocket, Social, **Live-Spracherkennung**, Replay |
| `analysis/lexicon.py` | 45 Begriffsgruppen + Modifikatoren (Verneinung, Abschwächung, Rückblick) |
| `analysis/entity_sentiment.py` | Lob/Kritik zu einem **namentlich genannten** Instrument |
| `analysis/surprise.py` | Konjunkturdaten: Abweichung vom Konsens statt Wortlaut |
| `analysis/llm.py` | Sprachmodell mit hartem Timeout und strukturierter Ausgabe |
| `analysis/dedup.py` | Neuigkeitsbewertung – derselbe Inhalt über den vierten Feed ist wertlos |
| `analysis/pipeline.py` | Fusion der vier Pfade, Zwei-Geschwindigkeiten-Logik |
| `strategy/universe.py` | 13 Instrumente mit Event-Betas (negativ = gegenläufig) |
| `strategy/selector.py` | Erwartungswert, Kostenmodell, risikoadjustierte Rangfolge |
| `strategy/engine.py` | Orchestrator |
| `risk/` | Sizing mit 4 Deckeln, Portfoliolimits, Not-Aus |
| `execution/` | Paper-Broker mit realistischem Fill-Modell, Positionsverwaltung |
| `backtest/` | Szenario-Replay mit virtueller Uhr, Ereignisstudie zur Beta-Schätzung |

## Kernentscheidungen

**Zwei Geschwindigkeiten.** Sehr eindeutige Meldungen aus Primärquellen lösen in
unter 5 ms einen Einstieg mit halber Größe aus; das Sprachmodell bestätigt
innerhalb von 1200 ms oder die Position wird sofort aufgelöst. Ein Timeout
blockiert nie – dann gilt das regelbasierte Ergebnis.

**Conviction ist multiplikativ.**
`|sentiment| · confidence · Quellenvertrauen · Konkretheit · Neuigkeit` –
ein einzelner schwacher Faktor blockiert den Trade zuverlässig.

**Negative Betas statt Sonderfälle.** Gold bei Zöllen, Anleihen bei Risk-off,
Öl bei Krieg: Die Richtung ist immer `Vorzeichen(sentiment × beta)`.

**Risiko steht außerhalb der Auswertung.** Kein Sprachmodell kann die Limits
übersteuern. Positionsgröße folgt aus dem erlaubten Verlust, nie aus der
erhofften Chance.

**Eine Position je Korrelationsgruppe.** Long in BTC, ETH, SOL und HYPE
gleichzeitig sind nicht vier Positionen, sondern eine Wette mit vierfachem
Einsatz.

## Dokumentation

| Datei | Inhalt |
|---|---|
| [`docs/01_STRATEGIE.md`](docs/01_STRATEGIE.md) | Vollständige Logik, Formeln, was bewusst nicht gehandelt wird, Grenzen |
| [`docs/02_QUELLEN.md`](docs/02_QUELLEN.md) | Quellenlandschaft, Latenzklassen, Spracherkennungs-Kanal, Fälschungsrisiko |
| [`docs/03_RISIKO.md`](docs/03_RISIKO.md) | Alle Limits mit Begründung, Checkliste vor dem Livegang |
| [`docs/04_PLAYBOOKS.md`](docs/04_PLAYBOOKS.md) | Je Ereignisklasse: Betas, Halbwertszeiten, typische Fehler |
| [`docs/05_BETRIEB.md`](docs/05_BETRIEB.md) | Konfiguration, Kalibrierung, Börsenanbindung, Livegang |

## Tests

```bash
python3 -m pytest -q        # 145 Tests
```

Darunter Regressionstests für beide Szenarien: Der Hyperliquid-Fall muss
gehandelt werden, die drei Fallen dürfen es nicht.

## Grenzen

* **Latenz ist ein Wettrüsten.** Gegen kolokierte Systeme gewinnt man den ersten
  Zuck nicht. Der realistische Vorteil liegt im zweiten Abschnitt der Bewegung
  und in der Übersetzungsleistung – zu erkennen, dass ein Nebenwert stärker
  profitiert als der Leitwert.
* **Die Betas sind Startwerte**, keine Messungen. Ohne Nachkalibrierung gegen
  eigene historische Daten handelt der Bot eine Vermutung.
* **Die gezeigten Zahlen stammen aus nachgebildeten Szenarien**, nicht aus
  historischen Marktdaten. Sie belegen, dass die Mechanik funktioniert – nicht,
  dass die Strategie Geld verdient.
* **Gefälschte Meldungen** bleiben ein Restrisiko, das die Quellenstufen mindern,
  aber nicht beseitigen.

Dieses Projekt ist Software, keine Anlageberatung. Ereignisgetriebenes Handeln
kann zum Totalverlust führen. Vor jedem Livebetrieb `docs/03_RISIKO.md` lesen.
