# Architekturentscheidungen

Chronologisch. Jede Entscheidung mit Begründung und, wo zutreffend, mit dem Auslöser aus
der Recherche. Entscheidungen, die noch auf Freigabe warten, sind als **offen** markiert.

---

## ADR-001 – Recherche gegen offizielle SDK-Quelltexte statt gegen Doku-Seiten

**Phase:** 0 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Der Egress-Proxy dieser Umgebung blockiert sämtliche Doku-Domains der
untersuchten Börsen (`docs.variational.io`, `api.docs.extended.exchange`,
`docs.extended.exchange`, `hyperliquid.gitbook.io`, `docs.lighter.xyz`) und die API-Hosts
selbst. Die Vorgabe „erfinde keine Endpunkte" schließt Raten aus.

**Entscheidung.** Primärquelle sind die offiziellen SDK-Repositories, per `git clone`
geholt und im Quelltext gelesen. Web-Suche nur als Sekundärquelle. Jede Angabe in
`RESEARCH.md` trägt einen Quellenmarker (`[SDK]`, `[DOC]`, `[SUCHE]`, `[DRITT]`, `[OFFEN]`).

**Begründung.** Der SDK-Quelltext ist das, was tatsächlich gegen die API läuft – er ist
damit belastbarer als eine Doku-Seite, die der Implementierung hinterherhinken kann.
Der Quellenmarker macht später nachvollziehbar, welche Zeile Code auf welcher Beleglage
steht.

**Folgen.** Angaben, die nur `[SUCHE]` oder `[DRITT]` tragen, dürfen nicht ungeprüft in
Code wandern. Die offenen Punkte sind in `RESEARCH.md` Abschnitt 6 einzeln aufgeführt.

---

## ADR-002 – Variational bleibt ein Adapter ohne Handelsfunktion

**Phase:** 0 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Variationals Trading-API ist laut eigener Doku weiterhin in Entwicklung und
für keinen Nutzer freigeschaltet; öffentlich gibt es nur `GET /metadata/stats` ohne
Authentifizierung.

**Entscheidung.** Variational wird als vollwertiger Adapter mit `supports_trading = False`
geführt, nicht als Sonderfall neben dem Interface.

**Begründung.** Das Flag steht bereits im Interface. Ein dritter Adapter, der nur lesen
kann, erzwingt früh, dass Engine und UI den Read-Only-Fall überall sauber behandeln – statt
dass es später beim Anschalten der Trading-API auffällt.

**Folgen.** Phase 1 baut **drei** Adapter, nicht zwei. Öffnet Variational seine
Trading-API, sind nur `supports_trading`, die sechs Handels-/Account-Methoden, das
Symbol-Mapping und ein Rate-Limiter-Profil zu ergänzen.

---

## ADR-003 – Funding-Normalisierung gehört in den Adapter, nicht in die Engine

**Phase:** 0 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Die Recherche hat vier verschiedene Funding-Mechaniken zutage gefördert:
stündliche Zahlung mit Stundenrate (Extended, Lighter), stündliche Zahlung als 1/8 einer
8h-Rate (Hyperliquid), kontinuierliche Berechnung pro Sekunde (Paradex) und ein festes
Intervall, das **je Symbol** 4 oder 8 Stunden beträgt (Aster).

**Entscheidung.** `FundingInfo` trägt immer drei Angaben: die native Rate, das native
Intervall und die normalisierte Stundenrate samt APR. Normalisiert wird genau einmal, im
Adapter. Jeder Adapter belegt seine Normalisierung mit einem Test gegen von Hand
gerechnete Referenzwerte; ohne diesen Test wird er nicht registriert.

**Begründung.** Ein 8h-Wert, der unnormalisiert neben einem 1h-Wert steht, ist der teuerste
denkbare Fehler dieses Projekts – er verschiebt den Netto-APR um Faktor 8. Aster belegt
außerdem, dass das Intervall je Symbol variieren kann; eine Annahme „Börse X hat Intervall
Y" wäre also schon heute falsch.

**Folgen.** `get_funding()` darf das Intervall nie aus einer Konstante ziehen, wenn die
Börse es ausliefert (Aster: `fundingIntervalHours` aus `GET /fapi/v3/fundingInfo`).

---

## ADR-004 – Deklariertes Funding-Intervall wird beim Start empirisch geprüft

**Phase:** 0 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Ob Extended in `funding_rate` die 1h- oder die 8h-Rate liefert, ließ sich aus
dem SDK nicht klären (`RESEARCH.md`, OFFEN-4). Dieselbe Unsicherheit kann jede Börse nach
einer Änderung ihrer Mechanik treffen.

**Entscheidung.** Beim Start holt jeder Adapter die Funding-Historie und misst die
Abstände zwischen den Zeitstempeln. Passt der gemessene Abstand nicht zum deklarierten
Intervall, startet der Adapter nicht.

**Begründung.** Verlagert eine unbelegbare Annahme von einer Behauptung im Code zu einer
Messung gegen die Börse. Fängt zusätzlich den Fall ab, dass eine Börse ihre Mechanik
ändert, ohne dass wir es mitbekommen.

