# Phase 0 – Machbarkeitsprüfung: Perp-DEX-Adapter für Delta-Neutral-Farming

**Stand:** 2026-09-18
**Zweck:** Entscheidungsgrundlage, welche zwei Börsen das Paar bilden. Kein Anwendungscode.

---

## 0. Methodik und Quellenqualität – bitte zuerst lesen

Diese Sitzung hat **keinen direkten Netzzugriff auf die Doku-Domains**. Der Egress-Proxy
blockiert `docs.variational.io`, `api.docs.extended.exchange`, `docs.extended.exchange`,
`hyperliquid.gitbook.io`, `docs.lighter.xyz` und die API-Hosts selbst. Ich konnte die
Doku-Seiten also nicht abrufen.

Was stattdessen möglich war und was ich genutzt habe:

1. **Offizielle SDK-Quelltexte per `git clone`.** Das ist die *stärkste* verfügbare Quelle –
   stärker als eine Doku-Seite, weil der Code das ist, was tatsächlich gegen die API läuft.
   Ich habe sechs offizielle Repositories geklont und die Endpunkte, Feldnamen, Base-URLs
   und Signaturverfahren direkt aus dem Quelltext gelesen.
2. **Web-Suche.** Liefert Zusammenfassungen der offiziellen Doku-Seiten, aber nicht deren
   Volltext. Schwächer, weil zwischen Doku und mir eine Zusammenfassung steht.

Jede Angabe unten ist mit ihrer Quellenqualität markiert:

| Marker | Bedeutung |
|---|---|
| **[SDK]** | Aus dem offiziellen SDK-Quelltext gelesen, in dieser Sitzung geklont. Belastbar. |
| **[DOC]** | Aus dem offiziellen Doku-Repository gelesen (Aster veröffentlicht seine Doku als Git-Repo). Belastbar. |
| **[SUCHE]** | Nur über eine Suchmaschinen-Zusammenfassung der offiziellen Doku. **Vor Live-Einsatz gegenprüfen.** |
| **[DRITT]** | Drittquelle (Blog, Review-Seite). Nur als Indiz, nicht als Grundlage für Code. |
| **[OFFEN]** | Konnte ich nicht belegen. Siehe Abschnitt 6. |

**Geklonte offizielle Repositories** (HEAD-Stand zum Zeitpunkt dieser Recherche):

| Börse | Repository | HEAD | Datum | Zustand |
|---|---|---|---|---|
| Extended | `x10xchange/python_sdk` (Branch `starknet` = Default) | `da9a797` | 2026-09-04 | aktiv gepflegt |
| Lighter | `elliottech/lighter-python` | `a38b640` (v1.1.4) | 2026-09-15 | sehr aktiv |
| Hyperliquid | `hyperliquid-dex/hyperliquid-python-sdk` | `2fdb18f` | 2026-06-05 | gepflegt, aber ~3,5 Monate ruhig |
| Paradex | `tradeparadex/paradex-py` | `daabde7` | 2026-09-17 | sehr aktiv |
| edgeX | `edgex-Tech/edgex-python-sdk` (v2.0.1) | `487274d` | 2026-09-10 | aktiv |
| Aster | `asterdex/api-docs` (Doku, kein SDK) | `0bd2a1a` | 2026-09-18 | sehr aktiv |

Für **Variational** existiert kein öffentliches SDK-Repository, das ich finden konnte. Alle
Angaben dort sind [SUCHE] oder [OFFEN].

---

## 1. Ergebnis in einem Satz

**Variational kann in dieser Ausbaustufe kein handelndes Bein sein** – die Trading-API ist
laut eigener Doku weiterhin in Entwicklung und für niemanden freigeschaltet. Das lesende
Bein kann Variational bleiben. Für das zweite handelnde Bein empfehle ich
**Extended + Lighter**, mit Paradex als starkem Ersatzkandidaten.

### Übersicht

| Börse | Orders per öffentlicher API? | Offizielles Python-SDK | Testnet | Funding-Intervall | Echte Funding-Zahlungen abrufbar? |
|---|---|---|---|---|---|
| **Variational** | **Nein** [SUCHE] | nein | [OFFEN] | variabel, 8h-Fenster [SUCHE] | nein (keine Account-API) |
| **Extended** | Ja [SDK] | ja, aktiv [SDK] | ja [SDK] | stündliche Zahlung [SUCHE] | **nur aggregiert** [SDK] ⚠ |
| **Lighter** | Ja [SDK] | ja, sehr aktiv [SDK] | ja [SDK] | 1h [SDK] | **ja, einzeln** [SDK] ✅ |
| **Hyperliquid** | Ja [SDK] | ja [SDK] | ja [SDK] | 1h (= 1/8 der 8h-Rate) [SUCHE] | **ja, einzeln** [SDK] ✅ |
| **Paradex** | Ja [SDK] | ja, sehr aktiv [SDK] | ja [SDK] | **kontinuierlich/pro Sekunde** [DRITT] ⚠ | **ja, einzeln + WS** [SDK] ✅ |
| **edgeX** | Ja [SDK] | ja [SDK] | ja [SDK] | [OFFEN] | [OFFEN] ⚠ |
| **Aster** | Ja [DOC] | **nein** [DOC] | ja [DOC] | **je Symbol 4h oder 8h** [DOC] ⚠ | ja (`income`, `FUNDING_FEE`) [DOC] ✅ |

---

## 2. Die Börsen im Einzelnen

### 2.1 Variational

**Orders per API: nein.**

Die offizielle Doku sagt, die Trading-API sei weiterhin in Entwicklung und stehe noch
keinem Nutzer zur Verfügung; es gibt ein Formular, um benachrichtigt zu werden. [SUCHE]
Damit bestätigt sich dein Kenntnisstand.

* **Read-Only-API:** Base-URL `https://omni-client-api.prod.ap-northeast-1.variational.io`,
  Endpunkt `GET /metadata/stats`, ohne Authentifizierung. [SUCHE]
* **Antwortfelder (bestätigt):** `total_volume_24h`, `cumulative_volume`, `tvl`, sowie ein
  Array `listings` mit mindestens `ticker` und `mark_price`. [SUCHE]
  Die weiteren Felder aus deinem Anhang (Volumen, Open Interest je Seite, Funding-Rate,
  Spread, Quotes) konnte ich **nicht einzeln bestätigen** → [OFFEN-1].
