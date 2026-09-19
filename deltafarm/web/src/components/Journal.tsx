import { useMemo, useState } from "react";
import type { JournalEntry } from "../api";
import { betrag, vorzeichenKlasse } from "../format";

export default function Journal({ eintraege }: { eintraege: JournalEntry[] }) {
  const [filter, setFilter] = useState("");
  const [nurFunding, setNurFunding] = useState(false);

  const gefiltert = useMemo(() => {
    const suche = filter.trim().toLowerCase();
    return eintraege
      .filter((e) => (nurFunding ? e.kind === "funding" : true))
      .filter((e) =>
        suche
          ? [e.kind, e.venue, e.symbol, e.message]
              .filter(Boolean)
              .some((t) => String(t).toLowerCase().includes(suche))
          : true,
      )
      .slice()
      .reverse(); // neueste zuerst
  }, [eintraege, filter, nurFunding]);

  return (
    <section className="p-4">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Journal</h2>

        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Suchen…"
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-slate-500 focus:outline-none"
        />

        <label className="flex items-center gap-1.5 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={nurFunding}
            onChange={(e) => setNurFunding(e.target.checked)}
            className="accent-emerald-500"
          />
          nur Funding
        </label>

        <span className="text-xs text-slate-500">
          {gefiltert.length} von {eintraege.length}
        </span>

        {/* Der Export geht direkt gegen das Backend, damit die CSV-Datei
            unveraendert aus dem Journal kommt und nicht im Browser entsteht. */}
        <a
          href="/api/journal?format=csv"
          className="ml-auto rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-slate-500 hover:text-slate-100"
        >
          CSV herunterladen
        </a>
      </div>

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-slate-900 text-slate-400">
              <th className="px-3 py-2 text-left font-medium">Zeitpunkt</th>
              <th className="px-3 py-2 text-left font-medium">Art</th>
              <th className="px-3 py-2 text-left font-medium">Börse</th>
              <th className="px-3 py-2 text-left font-medium">Symbol</th>
              <th className="px-3 py-2 text-right font-medium">Betrag</th>
              <th className="px-3 py-2 text-left font-medium">Beschreibung</th>
            </tr>
          </thead>
          <tbody>
            {gefiltert.map((e, i) => (
              <tr key={`${e.timestamp}-${i}`} className="border-t border-slate-800">
                <td className="whitespace-nowrap px-3 py-1.5 text-slate-400">
                  {new Date(e.timestamp).toLocaleString("de-DE")}
                </td>
                <td className="px-3 py-1.5 text-slate-300">{e.kind}</td>
                <td className="px-3 py-1.5 text-slate-400">{e.venue ?? "–"}</td>
                <td className="px-3 py-1.5 text-slate-400">{e.symbol ?? "–"}</td>
                <td className={`px-3 py-1.5 text-right ${vorzeichenKlasse(e.amount)}`}>
                  {e.amount ? `${betrag(e.amount, 4)} $` : "–"}
                </td>
                <td className="px-3 py-1.5 text-slate-300">
                  {e.message}
                  {e.confirmed === false && (
                    <span
                      className="ml-1.5 rounded bg-amber-900/70 px-1 py-0.5 text-[10px] text-amber-200"
                      title="Aus Rate und Größe gerechnet, nicht von der Börse bestätigt"
                    >
                      gerechnet
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {gefiltert.length === 0 && (
        <p className="mt-3 text-xs text-slate-500">Keine Einträge.</p>
      )}
    </section>
  );
}
