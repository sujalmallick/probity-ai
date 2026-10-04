import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Braces, Brain, Building2, CheckCircle2, ChevronDown, ChevronRight, ClipboardList, Download, FileText, Globe, HelpCircle,
  History, Landmark, Mail, MailCheck, MessageSquare, MoreHorizontal, PhoneCall, Search, Send, ShieldAlert, ShieldCheck, ThumbsDown, ThumbsUp, UserCheck, Workflow, XCircle,
} from "lucide-react";
import { api, can, fetchBlob, post, streamEvents, type Role } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAppConfig } from "../lib/config";
import { evValue, inr, isoDate, relTime, RUNNING, tierColor } from "../lib/format";
import { Gauge } from "../components/Gauge";
import { RelGraph } from "../components/RelGraph";
import { ActivityLog, Timeline, type AgentEvent } from "../components/Timeline";
import { Drawer, MenuButton, Modal, Skeleton, Spinner, StatusChip, VerifyBadge } from "../components/ui";

type Any = Record<string, any>;

async function download(path: string, name: string) {
  const b = await fetchBlob(path);
  const u = URL.createObjectURL(b);
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}

const SRC_ICON: Record<string, any> = {
  invoice: FileText, vendor_history: History, vendor_master: Building2, purchase_order: ClipboardList, registry: Landmark,
  web: Globe, domain: Globe, vendor_reply: Mail, approver: UserCheck,
};
const SRC_LABEL: Record<string, string> = {
  invoice: "Invoice", vendor_history: "Internal history", vendor_master: "Vendor master", purchase_order: "Purchase order", registry: "Registry",
  web: "Web", domain: "Domain registry", vendor_reply: "Vendor reply", approver: "Approver",
};

