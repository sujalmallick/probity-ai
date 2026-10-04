export function inr(minor: number | null | undefined, paise = false): string {
  if (minor === null || minor === undefined) return "—";
  const neg = minor < 0;
  const abs = Math.abs(minor);
  const rupees = Math.floor(abs / 100);
  const p = abs % 100;
  let s = String(rupees);
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    s = groups.join(",") + "," + tail;
  }
  return `${neg ? "-" : ""}₹${s}${paise || p ? "." + String(p).padStart(2, "0") : ""}`;
}

/** An amount in the currency the invoice states. Only INR gets ₹; other currencies keep their code and are never
 *  converted; with no stated currency the bare number is shown and labelled. */
export function money(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor === null || minor === undefined) return "—";
  if (currency === "INR") return inr(minor);
  const n = (minor / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!currency) return `${n} (currency not stated)`;
  if (currency === "MIXED") return `${n} (mixed currencies)`;
  if (currency === "UNRECOGNISED") return `${n} (unrecognised currency)`;
  return `${currency} ${n}`;
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function isoDate(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "";
}

export const tierColor: Record<string, string> = {
  LOW: "var(--low)",
  MEDIUM: "var(--medium)",
  HIGH: "var(--high)",
  CRITICAL: "var(--critical)",
};

export const tierSoft: Record<string, string> = {
  LOW: "var(--low-soft)",
  MEDIUM: "var(--medium-soft)",
  HIGH: "var(--high-soft)",
  CRITICAL: "var(--critical-soft)",
};

export const STATUS_LABEL: Record<string, string> = {
  QUEUED: "Queued",
  EXTRACTING: "Extracting",
  INVESTIGATING: "Investigating",
  VERIFYING: "Verifying",
  SCORING: "Scoring",
  AUTO_CLEARED: "Auto-cleared",
  AWAITING_HUMAN: "Awaiting decision",
  AWAITING_VENDOR: "Awaiting vendor",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  CLOSED: "Closed",
  FAILED: "Failed",
};

export const RUNNING = new Set(["QUEUED", "EXTRACTING", "INVESTIGATING", "VERIFYING", "SCORING"]);

/** Display value for evidence: minor-unit money fields in the given currency (₹ only for INR). */
export function evValue(field: string | null, value: unknown, currency: string | null = "INR"): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" && field && /(price|total|amount|subtotal|tax)/.test(field)) return money(value, currency);
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
