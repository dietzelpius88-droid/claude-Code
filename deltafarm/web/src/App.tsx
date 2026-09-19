import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  holeBalances,
  holeFunding,
  holeHealth,
  holeJournal,
  holePositionen,
  holeVenues,
} from "./api";
import FundingTable from "./components/FundingTable";
import Header from "./components/Header";
import Journal from "./components/Journal";
import Positions from "./components/Positions";

type Tab = "dashboard" | "journal";

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");

  const health = useQuery({ queryKey: ["health"], queryFn: holeHealth });
  const venues = useQuery({ queryKey: ["venues"], queryFn: holeVenues });
  const funding = useQuery({ queryKey: ["funding"], queryFn: holeFunding });
  const balances = useQuery({ queryKey: ["balances"], queryFn: holeBalances });
  const positionen = useQuery({ queryKey: ["positionen"], queryFn: holePositionen });
  const journal = useQuery({
    queryKey: ["journal"],
    queryFn: holeJournal,
    enabled: tab === "journal",
  });

  return (
    <div className="min-h-screen">
      <Header
        health={health.data}
        venues={venues.data}
        balances={balances.data}
        stand={funding.data?.as_of}
      />

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
          {funding.data && <FundingTable daten={funding.data} />}
          {positionen.data && <Positions paare={positionen.data} />}
        </>
      ) : (
        <Journal eintraege={journal.data ?? []} />
      )}

      <footer className="px-4 py-6 text-[11px] text-slate-600">
        Phase 2: Marktdaten, Konten und Journal. Es werden keine Orders gesendet.
      </footer>
    </div>
  );
}
