# Quellen: wo marktbewegende Politik zuerst auftaucht

## 1. Die Latenzklassen

Der wichtigste Satz dieses Dokuments: **Mit RSS allein ist der erste
Preisimpuls vorbei, bevor man überhaupt gelesen hat.**

| Klasse | Latenz | Kanäle |
|---|---|---|
| **A** | < 1 s | kommerzielle Wire-Feeds, Direktabfrage von Behördenendpunkten, Live-Spracherkennung, verifizierte Originalaccounts |
| **B** | 1–15 s | schnelle Social-Abfragen, gute Behörden-Feeds |
| **C** | 15–120 s | übliche RSS-Feeds von Behörden und Medien |
| **D** | > 2 min | langsame Pflichtveröffentlichungen |

Klasse C ist **Bestätigungs- und Kontextmaterial**, nicht die Handelsgrundlage.
Wer den ersten Impuls handeln will, braucht A. Das ist eine Kostenfrage, keine
Programmierfrage.

## 2. Der Katalog

Vollständig in `config/sources.json`, anzeigbar mit:

```bash
newsbot quellen
```

### 2.1 Geldpolitik – die größten Einzelbewegungen

| Quelle | Klasse | Bemerkung |
|---|---|---|
| `federalreserve.gov/feeds/press_monetary.xml` | C | FOMC-Beschlüsse; der wichtigste Einzelfeed überhaupt |
| Direktabfrage der Beschluss-URL | **A** | Die URL des Statements folgt einem festen Muster (`monetary{JJJJMMTT}a.htm`). Ab wenigen Sekunden vor dem Termin abfragen – der schnellste öffentliche Weg. |
| FOMC-Pressekonferenz per ASR | **A** | 30 Minuten nach dem Beschluss; häufig **größere** Bewegung als der Beschluss selbst |
| EZB, BoE, BoJ | C | für EUR-, GBP-, JPY-Paare |

### 2.2 Handel und Zölle

| Quelle | Klasse | Bemerkung |
|---|---|---|
| Federal-Register-API (`PRESDOCU`) | C | **Amtlicher Kanal** für Executive Orders und Proclamations – dort steht der verbindliche Text |
| `whitehouse.gov/presidential-actions/feed/` | C | oft früher als das Federal Register |
| USTR, Treasury/OFAC | C–D | Section 232/301, Sanktionen |
| EU-Handelsgeneraldirektion | D | Gegenmaßnahmen |

### 2.3 Konjunkturdaten

BLS (CPI, NFP, PPI), BEA (BIP, PCE), Census, Destatis. Diese Termine sind
**im Voraus bekannt** – das ändert die Taktik grundlegend: Der Bot kann
Sekunden vorher in Bereitschaft gehen, Konsenswerte vorladen und muss nur noch
die Zahl gegen die Erwartung stellen (`analysis/surprise.py`).

### 2.4 Krypto-Regulierung

SEC (Pressemitteilungen und Litigation Releases), CFTC, dazu CoinDesk,
The Block, Cointelegraph als Kontext. Die Behördenfeeds sind Stufe 1, die
Medien Stufe 3–4.

### 2.5 Soziale Originalquellen

Hier liegt der größte Vorsprung **und** das größte Fälschungsrisiko.

```
Verifizierter Originalaccount eines Amtsträgers  →  Stufe 1 (Primärquelle)
Schnelle Aggregator-Accounts                     →  Stufe 4
Alles andere                                     →  Stufe 5 (nie allein handelbar)
```

Beobachtet werden u. a. `realDonaldTrump`, `WhiteHouse`, `POTUS`,
`federalreserve`, `USTreasury`, `SECGov`, `ecb` (Stufe 1) sowie schnelle
Terminal-Aggregatoren (Stufe 4).

**Warnung:** Gefälschte Meldungen über gehackte oder nachgeahmte Accounts haben
mehrfach Milliardenbewegungen ausgelöst. Der Bot verlangt deshalb ab Stufe 4
zwingend eine unabhängige Zweitquelle innerhalb von 45 Sekunden.

