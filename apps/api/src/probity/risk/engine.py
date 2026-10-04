"""Risk engine — pure code, not an agent (Feature F7, Architecture §7, Guardrails G3).

    "The LLM can investigate, but it cannot manipulate the risk score."

Input: fired signals, each linked to the claim that evidences it, plus that claim's verification status.
Nothing else. There is deliberately no parameter through which free text or an LLM adjustment could enter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

WEIGHTS: dict[str, dict[str, int]] = {
    "w1": {
        # core — sums to 100
        "bank_account_changed": 35,
        "price_anomaly": 20,
        "new_domain": 15,
        "identity_mismatch": 10,
        "duplicate_invoice": 10,
        "address_mismatch": 5,
        "missing_po": 5,
        # supplementary — added on top, total clamped to 100
        "suspicious_instruction_in_document": 15,
        "prior_confirmed_issue": 15,
        "quantity_po_mismatch": 10,
        "temporal_anomaly": 5,
        "no_history": 5,
        "statistical_anomaly": 10,  # max
        "round_sum": 0,
        "shared_attribute": 0,  # flags for human review (blocks auto-clear) without changing the agreed weights
    }
}
CORE_SIGNALS = ["bank_account_changed", "price_anomaly", "new_domain", "identity_mismatch", "duplicate_invoice", "address_mismatch", "missing_po"]
CURRENT_VERSION = "w1"

TIERS = [(80, "CRITICAL"), (60, "HIGH"), (30, "MEDIUM"), (0, "LOW")]

LABELS = {
    "bank_account_changed": "Bank account changed",
    "price_anomaly": "Price anomaly",
    "new_domain": "Domain age",
    "identity_mismatch": "Vendor identity mismatch",
    "duplicate_invoice": "Duplicate invoice",
    "address_mismatch": "Address mismatch",
    "missing_po": "Missing PO",
    "suspicious_instruction_in_document": "Instruction-like text in document",
    "prior_confirmed_issue": "Prior confirmed issue",
    "quantity_po_mismatch": "Quantity exceeds PO",
    "temporal_anomaly": "Date anomaly",
    "no_history": "No transaction history",
    "statistical_anomaly": "Statistical outlier",
    "round_sum": "Round-sum amount",
    "shared_attribute": "Shared with another vendor",
}


@dataclass(frozen=True)
class ScoredSignal:
    signal: str
    fired: bool
    claim_id: str | None
    claim_status: str  # verified | unverified | refuted | none
    severity: str = "info"
    points_override: int | None = None  # only for bounded statistical points, capped by weight


@dataclass(frozen=True)
class Contribution:
    signal: str
    label: str
    points: int
    claim_id: str | None
    status: str  # counted | unconfirmed | refuted
    note: str


@dataclass(frozen=True)
class RiskResult:
    score: int
    tier: str
    weights_version: str
    contributions: list[Contribution]

    def explain(self) -> list[dict]:
        return [asdict(c) for c in self.contributions]


def tier_for(score: int) -> str:
    for floor, name in TIERS:
        if score >= floor:
            return name
    return "LOW"


def score(signals: list[ScoredSignal], weights_version: str = CURRENT_VERSION, overrides: dict[str, int] | None = None) -> RiskResult:
    weights = {**WEIGHTS[weights_version], **(overrides or {})}
    total = 0
    contributions: list[Contribution] = []
    for s in sorted(signals, key=lambda x: (-weights.get(x.signal, 0), x.signal)):
        if not s.fired:
            continue
        w = weights.get(s.signal, 0)
        if s.points_override is not None:
            w = max(0, min(w, s.points_override))
        label = LABELS.get(s.signal, s.signal)
        if s.claim_id is None or s.claim_status == "none":
            contributions.append(Contribution(s.signal, label, 0, None, "unconfirmed", "no claim/evidence — dropped (cite-or-drop)"))
        elif s.claim_status == "verified":
            total += w
            contributions.append(Contribution(s.signal, label, w, s.claim_id, "counted", "verified against evidence"))
        elif s.claim_status == "refuted":
            contributions.append(Contribution(s.signal, label, 0, s.claim_id, "refuted", "contradicted by evidence — discarded"))
        else:
            contributions.append(Contribution(s.signal, label, 0, s.claim_id, "unconfirmed", "unverified — 0 points until verified"))
    final = max(0, min(100, total))
    return RiskResult(final, tier_for(final), weights_version, contributions)


def recommendation_for(tier: str) -> str:
    return {"LOW": "PROCEED", "MEDIUM": "REVIEW", "HIGH": "HOLD_PAYMENT", "CRITICAL": "HOLD_PAYMENT"}[tier]
