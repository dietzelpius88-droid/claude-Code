# deltafarm

Lokales Dashboard für delta-neutrales Funding-Farming über zwei Perp-DEX hinweg.
Gleiche Größe long auf der einen, short auf der anderen Börse; verdient wird an
der Funding-Differenz.

**Stand: Phase 3.** Marktdaten, Konten, Journal — und Ausführung im
**Trockenlauf**. Der komplette Orderpfad läuft durch, inklusive Prüfung gegen
die Regeln der Börse, aber es geht nichts raus: jede Order wird nur geloggt.
Börsen: **Extended** und **Lighter**.

## Schnellstart

```bash
make setup     # virtuelle Umgebung, Python- und Node-Pakete
make demo      # Oberfläche mit Mock-Daten ansehen, ohne Schlüssel und ohne Netz
make dev       # Backend und Frontend gegen die echten Börsen
make test      # alle Tests
```

`make dev` startet das Backend auf `http://127.0.0.1:8787` und das Frontend auf
`http://127.0.0.1:5173`. Beides bindet ausschließlich an `127.0.0.1` und ist
weder aus dem LAN noch aus dem Internet erreichbar.

Für die Funding-Vergleichsansicht braucht es **keine Schlüssel** — alle dafür
genutzten Endpunkte sind öffentlich. Kontostände und Positionen erscheinen,
sobald Zugangsdaten in der `.env` stehen (siehe unten). Signiert wird nichts:
Extended liest mit dem `X-Api-Key`-Header, Lighter allein über den
Account-Index.

## Was das Dashboard zeigt

**Funding-Vergleich:** eine Tabelle je Symbol mit der Funding-Rate beider
Börsen, jeweils als Stundenrate und als APR, dazu der Netto-APR des besten
Paares, die Richtung (wo long, wo short) und die Mindesthaltedauer bis zum
Break-even.

**Offene Positionen:** je Symbol eine Karte mit beiden Beinen, Einstieg und
Mark-Preis, PnL, Rest-Delta in USD und Prozent, erhaltenem Funding, Haltedauer
und einem Balken je Bein für den Abstand zur Liquidation. Ein Symbol mit nur
einem offenen Bein wird deutlich als **NICHT GEHEDGT** markiert — das ist eine
ungesicherte Richtungswette, keine delta-neutrale Position.

**Paar eröffnen** (Dialog aus der Vergleichstabelle): Notional, maximaler
Slippage, Hebel je Seite und welches Bein zuerst ausgeführt wird. Darunter die
Preflight-Checkliste mit grünen, gelben und roten Punkten, die gerundeten Größen
je Börse, das Rest-Delta, die geschätzten Gebühren und die Mindesthaltedauer bis
zum Break-even. Der Ausführen-Knopf bleibt gesperrt, solange eine Prüfung rot ist.

**Journal** (eigener Tab): jede Aktion und jede Funding-Zahlung chronologisch,
filterbar, mit CSV-Export.

In der Kopfzeile stehen dauerhaft Umgebung (TESTNET/MAINNET), Modus
(DRY RUN/LIVE), Gesamtkapital und der Verbindungsstatus beider Börsen.

## Sicherheit

* Schlüssel stehen ausschließlich in `.env` (aus `.env.example` kopieren) oder
  in der Umgebung. `.env` ist in `.gitignore` und gehört **nie** ins Repository.
* Das Frontend sieht nie einen Schlüssel. Es spricht nur mit dem lokalen Backend.
* Der Antworttext einer Börse wird bei Fehlern gekürzt geloggt; Schlüssel stehen
  nur in Headern und erscheinen daher nicht im Log.

### Schlüssel anlegen (ab Phase 2 nötig)

**Extended** — API-Management der jeweiligen Umgebung:

* Testnet: <https://starknet.sepolia.extended.exchange/api-management>
* Mainnet: <https://starknet.extended.exchange/api-management>

Dort API-Key, Public/Private Stark-Key und Vault-ID je Sub-Account abholen.
Jeder Sub-Account ist eine eigene Starknet-Position mit eigenen Schlüsseln.
**Withdrawal-Rechte bei allen Schlüsseln deaktiviert lassen.** Wo die Börse
getrennte Schlüssel für Lesen und Handeln anbietet, beide getrennt anlegen und
den Leseschlüssel für alles nutzen, was nicht handelt.

