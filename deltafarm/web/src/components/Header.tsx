import type { Health, Summary, Venue } from "../api";
import { betrag, uhrzeit, vorzeichenKlasse } from "../format";
import KillSwitch from "./KillSwitch";

type Props = {
  health: Health | undefined;
  venues: Venue[] | undefined;
  summary: Summary | undefined;
  stand: string | undefined;
  onKillSwitch: () => void;
};

// Umgebung und Modus stehen dauerhaft in der Kopfzeile. Es darf nie unklar
// sein, ob gerade gegen Testnet oder Mainnet gearbeitet wird.
export default function Header({ health, venues, summary, stand, onKillSwitch }: Props) {
  const mainnet = health?.environment === "mainnet";
  const live = health?.dry_run === false;



  return (
    <header className="border-b border-slate-800 bg-slate-950/80 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-sm font-semibold tracking-wide text-slate-200">deltafarm</h1>

        <span
          className={`rounded px-2 py-0.5 text-xs font-bold tracking-wider ${
            mainnet ? "bg-amber-500 text-black" : "bg-slate-700 text-slate-200"
          }`}
        >
          {(health?.environment ?? "…").toUpperCase()}
        </span>

        <span
          className={`rounded px-2 py-0.5 text-xs font-bold tracking-wider ${
            live ? "bg-rose-600 text-white" : "bg-emerald-800 text-emerald-100"
          }`}
        >
          {health === undefined ? "…" : live ? "LIVE" : "DRY RUN"}
        </span>

        {summary && (
          <>
            <span className="text-xs text-slate-400">
              Kapital <span className="text-slate-200">{betrag(summary.total_equity)} $</span>
            </span>
            <span
              className="text-xs text-slate-400"
              title={
                summary.funding_today !== summary.funding_today_confirmed
                  ? `Davon bestätigt: ${betrag(summary.funding_today_confirmed)} $ — der Rest ist gerechnet`
                  : "Alle Beträge von der Börse bestätigt"
              }
            >
              Funding heute{" "}
              <span className={vorzeichenKlasse(summary.funding_today)}>
                {betrag(summary.funding_today)} $
              </span>
              {summary.funding_today !== summary.funding_today_confirmed && (
                <span className="ml-1 text-amber-500">*</span>
              )}
            </span>
          </>
        )}

        <div className="ml-auto flex items-center gap-3 text-xs text-slate-400">
          {venues?.map((v) => (
            <span key={v.name} className="flex items-center gap-1.5" title={v.detail ?? undefined}>
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  v.reachable ? "bg-emerald-500" : "bg-rose-500"
                }`}
              />
              {v.name}
              {!v.supports_trading && (
                <span className="text-slate-600" title="Adapter sendet keine Orders">
                  · nur lesend
                </span>
              )}
            </span>
          ))}
          <span className="tabular-nums">Stand {uhrzeit(stand)}</span>
          <KillSwitch onFertig={onKillSwitch} />
        </div>
      </div>
    </header>
  );
}