* **Rate Limit:** 10 Requests / 10 Sekunden pro IP, 1000 pro Minute global. [SUCHE]
  → deckt sich mit deinem Anhang.
* **Funding-Mechanik:** Premium-Index wird alle 60 Sekunden gesampelt; Zinsanteil
  0,00125 % pro Stunde; Deckel 2 % pro Stunde (mit manueller Override-Möglichkeit).
  Omni nutzt zusätzlich ein Modell, das auf die **Open-Interest-Schieflage** abstellt statt
  auf den reinen Preis-Premium, weil viele Listings illiquide sind. [SUCHE]
  Deine Anhang-Angabe „in den ersten sieben Stunden eines Acht-Stunden-Fensters ist die
  Stundenrate null" konnte ich **nicht bestätigen** → [OFFEN-2]. Das ist für die
  Vergleichsmatrix erheblich: träfe es zu, wäre eine naive Stundenrate sieben von acht
  Stunden irreführend.
* **SDK / WebSocket / Testnet:** nichts öffentlich auffindbar → [OFFEN-3].
* **Punkte:** laut Drittquellen Pre-Points-Closed-Beta, 50 % von `$VAR` für die Community
  angekündigt. [DRITT] – nicht als Planungsgrundlage belastbar.

**Folge für die Architektur:** Variational bekommt einen Adapter mit
`supports_trading = False`. Genau dafür ist das Flag im Interface vorgesehen. Die Börse
erscheint in der Funding-Vergleichsansicht, aber die Engine bietet sie nicht als Bein an.

---

### 2.2 Extended

**Orders per API: ja.** Alle Endpunkte aus deinem Anhang haben sich im SDK-Quelltext
bestätigt – keine Korrektur nötig.

* **SDK:** `x10xchange/python_sdk`, Python ≥ 3.10, Default-Branch `starknet`,
  Installation `pip install x10-python-trading-starknet`. Letzter Commit 2026-09-04. [SDK]
  Signatur läuft über einen Rust-Wrapper (`stark-crypto-wrapper-py`); die README nennt
  Windows-ARM64 als eingeschränkt, Linux/macOS sind voll unterstützt. [SDK]
* **Base-URLs** [SDK, aus `x10/config.py`]:
  * Mainnet REST `https://api.starknet.extended.exchange/api/v1`
  * Testnet REST `https://api.starknet.sepolia.extended.exchange/api/v1`
  * Stream `wss://api.starknet.extended.exchange/stream.extended.exchange/v1`
    (Testnet analog mit `sepolia`), zusätzlich ein RPC-Stream unter `/v2/rpc`
  * Starknet-Domain: `SN_MAIN` bzw. `SN_SEPOLIA`, Signing-Domain `extended.exchange`
    bzw. `starknet.sepolia.extended.exchange`
* **Authentifizierung** [SDK, `x10/utils/http.py`]: Header `X-Api-Key` für Lesen,
  zusätzlich Stark-Signatur (SNIP-12) für Ordermanagement. Der `User-Agent`-Header wird
  vom SDK gesetzt (`X10PythonTradingClient/<version>`). Das SDK maskiert `x-api-key` in
  eigenen Logs – dieses Verhalten übernehmen wir.
* **Endpunkte** [SDK, alle aus `x10/clients/rest/modules/`]:
  * Märkte: `GET /info/markets`, `GET /info/markets/<market>/stats`, `GET /info/settings`,
    `GET /info/assets`
  * Funding: `GET /info/<market>/funding` mit `startTime`/`endTime` (Millisekunden-Epoch)
    → Liste aus `market`, `funding_rate`, `timestamp`
  * Orderbuch: `GET /info/markets/<market>/orderbook`
  * Account: `GET /user/balance`, `GET /user/positions`, `GET /user/positions/history`,
    `GET /user/trades`, `GET /user/fees`, `GET /user/leverage`, `GET /user/assetOperations`
  * Handel: `POST /user/order`, `DELETE /user/order?externalId=…`,
    `DELETE /user/orders/<order_id>`, `POST /user/order/massCancel`
* **Sizing-Metadaten** [SDK, `TradingConfigModel`]: `min_order_size`,
  `min_order_size_change` (= Lot-Size), `min_price_change` (= Tick-Size),
  `max_market_order_value`, `max_position_value`, `max_leverage` sowie ein
  `risk_factor_config`, aus dem sich der maximale Hebel **abhängig vom Positionswert**
  ergibt. Das ist mehr, als das Interface in Abschnitt 5 vorsieht – wir sollten
  `SymbolRules` um einen positionswertabhängigen Hebel erweitern.
* **Gebühren** [SDK]: **stehen nicht im Markt-Modell.** Sie kommen aus
  `GET /user/fees` → `maker_fee_rate`, `taker_fee_rate`, `builder_fee_rate`, je Markt und
  **je Account**. Konsequenz: `get_symbol_rules()` kann die Gebühren bei Extended nicht
  allein aus Marktdaten füllen; der Adapter muss sie im authentifizierten Pfad nachladen.
  Öffentliche Angaben Dritter widersprechen sich (2 bp Maker / 4 bp Taker gegen
  0 % Maker / 0,05 % Taker) [DRITT] – ein weiterer Grund, zur Laufzeit `/user/fees` zu
  fragen statt eine Konstante zu hinterlegen.
* **WebSocket** [SDK, `x10/clients/stream/stream_client.py`]: `/orderbooks/<market?>`,
  `/publicTrades/<market?>`, `/funding/<market?>`, `/candles/<market>/<type>` und
  `/account` (mit API-Key). Im v2-RPC-Stream zusätzlich `scope: "funding-rates"`.
* **Funding-Mechanik:** Zahlung **stündlich**; Formel
  `(Average Premium + clamp(Interest Rate − Average Premium, ±0,05 %)) / 8`, also mit
  8-Stunden-Realisierungsfenster, das bereits in die Formel eingerechnet ist. Die Doku
  beschreibt die API-Datensätze als die tatsächlich angewandten **1-Stunden-Raten**. [SUCHE]
  → Das ist die kritischste Einzelannahme des Projekts, siehe [OFFEN-4].
* **Rate Limits:** Drosselung pro IP, 429 bei Überschreitung, Erhöhung auf Anfrage;
  **konkrete Zahlen konnte ich nicht belegen** → [OFFEN-5]. Deine Anhang-Angabe
  „ab 1000 Requests/Minute" bleibt unbestätigt.

