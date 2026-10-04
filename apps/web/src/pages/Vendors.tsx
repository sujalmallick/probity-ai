import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Archive, ArchiveRestore, ArrowLeft, Building2, CircleDashed, CreditCard, FileUp, Globe, History, Landmark, Mail, Pencil, Plus, Search, ShieldCheck, Trash2,
} from "lucide-react";
import { api, can, del, errMsg, patch, post, type Role } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr, isoDate, relTime } from "../lib/format";
import { RelGraph } from "../components/RelGraph";
import { CouldNotVerify, LoadError, Modal, Skeleton, Spinner, TierChip } from "../components/ui";

type Any = Record<string, any>;
type Verification = { by?: { id: string; name: string } | null; at?: string | null; method?: string | null; note?: string | null };

const VERIFY_NOTE_MIN = 5; // server rule (vendors._verify_gate)

/** A vendor counts as verified once it has at least one verified bank account and one verified contact. */
function isVerifiedVendor(v: Any) {
  const banks = v.verified_bank_accounts ?? v.accounts?.filter((a: Any) => a.verified).length ?? 0;
  const contacts = v.verified_contacts ?? v.contacts?.filter((c: Any) => c.verified).length ?? 0;
  return banks > 0 && contacts > 0;
}

function VendorStatus({ v }: { v: Any }) {
  return isVerifiedVendor(v)
    ? <span className="inline-flex items-center gap-1 rounded-full bg-low-soft px-2 py-0.5 text-xs font-medium whitespace-nowrap text-low"><ShieldCheck size={12} aria-hidden />Verified</span>
    : <span className="inline-flex items-center gap-1 rounded-full bg-medium-soft px-2 py-0.5 text-xs font-medium whitespace-nowrap text-medium"><CircleDashed size={12} aria-hidden />Needs verification</span>;
}

// ---------------------------------------------------------------- list

