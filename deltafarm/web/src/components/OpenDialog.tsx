import { useState } from "react";
import { erstelleVorschau, fuehreAus } from "../api";
import type { Check, PreviewEingabe, PreviewOut } from "../api";
import { apr, betrag, prozent, stunden, vorzeichenKlasse } from "../format";

const AMPEL: Record<Check["status"], string> = {
  PASS: "bg-emerald-500",
  WARN: "bg-amber-500",
  FAIL: "bg-rose-500",
};

type Props = {
  symbol: string;
  longVenue: string;
  shortVenue: string;
  onSchliessen: () => void;
  onGeoeffnet: () => void;
};

export default function OpenDialog({
  symbol,
  longVenue,
  shortVenue,
  onSchliessen,
  onGeoeffnet,
}: Props) {
  const [notional, setNotional] = useState("1000");
  const [slippage, setSlippage] = useState("0.2");
  const [hebelLong, setHebelLong] = useState("1");
  const [hebelShort, setHebelShort] = useState("1");
  const [erstes, setErstes] = useState(longVenue);
  const [autoRollback, setAutoRollback] = useState(false);

  const [vorschau, setVorschau] = useState<PreviewOut | null>(null);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  const [ergebnis, setErgebnis] = useState<string | null>(null);

  const eingabe = (): PreviewEingabe => ({
    symbol,
    notional_usd: notional,
    long_venue: longVenue,
    short_venue: shortVenue,
    // Die UI zeigt Prozent, die API erwartet einen Bruch.
    max_slippage: String(Number(slippage) / 100),
    long_leverage: hebelLong,
    short_leverage: hebelShort,
    first_venue: erstes,
    hedge_timeout_seconds: 10,
    auto_rollback: autoRollback,
  });

  async function vorschauHolen() {
    setLaeuft(true);
    setFehler(null);
    setErgebnis(null);
    try {
      setVorschau(await erstelleVorschau(eingabe()));
    } catch (e) {
      setFehler((e as Error).message);
      setVorschau(null);
    } finally {
      setLaeuft(false);
    }
  }

  async function ausfuehren() {
    if (!vorschau?.token) return;
    setLaeuft(true);
    setFehler(null);
    try {
      const e = await fuehreAus(vorschau.token);
      setErgebnis(`${e.state} — ${e.detail}`);
      setVorschau(null);
      onGeoeffnet();
    } catch (e) {
      setFehler((e as Error).message);
      // Nach einem Fehlschlag ist der Token verbraucht oder ungültig.
      setVorschau(null);
    } finally {
      setLaeuft(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/70 p-4">
      <div className="mt-8 w-full max-w-2xl rounded border border-slate-700 bg-slate-950">
        <header className="flex items-center gap-2 border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-100">Paar eröffnen — {symbol}</h3>
          <span className="text-xs text-slate-400">
            <span className="text-emerald-400">L</span> {longVenue}
            <span className="mx-1 text-slate-600">/</span>
            <span className="text-rose-400">S</span> {shortVenue}
          </span>
          <button
            onClick={onSchliessen}
            className="ml-auto text-xs text-slate-500 hover:text-slate-300"
          >
            schließen
          </button>
        </header>

        <div className="grid gap-3 border-b border-slate-800 p-4 sm:grid-cols-3">
          <Feld label="Notional (USD)" wert={notional} setzen={setNotional} />
          <Feld label="Max. Slippage (%)" wert={slippage} setzen={setSlippage} />
          <Feld label="Hebel Long" wert={hebelLong} setzen={setHebelLong} />
          <Feld label="Hebel Short" wert={hebelShort} setzen={setHebelShort} />

          <label className="text-xs">
            <span className="mb-1 block text-slate-400">Zuerst ausführen</span>
            <select
              value={erstes}
              onChange={(e) => setErstes(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200"
              title="Das illiquidere oder langsamere Bein zuerst"
            >
              <option value={longVenue}>{longVenue} (Long)</option>
              <option value={shortVenue}>{shortVenue} (Short)</option>
            </select>
          </label>

          <label className="flex items-end gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={autoRollback}
              onChange={(e) => setAutoRollback(e.target.checked)}
              className="mb-1.5 accent-emerald-500"
            />
            <span className="mb-1" title="Nur wenn du nicht am Rechner bist">
              Auto-Rollback
            </span>
          </label>
        </div>

        <div className="flex items-center gap-3 px-4 py-3">
          <button
            onClick={vorschauHolen}
            disabled={laeuft}
            className="rounded bg-slate-700 px-3 py-1.5 text-xs font-medium text-slate-100 hover:bg-slate-600 disabled:opacity-50"
          >
            {laeuft ? "…" : "Vorschau berechnen"}
          </button>

          {vorschau && (
            <button
              onClick={ausfuehren}
              disabled={laeuft || !vorschau.ok || !vorschau.token}
              title={
                vorschau.ok
                  ? `Gültig für ${vorschau.valid_for_seconds} s`
                  : "Erst alle roten Prüfungen beheben"
              }
              className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
            >
              Ausführen (Trockenlauf)
            </button>
          )}

          {vorschau && (
            <span className="text-[11px] text-slate-500">
              Vorschau {vorschau.valid_for_seconds} s gültig
            </span>
          )}
        </div>

        {fehler && (
          <p className="mx-4 mb-3 rounded border border-rose-800 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
            {fehler}
          </p>
        )}
        {ergebnis && (
          <p className="mx-4 mb-3 rounded border border-emerald-800 bg-emerald-950/60 px-3 py-2 text-xs text-emerald-200">
            {ergebnis}
          </p>
        )}

        {vorschau?.error && (
          <p className="mx-4 mb-3 text-xs text-amber-300">{vorschau.error}</p>
        )}

        {vorschau?.sizing && (
          <div className="grid gap-4 border-t border-slate-800 p-4 sm:grid-cols-2">
            <div>
              <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Größen
              </h4>
              <dl className="space-y-1 text-xs">
                <Zeile label="Menge je Bein" wert={betrag(vorschau.sizing.size, 6)} />
                <Zeile label="Raster" wert={betrag(vorschau.sizing.lot_size, 6)} />
                <Zeile
                  label={`Notional ${vorschau.sizing.long_venue}`}
                  wert={`${betrag(vorschau.sizing.long_notional)} $`}
                />
                <Zeile
                  label={`Notional ${vorschau.sizing.short_venue}`}
                  wert={`${betrag(vorschau.sizing.short_notional)} $`}
                />
                <Zeile
                  label="Rest-Delta"
                  wert={`${betrag(vorschau.sizing.residual_delta_usd)} $ (${prozent(
                    vorschau.sizing.residual_delta_pct,
                    3,
                  )})`}
                  klasse={vorzeichenKlasse(vorschau.sizing.residual_delta_usd)}
                />
                <Zeile
                  label="Gebühren (Auf und Zu)"
                  wert={
                    vorschau.sizing.estimated_round_trip_fees
                      ? `${betrag(vorschau.sizing.estimated_round_trip_fees)} $`
                      : "unbekannt"
                  }
                />
              </dl>

              <h4 className="mt-3 mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Ertrag
              </h4>
              <dl className="space-y-1 text-xs">
                <Zeile
                  label="Netto-APR"
                  wert={apr(vorschau.net_apr)}
                  klasse={vorzeichenKlasse(vorschau.net_apr)}
                />
                <Zeile label="Break-even" wert={stunden(vorschau.breakeven_hours)} />
              </dl>
            </div>

            <div>
              <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Preflight
              </h4>
              <ul className="space-y-1.5">
                {vorschau.checks.map((c) => (
                  <li key={c.key} className="flex gap-2 text-xs">
                    <span
                      className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${AMPEL[c.status]}`}
                    />
                    <span>
                      <span className="text-slate-200">{c.label}</span>
                      <span className="block text-[11px] text-slate-500">{c.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Feld({
  label,
  wert,
  setzen,
}: {
  label: string;
  wert: string;
  setzen: (w: string) => void;
}) {
  return (
    <label className="text-xs">
      <span className="mb-1 block text-slate-400">{label}</span>
      <input
        value={wert}
        onChange={(e) => setzen(e.target.value)}
        inputMode="decimal"
        className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200 focus:border-slate-500 focus:outline-none"
      />
    </label>
  );
}

function Zeile({ label, wert, klasse }: { label: string; wert: string; klasse?: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-slate-500">{label}</dt>
      <dd className={klasse ?? "text-slate-200"}>{wert}</dd>
    </div>
  );
}
