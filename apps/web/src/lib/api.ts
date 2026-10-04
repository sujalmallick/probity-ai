import { currentConfig } from "./config";

export type Role = "viewer" | "accountant" | "approver" | "owner";
export type Tier = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Me {
  id: string;
  name: string;
  email?: string;
  role: Role;
  workspace?: { id: string; name: string };
}

// ---------------------------------------------------------------- tokens
// Clerk session: a getter that returns a fresh short-lived JWT per call.

let tokenGetter: (() => Promise<string | null>) | null = null;
let onUnauthorized: (() => void) | null = null;

export function setTokenGetter(fn: (() => Promise<string | null>) | null) {
  tokenGetter = fn;
}
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

async function token(): Promise<string | null> {
  return tokenGetter ? tokenGetter() : null;
}

// ---------------------------------------------------------------- requests

/** Every API error arrives as {error: {code, message, retryable, ref}}. `message` is written for end users; `ref` is the
 *  request id support can look up. status 0 = the API could not be reached at all. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public retryable = false,
    public ref?: string,
    /** The whole `error` object, for codes that carry extra fields (e.g. `invitation_pending` lists `invitations`). */
    public details?: Record<string, any>,
  ) {
    super(message);
  }
}

/** The text to show for a caught error, with the reference appended when there is one. */
export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.ref ? `${e.message} (Reference: ${e.ref})` : e.message;
  return e instanceof Error && e.message ? e.message : "Something went wrong. Try again.";
}

const UNREACHABLE = "Can't reach Probity. Check your connection and try again.";
const UNAVAILABLE = "Probity is temporarily unavailable. Try again in a minute.";

async function authHeaders(init?: HeadersInit): Promise<Headers> {
  const h = new Headers(init);
  const t = await token();
  if (t) h.set("Authorization", `Bearer ${t}`);
  return h;
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = await authHeaders(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { ...init, headers });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, UNREACHABLE, "network_error", true);
  }
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function toApiError(res: Response): Promise<ApiError> {
  let err: { code?: string; message?: string; retryable?: boolean; ref?: string; [k: string]: any } | undefined;
  try {
    err = (await res.json())?.error;
  } catch {
    /* not JSON: a proxy or gateway answered instead of the API */
  }
  const ref = err?.ref ?? res.headers.get("X-Request-ID") ?? undefined;
  let msg = err?.message;
  let retryable = err?.retryable ?? (res.status >= 500 || res.status === 0);
  if (res.status === 413 || err?.code === "payload_too_large") {
    // nginx can answer 413 itself (no JSON body), so the text is built here from the configured limits.
    const { max_upload_mb, max_import_mb } = currentConfig().limits;
    msg = `That's too large to upload. Invoices can be up to ${max_upload_mb} MB and CSV files up to ${max_import_mb} MB.`;
    retryable = false;
  }
  if (!msg) msg = res.status >= 500 ? UNAVAILABLE : res.status === 401 ? "Your session has ended. Sign in again." : `Request failed (${res.status}).`;
  if (res.status === 401) onUnauthorized?.();
  return new ApiError(res.status, msg, err?.code, retryable, ref, err);
}

export const post = <T = any>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T = any>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

export async function uploadFile(file: File | Blob, name: string) {
  const fd = new FormData();
  fd.append("file", file, name);
  return api<{ document_id: string; sha256: string; filename: string; duplicate_of: string | null }>("/documents", { method: "POST", body: fd });
}

export async function fetchBlob(path: string): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(path, { headers: await authHeaders() });
  } catch {
    throw new ApiError(0, UNREACHABLE, "network_error", true);
  }
  if (!res.ok) throw await toApiError(res);
  return res.blob();
}

export type StreamState = "live" | "reconnecting" | "closed";

/** Server-Sent Events over fetch (so the bearer token travels in a header, never the URL).
 *  Reconnects with Last-Event-ID and reports "reconnecting" while the stream is down; returns a stop function. */
export function streamEvents(path: string, onEvent: (data: any) => void, onState?: (s: StreamState) => void): () => void {
  const ctrl = new AbortController();
  let last = 0;
  let stopped = false;
  (async () => {
    // Reconnect after 1, 2, 5, 10 s, then every 20 s: a free-tier host can take about a minute to wake up or restart.
    const delays = [1000, 2000, 5000, 10_000, 20_000];
    let attempt = 0;
    while (!stopped) {
      try {
        const headers = await authHeaders({ Accept: "text/event-stream" });
        if (last) headers.set("Last-Event-ID", String(last));
        const res = await fetch(`/api/v1${path}`, { headers, signal: ctrl.signal });
        if (res.status === 401) onUnauthorized?.();
        if (res.status === 401 || res.status === 403 || res.status === 404) return onState?.("closed");
        if (!res.ok || !res.body) throw new Error(String(res.status));
        attempt = 0;
        onState?.("live");
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, i);
            buf = buf.slice(i + 2);
            let id = 0;
            let data = "";
            for (const line of frame.split("\n")) {
              if (line.startsWith("id: ")) id = Number(line.slice(4));
              else if (line.startsWith("data: ")) data += line.slice(6);
            }
            if (data) {
              if (id) last = id;
              try {
                onEvent(JSON.parse(data));
              } catch {
                /* ignore malformed frame */
              }
            }
          }
        }
      } catch (e: any) {
        if (stopped || e?.name === "AbortError") return;
      }
      if (stopped) return;
      onState?.("reconnecting");
      await new Promise((r) => setTimeout(r, delays[Math.min(attempt++, delays.length - 1)]));
    }
  })();
  return () => {
    stopped = true;
    ctrl.abort();
  };
}

const ORDER: Role[] = ["viewer", "accountant", "approver", "owner"];
export const can = (role: Role | undefined, min: Role) => !!role && ORDER.indexOf(role) >= ORDER.indexOf(min);
