import { Link } from "react-router-dom";
import { ArrowRight, Check, Circle, X } from "lucide-react";

export interface OnboardingStep {
  key: string;
  title: string;
  required: boolean;
  done: boolean;
  detail?: string;
  why?: string;
  action: { type: "import"; kind: string } | { type: "route"; to: string };
}
export interface Onboarding {
  complete: boolean;
  progress: string;
  steps: OnboardingStep[];
}

export function stepHref(a: OnboardingStep["action"]): string {
  return a.type === "route" ? a.to : `/vendors/import?kind=${encodeURIComponent(a.kind)}`;
}

const dismissKey = (ws?: string) => `probity.onboarding.dismissed.${ws ?? "default"}`;
export function isDismissed(ws?: string): boolean {
  try {
    return localStorage.getItem(dismissKey(ws)) === "1";
  } catch {
    return false;
  }
}

/** Setup checklist; items tick off from server state. Dismissal is per workspace and per browser. */
export function OnboardingChecklist({ data, workspaceId, onDismiss }: { data: Onboarding; workspaceId?: string; onDismiss: () => void }) {
  const done = data.steps.filter((s) => s.done).length;
  const pct = Math.round((done / Math.max(1, data.steps.length)) * 100);
  const next = data.steps.find((s) => !s.done);
  const dismiss = () => {
    try {
      localStorage.setItem(dismissKey(workspaceId), "1");
    } catch {
      /* private mode: dismiss for this view only */
    }
    onDismiss();
  };
  return (
    <section className="card p-5" aria-labelledby="onb-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="onb-title" className="font-semibold">Set up your workspace</h2>
          <p className="mt-0.5 text-sm text-muted">{done} of {data.steps.length} done</p>
        </div>
        <button className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-ink" onClick={dismiss} aria-label="Dismiss setup checklist" title="Dismiss">
          <X size={16} />
        </button>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Setup progress">
        <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      <ol className="mt-4 grid gap-1 sm:grid-cols-2">
        {data.steps.map((s) => (
          <li key={s.key}>
            <Link
              to={stepHref(s.action)}
              title={s.why}
              className={`group flex items-center gap-3 rounded-lg px-2.5 py-2 transition-colors duration-150 hover:bg-surface-2 ${s === next ? "bg-surface-2" : ""}`}
            >
              {s.done ? (
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-accent-fg" aria-label="Done"><Check size={12} strokeWidth={3} /></span>
              ) : (
                <Circle size={20} className="shrink-0 text-muted" aria-label="To do" />
              )}
              <span className="min-w-0 flex-1">
                <span className={`block truncate text-sm ${s.done ? "text-muted line-through decoration-1" : "font-medium"}`}>{s.title}</span>
                {s.detail && <span className="block truncate text-xs text-muted">{s.detail}{s.required && !s.done ? " · required" : ""}</span>}
              </span>
              {!s.done && <ArrowRight size={14} className="shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />}
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
