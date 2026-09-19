import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  holeFunding,
  holeHealth,
  holeJournal,
  holePairs,
  holeSummary,
  holeVenues,
} from "./api";
import FundingTable from "./components/FundingTable";
import Header from "./components/Header";
import Journal from "./components/Journal";
import OpenDialog from "./components/OpenDialog";
import Positions from "./components/Positions";
import UnhedgedAlarm from "./components/UnhedgedAlarm";

type Tab = "dashboard" | "journal";
type DialogZustand = { symbol: string; longVenue: string; shortVenue: string } | null;

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [dialog, setDialog] = useState<DialogZustand>(null);
  const queryClient = useQueryClient();

  const health = useQuery({ queryKey: ["health"], queryFn: holeHealth });
  const venues = useQuery({ queryKey: ["venues"], queryFn: holeVenues });
  const funding = useQuery({ queryKey: ["funding"], queryFn: holeFunding });
  const summary = useQuery({ queryKey: ["summary"], queryFn: holeSummary });
  // Paare oefter abfragen: ein UNHEDGED-Zustand soll schnell sichtbar werden.
  const pairs = useQuery({ queryKey: ["pairs"], queryFn: holePairs, refetchInterval: 5000 });
  const journal = useQuery({
    queryKey: ["journal"],
    queryFn: holeJournal,
    enabled: tab === "journal",
  });

  const allesNeu = () => queryClient.invalidateQueries();

  return (
    <div className="min-h-screen">
      <Header
        health={health.data}
        venues={venues.data}
        summary={summary.data}
        stand={funding.data?.as_of}
        onKillSwitch={allesNeu}
      />

      <UnhedgedAlarm paare={pairs.data ?? []} onAufgeloest={allesNeu} />

      <nav className="flex gap-1 border-b border-slate-800 bg-slate-950/50 px-4">
        {(["dashboard", "journal"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`border-b-2 px-3 py-2 text-xs ${
              tab === t
                ? "border-emerald-500 text-slate-100"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            {t === "dashboard" ? "Dashboard" : "Journal"}
          </button>
        ))}
      </nav>

      {funding.isError && (
        <div className="m-4 rounded border border-rose-800 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
          Backend nicht erreichbar: {(funding.error as Error).message}
        </div>
      )}

      {tab === "dashboard" ? (
        <>
          {funding.isLoading && (
            <p className="p-4 text-xs text-slate-500">Marktdaten werden geladen…</p>
          )}
          {funding.data && (
            <FundingTable
              daten={funding.data}
              onVorschau={(symbol, longVenue, shortVenue) =>
                setDialog({ symbol, longVenue, shortVenue })
              }
            />
          )}
          {pairs.data && (
            <Positions
              paare={pairs.data}
              live={health.data?.dry_run === false}
              onAenderung={allesNeu}
            />
          )}
        </>
      ) : (
        <Journal eintraege={journal.data ?? []} />
      )}

      {dialog && (
        <OpenDialog
          symbol={dialog.symbol}
          longVenue={dialog.longVenue}
          shortVenue={dialog.shortVenue}
          live={health.data?.dry_run === false}
          onSchliessen={() => setDialog(null)}
          onGeoeffnet={allesNeu}
        />
      )}

      <footer className="px-4 py-6 text-[11px] text-slate-600">
        Phase 3: Ausführung im Trockenlauf. Orders werden geloggt, nicht gesendet.
      </footer>
    </div>
  );
}
