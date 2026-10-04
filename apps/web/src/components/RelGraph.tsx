import { Building2, CreditCard, Globe, Landmark, MapPin } from "lucide-react";

interface Node { id: string; type: string; label: string; root?: boolean; shared?: boolean }
interface Edge { source: string; target: string; rel: string }

const ICON: Record<string, any> = { vendor: Building2, bank: CreditCard, domain: Globe, gstin: Landmark, address: MapPin };

/** Radial relationship graph: vendor at the centre, its bank/domain/GSTIN/address around it, and any other
 *  vendor sharing one of those attributes on the outer ring (highlighted red). */
export function RelGraph({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) {
  const root = nodes.find((n) => n.root);
  if (!root) return <div className="text-sm text-muted">No relationship data yet.</div>;
  const attrs = nodes.filter((n) => n.type !== "vendor");
  const others = nodes.filter((n) => n.type === "vendor" && !n.root);
  const W = 640, H = 420, cx = W / 2, cy = H / 2;
  const pos: Record<string, { x: number; y: number }> = { [root.id]: { x: cx, y: cy } };
  attrs.forEach((a, i) => {
    const t = (i / Math.max(attrs.length, 1)) * Math.PI * 2 - Math.PI / 2;
    pos[a.id] = { x: cx + Math.cos(t) * 135, y: cy + Math.sin(t) * 120 };
  });
  others.forEach((o, i) => {
    const link = edges.find((e) => e.source === o.id);
    const anchor = link ? pos[link.target] : { x: cx, y: cy };
    const t = Math.atan2(anchor.y - cy, anchor.x - cx) + (i % 2 ? 0.25 : -0.25);
    pos[o.id] = { x: cx + Math.cos(t) * 255, y: cy + Math.sin(t) * 185 };
  });
  const sharedCount = attrs.filter((a) => a.shared).length;
  return (
    <div>
      <div className="mb-2 text-sm">
        {sharedCount ? <span className="font-semibold text-high">{sharedCount} attribute{sharedCount > 1 ? "s" : ""} shared with another vendor</span> : <span className="text-low">No bank account, domain or address is shared with another vendor.</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-3xl" role="img" aria-label="Vendor relationship graph">
        {edges.map((e, i) => {
          const a = pos[e.source], b = pos[e.target];
          if (!a || !b) return null;
          const shared = nodes.find((n) => n.id === e.target)?.shared && nodes.find((n) => n.id === e.source)?.type === "vendor" && !nodes.find((n) => n.id === e.source)?.root;
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={shared ? "var(--high)" : "var(--border)"} strokeWidth={shared ? 2 : 1.5} strokeDasharray={shared ? "5 4" : undefined} />;
        })}
        {nodes.map((n) => {
          const p = pos[n.id];
          if (!p) return null;
          const Icon = ICON[n.type] ?? Globe;
          const color = n.root ? "var(--accent)" : n.shared ? "var(--high)" : "var(--muted)";
          const r = n.root ? 26 : 20;
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`}>
              <circle r={r} fill="var(--surface)" stroke={color} strokeWidth={n.root || n.shared ? 2.5 : 1.5} />
              <foreignObject x={-9} y={-9} width={18} height={18}><Icon size={18} color={color} /></foreignObject>
              <text y={r + 14} textAnchor="middle" fontSize="11" fill="var(--text)" fontWeight={n.root ? 700 : 500}>{n.label.length > 30 ? n.label.slice(0, 29) + "…" : n.label}</text>
              {!n.root && <text y={r + 26} textAnchor="middle" fontSize="9" fill="var(--muted)">{n.type}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
