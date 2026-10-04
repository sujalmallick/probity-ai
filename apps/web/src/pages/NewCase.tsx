import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Copy, FileUp, Play } from "lucide-react";
import { post, uploadFile } from "../lib/api";
import { Spinner } from "../components/ui";

export default function NewCase() {
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [doc, setDoc] = useState<{ document_id: string; sha256: string; duplicate_of: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  const pick = async (f: File) => {
    setFile(f);
    setErr(null);
    setDoc(null);
    setBusy(true);
    try {
      setDoc(await uploadFile(f, f.name));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    if (!doc) return;
    setBusy(true);
    try {
      const c = await post<{ case_id: string }>("/cases", { document_id: doc.document_id });
      nav(`/cases/${c.case_id}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold">New investigation</h1>
        <p className="text-sm text-muted">PDF, PNG/JPG or EML, up to 15 MB. Files are checked by content, not extension.</p>
      </div>
      <div
        className={`card flex cursor-pointer flex-col items-center justify-center gap-2 border-2 border-dashed px-6 py-14 text-center ${drag ? "!border-accent bg-accent-soft" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) pick(f); }}
        role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <FileUp size={28} className="text-accent" />
        <div className="font-semibold">{file ? file.name : "Drop an invoice here, or click to choose"}</div>
        <div className="text-xs text-muted">SHA-256 is computed on upload; the same file is never investigated twice.</div>
        <input ref={input} type="file" accept=".pdf,.png,.jpg,.jpeg,.eml,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
      </div>
      {busy && <div className="flex items-center gap-2 text-sm text-muted"><Spinner />Working…</div>}
      {err && <div className="rounded-lg bg-high-soft p-3 text-sm text-high">{err}</div>}
      {doc && (
        <div className="card fade-in flex flex-col gap-3 p-4">
          <div className="text-xs text-muted">sha256 <code>{doc.sha256.slice(0, 16)}…</code></div>
          {doc.duplicate_of ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-medium-soft p-3 text-sm text-medium">
              <span className="flex items-center gap-2"><Copy size={15} />This exact file was already uploaded.</span>
              {doc.duplicate_of.startsWith("case_") && <button className="btn" onClick={() => nav(`/cases/${doc.duplicate_of}`)}>Open existing case</button>}
            </div>
          ) : (
            <button className="btn btn-primary self-start" onClick={start} disabled={busy}><Play size={15} />Start investigation</button>
          )}
        </div>
      )}
    </div>
  );
}