**⚠ Wichtigste Lücke bei Extended – echte Funding-Zahlungen.**
Das SDK kennt **keinen** Endpunkt, der einzelne Funding-Zahlungen eines Accounts liefert.
`AssetOperationType` umfasst nur `CLAIM`, `DEPOSIT`, `FAST_WITHDRAWAL`, `SLOW_WITHDRAWAL`,
`TRANSFER` – kein Funding. [SDK] Gefunden habe ich nur eine **Summe**:
`GET /user/positions/history` liefert je geschlossener Position ein
`realised_pnl_breakdown` mit `trade_pnl`, `funding_fees`, `open_fees`, `close_fees`. [SDK]

Damit ist Anforderung 6.4 („aufgelaufene Funding-Zahlungen je Seite, aus den tatsächlichen
Zahlungen der Börse, nicht geschätzt") bei Extended **so nicht erfüllbar**, solange die
Position offen ist. Siehe [OFFEN-6] mit zwei Lösungswegen.

---

### 2.3 Lighter

**Orders per API: ja** – aber nicht als klassischer REST-Order-Endpunkt, sondern als
**signierte Transaktion** über `POST /api/v1/sendTx` (bzw. `sendTxBatch`, alternativ über
WebSocket). [SDK]

* **SDK:** `elliottech/lighter-python`, Python ≥ 3.8, letzter Commit 2026-09-15 (v1.1.4).
  Installation laut README `pip install git+https://github.com/elliottech/zklighter-perps-python.git`
  – deine Anhang-Angabe stimmt. [SDK]
  Das Repo enthält eine **`openapi.json`**, also eine maschinenlesbare Endpunktliste. Das
  ist für uns die beste Ausgangslage aller sieben Kandidaten.
* **Base-URLs** [SDK, `lighter/endpoint_profiles.py`]:
  * Mainnet `https://mainnet.zklighter.elliot.ai`, WS `wss://mainnet.zklighter.elliot.ai/stream`, Chain-ID 304
  * Testnet `https://testnet.zklighter.elliot.ai`, WS `wss://testnet.zklighter.elliot.ai/stream`, Chain-ID 300
  * zusätzlich zwei Robinhood-Profile
* **Authentifizierung:** signaturbasiert über einen API-Key-Index; das SDK erzeugt
  Auth-Tokens mit Ablaufzeit (`create_auth_token_with_expiry`, Standard 10 Minuten). [SDK]
* **Endpunkte** [SDK, `openapi.json`]:
  * Märkte/Regeln: `GET /api/v1/orderBooks`, `GET /api/v1/orderBookDetails`
  * Orderbuch: `GET /api/v1/orderBookOrders`
  * Funding aktuell: `GET /api/v1/funding-rates` → `market_id`, `exchange`, `symbol`, `rate`
  * Funding historisch: `GET /api/v1/fundings` mit `resolution` ∈ `{1h, 1d}`,
    `start_timestamp`, `end_timestamp`, `count_back` → `timestamp`, `value`, `rate`, `direction`
  * **Funding-Zahlungen: `GET /api/v1/positionFunding`** → `timestamp`, `market_id`,
    `funding_id`, `change`, `discount`, `rate`, `position_size`, `position_side`.
    Das ist exakt das, was Anforderung 6.4 braucht.
  * Account: `GET /api/v1/account`, `/api/v1/accountActiveOrders`,
    `/api/v1/accountInactiveOrders`, `/api/v1/accountTxs`, `/api/v1/pnl`,
    `/api/v1/accountLimits`
  * Handel: `POST /api/v1/sendTx`, `POST /api/v1/sendTxBatch`, `GET /api/v1/nextNonce`
* **Sizing-Metadaten und Gebühren in einem Aufruf** [SDK]: `orderBookDetails` liefert
  `taker_fee`, `maker_fee`, `liquidation_fee`, `min_base_amount`, `min_quote_amount`,
  `supported_size_decimals`, `supported_price_decimals`, `mark_price`, `index_price`,
  `default_initial_margin_fraction`, `maintenance_margin_fraction`,
  `closeout_margin_fraction`, `funding_clamp_small`, `funding_clamp_big`,
  `base_interest_rate`, `funding_premium_multiplier`.
  Damit ist `SymbolRules` bei Lighter **vollständig aus einem einzigen öffentlichen
  Endpunkt** befüllbar – der sauberste Adapter der ganzen Liste.
* **Idempotenz:** `create_order(...)` nimmt einen `client_order_index` entgegen. [SDK]
  Genau der Haken, an dem unsere Client-Order-ID hängt.
* **WebSocket:** Das SDK umschließt die Kanäle `order_book/{market_id}` und
  `account_all/{account_id}`. [SDK] Ein Funding- oder Mark-Preis-Kanal ist im SDK **nicht**
  umschlossen; ob das Protokoll mehr kann, ist [OFFEN-7].
* **Funding-Intervall:** Die Auflösung `1h` in `/api/v1/fundings` spricht klar für
  stündliches Funding. [SDK]
* **Rate Limits:** nicht im SDK hinterlegt → [OFFEN-8]. Es gibt `GET /api/v1/accountLimits`,
  das aber Account-Limits meint, nicht Request-Limits.
* **Punkte:** LIT-Token seit Dezember 2025 live, Season 3 laut Drittquellen noch nicht
  angekündigt. [DRITT]

---

### 2.4 Hyperliquid

**Orders per API: ja.** Reifste und am besten dokumentierte API im Feld.

* **SDK:** `hyperliquid-dex/hyperliquid-python-sdk`, letzter Commit 2026-06-05. [SDK]
  Gepflegt, aber merklich weniger Bewegung als Lighter/Paradex/Extended.
* **Base-URLs** [SDK, `hyperliquid/utils/constants.py`]:
  Mainnet `https://api.hyperliquid.xyz`, Testnet `https://api.hyperliquid-testnet.xyz`.
* **Authentifizierung:** EIP-712-Signaturen, üblicherweise über ein Agent-Wallet
  (`approveAgent`). [SDK]
* **Endpunkte:** Alles läuft über zwei POST-Routen mit einem `type`-Feld. [SDK]
  * `POST /info` mit `type`: `meta`, `metaAndAssetCtxs` (Mark-Preis, Funding, OI in einem
    Aufruf), `l2Book`, `fundingHistory`, **`userFunding`** (echte Funding-Zahlungen),
    `clearinghouseState` (Positionen + Margin), `userFees`, `userFills`, `userFillsByTime`,
    `openOrders`, `orderStatus`, `userRateLimit`, `userNonFundingLedgerUpdates`
  * `POST /exchange` mit Aktionen `order`, `cancel`, `cancelByCloid`, `batchModify`,
    `scheduleCancel`, `updateIsolatedMargin`, …
* **Idempotenz:** `cloid` (Client Order ID) wird bei `order` und `cancelByCloid`
  unterstützt. [SDK]
* **WebSocket** [SDK]: `allMids`, `l2Book`, `trades`, `bbo`, `candle`, `activeAssetCtx`,
  `webData2`, `userEvents`, `userFills`, **`userFundings`**, `orderUpdates`,
  `userNonFundingLedgerUpdates`. Die vollständigste Stream-Palette im Vergleich.
* **Funding-Mechanik:** Zahlung stündlich zu 1/8 der berechneten 8-Stunden-Rate;
  Zinsanteil 0,01 % pro 8 Stunden = 0,00125 % pro Stunde. [SUCHE]
* **Zwei Punkte, die gegen Hyperliquid als erstes Bein sprechen:**
  1. **Keine expliziten Tick-/Lot-Felder.** Statt Metadaten gilt eine *Regel*: Preise auf
     5 signifikante Stellen und maximal `6 − szDecimals` Nachkommastellen bei Perps;
     Mengen auf `szDecimals`. [SDK, `exchange.py:131-132`] Unser `SymbolRules` müsste
     diese Regel für Hyperliquid nachbilden statt Felder zu lesen – mehr börsenspezifische
     Eigenheit im Adapter, als mir lieb ist.
  2. **Das SDK rechnet intern mit `float`.** `float_to_wire` und die Preisrundung arbeiten
     auf `float` und werfen sogar bewusst eine Ausnahme, wenn Rundung Information
     kostet. [SDK, `signing.py:476-497`] Das kollidiert mit deiner harten Vorgabe
     „niemals `float`". Machbar wäre es (intern `Decimal`, Umwandlung erst an der
     SDK-Grenze, mit Prüfung auf verlustfreie Konversion), aber es ist zusätzlicher
     Aufwand und eine dauerhafte Fehlerquelle.
