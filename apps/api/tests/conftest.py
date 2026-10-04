import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="probity-test-"))
# TEST_DATABASE_URL=postgresql+psycopg://probity_app:...  (+ TEST_DATABASE_MIGRATE_URL for the owner) runs
# the whole suite against Postgres with row-level security enforced; default is a throwaway SQLite file.
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["DATABASE_MIGRATE_URL"] = os.environ.get("TEST_DATABASE_MIGRATE_URL", os.environ["TEST_DATABASE_URL"])
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_tmp / 'test.db').as_posix()}")
os.environ.setdefault("STORAGE_DIR", str(_tmp / "uploads"))
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LLM_MODE", "mock")
os.environ.setdefault("TOOLS_MODE", "cached")
os.environ.setdefault("AUTH_MODE", "local")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from probity import services  # noqa: E402
from probity.db.models import User  # noqa: E402
from probity.db.session import session_scope  # noqa: E402
from probity.demo import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _sync_runs():
    services.run_sync(True)


@pytest.fixture()
def fresh_db():
    seed.reset_db()
    with session_scope() as s:
        ws = seed.seed_workspace(s)
        wsid = ws.id
    seed.write_demo_files()
    return wsid


@pytest.fixture()
def client(fresh_db):
    from probity.api.main import app

    services.run_sync(True)
    return TestClient(app)


def login(client, role: str, nth: int = 0) -> dict:
    with session_scope() as s:
        users = list(s.scalars(select(User).where(User.role == role).order_by(User.email)))
        uid = users[nth].id
    tok = client.post("/api/v1/auth/demo-login", json={"user_id": uid}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}
