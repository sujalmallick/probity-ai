import { useEffect, useState } from "react";

/** GET /api/v1/app/config — public, fetched once per page load and cached. */
export type AppConfig = {
  env: string;
  version: string;
  auth: { mode: "local" | "clerk"; demo_login: boolean; sign_up: boolean };
  features: { landing_page: boolean; demo: boolean; benchmark: boolean; simulated_inbox: boolean };
  integrations: Record<string, string>;
  limits: { max_upload_mb: number; max_import_mb: number };
};

// If the call fails, demo-only parts stay hidden: production must never show them by accident.
export const SAFE_CONFIG: AppConfig = {
  env: "unknown",
  version: "",
  auth: { mode: "local", demo_login: false, sign_up: false },
  features: { landing_page: true, demo: false, benchmark: false, simulated_inbox: false },
  integrations: {},
  limits: { max_upload_mb: 15, max_import_mb: 5 },
};

let cached: AppConfig | null = null;
let inflight: Promise<AppConfig> | null = null;

export function loadAppConfig(): Promise<AppConfig> {
  if (cached) return Promise.resolve(cached);
  inflight ??= fetch("/api/v1/app/config")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
    .then((c: AppConfig) => (cached = c))
    .catch(() => (cached = SAFE_CONFIG))
    .finally(() => (inflight = null));
  return inflight;
}

/** True when /app/config could not be loaded and the safe defaults are in use. */
export const isFallbackConfig = (c: AppConfig) => c === SAFE_CONFIG;

/** Integrations reported as "offline" (cached tools, mock AI). */
export const offlineIntegrations = (c: AppConfig) => Object.entries(c.integrations).filter(([, v]) => v === "offline").map(([k]) => (k === "ai" ? "AI" : k.replace("_", " ")));

/** Returns SAFE_CONFIG until the real config arrives. */
export function useAppConfig(): AppConfig {
  const [cfg, setCfg] = useState<AppConfig>(cached ?? SAFE_CONFIG);
  useEffect(() => {
    if (!cached) loadAppConfig().then(setCfg);
  }, []);
  return cfg;
}
