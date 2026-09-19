import { useState } from "react";
import { killSwitch } from "../api";

// Eine einzige Rueckfrage, dann wird alles storniert und geschlossen.
export default function KillSwitch({ onFertig }: { onFertig: () => void }) {
  const [frage, setFrage] = useState(false);
  const [laeuft, setLaeuft] = useState(false);
  const [meldung, setMeldung] = useState<string | null>(null);

  async function ausloesen() {
    setLaeuft(true);
    try {
      const e = await killSwitch();
      setMeldung(
        `${e.cancelled_orders} Orders storniert, ${e.closed_positions} Positionen geschlossen` +
          (e.errors.length ? ` — ${e.errors.length} Fehler` : ""),
      );
      onFertig();
    } catch (e) {
      setMeldung(`Fehlgeschlagen: ${(e as Error).message}`);
    } finally {
      setLaeuft(false);
      setFrage(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setFrage(true)}
        className="rounded border border-rose-700 px-2 py-0.5 text-xs font-bold tracking-wider text-rose-300 hover:bg-rose-900 hover:text-rose-100"
        title="Alle offenen Orders stornieren und alle Positionen schließen"
      >
        KILL
      </button>

      {frage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded border border-rose-700 bg-slate-950 p-4">
            <h3 className="text-sm font-semibold text-rose-200">Kill Switch auslösen?</h3>
            <p className="mt-2 text-xs text-slate-300">
              Alle offenen Orders werden storniert und alle Positionen auf beiden Börsen per
              Market geschlossen. Das lässt sich nicht rückgängig machen.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setFrage(false)}
                className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300"
              >
                Abbrechen
              </button>
              <button
                onClick={ausloesen}
                disabled={laeuft}
                className="rounded bg-rose-600 px-3 py-1 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
              >
                {laeuft ? "läuft…" : "Ja, alles schließen"}
              </button>
            </div>
          </div>
        </div>
      )}

      {meldung && (
        <span className="text-[11px] text-slate-400" title={meldung}>
          {meldung}
        </span>
      )}
    </>
  );
}
