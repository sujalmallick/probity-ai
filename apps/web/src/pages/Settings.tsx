import { useEffect, useState } from "react";
import { api, can } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr } from "../lib/format";

export default function SettingsPage() {
  const { user } = useAuth();
  const [p, setP] = useState<any | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = () => api("/workspace/policy").then(setP);
  useEffect(() => { load(); }, []);
  if (!p) return null;
  const owner = can(user?.role, "owner");
  const save = async (patch: Record<string, unknown>) => {
    try {
      await api("/workspace/policy", { method: "PUT", body: JSON.stringify(patch) });
      setMsg({ ok: true, text: "Saved. The change is in the audit log." });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    }
  };
  const core = ["bank_account_changed", "price_anomaly", "new_domain", "identity_mismatch", "duplicate_invoice", "address_mismatch", "missing_po"];
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="page-title">Risk policy</h1>
        <p className="text-sm text-muted">Agents discover signals; code decides the score. Weights are versioned ({p.weights_version}); changes are owner-only and audited.</p>
      </div>
      {msg && <div role={msg.ok ? "status" : "alert"} className={`rounded-lg p-3 text-sm ${msg.ok ? "bg-low-soft text-low" : "bg-high-soft text-high"}`}>{msg.text}</div>}
      <div className="grid gap-4 md:grid-cols-2">
        <div className="card p-4">
          <div className="label mb-2">Weights</div>
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(p.weights).map(([k, v]: [string, any]) => (
                <tr key={k} className="border-t border-line">
                  <td className="py-1.5">{k.replace(/_/g, " ")}{!core.includes(k) && <span className="ml-1 text-[11px] text-muted">supplementary</span>}</td>
                  <td className="py-1.5 text-right font-semibold tabular-nums">+{v}</td>
                </tr>
              ))}
              <tr className="border-t border-line"><td className="py-1.5 text-muted">core total</td><td className="py-1.5 text-right font-bold">{core.reduce((a, k) => a + (p.weights[k] ?? 0), 0)}</td></tr>
            </tbody>
          </table>
          <div className="mt-3 text-xs text-muted">Tiers: {Object.entries(p.tiers).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
        </div>
        <div className="flex flex-col gap-4">
          <div className="card p-4">
            <div className="label mb-2">Human gate</div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" disabled={!owner} title={owner ? "" : "Requires owner role"} checked={p.auto_clear_enabled} onChange={(e) => save({ auto_clear_enabled: e.target.checked })} />
              Auto-clear LOW-risk invoices when every check completed and no indicator fired
            </label>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" disabled={!owner} title={owner ? "" : "Requires owner role"} checked={!!p.require_mfa_for_approvals} onChange={(e) => save({ require_mfa_for_approvals: e.target.checked })} />
              Require multi-factor sign-in for approvals, sends and out-of-band confirmations
            </label>
            <ul className="mt-3 flex flex-col gap-1 text-sm text-muted">
              <li>Auto-clear limit: <b className="text-ink">{inr(p.auto_clear_max_amount_minor)}</b></li>
              <li>Web research above: <b className="text-ink">{inr(p.external_research_amount_minor)}</b></li>
              <li>Dual approval at or above: <b className="text-ink">{inr(p.dual_approval_amount_minor)}</b> or any CRITICAL case</li>
              <li>Written reason required to approve: {p.require_reason_for_approve_tiers.join(", ")}</li>
            </ul>
            {!owner && <div className="mt-2 text-xs text-muted">Only the owner can change policy.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
