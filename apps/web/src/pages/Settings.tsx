import { useEffect, useState } from "react";
import { api, can } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr } from "../lib/format";

const SPEEDS: [number, string][] = [[0, "Instant"], [400, "Readable"], [900, "Stage (slow)"]];

export default function SettingsPage() {
  const { user } = useAuth();
  const [p, setP] = useState<any | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api("/workspace/policy").then(setP);
  useEffect(() => { load(); }, []);
  if (!p) return null;
  const owner = can(user?.role, "owner");
  const save = async (patch: Record<string, unknown>) => {
    try {
      await api("/workspace/policy", { method: "PUT", body: JSON.stringify(patch) });
      setMsg("Saved (audited).");
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  const core = ["bank_account_changed", "price_anomaly", "new_domain", "identity_mismatch", "duplicate_invoice", "address_mismatch", "missing_po"];
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">Risk policy</h1>
        <p className="text-sm text-muted">Agents discover signals; code decides the score. Weights are versioned ({p.weights_version}); changes are owner-only and audited.</p>
      </div>
      {msg && <div className="rounded-lg bg-accent-soft p-3 text-sm text-accent">{msg}</div>}
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
              <input type="checkbox" disabled={!owner} checked={p.auto_clear_enabled} onChange={(e) => save({ auto_clear_enabled: e.target.checked })} />
              Auto-clear LOW-risk invoices when every check completed and no indicator fired
            </label>
            <ul className="mt-3 flex flex-col gap-1 text-sm text-muted">
              <li>Auto-clear limit: <b className="text-ink">{inr(p.auto_clear_max_amount_minor)}</b></li>
              <li>Web research above: <b className="text-ink">{inr(p.external_research_amount_minor)}</b></li>
              <li>Dual approval at or above: <b className="text-ink">{inr(p.dual_approval_amount_minor)}</b> or any CRITICAL case</li>
              <li>Written reason required to approve: {p.require_reason_for_approve_tiers.join(", ")}</li>
            </ul>
            {!owner && <div className="mt-2 text-xs text-muted">Only the owner can change policy.</div>}
          </div>
          <div className="card p-4">
            <div className="label mb-2">Demo: agent animation speed</div>
            <div className="flex gap-2">
              {SPEEDS.map(([ms, label]) => (
                <button key={ms} className={`btn flex-1 text-xs ${(p.demo_agent_delay_ms ?? 0) === ms ? "!border-accent text-accent" : ""}`} onClick={async () => { await api(`/demo/speed?delay_ms=${ms}`, { method: "PUT" }); load(); }}>{label}</button>
              ))}
            </div>
            <div className="mt-2 text-xs text-muted">Slows the live timeline so each agent step is readable on stage.</div>
          </div>
        </div>
      </div>
    </div>
  );
}