export default function CaseView() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const cfg = useAppConfig();
  const nav = useNavigate();
  const [c, setC] = useState<Any | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [evidence, setEvidence] = useState<Any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState("evidence");
  const [why, setWhy] = useState<Any | null>(null);
  const [modal, setModal] = useState<null | "approve" | "reject" | "verify" | "draft" | "oob" | "close" | "investigate">(null);
  const [pipeline, setPipeline] = useState(false);
  const [showQuiet, setShowQuiet] = useState(false);
  const [fullOnPhone, setFullOnPhone] = useState(false);
  const refetchTimer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const [cc, ev] = await Promise.all([api(`/cases/${id}`), api(`/cases/${id}/evidence`)]);
      setC(cc);
      setEvidence(ev.items);
    } catch (e: any) {
      setErr(e.message);
    }
  }, [id]);

  useEffect(() => {
    setC(null);
    setEvents([]);
    setWhy(null);
    load();
    const stop = streamEvents(`/cases/${id}/events`, (e: AgentEvent) => {
      setEvents((prev) => (prev.some((p) => p.seq === e.seq) ? prev : [...prev, e]));
      if (["agent.completed", "risk.updated", "gate.waiting", "decision.recorded", "action.sent", "vendor.reply_received", "case.closed", "agent.failed", "verification.confirmed_out_of_band"].includes(e.type)) {
        window.clearTimeout(refetchTimer.current);
        refetchTimer.current = window.setTimeout(load, 250);
      }
    });
    return stop;
  }, [id, load]);

  const running = !!c && RUNNING.has(c.status);
  const role = user?.role as Role | undefined;
  const isApprover = can(role, "approver");
  const awaiting = c?.status === "AWAITING_HUMAN";
  const evById = useMemo(() => Object.fromEntries(evidence.map((e) => [e.id, e])), [evidence]);
  const claimById = useMemo(() => Object.fromEntries((c?.claims ?? []).map((x: Any) => [x.id, x])), [c]);

  if (err) return <div className="rounded-lg bg-high-soft p-4 text-high">{err} <Link to="/dashboard" className="underline">Back</Link></div>;
  if (!c) return <div className="mx-auto flex max-w-6xl flex-col gap-3"><Skeleton className="h-16" /><Skeleton className="h-80" /></div>;

  const risk = c.risk ?? {};
  const contribs: Any[] = risk.contributions ?? [];
  const scored = contribs.filter((x) => x.status === "counted" || x.status === "unconfirmed");
  const replyClaims: Any[] = c.claims.filter((x: Any) => x.agent === "action" && x.active);
  const pendingReply = replyClaims.filter((x) => x.status === "unverified");
  const infoClaims: Any[] = c.claims.filter((x: Any) => x.active && !x.signal && x.status === "verified" && !["action", "approver"].includes(x.agent));
  const memoryClaims: Any[] = infoClaims.filter((x) => x.agent === "orchestrator");
  const otherInfo = infoClaims.filter((x) => x.agent !== "orchestrator");
  const unconfirmedInfo: Any[] = c.claims.filter((x: Any) => x.active && !x.signal && x.status === "unverified" && x.agent !== "action");
  const gate = c.recommendation?.gate;
  const draft = (c.drafts ?? []).find((d: Any) => d.status === "draft");
  const sent = (c.drafts ?? []).find((d: Any) => d.status === "sent");
  const dis = (needApprover = true) => (!awaiting ? `Case is ${c.status.toLowerCase().replace("_", " ")}` : needApprover && !isApprover ? "Requires approver role" : "");

  const openWhy = async () => setWhy(why ? null : await api(`/cases/${id}/explain`));
  const anomalies = scored.filter((x) => x.points > 0);
  const quiet = [...scored.filter((x) => !(x.points > 0)).map((x) => ({ id: x.signal, statement: x.label, status: claimById[x.claim_id]?.status ?? "verified" })), ...otherInfo, ...unconfirmedInfo];
  const lastEvent = [...events].reverse().find((e) => e.message);
  const rec = c.status === "AUTO_CLEARED" ? "Auto-cleared" : ACTION_LABEL[c.recommendation?.action] ?? "Pending";
  const RecIcon = risk.tier === "LOW" || c.status === "AUTO_CLEARED" ? ShieldCheck : risk.tier === "MEDIUM" ? AlertTriangle : ShieldAlert;
  const decided = ["APPROVED", "REJECTED", "AUTO_CLEARED"].includes(c.status);
  const latestReply = (c.messages ?? []).filter((m: Any) => m.direction === "in").slice(-1)[0];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 pb-24 lg:pb-0">
      {/* Top bar */}
      <div className="flex items-center justify-between gap-3">
        <Link to="/dashboard" className="inline-flex items-center gap-1.5 rounded-md text-sm text-muted transition-colors hover:text-ink"><ArrowLeft size={15} aria-hidden />Cases</Link>
        <div className="flex items-center gap-2">
          <button className="btn !py-1.5 text-sm" onClick={() => setPipeline(true)} aria-haspopup="dialog">
            {running ? <Spinner size={14} /> : <Workflow size={15} aria-hidden />}Agent pipeline
          </button>
          <MenuButton
            label={<span className="hidden sm:inline">Export</span>}
            icon={<Download size={15} aria-hidden />}
            items={[
              { label: "PDF report", hint: "For your records or auditors", icon: <FileText size={15} />, onSelect: () => download(`/api/v1/cases/${id}/export?format=pdf`, `probity-case-${c.number}.pdf`) },
              { label: "JSON data", hint: "Every claim, evidence item and score", icon: <Braces size={15} />, onSelect: () => download(`/api/v1/cases/${id}/export?format=json`, `probity-case-${c.number}.json`) },
            ]}
          />
        </div>
      </div>

      {/* Title */}
      <header>
        <h1 className="page-title">{c.vendor_name ?? "Unknown vendor"}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-muted">
          <span className="font-medium tabular-nums text-ink">{inr(c.amount?.amount_minor)}</span>
          <span>{c.invoice_number ?? c.document?.filename}</span>
          <span>Case #{c.number}</span>
          <StatusChip status={c.status} />
          {c.partial && <span className="text-medium">Some checks didn't finish</span>}
        </div>
      </header>

      {memoryClaims.map((m) => (
        <div key={m.id} className="fade-in flex items-start gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
          <Brain size={17} className="mt-0.5 shrink-0" aria-hidden />
          <div><span className="font-medium">From case memory: </span><span className="text-muted">{m.statement}</span></div>
        </div>
      ))}

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="flex min-w-0 flex-col gap-5">
          {/* Anomalies */}
          <section className="card p-5" aria-labelledby="anoms">
            <div className="mb-4 flex items-center justify-between gap-2">
              <h2 id="anoms" className="text-base font-semibold">
                {running ? "Investigating…" : anomalies.length ? `${anomalies.length} ${anomalies.length === 1 ? "anomaly" : "anomalies"} found` : "No anomalies found"}
              </h2>
              {!running && contribs.length > 0 && <button className="btn !py-1 text-xs" onClick={openWhy} aria-expanded={!!why}><HelpCircle size={14} aria-hidden /><span className="hidden sm:inline">Why this score?</span><span className="sm:hidden">Why?</span></button>}
            </div>
            {running && (
              <button className="flex w-full items-center gap-3 rounded-lg bg-surface-2 px-3 py-3 text-left text-sm" onClick={() => setPipeline(true)}>
                <Spinner />
                <span className="min-w-0 flex-1 truncate text-muted">{lastEvent?.message ?? "Starting agents…"}</span>
                <span className="shrink-0 text-xs font-medium">View pipeline</span>
              </button>
            )}
            {!running && anomalies.length === 0 && (
              <div className="flex items-center gap-2 text-sm text-muted"><CheckCircle2 size={16} className="text-low" aria-hidden />Every check that ran came back clean.</div>
            )}
            <div className="flex flex-col gap-2">
              {anomalies.map((f) => <Finding key={f.signal} f={f} claim={claimById[f.claim_id]} evById={evById} />)}
            </div>
            {risk.diff?.length > 0 && <ScoreDiff risk={risk} />}
            {why && <WhyPanel why={why} />}
            {!running && quiet.length > 0 && (
              <div className="mt-4 border-t border-line pt-3">
                <button className="flex w-full items-center gap-1.5 text-sm text-muted hover:text-ink" onClick={() => setShowQuiet(!showQuiet)} aria-expanded={showQuiet}>
                  {showQuiet ? <ChevronDown size={15} aria-hidden /> : <ChevronRight size={15} aria-hidden />}
                  {quiet.length} other {quiet.length === 1 ? "finding" : "findings"} with no score impact
                </button>
                {showQuiet && (
                  <ul className="mt-2 flex flex-col gap-1.5 pl-5">
                    {quiet.map((x: Any) => (
                      <li key={x.id} className="flex items-start justify-between gap-3 text-sm">
                        <span className="text-muted">{x.statement}</span>
                        {x.status !== "verified" && <VerifyBadge status={x.status} />}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          {/* Vendor reply */}
          {replyClaims.length > 0 && (
            <section className="card fade-in p-5" aria-labelledby="reply">
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 id="reply" className="flex items-center gap-2 text-base font-semibold"><Mail size={16} aria-hidden />Vendor reply</h2>
                {pendingReply.length > 0 && <span className="text-xs font-medium text-medium">Awaiting approver</span>}
              </div>
              {latestReply && (
                <details className="mb-3 rounded-lg bg-surface-2 p-3 text-sm">
                  <summary className="cursor-pointer text-xs text-muted">From <b className="text-ink">{latestReply.from}</b> · {relTime(latestReply.at)} · show message</summary>
                  <div className="mt-2 whitespace-pre-wrap">{latestReply.body}</div>
                </details>
              )}
              {latestReply?.indicators?.length > 0 && (
                <ul className="mb-3 flex flex-col gap-1">
                  {latestReply.indicators.map((i: string) => <li key={i} className="flex items-center gap-1.5 text-xs font-medium text-high"><ShieldAlert size={13} aria-hidden />{i}</li>)}
                </ul>
              )}
              <ul className="flex flex-col gap-2">
                {replyClaims.map((r) => <li key={r.id} className="flex items-start justify-between gap-3 text-sm"><span>{r.statement.split(" (unverified")[0]}</span><VerifyBadge status={r.status} /></li>)}
              </ul>
              {pendingReply.length > 0 && (
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <button className="btn btn-primary" disabled={!!dis()} title={dis() || "Confirm through a channel already on file"} onClick={() => setModal("oob")}><PhoneCall size={15} aria-hidden />Confirm out-of-band</button>
                  <span className="text-xs text-muted">A reply alone never lowers the score.</span>
                </div>
              )}
            </section>
          )}

          {!fullOnPhone && (
            <button className="btn md:hidden" onClick={() => setFullOnPhone(true)}><ChevronDown size={15} aria-hidden />Show evidence, invoice and comments</button>
          )}
          <div className={`flex min-w-0 flex-col gap-5 ${fullOnPhone ? "" : "hidden md:flex"}`}>
            <Comments caseId={id} canPost={can(role, "accountant")} />
            <Tabs tab={tab} setTab={setTab} c={c} evidence={evidence} id={id} />
          </div>
        </div>

        {/* Score + decision (sticky on desktop) */}
        <aside className="order-first flex flex-col gap-4 lg:order-none lg:sticky lg:top-6" aria-label="Score and decision">
          <section className="card flex flex-col items-center px-5 pt-5 pb-4 text-center">
            <div title={risk.weights_version ? `Computed by code · weights ${risk.weights_version}` : undefined}>
              <Gauge score={running && !risk.score && risk.score !== 0 ? null : risk.score ?? null} tier={risk.tier} provisional={running} size={136} />
            </div>
            {!running && (
              <div className="mt-2 flex items-center gap-1.5 text-base font-semibold" style={{ color: risk.tier ? tierColor[risk.tier] : undefined }} title={gate?.reasons?.join("; ")}>
                <RecIcon size={17} aria-hidden />{rec}
              </div>
            )}
            {gate?.dual_approval && awaiting && (
              <div className="mt-2 rounded-md bg-high-soft px-2 py-1 text-xs font-medium text-high">Two approvers needed{c.recommendation?.approvals?.length ? ` · ${c.recommendation.approvals.length}/2` : ""}</div>
            )}
          </section>

          <section className="card hidden flex-col gap-2 p-4 lg:flex" role="group" aria-label="Decision">
            <h2 className="mb-1 text-sm font-semibold">Your decision</h2>
            <button className="btn btn-primary" disabled={!!dis()} title={dis()} onClick={() => setModal("verify")}><MailCheck size={15} aria-hidden />Request verification</button>
            <div className="grid grid-cols-2 gap-2">
              <button className="btn" disabled={!!dis()} title={dis()} onClick={() => setModal("approve")}><ThumbsUp size={15} aria-hidden />Approve</button>
              <button className="btn btn-danger" disabled={!!dis()} title={dis()} onClick={() => setModal("reject")}><ThumbsDown size={15} aria-hidden />Reject</button>
            </div>
            <button className="btn" disabled={!!dis() || c.depth >= 2} title={c.depth >= 2 ? "Depth limit reached (2)" : dis()} onClick={() => setModal("investigate")}><Search size={15} aria-hidden />Investigate further</button>
            {decided && (
              <button
                className="btn"
                disabled={!can(role, c.status === "AUTO_CLEARED" ? "accountant" : "approver")}
                title={!can(role, c.status === "AUTO_CLEARED" ? "accountant" : "approver") ? `Requires ${c.status === "AUTO_CLEARED" ? "accountant" : "approver"} role` : ""}
                onClick={() => setModal("close")}
              >
                <CheckCircle2 size={15} aria-hidden />Close case
              </button>
            )}
            {!isApprover && <p className="mt-1 text-xs text-muted">View only. Approvers make decisions.</p>}
          </section>

          {c.status === "AWAITING_VENDOR" && sent && (
            <section className="card fade-in flex flex-col gap-2 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold"><MailCheck size={16} aria-hidden />Email sent</div>
              <div className="text-xs text-muted">To {sent.to_email} · follow up {isoDate(sent.followup_at)}</div>
              {cfg.features.simulated_inbox ? (
                <>
                  <div className="label mt-2">Simulated inbox</div>
                  <button className="btn" disabled={!can(role, "accountant")} title={!can(role, "accountant") ? "Requires accountant role" : ""} onClick={() => post(`/demo/vendor-reply/${id}?kind=legit`).then(load)}>Deliver vendor reply</button>
                  <button className="btn !text-xs text-muted" disabled={!can(role, "accountant")} onClick={() => post(`/demo/vendor-reply/${id}?kind=spoof`).then(load)}>Deliver spoofed reply</button>
                </>
              ) : (
                <div className="text-xs text-muted">Waiting for the vendor's reply.</div>
              )}
            </section>
          )}
        </aside>
      </div>

      {/* Phone / tablet decision bar */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-bg/95 px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur lg:hidden" role="group" aria-label="Decision">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <button className="btn btn-primary flex-1 !px-2" disabled={!!dis()} title={dis()} onClick={() => setModal("verify")}><MailCheck size={15} aria-hidden />Verify</button>
          <button className="btn flex-1 !px-2" disabled={!!dis()} title={dis()} onClick={() => setModal("approve")}><ThumbsUp size={15} aria-hidden />Approve</button>
          <button className="btn btn-danger flex-1 !px-2" disabled={!!dis()} title={dis()} onClick={() => setModal("reject")}><ThumbsDown size={15} aria-hidden />Reject</button>
          <MenuButton
            label={<span className="sr-only">More actions</span>}
            icon={<MoreHorizontal size={16} aria-hidden />}
            items={[
              ...(!dis() && c.depth < 2 ? [{ label: "Investigate further", icon: <Search size={15} />, onSelect: () => setModal("investigate") }] : []),
              ...(decided && can(role, c.status === "AUTO_CLEARED" ? "accountant" : "approver") ? [{ label: "Close case", icon: <CheckCircle2 size={15} />, onSelect: () => setModal("close") }] : []),
              { label: "Agent pipeline", icon: <Workflow size={15} />, onSelect: () => setPipeline(true) },
            ]}
            up
          />
        </div>
        {!isApprover && <p className="mt-1.5 text-center text-[11px] text-muted">View only. Approvers make decisions.</p>}
      </div>

      {pipeline && (
        <Drawer title="Agent pipeline" onClose={() => setPipeline(false)}>
          <Timeline events={events} running={running} />
          <details className="mt-4 rounded-lg border border-line p-3">
            <summary className="cursor-pointer text-sm font-medium">Activity log</summary>
            <div className="mt-3"><ActivityLog events={events} /></div>
          </details>
          <p className="mt-4 text-xs text-muted">{c.sources_checked > 0 ? `${c.sources_checked + 2} sources checked` : ""}{c.duration_ms ? ` · ${(c.duration_ms / 1000).toFixed(1)}s` : ""}{c.depth > 0 ? ` · investigated further ×${c.depth}` : ""}</p>
        </Drawer>
      )}

      {modal === "approve" && <DecisionModal title="Approve payment" kind="APPROVE" needReason={["HIGH", "CRITICAL"].includes(risk.tier)} id={id} onDone={() => { setModal(null); load(); }} onClose={() => setModal(null)} />}
      {modal === "reject" && <DecisionModal title="Reject invoice" kind="REJECT" needReason id={id} onDone={() => { setModal(null); load(); }} onClose={() => setModal(null)} />}
      {modal === "investigate" && <DecisionModal title="Investigate further" kind="INVESTIGATE_FURTHER" needReason={false} id={id} onDone={() => { setModal(null); setEvents([]); load(); }} onClose={() => setModal(null)} />}
      {modal === "verify" && <DecisionModal title="Request vendor verification" kind="REQUEST_VERIFICATION" needReason={false} id={id} onDone={async () => { await load(); setModal("draft"); }} onClose={() => setModal(null)}
        hint="Probity drafts a neutral email to the vendor's verified contact. Nothing is sent until you approve it." />}
      {modal === "draft" && draft && <DraftModal draft={draft} id={id} canSend={isApprover} onClose={() => setModal(null)} onSent={() => { setModal(null); load(); }} />}
      {modal === "oob" && <OOBModal claims={pendingReply} id={id} onClose={() => setModal(null)} onDone={() => { setModal(null); load(); }} />}
      {modal === "close" && <CloseModal id={id} onClose={() => setModal(null)} onDone={() => { setModal(null); load(); }} />}
      {draft && modal === null && awaiting && (
        <button className="btn btn-primary fixed right-5 bottom-24 z-30 shadow-lg lg:bottom-5" onClick={() => setModal("draft")}><Mail size={15} aria-hidden />Review draft email</button>
      )}
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  PROCEED: "Looks clear",
  REVIEW: "Recommend review",
  HOLD_PAYMENT: "Recommend hold",
  REQUEST_VERIFICATION: "Recommend vendor check",
};


const ROLE_NAME: Record<string, string> = { owner: "Owner", approver: "Approver", accountant: "Accountant", viewer: "Viewer" };

/** Case comment thread: oldest first, newest at the bottom next to the input. Every comment is audited server-side. */
function Comments({ caseId, canPost }: { caseId: string; canPost: boolean }) {
  const { user } = useAuth();
  const [notes, setNotes] = useState<Any[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const load = useCallback(() => api<{ items: Any[] }>(`/cases/${caseId}/notes`).then((r) => { setNotes(r.items); setLoadErr(null); }).catch((e) => setLoadErr(e.message)), [caseId]);
  useEffect(() => { load(); }, [load]);
  const ordered = useMemo(() => [...(notes ?? [])].sort((a, b) => String(a.at).localeCompare(String(b.at))), [notes]);
  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!text.trim()) return setErr("Write a comment first.");
    setBusy(true);
    setErr(null);
    try {
      const n = await post(`/cases/${caseId}/notes`, { text: text.trim() });
      setNotes((prev) => [...(prev ?? []), { author: user?.name, author_role: user?.role, ...n }]);
      setText("");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card p-5" aria-labelledby="comments-title">
      <h2 id="comments-title" className="mb-4 flex items-center gap-2 text-base font-semibold">
        <MessageSquare size={16} aria-hidden />Comments{notes && notes.length > 0 && <span className="text-sm font-normal text-muted tabular-nums">{notes.length}</span>}
      </h2>
      {loadErr && <div role="alert" className="mb-3 rounded-lg bg-high-soft p-2.5 text-sm text-high">Couldn't load comments: {loadErr}</div>}
      {!notes && !loadErr && <div className="flex flex-col gap-3"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
      {notes && ordered.length === 0 && <p className="mb-4 text-sm text-muted">No comments yet.{canPost ? " Add context for the next reviewer." : ""}</p>}
      {ordered.length > 0 && (
        <ol className="mb-4 flex flex-col gap-4">
          {ordered.map((n) => (
            <li key={n.id} className="flex gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[11px] font-semibold" aria-hidden>
                {(n.author ?? "?").split(" ").map((p: string) => p[0]).slice(0, 2).join("").toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                  <span className="font-medium">{n.author ?? "Unknown"}</span>
                  {n.author_role && <span className="rounded border border-line px-1.5 text-[11px] text-muted">{ROLE_NAME[n.author_role] ?? n.author_role}</span>}
                  <time className="text-xs text-muted" dateTime={n.at} title={n.at ? new Date(n.at).toLocaleString() : undefined}>{n.at ? relTime(n.at) : ""}</time>
                </div>
                <p className="mt-0.5 text-sm whitespace-pre-wrap break-words">{n.text}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
      {canPost ? (
        <form onSubmit={submit} className="flex flex-col gap-2">
          <label htmlFor="comment" className="sr-only">Add a comment</label>
          <textarea
            id="comment"
            className="input min-h-[72px] resize-y"
            placeholder="Add a comment…"
            value={text}
            maxLength={4000}
            onChange={(e) => { setText(e.target.value); setErr(null); }}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); }}
            aria-describedby="comment-hint"
          />
          {err && <p role="alert" className="text-xs text-high">{err}</p>}
          <div className="flex items-center justify-between gap-2">
            <span id="comment-hint" className="text-xs text-muted">Ctrl + Enter to post · saved to the audit log</span>
            <button type="submit" className="btn btn-primary !py-1.5 text-sm" disabled={busy}>{busy ? <Spinner /> : <Send size={14} aria-hidden />}Post</button>
          </div>
        </form>
      ) : (
        <p className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted" title="Requires accountant role">Viewers can read comments but not post.</p>
      )}
    </section>
  );
}

function Finding({ f, claim, evById }: { f: Any; claim?: Any; evById: Record<string, Any> }) {
  const [open, setOpen] = useState(false);
  const counted = f.status === "counted" && f.points > 0;
  const color = counted ? (f.points >= 20 ? "var(--high)" : "var(--medium)") : "var(--muted)";
  const d = claim?.data ?? {};
  return (
    <div className="fade-in rounded-lg border border-line">
      <button className="flex w-full items-center gap-3 px-3 py-2.5 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        {counted ? <ShieldAlert size={17} style={{ color }} aria-hidden /> : <ShieldCheck size={17} className="text-muted" aria-hidden />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{f.label}</span>
            <VerifyBadge status={claim?.status ?? (f.status === "counted" ? "verified" : "unverified")} />
          </div>
          {(d.observed || d.baseline) && (
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
              {d.baseline && <span>{d.label_baseline ?? "Expected"}: <b className="text-ink">{String(d.baseline)}</b></span>}
              {d.baseline && d.observed && <ArrowRight size={12} aria-hidden />}
              {d.observed && <span>{d.label_observed ?? "Observed"}: <b className="text-ink">{String(d.observed)}</b></span>}
              {d.pct_change !== undefined && <span className="font-semibold" style={{ color }}>+{d.pct_change}%</span>}
            </div>
          )}
        </div>
        <div className="text-right text-base font-bold tabular-nums" style={{ color }}>{f.points > 0 ? `+${f.points}` : "0"}</div>
        {open ? <ChevronDown size={16} className="text-muted" /> : <ChevronRight size={16} className="text-muted" />}
      </button>
      {open && claim && (
        <div className="border-t border-line px-3 py-3">
          <div className="text-sm">{claim.statement}</div>
          <div className="mt-1 text-xs text-muted">Verifier: {claim.verifier_notes}</div>
          <div className="mt-2 flex flex-col gap-2">
            {claim.evidence_ids.map((eid: string) => evById[eid] && <EvidenceCard key={eid} e={evById[eid]} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function EvidenceCard({ e }: { e: Any }) {
  const Icon = SRC_ICON[e.source] ?? FileText;
  const isUrl = /^https?:\/\//.test(e.source_ref);
  return (
    <div className="rounded-md bg-surface-2 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Icon size={13} className="text-accent" aria-hidden />
        <b>{SRC_LABEL[e.source] ?? e.source}</b>
        {e.field && <span className="text-muted">{e.field}</span>}
        {e.value !== null && e.value !== undefined && <code className="rounded bg-surface px-1">{evValue(e.field, e.value)}</code>}
        <span className="ml-auto text-muted">Tier {e.tier} · {relTime(e.retrieved_at)}</span>
      </div>
      {e.excerpt && <div className="mt-1 whitespace-pre-wrap break-words border-l-2 border-line pl-2 text-muted">{e.excerpt}</div>}
      <div className="mt-1 truncate text-muted">{isUrl ? <a className="underline" href={e.source_ref} target="_blank" rel="noreferrer noopener">{e.source_ref}</a> : e.source_ref}</div>
    </div>
  );
}

function ScoreDiff({ risk }: { risk: Any }) {
  return (
    <div className="fade-in mt-3 rounded-lg border border-line bg-low-soft p-3">
      <div className="text-sm font-semibold">Re-scored {risk.previous.score} → {risk.score} <span className="font-normal text-muted">({risk.previous.tier} → {risk.tier})</span></div>
      <ul className="mt-1 flex flex-col gap-0.5 text-xs">
        {risk.diff.map((d: Any) => (
          <li key={d.signal} className="flex justify-between"><span>{d.label}</span><span className="tabular-nums font-semibold">{d.after - d.before > 0 ? "+" : ""}{d.after - d.before}</span></li>
        ))}
      </ul>
    </div>
  );
}

function WhyPanel({ why }: { why: Any }) {
  return (
    <div className="fade-in mt-4 rounded-lg border border-line p-3">
      <div className="mb-2 text-sm font-semibold">Why {why.score}/100? <span className="font-normal text-muted">Each point traces claim → evidence → verification. Weights {why.weights_version}.</span></div>
      <ol className="flex flex-col gap-3">
        {why.steps.map((s: Any) => (
          <li key={s.n} className="text-sm">
            <div className="flex justify-between gap-2"><b>{s.n}. {s.label}</b><span className="tabular-nums">{s.points > 0 ? `+${s.points}` : `0 (${s.status})`}</span></div>
            <div className="text-muted">{s.claim}</div>
            {(s.baseline || s.observed) && <div className="text-xs">Baseline <b>{String(s.baseline ?? "—")}</b> → observed <b>{String(s.observed ?? "—")}</b></div>}
            <div className="text-xs text-muted">Verification: {s.verification}</div>
          </li>
        ))}
      </ol>
      <div className="mt-2 text-xs text-muted">The LLM can investigate, but it cannot manipulate the risk score.</div>
    </div>
  );
}

function Tabs({ tab, setTab, c, evidence, id }: { tab: string; setTab: (t: string) => void; c: Any; evidence: Any[]; id: string }) {
  const tabs = [["evidence", `Evidence · ${evidence.length}`], ["document", "Invoice"], ["checks", "Checks"], ["graph", "Graph"], ["comms", "Messages"], ["audit", "Audit log"]];
  return (
    <div className="card">
      <div className="flex gap-1 overflow-x-auto border-b border-line px-2" role="tablist" aria-label="Case details">
        {tabs.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab -mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-3 text-sm transition-colors duration-150 ${tab === k ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      <div className="p-4">
        {tab === "evidence" && <div className="flex flex-col gap-2">{evidence.map((e) => <EvidenceCard key={e.id} e={e} />)}</div>}
        {tab === "document" && <DocumentTab c={c} />}
        {tab === "checks" && <ChecksTab c={c} />}
        {tab === "graph" && <GraphTab vendorId={c.vendor_id} />}
        {tab === "comms" && <CommsTab c={c} />}
        {tab === "audit" && <AuditTab id={id} />}
      </div>
    </div>
  );
}

function GraphTab({ vendorId }: { vendorId: string | null }) {
  const [g, setG] = useState<Any | null>(null);
  useEffect(() => { if (vendorId) api(`/vendors/${vendorId}/graph`).then(setG); }, [vendorId]);
  if (!vendorId) return <div className="text-sm text-muted">Unknown vendor: no relationship graph.</div>;
  return g ? <RelGraph nodes={g.nodes} edges={g.edges} /> : <Spinner />;
}

function DocumentTab({ c }: { c: Any }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let u: string | null = null;
    if (c.document?.id) fetchBlob(`/api/v1/documents/${c.document.id}/file`).then((b) => { u = URL.createObjectURL(b); setUrl(u); }).catch(() => {});
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [c.document?.id]);
  const fields = Object.entries((c.invoice ?? {}) as Record<string, Any>).filter(([k]) => k !== "line_items");
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="min-h-[420px] overflow-hidden rounded-lg border border-line bg-surface-2">
        {url ? <iframe title="Invoice document" src={url} className="h-[520px] w-full" /> : <div className="p-4 text-sm text-muted">Loading document…</div>}
      </div>
      <div className="flex flex-col gap-3">
        <div>
          <div className="label mb-1">Extracted fields</div>
          <table className="w-full text-sm">
            <tbody>
              {fields.map(([k, f]: [string, Any]) => (
                <tr key={k} className="border-t border-line align-top" title={f.evidence_snippet}>
                  <td className="py-1.5 pr-2 text-muted">{k.replace(/_/g, " ")}</td>
                  <td className="py-1.5 pr-2 font-medium break-all">
                    {typeof f.value === "number" && /total|subtotal|tax/.test(k) ? inr(f.value, true) : String(f.value ?? "—")}
                    {f.via === "human_correction" && (
                      <div className="text-xs font-normal text-medium">Corrected by a person · document said: {f.original ? String(f.original.raw ?? f.original.value ?? "—") : "(not found)"}</div>
                    )}
                  </td>
                  <td className="py-1.5 text-right text-xs tabular-nums" style={{ color: f.confidence < 0.8 ? "var(--medium)" : "var(--muted)" }}>{Math.round((f.confidence ?? 0) * 100)}%{f.via === "human_correction" ? " ✎" : ""}</td>
                </tr>
              ))}
              {(c.invoice?.line_items?.value ?? []).map((li: Any, i: number) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-1.5 pr-2 text-muted">line {i + 1}</td>
                  <td className="py-1.5 pr-2 font-medium" colSpan={2}>{li.description} · {li.qty} × {inr(li.unit_price_minor)} = {inr(li.amount_minor ?? li.qty * li.unit_price_minor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <div className="label mb-1">Deterministic validation</div>
          <ul className="flex flex-col gap-1 text-sm">
            {Object.entries((c.validation ?? {}) as Record<string, Any>).map(([k, v]: [string, Any]) => (
              <li key={k} className="flex items-center gap-2">
                {v.ok === true ? <CheckCircle2 size={14} className="text-low" /> : v.ok === false ? <XCircle size={14} className="text-high" /> : <span className="h-3.5 w-3.5 rounded-full border border-line" />}
                <span>{k.replace(/_/g, " ")}</span>
                <span className="truncate text-xs text-muted">{v.ok === null ? "not present" : Array.isArray(v.detail) ? (v.detail.length ? JSON.stringify(v.detail) : "") : typeof v.detail === "string" ? v.detail : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ChecksTab({ c }: { c: Any }) {
  const plan = c.plan ?? {};
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <div className="label mb-1">Plan</div>
        <div className="text-sm">Priority <b>{plan.priority}</b> · vendor match <b>{plan.vendor_match}</b> · external research <b>{plan.external_research ? "yes" : "skipped"}</b></div>
        {plan.rationale && <div className="mt-1 text-xs text-muted">{plan.rationale}</div>}
        <div className="mt-2 flex flex-wrap gap-1">{(plan.required_checks ?? []).map((x: string) => <span key={x} className="rounded bg-surface-2 px-2 py-0.5 text-xs">{x}</span>)}</div>
        {(plan.missing_info ?? []).length > 0 && <div className="mt-2 text-xs text-medium">Missing: {plan.missing_info.join("; ")}</div>}
        {c.budget && <div className="mt-3 text-xs text-muted">Budget used: {c.budget.web_calls} web calls · {c.budget.llm_calls} LLM calls · {c.budget.seconds}s{c.budget.exhausted?.length ? ` · exhausted: ${c.budget.exhausted.join(", ")}` : ""}</div>}
      </div>
      <div>
        <div className="label mb-1">Checks (what ran, what didn't, why)</div>
        <ul className="flex flex-col gap-1 text-sm">
          {Object.entries((c.checks ?? {}) as Record<string, Any>).map(([k, v]: [string, Any]) => (
            <li key={k} className="flex items-center justify-between gap-2">
              <span>{k.replace(/_/g, " ")}</span>
              <span className="text-xs" style={{ color: v.status === "fired" ? "var(--high)" : v.status === "failed" ? "var(--high)" : v.status === "skipped" ? "var(--medium)" : "var(--low)" }}>
                {v.status}{v.reason ? ` · ${v.reason}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function CommsTab({ c }: { c: Any }) {
  const msgs: Any[] = c.messages ?? [];
  if (!msgs.length && !(c.drafts ?? []).length) return <div className="text-sm text-muted">No vendor communication yet.</div>;
  return (
    <div className="flex flex-col gap-3">
      {(c.drafts ?? []).filter((d: Any) => d.status === "draft").map((d: Any) => <div key={d.id} className="rounded-lg border border-dashed border-line p-3 text-sm"><b>Draft</b> to {d.to_email}: {d.subject}</div>)}
      {msgs.map((m) => (
        <div key={m.id} className="rounded-lg bg-surface-2 p-3 text-sm">
          <div className="text-xs text-muted">{m.direction === "out" ? "Sent" : "Received"} · {m.from} → {m.to} · {relTime(m.at)}</div>
          <div className="font-semibold">{m.subject}</div>
          <div className="mt-1 whitespace-pre-wrap">{m.body}</div>
        </div>
      ))}
    </div>
  );
}

function AuditTab({ id }: { id: string }) {
  const [a, setA] = useState<Any | null>(null);
  useEffect(() => { api(`/cases/${id}/audit`).then(setA); }, [id]);
  if (!a) return <Spinner />;
  return (
    <div>
      <div className={`mb-2 text-xs font-semibold ${a.chain_valid ? "text-low" : "text-high"}`}>{a.chain_valid ? "Hash chain verified — log is intact" : `Hash chain broken at entry ${a.first_bad_id}`}</div>
      <ul className="flex flex-col gap-1 font-mono text-xs">
        {a.items.map((r: Any) => <li key={r.id} className="flex gap-2"><span className="text-muted">{r.ts.slice(0, 19).replace("T", " ")}</span><span className="text-accent">{r.action}</span><span className="truncate text-muted">{r.actor} · {r.hash}</span></li>)}
      </ul>
    </div>
  );
}

function DecisionModal({ title, kind, needReason, id, onDone, onClose, hint }: { title: string; kind: string; needReason: boolean; id: string; onDone: () => void; onClose: () => void; hint?: string }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    try {
      await post(`/cases/${id}/decision`, { decision: kind, reason });
      onDone();
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Modal title={title} onClose={onClose}>
      {hint && <p className="mb-3 text-sm text-muted">{hint}</p>}
      <label className="label" htmlFor="reason">Reason {needReason ? "(required)" : "(optional)"}</label>
      <textarea id="reason" className="input mt-1 h-24" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={kind === "APPROVE" ? "e.g. Bank change confirmed by phone with known contact" : ""} />
      {err && <div className="mt-2 text-sm text-high">{err}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || (needReason && reason.trim().length < 5)} onClick={submit}>{busy && <Spinner />}Confirm</button>
      </div>
    </Modal>
  );
}

function DraftModal({ draft, id, canSend, onClose, onSent }: { draft: Any; id: string; canSend: boolean; onClose: () => void; onSent: () => void }) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [override, setOverride] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (subject !== draft.subject || body !== draft.body) await api(`/cases/${id}/drafts/${draft.id}`, { method: "PATCH", body: JSON.stringify({ subject, body }) });
      await post(`/cases/${id}/drafts/${draft.id}/send`, { override_unverified_recipient: override });
      onSent();
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Modal title="Verification email — review before sending" onClose={onClose} wide>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        To <b>{draft.to_email}</b>
        {draft.recipient_verified
          ? <span className="inline-flex items-center gap-1 rounded bg-low-soft px-1.5 py-0.5 text-xs font-semibold text-low"><ShieldCheck size={12} />Verified vendor-master contact</span>
          : <span className="inline-flex items-center gap-1 rounded bg-high-soft px-1.5 py-0.5 text-xs font-semibold text-high"><ShieldAlert size={12} />From the invoice — may belong to the sender of a suspicious invoice</span>}
      </div>
      <label className="label" htmlFor="subj">Subject</label>
      <input id="subj" className="input mb-3 mt-1" value={subject} onChange={(e) => setSubject(e.target.value)} />
      <label className="label" htmlFor="body">Body</label>
      <textarea id="body" className="input mt-1 h-64 font-mono text-xs" value={body} onChange={(e) => setBody(e.target.value)} />
      <div className="mt-2 text-xs text-muted">Neutral wording is enforced: accusatory language is rejected. No risk scores or internal evidence are shared.</div>
      {!draft.recipient_verified && (
        <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />I confirm this recipient through a separate channel (approver override)</label>
      )}
      {err && <div className="mt-2 text-sm text-high">{err}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Later</button>
        <button className="btn btn-primary" disabled={!canSend || busy} title={canSend ? "" : "Requires approver role"} onClick={send}>{busy ? <Spinner /> : <Mail size={15} />}Approve & send</button>
      </div>
    </Modal>
  );
}

// Same minimum as the server (services.OOB_NOTE_MIN_CHARS).
const OOB_NOTE_MIN = 20;
const OOB_EXAMPLE = "Called R. Kulkarni on the number on file (+91 20 4000 1000); confirmed the new HDFC account and billing domain.";

function OOBModal({ claims, id, onClose, onDone }: { claims: Any[]; id: string; onClose: () => void; onDone: () => void }) {
  const cfg = useAppConfig();
  const [sel, setSel] = useState<string[]>(claims.map((c) => c.id));
  const [method, setMethod] = useState("phone_known_contact");
  const [knownChannel, setKnownChannel] = useState(false);
  const [note, setNote] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const len = note.trim().length;
  const noteShort = len < OOB_NOTE_MIN;
  const blocker = !sel.length
    ? "Select at least one statement you verified."
    : !knownChannel
      ? "Confirm you used a channel already on file."
      : noteShort
        ? `Describe what you verified in at least ${OOB_NOTE_MIN} characters.`
        : "";
  const submit = async () => {
    if (blocker) return;
    setBusy(true);
    setErr(null);
    try {
      await post(`/cases/${id}/out-of-band-confirmation`, { claim_ids: sel, method, note: note.trim(), known_channel: true });
      onDone();
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Modal title="Confirm out-of-band" onClose={onClose} wide>
      <p className="mb-4 text-sm text-muted">A vendor's email can't lower the score on its own: whoever sent the invoice could also control that inbox. Confirm only what you verified yourself through a channel you already trust.</p>

      <fieldset className="mb-4">
        <legend className="label mb-1.5">Statements you verified</legend>
        <ul className="flex flex-col gap-1.5">
          {claims.map((c) => (
            <li key={c.id}><label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={sel.includes(c.id)} onChange={(e) => setSel(e.target.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id))} />{c.statement}</label></li>
          ))}
        </ul>
      </fieldset>

      <label className="label" htmlFor="method">Method</label>
      <select id="method" className="input mb-4 mt-1" value={method} onChange={(e) => setMethod(e.target.value)}>
        <option value="phone_known_contact">Phone call to known contact (number on file)</option>
        <option value="bank_letter">Bank letter verified with the bank</option>
        <option value="in_person">In person</option>
      </select>

      <div className="mb-4 rounded-lg border border-line bg-surface-2 p-3">
        <div className="label mb-2">Before you confirm</div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={knownChannel} onChange={(e) => setKnownChannel(e.target.checked)} />
          <span>I used a phone number or channel <b>already on file</b>, not one from the invoice or the reply.</span>
        </label>
      </div>

      <div className="flex items-end justify-between gap-2">
        <label className="label" htmlFor="note">What did you verify, and with whom?</label>
        {cfg.features.demo && (
          <button type="button" className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline" onClick={() => { setNote(OOB_EXAMPLE); setTouched(true); }}>
            Demo: fill example
          </button>
        )}
      </div>
      <textarea
        id="note"
        className="input mt-1 h-24 placeholder:text-muted/60"
        value={note}
        placeholder={`e.g. ${OOB_EXAMPLE}`}
        aria-invalid={touched && noteShort}
        aria-describedby="note-hint"
        onChange={(e) => setNote(e.target.value)}
        onBlur={() => setTouched(true)}
      />
      <div id="note-hint" className={`mt-1 flex justify-between gap-2 text-xs ${touched && noteShort ? "text-high" : "text-muted"}`}>
        <span>{touched && noteShort ? "Too short: say who you contacted, on which number on file, and what they confirmed." : "Name the person, the channel on file, and what they confirmed. This goes into the audit log."}</span>
        <span className="shrink-0 tabular-nums">{len}/{OOB_NOTE_MIN}</span>
      </div>
      {err && <div role="alert" className="mt-2 rounded-lg bg-high-soft p-2.5 text-sm text-high">{err}</div>}

      <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
        {blocker && <span id="oob-blocker" className="mr-auto text-xs text-muted">{blocker}</span>}
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !!blocker} title={blocker} aria-describedby={blocker ? "oob-blocker" : undefined} onClick={submit}>
          {busy ? <Spinner /> : <PhoneCall size={15} />}Confirm & re-score
        </button>
      </div>
    </Modal>
  );
}

const CLOSE_MIN = 10;
const CLOSE_EXAMPLE = "Bank-account change verified out-of-band with the known contact on file.";

function CloseModal({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const cfg = useAppConfig();
  const [outcome, setOutcome] = useState("CLEARED");
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const short = resolution.trim().length < CLOSE_MIN;
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await post(`/cases/${id}/close`, { outcome, resolution: resolution.trim() });
      onDone();
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Modal title="Close case" onClose={onClose}>
      <p className="mb-3 text-sm text-muted">Only human-confirmed outcomes become case memory. The next invoice from this vendor will reference it.</p>
      <label className="label" htmlFor="outcome">Outcome</label>
      <select id="outcome" className="input mb-3 mt-1" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
        <option value="CLEARED">Cleared — anomalies explained and verified</option>
        <option value="CONFIRMED_ISSUE">Confirmed issue — weight on future invoices</option>
        <option value="INCONCLUSIVE">Inconclusive</option>
      </select>
      <div className="flex items-end justify-between gap-2">
        <label className="label" htmlFor="res">Resolution</label>
        {cfg.features.demo && <button type="button" className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline" onClick={() => setResolution(CLOSE_EXAMPLE)}>Demo: fill example</button>}
      </div>
      <textarea id="res" className="input mt-1 h-20 placeholder:text-muted/60" value={resolution} placeholder={`e.g. ${CLOSE_EXAMPLE}`} aria-describedby="res-hint" onChange={(e) => setResolution(e.target.value)} />
      <div id="res-hint" className="mt-1 text-xs text-muted">What was established, and how. At least {CLOSE_MIN} characters.</div>
      {err && <div role="alert" className="mt-2 rounded-lg bg-high-soft p-2.5 text-sm text-high">{err}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || short} title={short ? `Write a resolution of at least ${CLOSE_MIN} characters.` : ""} onClick={submit}>{busy && <Spinner />}Close & save to memory</button>
      </div>
    </Modal>
  );
}
