"""Evidence-first agent contract (Feature F6, AI_Instructions Part C).

Agents never return `{"finding": "Vendor is suspicious"}`. They return claims with structured evidence:

    {"claim": "Vendor bank account differs from historical records",
     "evidence": [{"source": "invoice", "field": "bank_account", "value": "XXXX9812"},
                  {"source": "vendor_history", "field": "bank_account", "value": "XXXX1234"}],
     "confidence": 0.99}

The verifier — not the agent — decides whether the claim is supported.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["invoice", "vendor_history", "vendor_master", "purchase_order", "registry", "web", "domain", "vendor_reply", "approver"]


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source
    field: str | None = None
    value: Any = None
    source_ref: str = ""
    excerpt: str | None = Field(default=None, max_length=1200)
    tier: Literal[1, 2, 3] = 1
    match_key: str | None = None  # e.g. HMAC of an account number; never shown to users


class AgentClaim(BaseModel):
    """What every agent must emit. `assertion` is the machine-checkable form of `claim`."""

    model_config = ConfigDict(extra="forbid")
    claim: str = Field(max_length=600)
    signal: str | None = None
    evidence: list[EvidenceIn] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)  # references to evidence already in the store
    confidence: float = Field(ge=0, le=1)
    severity: Literal["info", "warn", "high"] = "info"
    assertion: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