* **Punkte:** Das große HYPE-Airdrop ist Vergangenheit. [DRITT] Als reines Punkte-Ziel
  heute weniger attraktiv – als liquides, zuverlässiges Gegenbein aber erstklassig.

---

### 2.5 Paradex

**Orders per API: ja.** Technisch der zweitbeste Adapter-Kandidat nach Lighter.

* **SDK:** `tradeparadex/paradex-py`, letzter Commit 2026-09-17 – das aktivste Repo im
  Feld. [SDK]
* **Base-URLs** [SDK, `paradex_py/api/api_client.py`]:
  `https://api.{env}.paradex.trade/v1` (und `/v2`) mit `env` ∈ `{prod, testnet, nightly}`.
  WebSocket: `ws-public.api.{env}.paradex.trade` (öffentlich, Standard) und
  `ws.api.{env}.paradex.trade` (direkt, opt-in). [SDK]
* **Endpunkte** [SDK]: `markets`, `markets/summary`, `markets/klines`, `orderbook/{market}`,
  `bbo/{market}`, `trades`, **`funding/data`** (öffentlich, historisch),
  **`funding/payments`** (authentifiziert, echte Zahlungen), `orders`, `orders/batch`,
  `orders/{order_id}`, `orders/by_client_id/{client_id}`, `orders-history`, `algo/orders`,
  `fills`, `positions`, `balance`, `account`, `account/info`, `transfers`, `liquidations`,
  `tradebusts`, `system/time`, `system/state`, `points_data/{market}/{program}`.
* **Sizing-Metadaten** [SDK, `responses.py`]: `order_size_increment`, `price_tick_size`,
  `min_notional`, `max_order_size`, `position_limit` – **genau die Felder, die unser
  `SymbolRules` erwartet**, ohne Umrechnung.
* **Idempotenz:** `orders/by_client_id/{client_id}` belegt eine erstklassige
  Client-ID-Unterstützung inklusive Abfrage und Storno über die eigene ID. [SDK]
* **WebSocket-Kanäle** [SDK, `ws_client.py`]: `bbo.{market}`, `trades.{market}`,
  `orders.{market}` / `orders.ALL`, `fills.{market}`, `markets_summary.{market}` / `.ALL`,
  **`funding_data.{market}`**, **`funding_payments.{market}`**, dazu Order-Operationen
  über WS (`order.create`, `order.cancel`, `order.cancel_batch`,
  `order.cancel_on_disconnect`).
  `order.cancel_on_disconnect` ist für unseren Kill Switch bemerkenswert: die Börse räumt
  selbst auf, wenn unsere Verbindung stirbt.
* **⚠ Funding-Mechanik – Sonderfall:** Paradex hat laut Drittquellen im Juni 2026
  „Funding V2" eingeführt: keine festen 8-Stunden-Resets mehr, sondern eine
  **sekündlich neu berechnete Rate**, abgeleitet aus einem gewichteten Median über sechs
  Handelsplätze (Paradex selbst mit Gewicht 3,5; Binance, Bybit, OKX, Hyperliquid, Lighter
  mit je 1,2). [DRITT]
  Wenn das stimmt, ist Paradex **genau der Fall „variables Intervall"** aus deiner
  Anforderung – und zugleich ein Warnsignal: eine Rate, die sich aus Lighter-Daten
  mitspeist, ist gegenüber Lighter **nicht unabhängig**. Ein Paar Paradex/Lighter hätte
  strukturell korrelierte Funding-Raten und damit eine kleinere Differenz zu ernten.
  → [OFFEN-9], vor einer Paradex-Entscheidung zu klären.
* **Punkte:** Season 2 läuft laut Drittquellen bis etwa Q3 2026. [DRITT]

---

### 2.6 edgeX

**Orders per API: ja.**

* **SDK:** `edgex-Tech/edgex-python-sdk`, v2.0.1, letzter Commit 2026-09-10. [SDK]
  Der `main`-Branch führt das **V2-Contract-API-SDK**; V1 liegt auf dem Branch `v1` und
  ist abgekündigt. Für Neuanbindungen also zwingend V2. [SUCHE/SDK]
