import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Building2, CheckCircle2, CircleDashed } from "lucide-react";
import { api } from "../lib/api";
import { inr, isoDate } from "../lib/format";
import { Skeleton } from "../components/ui";

export function Vendors() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { api("/vendors").then((r) => setRows(r.items)); }, []);
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">Vendors</h1>
        <p className="text-sm text-muted">Vendor master — the internal baseline every invoice is compared against.</p>
      </div>
      <div className="card overflow-x-auto">
        {!rows ? <div className="p-4"><Skeleton className="h-24" /></div> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted">{["Vendor", "GSTIN", "Website", "Invoices", "Cases"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody>
              {rows.map((v) => (
                <tr key={v.id} className="border-t border-line hover:bg-surface-2">
                  <td className="px-4 py-2.5"><Link className="font-medium hover:underline" to={`/vendors/${v.id}`}>{v.name}</Link></td>
                  <td className="px-4 py-2.5 font-mono text-xs">{v.gstin}</td>
                  <td className="px-4 py-2.5">{v.website}</td>
                  <td className="px-4 py-2.5 tabular-nums">{v.invoices}</td>
                  <td className="px-4 py-2.5 tabular-nums">{v.cases}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function VendorDetail() {
  const { id = "" } = useParams();
  const [v, setV] = useState<any | null>(null);
  useEffect(() => { api(`/vendors/${id}`).then(setV); }, [id]);
  if (!v) return <Skeleton className="h-64" />;
  const prices = v.price_history.map((h: any) => ({ date: isoDate(h.date), price: h.items?.[0]?.unit_price_minor ?? 0, n: h.invoice_number }));
  const max = Math.max(...prices.map((p: any) => p.price), 1);
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <Link to="/vendors" className="inline-flex items-center gap-1 text-xs text-muted"><ArrowLeft size={13} />Vendors</Link>
      <div className="card flex items-start gap-3 p-4">
        <Building2 className="text-accent" />
        <div>
          <h1 className="text-lg font-bold">{v.name}</h1>
          <div className="text-sm text-muted">{v.gstin} · {v.address}</div>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {[["Bank accounts", v.accounts.map((a: any) => [a.account + (a.ifsc ? ` · ${a.ifsc}` : ""), a.verified, a.verified_method])],
          ["Domains", v.domains.map((d: any) => [d.domain, d.verified, null])],
          ["Contacts", v.contacts.map((c: any) => [`${c.name ?? ""} <${c.email}>`, c.verified, null])]].map(([title, items]: any) => (
          <div key={title} className="card p-4">
            <div className="label mb-2">{title}</div>
            <ul className="flex flex-col gap-1.5 text-sm">
              {items.map(([t, ok, m]: any) => (
                <li key={t} className="flex items-center gap-2">
                  {ok ? <CheckCircle2 size={14} className="text-low" aria-label="verified" /> : <CircleDashed size={14} className="text-medium" aria-label="unverified" />}
                  <span className="break-all">{t}</span>{m && <span className="text-xs text-muted">({m.replace(/_/g, " ")})</span>}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="card p-4">
        <div className="label mb-3">Unit price history</div>
        <div className="flex h-36 items-end gap-1.5" role="img" aria-label="Unit price history bar chart">
          {prices.map((p: any) => (
            <div key={p.n} className="group relative flex flex-1 flex-col items-center justify-end" title={`${p.date} · ${p.n} · ${inr(p.price)}`}>
              <div className="w-full max-w-8 rounded-t bg-accent opacity-80 group-hover:opacity-100" style={{ height: `${(p.price / max) * 100}%` }} />
            </div>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-muted"><span>{prices[0]?.date}</span><span>{prices.at(-1)?.date}</span></div>
      </div>
      {v.prior_cases.length > 0 && (
        <div className="card p-4">
          <div className="label mb-2">Prior cases</div>
          {v.prior_cases.map((p: any) => <div key={p.case_id} className="text-sm"><b>{p.outcome}</b> · peak {p.peak_score} · {p.summary}</div>)}
        </div>
      )}
    </div>
  );
}
