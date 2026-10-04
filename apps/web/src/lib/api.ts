export type Role = "viewer" | "accountant" | "approver" | "owner";
export type Tier = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Me {
  id: string;
  name: string;
  role: Role;
  workspace?: { id: string; name: string };
}

const KEY = "probity.session";

export function getSession(): { token: string; user: Me } | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setSession(s: { token: string; user: Me } | null) {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable — session lives for this tab only */
  }
  memorySession = s;
}

let memorySession: { token: string; user: Me } | null = null;

export function token(): string | null {
  return (getSession() ?? memorySession)?.token ?? null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = token();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).error?.message ?? msg;
    } catch {
      /* non-JSON error */
    }
    if (res.status === 401) setSession(null);
    throw new ApiError(res.status, msg);
  }
  return res.json();
}

export const post = <T = any>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export async function uploadFile(file: File | Blob, name: string) {
  const fd = new FormData();
  fd.append("file", file, name);
  return api<{ document_id: string; sha256: string; filename: string; duplicate_of: string | null }>("/documents", { method: "POST", body: fd });
}

export async function fetchBlob(path: string): Promise<Blob> {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token()}` } });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.blob();
}

const ORDER: Role[] = ["viewer", "accountant", "approver", "owner"];
export const can = (role: Role | undefined, min: Role) => !!role && ORDER.indexOf(role) >= ORDER.indexOf(min);
