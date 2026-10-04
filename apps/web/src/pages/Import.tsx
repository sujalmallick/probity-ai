import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, ArrowLeft, CheckCircle2, Copy, Download, FileSpreadsheet, FileUp, RefreshCcw, RotateCcw, SkipForward } from "lucide-react";
import { api, can, fetchBlob, type Role } from "../lib/api";
import { useAuth } from "../lib/auth";
import { relTime } from "../lib/format";
import { Skeleton, Spinner } from "../components/ui";

type Any = Record<string, any>;
type Kind = "vendors" | "invoices" | "purchase_orders";
type Report = { kind: Kind; rows_total: number; rows_ok: number; error_count: number; errors: { row: number; field: string | null; message: string }[]; preview?: Any[]; created: number; updated: number; dry_run: boolean; committed: boolean };
// Server rule: marking rows verified in an import needs a note with at least this many letters or digits.
const VERIFY_NOTE_MIN = 10;
const alnum = (t: string) => (t.match(/[\p{L}\p{N}]/gu) ?? []).length;

const KINDS: { key: Kind; label: string }[] = [
  { key: "vendors", label: "Vendors" },
  { key: "invoices", label: "Past invoices" },
  { key: "purchase_orders", label: "Purchase orders" },
];

// ---------------------------------------------------------------- CSV helpers (RFC 4180: quotes, commas, newlines in quotes)

function parseCsv(text: string): string[][] {
  const out: string[][] = [];
  let row: string[] = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') q = false;
      else cell += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); out.push(row); row = []; cell = "";
    } else cell += ch;
  }
  if (cell !== "" || row.length) { row.push(cell); out.push(row); }
  return out.filter((r) => r.some((c) => c.trim() !== ""));
}
const csvCell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
const toCsv = (rows: string[][]) => rows.map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";

const isAccountCol = (c: string) => /bank_account/.test(c);
const mask = (v: string) => (v.replace(/\s/g, "").length > 4 ? `XXXX${v.replace(/\s/g, "").slice(-4)}` : v);
const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");