**Lighter** — Zugang über das Ethereum-Wallet; Account-Index, API-Key-Index und
den privaten API-Key in die `.env` eintragen.

## Umgebung und Modus

| Variable | Standard | Bedeutung |
|---|---|---|
| `DELTAFARM_DRY_RUN` | `true` | Orderpfade laufen durch, es wird nichts gesendet |
| `DELTAFARM_ENVIRONMENT` | `testnet` | `testnet` oder `mainnet` |
| `DELTAFARM_PORT` | `8787` | Port des Backends, immer auf 127.0.0.1 |
| `DELTAFARM_POLL_SECONDS` | `20` | Takt der Hintergrundaktualisierung |
| `DELTAFARM_PREVIEW_MAX_AGE` | `15` | Sekunden, die eine Vorschau gültig bleibt |
| `DELTAFARM_MARGIN_SHARE` | `0.5` | Anteil der freien Margin, den eine Position belegen darf |
| `DELTAFARM_HEDGE_TIMEOUT` | `10` | Sekunden, bis ein halb gefülltes Paar als ungesichert gilt |
| `EXTENDED_RATE_PER_SECOND` | `5.0` | Rate Limit, bewusst konservativ |
| `LIGHTER_RATE_PER_SECOND` | `5.0` | Rate Limit, bewusst konservativ |

Der Wechsel auf Mainnet ist bewusst zweistufig: `DELTAFARM_ENVIRONMENT=mainnet`
schaltet die Base-URLs um, `DELTAFARM_DRY_RUN=false` erlaubt echte Orders. Beides
zusammen ist erst ab Phase 5 vorgesehen, und die Kopfzeile zeigt den Zustand
dauerhaft an.

## Wie die Funding-Raten verglichen werden

Die Börsen rechnen unterschiedlich: stündliche Zahlung, stündliche Zahlung als
Achtel einer 8-Stunden-Rate, kontinuierliche Berechnung, feste Intervalle, die
je Symbol verschieden sein können. Verglichen wird deshalb **nie** die native
Rate, sondern immer die daraus normalisierte Stundenrate.

Zwei Schutzmechanismen laufen mit:

1. **Intervallprüfung beim Start.** Jeder Adapter misst die Abstände der
   tatsächlichen Funding-Zeitstempel und vergleicht sie mit dem deklarierten
   Intervall. Weicht es ab, steht das als Fehler im Log — bevor eine falsche
   Kennzahl in der Tabelle landet.
2. **Plausibilitätsprüfung.** Liegen die mittleren Ratenbeträge zweier Börsen um
   mehr als Faktor 20 auseinander, erscheint eine Warnung im Dashboard. Das
   deutet auf unterschiedliche Einheiten (Bruch gegen Prozent) hin, nicht auf
   einen echten Marktunterschied.
3. **Asset-Prüfung.** Ein gleicher Ticker macht zwei Listings noch nicht zum
   selben Asset. Bevor zwei Raten verglichen werden, müssen die Mark-Preise das
   bestätigen; weichen sie um mehr als 2 % ab, wird das Paar als ungültig
   markiert und erscheint nie als Vorschlag. Ohne Preise bleibt die Prüfung
   unentschieden und die Oberfläche zeigt ein Fragezeichen.

Die beiden Börsen geben ihre Rate unterschiedlich an: Extended einen Bruch je
Zahlungsintervall, Lighter **Prozent pro Jahr**. Die Art der Angabe wird getrennt
vom Zahlungsintervall geführt; verglichen wird ausschließlich die daraus
normalisierte Stundenrate.

## Aufbau

```
app/
  adapters/   ein Modul je Börse, alle gegen dasselbe Interface
  core/       Funding-Mathematik, Break-even, Paarbildung, Symboltabelle
  api/        FastAPI-Routen
  models/     fachliche Modelle
  storage/    SQLite und Journal (ab Phase 2)
  config.py
web/          React-Frontend
tests/
```

Alle Geldbeträge, Größen und Preise sind intern `Decimal`, nie `float` — auch
beim Lesen der Antworten (`json.loads(..., parse_float=Decimal)`) und bei der
Ausgabe über die API (Decimals gehen als String über die Leitung).

## API

