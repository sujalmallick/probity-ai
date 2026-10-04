"""Concurrent state changes on one case are serialised (H13): exactly one wins, the other gets a conflict."""

import threading

from conftest import login, owner_session
from factories import NEW_DOMAIN, bank_change_spec
from helpers import run_case
from sqlalchemy import func, select

from probity import services as svc
from probity.db.models import Decision, User
from probity.db.session import session_scope


def _race(ws: str, case_id: str, attempts: list[tuple[str, str]]) -> list[str]:
    barrier = threading.Barrier(len(attempts))
    results: list[str] = []

    def go(user_id: str, decision: str) -> None:
        try:
            with session_scope(ws) as s:
                user = s.get(User, user_id)
                barrier.wait()
                svc.decide(s, user, case_id, decision, "a meaningful written reason for this decision")
            results.append(decision)
        except svc.Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=go, args=a) for a in attempts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return sorted(results)


def test_concurrent_approve_and_reject_have_one_winner(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    a0, a1 = world.user("approver", 0).id, world.user("approver", 1).id
    results = _race(world.workspace_id, case["id"], [(a0, "APPROVE"), (a1, "REJECT")])
    assert results.count("conflict") == 1 and len(results) == 2, results
    with owner_session() as s:
        assert s.scalar(select(func.count()).select_from(Decision).where(Decision.case_id == case["id"])) == 1
