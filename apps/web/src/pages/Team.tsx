import { useEffect, useState } from "react";
import { Mail, ShieldCheck, Trash2, UserPlus, Users } from "lucide-react";
import { api, can, errMsg, post, type Role } from "../lib/api";
import { useAuth } from "../lib/auth";
import { relTime } from "../lib/format";
import { LoadError, Spinner } from "../components/ui";

const ROLES: [Role, string][] = [
  ["viewer", "Read only; account numbers masked"],
  ["accountant", "Upload invoices, start investigations"],
  ["approver", "Approve, reject, verify vendors, confirm out-of-band"],
  ["owner", "Policy, weights and team"],
];

export default function Team() {
  const { user, mode } = useAuth();
  const owner = can(user?.role, "owner");
  const [members, setMembers] = useState<any[] | null>(null);
  const [invites, setInvites] = useState<any[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("accountant");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [inviteErr, setInviteErr] = useState<string | null>(null);

  const load = () => {
    api("/workspace/members").then((r) => { setMembers(r.items); setLoadErr(null); }).catch((e) => setLoadErr(errMsg(e)));
    if (owner) api("/workspace/invitations").then((r) => { setInvites(r.items); setInviteErr(null); }).catch((e) => setInviteErr(errMsg(e)));
  };
  useEffect(load, [owner]);

  const run = async <T,>(fn: () => Promise<T>, ok: string | ((r: T) => string)) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      setMsg({ ok: true, text: typeof ok === "function" ? ok(r) : ok });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: errMsg(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="page-title">Team</h1>
        <p className="text-sm text-muted">Roles are enforced on the server for every action. Every change is written to the audit log.</p>
      </div>
      {msg && <div className={`rounded-lg p-3 text-sm ${msg.ok ? "bg-low-soft text-low" : "bg-high-soft text-high"}`}>{msg.text}</div>}

      {owner && (
        <form className="card flex flex-col gap-3 p-4 md:flex-row md:items-end" onSubmit={(e) => { e.preventDefault(); const invited = email; run(() => post<{ email_sent?: boolean; email_note?: string }>("/workspace/invitations", { email: invited, role }).then((r) => { setEmail(""); return r; }), (r) => r?.email_sent ? `Invitation emailed to ${invited}. They join this workspace when they sign in with that email.` : `Invitation created for ${invited}, but no email was sent${r?.email_note ? `: ${r.email_note}` : ""}. Ask them to sign up with that email.`); }}>
          <label className="flex-1">
            <span className="label">Invite by email</span>
            <input className="input mt-1" type="email" required placeholder="colleague@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            <span className="label">Role</span>
            <select className="input mt-1" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map(([r]) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <button className="btn btn-primary" disabled={busy || !email}>{busy ? <Spinner /> : <UserPlus size={15} />}Invite</button>
        </form>
      )}

      <div className="card overflow-x-auto">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3 font-semibold"><Users size={16} />Members</div>
        {!members ? (
          loadErr ? <LoadError error={`Could not load the team. ${loadErr}`} onRetry={load} className="m-3 !shadow-none" /> : <div className="p-4"><Spinner /></div>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {members.map((m) => (
                <tr key={m.id} className={`border-t border-line ${m.active ? "" : "opacity-50"}`}>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{m.name}{m.id === user?.id && <span className="ml-1 text-xs text-muted">(you)</span>}</div>
                    <div className="text-xs text-muted">{m.email}</div>
                  </td>
                  <td className="px-4 py-2.5">
                    {owner && m.id !== user?.id ? (
                      <select className="input !w-auto !py-1 text-xs" value={m.role} aria-label={`Role for ${m.name}`} onChange={(e) => run(() => api(`/workspace/members/${m.id}`, { method: "PATCH", body: JSON.stringify({ role: e.target.value }) }), `${m.name} is now ${e.target.value}.`)}>
                        {ROLES.map(([r]) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    ) : <span className="rounded bg-surface-2 px-2 py-0.5 text-xs">{m.role}</span>}
                  </td>
                  {mode === "clerk" && <td className="px-4 py-2.5 text-xs text-muted">{m.linked ? <span className="inline-flex items-center gap-1"><ShieldCheck size={13} className="text-low" />signed in</span> : "invited — not signed in yet"}</td>}
                  <td className="px-4 py-2.5 text-right">
                    {owner && m.id !== user?.id && (
                      <button className="btn !py-1 text-xs" onClick={() => run(() => api(`/workspace/members/${m.id}`, { method: "PATCH", body: JSON.stringify({ active: !m.active }) }), m.active ? `${m.name} deactivated.` : `${m.name} reactivated.`)}>
                        {m.active ? "Deactivate" : "Reactivate"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {owner && inviteErr && <LoadError error={`Could not load pending invitations. ${inviteErr}`} onRetry={load} />}
      {owner && invites.length > 0 && (
        <div className="card p-4">
          <div className="label mb-2">Pending invitations</div>
          <ul className="flex flex-col gap-2">
            {invites.map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="flex items-center gap-2"><Mail size={14} className="text-muted" />{i.email} · {i.role} <span className="text-xs text-muted">{relTime(i.created_at)}</span></span>
                <button className="btn !py-1 text-xs" aria-label={`Revoke invitation for ${i.email}`} onClick={() => run(() => api(`/workspace/invitations/${i.id}`, { method: "DELETE" }), "Invitation revoked.")}><Trash2 size={13} />Revoke</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card p-4">
        <div className="label mb-2">What each role can do</div>
        <ul className="grid gap-1 text-sm md:grid-cols-2">{ROLES.map(([r, d]) => <li key={r}><b>{r}</b> — <span className="text-muted">{d}</span></li>)}</ul>
      </div>
    </div>
  );
}
