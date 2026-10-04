import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { FilePlus2, FileText, Inbox, PlayCircle } from "lucide-react";
import { api, can, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr, relTime, RUNNING } from "../lib/format";
import { launchDemoFile } from "../lib/launch";
import { Empty, Skeleton, Spinner, StatusChip, TierChip } from "../components/ui";

interface Row {
  id: string;
  number: number;
  status: string;
  vendor_name: string;
  invoice_number: string;
  amount: { amount_minor: number | null };
  risk: { score?: number; tier?: string };
  created_at: string;
  outcome: string | null;
}
interface DemoFile { name: string; title: string; description: string }

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [kpi, setKpi] = useState<any>(null);
  const [files, setFiles] = useState<DemoFile[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tierF, setTierF] = useState("");
  const [statusF, setStatusF] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    api<{ items: Row[] }>("/cases").then((r) => setRows(r.items)).catch((e) => setErr(e.message));
    api("/dashboard/kpis").then(setKpi).catch(() => {});
  };
  useEffect(() => {
    load();
    if (can(user?.role, "accountant")) post<{ files: DemoFile[] }>("/demo/seed").then((r) => setFiles(r.files)).catch(() => setFiles([]));
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  const shown = useMemo(() => {
    let r = rows ?? [];
    if (tierF) r = r.filter((x) => x.risk?.tier === tierF);
    if (statusF) r = r.filter((x) => x.status === statusF);
    const rank: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 };
    return [...r].sort((a, b) => (rank[b.risk?.tier ?? ""] ?? 0) - (rank[a.risk?.tier ?? ""] ?? 0) || b.created_at.localeCompare(a.created_at));
  }, [rows, tierF, statusF]);

  const start = async (name: string) => {
    setBusy(name);
    setErr(null);
    try {
      const { caseId } = await launchDemoFile(name);
      nav(`/cases/${caseId}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Dashboard</h1>
          <p className="text-sm text-muted">Every invoice investigated before payment. Low risk clears automatically; everything else waits for you.</p>
        </div>
        {can(user?.role, "accountant") && <Link to="/cases/new" className="btn btn-primary"><FilePlus2 size={16} />Upload invoice</Link>}
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          ["Invoices processed", kpi?.processed ?? "—"],
          ["Auto-cleared", kpi ? `${kpi.auto_cleared_pct}%` : "—"],
          ["Avg investigation", kpi?.avg_investigation_seconds != null ? `${kpi.avg_investigation_seconds}s` : "—"],
          ["Held amount", kpi ? inr(kpi.held_amount_minor) : "—"],
          ["Open reviews", kpi?.open_reviews ?? "—"],
        ].map(([k, v]) => (
          <div key={k} className="card px-4 py-3">
            <div className="label">{k}</div>
            <div className="mt-1 text-xl font-bold tabular-nums">{v}</div>
          </div>
        ))}
      </div>

      {files && files.length > 0 && (
        <div className="card p-4">
          <div className="mb-3 flex items-center gap-2">
            <PlayCircle size={16} className="text-accent" />
            <div className="font-semibold">Demo invoices</div>
            <div className="text-xs text-muted">Synthetic data · runs fully offline</div>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {files.map((f, i) => (
              <button key={f.name} className={`btn !justify-start !py-3 text-left ${i === 0 ? "!border-accent" : ""}`} disabled={!!busy} onClick={() => start(f.name)}>
                {busy === f.name ? <Spinner /> : <FileText size={16} className={i === 0 ? "text-accent" : "text-muted"} />}
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{f.title}</span>
                  <span className="block truncate text-xs font-normal text-muted">{f.description}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      {err && <div className="rounded-lg bg-high-soft p-3 text-sm text-high">{err}</div>}

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="font-semibold">Case queue</div>
          <div className="flex gap-2">
            <select className="input !w-auto !py-1 text-xs" value={tierF} onChange={(e) => setTierF(e.target.value)} aria-label="Filter by tier">
              <option value="">All tiers</option>{["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((t) => <option key={t}>{t}</option>)}
            </select>
            <select className="input !w-auto !py-1 text-xs" value={statusF} onChange={(e) => setStatusF(e.target.value)} aria-label="Filter by status">
              <option value="">All statuses</option>{["AWAITING_HUMAN", "AWAITING_VENDOR", "AUTO_CLEARED", "APPROVED", "REJECTED", "CLOSED"].map((t) => <option key={t} value={t}>{t.replace("_", " ").toLowerCase()}</option>)}
            </select>
          </div>
        </div>
        {!rows ? (
          <div className="flex flex-col gap-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-9" />)}</div>
        ) : shown.length === 0 ? (
          <Empty icon={<Inbox size={28} />} title={rows.length ? "No cases match these filters" : "No invoices yet"}>
            <p className="max-w-sm text-sm text-muted">Upload an invoice, or start with <b>invoice_4821.pdf</b> above to watch a full investigation.</p>
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted">
                  {["Risk", "Case", "Vendor", "Amount", "Status", "Age"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} className="cursor-pointer border-t border-line hover:bg-surface-2" onClick={() => nav(`/cases/${r.id}`)}>
                    <td className="px-4 py-2.5">{RUNNING.has(r.status) ? <span className="inline-flex items-center gap-1.5 text-xs text-muted"><Spinner size={12} />investigating</span> : <TierChip tier={r.risk?.tier} score={r.risk?.score} />}</td>
                    <td className="px-4 py-2.5"><Link to={`/cases/${r.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>#{r.number}</Link> <span className="text-muted">· {r.invoice_number ?? "—"}</span></td>
                    <td className="px-4 py-2.5">{r.vendor_name ?? "—"}</td>
                    <td className="px-4 py-2.5 tabular-nums">{inr(r.amount?.amount_minor)}</td>
                    <td className="px-4 py-2.5"><StatusChip status={r.status} />{r.outcome && <span className="ml-1 text-xs text-muted">· {r.outcome.toLowerCase().replace("_", " ")}</span>}</td>
                    <td className="px-4 py-2.5 text-muted">{relTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
