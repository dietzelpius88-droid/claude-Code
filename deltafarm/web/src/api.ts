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
