import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, ChevronDown, ChevronRight, Lock, Mail } from "lucide-react";
import { api, post, setLocalSession, type Me } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAppConfig } from "../lib/config";
import { Spinner } from "../components/ui";
import { AuthLayout, authInput, authPrimary } from "../components/AuthLayout";

type DemoUser = Me & { email: string };

const initials = (name: string) => name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
const ROLE_LABEL: Record<string, string> = { owner: "Owner", approver: "Approver", accountant: "Accountant", viewer: "Viewer" };

export default function Login() {
  const cfg = useAppConfig();
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showDemo, setShowDemo] = useState(true);
  const { setUser } = useAuth();
  const nav = useNavigate();
  useEffect(() => {
    if (cfg.auth.demo_login) api<DemoUser[]>("/auth/demo-users").then(setUsers).catch((e) => setLoadErr(e.message));
  }, [cfg.auth.demo_login]);

  const signIn = async (id: string) => {
    setBusy(id);
    setFormErr(null);
    try {
      const r = await post<{ token: string; user: Me }>("/auth/demo-login", { user_id: id });
      setLocalSession(r);
      setUser(r.user);
      nav("/dashboard");
    } catch (e: any) {
      setFormErr(e.message);
      setBusy(null);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value = email.trim().toLowerCase();
    if (!value) return setFormErr("Enter your work email.");
    if (!users) return setFormErr("Still loading accounts. Try again in a moment.");
    const u = users.find((x) => x.email.toLowerCase() === value);
    if (!u) return setFormErr("No account uses this email. Choose a demo user below.");
    signIn(u.id);
  };

  if (!cfg.auth.demo_login) {
    return (
      <AuthLayout title="Welcome back" subtitle="Sign in to your Probity workspace.">
        <div className="flex items-start gap-3 rounded-2xl border border-border bg-black/30 p-4 text-sm text-muted-foreground">
          <Lock size={16} className="mt-0.5 shrink-0" aria-hidden />
          Demo sign-in is turned off for this deployment. Ask your workspace owner for an invitation.
        </div>
      </AuthLayout>
    );
  }

  const emailMatch = users?.find((x) => x.email.toLowerCase() === email.trim().toLowerCase());
  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to your Probity workspace.">
      <form onSubmit={submit} noValidate>
        <div className="mb-2 flex items-baseline justify-between">
          <label htmlFor="email" className="text-sm font-medium">Work email</label>
          <Link to="/forgot-password" className="rounded text-xs text-muted-foreground transition-colors duration-200 hover:text-foreground">Forgot password?</Link>
        </div>
        <div className="relative">
          <Mail size={16} className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input
            id="email"
            type="email"
            autoComplete="email"
            inputMode="email"
            autoFocus
            placeholder="you@company.com"
            value={email}
            onChange={(e) => { setEmail(e.target.value); setFormErr(null); }}
            aria-invalid={!!formErr}
            aria-describedby={formErr ? "email-err" : "email-note"}
            className={`${authInput} ${formErr ? "border-[#ff6b6b]/70" : "border-border hover:border-white/30"}`}
          />
        </div>
        {formErr
          ? <p id="email-err" role="alert" className="mt-2 text-sm text-[#ff6b6b]">{formErr}</p>
          : <p id="email-note" className="mt-2 text-xs text-muted-foreground">Local mode: no password needed.</p>}
        <button type="submit" disabled={!!busy} className={`${authPrimary} mt-5`}>
          {busy && busy === emailMatch?.id ? <Spinner /> : <>Continue <ArrowRight size={16} aria-hidden /></>}
        </button>
      </form>

      <p className="mt-5 text-center text-sm text-muted-foreground">
        New to Probity? <Link to="/sign-up" className="rounded font-medium text-foreground underline-offset-4 hover:underline">Create an account</Link>
      </p>

      <div className="mt-7 border-t border-border pt-5">
        <button
          className="flex w-full items-center justify-between rounded-md text-xs font-medium tracking-wide text-muted-foreground uppercase hover:text-foreground"
          aria-expanded={showDemo}
          aria-controls="demo-users"
          onClick={() => setShowDemo(!showDemo)}
        >
          Demo users
          <ChevronDown size={14} className={`transition-transform duration-200 ${showDemo ? "rotate-180" : ""}`} aria-hidden />
        </button>
        {showDemo && (
          <div id="demo-users" className="mt-3">
            {loadErr && <div role="alert" className="rounded-xl border border-[#ff6b6b]/30 bg-[#2c1213] p-3 text-sm text-[#ff6b6b]">Can't reach the API ({loadErr}). Start it with <code>make dev</code>.</div>}
            {!users && !loadErr && <div className="pulse h-[220px] rounded-2xl bg-white/[0.04]" aria-label="Loading demo users" />}
            {users?.length === 0 && <p className="text-sm text-muted-foreground">No users yet. Run <code>make seed</code>.</p>}
            {users && users.length > 0 && (
              <ul className="-mx-2 flex flex-col">
                {users.map((u) => (
                  <li key={u.id}>
                    <button
                      disabled={!!busy}
                      onClick={() => signIn(u.id)}
                      className="group flex w-full cursor-pointer items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors duration-150 hover:bg-white/[0.05] disabled:cursor-wait"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-[11px] font-semibold" aria-hidden>{initials(u.name)}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{u.name}</span>
                        <span className="block truncate text-xs text-muted-foreground">{ROLE_LABEL[u.role] ?? u.role}{u.workspace?.name ? ` · ${u.workspace.name}` : ""}</span>
                      </span>
                      {busy === u.id ? <Spinner /> : <ChevronRight size={15} className="shrink-0 text-muted-foreground transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden />}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </AuthLayout>
  );
}
