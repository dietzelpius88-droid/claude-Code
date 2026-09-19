import { useEffect, useState } from "react";
import type { RebalanceLeg, RebalancePlan } from "../api";
import { holeAusrichtung, richteAus } from "../api";
import { betrag } from "../format";

// Beide Wege werden durchgerechnet, entschieden wird von Hand: Aufstocken
// braucht Margin und bringt mehr Volumen, Verkleinern gibt Margin frei und
// senkt das Open Interest.
export default function RebalanceDialog({
  pairId,
  symbol,
  onSchliessen,
  onFertig,
}: {
  pairId: number;
  symbol: string;
  onSchliessen: () => void;
  onFertig: () => void;
}) {
  const [plan, setPlan] = useState<RebalancePlan | null>(null);
  const [laeuft, setLaeuft] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  useEffect(() => {
    holeAusrichtung(pairId)
      .then(setPlan)
      .catch((e) => setFehler((e as Error).message));
  }, [pairId]);

  async function ausfuehren(action: "increase" | "decrease") {
    setLaeuft(action);
    setFehler(null);
    try {
      await richteAus(pairId, action);
      onFertig();
      onSchliessen();
    } catch (e) {
      setFehler((e as Error).message);
    } finally {
      setLaeuft(null);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/70 p-4">
      <div className="mt-16 w-full max-w-xl rounded border border-slate-700 bg-slate-950">
        <header className="flex items-center gap-2 border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-100">Neu ausrichten — {symbol}</h3>
          <button
            onClick={onSchliessen}
            className="ml-auto text-xs text-slate-500 hover:text-slate-300"
          >
            schließen
          </button>
        </header>

        {fehler && (
          <p className="mx-4 mt-3 rounded border border-rose-800 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
            {fehler}
          </p>
        )}

        {!plan && !fehler && <p className="p-4 text-xs text-slate-500">Wird berechnet…</p>}

        {plan?.error && <p className="p-4 text-xs text-amber-300">{plan.error}</p>}

        {plan && !plan.error && plan.balanced && (
          <p className="p-4 text-xs text-slate-400">
            Die Beine sind bereits gleich groß — es gibt nichts auszurichten.
          </p>
        )}

        {plan && !plan.balanced && (
          <>
            <p className="px-4 pt-3 text-xs text-slate-400">
              Differenz: <span className="text-slate-200">{betrag(plan.difference, 6)}</span>
            </p>
            <div className="grid gap-3 p-4 sm:grid-cols-2">
              <Weg
                titel="Aufstocken"
                erlaeuterung="Position wächst — mehr Volumen und Open Interest für die Punkte, braucht zusätzliche Margin."
                bein={plan.increase}
                laeuft={laeuft === "increase"}
                gesperrt={laeuft !== null}
                onKlick={() => ausfuehren("increase")}
              />
              <Weg
                titel="Verkleinern"
                erlaeuterung="Position schrumpft — Margin wird frei, kostet aber Open Interest."
                bein={plan.decrease}
                laeuft={laeuft === "decrease"}
                gesperrt={laeuft !== null}
                onKlick={() => ausfuehren("decrease")}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Weg({
  titel,
  erlaeuterung,
  bein,
  laeuft,
  gesperrt,
  onKlick,
}: {
  titel: string;
  erlaeuterung: string;
  bein: RebalanceLeg | null;
  laeuft: boolean;
  gesperrt: boolean;
  onKlick: () => void;
}) {
  if (!bein) return null;
  return (
    <div className="rounded border border-slate-800 p-3">
      <h4 className="text-xs font-semibold text-slate-200">{titel}</h4>
      <p className="mt-1 text-[11px] text-slate-500">{erlaeuterung}</p>

      <dl className="mt-2 space-y-1 text-xs">
        <Zeile label="Börse" wert={bein.venue} />
        <Zeile label="Order" wert={`${bein.side} ${betrag(bein.size, 6)}`} />
        <Zeile
          label="Gebühr"
          wert={bein.estimated_fee ? `${betrag(bein.estimated_fee, 4)} $` : "unbekannt"}
        />
        <Zeile label="Danach" wert={`${betrag(bein.resulting_size, 6)}`} />
        <Zeile label="Notional" wert={`${betrag(bein.resulting_notional)} $`} />
      </dl>

      <button
        onClick={onKlick}
        disabled={gesperrt}
        className="mt-3 w-full rounded bg-slate-700 px-3 py-1.5 text-xs font-medium text-slate-100 hover:bg-slate-600 disabled:opacity-50"
      >
        {laeuft ? "…" : titel}
      </button>
    </div>
  );
}

function Zeile({ label, wert }: { label: string; wert: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-200">{wert}</dd>
    </div>
  );
}
