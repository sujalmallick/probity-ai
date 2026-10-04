import { SignIn, SignUp } from "@clerk/clerk-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Eye, EyeOff, KeyRound, Lock, Mail } from "lucide-react";
import { AuthLayout, authInput, authPrimary } from "../components/AuthLayout";
import { useAppConfig } from "../lib/config";

// Clerk's form sits inside our own auth card, so drop its card chrome and header.
// "Forgot password?" and email verification are part of Clerk's form.
const CLERK_APPEARANCE = {
  variables: {
    colorPrimary: "#fafafa", colorTextOnPrimaryBackground: "#0a0a0a", colorBackground: "transparent", colorText: "#fafafa",
    colorTextSecondary: "#a6a6a6", colorInputBackground: "#0a0a0a", colorInputText: "#fafafa", borderRadius: "0.75rem",
  },
  elements: { rootBox: "w-full", cardBox: "w-full !shadow-none !border-0", card: "!bg-transparent !shadow-none !border-0 !p-0", header: "hidden", footer: "!bg-transparent" },
};

export default function Login() {
  const cfg = useAppConfig();
  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to your Probity workspace.">
      <SignIn routing="hash" signUpUrl={cfg.auth.sign_up ? "/sign-up" : undefined} appearance={CLERK_APPEARANCE} />
    </AuthLayout>
  );
}

export function SignUpPage() {
  return (
    <AuthLayout title="Create an account" subtitle="Use your work email.">
      <SignUp routing="hash" signInUrl="/login" appearance={CLERK_APPEARANCE} />
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
