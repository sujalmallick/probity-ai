import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock3, FilePlus2, FileText, Inbox, PauseCircle, ShieldAlert, ShieldCheck, UploadCloud } from "lucide-react";
import { api, can } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr, relTime, RUNNING, tierColor } from "../lib/format";
import { Skeleton, Spinner, StatusChip, TierChip } from "../components/ui";
import { isDismissed, OnboardingChecklist, type Onboarding } from "../components/Onboarding";

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

const TABS: { key: string; label: string; statuses?: string[] }[] = [
  { key: "all", label: "All" },
  { key: "review", label: "To review", statuses: ["AWAITING_HUMAN"] },
  { key: "vendor", label: "Awaiting vendor", statuses: ["AWAITING_VENDOR"] },
  { key: "cleared", label: "Cleared", statuses: ["AUTO_CLEARED", "APPROVED", "CLOSED"] },
  { key: "rejected", label: "Rejected", statuses: ["REJECTED"] },
];
const TIERS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;
const TIER_RANK: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 };
const TIER_ICON: Record<string, typeof ShieldCheck> = { LOW: ShieldCheck, MEDIUM: AlertTriangle, HIGH: ShieldAlert, CRITICAL: ShieldAlert };
const byRisk = (a: Row, b: Row) =>
  (TIER_RANK[b.risk?.tier ?? ""] ?? 0) - (TIER_RANK[a.risk?.tier ?? ""] ?? 0) || (b.risk?.score ?? 0) - (a.risk?.score ?? 0) || b.created_at.localeCompare(a.created_at);
