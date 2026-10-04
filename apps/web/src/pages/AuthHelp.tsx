import { Link } from "react-router-dom";
import { ArrowLeft, Info, KeyRound, UserPlus } from "lucide-react";
import { AuthLayout, authPrimary } from "../components/AuthLayout";

// Local (demo) mode has no passwords and no self sign-up; production uses the sign-in provider for both.
// These pages say so plainly instead of showing forms that would not work.

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-border bg-black/30 p-4 text-sm leading-6 text-muted-foreground">
      <Info size={16} className="mt-1 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  );
}

function Back() {
  return (
    <Link to="/login" className={`${authPrimary} mt-6`}>
      <ArrowLeft size={16} aria-hidden />Back to sign in
    </Link>
  );
}

export function SignUpLocal() {
  return (
    <AuthLayout title="Create an account" subtitle="Probity workspaces are invite-only.">
      <ol className="mb-5 flex flex-col gap-3 text-sm">
        <li className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-xs font-semibold">1</span><span>Ask your workspace owner to invite your work email from <b>Team</b>.</span></li>
        <li className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-xs font-semibold">2</span><span>Sign up with that email. You join the workspace with the role they chose.</span></li>
      </ol>
      <Note>This is a local demo, so there's no self sign-up. Pick one of the demo users on the sign-in page instead.</Note>
      <Back />
      <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-muted-foreground"><UserPlus size={13} aria-hidden />Owners invite people from Team.</p>
    </AuthLayout>
  );
}

export function ForgotPasswordLocal() {
  return (
    <AuthLayout title="Reset your password" subtitle="We'll get you back in.">
      <Note>Local demo mode doesn't use passwords. Sign in with your work email or choose a demo user. In production, password resets are handled by the sign-in provider from the sign-in form.</Note>
      <Back />
      <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-muted-foreground"><KeyRound size={13} aria-hidden />No password is ever stored by Probity itself.</p>
    </AuthLayout>
  );
}
