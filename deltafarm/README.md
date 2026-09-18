# deltafarm

Lokales Dashboard für delta-neutrales Funding-Farming über zwei Perp-DEX hinweg.
Gleiche Größe long auf der einen, short auf der anderen Börse; verdient wird an
der Funding-Differenz.

**Stand: Phase 1.** Es werden Marktdaten gelesen und Paare bewertet.
Es wird keine Order gesendet — die Handelsmethoden der Adapter werfen bewusst
einen Fehler. Börsen: **Extended** und **Lighter**.

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

Phase 1 braucht **keine Schlüssel**: alle genutzten Endpunkte sind öffentlich.

## Was das Dashboard zeigt

Eine Tabelle je Symbol mit der Funding-Rate beider Börsen, jeweils als
Stundenrate und als APR, dazu der Netto-APR des besten Paares, die Richtung
(wo long, wo short) und die Mindesthaltedauer bis zum Break-even.

In der Kopfzeile stehen dauerhaft Umgebung (TESTNET/MAINNET), Modus
(DRY RUN/LIVE) und der Verbindungsstatus beider Börsen.

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
GET /api/health          Status, Modus, Umgebung
GET /api/venues          je Börse: erreichbar, handelsfähig, Umgebung
GET /api/markets         Märkte je Börse
GET /api/funding         Vergleichsmatrix, normalisiert
GET /api/opportunities   Paare nach Netto-APR sortiert, mit Break-even
```

## Weiterführend

* `RESEARCH.md` — Machbarkeitsprüfung aller untersuchten Börsen, mit
  Quellenangabe je Aussage und einer Liste der offenen Punkte.
* `DECISIONS.md` — Architekturentscheidungen mit Begründung.
