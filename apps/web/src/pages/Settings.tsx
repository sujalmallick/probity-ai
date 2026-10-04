import { useEffect, useState, type ReactNode } from "react";
import { Lock } from "lucide-react";
import { api, can, errMsg } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr, relTime, tierColor, tierSoft } from "../lib/format";
import { LoadError, Skeleton, Spinner } from "../components/ui";

// What each check looks for, in plain words. Keys match the risk engine's signal names.
const SIGNALS: Record<string, { label: string; what: string }> = {
  bank_account_changed: { label: "Bank account changed", what: "The invoice pays into an account that isn't a verified account for this vendor." },
  price_anomaly: { label: "Higher prices", what: "Unit prices are well above what this vendor charged before." },
  new_domain: { label: "New email domain", what: "The sender's email domain was registered only recently." },
  identity_mismatch: { label: "Vendor details don't match", what: "Name, GSTIN or PAN differ from your vendor master." },
  duplicate_invoice: { label: "Duplicate invoice", what: "The same invoice number or document was seen before." },
  address_mismatch: { label: "Different address", what: "The address differs from your vendor master." },
  missing_po: { label: "No purchase order", what: "No matching purchase order was found." },
  suspicious_instruction_in_document: { label: "Instructions hidden in the document", what: "The file contains text that tries to instruct the software." },
  prior_confirmed_issue: { label: "Past confirmed problem", what: "An earlier case with this vendor, account or domain was confirmed as a problem." },
  quantity_po_mismatch: { label: "More than was ordered", what: "Quantities exceed the purchase order." },
  temporal_anomaly: { label: "Odd dates", what: "Dates don't add up, for example due before it was issued." },
  no_history: { label: "No history to compare", what: "There are no earlier invoices from this vendor yet." },
  statistical_anomaly: { label: "Unusual amount", what: "The amount is far outside this vendor's usual range (up to these points)." },
  round_sum: { label: "Round-number total", what: "The total is a suspiciously round number." },
  shared_attribute: { label: "Shared with another vendor", what: "A bank account, domain or address is also used by a different vendor." },
};
const CORE = ["bank_account_changed", "price_anomaly", "new_domain", "identity_mismatch", "duplicate_invoice", "address_mismatch", "missing_po"];
const TIER_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;

