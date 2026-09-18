// Einheitliche Zahlendarstellung: Prozentwerte immer mit Vorzeichen, Betraege
// mit Tausendertrennung und fester Nachkommastellenzahl.

export function prozent(wert: string | null | undefined, stellen = 4): string {
  if (wert == null) return "–";
  const zahl = Number(wert) * 100;
  if (!Number.isFinite(zahl)) return "–";
  const vorzeichen = zahl > 0 ? "+" : zahl < 0 ? "−" : "";
  return `${vorzeichen}${Math.abs(zahl).toLocaleString("de-DE", {
    minimumFractionDigits: stellen,
    maximumFractionDigits: stellen,
  })} %`;
}

export function apr(wert: string | null | undefined): string {
  return prozent(wert, 2);
}

export function stunden(wert: string | null | undefined): string {
  if (wert == null) return "–";
  const zahl = Number(wert);
  if (!Number.isFinite(zahl)) return "–";
  if (zahl >= 1000) return `${Math.round(zahl).toLocaleString("de-DE")} h`;
  return `${zahl.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} h`;
}

export function betrag(wert: string | null | undefined, stellen = 2): string {
  if (wert == null) return "–";
  const zahl = Number(wert);
  if (!Number.isFinite(zahl)) return "–";
  return zahl.toLocaleString("de-DE", {
    minimumFractionDigits: stellen,
    maximumFractionDigits: stellen,
  });
}

export function uhrzeit(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "–"
    : d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Vorzeichenabhaengige Einfaerbung. Gruen verdient, rot kostet.
export function vorzeichenKlasse(wert: string | null | undefined): string {
  if (wert == null) return "text-slate-400";
  const zahl = Number(wert);
  if (!Number.isFinite(zahl) || zahl === 0) return "text-slate-400";
  return zahl > 0 ? "text-emerald-400" : "text-rose-400";
}
