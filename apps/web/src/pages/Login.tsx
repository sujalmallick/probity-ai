import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { api, post, setSession, type Me } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Spinner } from "../components/ui";

const ROLE_DESC: Record<string, string> = {
  owner: "Policy, weights, users",
  approver: "Approve, reject, request verification, confirm out-of-band",
  accountant: "Upload invoices, start investigations, annotate",
  viewer: "Read only — sees masked account numbers",
};

export default function Login() {
  const [users, setUsers] = useState<(Me & { email: string })[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { setUser } = useAuth();
  const nav = useNavigate();
  useEffect(() => {
    api("/auth/demo-users").then(setUsers).catch((e) => setErr(e.message));
  }, []);
  const login = async (id: string) => {
    const r = await post<{ token: string; user: Me }>("/auth/demo-login", { user_id: id });
    setSession(r);
    setUser(r.user);
    nav("/dashboard");
  };
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-lg font-bold text-white">P</div>
          <div>
            <h1 className="text-xl font-bold">Probity</h1>
            <p className="text-sm text-muted">Evidence before payment.</p>
          </div>
        </div>
        <div className="card p-5">
          <div className="mb-1 font-semibold">Sign in as a demo user</div>
          <p className="mb-4 text-sm text-muted">Local demo authentication (non-production). Each role sees what its permissions allow.</p>
          {err && <div className="mb-3 rounded-lg bg-high-soft p-3 text-sm text-high">API unreachable: {err}. Start it with <code>make dev</code>.</div>}
          {!users && !err && <Spinner />}
          {users?.length === 0 && <div className="text-sm text-muted">No users yet — run <code>make seed</code>.</div>}
          <div className="flex flex-col gap-2">
            {users?.map((u) => (
              <button key={u.id} className="btn !justify-between !py-3 text-left" onClick={() => login(u.id)}>
                <span>
                  <span className="block font-semibold">{u.name}</span>
                  <span className="block text-xs font-normal text-muted">{ROLE_DESC[u.role]}</span>
                </span>
                <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent">{u.role}</span>
              </button>
            ))}
          </div>
        </div>
        <p className="mt-4 flex items-center gap-1.5 text-xs text-muted"><ShieldCheck size={13} />The AI investigates. Code scores. A human decides.</p>
      </div>
    </div>
  );
}
