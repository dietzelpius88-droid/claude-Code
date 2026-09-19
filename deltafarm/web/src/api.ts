// Typen und Abrufe. Zahlen kommen als String vom Backend und werden nur fuer
// die Anzeige in Number gewandelt - nie fuer Rechnungen.

export type Rate = {
  rate_hourly: string;
  apr: string;
  native_rate: string;
  native_convention: string;
  native_interval_hours: string | null;
  next_funding_time: string | null;
  as_of: string;
};

export type BestPair = {
  long_venue: string;
  short_venue: string;
  net_rate_hourly: string;
  net_apr: string;
  breakeven_hours: string | null;
  cost_basis_known: boolean;
  price_deviation: string | null;
  // true = Preise bestaetigen dasselbe Asset, false = widerlegen es,
  // null = keine Aussage moeglich.
  asset_match: boolean | null;
};

export type FundingRow = {
  symbol: string;
  rates: Record<string, Rate>;
  best: BestPair | null;
};

export type FundingMatrix = {
  as_of: string;
  venues: string[];
  rows: FundingRow[];
  warnings: string[];
};

export type Venue = {
  name: string;
  supports_trading: boolean;
  reachable: boolean;
  environment: string;
  detail: string | null;
};

export type Health = {
  status: string;
  dry_run: boolean;
  environment: string;
  version: string;
};

async function hole<T>(pfad: string): Promise<T> {
  const antwort = await fetch(pfad);
  if (!antwort.ok) {
    throw new Error(`${pfad}: ${antwort.status} ${antwort.statusText}`);
  }
  return (await antwort.json()) as T;
}

export const holeFunding = () => hole<FundingMatrix>("/api/funding");
export const holeVenues = () => hole<Venue[]>("/api/venues");
export const holeHealth = () => hole<Health>("/api/health");

// --- Phase 2 --------------------------------------------------------------

export type BalanceRow = {
  venue: string;
  collateral: string;
  equity: string;
  available: string;
  unrealised_pnl: string;
  initial_margin: string;
  margin_usage: string | null;
  as_of: string;
};

// Ampel eines einzelnen Beins - nicht zu verwechseln mit Health, dem Status
// der Anwendung selbst.
export type LegHealth = {
  liquidation_distance: string | null;
  margin_usage: string | null;
  level: "GREEN" | "YELLOW" | "RED";
  detail: string | null;
};

export type Leg = {
  venue: string;
  symbol: string;
  side: "LONG" | "SHORT";
  size: string;
  entry_price: string;
  mark_price: string;
  notional: string;
  unrealised_pnl: string;
  liquidation_price: string | null;
  funding_paid: string | null;
  funding_received: string | null;
  health: LegHealth;
};

export type DetectedPair = {
  symbol: string;
  legs: Leg[];
  net_delta_usd: string;
  net_delta_pct: string;
  combined_pnl: string;
  funding_received: string;
  hedged: boolean;
  level: "GREEN" | "YELLOW" | "RED";
  holding_hours: string | null;
};

export type JournalEntry = {
  timestamp: string;
  kind: string;
  venue: string | null;
  symbol: string | null;
  amount: string | null;
  confirmed: boolean | null;
  message: string;
};

export const holeBalances = () => hole<BalanceRow[]>("/api/balances");
export const holePositionen = () => hole<DetectedPair[]>("/api/positions");
export const holeJournal = () => hole<JournalEntry[]>("/api/journal");

// --- Phase 3 --------------------------------------------------------------

export type Check = {
  key: string;
  label: string;
  status: "PASS" | "WARN" | "FAIL";
  detail: string;
};

export type SizingOut = {
  size: string;
  lot_size: string;
  long_venue: string;
  short_venue: string;
  long_mark: string;
  short_mark: string;
  long_notional: string;
  short_notional: string;
  residual_delta_usd: string;
  residual_delta_pct: string;
  estimated_open_fees: string | null;
  estimated_round_trip_fees: string | null;
};

export type PreviewOut = {
  token: string | null;
  ok: boolean;
  sizing: SizingOut | null;
  checks: Check[];
  net_rate_hourly: string | null;
  net_apr: string | null;
  breakeven_hours: string | null;
  blocking_reasons: string[];
  error: string | null;
  valid_for_seconds: number;
};

export type ExecutionOut = {
  pair_id: number;
  state: string;
  detail: string;
  dry_run: boolean;
};

export type PairRow = {
  id: number;
  symbol: string;
  long_venue: string;
  short_venue: string;
  status: string;
  notional_usd: string | null;
  opened_at: string | null;
  closed_at: string | null;
  legs: Array<{
    venue: string;
    side: string;
    target_size: string | null;
    filled_size: string | null;
    avg_price: string | null;
    status: string;
  }>;
};

export type PanicOut = {
  cancelled_orders: number;
  closed_positions: number;
  errors: string[];
  dry_run: boolean;
};

async function sende<T>(pfad: string, koerper?: unknown): Promise<T> {
  const antwort = await fetch(pfad, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: koerper === undefined ? undefined : JSON.stringify(koerper),
  });
  const daten = await antwort.json().catch(() => ({}));
  if (!antwort.ok) {
    throw new Error((daten as { detail?: string }).detail ?? `${antwort.status} ${antwort.statusText}`);
  }
  return daten as T;
}

export type PreviewEingabe = {
  symbol: string;
  notional_usd: string;
  long_venue: string;
  short_venue: string;
  max_slippage: string;
  long_leverage: string;
  short_leverage: string;
  first_venue: string;
  hedge_timeout_seconds: number;
  auto_rollback: boolean;
};

export const holePairs = () => hole<PairRow[]>("/api/pairs");
export const erstelleVorschau = (e: PreviewEingabe) => sende<PreviewOut>("/api/pairs/preview", e);
export const fuehreAus = (token: string) => sende<ExecutionOut>("/api/pairs/open", { token });
export const schliessePaar = (id: number) => sende<ExecutionOut>(`/api/pairs/${id}/close`);
export const loeseAuf = (id: number, action: "hedge" | "rollback") =>
  sende<ExecutionOut>(`/api/pairs/${id}/resolve`, { action });
export const killSwitch = () => sende<PanicOut>("/api/panic");