const titleCase = (t: string) => t[0] + t.slice(1).toLowerCase();

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const canUpload = can(user?.role, "accountant");
  const wsId = user?.workspace?.id;
  const queueRef = useRef<HTMLElement>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [kpi, setKpi] = useState<any>(null);
  const [onb, setOnb] = useState<Onboarding | null>(null);
  const [onbHidden, setOnbHidden] = useState(false);
  const [tab, setTab] = useState("all");
  const [tierF, setTierF] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    api<{ items: Row[] }>("/cases").then((r) => setRows(r.items)).catch((e) => setErr(e.message));
    api("/dashboard/kpis").then(setKpi).catch(() => {});
  };
  useEffect(() => {
    load();
    api<Onboarding>("/workspace/onboarding").then(setOnb).catch(() => setOnb(null));
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  const counts = useMemo(() => Object.fromEntries(TABS.map((t) => [t.key, (rows ?? []).filter((r) => !t.statuses || t.statuses.includes(r.status)).length])), [rows]);
  const shown = useMemo(() => {
    const st = TABS.find((t) => t.key === tab)?.statuses;
    let r = rows ?? [];
    if (st) r = r.filter((x) => st.includes(x.status));
    if (tierF) r = r.filter((x) => x.risk?.tier === tierF);
    return [...r].sort(byRisk);
  }, [rows, tab, tierF]);
  const waiting = useMemo(() => (rows ?? []).filter((r) => r.status === "AWAITING_HUMAN").sort(byRisk), [rows]);
  const mix = useMemo(() => {
    const scored = (rows ?? []).filter((r) => r.risk?.tier && !RUNNING.has(r.status));
    return { total: scored.length, by: Object.fromEntries(TIERS.map((t) => [t, scored.filter((r) => r.risk.tier === t).length])) as Record<string, number> };
  }, [rows]);

  const showQueue = (nextTab: string, tier = "") => {
    setTab(nextTab);
    setTierF(tier);
    queueRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  };

  const empty = rows !== null && rows.length === 0;
  // Wait for the workspace id (local sessions get it from /me) so dismissal is read under the right key.
  const showOnb = !!onb && !onb.complete && !!wsId && !onbHidden && !isDismissed(wsId);
  const onboarding = showOnb && <OnboardingChecklist data={onb!} workspaceId={wsId} onDismiss={() => setOnbHidden(true)} />;
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">{greeting()}{user?.name ? `, ${user.name.split(" ")[0]}` : ""}</h1>
          <p className="text-sm text-muted">{user?.workspace?.name ?? "Your workspace"} · {today}</p>
        </div>
        {canUpload && <Link to="/cases/new" className="btn btn-primary"><FilePlus2 size={16} />Upload invoice</Link>}
      </header>

      {err && <div role="alert" className="rounded-lg bg-high-soft p-3 text-sm text-high">{err}</div>}

      {empty ? (
        <div className={`grid gap-4 ${showOnb ? "lg:grid-cols-[1fr_1.15fr]" : ""}`}>
          <section className="card flex flex-col items-center justify-center gap-4 px-6 py-14 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-2"><UploadCloud size={26} aria-hidden /></div>
            <div>
              <h2 className="text-xl font-medium tracking-tight">Upload your first invoice</h2>
              <p className="mt-1 text-sm text-muted">Probity checks it before anyone pays.</p>
            </div>
            {canUpload
              ? <Link to="/cases/new" className="btn btn-primary"><FilePlus2 size={16} />Upload invoice</Link>
              : <p className="text-xs text-muted">Ask an accountant to upload one.</p>}
          </section>
          {onboarding}
        </div>
      ) : (
        <>
          {onboarding}

          {/* KPI strip */}
          <section className="card grid grid-cols-2 overflow-hidden md:grid-cols-5" aria-label="Key numbers">
            <Stat icon={<FileText size={14} />} label="Processed" value={kpi?.processed} />
            <Stat icon={<CheckCircle2 size={14} />} label="Auto-cleared" value={kpi ? `${kpi.auto_cleared_pct}%` : undefined} />
            <Stat icon={<Clock3 size={14} />} label="Avg time" value={kpi?.avg_investigation_seconds != null ? `${kpi.avg_investigation_seconds}s` : kpi ? "—" : undefined} />
            <Stat icon={<PauseCircle size={14} />} label="On hold" value={kpi ? inr(kpi.held_amount_minor) : undefined} />
            <Stat icon={<Inbox size={14} />} label="To review" value={kpi?.open_reviews} className="col-span-2 md:col-span-1" onClick={() => showQueue("review")} />
          </section>

          {/* Needs your decision */}
          <section aria-labelledby="decide-title">
            <div className="mb-3 flex items-baseline justify-between gap-2">
              <h2 id="decide-title" className="text-base font-semibold">Needs your decision{waiting.length > 0 && <span className="ml-2 text-sm font-normal text-muted tabular-nums">{waiting.length}</span>}</h2>
              {waiting.length > 3 && <button className="text-sm text-muted hover:text-ink" onClick={() => showQueue("review")}>View all</button>}
            </div>
            {!rows ? (
              <div className="grid gap-3 md:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-[132px] !rounded-[14px]" />)}</div>
            ) : waiting.length === 0 ? (
              <div className="card flex items-center gap-3 px-5 py-4 text-sm">
                <CheckCircle2 size={18} className="text-low" aria-hidden />
                <span><span className="font-medium">All caught up.</span> <span className="text-muted">Nothing is waiting on a decision.</span></span>
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-3">
                {waiting.slice(0, 3).map((r) => <DecisionCard key={r.id} row={r} />)}
              </div>
            )}
          </section>

          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
            {/* Queue */}
            <section ref={queueRef} className="card scroll-mt-6 overflow-hidden" aria-label="Case queue">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2.5">
                <div className="flex gap-1 overflow-x-auto" role="group" aria-label="Filter cases by status">
                  {TABS.map((t) => (
                    <button
                      key={t.key}
                      aria-pressed={tab === t.key}
                      className={`tab flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-sm transition-colors duration-150 ${tab === t.key ? "bg-surface-2 font-medium text-ink shadow-[inset_0_0_0_1px_var(--border)]" : "text-muted hover:text-ink"}`}
                      onClick={() => setTab(t.key)}
                    >
                      {t.label}
                      {rows && <span className="text-xs tabular-nums text-muted">{counts[t.key]}</span>}
                    </button>
                  ))}
                </div>
                <select className="input !w-auto !py-1 text-xs" value={tierF} onChange={(e) => setTierF(e.target.value)} aria-label="Filter by risk tier">
                  <option value="">Any risk</option>{TIERS.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
                </select>
              </div>
              {!rows ? (
                <div className="flex flex-col gap-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-9" />)}</div>
              ) : shown.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                  <Inbox size={24} className="text-muted" aria-hidden />
                  <div className="text-sm font-medium">Nothing here</div>
                  <button className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline" onClick={() => { setTab("all"); setTierF(""); }}>Clear filters</button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-muted">
                        {["Risk", "Case", "Vendor", "Amount", "Status", "Age"].map((h) => <th key={h} className="px-4 py-2 font-medium whitespace-nowrap">{h}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((r) => (
                        <tr key={r.id} className="cursor-pointer border-t border-line transition-colors duration-150 hover:bg-surface-2" onClick={() => nav(`/cases/${r.id}`)}>
                          <td className="px-4 py-3">{RUNNING.has(r.status) ? <span className="inline-flex items-center gap-1.5 text-xs text-muted"><Spinner size={12} />Investigating</span> : <TierChip tier={r.risk?.tier} score={r.risk?.score} />}</td>
                          <td className="px-4 py-3 whitespace-nowrap"><Link to={`/cases/${r.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>#{r.number}</Link> <span className="text-muted">· {r.invoice_number ?? "—"}</span></td>
                          <td className="max-w-[220px] truncate px-4 py-3 whitespace-nowrap" title={r.vendor_name ?? undefined}>{r.vendor_name ?? "—"}</td>
                          <td className="px-4 py-3 tabular-nums whitespace-nowrap">{inr(r.amount?.amount_minor)}</td>
                          <td className="px-4 py-3"><StatusChip status={r.status} /></td>
                          <td className="px-4 py-3 whitespace-nowrap text-muted">{relTime(r.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Side column */}
            <aside className="flex flex-col gap-4">
              <section className="card p-4" aria-labelledby="mix-title">
                <h2 id="mix-title" className="text-sm font-semibold">Risk mix</h2>
                {!rows ? <Skeleton className="mt-3 h-24" /> : mix.total === 0 ? (
                  <p className="mt-2 text-sm text-muted">No scored cases yet.</p>
                ) : (
                  <>
                    <div className="mt-3 flex h-2 gap-0.5 overflow-hidden rounded-full" aria-hidden>
                      {TIERS.filter((t) => mix.by[t]).map((t) => <div key={t} style={{ flex: mix.by[t], background: tierColor[t] }} />)}
                    </div>
                    <ul className="mt-3 flex flex-col">
                      {TIERS.map((t) => {
                        const Icon = TIER_ICON[t];
                        return (
                          <li key={t}>
                            <button
                              className={`flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-sm transition-colors duration-150 hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-40 ${tierF === t ? "bg-surface-2" : ""}`}
                              disabled={!mix.by[t]}
                              aria-pressed={tierF === t}
                              onClick={() => showQueue("all", tierF === t ? "" : t)}
                            >
                              <Icon size={14} style={{ color: tierColor[t] }} aria-hidden />
                              <span className="flex-1 text-left">{titleCase(t)}</span>
                              <span className="tabular-nums text-muted">{mix.by[t]}</span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </section>

            </aside>
          </div>
        </>
      )}
    </div>
  );
}

function DecisionCard({ row }: { row: Row }) {
  const tier = row.risk?.tier ?? "";
  return (
    <Link to={`/cases/${row.id}`} className="card group relative flex flex-col gap-3 overflow-hidden p-4 transition-colors duration-150 hover:bg-surface-2">
      <span aria-hidden className="absolute inset-x-0 top-0 h-0.5" style={{ background: tierColor[tier] ?? "var(--border)" }} />
      <div className="flex items-center justify-between gap-2">
        <TierChip tier={row.risk?.tier} score={row.risk?.score} />
        <span className="text-xs text-muted">{relTime(row.created_at)}</span>
      </div>
      <div className="min-w-0">
        <div className="kpi-value truncate">{inr(row.amount?.amount_minor)}</div>
        <div className="mt-0.5 truncate text-sm text-muted">{row.vendor_name ?? "Unknown vendor"} · {row.invoice_number ?? `#${row.number}`}</div>
      </div>
      <span className="flex items-center gap-1 text-sm font-medium">Review<ArrowRight size={14} className="transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden /></span>
    </Link>
  );
}

function Stat({ icon, label, value, onClick, className = "" }: { icon: ReactNode; label: string; value: ReactNode; onClick?: () => void; className?: string }) {
  const body = (
    <>
      <div className="flex items-center gap-1.5 text-xs text-muted"><span aria-hidden>{icon}</span>{label}</div>
      {value === undefined ? <Skeleton className="mt-2 h-7 w-16" /> : <div className="mt-1.5 kpi-value">{value}</div>}
    </>
  );
  // Hairline grid: every cell gets a right and bottom border; the card clips the outer ones.
  const cell = `-mr-px -mb-px border-r border-b border-line px-5 py-4 ${className}`;
  if (!onClick) return <div className={cell}>{body}</div>;
  return (
    <button className={`${cell} group text-left transition-colors duration-150 hover:bg-surface-2`} onClick={onClick}>
      {body}
    </button>
  );
}
