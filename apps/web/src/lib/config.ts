import { useEffect, useState } from "react";

/** GET /api/v1/app/config — public, fetched once per page load and cached. */
export type AppConfig = {
  env: string;
  version: string;
  auth: { mode: "clerk"; sign_up: boolean };
  features: { landing_page: boolean };
  integrations: Partial<Record<Integration, string>>;
  limits: {
    max_upload_mb: number;
    max_import_mb: number;
    workspace_daily_cases?: number;
    case_tokens?: number;
    workspace_daily_tokens?: number;
    case_web_searches?: number;
  };
};

export type Integration = "ai" | "web_search" | "domain_lookup" | "gst_registry" | "email" | "storage" | "antivirus" | "background_jobs";

// Used when /app/config can't be loaded. The landing page and sign-in still render.
export const SAFE_CONFIG: AppConfig = {
  env: "unknown",
  version: "",
  auth: { mode: "clerk", sign_up: true },
  features: { landing_page: true },
  integrations: {},
  limits: { max_upload_mb: 10, max_import_mb: 5 },
};

let cached: AppConfig | null = null;
let inflight: Promise<AppConfig> | null = null;

export function loadAppConfig(): Promise<AppConfig> {
  if (cached) return Promise.resolve(cached);
  // A dead API (or dev proxy) can leave the request hanging; give up after a few seconds and use the safe defaults.
  inflight ??= fetch("/api/v1/app/config", { signal: AbortSignal.timeout(4000) })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
    .then((c: AppConfig) => (cached = c))
    .catch(() => (cached = SAFE_CONFIG))
    .finally(() => (inflight = null));
  return inflight;
}

/** The loaded config, or the safe defaults before it arrives (for code outside React components). */
export const currentConfig = (): AppConfig => cached ?? SAFE_CONFIG;

/** True when /app/config could not be loaded and the safe defaults are in use. */
export const isFallbackConfig = (c: AppConfig) => c === SAFE_CONFIG;

/** How an integration's status should read. Not every "off" is a problem:
 *  - "ok": working
 *  - "setup": works without it, but something useful is missing until someone connects it (amber)
 *  - "optional": off by choice or not offered; nothing to fix (grey)
 *  - "broken": the app can't do its job (red) */
export type IntegrationTone = "ok" | "setup" | "optional" | "broken";
export type IntegrationState = { key: Integration; label: string; tone: IntegrationTone; status: string; note?: string };

const INTEGRATIONS: Record<Integration, { label: string; off: IntegrationTone; offStatus: string; note: string }> = {
  ai: { label: "AI", off: "setup", offStatus: "Not set up", note: "Summaries and claim checks use rules instead, and are labelled as a rule-based fallback." },
  web_search: { label: "Web search", off: "setup", offStatus: "Not set up", note: "The web reputation check reports “could not verify”, so larger invoices are held for a person." },
  domain_lookup: { label: "Domain lookup", off: "setup", offStatus: "Not set up", note: "The domain age check reports “could not verify”." },
  gst_registry: { label: "GST registry", off: "optional", offStatus: "Not connected", note: "No GST registry is connected, so registration status can't be checked automatically. You can record it on the vendor page." },
  email: { label: "Email", off: "setup", offStatus: "Not set up", note: "Verification emails to vendors, invitations and notifications can't be sent until an email provider is connected." },
  storage: { label: "File storage", off: "broken", offStatus: "Unavailable", note: "Uploads can't be stored." },
  antivirus: { label: "Antivirus", off: "optional", offStatus: "Off (optional)", note: "Uploads aren't virus-scanned. Every PDF is still cleaned of active content when it's uploaded." },
  background_jobs: { label: "Background jobs", off: "broken", offStatus: "Unavailable", note: "Investigations can't start." },
};
const OK_STATUS: Record<string, string> = { live: "Connected", on: "On", cloud: "Cloud", local: "On this server", inline: "In the app server", worker: "Separate worker" };
const OFF = new Set(["missing", "unavailable", "off"]);

/** Every integration the server reported, with a plain-language status and tone. */
export function integrationStates(c: AppConfig): IntegrationState[] {
  return (Object.keys(INTEGRATIONS) as Integration[])
    .filter((k) => typeof c.integrations[k] === "string")
    .map((k) => {
      const v = c.integrations[k]!;
      const info = INTEGRATIONS[k];
      return OFF.has(v)
        ? { key: k, label: info.label, tone: info.off, status: info.offStatus, note: info.note }
        : { key: k, label: info.label, tone: "ok" as const, status: OK_STATUS[v] ?? v };
    });
}

/** Integrations that need someone to act (set up or broken), as readable labels, e.g. ["Email"]. */
export const integrationsNeedingAction = (c: AppConfig) => integrationStates(c).filter((s) => s.tone === "setup" || s.tone === "broken").map((s) => s.label);

/** Returns SAFE_CONFIG until the real config arrives. */
export function useAppConfig(): AppConfig {
  const [cfg, setCfg] = useState<AppConfig>(cached ?? SAFE_CONFIG);
  useEffect(() => {
    if (!cached) loadAppConfig().then(setCfg);
  }, []);
  return cfg;
}
