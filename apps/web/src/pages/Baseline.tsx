import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, ClipboardCheck } from "lucide-react";
import { api, can, errMsg, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr, isoDate } from "../lib/format";
import { Empty, LoadError, Skeleton, Spinner } from "../components/ui";

type Person = { id: string; name: string | null } | null;
type Line = { description: string; qty: number; unit_price_minor: number; amount_minor?: number };
type PastInvoice = {
  id: string; vendor_id: string; vendor: string | null; invoice_number: string; invoice_date: string; total_minor: number;
  po_number: string | null; line_items: Line[]; source: string; entered_by: Person;
};
type PO = { id: string; vendor_id: string; vendor: string | null; po_number: string; po_date: string; lines: Line[]; source: string; entered_by: Person };
type Pending = { invoices: number; purchase_orders: number; invoice_items: PastInvoice[]; po_items: PO[] };

const SOURCE: Record<string, string> = { import: "CSV import", manual: "Entered by hand", case: "From a case" };

/** Approver queue (GET /baseline/pending): past invoices and purchase orders that don't count in price, PO and quantity
 *  comparisons until an approver approves them. Pending rows still count for duplicate detection. */
export default function Baseline() {
  const { user } = useAuth();
  const approver = can(user?.role, "approver");
  const [data, setData] = useState<Pending | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<Pending>("/baseline/pending").then((r) => { setData(r); setErr(null); }).catch((e) => setErr(errMsg(e))), []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <div>
        <h1 className="page-title">Approvals</h1>
        <p className="text-sm text-muted">
          Past invoices and purchase orders entered by accountants are compared against only after an approver checks them. Until then they still count for duplicate detection.
        </p>
      </div>
      {err ? <LoadError error={`Could not load the approval queue. ${err}`} onRetry={load} /> : !data ? (
        <div className="flex flex-col gap-4" aria-busy><Skeleton className="h-40" /><Skeleton className="h-40" /></div>
      ) : data.invoices + data.purchase_orders === 0 ? (
        <div className="card"><Empty icon={<CheckCircle2 size={28} />} title="Nothing waiting for approval"><p className="text-sm text-muted">New past invoices and purchase orders from accountants appear here.</p></Empty></div>
      ) : (
        <>
          <Queue
            title="Past invoices" total={data.invoices} rows={data.invoice_items} approver={approver} endpoint="/history/approve" onDone={load}
            noun={["past invoice", "past invoices"]}
            head={["Vendor", "Invoice", "Date", "Total", "Entered"]}
            cells={(r) => [
              <Link to={`/vendors/${r.vendor_id}`} className="font-medium hover:underline">{r.vendor ?? "Unknown vendor"}</Link>,
              <span>{r.invoice_number}{r.po_number && <span className="text-muted"> · PO {r.po_number}</span>}<span className="block text-xs text-muted">{r.line_items.length} line{r.line_items.length === 1 ? "" : "s"}</span></span>,
              isoDate(r.invoice_date),
              <span className="tabular-nums">{inr(r.total_minor)}</span>,
              <Entered r={r} />,
            ]}
          />
          <Queue
            title="Purchase orders" total={data.purchase_orders} rows={data.po_items} approver={approver} endpoint="/purchase-orders/approve" onDone={load}
            noun={["purchase order", "purchase orders"]}
            head={["Vendor", "PO", "Date", "Value", "Entered"]}
            cells={(r) => [
              <Link to={`/vendors/${r.vendor_id}`} className="font-medium hover:underline">{r.vendor ?? "Unknown vendor"}</Link>,
              <span>{r.po_number}<span className="block text-xs text-muted">{r.lines.length} line{r.lines.length === 1 ? "" : "s"}</span></span>,
              isoDate(r.po_date),
              <span className="tabular-nums">{inr(r.lines.reduce((a, l) => a + l.qty * l.unit_price_minor, 0))}</span>,
              <Entered r={r} />,
            ]}
          />
        </>
      )}
    </div>
  );
}

function Entered({ r }: { r: { entered_by: Person; source: string } }) {
  return <span className="text-xs text-muted">{r.entered_by?.name ?? "—"}<span className="block">{SOURCE[r.source] ?? r.source}</span></span>;
}

function Queue<T extends { id: string }>({ title, total, rows, approver, endpoint, onDone, noun, head, cells }: {
  title: string; total: number; rows: T[]; approver: boolean; endpoint: string; onDone: () => void;
  noun: [string, string]; head: string[]; cells: (r: T) => ReactNode[];
}) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setSel((s) => new Set([...s].filter((id) => rows.some((r) => r.id === id)))), [rows]);
  const all = rows.length > 0 && sel.size === rows.length;
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const approve = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await post<{ approved: number }>(endpoint, { ids: [...sel] });
      setMsg({ ok: true, text: `Approved ${r.approved} ${r.approved === 1 ? noun[0] : noun[1]}. They now count in comparisons.` });
      setSel(new Set());
      onDone();
    } catch (e) {
      setMsg({ ok: false, text: errMsg(e) });
    } finally {
      setBusy(false);
    }
  };
  const shownLabel = useMemo(() => (total > rows.length ? `showing ${rows.length} of ${total}` : String(total)), [total, rows.length]);
  if (total === 0) return null;
  return (
    <section className="card overflow-hidden" aria-label={title}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><ClipboardCheck size={15} aria-hidden />{title}<span className="font-normal text-muted tabular-nums">{shownLabel}</span></h2>
        {approver ? (
          <button className="btn btn-primary !py-1.5 text-sm" disabled={busy || sel.size === 0} onClick={approve}>
            {busy && <Spinner />}Approve{sel.size ? ` ${sel.size}` : ""}
          </button>
        ) : <span className="text-xs text-muted">Only approvers can approve</span>}
      </div>
      {msg && <div role={msg.ok ? "status" : "alert"} className={`px-4 py-2.5 text-sm ${msg.ok ? "bg-low-soft text-low" : "bg-high-soft text-high"}`}>{msg.text}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              {approver && (
                <th className="w-10 px-4 py-2">
                  <input type="checkbox" aria-label={`Select all ${noun[1]}`} checked={all} onChange={() => setSel(all ? new Set() : new Set(rows.map((r) => r.id)))} />
                </th>
              )}
              {head.map((h) => <th key={h} className="px-3 py-2 font-medium whitespace-nowrap">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-line align-top">
                {approver && <td className="px-4 py-3"><input type="checkbox" aria-label={`Select ${noun[0]}`} checked={sel.has(r.id)} onChange={() => toggle(r.id)} /></td>}
                {cells(r).map((c, i) => <td key={i} className="px-3 py-3 whitespace-nowrap">{c}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