export default function ImportPage() {
  const { user } = useAuth();
  const role = user?.role as Role | undefined;
  const allowed = can(role, "accountant");
  const [params, setParams] = useSearchParams();
  const kind = (KINDS.some((k) => k.key === params.get("kind")) ? params.get("kind") : "vendors") as Kind;
  const [spec, setSpec] = useState<Any | null>(null);
  const [history, setHistory] = useState<Any[] | null>(null);
  const [vendors, setVendors] = useState<Any[]>([]);
  const [fileName, setFileName] = useState<string | null>(null);
  const [grid, setGrid] = useState<string[][] | null>(null); // header + rows, editable
  const [edited, setEdited] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [result, setResult] = useState<Report | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [drag, setDrag] = useState(false);
  const [verifyNote, setVerifyNote] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const loadHistory = () => api<{ items: Any[] }>("/imports").then((r) => setHistory(r.items)).catch(() => setHistory([]));
  useEffect(() => {
    api("/imports/spec").then(setSpec).catch((e) => setErr(e.message));
    api<{ items: Any[] }>("/vendors?include_archived=true").then((r) => setVendors(r.items)).catch(() => {});
    loadHistory();
  }, []);
  const startOver = () => { setFileName(null); setGrid(null); setReport(null); setResult(null); setEdited(false); setErr(null); setOnlyProblems(false); setVerifyNote(""); };
  useEffect(startOver, [kind]);

  const dryRun = async (rows: string[][], name: string) => {
    setBusy("Checking rows…");
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", new Blob([toCsv(rows)], { type: "text/csv" }), name);
      setReport(await api<Report>(`/imports/${kind}?dry_run=true`, { method: "POST", body: fd }));
      setEdited(false);
    } catch (e: any) {
      setErr(e.message);
      setReport(null);
    } finally {
      setBusy(null);
    }
  };
  const pick = async (f: File) => {
    startOver();
    if (!/\.csv$/i.test(f.name)) return setErr("Choose a .csv file. In Excel or Sheets use File → Save as / Download → CSV.");
    if (f.size > (5 << 20)) return setErr("This file is larger than 5 MB. Split it into smaller files.");
    const rows = parseCsv(await f.text());
    if (rows.length < 2) return setErr("The file has no data rows under the header.");
    setFileName(f.name);
    setGrid(rows);
    dryRun(rows, f.name);
  };
  const commit = async (skipInvalid: boolean) => {
    if (!grid || !fileName) return;
    setBusy("Importing…");
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", new Blob([toCsv(grid)], { type: "text/csv" }), fileName);
      const qs = new URLSearchParams({ dry_run: "false", ...(skipInvalid ? { skip_invalid: "true" } : {}), ...(verifiedRows ? { verification_note: verifyNote.trim() } : {}) });
      const r = await api<Report>(`/imports/${kind}?${qs}`, { method: "POST", body: fd });
      setResult(r);
      loadHistory();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };
  const template = async () => {
    try {
      const b = await fetchBlob(`/api/v1/imports/templates/${kind}`);
      const u = URL.createObjectURL(b);
      const a = document.createElement("a");
      a.href = u;
      a.download = `probity-${kind}-template.csv`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(u), 1000);
    } catch (e: any) {
      setErr(`Could not download the template: ${e.message}`);
    }
  };

  // ---- derived review data
  const header = grid?.[0] ?? [];
  const errs = useMemo(() => {
    const m: Record<number, { fields: Record<string, string[]>; row: string[] }> = {};
    for (const e of report?.errors ?? []) {
      const r = (m[e.row] ??= { fields: {}, row: [] });
      if (e.field && header.includes(e.field)) (r.fields[e.field] ??= []).push(e.message);
      else r.row.push(e.field ? `${e.field}: ${e.message}` : e.message);
    }
    return m;
  }, [report, header.join()]);
  // Information flags for vendor files: repeats inside the file and vendors that already exist.
  const notes = useMemo(() => {
    const m: Record<number, { repeatOf?: number; existing?: string }> = {};
    if (kind !== "vendors" || !grid) return m;
    const gi = header.indexOf("gstin"), ni = header.indexOf("name");
    const seen = new Map<string, number>();
    const known = new Map<string, string>();
    vendors.forEach((v) => { if (v.gstin) known.set(`g:${v.gstin.toUpperCase()}`, v.name); known.set(`n:${norm(v.name)}`, v.name); });
    grid.slice(1).forEach((r, i) => {
      const rowNo = i + 2;
      const key = gi >= 0 && r[gi]?.trim() ? `g:${r[gi].trim().toUpperCase()}` : ni >= 0 ? `n:${norm(r[ni] ?? "")}` : "";
      if (!key || key === "n:") return;
      if (seen.has(key)) m[rowNo] = { ...m[rowNo], repeatOf: seen.get(key) };
      else seen.set(key, rowNo);
      const name = known.get(key) ?? (ni >= 0 ? known.get(`n:${norm(r[ni] ?? "")}`) : undefined);
      if (name) m[rowNo] = { ...m[rowNo], existing: name };
    });
    return m;
  }, [grid, vendors, kind]);
  const visibleCols = header.map((c, i) => ({ c, i })).filter(({ i }) => grid!.slice(1).some((r) => (r[i] ?? "").trim() !== "") || Object.values(errs).some((e) => e.fields[header[i]]));
  const badRows = Object.keys(errs).length;
  const verifiedRows = (report?.preview ?? []).filter((r: Any) => r.bank_verified || r.contact_verified).length;
  const noteMissing = verifiedRows > 0 && alnum(verifyNote) < VERIFY_NOTE_MIN;
  const shownRows = (grid?.slice(1) ?? []).map((r, i) => ({ r, rowNo: i + 2 })).filter(({ rowNo }) => !onlyProblems || errs[rowNo]);
  const setCell = (rowNo: number, col: number, value: string) => {
    setGrid((g) => g!.map((r, i) => {
      if (i !== rowNo - 1) return r;
      const next = [...r];
      while (next.length <= col) next.push("");
      next[col] = value;
      return next;
    }));
    setEdited(true);
  };
  const deleteRow = (rowNo: number) => { setGrid((g) => g!.filter((_, i) => i !== rowNo - 1)); setEdited(true); };

  if (!allowed) {
    return (
      <div className="mx-auto max-w-3xl">
        <BackLink />
        <div className="card mt-4 p-6 text-sm text-muted">Importing requires the accountant role. Ask your workspace owner for access.</div>
      </div>
    );
  }

  const s = spec?.[kind];
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <BackLink />
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Import from CSV</h1>
          <p className="text-sm text-muted">Check every row before anything is saved.</p>
        </div>
        <div className="flex gap-1 rounded-xl border border-line bg-surface p-1" role="group" aria-label="What to import">
          {KINDS.map((k) => (
            <button key={k.key} aria-pressed={kind === k.key} onClick={() => setParams({ kind: k.key })}
              className={`rounded-lg px-3 py-1.5 text-sm transition-colors duration-150 ${kind === k.key ? "bg-surface-2 font-medium text-ink shadow-[inset_0_0_0_1px_var(--border)]" : "text-muted hover:text-ink"}`}>
              {k.label}
            </button>
          ))}
        </div>
      </header>

      {kind !== "vendors" && <div className="rounded-lg border border-line bg-surface px-4 py-3 text-sm text-muted">Import vendors first: rows are matched to vendors by GSTIN or exact name.</div>}
      {err && <div role="alert" className="flex items-start gap-2 rounded-lg bg-high-soft p-3 text-sm text-high"><AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />{err}</div>}

      {/* Step 1: file */}
      {!grid && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div
            className={`card flex cursor-pointer flex-col items-center justify-center gap-3 border-2 border-dashed px-6 py-16 text-center transition-colors ${drag ? "!border-accent bg-surface-2" : ""}`}
            onClick={() => input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) pick(f); }}
            role="button" tabIndex={0} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
            aria-label="Choose a CSV file"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-2"><FileUp size={22} aria-hidden /></div>
            <div className="font-medium">Drop a CSV here, or click to choose</div>
            <div className="text-xs text-muted">Up to 5 MB · 20,000 rows</div>
            <input ref={input} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
          </div>
          <aside className="card flex flex-col gap-3 p-5">
            <div className="flex items-center gap-2 font-semibold"><FileSpreadsheet size={16} aria-hidden />Columns</div>
            {!s ? <Skeleton className="h-28" /> : (
              <>
                <ul className="flex flex-wrap gap-1.5">
                  {s.columns.map((c: string) => (
                    <li key={c} className={`rounded-md px-2 py-0.5 font-mono text-[11px] ${s.required.includes(c) ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted"}`} title={s.required.includes(c) ? "Required" : "Optional"}>
                      {c}{s.required.includes(c) ? " *" : ""}
                    </li>
                  ))}
                </ul>
                <p className="text-xs leading-5 text-muted">{s.help}</p>
              </>
            )}
            <button className="btn mt-auto" onClick={template}><Download size={15} aria-hidden />Download template</button>
          </aside>
        </div>
      )}

      {busy && <div className="flex items-center gap-2 text-sm text-muted"><Spinner />{busy}</div>}

      {/* Step 3: result */}
      {result && (
        <section className="card fade-in p-6" aria-labelledby="done-title">
          <div className="flex items-center gap-3">
            <CheckCircle2 size={22} className="text-low" aria-hidden />
            <h2 id="done-title" className="text-lg font-medium">Import finished</h2>
          </div>
          <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[["Added", result.created], ["Updated", result.updated], ["Skipped", result.rows_total - result.rows_ok], ["Failed", result.committed ? 0 : result.error_count]].map(([k, v]) => (
              <div key={k as string} className="rounded-xl border border-line px-4 py-3">
                <dt className="text-xs text-muted">{k}</dt>
                <dd className="kpi-value mt-1">{v as number}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link to="/vendors" className="btn btn-primary">Go to vendors</Link>
            <button className="btn" onClick={startOver}><RotateCcw size={14} aria-hidden />Import another file</button>
          </div>
        </section>
      )}

      {/* Step 2: review */}
      {grid && report && !result && (
        <section className="card fade-in overflow-hidden" aria-labelledby="review-title">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
            <div>
              <h2 id="review-title" className="font-semibold">{fileName}</h2>
              <p className="mt-0.5 text-sm text-muted">
                {report.rows_total} rows · <span className="text-low">{report.rows_ok} ready</span>
                {badRows > 0 && <> · <span className="text-high">{badRows} need fixing</span></>}
                {kind === "vendors" && Object.values(notes).some((n) => n.existing) && <> · {Object.values(notes).filter((n) => n.existing).length} update existing vendors</>}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {badRows > 0 && (
                <label className="flex items-center gap-2 text-sm text-muted"><input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />Only rows with problems</label>
              )}
              <button className="btn !py-1.5 text-sm" onClick={startOver}>Choose another file</button>
            </div>
          </div>

          <div className="max-h-[520px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-surface">
                <tr className="text-left text-xs text-muted">
                  <th className="px-3 py-2 font-medium">Row</th>
                  {visibleCols.map(({ c }) => <th key={c} className="px-3 py-2 font-mono font-medium whitespace-nowrap normal-case">{c}{s?.required.includes(c) ? " *" : ""}</th>)}
                  <th className="px-3 py-2"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {shownRows.map(({ r, rowNo }) => {
                  const e = errs[rowNo];
                  const n = notes[rowNo];
                  return (
                    <tr key={rowNo} className={`border-t border-line align-top ${e ? "bg-high-soft/40" : ""}`}>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <div className="flex items-center gap-1.5 tabular-nums">
                          {e ? <AlertCircle size={14} className="text-high" aria-label="Has problems" /> : <CheckCircle2 size={14} className="text-low" aria-label="Ready" />}
                          {rowNo}
                        </div>
                        {n?.repeatOf && <div className="mt-1 flex items-center gap-1 text-[11px] text-muted" title="Extra rows add bank accounts or contacts to the same vendor"><Copy size={11} aria-hidden />Same as row {n.repeatOf}</div>}
                        {n?.existing && !n.repeatOf && <div className="mt-1 text-[11px] text-muted" title={`Matches existing vendor ${n.existing}`}>Updates existing</div>}
                        {e?.row.map((m) => <div key={m} className="mt-1 max-w-[180px] text-[11px] text-high">{m}</div>)}
                      </td>
                      {visibleCols.map(({ c, i }) => {
                        const val = r[i] ?? "";
                        const fe = e?.fields[c];
                        return (
                          <td key={c} className="px-3 py-2.5">
                            {fe ? (
                              <div className="flex min-w-[160px] flex-col gap-1">
                                <input
                                  className="input !border-high/60 !py-1 text-xs"
                                  value={val}
                                  aria-label={`Row ${rowNo}, ${c}`}
                                  aria-invalid
                                  aria-describedby={`e-${rowNo}-${c}`}
                                  autoComplete="off"
                                  onChange={(ev) => setCell(rowNo, i, ev.target.value)}
                                />
                                <span id={`e-${rowNo}-${c}`} className="text-[11px] text-high">{fe.join("; ")}</span>
                              </div>
                            ) : (
                              <span className={`block max-w-[220px] truncate ${isAccountCol(c) ? "font-mono" : ""} ${val ? "" : "text-muted"}`} title={isAccountCol(c) ? undefined : val}>
                                {val ? (isAccountCol(c) ? mask(val) : val) : "—"}
                              </span>
                            )}
                          </td>
                        );
                      })}
                      <td className="px-3 py-2.5 text-right">
                        {e && <button className="text-xs whitespace-nowrap text-muted hover:text-high hover:underline" onClick={() => deleteRow(rowNo)} aria-label={`Remove row ${rowNo} from this import`}>Remove row</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {verifiedRows > 0 && (
            <div className="border-t border-line px-5 py-4">
              <label htmlFor="imp-vnote" className="text-sm font-medium">How were these verified out-of-band?</label>
              <p className="mb-2 text-xs text-muted">{verifiedRows} {verifiedRows === 1 ? "row marks" : "rows mark"} a bank account or contact as verified. Say how they were confirmed (required, saved to the audit log). Only approvers can import verified details.</p>
              <textarea id="imp-vnote" className="input h-20 placeholder:text-muted/60" value={verifyNote} onChange={(e) => setVerifyNote(e.target.value)} placeholder="e.g. Each account was confirmed by phone using the contact numbers from our onboarding records." aria-describedby="imp-vnote-count" />
              <div id="imp-vnote-count" className="mt-1 text-right text-xs text-muted tabular-nums">{Math.min(alnum(verifyNote), VERIFY_NOTE_MIN)}/{VERIFY_NOTE_MIN}</div>
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-4">
            <p className="text-xs text-muted">{edited ? "You changed some rows. Check them again before importing." : badRows ? "Fix the highlighted cells and check again, or import only the ready rows." : "Every row is ready."}</p>
            <div className="flex flex-wrap gap-2">
              {edited && <button className="btn" disabled={!!busy} onClick={() => dryRun(grid, fileName!)}><RefreshCcw size={14} aria-hidden />Check again</button>}
              {!edited && badRows > 0 && report.rows_ok > 0 && (
                <button className="btn" disabled={!!busy || noteMissing} title={noteMissing ? "Describe how the verified rows were confirmed first" : ""} onClick={() => commit(true)}><SkipForward size={14} aria-hidden />Import {report.rows_ok} ready, skip {report.rows_total - report.rows_ok}</button>
              )}
              <button
                className="btn btn-primary"
                disabled={!!busy || edited || badRows > 0 || noteMissing}
                title={edited ? "Check the edited rows first" : badRows ? "Fix or skip the rows with problems first" : noteMissing ? "Describe how the verified rows were confirmed first" : ""}
                onClick={() => commit(false)}
              >
                Import {report.rows_total} rows
              </button>
            </div>
          </div>
        </section>
      )}

      {/* History */}
      <section className="card overflow-hidden" aria-labelledby="hist-title">
        <h2 id="hist-title" className="border-b border-line px-5 py-3.5 text-sm font-semibold">Past imports</h2>
        {!history ? <div className="p-4"><Skeleton className="h-16" /></div> : history.length === 0 ? (
          <p className="px-5 py-6 text-sm text-muted">No imports yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted">{["File", "Type", "Added", "Updated", "Skipped", "By", "When"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id} className="border-t border-line">
                    <td className="max-w-[220px] truncate px-4 py-2.5">{h.filename}</td>
                    <td className="px-4 py-2.5 text-muted">{KINDS.find((k) => k.key === h.kind)?.label ?? h.kind}</td>
                    <td className="px-4 py-2.5 tabular-nums">{h.created ?? 0}</td>
                    <td className="px-4 py-2.5 tabular-nums">{h.updated ?? 0}</td>
                    <td className="px-4 py-2.5 tabular-nums">{h.skipped ?? 0}</td>
                    <td className="px-4 py-2.5 text-muted">{h.by ?? "—"}</td>
                    <td className="px-4 py-2.5 whitespace-nowrap text-muted">{h.at ? relTime(h.at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function BackLink() {
  return <Link to="/vendors" className="inline-flex w-fit items-center gap-1.5 rounded-md text-sm text-muted hover:text-ink"><ArrowLeft size={15} aria-hidden />Vendors</Link>;
}
