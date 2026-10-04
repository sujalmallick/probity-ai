import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, Building2, CreditCard, Globe, Landmark, MapPin, Maximize2, Minus, Plus, X } from "lucide-react";

interface Node { id: string; type: string; label: string; root?: boolean; shared?: boolean }
interface Edge { source: string; target: string; rel: string }
type Pt = { x: number; y: number };

const ICON: Record<string, any> = { vendor: Building2, bank: CreditCard, domain: Globe, gstin: Landmark, address: MapPin };
const TYPE_LABEL: Record<string, string> = { vendor: "Vendor", bank: "Bank account", domain: "Domain", gstin: "GSTIN", address: "Address" };
const W = 720, H = 440;

function layout(nodes: Node[], edges: Edge[]): Record<string, Pt> {
  const cx = W / 2, cy = H / 2;
  const root = nodes.find((n) => n.root);
  const pos: Record<string, Pt> = root ? { [root.id]: { x: cx, y: cy } } : {};
  const attrs = nodes.filter((n) => n.type !== "vendor");
  attrs.forEach((a, i) => {
    const t = (i / Math.max(attrs.length, 1)) * Math.PI * 2 - Math.PI / 2;
    pos[a.id] = { x: cx + Math.cos(t) * 150, y: cy + Math.sin(t) * 125 };
  });
  nodes.filter((n) => n.type === "vendor" && !n.root).forEach((o, i) => {
    const link = edges.find((e) => e.source === o.id || e.target === o.id);
    const anchorId = link ? (link.source === o.id ? link.target : link.source) : null;
    const anchor = (anchorId && pos[anchorId]) || { x: cx, y: cy };
    const t = Math.atan2(anchor.y - cy, anchor.x - cx) + (i % 2 ? 0.28 : -0.28);
    pos[o.id] = { x: cx + Math.cos(t) * 280, y: cy + Math.sin(t) * 190 };
  });
  return pos;
}

/** Interactive relationship graph: the vendor in the middle, its bank account, domain, GSTIN and address around it,
 *  and any other vendor sharing one of those on the outer ring (marked "shared"). Drag nodes, pan the background,
 *  zoom with the buttons (or Ctrl + scroll), hover to trace connections, click or press Enter for details. */
