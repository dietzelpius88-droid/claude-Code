# Strategie: ereignisgetriebenes Handeln auf Nachrichten und Politik

## 1. Die Kernthese

Wenn eine marktbewegende Information zum ersten Mal öffentlich wird, ist der
Preis für einige Sekunden bis Minuten falsch. Der Vorteil entsteht nicht durch
eine bessere Prognose, sondern durch drei Dinge:

1. **Schneller lesen** – die Information vor dem Markt haben.
2. **Richtig übersetzen** – wissen, welches Instrument *wie stark* reagiert.
3. **Sauber aussteigen** – der Vorteil verfällt, und zwar planbar.

Punkt 3 wird am häufigsten unterschätzt. Ein Nachrichtenimpuls hat eine
Halbwertszeit. Wer eine Position darüber hinaus hält, handelt nicht mehr die
Nachricht, sondern hält ein Zufallsrisiko.

## 2. Der Ablauf

```
   Quellen              Auswertung                Entscheidung        Ausführung
 ┌──────────┐      ┌──────────────────┐      ┌───────────────┐    ┌────────────┐
 │ Rede-ASR │      │ 1 Lexikon   ~2ms │      │ Asset-Auswahl │    │ Scheibe 1  │
 │ Wire-Feed│ ───▶ │ 2 Entität   ~1ms │ ───▶ │ Erwartungswert│───▶│ Scheibe 2  │
 │ Behörden │      │ 3 Überraschung   │      │ Kosten        │    │ Scheibe 3  │
 │ Social   │      │ 4 Sprachmodell   │      │ Sizing        │    └─────┬──────┘
 │ RSS      │      │   ≤1200 ms       │      │ Risiko-Gates  │          │
 └──────────┘      └────────┬─────────┘      └───────────────┘          ▼
                            │                                    Stop / Ziele /
                     Duplikatprüfung                             Trailing /
                     (Neuigkeit)                                 Zeit-Stop
```

### Zwei Geschwindigkeiten

Der Bot entscheidet **zweimal** über dieselbe Meldung:

| Pfad | Zeit | Grundlage | Wirkung |
|---|---|---|---|
| **Fast Path** | < 5 ms | Lexikon + Entitäten + Datenüberraschung | Bei sehr eindeutigen Meldungen aus Primärquellen sofortiger Einstieg mit **halber** Größe |
| **Slow Path** | ≤ 1200 ms | Sprachmodell | Bestätigung → Position bleibt und der Zeit-Stop wird verlängert; Widerspruch → sofortige Auflösung |

Damit ist der Bot bei „Fed senkt Zinsen um 50 Basispunkte, sofort wirksam"
in Millisekunden im Markt, bei „Trump erwägt womöglich Zölle" dagegen geduldig.
Der Preis dieser Geschwindigkeit ist die halbe Größe – das ist der bewusst
eingekaufte Versicherungsbeitrag gegen Fehlinterpretationen.

## 3. Die vier Auswertungspfade

### 3.1 Begriffslexikon (`analysis/lexicon.py`)

45 Begriffsgruppen mit Polarität und Gewicht, verteilt auf sieben
Ereignisklassen. Entscheidend sind nicht die Begriffe selbst, sondern die
**Modifikatoren**, die im Kontextfenster um jeden Treffer gesucht werden:

| Modifikator | Faktor | Beispiel |
|---|---|---|
| Verneinung | ×(−0,85) | „denies he will impose tariffs" |
| Abschwächung | ×0,45 | „is considering", „könnte", „sources say" |
| Bedingung | ×0,55 | „if China retaliates, we will…" |
| Rückblick | ×0,30 | „last week Powell reiterated" |
| Fremdzuschreibung | ×0,40 | „critics warn that he will…" |
| Verstärkung | ×1,35 | „signed", „effective immediately", „emergency" |