**Folgen.** Braucht einen Funding-Historien-Endpunkt je Börse. Für die empfohlenen Börsen
vorhanden: Extended `GET /info/<market>/funding`, Lighter `GET /api/v1/fundings`.

---

## ADR-005 – Gebühren werden zur Laufzeit geholt, nicht hart hinterlegt

**Phase:** 0 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Extended liefert Maker-/Taker-Gebühren **nicht** im Markt-Modell, sondern über
`GET /user/fees` je Markt **und je Account**. Öffentliche Angaben Dritter zu Extendeds
Gebühren widersprechen sich. Lighter dagegen liefert `taker_fee`/`maker_fee` öffentlich in
`orderBookDetails`.

**Entscheidung.** `get_symbol_rules()` füllt die Gebühren aus der Börse. Wo das einen
authentifizierten Aufruf braucht, macht der Adapter ihn. Keine Gebührenkonstanten im Code.

**Begründung.** Die Break-even-Rechnung aus Anforderung 6.2 entscheidet, ob eine Position
Geld verdient. Sie mit einem Listenpreis statt der eigenen Gebührenstufe zu rechnen, macht
sie systematisch zu optimistisch – und zwar genau in die Richtung, die Geld kostet.

**Folgen.** Die Vergleichsansicht (Dashboard-Bereich A) zeigt Break-even-Stunden erst
vollständig, wenn Account-Zugang besteht. Bis dahin muss sie kennzeichnen, dass sie mit
Standardgebühren rechnet.

---

## ADR-006 – `SymbolRules` bildet positionswertabhängige Hebel ab

**Phase:** 0 · **Status:** angenommen (Phase 1 umgesetzt) · **Datum:** 2026-09-18

**Kontext.** Das Interface im Auftrag sieht ein einzelnes `max_leverage` vor. Extended
liefert stattdessen eine `risk_factor_config`, aus der sich der maximale Hebel **abhängig
vom Positionswert** ergibt; Paradex liefert zusätzlich `position_limit`.

**Entscheidung.** `SymbolRules` bildet Positionswert → maximaler Hebel ab, mit
`max_leverage` als Sonderfall für Börsen, die nur einen Wert kennen.

**Begründung.** Sonst wirft der Adapter die Information weg, und die Margin-Preflight-
Prüfung rechnet bei großen Notionals zu optimistisch – also wieder in die teure Richtung.

**Folgen bei Zustimmung.** Kleine Erweiterung des Modells in `models/`, betrifft
Preflight-Prüfung 2.

---

## ADR-007 – Börsenpaar für den Start

**Phase:** 0 · **Status:** angenommen · **Datum:** 2026-09-18

**Entscheidung.** Handelndes Paar **Extended + Lighter**, lesendes Bein **Variational**.
Ausführungsreihenfolge zunächst **Lighter zuerst** (signierte Transaktion mit
Nonce-Verwaltung ist fragiler als ein REST-POST), als Konfigurationswert, in Phase 4 mit
echten Latenzmessungen zu überprüfen.

**Begründung.** Ausführlich in `RESEARCH.md` Abschnitt 4 und 5. Kurz: beide mit aktiv
gepflegtem offiziellem SDK und Testnet, beide für den Nutzer heute schon relevant, und
ihre Funding-Raten speisen sich nicht gegenseitig – anders als bei Paradex, dessen Rate
laut Drittquellen einen Median über sechs Handelsplätze inklusive Lighter bildet.

**Alternativen.** Paradex (technisch sauberstes Metadaten-Modell, `funding_payments` per
REST und WebSocket, aber mögliche Korrelation zur Lighter-Rate) und Hyperliquid (tiefste
Liquidität, aber keine Tick-/Lot-Felder, `float` im SDK, Airdrop vorbei).

**Offen mit Auswirkung auf diese Entscheidung.** OFFEN-6 – bei Extended ließ sich kein
Endpunkt für **einzelne** Funding-Zahlungen belegen; gefunden wurde nur die Summe
`realised_pnl_breakdown.funding_fees` je geschlossener Position. Anforderung 6.4 ist damit
bei Extended nicht ohne Weiteres erfüllbar. Lösungswege stehen in `RESEARCH.md` Abschnitt 6.


---

## ADR-008 – Eigenes Projektverzeichnis statt Wurzelverzeichnis

**Phase:** 1 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Der Auftrag gibt `app/`, `web/` und `tests/` im Wurzelverzeichnis
vor. Das Repository enthält dort bereits den `newsbot` mit eigenem `tests/` und
eigener `pyproject.toml` (`testpaths = ["tests"]`).

**Entscheidung.** Das Projekt liegt unter `deltafarm/`, die vorgegebene Struktur
gilt innerhalb dieses Verzeichnisses unverändert.

**Begründung.** Ein gemeinsames `tests/` hätte beide Testsuiten vermischt und die
bestehende zerbrochen. Die Vorgabe war für ein leeres Repository geschrieben.

---

## ADR-009 – Phase 1 spricht REST direkt an, nicht über die offiziellen SDKs

