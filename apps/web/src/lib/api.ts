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

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function authHeaders(init?: HeadersInit): Promise<Headers> {
  const h = new Headers(init);
  const t = await token();
  if (t) h.set("Authorization", `Bearer ${t}`);
  return h;
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = await authHeaders(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!res.ok) {
    let msg = res.statusText;
    let code: string | undefined;
    try {
      const body = await res.json();
      msg = body.error?.message ?? msg;
      code = body.error?.code;
    } catch {
      /* non-JSON error */
    }
    if (res.status === 413 || code === "payload_too_large") {
      const { max_upload_mb, max_import_mb } = currentConfig().limits;
      msg = `That's too large to upload. Invoices can be up to ${max_upload_mb} MB and CSV files up to ${max_import_mb} MB.`;
    }
    if (res.status === 401) onUnauthorized?.();
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
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
  const res = await fetch(path, { headers: await authHeaders() });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.blob();
}

/** Server-Sent Events over fetch (so the bearer token travels in a header, never the URL).
 *  Reconnects with Last-Event-ID; returns a stop function. */
export function streamEvents(path: string, onEvent: (data: any) => void): () => void {
  const ctrl = new AbortController();
  let last = 0;
  let stopped = false;
  (async () => {
    let backoff = 500;
    while (!stopped) {
      try {
        const headers = await authHeaders({ Accept: "text/event-stream" });
        if (last) headers.set("Last-Event-ID", String(last));
        const res = await fetch(`/api/v1${path}`, { headers, signal: ctrl.signal });
        if (res.status === 401 || res.status === 403 || res.status === 404) return;
        if (!res.ok || !res.body) throw new Error(String(res.status));
        backoff = 500;
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
      await new Promise((r) => setTimeout(r, backoff));
      backoff = Math.min(backoff * 2, 10_000);
    }
  })();
  return () => {
    stopped = true;
    ctrl.abort();
  };
}

const ORDER: Role[] = ["viewer", "accountant", "approver", "owner"];
export const can = (role: Role | undefined, min: Role) => !!role && ORDER.indexOf(role) >= ORDER.indexOf(min);