* **Base-URLs** [SDK]:
  * Mainnet REST `https://edgex-prod-v2.edgex.exchange`
  * Asset-/Spot-Host `https://spot.edgex.exchange`
  * Mainnet WS `wss://edgex-quote-prod-v2.edgex.exchange`
  * Testnet `https://testnet.edgex.exchange` – **Achtung:** diese URL steht im SDK nur in
    der Test- und Mock-Konfiguration (`run_mock_tests.py`, `tests/integration/config.py`),
    nicht in der README-Dokumentation. Vor Gebrauch bestätigen → [OFFEN-10].
* **Authentifizierung:** gemischt – HMAC-Header (`build_hmac_headers`,
  `should_sign_with_hmac`) **und** EIP-712-Typed-Data-Signaturen (`sign_typed_data`), mit
  getrennten Schlüsseln für Trading und Wallet
  (`resolve_trading_signer_address`, `resolve_wallet_signer_address`). [SDK]
  Die getrennten Schlüssel passen gut zu deiner Sicherheitsvorgabe „getrennte Schlüssel für
  Lesen und Handeln".
* **Endpunkte** [SDK]:
  * Öffentlich: `GET /api/v2/public/meta/getMetaData`, `/meta/getServerTime`,
    `/quote/getDepth`, `/quote/getTicker`, `/quote/getKline`, `/quote/getMarketStatus`,
    `/funding/getLatestFundingRate`, `/funding/getFundingRatePage`
  * Privat: `/api/v2/private/account/getAccountAsset`, `/account/getPositionByContractId`,
    `/account/getPositionTransactionPage`, `/account/getCollateralTransactionPage`,
    `/account/updateLeverageSetting`, `/account/setMarginMode`,
    `/order/createOrder`, `/order/cancelOrderById`, `/order/cancelOrderByClientOrderId`,
    `/order/cancelAllOrder`, `/order/getActiveOrderPage`,
    `/order/getHistoryOrderFillTransactionPage`, `/order/getMaxCreateOrderSize`
* **Idempotenz:** `cancelOrderByClientOrderId` und `getOrderByClientOrderId` belegen
  Client-Order-IDs. [SDK]
* **Gebühren:** `defaultMakerFeeRate` / `defaultTakerFeeRate` in den Metadaten,
  `tickSize` ebenfalls. [SDK]
* **⚠ Zwei Lücken:**
  * **Funding-Intervall nicht belegt** → [OFFEN-11].
  * **Kein eindeutiger Endpunkt für Funding-Zahlungen.** Plausible Kandidaten sind
    `getPositionTransactionPage` oder `getCollateralTransactionPage`, aber im SDK findet
    sich unter `account/` kein einziger Treffer auf „funding". → [OFFEN-12].
    Solange das offen ist, erfüllt edgeX Anforderung 6.4 nicht nachweisbar.
* **Punkte:** TGE laut Drittquellen am 31.03.2026 erfolgt; seither Referral-Rebates und
  laufende Trader-Rewards. [DRITT]

---

### 2.7 Aster

**Orders per API: ja** – und die Doku ist die zugänglichste von allen, weil sie als
Git-Repository (`asterdex/api-docs`, letzter Commit **heute**) veröffentlicht wird. [DOC]

* **Kein offizielles Python-SDK.** Im offiziellen Doku-Repo finden sich nur Demo-Skripte
  (`demo/aster-code.py`, `demo/sol_agent.py`, einige JS-Dateien), keine SDK-Verweise. [DOC]
  Wir müssten den Adapter vollständig selbst gegen REST schreiben. Machbar, weil die API
  im Binance-Stil aufgebaut ist, aber es ist Mehrarbeit ohne Referenzimplementierung.
* **Base-URLs** [DOC]: Mainnet `https://fapi.asterdex.com`,
  Testnet `https://fapi.asterdex-testnet.com`.
* **Authentifizierung V3** [DOC]: Signatur über die Felder `user` (Wallet der
  Hauptaccounts), `signer` (API-Wallet), `nonce` (Zeitstempel in **Mikrosekunden**) und
  `signature`, alles im Request-Body. Die Nonce darf höchstens 10 Sekunden von der
  Serverzeit abweichen – das ist ein direkter Treffer auf deine Preflight-Prüfung Nr. 1
  (Zeitversatz zur Börse). V1 nutzte HMAC und ist Altlast. [DOC/DRITT]
* **Endpunkte** [DOC]:
  * `GET /fapi/v3/exchangeInfo` (enthält ein `rateLimits`-Array)
  * `GET /fapi/v3/premiumIndex` → `lastFundingRate`, Mark-Preis, nächste Funding-Zeit
  * `GET /fapi/v3/fundingRate` (Historie, `startTime`/`endTime`)
  * **`GET /fapi/v3/fundingInfo`** → je Symbol `interestRate`, **`fundingIntervalHours`**,
    `fundingFeeCap`, `fundingFeeFloor`
  * `GET /fapi/v3/depth`, `POST /fapi/v3/order`, Account-/Positions-Endpunkte
  * `GET /fapi/v3/income` mit `incomeType` ∈ `{TRANSFER, WELCOME_BONUS, REALIZED_PNL,
    FUNDING_FEE, COMMISSION, INSURANCE_CLEAR, MARKET_MERCHANT_RETURN_REWARD}`
    → **`FUNDING_FEE` liefert die echten Funding-Zahlungen.**
* **⚠ Funding-Intervall ist je Symbol verschieden.** Das Doku-Beispiel zeigt
  `INJUSDT` mit `fundingIntervalHours: 8` und `ZORAUSDT` mit
  `fundingIntervalHours: 4`, mit unterschiedlichen Caps (0,03 gegen 0,02). [DOC]
  Das ist der beste Beleg dafür, dass die Normalisierung **je Symbol** und nicht je Börse
  erfolgen muss. Wenn Aster ins Spiel kommt, ist `fundingInfo` ein Pflichtaufruf vor jedem
  Vergleich.
* **Rate Limits** [DOC]: gewichtsbasiert im Binance-Stil. Antwort-Header
  `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` und
  `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)`; `429` bei Überschreitung,
  **`418` als automatischer IP-Bann**, wenn nach 429 weiter gesendet wird. Die konkreten
  Grenzen stehen im `rateLimits`-Array von `exchangeInfo`, sind also zur Laufzeit
  auslesbar – das ist für unseren Token-Bucket ideal, weil wir die Limits nicht hart
  kodieren müssen.
* **WebSocket** [DOC]: Mark-Preis- und Funding-Streams, je Symbol oder für alle Symbole,
  Push alle 1 oder 3 Sekunden.
