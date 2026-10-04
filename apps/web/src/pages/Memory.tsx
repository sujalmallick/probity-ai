import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Brain, Search } from "lucide-react";
import { api, errMsg } from "../lib/api";
import { relTime } from "../lib/format";
import { Empty, LoadError, Skeleton, TierChip } from "../components/ui";

export default function Memory() {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(
    (query: string) => api(`/memory/cases?q=${encodeURIComponent(query)}`).then((r) => { setRows(r.items); setErr(null); }).catch((e) => setErr(errMsg(e))),
    [],
  );
  useEffect(() => {
    const t = setTimeout(() => load(q), 200);
    return () => clearTimeout(t);
  }, [q, load]);
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="page-title">Case memory</h1>
        <p className="text-sm text-muted">Human-confirmed outcomes only. Reused when the same vendor, bank account or domain appears again. Never shared across workspaces.</p>
      </div>
      <label className="relative">
        <Search size={15} className="absolute left-3 top-2.5 text-muted" />
        <input className="input !pl-9" placeholder="Search past cases (vendor, issue, resolution)…" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>
      {err ? (
        <LoadError error={`Could not load case memory. ${err}`} onRetry={() => load(q)} />
      ) : rows === null ? (
        <div className="flex flex-col gap-3" aria-busy><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
      ) : rows.length === 0 ? (
        <div className="card">
          <Empty icon={<Brain size={28} />} title={q ? "No matching cases" : "No closed cases yet"}>
            <p className="text-sm text-muted">{q ? "Try a different search." : "Close a case to save its outcome here."}</p>
          </Empty>
        </div>
      ) : rows.map((r) => (
        <Link key={r.case_id} to={`/cases/${r.case_id}`} className="card block p-4 hover:bg-surface-2">
          <div className="flex flex-wrap items-center gap-2">
            <b>{r.vendor}</b>
            <span className="rounded bg-surface-2 px-2 py-0.5 text-xs">{r.outcome}</span>
            <TierChip tier={r.peak_tier} score={r.peak_score} />
            <span className="ml-auto text-xs text-muted">{relTime(r.at)}</span>
          </div>
          <div className="mt-1 text-sm text-muted">{r.summary}</div>
          {r.resolution && <div className="mt-1 text-sm">Resolution: {r.resolution}</div>}
        </Link>
      ))}
    </div>
  );
}
