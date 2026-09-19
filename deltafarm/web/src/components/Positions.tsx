import type { DetectedPair, Leg } from "../api";
import { betrag, prozent, stunden, vorzeichenKlasse } from "../format";

const AMPEL: Record<string, string> = {
  GREEN: "bg-emerald-500",
  YELLOW: "bg-amber-500",
  RED: "bg-rose-500",
};

export default function Positions({ paare }: { paare: DetectedPair[] }) {
  return (
    <section className="p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Offene Positionen
      </h2>

      {paare.length === 0 ? (
        <p className="text-xs text-slate-500">
          Keine offenen Positionen. Ohne hinterlegte Zugangsdaten bleibt dieser Bereich leer –
          siehe README, Abschnitt „Schlüssel anlegen".
        </p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {paare.map((p) => (
            <PaarKarte key={p.symbol} paar={p} />
          ))}
        </div>
      )}
    </section>
  );
}

function PaarKarte({ paar }: { paar: DetectedPair }) {
  return (
    <article className="rounded border border-slate-800 bg-slate-950/60">
      <header className="flex items-center gap-2 border-b border-slate-800 px-3 py-2">
        <span className={`inline-block h-2 w-2 rounded-full ${AMPEL[paar.level]}`} />
        <h3 className="text-sm font-semibold text-slate-200">{paar.symbol}</h3>

        {!paar.hedged && (
          <span
            className="rounded bg-rose-600 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-white"
            title="Nur ein Bein offen – das ist eine ungesicherte Richtungswette, keine delta-neutrale Position."
          >
            NICHT GEHEDGT
          </span>
        )}

        <div className="ml-auto flex items-center gap-3 text-[11px] text-slate-400">
          {paar.holding_hours && <span>gehalten {stunden(paar.holding_hours)}</span>}
          <span className={vorzeichenKlasse(paar.combined_pnl)}>
            PnL {betrag(paar.combined_pnl)} $
          </span>
        </div>
      </header>

      <div className="divide-y divide-slate-800/70">
        {paar.legs.map((b) => (
          <BeinZeile key={b.venue} bein={b} />
        ))}
      </div>

      <footer className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-slate-800 px-3 py-2 text-[11px] sm:grid-cols-3">
        <Kennzahl
          label="Rest-Delta"
          wert={`${betrag(paar.net_delta_usd)} $`}
          zusatz={prozent(paar.net_delta_pct, 2)}
          klasse={Math.abs(Number(paar.net_delta_pct)) >= 0.02 ? "text-amber-400" : "text-slate-300"}
        />
        <Kennzahl
          label="Funding erhalten"
          wert={`${betrag(paar.funding_received)} $`}
          klasse={vorzeichenKlasse(paar.funding_received)}
        />
        <Kennzahl label="Beine" wert={`${paar.legs.length}`} />
      </footer>
    </article>
  );
}

function BeinZeile({ bein }: { bein: Leg }) {
  const abstand = bein.health.liquidation_distance;
  const breite = abstand ? Math.min(100, Number(abstand) * 100 * 2) : 0;

  return (
    <div className="px-3 py-2">
      <div className="flex items-center gap-2 text-xs">
        <span
          className={`font-semibold ${bein.side === "LONG" ? "text-emerald-400" : "text-rose-400"}`}
        >
          {bein.side === "LONG" ? "L" : "S"}
        </span>
        <span className="text-slate-300">{bein.venue}</span>
        <span className="text-slate-500">{betrag(bein.size, 4)}</span>
        <span className="text-slate-500">
          @ {betrag(bein.entry_price)} → {betrag(bein.mark_price)}
        </span>
        <span className={`ml-auto ${vorzeichenKlasse(bein.unrealised_pnl)}`}>
          {betrag(bein.unrealised_pnl)} $
        </span>
      </div>

      <div className="mt-1.5 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded bg-slate-800">
          <div
            className={`h-full ${AMPEL[bein.health.level]}`}
            style={{ width: `${breite}%` }}
          />
        </div>
        <span
          className="w-28 text-right text-[10px] text-slate-500"
          title={bein.health.detail ?? "Abstand zur Liquidation, als Preisbewegung"}
        >
          {abstand ? `${prozent(abstand, 1)} bis Liq.` : "Liq. unbekannt"}
        </span>
      </div>
    </div>
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
