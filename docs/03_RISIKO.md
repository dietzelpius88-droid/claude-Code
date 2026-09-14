# Risikoregeln

Diese Regeln sind nicht verhandelbar und stehen bewusst **außerhalb** der
Auswertungslogik. Kein Sprachmodell und keine noch so hohe Überzeugung kann sie
übersteuern.

## 1. Die Grundrechnung

```
Risiko_USD = Kapital · Risiko_pro_Trade · Überzeugungsfaktor
Stückzahl  = Risiko_USD / Stopweite_in_Preis
```

Die Größe folgt aus dem **erlaubten Verlust**, nie aus der erhofften Chance.
Der Überzeugungsfaktor skaliert linear mit der Conviction, begrenzt auf
0,35×–2,0×: Ein einzelnes Ereignis darf das Konto nie dominieren.

## 2. Die vier Deckel

Nach der Risikorechnung greifen vier unabhängige Obergrenzen. **Der kleinste
gewinnt.**

| Deckel | Standard | Zweck |
|---|---|---|
| Nominalwert je Trade | 25 % des Kapitals | Konzentrationsschutz |
| Anteil am Tagesvolumen | 0,5 % | wir wollen den Kurs nicht selbst machen |
| Anteil an der Buchtiefe | 15 % | Ausführbarkeit, auch beim Ausstieg |
| Hebelspielraum | 3× Konto, zusätzlich Instrumentenlimit | Liquidationsschutz |

Der Buchtiefen-Deckel ist der wichtigste bei dünnen Werten. Eine Position, die
man nicht wieder loswird, ist keine Position, sondern ein Problem.

## 3. Abschaltbedingungen

| Auslöser | Standard | Folge |
|---|---|---|
| Tagesverlust | 2 % des Startkapitals | Stopp für 1 Stunde |
| Wochenverlust | 5 % | Stopp für 6 Stunden |
| Verluste in Folge | 3 | Stopp für 1 Stunde |

Bei ausgelöstem Stopp werden **offene Positionen glattgestellt**, nicht nur
neue verhindert. Ein Bot, der nach einer Verlustserie weiterhält, hat bereits
aufgehört, seiner eigenen Logik zu folgen.

## 4. Portfoliogrenzen

| Regel | Standard | Begründung |
|---|---|---|
| Gleichzeitige Positionen | 3 | mehr ist bei Ereignissen nicht überwachbar |
| Positionen je Korrelationsgruppe | 1 | **die wichtigste Regel** |
| Bruttoexposure | 60 % des Kapitals | |
| Gegenpositionen in einer Gruppe | verboten | heben sich auf, kosten nur Gebühren |

Die Gruppenregel verhindert den typischsten Anfängerfehler im News-Trading:
Eine Krypto-Meldung führt zu Long in BTC, ETH, SOL und HYPE gleichzeitig. Das
sieht nach vier Positionen aus, ist aber **eine Wette mit vierfachem Einsatz**.
Gruppen: `crypto_major`, `crypto_beta`, `equity_us`, `rates`, `fx`, `metals`,
`energy`.

## 5. Infrastruktur-Wachen

Diese greifen auch bei völlig gesundem Konto:

| Wache | Schwelle | Folge |
|---|---|---|
| Kurs veraltet | > 5 s | keine neuen Orders |
| Spread-Explosion | > 4× Normal | Instrument pausiert |
| Ausführungsanomalie | > 80 bps Abweichung | Börse für 10 min gesperrt |
| Orderfehler in Folge | 3 | Börse für 5 min gesperrt |
| Feed still | > 15 min | Warnung |

## 6. Fälschungsschutz

Der teuerste denkbare Einzelfehler ist ein Trade auf eine gefälschte Meldung.
Vier Schichten:

1. **Quellenstufen.** Vertrauensfaktoren 1,00 / 0,95 / 0,80 / 0,60 / 0,35 gehen
   multiplikativ in die Conviction ein.
2. **Zweitbestätigungspflicht** ab Stufe 4 innerhalb von 45 Sekunden.
3. **Dissens-Abbruch.** Widersprechen sich Lexikon und Sprachmodell stärker als
   0,9 (auf einer Skala von −1 bis +1), wird nicht gehandelt und eine bereits
   eröffnete Vorhutposition sofort aufgelöst.
4. **Widerspruchserkennung.** Ein Dementi oder eine Rücknahme stellt alle
   Positionen der betroffenen Ereignisklasse sofort glatt.

## 7. Warum 0,5 % pro Trade

Weil Verluste **multiplikativ** wirken. Auch bei klar positivem Erwartungswert
wird das geometrische Wachstum ab einem gewissen Einsatz negativ:

```python
from newsbot.backtest.event_study import geometric_mean_growth
r = [-1.0]*6 + [3.0, 3.0, 2.0]      # Erwartungswert +2 R auf 9 Trades
```

| Risiko je Trade | Wachstum je Trade |
|---|---|
| 0,5 % | +0,107 % |
| 5 % | +0,748 % |
| 10 % | **+0,836 %** (nahe am Optimum) |
| 15 % | +0,338 % |
| 20 % | −0,691 % |
| 50 % | −16,595 % |

Die Kante ist in allen Zeilen dieselbe. Nur der Einsatz ändert sich – und ab
etwa 18 % kippt das Ergebnis trotz positiven Erwartungswerts ins Negative.

Der Standardwert liegt bewusst **weit unter** dem rechnerischen Optimum, weil
das Optimum von geschätzten Trefferquoten abhängt und diese Schätzung im
News-Trading systematisch zu optimistisch ausfällt: Der Backtest kennt weder
Ausfälle noch Fälschungen noch Regimewechsel.

## 8. Vor dem Livebetrieb

- [ ] Mindestens 4 Wochen Paper-Trading mit echten Feeds
- [ ] Betas gegen eigene historische Daten kalibriert
- [ ] Ausführungsqualität geprüft: gemessene Slippage vs. Modell
- [ ] Alle Abschaltbedingungen einmal absichtlich ausgelöst und verifiziert
- [ ] Handverfahren zum Glattstellen dokumentiert und getestet
- [ ] Startkapital so gewählt, dass ein Totalverlust verkraftbar ist
- [ ] Rechtliche Lage geprüft (Lizenzbedingungen der Feeds, Marktzugang,
      steuerliche Behandlung, ggf. Erlaubnispflicht)
