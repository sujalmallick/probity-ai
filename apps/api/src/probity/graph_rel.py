"""Relationship graph (Feature F12): vendor → attribute edges in Postgres, shared-attribute detection.

Attributes are stored as keys that never reveal raw values: bank accounts by HMAC (label shows last 4),
domains and GSTINs as-is, addresses as a normalized hash. Two *different* vendors sharing a bank account,
sender domain or address is a classic sign of a shell/duplicate supplier.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import GraphEdge, Vendor, VendorBankAccount, VendorDomain

REL_LABEL = {"has_bank": "bank account", "uses_domain": "domain", "has_gstin": "GSTIN", "has_address": "address"}


def _addr_key(addr: str) -> str:
    norm = re.sub(r"[^a-z0-9]", "", addr.lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


def add_edge(s: Session, workspace_id: str, vendor_id: str, rel: str, dst_type: str, dst_id: str, label: str, case_id: str | None = None) -> None:
    exists = s.scalars(select(GraphEdge).where(
        GraphEdge.workspace_id == workspace_id, GraphEdge.src_type == "vendor", GraphEdge.src_id == vendor_id,
        GraphEdge.rel == rel, GraphEdge.dst_type == dst_type, GraphEdge.dst_id == dst_id)).first()
    if not exists:
        s.add(GraphEdge(workspace_id=workspace_id, src_type="vendor", src_id=vendor_id, rel=rel, dst_type=dst_type, dst_id=dst_id, label=label, case_id=case_id))
        s.flush()


def index_vendor_master(s: Session, workspace_id: str) -> None:
    for v in s.scalars(select(Vendor).where(Vendor.workspace_id == workspace_id)):
        if v.gstin:
            add_edge(s, workspace_id, v.id, "has_gstin", "gstin", v.gstin, v.gstin)
        if v.address:
            add_edge(s, workspace_id, v.id, "has_address", "address", _addr_key(v.address), v.address[:120])
    for a in s.scalars(select(VendorBankAccount).where(VendorBankAccount.workspace_id == workspace_id)):
        add_edge(s, workspace_id, a.vendor_id, "has_bank", "bank", a.acct_hmac, f"XXXX{a.last4}")
    for d in s.scalars(select(VendorDomain).where(VendorDomain.workspace_id == workspace_id)):
        add_edge(s, workspace_id, d.vendor_id, "uses_domain", "domain", d.domain, d.domain)


def index_invoice(s: Session, workspace_id: str, vendor_id: str, case_id: str, extraction: dict) -> list[tuple[str, str, str, str]]:
    """Record the invoice's attributes against its vendor; return [(rel, dst_type, dst_id, label)] added."""
    out = []
    bank = extraction.get("bank_account") or {}
    if bank.get("hmac"):
        out.append(("has_bank", "bank", bank["hmac"], f"XXXX{bank.get('last4')}"))
    dom = (extraction.get("sender_domain") or {}).get("value")
    if dom:
        out.append(("uses_domain", "domain", dom, dom))
    addr = (extraction.get("vendor_address") or {}).get("value")
    if addr:
        out.append(("has_address", "address", _addr_key(addr), addr[:120]))
    for rel, t, i, label in out:
        add_edge(s, workspace_id, vendor_id, rel, t, i, label, case_id)
    return out


def shared_with_other_vendors(s: Session, workspace_id: str, vendor_id: str, attrs: list[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
    hits = []
    for rel, t, i, label in attrs:
        others = s.scalars(select(GraphEdge).where(GraphEdge.workspace_id == workspace_id, GraphEdge.dst_type == t, GraphEdge.dst_id == i, GraphEdge.src_id != vendor_id)).all()
        for e in others:
            v = s.get(Vendor, e.src_id)
            hits.append({"rel": rel, "kind": REL_LABEL[rel], "label": label, "other_vendor_id": e.src_id, "other_vendor": v.name if v else e.src_id, "case_id": e.case_id})
    return hits


def vendor_graph(s: Session, workspace_id: str, vendor_id: str) -> dict[str, Any]:
    """Nodes/edges within 2 hops: vendor → attributes → other vendors sharing them."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    v = s.get(Vendor, vendor_id)
    if v is None or v.workspace_id != workspace_id:
        raise LookupError("vendor not found")
    nodes[f"vendor:{v.id}"] = {"id": f"vendor:{v.id}", "type": "vendor", "label": v.name, "root": True}
    mine = s.scalars(select(GraphEdge).where(GraphEdge.workspace_id == workspace_id, GraphEdge.src_id == vendor_id)).all()
    for e in mine:
        aid = f"{e.dst_type}:{e.dst_id}"
        sharers = s.scalars(select(GraphEdge).where(GraphEdge.workspace_id == workspace_id, GraphEdge.dst_type == e.dst_type, GraphEdge.dst_id == e.dst_id, GraphEdge.src_id != vendor_id)).all()
        nodes.setdefault(aid, {"id": aid, "type": e.dst_type, "label": e.label, "shared": bool(sharers)})
        edges.append({"source": f"vendor:{vendor_id}", "target": aid, "rel": e.rel})
        for o in sharers:
            ov = s.get(Vendor, o.src_id)
            oid = f"vendor:{o.src_id}"
            nodes.setdefault(oid, {"id": oid, "type": "vendor", "label": ov.name if ov else o.src_id, "shared": True})
            edges.append({"source": oid, "target": aid, "rel": o.rel})
    return {"nodes": list(nodes.values()), "edges": edges}


def shared_attributes(s: Session, workspace_id: str) -> list[dict[str, Any]]:
    from collections import defaultdict

    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    labels: dict[tuple[str, str], str] = {}
    for e in s.scalars(select(GraphEdge).where(GraphEdge.workspace_id == workspace_id)):
        groups[(e.dst_type, e.dst_id)].add(e.src_id)
        labels[(e.dst_type, e.dst_id)] = e.label or e.dst_id
    names = {v.id: v.name for v in s.scalars(select(Vendor).where(Vendor.workspace_id == workspace_id))}
    return [{"type": t, "label": labels[(t, i)], "vendors": [{"id": vid, "name": names.get(vid, vid)} for vid in sorted(vids)]}
            for (t, i), vids in groups.items() if len(vids) > 1]