Ohne diese Schicht wäre das Lexikon unbrauchbar: „Trump dementiert neue Zölle"
und „Trump verhängt neue Zölle" enthalten dieselben Begriffe und bedeuten das
Gegenteil.

### 3.2 Entitäts-Sentiment (`analysis/entity_sentiment.py`)

Der Pfad für genau den Fall aus der Aufgabenstellung. Der Satz

> „And I looked at Hyperliquid, this platform, it is very impressive,
> tremendous technology, we support it."

enthält **keinen einzigen politischen Fachbegriff**. Ein reines Lexikon sieht
hier nichts. Dieser Pfad kombiniert stattdessen zwei Signale:

1. Wird ein Instrument des Universums **namentlich** genannt?
2. Ist die Aussage darüber wertend positiv oder negativ?

Nur beides zusammen ergibt ein Signal. Lob ohne Bezug ist Rauschen, ein Name
ohne Wertung ist eine Erwähnung. Bewusst nur für Anlageklassen freigegeben, bei
denen die bloße Äußerung einer Autorität den Preis bewegt (Krypto, Energie) –
bei Aktienindizes bewegt nicht das Lob den Kurs, sondern die Politik dahinter.

### 3.3 Datenüberraschung (`analysis/surprise.py`)

Bei geplanten Veröffentlichungen (CPI, NFP, PCE) zählt nicht der Wortlaut,
sondern die Abweichung vom Konsens:

```
z = (Ist − Konsens) / σ            sentiment = risk_sign · tanh(z/2)
```

`σ` ist die historische Prognoseabweichung der Kennzahl, `risk_sign` legt fest,
ob ein höherer Wert gut oder schlecht für Risiko-Assets ist. Eine Abweichung
unter 0,5 σ ist Rauschen und erzeugt praktisch kein Vertrauen.

### 3.4 Sprachmodell (`analysis/llm.py`)

Erkennt Umschreibungen, Ironie, Zitate und neue Themen, für die noch keine
Regel existiert. Harte Regeln:

* **Hartes Timeout.** Antwortet das Modell nicht rechtzeitig, gilt das
  regelbasierte Ergebnis. Der Bot blockiert nie auf einer API.
* **Strukturierte Ausgabe** über Tool-Use, nie freier Text.
* **Das Modell entscheidet nie über Geld.** Es liefert eine Einschätzung;
  Positionsgröße und Limits bleiben deterministischer Code.

## 4. Fusion und Überzeugung

Die Pfade werden gewichtet zusammengeführt. Ein Pfad, der nichts erkannt hat,
verwässert das Ergebnis **nicht** – sein Gewicht wird auf die aktiven Pfade
verteilt. Bestätigen sich mindestens zwei Pfade im Vorzeichen, steigt das
Vertrauen um 15 %.

Daraus entsteht die zentrale Kennzahl:

```
Conviction = |sentiment| · confidence · Quellenvertrauen
             · (0,55 + 0,45 · Konkretheit) · Neuigkeit
```

Multiplikativ, damit ein einzelner schwacher Faktor den Trade zuverlässig
blockiert. Eine perfekt formulierte Meldung aus einer anonymen Quelle wird
genauso aussortiert wie eine vage Andeutung aus dem Weißen Haus.

| Schwelle | Wert | Bedeutung |
|---|---|---|
| `min_conviction` | 0,35 | darunter entsteht gar kein Signal |
| `min_conviction_instant` | 0,62 | darüber Sofort-Einstieg ohne Bestätigung |

Die **Neuigkeit** ist der am meisten unterschätzte Faktor. Derselbe Inhalt über
den vierten Feed ist wertlos – der Markt hat längst reagiert. Die Erkennung
läuft über eine *Claim-Signatur* aus Ereignisklasse, Richtung, direkt genannten
Instrumenten und Entschiedenheit. Letztere trennt Erwägung von Beschluss:
„erwägt Zölle" darf den späteren „unterzeichnet Zölle" nicht als Duplikat
blockieren – genau der ist der handelbare.

## 5. Asset-Auswahl

