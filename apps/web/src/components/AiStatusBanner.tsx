import { useEffect, useState } from "react";
import { BotOff } from "lucide-react";
import { api } from "../lib/api";

type AiStatus = { ok: boolean; provider: string; problem: { code: string; reason: string; since: string | null } | null };

const TITLE: Record<string, string> = {
  credits_exhausted: "AI is off: credits have run out",
  quota_exhausted: "AI is off: usage quota is used up",
  key_invalid: "AI is off: the API key was rejected",
  not_configured: "AI is off: no API key is set",
};

/** Account-level AI problem (credits/quota used up, key rejected, not configured), from GET /ai/status. Results keep
 *  working with rules and are labelled "rule-based fallback"; this says why, once, where people will see it. */
export function AiStatusBanner({ className = "" }: { className?: string }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  useEffect(() => {
    let live = true;
    api<AiStatus>("/ai/status").then((s) => live && setStatus(s)).catch(() => undefined);
    return () => { live = false; };
  }, []);
  const p = status?.problem;
  if (!p) return null;
  return (
    <div role="status" className={`flex items-start gap-2 rounded-lg border border-medium/40 bg-medium-soft p-3 text-sm text-medium ${className}`}>
      <BotOff size={16} className="mt-0.5 shrink-0" aria-hidden />
      <div>
        <div className="font-semibold">{TITLE[p.code] ?? "AI is unavailable"}</div>
        <div className="text-xs">{p.reason} Investigations still run; anything the AI would have written is produced by rules and labelled.</div>
      </div>
    </div>
  );
}