export default function SettingsPage() {
  const { user } = useAuth();
  const [p, setP] = useState<any | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const load = () => api("/workspace/policy").then((r) => { setP(r); setLoadErr(null); }).catch((e) => setLoadErr(errMsg(e)));
  useEffect(() => { load(); }, []);
  if (!p) {
    return (
      <div className="mx-auto flex max-w-4xl flex-col gap-4">
        <h1 className="page-title">Risk policy</h1>
        {loadErr ? <LoadError error={`Could not load the policy. ${loadErr}`} onRetry={load} /> : (
          <div className="flex flex-col gap-4" aria-busy><Skeleton className="h-28" /><Skeleton className="h-72" /><Skeleton className="h-64" /></div>
        )}
      </div>
    );
  }
  const owner = can(user?.role, "owner");
  const save = async (key: string, patch: Record<string, unknown>) => {
    setSaving(key);
    setMsg(null);
    try {
      await api("/workspace/policy", { method: "PUT", body: JSON.stringify(patch) });
      setMsg({ ok: true, text: "Saved. New investigations use this right away, and the change is in the audit log." });
      await load();
    } catch (e: any) {
      setMsg({ ok: false, text: errMsg(e) });
    } finally {
      setSaving(null);
    }
  };
  const weights: Record<string, number> = p.weights ?? {};
  const reasonTiers: string[] = p.require_reason_for_approve_tiers ?? [];
  const meaning: Record<string, string> = {
    LOW: p.auto_clear_enabled ? "Cleared without a person if every required check finished and the amount is within the limit." : "A person still reviews it (auto-clear is off).",
    MEDIUM: "Sent to a person to review.",
    HIGH: `Payment held for an approver${reasonTiers.includes("HIGH") ? ", who must write a reason to approve" : ""}.`,
    CRITICAL: `Payment held; two approvers must agree${reasonTiers.includes("CRITICAL") ? ", with a written reason" : ""}.`,
  };
  const extra = Object.keys(weights).filter((k) => !CORE.includes(k) && weights[k] > 0);
  const flagOnly = Object.keys(weights).filter((k) => !CORE.includes(k) && !(weights[k] > 0));
  const maxW = Math.max(...Object.values(weights), 1);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <div>
        <h1 className="page-title">Risk policy</h1>
        <p className="text-sm text-muted">How Probity decides which invoices a person has to look at. The score is calculated by fixed rules. The AI gathers evidence but can't change the score.</p>
      </div>
      {msg && <div role={msg.ok ? "status" : "alert"} className={`rounded-lg p-3 text-sm ${msg.ok ? "bg-low-soft text-low" : "bg-high-soft text-high"}`}>{msg.text}</div>}

      {/* 1. The scale */}
      <section className="card p-5" aria-labelledby="scale-title">
        <h2 id="scale-title" className="text-base font-semibold">1. Every invoice gets a score from 0 to 100</h2>
        <p className="mt-1 text-sm text-muted">The higher the score, the more care it needs. The level decides what happens next.</p>
        <div className="mt-4 flex h-2.5 overflow-hidden rounded-full" aria-hidden>
          <div style={{ flex: 30, background: tierColor.LOW }} /><div style={{ flex: 30, background: tierColor.MEDIUM }} />
          <div style={{ flex: 20, background: tierColor.HIGH }} /><div style={{ flex: 21, background: tierColor.CRITICAL }} />
        </div>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {TIER_ORDER.map((t) => (
            <li key={t} className="rounded-lg border border-line p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="rounded-full px-2 py-0.5 text-xs font-semibold" style={{ color: tierColor[t], background: tierSoft[t] }}>{t[0] + t.slice(1).toLowerCase()}</span>
                <span className="text-xs tabular-nums text-muted">{p.tiers?.[t] ?? ""}</span>
              </div>
              <p className="mt-2 text-sm">{meaning[t]}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* 2. What adds points */}
      <section className="card p-5" aria-labelledby="points-title">
        <h2 id="points-title" className="text-base font-semibold">2. What adds points</h2>
        <p className="mt-1 text-sm text-muted">
          Points count only when the finding is backed by evidence. A check that couldn't run adds nothing; it holds the invoice for a person instead.
        </p>
        <SignalGroup title="Main checks" note="These add up to 100." keys={CORE.filter((k) => k in weights)} weights={weights} max={maxW} />
        {extra.length > 0 && <SignalGroup title="Extra checks" note="Added on top; the total never goes above 100." keys={extra} weights={weights} max={maxW} />}
        {flagOnly.length > 0 && <SignalGroup title="Flag for review only" note="No points. Shown to the reviewer; a detail shared with another vendor also stops auto-clear." keys={flagOnly} weights={weights} max={maxW} />}
        <p className="mt-4 text-xs text-muted">Weights version {p.weights_version}. The weights are fixed and can't be edited here.</p>
      </section>

      {/* 3. When a person decides */}
      <section className="card overflow-hidden" aria-labelledby="gate-title">
        <div className="p-5 pb-2">
          <h2 id="gate-title" className="text-base font-semibold">3. When a person has to decide</h2>
          <p className="mt-1 text-sm text-muted">{owner ? "You can change these as the workspace owner. Every change is audited." : "Only the workspace owner can change these."}</p>
        </div>
        <ul className="divide-y divide-line">
          <Rule title="Auto-clear low-risk invoices" desc="Let LOW invoices through without a person when every required check finished and nothing was found.">
            <Toggle on={!!p.auto_clear_enabled} disabled={!owner || saving !== null} busy={saving === "auto"} label="Auto-clear low-risk invoices" onChange={(v) => save("auto", { auto_clear_enabled: v })} />
          </Rule>
          <AmountRule
            title="Auto-clear limit" desc="Invoices above this amount always go to a person, even when they score LOW."
            minor={p.auto_clear_max_amount_minor} owner={owner} busy={saving === "acl"} disabled={saving !== null}
            onSave={(m) => save("acl", { auto_clear_max_amount_minor: m })}
          />
          <AmountRule
            title="Web checks above" desc="Public web research (reputation, news) runs only for invoices above this amount."
            minor={p.external_research_amount_minor} owner={owner} busy={saving === "web"} disabled={saving !== null}
            onSave={(m) => save("web", { external_research_amount_minor: m })}
          />
          <AmountRule
            title="Two approvers from" desc="At or above this amount, or for any CRITICAL invoice, two different approvers must approve."
            minor={p.dual_approval_amount_minor} owner={owner} busy={saving === "dual"} disabled={saving !== null}
            onSave={(m) => save("dual", { dual_approval_amount_minor: m })}
          />
          <Rule title="Written reason to approve" desc="Approvers must explain an approval when an invoice reached these levels.">
            <span className="flex gap-1">{reasonTiers.length ? reasonTiers.map((t) => <span key={t} className="rounded-full px-2 py-0.5 text-xs font-semibold" style={{ color: tierColor[t], background: tierSoft[t] }}>{t}</span>) : <span className="text-sm text-muted">Never</span>}</span>
          </Rule>
          {p.previously_flagged_blocks_auto_clear && (
            <Rule title="Previously flagged vendors" desc="Vendors with a past confirmed problem are never auto-cleared.">
              <span className="text-sm text-muted">Always on</span>
            </Rule>
          )}
          <Rule title="Two-step sign-in for approvals" desc="Approvals, sending emails and out-of-band confirmations need multi-factor sign-in. Turn on once approvers have set it up.">
            <Toggle on={!!p.require_mfa_for_approvals} disabled={!owner || saving !== null} busy={saving === "mfa"} label="Require multi-factor sign-in for approvals" onChange={(v) => save("mfa", { require_mfa_for_approvals: v })} />
          </Rule>
        </ul>
        {p.reviewed_at && <p className="border-t border-line px-5 py-3 text-xs text-muted">Last changed {relTime(p.reviewed_at)}</p>}
      </section>
    </div>
  );
}

function SignalGroup({ title, note, keys, weights, max }: { title: string; note: string; keys: string[]; weights: Record<string, number>; max: number }) {
  return (
    <div className="mt-5">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2"><h3 className="text-sm font-semibold">{title}</h3><span className="text-xs text-muted">{note}</span></div>
      <ul className="divide-y divide-line rounded-lg border border-line">
        {keys.map((k) => {
          const s = SIGNALS[k] ?? { label: k.replace(/_/g, " "), what: "" };
          const w = weights[k] ?? 0;
          return (
            <li key={k} className="flex items-center gap-4 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{s.label}</div>
                {s.what && <div className="text-xs text-muted">{s.what}</div>}
              </div>
              <div className="hidden w-28 sm:block" aria-hidden>
                <div className="h-1.5 rounded-full bg-surface-2"><div className="h-1.5 rounded-full bg-ink/70" style={{ width: `${(w / max) * 100}%` }} /></div>
              </div>
              <div className="w-12 text-right text-sm font-semibold tabular-nums">{w > 0 ? `+${w}` : "0"}</div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Rule({ title, desc, children }: { title: string; desc: string; children: ReactNode }) {
  return (
    <li className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-muted">{desc}</div>
      </div>
      <div className="shrink-0">{children}</div>
    </li>
  );
}

function Toggle({ on, disabled, busy, label, onChange }: { on: boolean; disabled: boolean; busy: boolean; label: string; onChange: (v: boolean) => void }) {
  return (
    <span className="inline-flex items-center gap-2">
      {busy && <Spinner size={13} />}
      <button
        role="switch" aria-checked={on} aria-label={label} disabled={disabled} title={disabled && !busy ? "Only the owner can change this" : ""}
        onClick={() => onChange(!on)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-line transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 ${on ? "bg-accent" : "bg-surface-2"}`}
      >
        <span className={`inline-block h-4.5 w-4.5 rounded-full shadow transition-transform duration-150 ${on ? "translate-x-[22px] bg-accent-fg" : "translate-x-[3px] bg-muted"}`} />
      </button>
      <span className="w-7 text-xs text-muted">{on ? "On" : "Off"}</span>
    </span>
  );
}

function AmountRule({ title, desc, minor, owner, busy, disabled, onSave }: {
  title: string; desc: string; minor: number; owner: boolean; busy: boolean; disabled: boolean; onSave: (minor: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!busy) setEditing(false); }, [minor, busy]);
  const start = () => { setVal(String(Math.round(minor / 100))); setErr(null); setEditing(true); };
  const submit = () => {
    const rupees = Number(val.replace(/[,\s₹]/g, ""));
    if (!Number.isFinite(rupees) || rupees < 0 || !Number.isInteger(rupees)) return setErr("Enter a whole number of rupees.");
    onSave(rupees * 100);
  };
  return (
    <Rule title={title} desc={desc}>
      {editing ? (
        <form className="flex flex-col items-end gap-1" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted">₹</span>
            <input className="input !w-36 !py-1.5 tabular-nums" inputMode="numeric" aria-label={`${title} in rupees`} value={val} onChange={(e) => { setVal(e.target.value); setErr(null); }} autoFocus />
            <button type="submit" className="btn btn-primary !py-1.5 text-xs" disabled={busy}>{busy && <Spinner size={12} />}Save</button>
            <button type="button" className="btn !py-1.5 text-xs" onClick={() => setEditing(false)}>Cancel</button>
          </div>
          {err && <span role="alert" className="text-xs text-high">{err}</span>}
        </form>
      ) : (
        <span className="flex items-center gap-3">
          <span className="text-sm font-semibold tabular-nums">{inr(minor)}</span>
          {owner
            ? <button className="btn !py-1 text-xs" disabled={disabled} onClick={start}>Change</button>
            : <Lock size={13} className="text-muted" aria-label="Only the owner can change this" />}
        </span>
      )}
    </Rule>
  );
}
