import { useState } from "react";
import { Building2, Plus, ShieldAlert } from "lucide-react";
import { ApiError, errMsg, post, type Me } from "../lib/api";
import { AuthLayout, authPrimary } from "./AuthLayout";

export type Invitation = {
  id: string;
  workspace: string;
  role: string;
  invited_by: { name?: string | null; email?: string | null } | null;
  expires_at?: string | null;
};

const ROLE: Record<string, string> = { viewer: "Viewer", accountant: "Accountant", approver: "Approver", owner: "Owner" };

/** Shown to a first-time user who has pending invitations (GET /me answered 409 `invitation_pending`). Nobody joins a
 *  workspace automatically: they accept one invitation or start their own (POST /me/join). */
export function InvitationChoice({ invitations, onJoined, onRefresh, onSignOut }: {
  invitations: Invitation[];
  onJoined: (me: Me) => void;
  onRefresh: () => void;
  onSignOut: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const join = async (key: string, body: { invitation_id: string } | { own_workspace: true }) => {
    setBusy(key);
    setErr(null);
    try {
      onJoined(await post<Me>("/me/join", body));
    } catch (e) {
      // 404: that invitation expired or was withdrawn. 409: already in a workspace. Either way, ask /me again.
      if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
        if (e.status === 404) setErr("That invitation has expired or was withdrawn. The list has been refreshed.");
        onRefresh();
      } else setErr(errMsg(e));
      setBusy(null);
    }
  };
  const many = invitations.length > 1;
  return (
    <AuthLayout title="You've been invited" subtitle={many ? "Choose a workspace to join, or start your own." : "Join this workspace, or start your own."}>
      <ul className="flex flex-col gap-3">
        {invitations.map((inv) => (
          <li key={inv.id} className="rounded-2xl border border-border bg-black/30 p-4">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10"><Building2 size={18} aria-hidden /></span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{inv.workspace}</div>
                <div className="text-sm text-muted-foreground">as {ROLE[inv.role] ?? inv.role}</div>
                {inv.invited_by && (inv.invited_by.name || inv.invited_by.email) && (
                  <div className="mt-1 text-xs break-words text-muted-foreground">
                    Invited by {inv.invited_by.name ?? inv.invited_by.email}{inv.invited_by.name && inv.invited_by.email ? ` (${inv.invited_by.email})` : ""}
                  </div>
                )}
                {inv.expires_at && <div className="text-xs text-muted-foreground">Expires {new Date(inv.expires_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</div>}
              </div>
            </div>
            <button className={`${authPrimary} mt-3 !h-10 text-sm`} disabled={busy !== null} onClick={() => join(inv.id, { invitation_id: inv.id })}>
              {busy === inv.id ? "Joining…" : `Accept and join ${many ? inv.workspace : ""}`.trim()}
            </button>
          </li>
        ))}
      </ul>

      <p className="mt-4 flex items-start gap-2 text-xs text-muted-foreground">
        <ShieldAlert size={14} className="mt-px shrink-0" aria-hidden />
        Only accept invitations from people you know.
      </p>

      {err && <p role="alert" className="mt-3 text-sm text-[#ff6b6b]">{err}</p>}

      <div className="mt-5 border-t border-border pt-5">
        <button
          className="flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-border text-sm font-medium transition-colors duration-200 hover:bg-white/5 disabled:cursor-wait disabled:opacity-60"
          disabled={busy !== null}
          onClick={() => join("own", { own_workspace: true })}
        >
          <Plus size={15} aria-hidden />{busy === "own" ? "Creating your workspace…" : "Start my own workspace instead"}
        </button>
        <button className="mt-3 w-full text-center text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline" onClick={onSignOut}>
          Not you? Sign out
        </button>
      </div>
    </AuthLayout>
  );
}
