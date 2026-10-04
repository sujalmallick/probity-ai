"""Policy-driven invoice risk scoring on real cases, and the workspace policy that drives it.

GET  /cases/{id}/invoice-risk          score the case's invoice against workspace history (?narrate=true adds LLM prose)
GET  /workspace/invoice-risk-policy    the policy in force (workspace's own, or the bundled default)
PUT  /workspace/invoice-risk-policy    owner only; validated and audited
DELETE /workspace/invoice-risk-policy  owner only; revert to the default
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from probity import services as svc
from probity.api.deps import current_user, db, require_mfa_for_approvals
from probity.db.audit import audit
from probity.db.models import User, Workspace
from probity.risk import case_scoring
from probity.risk.invoice_scoring import validate_policy

router = APIRouter(prefix="/api/v1", tags=["risk"])


@router.get("/cases/{case_id}/invoice-risk")
def case_invoice_risk(case_id: str, narrate: bool = Query(False), user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    case = svc.get_case(s, user.workspace_id, case_id)
    if not case.extraction:
        raise svc.Conflict("the invoice has not been read yet")
    return case_scoring.score_case(s, case, narrate=narrate)


@router.get("/workspace/invoice-risk-policy")
def get_invoice_risk_policy(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    policy, source = case_scoring.workspace_policy(s.get(Workspace, user.workspace_id))
    return {"policy": policy, "source": source}


@router.put("/workspace/invoice-risk-policy")
def put_invoice_risk_policy(body: dict[str, Any], request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    problems = validate_policy(body)
    if problems:
        raise svc.BadRequest("invalid invoice risk policy: " + "; ".join(problems[:10]))
    ws = s.get(Workspace, user.workspace_id)
    assert ws
    before = (ws.policy or {}).get(case_scoring.POLICY_KEY)
    ws.policy = {**(ws.policy or {}), case_scoring.POLICY_KEY: body, "reviewed_at": datetime.now(timezone.utc).isoformat()}
    audit(s, ws.id, user.id, "invoice_risk_policy.updated", ws.id, {"before": before, "after": body}, request.state.request_id)
    return {"policy": body, "source": "workspace"}


@router.delete("/workspace/invoice-risk-policy")
def reset_invoice_risk_policy(request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    ws = s.get(Workspace, user.workspace_id)
    assert ws
    before = (ws.policy or {}).get(case_scoring.POLICY_KEY)
    ws.policy = {k: v for k, v in (ws.policy or {}).items() if k != case_scoring.POLICY_KEY}
    audit(s, ws.id, user.id, "invoice_risk_policy.reset", ws.id, {"before": before}, request.state.request_id)
    return {"policy": case_scoring.default_policy(), "source": "default"}
