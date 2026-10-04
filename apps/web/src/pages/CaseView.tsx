import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, ArrowRight, Brain, Building2, CheckCircle2, ChevronDown, ChevronRight, ClipboardList, Download, FileText, Globe, HelpCircle,
  History, Landmark, Mail, MailCheck, PhoneCall, Search, ShieldAlert, ShieldCheck, ThumbsDown, ThumbsUp, UserCheck, XCircle,
} from "lucide-react";
import { api, can, fetchBlob, post, token, type Role } from "../lib/api";
import { useAuth } from "../lib/auth";
import { evValue, inr, isoDate, relTime, RUNNING, tierColor } from "../lib/format";
import { Gauge } from "../components/Gauge";
import { ActivityLog, Timeline, type AgentEvent } from "../components/Timeline";
import { Modal, Skeleton, Spinner, StatusChip, TierChip, VerifyBadge } from "../components/ui";

type Any = Record<string, any>;

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
  const nav = useNavigate();
  const [c, setC] = useState<Any | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [evidence, setEvidence] = useState<Any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState("evidence");
  const [why, setWhy] = useState<Any | null>(null);
  const [modal, setModal] = useState<null | "approve" | "reject" | "verify" | "draft" | "oob" | "close" | "investigate">(null);
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
    const es = new EventSource(`/api/v1/cases/${id}/events?token=${encodeURIComponent(token() ?? "")}`);
    es.onmessage = (m) => {
      const e: AgentEvent = JSON.parse(m.data);
      setEvents((prev) => (prev.some((p) => p.seq === e.seq) ? prev : [...prev, e]));
      if (["agent.completed", "risk.updated", "gate.waiting", "decision.recorded", "action.sent", "vendor.reply_received", "case.closed", "agent.failed", "verification.confirmed_out_of_band"].includes(e.type)) {
        window.clearTimeout(refetchTimer.current);
        refetchTimer.current = window.setTimeout(load, 250);
      }
    };
    return () => es.close();
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

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4">
      {/* Header */}
      <div className="card flex flex-wrap items-center justify-between gap-4 px-5 py-4">
        <div className="min-w-0">
          <button className="mb-1 inline-flex items-center gap-1 text-xs text-muted hover:text-ink" onClick={() => nav("/dashboard")}><ArrowLeft size={13} />Queue</button>
          <h1 className="text-lg font-bold">
            Case #{c.number} · {c.invoice_number ?? c.document?.filename} · {c.vendor_name ?? "Unknown vendor"} · <span className="tabular-nums">{inr(c.amount?.amount_minor)}</span>
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted">
            <StatusChip status={c.status} />
            {c.partial && <span className="font-medium text-medium">Some checks did not complete — confidence reduced</span>}
            {c.sources_checked > 0 && <span>{c.sources_checked + 2} sources checked</span>}
            {c.duration_ms && <span>{(c.duration_ms / 1000).toFixed(1)}s investigation</span>}
            {c.depth > 0 && <span>Investigated further ×{c.depth}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {risk.tier && !running && <TierChip tier={risk.tier} score={risk.score} />}
          <a className="btn !py-1.5 text-xs" onClick={async (e) => { e.preventDefault(); const b = await fetchBlob(`/api/v1/cases/${id}/export?format=json`); const u = URL.createObjectURL(b); const a = document.createElement("a"); a.href = u; a.download = `probity-case-${c.number}.json`; a.click(); URL.revokeObjectURL(u); }} href="#"><Download size={13} />Export</a>
        </div>
      </div>

      {memoryClaims.map((m) => (
        <div key={m.id} className="fade-in flex items-start gap-3 rounded-xl border border-line bg-accent-soft px-4 py-3 text-sm">
          <Brain size={18} className="mt-0.5 shrink-0 text-accent" />
          <div><div className="font-semibold text-accent">From case memory</div><div>{m.statement}</div></div>
        </div>
      ))}

      <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)_300px]">
        {/* Timeline */}
        <div className="card h-fit p-3">
          <div className="label mb-2 px-2">Agent timeline</div>
          <Timeline events={events} running={running} />
        </div>

        {/* Findings */}
        <div className="flex min-w-0 flex-col gap-4">
          <div className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="font-semibold">
                {running ? "Findings (streaming…)" : scored.filter((x) => x.points > 0).length ? `${scored.filter((x) => x.points > 0).length} verified anomal${scored.filter((x) => x.points > 0).length === 1 ? "y" : "ies"}` : "Findings"}
              </div>
              {!running && contribs.length > 0 && <button className="btn !py-1 text-xs" onClick={openWhy} aria-expanded={!!why}><HelpCircle size={14} />Why?</button>}
            </div>
            {running && scored.length === 0 && <div className="flex items-center gap-2 py-6 text-sm text-muted"><Spinner />Agents are investigating — findings appear once verified.</div>}
            {!running && scored.length === 0 && <div className="py-4 text-sm text-muted">No risk indicators fired across the checks that ran.</div>}
            <div className="flex flex-col gap-2">
              {scored.map((f) => <Finding key={f.signal} f={f} claim={claimById[f.claim_id]} evById={evById} />)}
            </div>
            {risk.diff?.length > 0 && <ScoreDiff risk={risk} />}
            {why && <WhyPanel why={why} />}
          </div>

          {(pendingReply.length > 0 || replyClaims.length > 0) && (
            <div className="card fade-in p-4">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-2 font-semibold"><Mail size={16} />Vendor reply</div>
                {pendingReply.length > 0 && <span className="text-xs text-medium">Unverified — awaiting approver</span>}
              </div>
              {(c.messages ?? []).filter((m: Any) => m.direction === "in").slice(-1).map((m: Any) => (
                <div key={m.id} className="mb-3 rounded-lg bg-surface-2 p-3 text-sm">
                  <div className="mb-1 text-xs text-muted">From <b>{m.from}</b> · {relTime(m.at)}</div>
                  <div className="whitespace-pre-wrap">{m.body}</div>
                  {m.indicators?.length > 0 && (
                    <ul className="mt-2 flex flex-col gap-1">
                      {m.indicators.map((i: string) => <li key={i} className="flex items-center gap-1.5 text-xs font-medium text-high"><ShieldAlert size={13} />{i}</li>)}
                    </ul>
                  )}
                </div>
              ))}
              <ul className="flex flex-col gap-1.5">
                {replyClaims.map((r) => <li key={r.id} className="flex items-start justify-between gap-2 text-sm"><span>{r.statement}</span><VerifyBadge status={r.status} /></li>)}
              </ul>
              {pendingReply.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button className="btn btn-primary" disabled={!!dis()} title={dis()} onClick={() => setModal("oob")}><PhoneCall size={15} />Confirm out-of-band</button>
                  <span className="text-xs text-muted">The score changes only after an approver confirms through a known channel.</span>
                </div>
              )}
            </div>
          )}

          {(otherInfo.length > 0 || unconfirmedInfo.length > 0) && (
            <div className="card p-4">
              <div className="label mb-2">Other findings</div>
              <ul className="flex flex-col gap-1.5">
                {[...otherInfo, ...unconfirmedInfo].map((x) => (
                  <li key={x.id} className="flex items-start justify-between gap-3 text-sm">
                    <span>{x.statement}</span>
                    {x.status === "verified" ? <span className="text-[11px] text-muted">0 pts</span> : <VerifyBadge status={x.status} />}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Tabs tab={tab} setTab={setTab} c={c} evidence={evidence} events={events} id={id} />
        </div>

        {/* Recommendation + decision bar */}
        <div className="flex flex-col gap-4 lg:sticky lg:top-4 lg:h-fit">
          <div className="card flex flex-col items-center p-4">
            <Gauge score={running && !risk.score && risk.score !== 0 ? null : risk.score ?? null} tier={risk.tier} provisional={running} />
            {risk.weights_version && <div className="mt-1 text-[11px] text-muted">Computed by code · weights {risk.weights_version}</div>}
          </div>
          <div className="card p-4">
            <div className="label mb-1">Recommendation</div>
            {running ? <div className="flex items-center gap-2 text-sm text-muted"><Spinner />Pending</div> : (
              <>
                <div className="text-lg font-bold" style={{ color: risk.tier ? tierColor[risk.tier] : undefined }}>
                  {c.status === "AUTO_CLEARED" ? "AUTO-CLEARED" : (c.recommendation?.action ?? "—").replace("_", " ")}
                </div>
                <p className="mt-1 text-sm text-muted">{c.summary}</p>
                {gate && !gate.auto_cleared && gate.reasons?.length > 0 && c.status === "AWAITING_HUMAN" && (
                  <div className="mt-2 text-xs text-muted">Held: {gate.reasons.join("; ")}</div>
                )}
                {gate?.dual_approval && awaiting && <div className="mt-2 rounded-md bg-high-soft px-2 py-1 text-xs font-medium text-high">Two approvers required{c.recommendation?.approvals?.length ? ` · ${c.recommendation.approvals.length} of 2 received` : ""}</div>}
              </>
            )}
          </div>

          <div className="card flex flex-col gap-2 p-4" role="group" aria-label="Decision">
            <div className="label">Human decision</div>
            <button className="btn btn-primary" disabled={!!dis()} title={dis()} onClick={() => setModal("verify")}><MailCheck size={15} />Request vendor verification</button>
            <div className="grid grid-cols-2 gap-2">
              <button className="btn" disabled={!!dis()} title={dis()} onClick={() => setModal("approve")}><ThumbsUp size={15} />Approve</button>
              <button className="btn btn-danger" disabled={!!dis()} title={dis()} onClick={() => setModal("reject")}><ThumbsDown size={15} />Reject</button>
            </div>
            <button className="btn" disabled={!!dis() || c.depth >= 2} title={c.depth >= 2 ? "Depth limit reached (2)" : dis()} onClick={() => setModal("investigate")}><Search size={15} />Investigate further</button>
            {["APPROVED", "REJECTED", "AUTO_CLEARED"].includes(c.status) && (
              <button className="btn" disabled={!can(role, c.status === "AUTO_CLEARED" ? "accountant" : "approver")} onClick={() => setModal("close")}><CheckCircle2 size={15} />Close case & save to memory</button>
            )}
            {!isApprover && <div className="text-xs text-muted">Signed in as {role}. Decisions require an approver.</div>}
            <div className="text-[11px] text-muted">Probity never executes payments.</div>
          </div>

          {c.status === "AWAITING_VENDOR" && sent && (
            <div className="card fade-in flex flex-col gap-2 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold"><MailCheck size={16} className="text-accent" />Verification email sent</div>
              <div className="text-xs text-muted">To {sent.to_email} · follow-up {isoDate(sent.followup_at)}</div>
              <div className="label mt-2">Simulated vendor inbox</div>
              <button className="btn" onClick={() => post(`/demo/vendor-reply/${id}?kind=legit`).then(load)}>Deliver vendor reply</button>
              <button className="btn !text-xs text-muted" onClick={() => post(`/demo/vendor-reply/${id}?kind=spoof`).then(load)}>Deliver spoofed reply (lookalike domain)</button>
            </div>
          )}
        </div>
      </div>

      {modal === "approve" && <DecisionModal title="Approve payment" kind="APPROVE" needReason={["HIGH", "CRITICAL"].includes(risk.tier)} id={id} onDone={() => { setModal(null); load(); }} onClose={() => setModal(null)} />}
      {modal === "reject" && <DecisionModal title="Reject invoice" kind="REJECT" needReason id={id} onDone={() => { setModal(null); load(); }} onClose={() => setModal(null)} />}
      {modal === "investigate" && <DecisionModal title="Investigate further" kind="INVESTIGATE_FURTHER" needReason={false} id={id} onDone={() => { setModal(null); setEvents([]); load(); }} onClose={() => setModal(null)} />}
      {modal === "verify" && <DecisionModal title="Request vendor verification" kind="REQUEST_VERIFICATION" needReason={false} id={id} onDone={async () => { await load(); setModal("draft"); }} onClose={() => setModal(null)}
        hint="The Action agent drafts a neutral email to the vendor's verified contact. Nothing is sent until you approve the draft." />}
      {modal === "draft" && draft && <DraftModal draft={draft} id={id} canSend={isApprover} onClose={() => setModal(null)} onSent={() => { setModal(null); load(); }} />}
      {modal === "oob" && <OOBModal claims={pendingReply} id={id} onClose={() => setModal(null)} onDone={() => { setModal(null); load(); }} />}
      {modal === "close" && <CloseModal id={id} onClose={() => setModal(null)} onDone={() => { setModal(null); load(); }} />}
      {draft && modal === null && awaiting && (
        <button className="btn btn-primary fixed bottom-5 right-5 shadow-lg" onClick={() => setModal("draft")}><Mail size={15} />Review draft email</button>
      )}
    </div>
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

function Tabs({ tab, setTab, c, evidence, events, id }: { tab: string; setTab: (t: string) => void; c: Any; evidence: Any[]; events: AgentEvent[]; id: string }) {
  const tabs = [["evidence", `Evidence (${evidence.length})`], ["document", "Document"], ["checks", "Plan & checks"], ["comms", "Communications"], ["audit", "Audit"], ["activity", "Activity"]];
  return (
    <div className="card">
      <div className="flex gap-1 overflow-x-auto border-b border-line px-2" role="tablist">
        {tabs.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab whitespace-nowrap px-3 py-2.5 text-sm ${tab === k ? "border-b-2 border-accent font-semibold text-accent" : "text-muted"}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      <div className="p-4">
        {tab === "evidence" && <div className="flex flex-col gap-2">{evidence.map((e) => <EvidenceCard key={e.id} e={e} />)}</div>}
        {tab === "document" && <DocumentTab c={c} />}
        {tab === "checks" && <ChecksTab c={c} />}
        {tab === "comms" && <CommsTab c={c} />}
        {tab === "audit" && <AuditTab id={id} />}
        {tab === "activity" && <ActivityLog events={events} />}
      </div>
    </div>
  );
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
                  <td className="py-1.5 pr-2 font-medium break-all">{typeof f.value === "number" && /total|subtotal|tax/.test(k) ? inr(f.value, true) : String(f.value ?? "—")}</td>
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

function OOBModal({ claims, id, onClose, onDone }: { claims: Any[]; id: string; onClose: () => void; onDone: () => void }) {
  const [sel, setSel] = useState<string[]>(claims.map((c) => c.id));
  const [method, setMethod] = useState("phone_known_contact");
  const [note, setNote] = useState("Called R. Kulkarni on the number on file (+91 20 4000 1000); confirmed new HDFC account and billing domain.");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    try {
      await post(`/cases/${id}/out-of-band-confirmation`, { claim_ids: sel, method, note });
      onDone();
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Modal title="Confirm out-of-band" onClose={onClose} wide>
      <p className="mb-3 text-sm text-muted">A vendor's email can't lower the score on its own: whoever sent the suspicious invoice could also control that inbox. Confirm only what you verified through a channel you already trust.</p>
      <ul className="mb-3 flex flex-col gap-1.5">
        {claims.map((c) => (
          <li key={c.id}><label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={sel.includes(c.id)} onChange={(e) => setSel(e.target.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id))} />{c.statement}</label></li>
        ))}
      </ul>
      <label className="label" htmlFor="method">Method</label>
      <select id="method" className="input mb-3 mt-1" value={method} onChange={(e) => setMethod(e.target.value)}>
        <option value="phone_known_contact">Phone call to known contact (number on file)</option>
        <option value="bank_letter">Bank letter verified with the bank</option>
        <option value="in_person">In person</option>
      </select>
      <label className="label" htmlFor="note">What did you verify?</label>
      <textarea id="note" className="input mt-1 h-20" value={note} onChange={(e) => setNote(e.target.value)} />
      {err && <div className="mt-2 text-sm text-high">{err}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !sel.length || note.trim().length < 5} onClick={submit}>{busy ? <Spinner /> : <PhoneCall size={15} />}Confirm & re-score</button>
      </div>
    </Modal>
  );
}

function CloseModal({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const [outcome, setOutcome] = useState("CLEARED");
  const [resolution, setResolution] = useState("Bank-account change verified out-of-band with known contact.");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="Close case" onClose={onClose}>
      <p className="mb-3 text-sm text-muted">Only human-confirmed outcomes become case memory. The next invoice from this vendor will reference it.</p>
      <label className="label" htmlFor="outcome">Outcome</label>
      <select id="outcome" className="input mb-3 mt-1" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
        <option value="CLEARED">Cleared — anomalies explained and verified</option>
        <option value="CONFIRMED_ISSUE">Confirmed issue — weight on future invoices</option>
        <option value="INCONCLUSIVE">Inconclusive</option>
      </select>
      <label className="label" htmlFor="res">Resolution</label>
      <textarea id="res" className="input mt-1 h-20" value={resolution} onChange={(e) => setResolution(e.target.value)} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy} onClick={async () => { setBusy(true); await post(`/cases/${id}/close`, { outcome, resolution }); onDone(); }}>{busy && <Spinner />}Close & save to memory</button>
      </div>
    </Modal>
  );
}
