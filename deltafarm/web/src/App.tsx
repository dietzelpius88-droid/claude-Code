import { useQuery } from "@tanstack/react-query";
import { holeFunding, holeHealth, holeVenues } from "./api";
import FundingTable from "./components/FundingTable";
import Header from "./components/Header";

export default function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: holeHealth });
  const venues = useQuery({ queryKey: ["venues"], queryFn: holeVenues });
  const funding = useQuery({ queryKey: ["funding"], queryFn: holeFunding });

  return (
    <div className="min-h-screen">
      <Header health={health.data} venues={venues.data} stand={funding.data?.as_of} />

      {funding.isError && (
        <div className="m-4 rounded border border-rose-800 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
          Backend nicht erreichbar: {(funding.error as Error).message}
        </div>
      )}

      {funding.isLoading && (
        <p className="p-4 text-xs text-slate-500">Marktdaten werden geladen…</p>
      )}

      {funding.data && <FundingTable daten={funding.data} />}

      <footer className="px-4 py-6 text-[11px] text-slate-600">
        Phase 1: nur Marktdaten. Es werden keine Orders gesendet.
      </footer>
    </div>
  );
}