* **Hinweis:** Laut Drittquellen werden seit dem 25.03.2026 keine neuen V1-API-Keys mehr
  ausgegeben, und seit dem 01.09.2026 setzen authentifizierte V3-Endpunkte eine erfolgte
  Einzahlung des verknüpften Haupt-Wallets voraus. [DRITT] Letzteres wäre für einen
  Testnet-Durchlauf relevant → [OFFEN-13].

---

## 3. Die Funding-Normalisierung ist das eigentliche Risiko

Du hast im Auftrag geschrieben, das sei der wichtigste Fehler, den das Projekt vermeiden
muss. Die Recherche bestätigt das deutlicher als erwartet – die sieben Börsen verteilen
sich auf **vier verschiedene Mechaniken**:

| Mechanik | Börsen | Was das für `FundingInfo` heißt |
|---|---|---|
| Stündliche Zahlung, API liefert Stundenrate | Extended [SUCHE], Lighter [SDK] | `rate_hourly` direkt, aber **verifizieren** |
| Stündliche Zahlung als 1/8 einer 8h-Rate | Hyperliquid [SUCHE] | Aufpassen, welche der beiden Zahlen die API liefert |
| Kontinuierlich / pro Sekunde | Paradex [DRITT] | Kein „Intervall" – Rate muss über ein Zeitfenster integriert werden |
| Festes Intervall, **je Symbol verschieden** | Aster: 4h oder 8h [DOC] | `fundingIntervalHours` je Symbol lesen, nie je Börse annehmen |
| Variabel, OI-basiert, 8h-Fenster | Variational [SUCHE] | nur Anzeige, kein Handel |

Daraus folgt eine Designentscheidung, die ich vor Phase 1 festhalten will:

> `FundingInfo` speichert **immer** die native Rate *und* das native Intervall *und* die
> normalisierte Stundenrate. Die Normalisierung passiert **einmal im Adapter**, nicht in
> der Engine, und jeder Adapter muss seine Normalisierung mit einem Test gegen von Hand
> gerechnete Referenzwerte belegen. Ein Adapter ohne diesen Test wird nicht registriert.

Zusätzlich schlage ich eine **empirische Selbstprüfung** vor, die dauerhaft mitläuft:
Der Adapter holt die Funding-Historie und misst den Abstand zwischen den Zeitstempeln. Passt
der gemessene Abstand nicht zum deklarierten Intervall, schlägt der Start fehl. Das fängt
genau den Fall ab, in dem eine Börse ihre Mechanik ändert und niemand es merkt.

---

## 4. Fazit: Welche zwei Börsen?

### Variational scheidet als handelndes Bein aus

Bestätigt: keine öffentliche Trading-API, nur eine Read-Only-Statistik-API ohne
Authentifizierung. [SUCHE] Damit gibt es keinen Weg, dort per Knopfdruck eine Order zu
platzieren.

### Die drei besten Kandidaten für das zweite Bein

**1. Lighter — mein erster Vorschlag**

* Der einzige Kandidat, bei dem `SymbolRules` **komplett aus einem einzigen öffentlichen
  Endpunkt** befüllbar ist (`orderBookDetails`: Gebühren, Mindestgrößen, Dezimalstellen,
  Margin-Fraktionen, Mark- und Index-Preis). Das macht den Adapter kurz und ehrlich.
* `positionFunding` liefert **einzelne, echte Funding-Zahlungen** mit Zeitstempel, Rate
  und Positionsgröße – Anforderung 6.4 ist damit sauber erfüllbar.
* `openapi.json` im Repo: wir können Modelle generieren statt abtippen, und Feldnamen
  lassen sich maschinell gegenprüfen.
* Sehr aktives SDK (Commit vor drei Tagen), Testnet vorhanden.
* Du verfolgst dort ohnehin schon die Funding-Raten – der geringste Lernaufwand für dich.
* Gegen Lighter: Orders laufen über signierte Transaktionen statt eines REST-Endpunkts,
  das Nonce-Handling (`nextNonce`) ist ein zusätzlicher Zustand, den der Adapter führen
  muss. Der WS-Wrapper des SDK deckt nur Orderbuch und Account ab.

**2. Paradex — der stärkste Ersatz**

* Technisch das sauberste Metadaten-Modell: `order_size_increment`, `price_tick_size`,
  `min_notional`, `max_order_size` – eins zu eins unser Interface.
* `funding_payments` als REST-Endpunkt **und** als WebSocket-Kanal; dazu
  `order.cancel_on_disconnect`, was unseren Kill Switch absichert.
* Aktivstes SDK von allen, Punkteprogramm läuft laut Drittquellen noch. [DRITT]
* Gegen Paradex: die sekündliche Funding-Berechnung ist der aufwendigste Fall für unsere
  Normalisierung, **und** die Rate speist sich aus einem Median, in dem Lighter und
  Hyperliquid enthalten sind. Ein Paar mit einer dieser Börsen erntet dann eine
  strukturell gedämpfte Differenz. Das will ich vor einer Entscheidung geklärt haben
  → [OFFEN-9].

**3. Hyperliquid — das verlässliche Gegenbein**

* Tiefste Liquidität, ausgereifteste API, beste Stream-Palette, `userFunding` für echte
  Zahlungen, `cloid` für Idempotenz.
* Gegen Hyperliquid: keine Tick-/Lot-Felder (Regel statt Metadaten), das SDK rechnet
  intern mit `float`, und das große Airdrop ist vorbei – als Punkte-Ziel also schwächer,
  obwohl als Ausführungsort erstklassig.

**Nicht empfohlen für den Start:** *edgeX*, weil Funding-Intervall und der Pfad zu echten
Funding-Zahlungen unbelegt sind – zwei Lücken in genau den Punkten, die dein Projekt
korrekt haben muss. *Aster*, weil es kein offizielles Python-SDK gibt und die
Einzahlungspflicht für V3-Endpunkte den Testnet-Durchlauf behindern könnte. Beide bleiben
gute Kandidaten für Phase 5+, wenn das Interface steht und ein dritter Adapter billig wird.

---

## 5. Empfehlung für den Startaufbau

**Handelndes Paar: Extended (Bein A) + Lighter (Bein B).**
**Lesendes Bein: Variational** mit `supports_trading = False`, allein für die
Vergleichsansicht.

Begründung:

* Beide haben ein aktiv gepflegtes offizielles Python-SDK, beide ein Testnet – du kannst
  Phase 4 also wirklich durchlaufen, statt sie zu überspringen.