export function RelGraph({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) {
  const root = nodes.find((n) => n.root);
  const svg = useRef<SVGSVGElement>(null);
  const [pos, setPos] = useState(() => layout(nodes, edges));
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const drag = useRef<{ kind: "node" | "pan"; id?: string; start: Pt; origin: Pt; moved: boolean } | null>(null);

  useEffect(() => { setPos(layout(nodes, edges)); setSelected(null); }, [nodes, edges]);

  const types = useMemo(() => [...new Set(nodes.map((n) => n.type))], [nodes]);
  const visible = useMemo(() => nodes.filter((n) => n.root || !hidden.has(n.type)), [nodes, hidden]);
  const visibleIds = useMemo(() => new Set(visible.map((n) => n.id)), [visible]);
  const shownEdges = edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));
  const focus = hover ?? selected;
  const neighbours = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    edges.forEach((e) => { if (e.source === focus) s.add(e.target); if (e.target === focus) s.add(e.source); });
    return s;
  }, [focus, edges]);
  const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);

  // Ctrl/Cmd + wheel zooms; plain wheel keeps scrolling the page.
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  if (!root) return <div className="text-sm text-muted">No relationship data yet.</div>;

  const toSvg = (e: { clientX: number; clientY: number }): Pt => {
    const m = svg.current!.getScreenCTM()!.inverse();
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m);
    return { x: p.x, y: p.y };
  };
  const zoomBy = (f: number) => setView((v) => {
    const k = Math.min(2.5, Math.max(0.5, v.k * f));
    // zoom around the centre of the canvas
    return { k, x: W / 2 - ((W / 2 - v.x) * k) / v.k, y: H / 2 - ((H / 2 - v.y) * k) / v.k };
  });
  const reset = () => { setView({ x: 0, y: 0, k: 1 }); setPos(layout(nodes, edges)); };

  const onDown = (e: RPointerEvent, id?: string) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const start = toSvg(e);
    drag.current = id
      ? { kind: "node", id, start, origin: pos[id], moved: false }
      : { kind: "pan", start, origin: { x: view.x, y: view.y }, moved: false };
  };
  const onMove = (e: RPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = toSvg(e);
    const dx = p.x - d.start.x, dy = p.y - d.start.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (d.kind === "node" && d.id) setPos((ps) => ({ ...ps, [d.id!]: { x: d.origin.x + dx / view.k, y: d.origin.y + dy / view.k } }));
    else setView((v) => ({ ...v, x: d.origin.x + dx, y: d.origin.y + dy }));
  };
  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) setSelected(d.kind === "node" ? (selected === d.id ? null : d.id!) : null);
  };

  const sharedCount = nodes.filter((a) => a.type !== "vendor" && a.shared).length;
  const sel = selected ? byId[selected] : null;
  const selLinks = sel ? edges.filter((e) => e.source === sel.id || e.target === sel.id).map((e) => byId[e.source === sel.id ? e.target : e.source]).filter(Boolean) : [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          {sharedCount
            ? <span className="font-medium text-high">{sharedCount} {sharedCount > 1 ? "details are" : "detail is"} shared with another vendor</span>
            : <span className="text-muted">Nothing is shared with another vendor.</span>}
        </div>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Show node types">
          {types.filter((t) => t !== "vendor" || nodes.some((n) => n.type === "vendor" && !n.root)).map((t) => {
            const Icon = ICON[t] ?? Globe;
            const on = !hidden.has(t);
            return (
              <button
                key={t}
                aria-pressed={on}
                className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors duration-150 ${on ? "border-line bg-surface-2 text-ink" : "border-dashed border-line text-muted line-through"}`}
                onClick={() => setHidden((h) => { const n = new Set(h); on ? n.add(t) : n.delete(t); return n; })}
              >
                <Icon size={12} aria-hidden />{TYPE_LABEL[t] ?? t}
              </button>
            );
          })}
        </div>
      </div>

      <div className="relative overflow-hidden rounded-xl border border-line bg-surface-2/40">
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          className="block h-auto max-h-[460px] w-full touch-none select-none"
          style={{ cursor: drag.current?.kind === "pan" ? "grabbing" : "grab" }}
          role="group"
          aria-label="Vendor relationship graph. Use Tab to move between nodes and Enter to see details."
          onPointerDown={(e) => onDown(e)}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerLeave={() => { if (drag.current) onUp(); }}
        >
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            {shownEdges.map((e, i) => {
              const a = pos[e.source], b = pos[e.target];
              if (!a || !b) return null;
              const s = byId[e.source], t = byId[e.target];
              const risky = (t?.shared || s?.shared) && (s?.type === "vendor" && !s.root || t?.type === "vendor" && !t.root);
              const lit = neighbours ? neighbours.has(e.source) && neighbours.has(e.target) : true;
              const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
              return (
                <g key={i} opacity={lit ? 1 : 0.15} className="transition-opacity duration-150">
                  <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={risky ? "var(--high)" : lit && neighbours ? "var(--text)" : "var(--border)"} strokeWidth={risky ? 2 : 1.5} strokeDasharray={risky ? "5 4" : undefined} />
                  {neighbours && lit && <text x={mx} y={my - 4} textAnchor="middle" fontSize="10" fill="var(--muted)">{e.rel.replace(/_/g, " ")}</text>}
                </g>
              );
            })}
            {visible.map((n) => {
              const p = pos[n.id];
              if (!p) return null;
              const Icon = ICON[n.type] ?? Globe;
              const color = n.root ? "var(--accent)" : n.shared ? "var(--high)" : "var(--muted)";
              const r = n.root ? 28 : 21;
              const lit = neighbours ? neighbours.has(n.id) : true;
              const isSel = selected === n.id;
              return (
                <g
                  key={n.id}
                  transform={`translate(${p.x},${p.y})`}
                  opacity={lit ? 1 : 0.25}
                  className="cursor-pointer outline-none transition-opacity duration-150 [&:focus-visible>circle.ring]:opacity-100"
                  tabIndex={0}
                  role="button"
                  aria-label={`${TYPE_LABEL[n.type] ?? n.type}: ${n.label}${n.shared ? ", shared with another vendor" : ""}`}
                  aria-pressed={isSel}
                  onPointerDown={(e) => onDown(e, n.id)}
                  onPointerEnter={() => setHover(n.id)}
                  onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(n.id)}
                  onBlur={() => setHover(null)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(isSel ? null : n.id); } }}
                >
                  <circle className="ring" r={r + 6} fill="none" stroke="var(--accent)" strokeWidth={2} opacity={isSel ? 1 : 0} />
                  <circle r={r} fill="var(--surface)" stroke={color} strokeWidth={n.root || n.shared ? 2.5 : 1.5} />
                  <foreignObject x={-9} y={-9} width={18} height={18} pointerEvents="none"><Icon size={18} color={color} /></foreignObject>
                  <text y={r + 15} textAnchor="middle" fontSize="11.5" fill="var(--text)" fontWeight={n.root ? 700 : 500} pointerEvents="none">
                    {n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label}
                  </text>
                  {n.shared && !n.root && <text y={r + 28} textAnchor="middle" fontSize="9.5" fill="var(--high)" fontWeight={600} pointerEvents="none">shared</text>}
                </g>
              );
            })}
          </g>
        </svg>

        <div className="absolute right-2 bottom-2 flex flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm">
          <button className="p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Zoom in" title="Zoom in" onClick={() => zoomBy(1.2)}><Plus size={15} /></button>
          <button className="border-t border-line p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Zoom out" title="Zoom out" onClick={() => zoomBy(1 / 1.2)}><Minus size={15} /></button>
          <button className="border-t border-line p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Reset view" title="Reset view" onClick={reset}><Maximize2 size={14} /></button>
        </div>
        <div className="pointer-events-none absolute bottom-2 left-3 hidden text-[11px] text-muted sm:block">Drag to move · Ctrl + scroll to zoom</div>

        {sel && (
          <div className="fade-in absolute top-2 right-2 w-64 rounded-xl border border-line bg-surface p-3 text-sm shadow-xl" role="region" aria-label="Node details">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs text-muted">{TYPE_LABEL[sel.type] ?? sel.type}</div>
                <div className="font-medium break-words">{sel.label}</div>
              </div>
              <button className="rounded p-0.5 text-muted hover:bg-surface-2" aria-label="Close details" onClick={() => setSelected(null)}><X size={15} /></button>
            </div>
            {sel.shared && <div className="mt-2 rounded-md bg-high-soft px-2 py-1 text-xs text-high">Also used by another vendor</div>}
            {selLinks.length > 0 && (
              <div className="mt-2">
                <div className="mb-1 text-xs text-muted">Connected to</div>
                <ul className="flex flex-col gap-1">
                  {selLinks.map((n) => <li key={n.id} className="truncate text-xs"><span className="text-muted">{TYPE_LABEL[n.type] ?? n.type}:</span> {n.label}</li>)}
                </ul>
              </div>
            )}
            {sel.type === "vendor" && !sel.root && (
              <Link to={`/vendors/${sel.id.replace(/^vendor:/, "")}`} className="mt-3 inline-flex items-center gap-1 text-xs font-medium hover:underline">Open vendor<ArrowUpRight size={12} aria-hidden /></Link>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
