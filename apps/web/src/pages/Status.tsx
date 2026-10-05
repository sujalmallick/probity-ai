import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, MinusCircle, RotateCw, XCircle } from "lucide-react";
import { integrationStates, isFallbackConfig, useAppConfig, type IntegrationTone } from "../lib/config";
import { relTime } from "../lib/format";
import { Skeleton } from "../components/ui";

/** GET /api/v1/ready (public): {ok, database, migrations?, redis?, env, integrations}. 503 when degraded, same body. */
export type Ready = { ok: boolean; database?: string; migrations?: string; redis?: string; integrations?: Record<string, string> };

export async function fetchReady(): Promise<Ready | null> {
  try {
    const r = await fetch("/api/v1/ready", { signal: AbortSignal.timeout(8000) });
    return (await r.json()) as Ready;
  } catch {
    return null; // API unreachable, or a proxy answered with something that isn't JSON
  }
}

const TONE = {
  ok: { Icon: CheckCircle2, cls: "text-low" },
  setup: { Icon: AlertTriangle, cls: "text-medium" },
  optional: { Icon: MinusCircle, cls: "text-muted" },
  broken: { Icon: XCircle, cls: "text-high" },
} as const;

function Row({ tone, label, value, note }: { tone: IntegrationTone; label: string; value?: string; note?: string }) {
  const { Icon, cls } = TONE[tone];
  return (
    <li className="flex items-start gap-3 border-t border-line px-4 py-3 first:border-t-0">
      <Icon size={16} className={`mt-0.5 shrink-0 ${cls}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="text-sm font-medium">{label}</span>
          {value && <span className={`text-xs ${tone === "ok" ? "text-muted" : cls}`}>{value}</span>}
        </div>
        {note && <p className="mt-0.5 text-xs text-muted">{note}</p>}
      </div>
    </li>
  );
}

export default function StatusPage() {
  const cfg = useAppConfig();
  const [ready, setReady] = useState<Ready | null | undefined>(undefined);
  const [at, setAt] = useState<string>("");
  const check = useCallback(() => {
    setReady(undefined);
    fetchReady().then((r) => { setReady(r); setAt(new Date().toISOString()); });
  }, []);
  useEffect(check, [check]);

  const reachable = ready !== null;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">System status</h1>
          <p className="text-sm text-muted">{at ? `Checked ${relTime(at)}` : "Checking…"}{cfg.version ? ` · version ${cfg.version}` : ""}</p>
        </div>
        <button className="btn" onClick={check} disabled={ready === undefined}><RotateCw size={14} />Check again</button>
      </header>

      <section className="card overflow-hidden" aria-label="Service">
        <div className="border-b border-line px-4 py-3 text-sm font-semibold">Service</div>
        {ready === undefined ? <div className="p-4"><Skeleton className="h-16" /></div> : (
          <ul>
            <Row tone={reachable ? "ok" : "broken"} label="API" value={reachable ? "Reachable" : "Not reachable"} note={reachable ? undefined : "Probity's server isn't answering. It may be starting up; check again in a minute."} />
            {reachable && <Row tone={ready!.database?.startsWith("ok") ? "ok" : "broken"} label="Database" value={ready!.database ?? "unknown"} />}
            {reachable && ready!.migrations && <Row tone={ready!.migrations === "ok" ? "ok" : "broken"} label="Database schema" value={ready!.migrations === "ok" ? "Up to date" : ready!.migrations} />}
            {reachable && ready!.redis && <Row tone={ready!.redis === "ok" ? "ok" : "broken"} label="Job queue" value={ready!.redis} />}
          </ul>
        )}
      </section>

      <section className="card overflow-hidden" aria-label="Integrations">
        <div className="border-b border-line px-4 py-3">
          <div className="text-sm font-semibold">Integrations</div>
          <p className="mt-0.5 text-xs text-muted">
            <AlertTriangle size={12} className="mr-1 inline align-[-1px] text-medium" aria-hidden />needs setting up ·{" "}
            <MinusCircle size={12} className="mr-1 inline align-[-1px] text-muted" aria-hidden />optional or not offered ·{" "}
            <XCircle size={12} className="mr-1 inline align-[-1px] text-high" aria-hidden />broken
          </p>
        </div>
        {isFallbackConfig(cfg) ? (
          <p className="p-4 text-sm text-muted">Integration status couldn't be loaded.</p>
        ) : (
          <ul>
            {integrationStates(cfg).map((i) => <Row key={i.key} tone={i.tone} label={i.label} value={i.status} note={i.note} />)}
          </ul>
        )}
      </section>

      {!isFallbackConfig(cfg) && (
        <section className="card p-4 text-sm" aria-label="Limits">
          <div className="font-semibold">Limits</div>
          <p className="mt-0.5 mb-3 text-xs text-muted">
            Daily limits are per workspace and reset at midnight UTC (5:30 am IST). When an invoice hits its own limit, the investigation stops early and the invoice is held for a person.
          </p>
          <ul className="grid gap-1 text-muted sm:grid-cols-2">
            <li>Invoice upload: <b className="text-ink">{cfg.limits.max_upload_mb} MB</b></li>
            <li>CSV import: <b className="text-ink">{cfg.limits.max_import_mb} MB</b></li>
            {cfg.limits.workspace_daily_cases != null && <li>Investigations per day: <b className="text-ink">{cfg.limits.workspace_daily_cases}</b></li>}
            {cfg.limits.case_web_searches != null && <li>Web searches per invoice: <b className="text-ink">{cfg.limits.case_web_searches}</b></li>}
            {cfg.limits.case_tokens != null && <li>AI tokens per invoice: <b className="text-ink">{cfg.limits.case_tokens.toLocaleString("en-IN")}</b></li>}
            {cfg.limits.workspace_daily_tokens != null && <li>AI tokens per day: <b className="text-ink">{cfg.limits.workspace_daily_tokens.toLocaleString("en-IN")}</b></li>}
          </ul>
        </section>
      )}
    </div>
  );
}