* Beide sind für dich heute schon relevant (Extended handelst du bereits, Lighter
  beobachtest du).
* Die Funding-Mechaniken sind unterschiedlich genug, dass eine echte Differenz entsteht,
  aber keine der beiden Raten speist sich aus der anderen – anders als bei Paradex.
* Extended liefert Gebühren **je Account** über `/user/fees`; das zwingt uns von Anfang an
  zu ehrlichen Break-even-Zahlen statt Listenpreisen.

**Ausführungsreihenfolge im Paar** (deine Anforderung 6.3 verlangt eine feste, konfigurierte
Reihenfolge): Ich schlage **Lighter zuerst** vor, weil eine signierte Transaktion mit
Nonce-Verwaltung mehr schiefgehen kann als ein REST-POST, und das langsamere/fragilere Bein
laut deiner Vorgabe zuerst läuft. Das gehört aber in die Konfiguration, nicht in den Code –
und wir sollten es in Phase 4 mit echten Latenzmessungen überprüfen statt es zu glauben.

### Was sich ändert, sobald Variational die Trading-API öffnet

Wenn das Interface aus Abschnitt 5 deines Auftrags eingehalten wird, ist der Umbau klein:

1. Im Variational-Adapter `supports_trading` auf `True` setzen und die sechs
   Handels-/Account-Methoden implementieren. Die Marktdaten-Methoden stehen dann schon.
2. Symbol-Mapping-Tabelle um die handelbaren Variational-Ticker ergänzen.
3. Eine Zeile Konfiguration für die Ausführungsreihenfolge des neuen Paares.
4. Rate-Limiter-Profil eintragen (10/10 s pro IP ist streng – das ist der niedrigste Wert
   aller untersuchten Börsen und sollte den Polling-Takt der Vergleichsansicht bestimmen).
5. Die Funding-Normalisierung für Variational braucht besondere Sorgfalt wegen des
   OI-basierten Modells und der offenen Frage [OFFEN-2].

Was sich **nicht** ändert: Engine, Sizing, Preflight, Journal, UI. Genau dafür ist das
Adapter-Interface da. Wichtig ist deshalb, dass wir in Phase 1 **drei** Adapter bauen
(Extended, Lighter, Variational-read-only) und nicht zwei – der dritte beweist, dass das
Interface trägt, und der Read-Only-Fall zwingt uns früh dazu, `supports_trading` überall
sauber zu behandeln.

---

## 6. Was ich nicht belegen konnte – bitte entscheiden oder bestätigen

Ich rate hier bewusst nicht. Jede offene Frage mit Vorschlag, wie wir sie schließen.

| Nr. | Offene Frage | Warum es zählt | Vorschlag |
|---|---|---|---|
| **OFFEN-1** | Variational `/metadata/stats`: Enthält die Antwort wirklich Funding-Rate, OI je Seite, Spread und Quotes? | Ohne Funding-Feld ist Variational in der Vergleichsansicht wertlos. | Ein einziger `curl` von deinem Rechner, Antwort an mich. Dauert eine Minute und ersetzt jede Spekulation. |
| **OFFEN-2** | Variational: Ist die Stundenrate in den ersten 7 von 8 Stunden wirklich null? | Wenn ja, ist ein naiver Stundenvergleich 7/8 der Zeit falsch. | Wie OFFEN-1: Rate über 8 Stunden mitschreiben, dann sehen wir es. |
| **OFFEN-3** | Variational: Testnet, WebSocket, SDK? | Bestimmt, ob Variational je mehr als ein Anzeigebein wird. | Bei der Trading-API-Anmeldung mitfragen. |
| **OFFEN-4** | **Extended: Ist `funding_rate` die 1h- oder die 8h-Rate?** | **Die gefährlichste Annahme im Projekt.** Faktor 8 im Netto-APR. | `GET /info/<market>/funding` über 24 h abrufen und die Zeitstempel-Abstände messen. Stündliche Stempel = Stundenrate. Das baue ich in Phase 1 als Startprüfung ein. |
| **OFFEN-5** | Extended: konkrete Rate Limits? | Der Token-Bucket braucht eine Zahl. | Bis zur Klärung konservativ fahren (z. B. 300/min) und `429` sauber behandeln. Alternativ im Extended-Discord nachfragen. |
| **OFFEN-6** | **Extended: einzelne Funding-Zahlungen bei offener Position?** | Anforderung 6.4 verlangt echte Zahlungen, nicht Schätzungen. | Zwei Wege: **(a)** Du fragst im Extended-Discord nach einem Funding-Endpunkt, den das SDK nicht umschließt. **(b)** Wir akzeptieren für Extended eine Zwischenlösung: stündlicher Snapshot aus `/info/<market>/funding` × gehaltene Größe, **klar als „berechnet" statt „bestätigt" gekennzeichnet**, und beim Schließen gegen `realised_pnl_breakdown.funding_fees` abgeglichen. Ich würde (a) versuchen und (b) als Rückfall bauen. |
| **OFFEN-7** | Lighter: gibt es WS-Kanäle für Mark-Preis/Funding jenseits von `order_book` und `account_all`? | Sonst müssen wir Funding pollen statt streamen. | Polling als Standard einplanen, Stream als spätere Optimierung. |
| **OFFEN-8** | Lighter: Request-Rate-Limits? | Token-Bucket. | Wie OFFEN-5: konservativ starten, `429` beobachten. |
| **OFFEN-9** | **Paradex: Speist sich die Funding-Rate wirklich aus einem Median inkl. Lighter und Hyperliquid?** | Wenn ja, ist Paradex/Lighter als Paar strukturell schwächer. | Nur relevant, falls du Paradex statt Lighter willst. Dann kläre ich es zuerst. |
| **OFFEN-10** | edgeX: ist `https://testnet.edgex.exchange` die offizielle Testnet-URL? | Steht im SDK nur in Testkonfiguration. | Nur relevant, falls edgeX gewählt wird. |
| **OFFEN-11** | edgeX: Funding-Intervall? | Normalisierung unmöglich ohne diese Angabe. | dito |
| **OFFEN-12** | edgeX: Endpunkt für echte Funding-Zahlungen? | Anforderung 6.4. | dito |
| **OFFEN-13** | Aster: Blockiert die Einzahlungspflicht für V3 den Testnet-Durchlauf? | Phase 4 hinge daran. | Nur relevant, falls Aster gewählt wird. |
| ~~OFFEN-14~~ | **Beantwortet (Betreiber, 2026-09-18):** `rate` in `/api/v1/funding-rates` ist **Prozent pro Jahr**. | Die Umrechnung lautet damit `rate / 100 / 8760`. Ungerechnet übernommen wäre der APR um Faktor 876 000 zu hoch gewesen. | Umgesetzt als eigene Ratenkonvention `ANNUALIZED_PERCENT` (ADR-013). Die Plausibilitätsprüfung bleibt als Netz bestehen. Offen bleibt, ob das Feld `rate` in der **Historie** (`/api/v1/fundings`, Beispiel `0.0001`) derselben Konvention folgt – dort wird es nicht als Rate genutzt, nur die Zeitstempel. |
| **OFFEN-17** | **Gibt es bei Extended oder Lighter einen Endpunkt für die Serverzeit?** | Preflight-Prüfung 1 verlangt einen Zeitversatz unter einer Schwelle. Aster lehnt Orders mit mehr als 10 s Abweichung ab — das ist kein kosmetisches Problem. | In keinem der beiden SDKs auffindbar. Der Zeitversatz wird deshalb als unbekannt geführt und erzeugt eine Warnung statt eines stillen Bestehens (ADR-024). Möglicher Weg: der `Date`-Header der HTTP-Antwort. Ungenau, aber messbar — vor dem Einsatz gegen die echte API prüfen. |
| ~~OFFEN-16~~ | **Beantwortet in Phase 2 aus der `openapi.json`:** Lighters Kontodaten sind ohne Signatur lesbar. | Entscheidet, ob Phase 2 schon eine Signaturbibliothek braucht. | `GET /api/v1/account?by=index&value=<index>` verlangt keine Authentifizierung; bei `positionFunding` ist `authorization` optional. Für Phase 2 genügt der Account-Index (ADR-017). |
| ~~OFFEN-15~~ | **Beantwortet (Betreiber, 2026-09-18):** als Kennung dient das Ticker-Symbol, für Lighter also **`LIT`**. | Eine fremde Börsenrate als Lighter-Rate zu verbuchen wäre der teuerste denkbare Fehler. | `LIT` steht in der Kandidatenliste des Adapters, neben `lighter` und `zklighter`. Der harte Abbruch bleibt: passt keine Kennung, nennt die Fehlermeldung alle gefundenen – nie wird der erstbeste Treffer genommen. |

