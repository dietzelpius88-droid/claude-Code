# Playbooks je Ereignisklasse

Für jede Ereignisklasse: was bewegt sich, wie stark, wie lange, und wo die
typischen Fehler liegen. Die Zahlen sind Startwerte für die Kalibrierung
(`docs/05_BETRIEB.md`), keine gemessenen Konstanten.

---

## Geldpolitik (`monetary_policy`)

**Auslöser:** FOMC-Beschluss, Pressekonferenz, Reden, unplanmäßige Sitzungen,
Personalien an der Fed-Spitze.

| | |
|---|---|
| Impact-Skala | 0,55 Tagesvolatilitäten (höchste aller Klassen) |
| Halbwertszeit | ~45 min, bei Überraschungen länger |
| Leitinstrumente | ES, NQ, ZN, DXY, Gold |
| Vorzeichen | Zinssenkung = +1 (bullisch für Risiko-Assets) |

**Betas:** NQ 1,35 · ETH 1,30 · ZN 1,20 · BTC 1,15 · ES 1,00 · GC 0,95 · DXY −0,55

**Besonderheiten**
* Der Termin ist bekannt → Bereitschaftsmodus, Konsens vorladen, die
  Beschluss-URL direkt abfragen.
* Die **Pressekonferenz 30 Minuten nach dem Beschluss** bewegt oft mehr als der
  Beschluss selbst. Hier zählt der ASR-Kanal, nicht der Feed.
* Die Bewegung kommt in zwei Wellen: Erstreaktion auf die Zahl, dann Revision
  auf die Begründung. Die Erstreaktion dreht häufig.

**Typischer Fehler:** Auf den Beschluss handeln, wenn er dem Markterwartungswert
entspricht. Gehandelt wird die *Überraschung*, nicht das Ereignis. Bei
Leitzinsentscheiden ist der Konsens über Futures ablesbar und gehört in
`analysis/surprise.py` als `fed_funds` vorgeladen.

---

## Handelspolitik und Zölle (`trade_policy`)

**Auslöser:** Executive Orders, Proclamations, Section-232/301-Verfahren,
Exportkontrollen, Sanktionen, Vergeltungsmaßnahmen.

| | |
|---|---|
| Impact-Skala | 0,40 |
| Halbwertszeit | ~30 min |
| Leitinstrumente | ES, NQ, Kupfer, betroffene Währungen |
| Vorzeichen | neue Zölle = −1 |

**Betas:** HG 1,35 · NQ 1,25 · ES 1,00 · BTC 0,65 · GC −0,55 · ZN −0,20

**Besonderheiten**
* **Ankündigung ≠ Beschluss.** Die Abstufung ist die ganze Kunst:
  „erwägt" (×0,45) → „kündigt an" (×1,0) → „unterzeichnet, sofort wirksam"
  (×1,35). Der Bot bildet das über die Modifikatoren ab.
* Kupfer ist der reinste Zoll-Proxy – Konjunktursensitivität ohne
  Techniküberlagerung.
* Gold läuft gegenläufig (negatives Beta).
* Zölle sind gleichzeitig *wachstumsschädlich* und *inflationär*. Für Anleihen
  heben sich beide Effekte teilweise auf, deshalb das kleine Beta von −0,20.

**Typischer Fehler:** Die vierte Zollmeldung in zwei Wochen genauso handeln wie
die erste. Die Neuigkeitserkennung dämpft Wiederholungen, aber bei echtem
Regimewechsel muss die Impact-Skala neu geschätzt werden.

---

## Krypto-Politik (`crypto_policy`)

**Auslöser:** SEC/CFTC-Verfahren, ETF-Entscheidungen, Gesetzesvorhaben,
strategische Reserven, Äußerungen von Amtsträgern.

| | |
|---|---|
| Impact-Skala | 0,35 |
| Halbwertszeit | ~20 min (kürzeste der politischen Klassen) |
| Leitinstrument | BTC |
| Vorzeichen | kryptofreundlich = +1 |

**Betas:** HYPE 2,40 · XRP 2,10 · DOGE 2,00 · SOL 1,75 · ETH 1,30 · BTC 1,00

**Besonderheiten**
* **Die Beta-Spreizung ist die eigentliche Chance.** Dieselbe Nachricht bewegt
  einen dünnen Nebenwert zwei- bis dreimal so stark wie Bitcoin. Genau dafür
  existiert die Asset-Auswahl.
* **Namentliche Nennung schlägt alles.** Wird ein konkretes Asset genannt, zieht
  der Nennungsbonus die erwartete Bewegung um bis zu 60 % hoch. Hier greift der
  Entitäts-Sentiment-Pfad (`analysis/entity_sentiment.py`), weil solche Sätze
  meist keinen Fachbegriff enthalten.
* **Der Liquiditäts-Deckel ist hier bindend.** Genau die Werte mit dem höchsten
  Beta haben die dünnsten Bücher. Der 15-%-Buchtiefen-Deckel begrenzt die Größe
  regelmäßig stärker als das Risikobudget – das ist beabsichtigt.

**Fallbeispiel – die Rede mit der Hyperliquid-Erwähnung**

