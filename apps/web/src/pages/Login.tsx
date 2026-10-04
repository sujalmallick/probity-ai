import { SignIn, SignUp } from "@clerk/clerk-react";
import { useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { Eye, EyeOff, KeyRound, Lock, Mail } from "lucide-react";
import { AuthLayout, authInput, authPrimary } from "../components/AuthLayout";
import { useAppConfig } from "../lib/config";

// Clerk's form sits inside our own auth card, so its card chrome and header are removed and it fills our card's width.
// Style objects (not class names) so they apply regardless of stylesheet order. "Forgot password?" and email
// verification are part of Clerk's form.
const BORDER = "1px solid #2e2e2e";
// Clerk draws field borders as a box-shadow, so the outline is set the same way.
const FIELD = { height: "2.75rem", background: "#0a0a0a", border: "none", borderRadius: "0.75rem", boxShadow: "inset 0 0 0 1px #3a3a3a !important", color: "#fafafa" };
const CLERK_APPEARANCE = {
  variables: {
    colorPrimary: "#fafafa", colorPrimaryForeground: "#0a0a0a", colorBackground: "#0a0a0a", colorForeground: "#fafafa",
    colorMutedForeground: "#a6a6a6", colorNeutral: "#fafafa", colorInput: "#0a0a0a", colorInputForeground: "#fafafa",
    colorBorder: "#3a3a3a", colorDanger: "#ff6b6b", borderRadius: "0.75rem", fontFamily: "inherit",
  },
  elements: {
    rootBox: { width: "100%" },
    cardBox: { width: "100%", maxWidth: "100%", background: "transparent", border: "none", boxShadow: "none", overflow: "visible" },
    card: { width: "100%", padding: 0, background: "transparent", border: "none", boxShadow: "none", gap: "1.25rem" },
    header: { display: "none" },
    socialButtonsBlockButton: { ...FIELD, position: "relative", "&:focus-visible": { boxShadow: "inset 0 0 0 1.5px #fafafa !important" } },
    socialButtonsBlockButtonText: { color: "#fafafa", fontWeight: 500, fontSize: "0.875rem" },
    lastAuthenticationStrategyBadge: { position: "absolute", top: "-0.6rem", right: "0.75rem", background: "#1f1f1f", color: "#a6a6a6", border: BORDER },
    dividerLine: { background: "#2e2e2e" },
    dividerText: { color: "#a6a6a6" },
    formFieldLabel: { color: "#fafafa", fontWeight: 500 },
    formFieldInput: { ...FIELD, "&:focus": { boxShadow: "inset 0 0 0 1.5px #fafafa !important" } },
    formButtonPrimary: { height: "2.75rem", background: "#fafafa", color: "#0a0a0a", fontSize: "0.9375rem", fontWeight: 600, textTransform: "none", boxShadow: "none", borderRadius: "0.75rem" },
    footer: { background: "transparent", backgroundImage: "none", padding: 0, marginTop: "0.25rem" },
    footerAction: { background: "transparent" },
    footerActionText: { color: "#a6a6a6" },
    footerActionLink: { color: "#fafafa", fontWeight: 600 },
    identityPreview: { background: "#0a0a0a", border: BORDER },
    otpCodeFieldInput: { background: "#0a0a0a", border: BORDER, color: "#fafafa" },
  },
} as const;

/** Where people land after signing in or creating an account (Clerk otherwise returns them to "/", the landing page). */
const AFTER_AUTH = "/dashboard";

export default function Login() {
  const cfg = useAppConfig();
  const expired = new URLSearchParams(useLocation().search).has("expired");
  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to your Probity workspace.">
      {expired && (
        <p role="status" className="mb-4 rounded-xl border border-border bg-black/30 px-4 py-3 text-sm text-muted-foreground">
          Your session ended, so you were signed out. Sign in again to continue.
        </p>
      )}
      <SignIn routing="hash" signUpUrl={cfg.auth.sign_up ? "/sign-up" : undefined} fallbackRedirectUrl={AFTER_AUTH} signUpFallbackRedirectUrl={AFTER_AUTH} appearance={CLERK_APPEARANCE} />
    </AuthLayout>
  );
}