Zusätzlich zwei Angaben aus deinem Anhang, die ich **weder bestätigen noch widerlegen**
konnte und die deshalb nicht ungeprüft in Code wandern sollten:

* „Extended: Rate Limit ab 1000 Requests/Minute" → siehe OFFEN-5.
* „Lighter: bis zu 256 API-Keys je Account" → im SDK nicht belegt. Für uns ohnehin
  unkritisch, aber ich führe es nicht als Tatsache.

**Während der Umsetzung von Phase 1 neu aufgetaucht** waren OFFEN-14 und
OFFEN-15. Beide betrafen Lighter, beide hat der Betreiber am 2026-09-18
beantwortet: `rate` ist Prozent pro Jahr, und als Börsenkennung dient das
Ticker-Symbol `LIT`. Beides ist umgesetzt.

**Variational ist auf Entscheidung des Betreibers (2026-09-18) ganz aus dem
Projekt genommen** – auch als lesendes Bein. Die Punkte OFFEN-1 bis OFFEN-3
sind damit gegenstandslos und nur noch als Notiz für den Fall enthalten, dass
die Trading-API später doch öffnet.

Bestätigt aus deinem Anhang haben sich dagegen: sämtliche Extended-Endpunkte und
Base-URLs, das `X-Api-Key`-Verfahren, die Stark-Signatur, der Lighter-Installationspfad
über `zklighter-perps-python`, die Variational-Base-URL, deren Rate Limit von 10/10 s,
der Zinsanteil von 0,00125 %/h und der Deckel von 2 %/h.

---

## 7. Was ich in Phase 1 als Erstes bauen würde

Nur zur Einordnung – ich fange nichts an, bevor du freigibst.

1. **Zuerst die Tests**, wie von dir vorgegeben: Funding-Normalisierung gegen von Hand
   gerechnete Referenzwerte, für alle vier Mechaniken aus Abschnitt 3.
2. Adapter-Interface plus `MockAdapter`, damit die Engine nie gegen echte Börsen entwickelt
   wird.
3. Die drei Adapter im Read-Only-Betrieb, jeder mit der Intervall-Selbstprüfung aus
   Abschnitt 3 als Startbedingung.
4. `/api/funding` und `/api/opportunities`, danach Dashboard-Bereich A.

Eine Abweichung von deinem Auftrag schlage ich vor: `SymbolRules` sollte zusätzlich zu
`max_leverage` einen **positionswertabhängigen** Hebel abbilden können, weil Extended das
über `risk_factor_config` so ausliefert und Paradex über `position_limit`. Sonst müssten
wir die Information im Adapter wegwerfen und die Margin-Preflight-Prüfung würde bei großen
Notionals zu optimistisch rechnen.

---

## 8. Quellen

Offizielle SDKs und Doku-Repositories, in dieser Sitzung geklont und im Quelltext gelesen:

* Extended – https://github.com/x10xchange/python_sdk (Branch `starknet`)
* Lighter – https://github.com/elliottech/lighter-python
* Hyperliquid – https://github.com/hyperliquid-dex/hyperliquid-python-sdk
* Paradex – https://github.com/tradeparadex/paradex-py
* edgeX – https://github.com/edgex-Tech/edgex-python-sdk
* Aster – https://github.com/asterdex/api-docs

Offizielle Doku-Seiten (über Suchmaschinen-Zusammenfassung, nicht direkt abrufbar):

* Variational API – https://docs.variational.io/technical-documentation/api
* Variational Funding – https://docs.variational.io/omni/trading/funding-rates
* Variational Risk/Rate Limits – https://docs.variational.io/omni/trading/risk-limits-rate-limits
* Extended API – https://api.docs.extended.exchange/
* Extended Funding Payments – https://docs.extended.exchange/extended-resources/trading/funding-payments
* Extended Testnet – https://docs.extended.exchange/extended-resources/more/testnet
* Hyperliquid Funding – https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
* Lighter – https://docs.lighter.xyz/ , https://apidocs.lighter.xyz/docs/get-started
* Paradex Funding – https://docs.paradex.trade/risk/funding-mechanism
* edgeX – https://docs.edgex.exchange
* Aster – https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation
