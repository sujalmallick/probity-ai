import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CircleSlash, RotateCw, XCircle } from "lucide-react";
import { isFallbackConfig, useAppConfig, type Integration } from "../lib/config";
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

const INTEGRATIONS: { key: Integration; label: string; off: string }[] = [
  { key: "ai", label: "AI", off: "Summaries and claim checks use rules instead and are labelled as a rule-based fallback." },
  { key: "web_search", label: "Web search", off: "The web reputation check reports “could not verify”." },
  { key: "domain_lookup", label: "Domain lookup", off: "The domain age check reports “could not verify”." },
  { key: "gst_registry", label: "GST registry", off: "GST registration can't be checked automatically; enter it on the vendor page." },
  { key: "email", label: "Email", off: "Emails to vendors and notification emails can't be sent." },
  { key: "storage", label: "File storage", off: "Uploads can't be stored." },
  { key: "antivirus", label: "Antivirus", off: "Uploads aren't virus-scanned." },
  { key: "background_jobs", label: "Background jobs", off: "Investigations can't start." },
];
const NOT_AVAILABLE = new Set(["missing", "unavailable", "off"]);

function Row({ ok, label, value, note }: { ok: boolean | null; label: string; value?: string; note?: string }) {
  const Icon = ok === null ? CircleSlash : ok ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-start gap-3 border-t border-line px-4 py-3 first:border-t-0">
      <Icon size={16} className={`mt-0.5 shrink-0 ${ok === null ? "text-muted" : ok ? "text-low" : "text-high"}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="text-sm font-medium">{label}</span>
          {value && <span className="text-xs text-muted">{value}</span>}
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
            <Row ok={reachable} label="API" value={reachable ? "reachable" : "not reachable"} note={reachable ? undefined : "Probity's server isn't answering. It may be starting up; check again in a minute."} />
            {reachable && <Row ok={ready!.database?.startsWith("ok") ?? false} label="Database" value={ready!.database ?? "unknown"} />}
            {reachable && ready!.migrations && <Row ok={ready!.migrations === "ok"} label="Database schema" value={ready!.migrations} />}
            {reachable && ready!.redis && <Row ok={ready!.redis === "ok"} label="Job queue" value={ready!.redis} />}
          </ul>
        )}
      </section>

      <section className="card overflow-hidden" aria-label="Integrations">
        <div className="border-b border-line px-4 py-3 text-sm font-semibold">Integrations</div>
        {isFallbackConfig(cfg) ? (
          <p className="p-4 text-sm text-muted">Integration status couldn't be loaded.</p>
        ) : (
          <ul>
            {INTEGRATIONS.filter((i) => cfg.integrations[i.key] !== undefined).map((i) => {
              const v = cfg.integrations[i.key]!;
              const off = NOT_AVAILABLE.has(v);
              return <Row key={i.key} ok={!off} label={i.label} value={v} note={off ? i.off : undefined} />;
            })}
          </ul>
        )}
      </section>

      {!isFallbackConfig(cfg) && (
        <section className="card p-4 text-sm" aria-label="Limits">
          <div className="mb-2 font-semibold">Limits</div>
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
