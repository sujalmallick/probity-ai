import { Check, CircleSlash, Clock, X } from "lucide-react";
import { Spinner } from "./ui";

export interface AgentEvent {
  seq: number;
  ts: string;
  type: string;
  agent: string | null;
  status: string | null;
  message: string;
  data: Record<string, any>;
}

const STEPS: { agent: string; label: string; doneText?: (e: AgentEvent[]) => string | undefined }[] = [
  { agent: "document", label: "Invoice extracted" },
  { agent: "orchestrator", label: "Vendor identified" },
  { agent: "transaction_analyst", label: "Historical invoices searched" },
  { agent: "vendor_investigator", label: "Registry & domain checked" },
  { agent: "web_research", label: "External sources searched" },
  { agent: "verification", label: "Evidence verified" },
  { agent: "risk_engine", label: "Risk scored (code only)" },
  { agent: "case_analyst", label: "Case summary written" },
];

type State = "queued" | "running" | "done" | "failed" | "skipped";

export function agentStates(events: AgentEvent[]) {
  const st: Record<string, { state: State; message: string }> = {};
  for (const e of events) {
    if (!e.agent) continue;
    const cur = st[e.agent] ?? { state: "queued", message: "" };
    if (e.type === "agent.started" || e.type === "agent.progress") st[e.agent] = { state: "running", message: e.message };
    else if (e.type === "agent.completed") st[e.agent] = { state: "done", message: e.message };
    else if (e.type === "agent.failed") st[e.agent] = { state: "failed", message: e.message };
    else if (e.type === "agent.skipped") st[e.agent] = { state: "skipped", message: e.message };
    else st[e.agent] = cur;
  }
  return st;
}

export function Timeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const st = agentStates(events);
  return (
    <ol className="flex flex-col gap-0.5" aria-live="polite">
      {STEPS.map((s) => {
        const cur = st[s.agent] ?? { state: running ? "queued" : "queued", message: "" };
        const icon =
          cur.state === "done" ? <Check size={14} className="text-low" aria-label="done" /> :
          cur.state === "running" ? <Spinner size={14} /> :
          cur.state === "failed" ? <X size={14} className="text-high" aria-label="failed" /> :
          cur.state === "skipped" ? <CircleSlash size={14} className="text-muted" aria-label="skipped" /> :
          <Clock size={14} className="text-muted opacity-50" aria-label="queued" />;
        return (
          <li key={s.agent} className="flex gap-2.5 rounded-md px-2 py-1.5" style={{ background: cur.state === "running" ? "var(--accent-soft)" : undefined }}>
            <div className="mt-0.5 shrink-0">{icon}</div>
            <div className="min-w-0">
              <div className={`text-sm ${cur.state === "queued" ? "text-muted" : "font-medium"}`}>{s.label}</div>
              {cur.message && (cur.state !== "done" || s.agent !== "document") && (
                <div className="truncate text-xs text-muted" title={cur.message}>{cur.message}</div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ActivityLog({ events }: { events: AgentEvent[] }) {
  return (
    <ol className="flex max-h-[420px] flex-col gap-1 overflow-auto font-mono text-xs">
      {events.map((e) => (
        <li key={e.seq} className="flex gap-2">
          <span className="shrink-0 text-muted">{e.ts.slice(11, 19)}</span>
          <span className="shrink-0 text-accent">{e.agent ?? "case"}</span>
          <span className="break-words">{e.message || e.type}</span>
        </li>
      ))}
    </ol>
  );
}
