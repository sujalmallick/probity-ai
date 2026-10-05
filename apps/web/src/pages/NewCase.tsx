import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Copy, FileUp, Pencil, Play, XCircle } from "lucide-react";
import { errMsg, post, uploadFile } from "../lib/api";
import { inr } from "../lib/format";
import { Spinner } from "../components/ui";

type Field = { value: any; raw?: string; confidence: number; evidence_snippet?: string; via?: string };
const ORDER = ["vendor_name", "gstin", "invoice_number", "invoice_date", "due_date", "po_number", "subtotal", "tax", "total", "bank_account", "ifsc", "vendor_email", "vendor_address"];
const MONEY = new Set(["subtotal", "tax", "total"]);
const EDITABLE = new Set(["vendor_name", "gstin", "invoice_number", "invoice_date", "due_date", "po_number", "ifsc", "vendor_email", "vendor_address"]);

export default function NewCase() {
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [doc, setDoc] = useState<{ document_id: string; sha256: string; duplicate_of: string | null } | null>(null);
  const [preview, setPreview] = useState<{ fields: Record<string, Field>; validation: Record<string, any>; low_confidence: string[]; injection_detected: boolean } | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  const pick = async (f: File) => {
    setFile(f);
    setErr(null);
    setDoc(null);
    setPreview(null);
    setEdits({});
    setBusy("Uploading and scanning…");
    try {
      const d = await uploadFile(f, f.name);
      setDoc(d);
      // A file uploaded before but never investigated (e.g. the earlier preview failed) continues from that upload:
      // the server returns the existing document. Only an existing case stops here, so one file never becomes two cases.
      if (!d.duplicate_of?.startsWith("case_")) {
        setBusy("Reading the document…");
        setPreview(await post(`/documents/${d.document_id}/preview`));
      }
    } catch (e: any) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  };

  const start = async () => {
    if (!doc) return;
    setBusy("Starting investigation…");
    try {
      const corrections = Object.fromEntries(Object.entries(edits).filter(([, v]) => v.trim() !== ""));
      const c = await post<{ case_id: string }>("/cases", { document_id: doc.document_id, corrections: Object.keys(corrections).length ? corrections : undefined });
      nav(`/cases/${c.case_id}`);
    } catch (e: any) {
      setErr(errMsg(e));
      setBusy(null);
    }
  };

  const show = (k: string, f: Field) => (MONEY.has(k) && typeof f.value === "number" ? inr(f.value, true) : String(f.value ?? "—"));
  const keys = preview ? [...ORDER.filter((k) => k in preview.fields || preview.low_confidence.includes(k)), ...Object.keys(preview.fields).filter((k) => !ORDER.includes(k) && k !== "line_items" && k !== "sender_domain" && k !== "currency" && k !== "bank_name" && k !== "vendor_phone")] : [];

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div>
        <h1 className="page-title">New investigation</h1>
        <p className="text-sm text-muted">PDF, image or email file, up to 15 MB.</p>
      </div>
      <div
        className={`card flex cursor-pointer flex-col items-center justify-center gap-2 border-2 border-dashed px-6 py-12 text-center ${drag ? "!border-accent bg-accent-soft" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) pick(f); }}
        role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <FileUp size={28} className="text-accent" />
        <div className="font-semibold">{file ? file.name : "Drop an invoice here, or click to choose"}</div>
        <div className="text-xs text-muted">You'll see what was extracted — and can correct it — before anything is investigated.</div>
        <input ref={input} type="file" accept=".pdf,.png,.jpg,.jpeg,.eml,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
      </div>
      {busy && <div className="flex items-center gap-2 text-sm text-muted"><Spinner />{busy}</div>}
      {err && <div className="rounded-lg bg-high-soft p-3 text-sm text-high">{err}</div>}

      {doc?.duplicate_of && (
        <div className="card fade-in flex flex-wrap items-center justify-between gap-2 p-4 text-sm">
          {doc.duplicate_of.startsWith("case_") ? (
            <>
              <span className="flex items-center gap-2 text-medium"><Copy size={15} />This exact file has already been investigated.</span>
              <button className="btn" onClick={() => nav(`/cases/${doc.duplicate_of}`)}>Open existing case</button>
            </>
          ) : (
            <span className="flex items-center gap-2 text-muted"><Copy size={15} />You uploaded this exact file before but didn't start an investigation, so we're continuing with that upload.</span>
          )}
        </div>
      )}

      {preview && (
        <div className="card fade-in flex flex-col gap-4 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold">Extracted fields</div>
            {preview.low_confidence.length > 0
              ? <span className="inline-flex items-center gap-1 text-xs font-medium text-medium"><AlertTriangle size={13} />{preview.low_confidence.length} field(s) need review</span>
              : <span className="inline-flex items-center gap-1 text-xs font-medium text-low"><CheckCircle2 size={13} />All required fields read with high confidence</span>}
          </div>
          {preview.injection_detected && <div className="rounded-lg bg-high-soft p-3 text-sm text-high">This document contains instruction-like text. It will be neutralized and flagged as a risk indicator.</div>}
          <table className="w-full text-sm">
            <tbody>
              {keys.map((k) => {
                const f = preview.fields[k];
                const low = preview.low_confidence.includes(k);
                return (
                  <tr key={k} className="border-t border-line align-top">
                    <td className="w-40 py-2 pr-2 text-muted">{k.replace(/_/g, " ")}</td>
                    <td className="py-2 pr-2">
                      {EDITABLE.has(k) && (low || k in edits) ? (
                        <input className="input !py-1" aria-label={`Correct ${k}`} value={edits[k] ?? (f ? String(f.value ?? "") : "")} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} placeholder="Not found — type the value as printed" />
                      ) : (
                        <div className="flex items-center gap-2">
                          <span className="font-medium break-all">{f ? show(k, f) : "—"}</span>
                          {EDITABLE.has(k) && <button className="text-muted hover:text-ink" aria-label={`Edit ${k}`} onClick={() => setEdits({ ...edits, [k]: String(f?.value ?? "") })}><Pencil size={13} /></button>}
                        </div>
                      )}
                      {f?.evidence_snippet && <div className="mt-0.5 truncate text-xs text-muted" title={f.evidence_snippet}>“{f.evidence_snippet}”</div>}
                    </td>
                    <td className="w-16 py-2 text-right text-xs tabular-nums" style={{ color: low ? "var(--medium)" : "var(--muted)" }}>{f ? `${Math.round(f.confidence * 100)}%` : "missing"}</td>
                  </tr>
                );
              })}
              {(preview.fields.line_items?.value ?? []).map((li: any, i: number) => (
                <tr key={`li${i}`} className="border-t border-line">
                  <td className="py-2 pr-2 text-muted">line {i + 1}</td>
                  <td className="py-2" colSpan={2}>{li.description} · {li.qty} × {inr(li.unit_price_minor)} = {inr(li.amount_minor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex flex-wrap gap-3 text-xs">
            {Object.entries(preview.validation).filter(([k]) => k !== "injection").map(([k, v]: [string, any]) => (
              <span key={k} className="inline-flex items-center gap-1">
                {v.ok === true ? <CheckCircle2 size={13} className="text-low" /> : v.ok === false ? <XCircle size={13} className="text-high" /> : <span className="h-3 w-3 rounded-full border border-line" />}
                {k.replace(/_/g, " ")}
              </span>
            ))}
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted">{Object.keys(edits).length ? `${Object.keys(edits).length} correction(s) will be recorded as human-entered.` : "Corrections are recorded as human-entered values."}</span>
            <button className="btn btn-primary" onClick={start} disabled={!!busy}><Play size={15} />Start investigation</button>
          </div>
        </div>
      )}
    </div>
  );
}