```
Satz 1    „We are going to make America the crypto capital of the world."
          sentiment +0,660 · confidence 0,720 · Konkretheit 0,18
          → Conviction 0,300 · unter der Schwelle 0,35 · kein Trade

Satz 2    „And I looked at Hyperliquid, this platform, it is very impressive,
           tremendous technology, we support it."
          → Entitäts-Pfad: HYPE namentlich + Wertung „impressive/tremendous/support"
          → sentiment +0,770 · Conviction 0,519 · über der Schwelle
          → Rangliste (mit Live-Quotes):
               HYPE  808 bps  Stop  99 bps  CRV 7,90
               SOL   356 bps  Stop  63 bps  CRV 5,44
               XRP   294 bps  Stop  66 bps  CRV 4,26
          → long HYPE; SOL abgelehnt, weil die Gruppe crypto_beta belegt ist

sofort    Scheibe 1 (50 %) gefüllt
+4 s      Scheibe 2 (30 %) auf dem Rücksetzer gefüllt
+80 s     Ziel 1 → 40 % verkauft    +311 USD  (+3,14 R)
+140 s    Ziel 2 → 30 % verkauft    +420 USD  (+5,66 R)
+892 s    Zeit-Stop → Rest        +1.063 USD  (+14,32 R)

Summe: +1.795 USD auf 100.000 USD Kapital, 23,1 R
```

Nachspielbar mit `newsbot demo`.

---

## Konjunkturdaten (`macro_data`)

**Auslöser:** CPI, PCE, NFP, PPI, BIP, ISM, Einzelhandel.

| | |
|---|---|
| Impact-Skala | 0,35 |
| Halbwertszeit | ~15 min (die kürzeste überhaupt) |
| Bewertung | ausschließlich über die Überraschung |

**Besonderheiten**
* Die Termine stehen Monate vorher fest – der einzige Fall, in dem der Bot
  vorbereitet in die Sekunde gehen kann.
* Der Text ist irrelevant, nur `(Ist − Konsens) / σ` zählt.
* Unter 0,5 σ Überraschung wird nicht gehandelt: das ist Rauschen.
* Die kurze Halbwertszeit ist ernst zu nehmen. Nach 15 Minuten handelt man
  nicht mehr die Zahl, sondern die Tagesstimmung.

**Typischer Fehler:** Nonfarm Payrolls als eindeutig zu behandeln. Eine starke
Zahl ist gut für die Konjunktur und schlecht für die Zinserwartung – die
Reaktion hängt vom Regime ab. Das Beta für NFP gehört häufiger nachkalibriert
als jedes andere.

---

## Geopolitik (`geopolitics`)

**Auslöser:** Militärische Eskalation, Waffenruhen, Blockaden, Wahlen.

| | |
|---|---|
| Impact-Skala | 0,45 |
| Halbwertszeit | ~40 min |
| Vorzeichen | Eskalation = −1 |

**Betas:** CL −0,90 · ES 0,85 · GC −0,85 · ZN −0,45

**Besonderheiten**
* Gold, Öl und Anleihen laufen gegenläufig – alle drei über negative Betas.
* Meldungen aus Kriegsgebieten sind die **fälschungsanfälligsten überhaupt**.
  Hier lohnt es, `confirm_required_from_tier` auf 3 zu senken.
* Erstmeldungen werden häufig innerhalb von Minuten revidiert. Die
  Widerspruchserkennung ist in dieser Klasse am wichtigsten.

---

## Energiepolitik (`energy_policy`)

**Auslöser:** OPEC+-Beschlüsse, strategische Reserven, Embargos,
Förderentscheidungen.

| | |
|---|---|
| Impact-Skala | 0,40 |
| Halbwertszeit | ~30 min |
| Leitinstrument | Rohöl |
| Vorzeichen | Förderkürzung = +1 (bullisch Öl) |

**Betas:** CL 1,00 · NQ −0,30 · ES −0,25

**Besonderheiten**
* OPEC-Beschlüsse sind oft vorab durchgesickert – die Neuigkeitserkennung ist
  hier besonders wichtig.
* Die Reaktion von Aktien ist gegenläufig, aber schwach und regimeabhängig.

---

## Fiskalpolitik (`fiscal_policy`)

**Auslöser:** Steuergesetze, Ausgabenpakete, Schuldenobergrenze,
Haushaltssperren.

| | |
|---|---|
| Impact-Skala | 0,30 (niedrigste) |
| Halbwertszeit | ~30 min |
| Vorzeichen | Steuersenkung = +1 |

**Besonderheiten**
* Gesetzgebungsverfahren ziehen sich über Wochen – fast jede Meldung ist eine
  Wiederholung. Die Neuigkeitserkennung filtert hier am meisten aus.
* Handelbar sind praktisch nur die Entscheidungspunkte: Abstimmung bestanden,
  Unterschrift geleistet, Verfahren gescheitert.

---

## Ein eigenes Playbook ergänzen

1. `EventClass` in `models.py` erweitern.
2. Begriffe in `lexicon.py` eintragen (Polarität, Gewicht, Konkretheitsbeitrag).
3. Halbwertszeit in `HALF_LIFE_S` setzen.
4. Impact-Skala in `selector.py` ergänzen.
5. Betas je Instrument in `universe.py` hinterlegen.
6. Testfall in `tests/test_lexicon.py` ergänzen.
7. Gegen historische Ereignisse kalibrieren (`backtest/event_study.py`).

Schritt 7 ist nicht optional. Ohne ihn ist das Playbook eine Vermutung.
