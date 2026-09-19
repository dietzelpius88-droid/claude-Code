import { useState } from "react";
import type { PairLeg, PairRow } from "../api";
import { schliessePaar } from "../api";
import { apr, betrag, prozent, stunden, vorzeichenKlasse } from "../format";
import RebalanceDialog from "./RebalanceDialog";

const AMPEL: Record<string, string> = {
  GREEN: "bg-emerald-500",
  YELLOW: "bg-amber-500",
  RED: "bg-rose-500",
};

export default function Positions({
  paare,
  live,
  onAenderung,
}: {
  paare: PairRow[];
  live: boolean;
  onAenderung: () => void;
}) {
  const offen = paare.filter((p) => !["CLOSED", "FAILED"].includes(p.status));
  const geschlossen = paare.filter((p) => p.status === "CLOSED");

  return (
    <>
      <section className="p-4">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          Offene Paare
        </h2>

        {offen.length === 0 ? (
          <p className="text-xs text-slate-500">
            Keine offenen Paare. Ohne hinterlegte Zugangsdaten bleibt dieser Bereich leer –
            siehe README, Abschnitt „Schlüssel anlegen".
          </p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {offen.map((p) => (
              <PaarKarte key={p.id} paar={p} onAenderung={onAenderung} />
            ))}
          </div>
        )}
      </section>

      {geschlossen.length > 0 && <Ergebnisse paare={geschlossen} live={live} />}
    </>
  );
}

function PaarKarte({ paar, onAenderung }: { paar: PairRow; onAenderung: () => void }) {
  const [ausrichten, setAusrichten] = useState(false);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  async function schliessen() {
    setLaeuft(true);
    setFehler(null);
    try {
      await schliessePaar(paar.id);
      onAenderung();
    } catch (e) {
      setFehler((e as Error).message);
    } finally {
      setLaeuft(false);
    }
  }

  const deltaWarnung = Math.abs(Number(paar.net_delta_pct ?? 0)) >= 0.02;

  return (
    <article className="rounded border border-slate-800 bg-slate-950/60">
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
        <span className={`inline-block h-2 w-2 rounded-full ${AMPEL[paar.level ?? "RED"]}`} />
        <h3 className="text-sm font-semibold text-slate-200">{paar.symbol}</h3>

        {paar.source === "ADOPTED" && (
          <span
            className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400"
            title="Aus offenen Börsenpositionen übernommen, nicht über die Vorschau eröffnet"
          >
            übernommen
          </span>
        )}

        {paar.hedged === false && (
          <span className="rounded bg-rose-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
            NICHT GEHEDGT
          </span>
        )}

        {paar.positions_missing && (
          <span
            className="rounded bg-amber-600 px-1.5 py-0.5 text-[10px] font-bold text-black"
            title="Das Paar steht in der Datenbank, an der Börse ist aber nicht beides offen"
          >
            POSITION FEHLT
          </span>
        )}

        <div className="ml-auto flex items-center gap-3 text-[11px] text-slate-400">
          {paar.holding_hours && <span>gehalten {stunden(paar.holding_hours)}</span>}
          <span className={vorzeichenKlasse(paar.combined_pnl)}>
            PnL {betrag(paar.combined_pnl)} $
          </span>
        </div>
      </header>

      {paar.funding_negative && (
        <p className="border-b border-rose-900 bg-rose-950/50 px-3 py-1.5 text-[11px] text-rose-200">
          ⚠ Das Netto-Funding ist nicht mehr positiv – in dieser Richtung zahlt das Paar drauf.
        </p>
      )}

      <div className="divide-y divide-slate-800/70">
        {paar.legs.map((b) => (
          <BeinZeile key={b.venue} bein={b} />
        ))}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-slate-800 px-3 py-2 text-[11px] sm:grid-cols-4">
        <Kennzahl
          label="Netto-Funding"
          wert={paar.net_funding_apr ? apr(paar.net_funding_apr) : "–"}
          zusatz={paar.net_funding_hourly ? `${prozent(paar.net_funding_hourly)}/h` : undefined}
          klasse={vorzeichenKlasse(paar.net_funding_apr)}
        />
        <Kennzahl
          label="Funding erhalten"
          wert={`${betrag(paar.funding_received)} $`}
          klasse={vorzeichenKlasse(paar.funding_received)}
        />
        <Kennzahl
          label="Gebühren"
          wert={paar.fees_known ? `${betrag(paar.fees_paid)} $` : "unbekannt"}
          zusatz={
            paar.costs_recovered === false
              ? "noch nicht eingespielt"
              : paar.costs_recovered === true
                ? "eingespielt"
                : undefined
          }
          klasse={paar.costs_recovered === false ? "text-amber-400" : undefined}
        />
        <Kennzahl
          label="Rest-Delta"
          wert={`${betrag(paar.net_delta_usd)} $`}
          zusatz={prozent(paar.net_delta_pct, 2)}
          klasse={deltaWarnung ? "text-amber-400" : undefined}
        />
      </div>

      {fehler && <p className="px-3 pb-2 text-[11px] text-rose-300">{fehler}</p>}

      <footer className="flex gap-2 border-t border-slate-800 px-3 py-2">
        <button
          onClick={schliessen}
          disabled={laeuft}
          className="rounded bg-slate-700 px-3 py-1 text-xs font-medium text-slate-100 hover:bg-slate-600 disabled:opacity-50"
        >
          {laeuft ? "…" : "Schließen"}
        </button>
        <button
          onClick={() => setAusrichten(true)}
          disabled={laeuft}
          className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50"
        >
          Neu ausrichten
        </button>
      </footer>

      {ausrichten && (
        <RebalanceDialog
          pairId={paar.id}
          symbol={paar.symbol}
          onSchliessen={() => setAusrichten(false)}
          onFertig={onAenderung}
        />
      )}
    </article>
  );
}