### 2.6 Live-Reden – der Kanal mit dem größten Vorsprung

Während Agenturen noch formulieren, liegt der Wortlaut bereits vor.

```
Audio-Stream (HLS/RTMP)
    → Spracherkennung (Whisper-Streaming, Deepgram, AssemblyAI)
    → TranscriptChunk-Strom
    → Satzsegmentierung + Kontextfenster        (sources/speech.py)
    → RawNews
```

Drei Besonderheiten, die dieses Modul löst:

1. **Teilsätze.** Ein Satz darf nicht erst nach seinem Ende ausgewertet werden,
   sonst verliert man Sekunden. Nach `max_wait_ms` (Standard 1200 ms) wird
   zwangsweise ausgewertet, was da ist.
2. **Kontext.** „Das machen wir sofort" ist ohne die Vorsätze wertlos. Ein
   gleitendes Fenster der letzten zwei Sätze wird mitgegeben.
3. **Erkennungsfehler.** Eigennamen werden systematisch falsch erkannt:
   `hyper liquid`, `higher liquid` → **Hyperliquid**; `salana` → Solana;
   `beeps` → basis points. Die Korrekturtabelle in `speech.py` läuft **vor**
   der Auswertung. Ohne sie geht genau der Fall aus der Aufgabenstellung verloren.

Vorläufige Hypothesen der Erkennung (`is_final=False`) werden nie gehandelt –
die Erkennung revidiert sie oft, und ein Trade auf einem revidierten Wort ist
der teuerste denkbare Fehler („we will **ban** crypto" vs. „we will **back**
crypto").

### 2.7 Kommerzielle Niedriglatenz-Feeds

Dow Jones Newswires, Bloomberg B-PIPE, Reuters/LSEG, Benzinga Pro. Strukturierte
Meldungen mit Millisekunden-Zeitstempel, oft maschinenlesbar vorkategorisiert.
Benzinga Pro ist der günstigste Einstieg in echte Wire-Latenz. Die Anbindung
läuft über `sources/websocket.py`; nur die `parse`-Funktion ist anbieterspezifisch.

## 3. Eigene Quellen anbinden

```python
from newsbot.sources.base import NewsSource
from newsbot.models import SourceTier

class MeineQuelle(NewsSource):
    def __init__(self):
        super().__init__("meine_quelle", SourceTier.WIRE)

    async def stream(self):
        while True:
            for eintrag in await hole_daten():
                news = self.emit(eintrag["titel"], url=eintrag["url"],
                                 published_ms=eintrag["ts"])
                if news is not None:      # None = Duplikat
                    yield news
            await asyncio.sleep(2)
```

`emit()` übernimmt Duplikatunterdrückung, Zeitstempel und Latenzmessung.

## 4. Quellenhygiene im Betrieb

* **Latenz jeder Quelle messen.** `source.stats.median_latency_ms` zeigt, wie
  alt Meldungen bei Ankunft sind. Eine Quelle, die konstant über 30 s liegt,
  ist Kontext, keine Handelsgrundlage – und sollte entsprechend eingestuft werden.
* **Stille Feeds überwachen.** `GuardState.stale_feeds()` meldet Quellen, die
  verdächtig lange geschwiegen haben. Ein ausgefallener Feed ist gefährlicher
  als ein fehlender, weil man ihn für ruhig hält.
* **Rate-Limits respektieren.** Abfrageintervalle unter einer Sekunde auf
  öffentlichen Behördenservern sind unhöflich und führen zur Sperre.
  `MIN_POLL_S = 0.5` ist eine harte Untergrenze im Code.
* **Nutzungsbedingungen prüfen.** Automatisiertes Abgreifen ist nicht überall
  erlaubt, und kommerzielle Feeds haben Lizenzbedingungen zur Weiterverwendung.