export function SignUpPage() {
  return (
    <AuthLayout title="Create an account" subtitle="Use your work email.">
      <SignUp routing="hash" signInUrl="/login" fallbackRedirectUrl={AFTER_AUTH} signInFallbackRedirectUrl={AFTER_AUTH} appearance={CLERK_APPEARANCE} />
    </AuthLayout>
  );
}

/** /login and /sign-up when this build has no Clerk publishable key. The usual email and password form is shown;
 *  submitting it explains that sign-in isn't set up yet. Nothing typed here is sent or stored anywhere. */
export function SignInUnavailable({ mode }: { mode: "sign-in" | "sign-up" }) {
  const signIn = mode === "sign-in";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) return setErr("Enter your work email.");
    if (!password) return setErr("Enter your password.");
    setErr(null);
    setPassword(""); // there is nowhere to send it; don't keep it on screen either
    setNotice(true);
  };
  return (
    <AuthLayout title={signIn ? "Welcome back" : "Create an account"} subtitle={signIn ? "Sign in to your Probity workspace." : "Use your work email."}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <div>
          <label htmlFor="auth-email" className="mb-2 block text-sm font-medium">Work email</label>
          <div className="relative">
            <Mail size={16} className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              id="auth-email" type="email" inputMode="email" autoComplete="email" autoFocus placeholder="you@company.com"
              value={email} onChange={(e) => { setEmail(e.target.value); setErr(null); }}
              className={`${authInput} border-border hover:border-white/30`}
            />
          </div>
        </div>
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <label htmlFor="auth-password" className="text-sm font-medium">Password</label>
            {signIn && (
              <button type="button" className="rounded text-xs text-muted-foreground transition-colors duration-200 hover:text-foreground" onClick={() => { setErr(null); setNotice(true); }}>
                Forgot password?
              </button>
            )}
          </div>
          <div className="relative">
            <Lock size={16} className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              id="auth-password" type={show ? "text" : "password"} autoComplete={signIn ? "current-password" : "new-password"}
              placeholder={signIn ? "Your password" : "Choose a password"}
              value={password} onChange={(e) => { setPassword(e.target.value); setErr(null); }}
              className={`${authInput} !pr-12 border-border hover:border-white/30`}
            />
            <button
              type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}
              className="absolute top-1/2 right-3 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground"
            >
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>
        {err && <p role="alert" className="-mt-1 text-sm text-[#ff6b6b]">{err}</p>}
        {notice && (
          <div role="alert" className="flex flex-col gap-2 rounded-2xl border border-dashed border-border bg-black/30 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium"><KeyRound size={16} aria-hidden />{signIn ? "Sign-in isn't set up yet" : "Sign-up isn't set up yet"}</div>
            <p className="text-muted-foreground">
              This copy of Probity has no sign-in provider configured, so nobody can {signIn ? "sign in" : "create an account"} until it's added. Nothing you typed was sent.
            </p>
            <p className="text-muted-foreground">
              If you run this site: add <code className="rounded bg-white/10 px-1 py-0.5 text-[12px] text-foreground">VITE_CLERK_PUBLISHABLE_KEY</code> to{" "}
              <code className="rounded bg-white/10 px-1 py-0.5 text-[12px] text-foreground">apps/web/.env.local</code>, then restart the web server.
            </p>
          </div>
        )}
        <button type="submit" className={authPrimary}>{signIn ? "Continue" : "Create account"}</button>
      </form>
      <p className="mt-5 text-center text-sm text-muted-foreground">
        {signIn
          ? <>New to Probity? <Link to="/sign-up" className="rounded font-medium text-foreground underline-offset-4 hover:underline">Create an account</Link></>
          : <>Already have an account? <Link to="/login" className="rounded font-medium text-foreground underline-offset-4 hover:underline">Sign in</Link></>}
      </p>
    </AuthLayout>
  );
}