Nicht „welcher Markt ist betroffen", sondern **welches Instrument liefert den
besten risikoadjustierten Netto-Ertrag**.

### Erwartungswert

```
Bewegung_bps = |sentiment| · impact(Klasse) · |beta| · Tagesvol_bps · Nennungsbonus
Richtung     = Vorzeichen(sentiment · beta)
```

* `impact` = wie viele Tagesvolatilitäten eine maximale Überraschung dieser
  Klasse im Leitinstrument bewegt (Geldpolitik 0,55; Geopolitik 0,45;
  Handel 0,40; Krypto 0,35 …).
* `beta` = Reaktionsstärke des Instruments. **Negative Betas** modellieren
  gegenläufige Instrumente (Gold bei Zöllen, Anleihen bei Risk-off). Dadurch
  braucht der Code keine Sonderfälle.
* `Nennungsbonus` = bis zu 1,6× bei direkter namentlicher Nennung. Der Markt
  handelt die Schlagzeile wörtlich, bevor er über Zusammenhänge nachdenkt.

### Kosten

```
Kosten_bps = (Spread/2 + 0,6 · Tagesvol · √(Nominal/Tagesvolumen)) · 1,75
             + 2 · Gebühren
```

Der Wurzelterm ist das übliche Markteinfluss-Gesetz. Bei Nachrichten wird der
Normalspread zusätzlich mit 3 multipliziert, falls keine Live-Quote vorliegt.

### Rangfolge

Sortiert nach **Netto-Edge geteilt durch Stopweite**, nicht nach Rohbewegung –
sonst gewinnt immer der dünnste Schrottcoin. Davor greifen harte Gates:
Mindest-Edge, Mindest-CRV, Spread-Obergrenze, Spread-Explosion, Buchtiefe,
Kursalter.

### Beispiel: die Hyperliquid-Rede

Rangliste aus dem Demo-Szenario, gemessen mit Live-Quotes zum Zeitpunkt der
Aussage (`newsbot demo`, Conviction 0,519):

| Symbol | Richtung | Erw. Bewegung | Stop | CRV | Nennung |
|---|---|---|---|---|---|
| **HYPE** | long | 808 bps (8,1 %) | 99 bps | **7,90** | 1,00 |
| SOL | long | 356 bps | 63 bps | 5,44 | 0,00 |
| XRP | long | 294 bps | 66 bps | 4,26 | 0,00 |
| DOGE | long | 351 bps | 82 bps | 4,06 | 0,00 |

HYPE gewinnt aus drei Gründen zugleich: höchstes Beta der Klasse (2,40),
direkte namentliche Nennung und die höchste Tagesvolatilität. Gehandelt wird
nur HYPE – SOL fällt aus, weil die Korrelationsgruppe `crypto_beta` dann
belegt ist (siehe `docs/03_RISIKO.md`, Abschnitt 4).

## 6. Einstieg

Grundsatz: **nie ungeschützt Market in die Nachrichtenspitze.** Jede Order ist
ein IOC-Limit mit Slippage-Deckel.

Drei Scheiben:

| Scheibe | Anteil | Zeitpunkt | Limit |
|---|---|---|---|
| 1 | 50 % | sofort | aggressiv, Berührungskurs + 12 bps |
| 2 | 30 % | nach ~4 s | **Preis-Trigger** auf Rücksetzer −15 bps |
| 3 | 20 % | nach ~20 s | nur bei Fortsetzung |

Scheibe 2 ist bewusst ein Trigger und keine ruhende Order: ein passives Limit
unter dem Briefkurs kann als IOC nie ausgeführt werden, und eine sichtbare
ruhende Order im dünnen Buch wird bei Nachrichten überrannt.

### Zwei Schutzmechanismen

* **Chase-Limit.** Ist der Preis seit dem Nachrichtenzeitpunkt bereits um mehr
  als 40 % der erwarteten Bewegung gelaufen, ist die Kante weg → kein Einstieg.
  Das ist die Regel, die den Bot davor bewahrt, jedem Ausbruch hinterherzurennen.