```
GET /api/health                Status, Modus, Umgebung
GET /api/venues                je Börse: erreichbar, handelsfähig, Umgebung
GET /api/markets               Märkte je Börse
GET /api/funding               Vergleichsmatrix, normalisiert
GET /api/opportunities         Paare nach Netto-APR sortiert, mit Break-even
GET /api/balances              Kontostände je Börse
GET /api/positions             offene Positionen, zu Paaren gruppiert
GET /api/journal?format=csv    Journal als JSON oder CSV

POST /api/pairs/preview        Sizing und Preflight, ohne Order
POST /api/pairs/open           führt eine gültige Vorschau aus
GET  /api/pairs                offene und geschlossene Paare
POST /api/pairs/{id}/close     Paar beidseitig schließen
POST /api/pairs/{id}/resolve   UNHEDGED auflösen: hedge | rollback
POST /api/panic                Kill Switch
```

## Ausführung und ihre Absicherungen

**Trockenlauf.** `DELTAFARM_DRY_RUN=true` ist Standard. Der Orderpfad läuft
vollständig durch — Größe gegen Lot-Size und Mindestnotional geprüft, Gebühr
geschätzt, alles protokolliert —, aber nichts wird gesendet. Steht die Variable
auf `false`, sagt der Adapter ausdrücklich, dass Live-Orders noch nicht
freigeschaltet sind (Phase 4), statt stillschweigend nichts zu tun.

**Vorschau mit Verfallsdatum.** `POST /api/pairs/open` nimmt nur einen Token an,
der jünger als 15 Sekunden ist und dessen Grundlage sich seither nicht bewegt
hat: Preis höchstens 0,2 %, Funding-Vorzeichen unverändert, Netto-Funding noch
positiv. Danach ist der Token verbraucht — ein zweiter Klick eröffnet keine
zweite Position. Ein alter Browser-Tab kann damit keine Order auslösen.

**Der gefährliche Zustand.** Ist ein Bein gefüllt und das andere nicht, geht das
Paar in `UNHEDGED`. Die Oberfläche zeigt dann einen nicht wegklickbaren Alarm mit
zwei Auswegen: Gegenseite nachziehen oder erstes Bein schließen. Ohne
Entscheidung passiert nichts — außer `auto_rollback` ist ausdrücklich
eingeschaltet.

Läuft der Timer ab (Standard 10 s), ist der Zustand der zweiten Order
**unbekannt**, nicht „nicht gefüllt": die Börse kann sie trotzdem ausgeführt
haben. Die Meldung sagt das, damit beim Nachziehen nicht doppelt gehedgt wird.

**Wiederanlauf.** Jeder Zustandsübergang steht in der Datenbank, bevor die Order
rausgeht. Beim Start gleicht die Anwendung hängengebliebene Paare gegen den
tatsächlichen Börsenzustand ab und meldet Abweichungen.

**Kill Switch.** Ein Knopf in der Kopfzeile, eine einzige Rückfrage, dann werden
alle offenen Orders storniert und alle Positionen geschlossen. Er zieht die
Symbole aus zwei Quellen — der Positionsliste der Börse und den offenen Paaren
in der Datenbank — und fängt jede Ausnahme ab. Er darf an nichts scheitern.

## Datenbank

Eine SQLite-Datei (`deltafarm.db`), kein Server. Tabellen: `venues`, `pairs`,
`legs`, `orders`, `fills`, `funding_payments`, `events`, `snapshots`.

Beträge werden als **Text** abgelegt — SQLAlchemys `Numeric` liefe auf SQLite
durch `float`. Schemaänderungen laufen über Alembic:

```bash
.venv/bin/alembic upgrade head        # bestehende Datei migrieren
.venv/bin/alembic revision --autogenerate -m "…"
```

Funding-Zahlungen werden periodisch nachgeladen und über (Börse, externe ID)
gegen Doppelzählung gesichert. Eine Zahlung, die nicht von der Börse bestätigt,
sondern aus Rate und Größe gerechnet wurde, ist im Journal und im CSV-Export als
`gerechnet` gekennzeichnet und wird nie wie eine bestätigte dargestellt.

## Weiterführend

* `RESEARCH.md` — Machbarkeitsprüfung aller untersuchten Börsen, mit
  Quellenangabe je Aussage und einer Liste der offenen Punkte.
* `DECISIONS.md` — Architekturentscheidungen mit Begründung.