export function Vendors() {
  const { user } = useAuth();
  const role = user?.role as Role | undefined;
  const canEdit = can(role, "accountant");
  const nav = useNavigate();
  const [rows, setRows] = useState<Any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ ...(q.trim() ? { q: q.trim() } : {}), ...(archived ? { include_archived: "true" } : {}) });
      api<{ items: Any[] }>(`/vendors?${qs}`).then((r) => { setRows(r.items); setErr(null); }).catch((e) => setErr(errMsg(e)));
    }, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q, archived]);

  const needs = useMemo(() => (rows ?? []).filter((v) => !v.archived && !isVerifiedVendor(v)).length, [rows]);
  const noRole = "Requires accountant role";

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Vendors</h1>
          <p className="text-sm text-muted">{rows ? `${rows.length} vendor${rows.length === 1 ? "" : "s"}${needs ? ` · ${needs} need verification` : ""}` : "Loading…"}</p>
        </div>
        <div className="flex gap-2">
          <Link to={canEdit ? "/vendors/import" : "#"} aria-disabled={!canEdit} title={canEdit ? "" : noRole} onClick={(e) => !canEdit && e.preventDefault()} className={`btn ${canEdit ? "" : "pointer-events-auto cursor-not-allowed opacity-45"}`}>
            <FileUp size={15} aria-hidden />Import CSV
          </Link>
          <button className="btn btn-primary" disabled={!canEdit} title={canEdit ? "" : noRole} onClick={() => setAdding(true)}><Plus size={15} aria-hidden />Add vendor</button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <label className="relative min-w-0 flex-1 sm:max-w-sm">
          <span className="sr-only">Search vendors</span>
          <Search size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" aria-hidden />
          <input className="input !pl-9" placeholder="Search by name or GSTIN" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />Show archived
        </label>
      </div>

      {err && <div role="alert" className="rounded-lg bg-high-soft p-3 text-sm text-high">{err}</div>}

      <section className="card overflow-hidden" aria-label="Vendor list">
        {!rows ? (
          <div className="flex flex-col gap-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-2"><Building2 size={22} aria-hidden /></div>
            <div className="font-medium">{q ? "No vendors match" : "No vendors yet"}</div>
            <p className="max-w-sm text-sm text-muted">{q ? "Try another name or GSTIN." : "Invoices are checked against your vendor list. Add vendors one by one or import a CSV."}</p>
            {!q && canEdit && (
              <div className="flex gap-2">
                <Link to="/vendors/import" className="btn"><FileUp size={15} aria-hidden />Import CSV</Link>
                <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={15} aria-hidden />Add vendor</button>
              </div>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted">{["Vendor", "Status", "Bank accounts", "Open cases", "Last invoice"].map((h) => <th key={h} className="px-4 py-2 font-medium whitespace-nowrap">{h}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((v) => (
                  <tr key={v.id} className="cursor-pointer border-t border-line transition-colors duration-150 hover:bg-surface-2" onClick={() => nav(`/vendors/${v.id}`)}>
                    <td className="px-4 py-3">
                      <Link className="font-medium hover:underline" to={`/vendors/${v.id}`} onClick={(e) => e.stopPropagation()}>{v.name}</Link>
                      <div className="font-mono text-xs text-muted">{v.gstin ?? "No GSTIN"}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1.5">
                        {v.archived ? <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">Archived</span> : <VendorStatus v={v} />}
                        {v.previously_flagged && <span className="rounded-full bg-high-soft px-2 py-0.5 text-xs font-medium text-high" title="A past case for this vendor confirmed an issue">Past issue</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3 tabular-nums">{v.verified_bank_accounts ?? 0} verified</td>
                    <td className="px-4 py-3 tabular-nums">{v.open_cases ?? 0}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-muted">{v.last_invoice_date ? isoDate(v.last_invoice_date) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {adding && <VendorFormModal onClose={() => setAdding(false)} onSaved={(v) => nav(`/vendors/${v.id}`)} />}
    </div>
  );
}

// ---------------------------------------------------------------- create / edit

function Field({ label, hint, error, children, id }: { label: string; hint?: string; error?: string | null; children: ReactNode; id: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      {children}
      {error ? <p role="alert" className="text-xs text-high">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

/** Create a vendor (with optional first contact, domain and bank account) or edit its basic details. */
function VendorFormModal({ vendor, onClose, onSaved }: { vendor?: Any; onClose: () => void; onSaved: (v: Any) => void }) {
  const { user } = useAuth();
  const isApprover = can(user?.role as Role, "approver");
  const editing = !!vendor;
  const [f, setF] = useState({
    name: vendor?.name ?? "", gstin: vendor?.gstin ?? "", website: vendor?.website ?? "", address: vendor?.address ?? "", notes: vendor?.notes ?? "",
    email: "", phone: "", domain: "", account: "", ifsc: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF({ ...f, [k]: e.target.value }); setFieldErr({ ...fieldErr, [k]: "" }); };
  // Name, GSTIN and address identify the vendor every invoice is matched against, so only approvers change them.
  const identityLocked = editing && !isApprover;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const fe: Record<string, string> = {};
    if (!f.name.trim()) fe.name = "Enter the vendor's legal name.";
    if (f.gstin.trim() && !/^[0-9A-Z]{15}$/.test(f.gstin.trim().toUpperCase())) fe.gstin = "A GSTIN has 15 letters and digits.";
    if (f.email.trim() && !/^\S+@\S+\.\S+$/.test(f.email.trim())) fe.email = "Enter a valid email.";
    if (f.account.trim() && !/^\d{6,20}$/.test(f.account.replace(/\s/g, ""))) fe.account = "Account numbers are 6–20 digits.";
    setFieldErr(fe);
    if (Object.keys(fe).length) return;
    setBusy(true);
    setErr(null);
    try {
      const base = { name: f.name.trim(), website: f.website.trim() || undefined, address: f.address.trim() || undefined, notes: f.notes.trim() || undefined };
      const gstin = f.gstin.trim().toUpperCase() || undefined;
      let v: Any;
      if (editing) {
        // Send only what changed, so an accountant editing notes never touches the approver-only fields.
        const changes: Any = {};
        const was = (k: string) => (vendor![k] ?? "") as string;
        if (!identityLocked && base.name !== was("name")) changes.name = base.name;
        if (!identityLocked && (gstin ?? "") !== was("gstin")) changes.gstin = gstin;
        if (!identityLocked && (base.address ?? "") !== was("address")) changes.address = base.address ?? "";
        if ((base.website ?? "") !== was("website")) changes.website = base.website ?? "";
        if ((base.notes ?? "") !== was("notes")) changes.notes = base.notes ?? "";
        if (!Object.keys(changes).length) return onSaved(vendor!);
        v = await patch(`/vendors/${vendor!.id}`, changes);
      } else {
        v = await post("/vendors", { ...base, gstin });
        // First details are added unverified; an approver verifies them on the vendor page.
        const extra: Promise<unknown>[] = [];
        if (f.email.trim()) extra.push(post(`/vendors/${v.id}/contacts`, { email: f.email.trim(), phone: f.phone.trim() || undefined }));
        if (f.domain.trim()) extra.push(post(`/vendors/${v.id}/domains`, { domain: f.domain.trim().toLowerCase() }));
        if (f.account.trim()) extra.push(post(`/vendors/${v.id}/bank-accounts`, { account_number: f.account.replace(/\s/g, ""), ifsc: f.ifsc.trim().toUpperCase() || undefined }));
        const results = await Promise.allSettled(extra);
        const failed = results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
        if (failed.length) {
          setErr(`Vendor created, but ${failed.length} detail${failed.length > 1 ? "s" : ""} could not be added: ${failed.map((r) => errMsg(r.reason)).join("; ")}. Add ${failed.length > 1 ? "them" : "it"} from the vendor page.`);
          setBusy(false);
          setTimeout(() => onSaved(v), 2500);
          return;
        }
      }
      onSaved(v);
    } catch (e: any) {
      setErr(errMsg(e));
      setBusy(false);
    }
  };

  return (
    <Modal title={editing ? "Edit vendor" : "Add vendor"} onClose={onClose} wide>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="v-name" label="Legal name" error={fieldErr.name}>
            <input id="v-name" className="input" value={f.name} onChange={set("name")} disabled={identityLocked} title={identityLocked ? "Requires approver role" : ""} autoFocus={!identityLocked} />
          </Field>
          <Field id="v-gstin" label="GSTIN" hint={identityLocked ? undefined : "15 characters, checked against the GSTIN checksum."} error={fieldErr.gstin}>
            <input id="v-gstin" className="input font-mono uppercase" value={f.gstin} onChange={set("gstin")} disabled={identityLocked} title={identityLocked ? "Requires approver role" : ""} maxLength={15} />
          </Field>
          <Field id="v-web" label="Website"><input id="v-web" className="input" value={f.website} onChange={set("website")} placeholder="vendor.com" /></Field>
          <Field id="v-addr" label="Address"><input id="v-addr" className="input" value={f.address} onChange={set("address")} disabled={identityLocked} title={identityLocked ? "Requires approver role" : ""} /></Field>
        </div>
        {identityLocked && <p className="-mt-1 text-xs text-muted">Name, GSTIN and address can only be changed by an approver, because every invoice is matched against them.</p>}
        {!editing && (
          <fieldset className="rounded-xl border border-line p-4">
            <legend className="px-1 text-sm font-medium">First details <span className="font-normal text-muted">(optional, added as unverified)</span></legend>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="v-email" label="Contact email" error={fieldErr.email}><input id="v-email" type="email" className="input" value={f.email} onChange={set("email")} /></Field>
              <Field id="v-phone" label="Contact phone"><input id="v-phone" type="tel" className="input" value={f.phone} onChange={set("phone")} /></Field>
              <Field id="v-domain" label="Email domain"><input id="v-domain" className="input" value={f.domain} onChange={set("domain")} placeholder="vendor.com" /></Field>
              <div className="grid grid-cols-[1fr_auto] gap-2">
                <Field id="v-acct" label="Bank account no." hint="Only the last 4 digits are shown after saving." error={fieldErr.account}>
                  <input id="v-acct" className="input font-mono" inputMode="numeric" autoComplete="off" value={f.account} onChange={set("account")} />
                </Field>
                <Field id="v-ifsc" label="IFSC"><input id="v-ifsc" className="input w-32 font-mono uppercase" value={f.ifsc} onChange={set("ifsc")} maxLength={11} /></Field>
              </div>
            </div>
          </fieldset>
        )}
        {editing && <Field id="v-notes" label="Notes"><textarea id="v-notes" className="input h-20" value={f.notes} onChange={set("notes")} /></Field>}
        {err && <div role="alert" className="rounded-lg bg-high-soft p-3 text-sm text-high">{err}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy && <Spinner />}{editing ? "Save changes" : "Add vendor"}</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- detail

type Kind = "bank" | "domain" | "contact";
const KIND: Record<Kind, { path: string; title: string; icon: any; noun: string }> = {
  bank: { path: "bank-accounts", title: "Bank accounts", icon: CreditCard, noun: "bank account" },
  domain: { path: "domains", title: "Domains", icon: Globe, noun: "domain" },
  contact: { path: "contacts", title: "Contacts", icon: Mail, noun: "contact" },
};

export function VendorDetail() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const role = user?.role as Role | undefined;
  const canEdit = can(role, "accountant");
  const isApprover = can(role, "approver");
  const [v, setV] = useState<Any | null>(null);
  const [graph, setGraph] = useState<Any | null>(null);
  const [graphErr, setGraphErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [verify, setVerify] = useState<{ kind: Kind; item: Any } | null>(null);
  const [remove, setRemove] = useState<{ kind: Kind; item: Any } | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const load = () => api(`/vendors/${id}`).then((r) => { setV(r); setErr(null); }).catch((e) => setErr(errMsg(e)));
  const loadGraph = () => api(`/vendors/${id}/graph`).then((r) => { setGraph(r); setGraphErr(null); }).catch((e) => setGraphErr(errMsg(e)));
  useEffect(() => {
    load();
    loadGraph();
  }, [id]);

  if (err && !v) {
    return (
      <div className="mx-auto flex max-w-5xl flex-col gap-3">
        <Link to="/vendors" className="inline-flex w-fit items-center gap-1.5 rounded-md text-sm text-muted hover:text-ink"><ArrowLeft size={15} aria-hidden />Vendors</Link>
        <LoadError error={`Could not load this vendor. ${err}`} onRetry={load} />
      </div>
    );
  }
  if (!v) return <div className="mx-auto flex max-w-5xl flex-col gap-4"><Skeleton className="h-20" /><div className="grid gap-4 md:grid-cols-3"><Skeleton className="h-40" /><Skeleton className="h-40" /><Skeleton className="h-40" /></div></div>;

  const prices = (v.price_history ?? []).map((h: Any) => ({ date: isoDate(h.date), price: h.items?.[0]?.unit_price_minor ?? 0, n: h.invoice_number }));
  const max = Math.max(...prices.map((p: Any) => p.price), 1);
  const toggleArchive = async () => {
    setActionErr(null);
    try { await patch(`/vendors/${id}`, { archived: !v.archived }); load(); } catch (e: any) { setActionErr(errMsg(e)); }
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <Link to="/vendors" className="inline-flex w-fit items-center gap-1.5 rounded-md text-sm text-muted hover:text-ink"><ArrowLeft size={15} aria-hidden />Vendors</Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="page-title">{v.name}</h1>
            {v.archived ? <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">Archived</span> : <VendorStatus v={v} />}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted">
            {v.gstin && <span className="font-mono">{v.gstin}</span>}
            {v.website && <span>{v.website}</span>}
            {v.address && <span>{v.address}</span>}
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn" disabled={!canEdit} title={canEdit ? "" : "Requires accountant role"} onClick={() => setEditing(true)}><Pencil size={14} aria-hidden />Edit</button>
          <button className="btn" disabled={!canEdit} title={canEdit ? "" : "Requires accountant role"} onClick={toggleArchive}>
            {v.archived ? <><ArchiveRestore size={14} aria-hidden />Restore</> : <><Archive size={14} aria-hidden />Archive</>}
          </button>
        </div>
      </header>

      {actionErr && <div role="alert" className="rounded-lg bg-high-soft p-3 text-sm text-high">{actionErr}</div>}

      <GstSection vendor={v} canEdit={canEdit} onChanged={load} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {(["bank", "domain", "contact"] as Kind[]).map((k) => (
          <ItemSection
            key={k}
            kind={k}
            vendorId={id}
            items={k === "bank" ? v.accounts : k === "domain" ? v.domains : v.contacts}
            canAdd={canEdit}
            isApprover={isApprover}
            onVerify={(item) => setVerify({ kind: k, item })}
            onRemove={(item) => setRemove({ kind: k, item })}
            onChanged={load}
          />
        ))}
      </div>

      {graphErr && !graph && <LoadError error={`Could not load this vendor's connections. ${graphErr}`} onRetry={loadGraph} />}
      {graph && (
        <section className="card p-5" aria-labelledby="graph-title">
          <h2 id="graph-title" className="mb-3 text-base font-semibold">Connections</h2>
          <RelGraph nodes={graph.nodes} edges={graph.edges} />
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="card p-5" aria-labelledby="price-title">
          <h2 id="price-title" className="mb-4 text-base font-semibold">Unit price history</h2>
          {v.invoices_pending > 0 && (
            <p className="-mt-2 mb-3 text-xs text-medium">
              {v.invoices_pending} past invoice{v.invoices_pending === 1 ? " is" : "s are"} waiting for approval and not yet used in price checks.{" "}
              {isApprover && <Link to="/baseline" className="font-medium underline underline-offset-2">Review</Link>}
            </p>
          )}
          {prices.length === 0 ? <p className="text-sm text-muted">No invoice history yet.</p> : (
            <>
              <div className="flex h-36 items-end gap-1.5" role="img" aria-label={`Unit price history, ${prices.length} invoices, latest ${inr(prices.at(-1)?.price)}`}>
                {prices.map((p: Any) => (
                  <div key={p.n} className="group relative flex h-full flex-1 flex-col items-center justify-end" title={`${p.date} · ${p.n} · ${inr(p.price)}`}>
                    <div className="w-full max-w-8 rounded-t bg-accent opacity-70 transition-opacity group-hover:opacity-100" style={{ height: `${(p.price / max) * 100}%` }} />
                  </div>
                ))}
              </div>
              <div className="mt-1 flex justify-between text-[11px] text-muted"><span>{prices[0]?.date}</span><span>{prices.at(-1)?.date}</span></div>
            </>
          )}
        </section>
        <section className="card p-5" aria-labelledby="cases-title">
          <h2 id="cases-title" className="mb-3 flex items-center gap-2 text-base font-semibold"><History size={16} aria-hidden />Past cases</h2>
          {(v.prior_cases ?? []).length === 0 ? <p className="text-sm text-muted">No closed cases for this vendor.</p> : (
            <ul className="flex flex-col divide-y divide-line">
              {v.prior_cases.map((p: Any) => (
                <li key={p.case_id} className="py-2.5 first:pt-0">
                  <Link to={`/cases/${p.case_id}`} className="flex items-center justify-between gap-3 hover:underline">
                    <span className="text-sm font-medium">{p.outcome?.replace(/_/g, " ").toLowerCase()}</span>
                    <TierChip tier={p.peak_tier} score={p.peak_score} />
                  </Link>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted">{p.summary}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {editing && <VendorFormModal vendor={v} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load(); }} />}
      {verify && <VerifyModal kind={verify.kind} item={verify.item} vendorId={id} onClose={() => setVerify(null)} onDone={() => { setVerify(null); load(); }} />}
      {remove && <RemoveModal kind={remove.kind} item={remove.item} vendorId={id} onClose={() => setRemove(null)} onDone={() => { setRemove(null); load(); }} />}
    </div>
  );
}

const itemLabel = (k: Kind, it: Any) => (k === "bank" ? `${it.account}${it.ifsc ? ` · ${it.ifsc}` : ""}` : k === "domain" ? it.domain : it.name ? `${it.name}` : it.email);

function VerifiedBy({ item }: { item: Any }) {
  const ver: Verification = item.verification ?? {};
  const method = ver.method ?? item.verified_method;
  const who = ver.by?.name;
  const when = ver.at;
  if (!item.verified) return <span className="text-xs text-muted">Not verified</span>;
  return (
    <span className="text-xs text-muted" title={ver.note ?? undefined}>
      {who ? `Verified by ${who}` : method === "onboarding_kyc" ? "Verified at onboarding" : "Verified"}
      {when ? ` · ${isoDate(when)}` : ""}
      {!who && method && method !== "onboarding_kyc" ? ` · ${method.replace(/_/g, " ")}` : ""}
      {ver.note && <span className="mt-0.5 block italic">“{ver.note}”</span>}
    </span>
  );
}

function ItemSection({ kind, vendorId, items, canAdd, isApprover, onVerify, onRemove, onChanged }: {
  kind: Kind; vendorId: string; items: Any[]; canAdd: boolean; isApprover: boolean;
  onVerify: (it: Any) => void; onRemove: (it: Any) => void; onChanged: () => void;
}) {
  const meta = KIND[kind];
  const [adding, setAdding] = useState(false);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const reset = () => { setAdding(false); setA(""); setB(""); setErr(null); };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const v1 = a.trim();
    if (!v1) return setErr(kind === "bank" ? "Enter the account number." : kind === "domain" ? "Enter a domain." : "Enter an email.");
    if (kind === "bank" && !/^\d{6,20}$/.test(v1.replace(/\s/g, ""))) return setErr("Account numbers are 6–20 digits.");
    if (kind === "contact" && !/^\S+@\S+\.\S+$/.test(v1)) return setErr("Enter a valid email.");
    setBusy(true);
    setErr(null);
    try {
      const body = kind === "bank" ? { account_number: v1.replace(/\s/g, ""), ifsc: b.trim().toUpperCase() || undefined }
        : kind === "domain" ? { domain: v1.toLowerCase() }
        : { email: v1, phone: b.trim() || undefined };
      await post(`/vendors/${vendorId}/${meta.path}`, body);
      reset();
      onChanged();
    } catch (e: any) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card flex flex-col p-4" aria-labelledby={`sec-${kind}`}>
      <div className="mb-3 flex items-center justify-between">
        <h2 id={`sec-${kind}`} className="flex items-center gap-2 text-sm font-semibold"><meta.icon size={15} aria-hidden />{meta.title}</h2>
        {!adding && (
          <button className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-40" disabled={!canAdd} title={canAdd ? `Add ${meta.noun}` : "Requires accountant role"} aria-label={`Add ${meta.noun}`} onClick={() => setAdding(true)}>
            <Plus size={16} />
          </button>
        )}
      </div>
      {items.length === 0 && !adding && <p className="text-sm text-muted">None yet.</p>}
      <ul className="flex flex-col gap-3">
        {items.map((it) => (
          <li key={it.id} className="group flex items-start gap-2.5">
            {it.verified ? <ShieldCheck size={16} className="mt-0.5 shrink-0 text-low" aria-label="Verified" /> : <CircleDashed size={16} className="mt-0.5 shrink-0 text-medium" aria-label="Not verified" />}
            <div className="min-w-0 flex-1">
              <div className={`text-sm font-medium break-all ${kind === "bank" ? "font-mono" : ""}`}>{itemLabel(kind, it)}</div>
              {kind === "contact" && (it.name || it.phone) && <div className="text-xs break-all text-muted">{[it.name ? it.email : null, it.phone].filter(Boolean).join(" · ")}</div>}
              {kind === "bank" && it.last_seen && <div className="text-xs text-muted">Last seen {relTime(it.last_seen)}</div>}
              <VerifiedBy item={it} />
              <div className="mt-1.5 flex gap-3">
                {!it.verified && (
                  <button className="text-xs font-medium hover:underline disabled:cursor-not-allowed disabled:opacity-45 disabled:no-underline" disabled={!isApprover} title={isApprover ? "" : "Requires approver role"} onClick={() => onVerify(it)}>
                    Verify
                  </button>
                )}
                <button className="text-xs text-muted hover:text-high hover:underline disabled:cursor-not-allowed disabled:opacity-45 disabled:no-underline" disabled={!isApprover} title={isApprover ? "" : "Requires approver role"} onClick={() => onRemove(it)}>
                  Remove
                </button>
              </div>
            </div>
          </li>
        ))}
      </ul>
      {adding && (
        <form onSubmit={add} className="mt-3 flex flex-col gap-2 rounded-lg border border-line p-3" noValidate>
          <label className="sr-only" htmlFor={`add-${kind}`}>{kind === "bank" ? "Account number" : kind === "domain" ? "Domain" : "Email"}</label>
          <input
            id={`add-${kind}`}
            className={`input ${kind === "bank" ? "font-mono" : ""}`}
            placeholder={kind === "bank" ? "Account number" : kind === "domain" ? "vendor.com" : "accounts@vendor.com"}
            inputMode={kind === "bank" ? "numeric" : kind === "contact" ? "email" : undefined}
            autoComplete="off"
            value={a}
            onChange={(e) => { setA(e.target.value); setErr(null); }}
            autoFocus
          />
          {kind !== "domain" && (
            <input className={`input ${kind === "bank" ? "font-mono uppercase" : ""}`} placeholder={kind === "bank" ? "IFSC (optional)" : "Phone (optional)"} aria-label={kind === "bank" ? "IFSC" : "Phone"} value={b} onChange={(e) => setB(e.target.value)} />
          )}
          {err && <p role="alert" className="text-xs text-high">{err}</p>}
          <p className="text-[11px] text-muted">Added as unverified.{kind === "bank" ? " Only the last 4 digits are kept on screen." : ""}</p>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn !py-1 text-xs" onClick={reset}>Cancel</button>
            <button type="submit" className="btn btn-primary !py-1 text-xs" disabled={busy}>{busy && <Spinner size={12} />}Add</button>
          </div>
        </form>
      )}
    </section>
  );
}

function VerifyModal({ kind, item, vendorId, onClose, onDone }: { kind: Kind; item: Any; vendorId: string; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const short = note.trim().length < VERIFY_NOTE_MIN;
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await patch(`/vendors/${vendorId}/${KIND[kind].path}/${item.id}`, { verified: true, verification_note: note.trim() });
      onDone();
    } catch (e: any) {
      setErr(errMsg(e));
      setBusy(false);
    }
  };
  return (
    <Modal title={`Verify ${KIND[kind].noun}`} onClose={onClose}>
      <div className="mb-3 rounded-lg bg-surface-2 px-3 py-2 text-sm"><span className="font-mono">{itemLabel(kind, item)}</span></div>
      <p className="mb-3 text-sm text-muted">Verified details become the baseline every invoice is checked against. Confirm through a channel you already trust, not one from an invoice or email.</p>
      <label htmlFor="ver-note" className="text-sm font-medium">How did you confirm it?</label>
      <textarea id="ver-note" className="input mt-1 h-24 placeholder:text-muted/60" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Called Meera in accounts on the number from our onboarding file" aria-describedby="ver-hint" />
      <p id="ver-hint" className="mt-1 text-xs text-muted">Saved with your name in the audit log.</p>
      {err && <div role="alert" className="mt-2 rounded-lg bg-high-soft p-2.5 text-sm text-high">{err}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || short} title={short ? "Describe how you confirmed it" : ""} onClick={submit}>{busy ? <Spinner /> : <ShieldCheck size={15} aria-hidden />}Mark verified</button>
      </div>
    </Modal>
  );
}

function RemoveModal({ kind, item, vendorId, onClose, onDone }: { kind: Kind; item: Any; vendorId: string; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await del(`/vendors/${vendorId}/${KIND[kind].path}/${item.id}`);
      onDone();
    } catch (e: any) {
      setErr(errMsg(e));
      setBusy(false);
    }
  };
  return (
    <Modal title={`Remove ${KIND[kind].noun}?`} onClose={onClose}>
      <p className="text-sm text-muted">
        <span className="font-mono text-ink">{itemLabel(kind, item)}</span> will no longer be used when checking invoices. The removal is recorded in the audit log.
      </p>
      {err && <div role="alert" className="mt-3 rounded-lg bg-high-soft p-2.5 text-sm text-high">{err}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" disabled={busy} onClick={submit}>{busy ? <Spinner /> : <Trash2 size={14} aria-hidden />}Remove</button>
      </div>
    </Modal>
  );
}



// ---------------------------------------------------------------- GST registration

const GST_STATUSES = ["Active", "Cancelled", "Suspended"] as const;

/** No GST registry provider is connected, so the registry status is always "could not verify". A person may record what they
 *  saw on the GST portal; that entry is labelled as manual and is never shown as verified. */
function GstSection({ vendor, canEdit, onChanged }: { vendor: Any; canEdit: boolean; onChanged: () => void }) {
  const gst = vendor.gst ?? {};
  const m = vendor.gst_manual as Any | null;
  const [editing, setEditing] = useState(false);
  const [legalName, setLegalName] = useState("");
  const [status, setStatus] = useState<string>("Active");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const noGstin = !vendor.gstin;
  const blockedWhy = !canEdit ? "Requires accountant role" : noGstin ? "Add the vendor's GSTIN first" : "";

  const open = () => {
    setLegalName(m?.legal_name ?? "");
    setStatus(m?.status ?? "Active");
    setNote(m?.note ?? "");
    setErr(null);
    setEditing(true);
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api(`/vendors/${vendor.id}/gst-manual`, { method: "PUT", body: JSON.stringify({ legal_name: legalName.trim() || undefined, status, note: note.trim() || undefined }) });
      setEditing(false);
      onChanged();
    } catch (e: any) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    setBusy(true);
    setErr(null);
    try {
      await del(`/vendors/${vendor.id}/gst-manual`);
      onChanged();
    } catch (e: any) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-5" aria-labelledby="gst-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="gst-title" className="flex items-center gap-2 text-base font-semibold"><Landmark size={16} aria-hidden />GST registration</h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="font-mono">{vendor.gstin ?? "No GSTIN"}</span>
            {gst.gstin_format === "valid" && <span className="text-xs text-muted">Format and checksum OK</span>}
            {gst.gstin_format === "invalid" && <span className="text-xs font-medium text-high">Format or checksum is invalid</span>}
          </div>
          <div className="mt-1.5 text-sm">
            <span className="text-muted">Registry status: </span><CouldNotVerify reason={gst.registry_reason} />
          </div>
        </div>
        {!editing && (
          <button className="btn !py-1.5 text-sm" disabled={!!blockedWhy} title={blockedWhy} onClick={open}>
            <Pencil size={14} aria-hidden />{m ? "Update manual entry" : "Enter manually"}
          </button>
        )}
      </div>

      {m && !editing && (
        <div className="mt-4 rounded-xl border border-dashed border-line bg-surface-2 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm">
              <span className="font-medium">{m.legal_name ?? "No legal name entered"}</span>
              <span className="text-muted"> · {m.status}</span>
            </div>
            <button className="text-xs text-muted hover:text-high hover:underline disabled:cursor-not-allowed disabled:opacity-45" disabled={!canEdit || busy} title={canEdit ? "" : "Requires accountant role"} onClick={clear}>Remove</button>
          </div>
          <div className="mt-1 text-xs text-muted">{m.label}</div>
          {m.note && <div className="mt-1 text-xs text-muted italic">“{m.note}”</div>}
          {m.stale && <div className="mt-1.5 text-xs font-medium text-medium">Entered for a different GSTIN ({m.gstin}). Update it for the current GSTIN.</div>}
        </div>
      )}

      {editing && (
        <form onSubmit={save} className="mt-4 flex flex-col gap-3 rounded-xl border border-line p-4" noValidate>
          <p className="text-xs text-muted">Record what you saw on the official GST portal. It will be shown as a manual entry with your name, not as verified.</p>
          <div className="grid gap-3 sm:grid-cols-[1fr_180px]">
            <div className="flex flex-col gap-1">
              <label htmlFor="gst-name" className="text-sm font-medium">Legal name</label>
              <input id="gst-name" className="input" value={legalName} onChange={(e) => setLegalName(e.target.value)} autoFocus />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="gst-status" className="text-sm font-medium">Status</label>
              <select id="gst-status" className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
                {GST_STATUSES.map((x) => <option key={x}>{x}</option>)}
              </select>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="gst-note" className="text-sm font-medium">Note <span className="font-normal text-muted">(optional)</span></label>
            <input id="gst-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Where and when you checked" />
          </div>
          {err && <p role="alert" className="text-sm text-high">{err}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy && <Spinner />}Save manual entry</button>
          </div>
        </form>
      )}
      {err && !editing && <p role="alert" className="mt-2 text-sm text-high">{err}</p>}
    </section>
  );
}