* **Chancenabhängiges Slippage-Budget.** `6 % der erwarteten Bewegung`,
  begrenzt auf 8–60 bps. Ein fester bps-Wert würde breitspreadige Instrumente
  strukturell aussperren – genau die, bei denen die Bewegung groß ist.

## 7. Ausstieg

| Mechanismus | Auslöser |
|---|---|
| Harter Stop | 1,6 × erwartete Ereignis-ATR, mindestens 25 bps |
| Teilverkäufe | 40 % am ersten, 30 % am zweiten Ziel |
| Stop auf Einstand | ab +0,8 R |
| Chandelier-Trailing | `max(2 × ATR, 30 % der erwarteten Bewegung)` |
| **Zeit-Stop** | nach einer Halbwertszeit der Ereignisklasse |
| Sofort glattstellen | Widerspruchsmeldung / Dementi |

Die Stopweite nutzt bewusst die **erwartete Ereignis-Volatilität**, nicht die
gemeldete Ruhe-ATR. Ein Instrument mit 780 bps Tagesvolatilität meldet im
ruhigen Markt vielleicht 20 bps ATR; ein daraus abgeleiteter 32-bps-Stop wird
von der ersten Nachrichtenkerze abgeräumt. Weil die Stopweite im Nenner des
Chance-Risiko-Verhältnisses steht, würde genau das illiquideste Instrument
sonst fälschlich an die Spitze der Rangliste rutschen.

Die Ziele sind an die Chancengröße gekoppelt: Bei einer 8-R-Chance darf die
Leiter nicht bei 2 R enden.

## 8. Was der Bot bewusst NICHT handelt

| Fall | Grund |
|---|---|
| „erwägt", „prüft", „könnte" | Abschwächung senkt die Überzeugung unter die Schwelle |
| „letzte Woche sagte…" | Rückblick – längst eingepreist |
| Eilmeldung von Stufe 5 ohne Zweitquelle | Fälschungsschutz |
| Vierte Wiederholung derselben Aussage | Neuigkeit ≈ 0 |
| Lexikon und Modell widersprechen sich stark | Dissens-Abbruch |
| Preis schon 40 % der Bewegung gelaufen | Kante weg |
| Spread mehr als 4× über Normal | Ausführung unkalkulierbar |

## 9. Grenzen dieser Strategie

Das gehört offen gesagt:

* **Latenz ist ein Wettrüsten.** Gegen kolokierte Systeme mit
  Direktleitungen gewinnt man den ersten Zuck nicht. Der realistische Vorteil
  liegt im zweiten Abschnitt der Bewegung (Sekunden bis Minuten) und in der
  *Übersetzungsleistung* – zu erkennen, dass ein Nebenwert stärker profitiert
  als der Leitwert.
* **Gefälschte Meldungen** haben schon mehrfach Milliarden bewegt. Die
  Quellenstufen und der Zwang zur Zweitbestätigung mindern das Risiko, sie
  beseitigen es nicht.
* **Die Betas sind Startwerte.** Sie müssen gegen echte Daten nachkalibriert
  werden (`backtest/event_study.py`), sonst handelt der Bot eine
  Wunschvorstellung.
* **Regimewechsel.** Nach dem dritten Zolldekret in einem Monat reagiert der
  Markt anders als beim ersten. Die Neuigkeitserkennung fängt das teilweise ab,
  eine regelmäßige Neukalibrierung ersetzt sie nicht.
* **Die Backtest-Zahlen in diesem Projekt stammen aus nachgebildeten
  Szenarien**, nicht aus historischen Marktdaten. Sie zeigen, dass die Mechanik
  funktioniert – nicht, dass die Strategie Geld verdient.

Vor jedem Livebetrieb: `docs/03_RISIKO.md` und `docs/05_BETRIEB.md` lesen.
