import { useEffect, useState } from "react";

/** GET /api/v1/app/config — public, fetched once per page load and cached. */
export type AppConfig = {
  env: string;
  version: string;
  auth: { mode: "clerk"; sign_up: boolean };
  features: { landing_page: boolean };
  integrations: Partial<Record<Integration, string>>;
  limits: { max_upload_mb: number; max_import_mb: number };
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

const INTEGRATION_LABEL: Record<Integration, string> = {
  ai: "AI", web_search: "Web search", domain_lookup: "Domain lookup", gst_registry: "GST registry", email: "Email",
  storage: "Storage", antivirus: "Antivirus", background_jobs: "Background jobs",
};
// Values that mean "this integration is not working right now". Anything else ("live", "local", "cloud", "inline", …) is fine.
const NOT_AVAILABLE = new Set(["missing", "unavailable", "off"]);

/** Integrations that are not available, as readable labels (e.g. ["Web search", "GST registry"]). */
export const unavailableIntegrations = (c: AppConfig) =>
  (Object.entries(c.integrations) as [Integration, string][])
    .filter(([, v]) => NOT_AVAILABLE.has(v))
    .map(([k]) => INTEGRATION_LABEL[k] ?? k.replace(/_/g, " "));

/** Returns SAFE_CONFIG until the real config arrives. */
export function useAppConfig(): AppConfig {
  const [cfg, setCfg] = useState<AppConfig>(cached ?? SAFE_CONFIG);
  useEffect(() => {
    if (!cached) loadAppConfig().then(setCfg);
  }, []);
  return cfg;
}
