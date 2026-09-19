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