function BeinZeile({ bein }: { bein: PairLeg }) {
  const abstand = bein.liquidation_distance;
  const breite = abstand ? Math.min(100, Number(abstand) * 100 * 2) : 0;

  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className={`font-semibold ${bein.side === "LONG" ? "text-emerald-400" : "text-rose-400"}`}
        >
          {bein.side === "LONG" ? "L" : "S"}
        </span>
        <span className="text-slate-300">{bein.venue}</span>
        <span className="text-slate-500">{betrag(bein.filled_size, 6)}</span>
        {bein.entry_price && bein.mark_price && (
          <span className="text-slate-500">
            @ {betrag(bein.entry_price)} → {betrag(bein.mark_price)}
          </span>
        )}
        {bein.unrealised_pnl && (
          <span className={`ml-auto ${vorzeichenKlasse(bein.unrealised_pnl)}`}>
            {betrag(bein.unrealised_pnl)} $
          </span>
        )}
      </div>

      <div className="mt-1.5 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded bg-slate-800">
          <div className={`h-full ${AMPEL[bein.health ?? "RED"]}`} style={{ width: `${breite}%` }} />
        </div>
        <span className="w-28 text-right text-[10px] text-slate-500">
          {abstand ? `${prozent(abstand, 1)} bis Liq.` : "Liq. unbekannt"}
        </span>
      </div>
    </div>
  );
}

// Auftrag 6.5: je abgeschlossenem Paar eine Ergebniszeile, damit sich die
// Kosten pro Airdrop-Punkt nachrechnen lassen.
function Ergebnisse({ paare, live }: { paare: PairRow[]; live: boolean }) {
  return (
    <section className="p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Abgeschlossene Paare
      </h2>
      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-slate-900 text-slate-400">
              <th className="px-3 py-2 text-left font-medium">Symbol</th>
              <th className="px-3 py-2 text-right font-medium">Funding</th>
              <th className="px-3 py-2 text-right font-medium">Gebühren</th>
              <th className="px-3 py-2 text-right font-medium">Preis-PnL</th>
              <th className="px-3 py-2 text-right font-medium">Netto</th>
              <th className="px-3 py-2 text-right font-medium">Gehalten</th>
              <th className="px-3 py-2 text-right font-medium">Realisierte APR</th>
            </tr>
          </thead>
          <tbody>
            {paare.map((p) => (
              <tr key={p.id} className="border-t border-slate-800">
                <td className="px-3 py-1.5 text-slate-200">{p.symbol}</td>
                <td className={`px-3 py-1.5 text-right ${vorzeichenKlasse(p.funding_received)}`}>
                  {betrag(p.funding_received)} $
                </td>
                <td className="px-3 py-1.5 text-right text-slate-400">
                  {betrag(p.fees_paid)} $
                </td>
                <td className={`px-3 py-1.5 text-right ${vorzeichenKlasse(p.price_pnl)}`}>
                  {betrag(p.price_pnl)} $
                </td>
                <td
                  className={`px-3 py-1.5 text-right font-semibold ${vorzeichenKlasse(p.net_result)}`}
                >
                  {betrag(p.net_result)} $
                </td>
                <td className="px-3 py-1.5 text-right text-slate-400">
                  {stunden(p.holding_hours)}
                </td>
                <td className={`px-3 py-1.5 text-right ${vorzeichenKlasse(p.realized_apr)}`}>
                  {p.realized_apr ? apr(p.realized_apr) : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-600">
        Realisierte APR bezogen auf das Notional, nicht auf die hinterlegte Margin.
      </p>
      {!live && (
        <p className="mt-1 text-[11px] text-amber-600/80">
          Im Trockenlauf bleibt die Position an der Börse bestehen – sie taucht deshalb
          gleich wieder als übernommenes Paar auf. Erst im Live-Modus wird wirklich
          glattgestellt.
        </p>
      )}
    </section>
  );
}

function Kennzahl({
  label,
  wert,
  zusatz,
  klasse,
}: {
  label: string;
  wert: string;
  zusatz?: string;
  klasse?: string;
}) {
  return (
    <div>
      <div className="text-slate-500">{label}</div>
      <div className={klasse ?? "text-slate-300"}>
        {wert}
        {zusatz && <span className="ml-1 text-slate-500">({zusatz})</span>}
      </div>
    </div>
  );
}