**Phase:** 1 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Für beide Börsen gibt es ein offizielles Python-SDK. Der Auftrag
gibt `httpx` als HTTP-Client vor. Phase 1 nutzt ausschließlich öffentliche
Endpunkte ohne Authentifizierung.

**Entscheidung.** Phase 1 ruft REST direkt über `httpx` auf. Endpunkte, Feldnamen
und Antwortverpackung sind aus den SDK-Quelltexten übernommen und in
`tests/fixtures_wire.py` nachgebildet. Ab Phase 3 kommen die SDKs für das
Signieren dazu – dort sind sie unverzichtbar (Stark-Signatur bei Extended,
Transaktionssignatur bei Lighter).

**Begründung.** Zwei schwergewichtige Abhängigkeiten (eine davon mit
Rust-Erweiterung) nur für öffentliche GET-Aufrufe wären unverhältnismäßig. Die
Feldnamen stammen trotzdem aus der offiziellen Quelle, nicht aus Vermutungen.

**Folgen.** Die Adapter akzeptieren je Feld dieselbe Menge an Schreibweisen wie
das jeweilige SDK (Extended etwa `funding_rate` und `f`), weil sich nicht
gegenprüfen ließ, welche die REST-API tatsächlich liefert.

---

## ADR-010 – Rate-Limiter über theoretische Ankunftszeit statt Token-Zähler

**Phase:** 1 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Die erste Umsetzung führte einen laufenden Token-Zähler. Beim
Auffüllen sammelt der Float-Restbeträge ein; fällt er dadurch auf 0,999… statt
1,0, wird die errechnete Wartezeit verschwindend klein. Eine Wartezeit von
1e-17 lässt sich auf eine Float-Uhr nicht mehr aufaddieren – die Schleife lief
endlos. Der Fehler trat in den Tests auf, nicht erst im Betrieb.

**Entscheidung.** Der Eimer führt eine absolute theoretische Ankunftszeit statt
eines Zählers und vergleicht Zeitpunkte mit einer Toleranz von einer
Nanosekunde.

**Begründung.** Eine absolute Größe korrigiert sich bei jedem Durchlauf selbst
und sammelt keine Restbeträge an. Ein Regressionstest hält den Fall fest.

**Folgen.** Zeit bleibt `float` – `time.monotonic()` liefert float, und Zeit ist
kein Geldbetrag. Die Decimal-Vorgabe gilt für Beträge, Größen und Preise.

---

## ADR-011 – Die Oberfläche liest den Zwischenspeicher, nicht die Börse

**Phase:** 1 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Anfangs lud jeder API-Aufruf bei Bedarf selbst nach. Beim
Smoke-Test gegen nicht erreichbare Börsen blockierte `/api/funding` deshalb
minutenlang: acht Symbole mal zwei Börsen mal vier Wiederholungen mit Backoff.
Auch die Startprüfung lief vor dem ersten `yield` und verzögerte den Serverstart.

**Entscheidung.** Ein Hintergrundlauf hält den Datenstand aktuell; die Routen
lesen ihn nur. Gibt es noch keinen, wird einmal mit Zeitlimit nachgeladen. Die
Startprüfung läuft ebenfalls im Hintergrund.

**Begründung.** Eine nicht erreichbare Börse soll in der Kopfzeile als nicht
erreichbar erscheinen – dafür muss die Oberfläche antworten können. Ein Filter
im Suchfeld darf ohnehin nie einen neuen Börsenabruf auslösen.

**Folgen.** Auch die Notfallantwort ohne Daten führt beide Börsen auf, sonst
zeigte die Kopfzeile nichts an, obwohl die Warnung dorthin verweist.

---

## ADR-012 – Unbelegte Annahmen werden abgesichert, nicht überbrückt

**Phase:** 1 · **Status:** angenommen · **Datum:** 2026-09-18

**Kontext.** Zwei Punkte ließen sich weder aus SDK noch aus Doku klären
(`RESEARCH.md`, OFFEN-14 und OFFEN-15): die Einheit von Lighters Feld `rate`
und der Wert, der dort für Lighter selbst im Feld `exchange` steht.

**Entscheidung.** Statt einer Annahme steht im Code jeweils eine Absicherung.
Bei der Einheit warnt eine Plausibilitätsprüfung, wenn die mittleren
Ratenbeträge beider Börsen um mehr als Faktor 20 auseinanderliegen. Bei der
Börsenkennung bricht der Adapter mit einer Fehlermeldung ab, die alle
gefundenen Kennungen nennt, statt den erstbesten Treffer zu nehmen.

**Begründung.** Eine fremde Börsenrate als Lighter-Rate zu führen oder Prozent
mit Brüchen zu vergleichen wäre in der Tabelle nicht zu erkennen – das Ergebnis
sähe plausibel aus und wäre falsch. Lieber keine Zahl als eine falsche.

**Folgen.** Beide Fragen bleiben offen und sind beim ersten Live-Abruf in einer
Minute zu klären. Die Absicherung ersetzt die Antwort nicht.
