import { useState } from "react";
import type { PairRow } from "../api";
import { loeseAuf } from "../api";

// Nicht wegklickbar. Ein einseitig gefuelltes Paar ist eine ungesicherte
// Richtungswette - der Alarm verschwindet erst, wenn er aufgeloest ist.
export default function UnhedgedAlarm({
  paare,
  onAufgeloest,
}: {
  paare: PairRow[];
  onAufgeloest: () => void;
}) {
  const betroffen = paare.filter((p) => p.status === "UNHEDGED");
  if (betroffen.length === 0) return null;

  return (
    <div className="border-b-2 border-rose-500 bg-rose-950">
      {betroffen.map((p) => (
        <AlarmZeile key={p.id} paar={p} onAufgeloest={onAufgeloest} />
      ))}
    </div>
  );
}

function AlarmZeile({ paar, onAufgeloest }: { paar: PairRow; onAufgeloest: () => void }) {
  const [laeuft, setLaeuft] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  async function handeln(action: "hedge" | "rollback") {
    setLaeuft(action);
    setFehler(null);
    try {
      await loeseAuf(paar.id, action);
      onAufgeloest();
    } catch (e) {
      setFehler((e as Error).message);
    } finally {
      setLaeuft(null);
    }
  }

  const gefuellt = paar.legs.filter((b) => Number(b.filled_size ?? 0) > 0);

  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="animate-pulse rounded bg-rose-500 px-2 py-1 text-xs font-bold tracking-wider text-white">
          UNGESICHERT
        </span>
        <span className="text-sm font-semibold text-rose-100">{paar.symbol}</span>
        <span className="text-xs text-rose-200">
          {gefuellt.map((b) => `${b.venue} ${b.side} ${b.filled_size}`).join(" · ") ||
            "kein Bein gefüllt"}
          {" — die Gegenseite fehlt. Das ist eine ungesicherte Richtungswette."}
        </span>

        <div className="ml-auto flex gap-2">
          <button
            onClick={() => handeln("hedge")}
            disabled={laeuft !== null}
            className="rounded bg-emerald-700 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
          >
            {laeuft === "hedge" ? "…" : "Gegenseite nachziehen"}
          </button>
          <button
            onClick={() => handeln("rollback")}
            disabled={laeuft !== null}
            className="rounded bg-slate-700 px-3 py-1 text-xs font-medium text-white hover:bg-slate-600 disabled:opacity-50"
          >
            {laeuft === "rollback" ? "…" : "Erstes Bein schließen"}
          </button>
        </div>
      </div>

      {fehler && <p className="mt-2 text-xs text-rose-300">Fehlgeschlagen: {fehler}</p>}
    </div>
  );
}
