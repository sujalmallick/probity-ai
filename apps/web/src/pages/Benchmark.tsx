import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import { api } from "../lib/api";
import { Empty, TierChip } from "../components/ui";

export default function Benchmark() {
  const [b, setB] = useState<any | null>(null);
  useEffect(() => { api("/benchmark/summary").then(setB); }, []);
  if (!b) return null;
  if (!b.available) return <div className="card mx-auto max-w-3xl"><Empty icon={<BarChart3 size={28} />} title="No benchmark results yet"><p className="text-sm text-muted">Run <code>make benchmark</code> to generate the manual-vs-system comparison.</p></Empty></div>;
  const s = b.system;
  const rows: [string, string, string][] = [
    ["Investigation time / invoice", "20–30 min", `${s.p50_seconds} s (p50)`],
    ["Sources checked (seeded cases)", "2–4", String(s.avg_sources_seeded)],
    ["Clean invoices auto-cleared", "0%", `${s.auto_cleared_clean_pct}%`],
    ["Human intervention on clean invoices", "100%", `${s.human_intervention_pct_clean}%`],
    ["False clears on seeded anomalies", "n/a", String(s.false_clears_seeded)],
    ["Signal precision / recall", "n/a", `${s.precision.toFixed(2)} / ${s.recall.toFixed(2)}`],
  ];
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">Benchmark</h1>
        <p className="text-sm text-muted">{b.dataset.clean} clean + {b.dataset.seeded} seeded-anomaly synthetic invoices · {b.generated_at}</p>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted"><th className="px-4 py-2 font-medium">Metric</th><th className="px-4 py-2 font-medium">Manual</th><th className="px-4 py-2 font-medium">Probity</th></tr></thead>
          <tbody>{rows.map(([m, a, p]) => <tr key={m} className="border-t border-line"><td className="px-4 py-2.5">{m}</td><td className="px-4 py-2.5 text-muted">{a}</td><td className="px-4 py-2.5 font-semibold tabular-nums">{p}</td></tr>)}</tbody>
        </table>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted">{["Invoice", "Seeded", "Result", "Status", "Signals fired"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>
            {b.rows.filter((r: any) => r.kind !== "clean").map((r: any) => (
              <tr key={r.label} className="border-t border-line">
                <td className="px-4 py-2">{r.label}</td><td className="px-4 py-2 text-muted">{r.kind}</td><td className="px-4 py-2"><TierChip tier={r.tier} score={r.score} /></td>
                <td className="px-4 py-2 text-xs">{r.status.replace("_", " ").toLowerCase()}</td><td className="px-4 py-2 text-xs text-muted">{r.fired.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{b.note}</p>
    </div>
  );
}
