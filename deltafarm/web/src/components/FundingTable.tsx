import { Fragment, useMemo, useState } from "react";
import type { FundingMatrix } from "../api";
import { apr, prozent, stunden, uhrzeit, vorzeichenKlasse } from "../format";

type SortKey = "net" | "symbol";

export default function FundingTable({ daten }: { daten: FundingMatrix }) {
  const [sortierung, setSortierung] = useState<SortKey>("net");
  const [filter, setFilter] = useState("");

  const zeilen = useMemo(() => {
    const gefiltert = daten.rows.filter((z) =>
      z.symbol.toLowerCase().includes(filter.trim().toLowerCase()),
    );
    return [...gefiltert].sort((a, b) => {
      if (sortierung === "symbol") return a.symbol.localeCompare(b.symbol);
      const av = a.best ? Number(a.best.net_apr) : -Infinity;
      const bv = b.best ? Number(b.best.net_apr) : -Infinity;
      return bv - av;
    });
  }, [daten.rows, filter, sortierung]);

  return (
    <section className="p-4">
      <div className="mb-2 flex items-center gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Funding-Vergleich
        </h2>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Symbol filtern…"
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-slate-500 focus:outline-none"
        />
        <span className="text-xs text-slate-500">
          {zeilen.length} von {daten.rows.length}
        </span>
      </div>

      {daten.warnings.length > 0 && (
        <div className="mb-3 rounded border border-amber-700 bg-amber-950/60 px-3 py-2 text-xs text-amber-200">
          {daten.warnings.map((w, i) => (
            <p key={i}>⚠ {w}</p>
          ))}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-slate-900 text-slate-400">
              <Th onClick={() => setSortierung("symbol")} aktiv={sortierung === "symbol"}>
                Symbol
              </Th>
              {daten.venues.map((v) => (
                <th key={v} className="px-3 py-2 text-center font-medium" colSpan={2}>
                  {v}
                </th>
              ))}
              <Th onClick={() => setSortierung("net")} aktiv={sortierung === "net"} rechts>
                Netto-APR
              </Th>
              <th className="px-3 py-2 text-left font-medium">Richtung</th>
              <th className="px-3 py-2 text-right font-medium">Break-even</th>
            </tr>
            <tr className="bg-slate-900/60 text-[10px] text-slate-500">
              <th />
              {daten.venues.map((v) => (
                <Fragment key={v}>
                  <th className="px-3 pb-1 text-right font-normal">1 h</th>
                  <th className="px-3 pb-1 text-right font-normal">APR</th>
                </Fragment>
              ))}
              <th />
              <th />
              <th />
            </tr>
          </thead>
          <tbody>
            {zeilen.map((z) => (
              <tr key={z.symbol} className="border-t border-slate-800 hover:bg-slate-900/40">
                <td className="px-3 py-1.5 font-medium text-slate-200">{z.symbol}</td>

                {daten.venues.map((v) => {
                  const r = z.rates[v];
                  return (
                    <Fragment key={v}>
                      <td
                        className={`px-3 py-1.5 text-right ${vorzeichenKlasse(r?.rate_hourly)}`}
                        title={r ? nativHinweis(r) : "keine Daten"}
                      >
                        {r ? prozent(r.rate_hourly) : "–"}
                      </td>
                      <td className={`px-3 py-1.5 text-right ${vorzeichenKlasse(r?.apr)}`}>
                        {r ? apr(r.apr) : "–"}
                      </td>
                    </Fragment>
                  );
                })}

                <td
                  className={`px-3 py-1.5 text-right font-semibold ${vorzeichenKlasse(
                    z.best?.net_apr,
                  )}`}
                >
                  {z.best ? apr(z.best.net_apr) : "–"}
                </td>

                <td className="px-3 py-1.5 text-slate-300">
                  {z.best ? (
                    <span>
                      <span className="text-emerald-400">L</span> {z.best.long_venue}
                      <span className="mx-1 text-slate-600">/</span>
                      <span className="text-rose-400">S</span> {z.best.short_venue}
                    </span>
                  ) : (
                    <span className="text-slate-600">nur eine Boerse</span>
                  )}
                </td>

                <td className="px-3 py-1.5 text-right text-slate-300">
                  {z.best?.cost_basis_known ? (
                    stunden(z.best.breakeven_hours)
                  ) : (
                    <span
                      className="text-slate-600"
                      title="Gebuehren noch nicht bekannt - Extended liefert sie erst mit Account-Zugang (Phase 2)"
                    >
                      Gebühren fehlen
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {zeilen.length === 0 && (
        <p className="mt-3 text-xs text-slate-500">
          Keine Zeilen. Entweder filtert der Suchbegriff alles weg, oder keine Börse ist erreichbar.
        </p>
      )}
    </section>
  );
}

// Was die Boerse tatsaechlich geliefert hat - damit nachvollziehbar bleibt,
// woraus die angezeigte Stundenrate gerechnet wurde.
function nativHinweis(r: {
  native_rate: string;
  native_convention: string;
  native_interval_hours: string | null;
  as_of: string;
}): string {
  const form =
    r.native_convention === "annualized_percent"
      ? `${r.native_rate} % p. a.`
      : `${r.native_rate} je ${r.native_interval_hours ?? "?"} h`;
  return `nativ: ${form} · Zahlung alle ${r.native_interval_hours ?? "?"} h · Stand ${uhrzeit(r.as_of)}`;
}

function Th({
  children,
  onClick,
  aktiv,
  rechts,
}: {
  children: React.ReactNode;
  onClick: () => void;
  aktiv: boolean;
  rechts?: boolean;
}) {
  return (
    <th
      onClick={onClick}
      className={`cursor-pointer px-3 py-2 font-medium select-none ${
        rechts ? "text-right" : "text-left"
      } ${aktiv ? "text-slate-200" : ""}`}
    >
      {children}
      {aktiv && <span className="ml-1 text-slate-600">▾</span>}
    </th>
  );
}
