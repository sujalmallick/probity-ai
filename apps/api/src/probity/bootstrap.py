"""Create an empty Probity database and check configuration.

    python -m probity.bootstrap                     # print the live/missing checklist, create/upgrade the schema
    python -m probity.bootstrap --generate-secrets  # also create apps/api/.env and fill FIELD_KEY_B64 / HMAC_KEY if empty
    python -m probity.bootstrap --reset --yes       # dev only: drop every table and start from an empty schema

This only creates the schema. It never inserts vendors, users, invoices or any other data: users arrive by signing in
with Clerk, and everything else is entered by them.
"""

from __future__ import annotations

import argparse
import base64
import re
import secrets
import sys

from probity.config import ENV_FILE, get_settings

EXAMPLE = ENV_FILE.with_name(".env.example")


def _set_if_empty(text: str, key: str, value: str) -> tuple[str, bool]:
    """Fill `KEY=` when it is missing or empty. Existing values are never replaced (that would make data unreadable)."""
    m = re.search(rf"(?m)^{key}=(.*)$", text)
    if m and m.group(1).split("#", 1)[0].strip():
        return text, False
    line = f"{key}={value}"
    if m:
        return text[: m.start()] + line + text[m.end():], True
    return text.rstrip("\n") + f"\n{line}\n", True


def generate_secrets() -> None:
    if not ENV_FILE.exists():
        ENV_FILE.write_text(EXAMPLE.read_text(encoding="utf-8") if EXAMPLE.exists() else "", encoding="utf-8")
        print(f"created {ENV_FILE} from .env.example")
    text = ENV_FILE.read_text(encoding="utf-8")
    text, a = _set_if_empty(text, "FIELD_KEY_B64", base64.b64encode(secrets.token_bytes(32)).decode())
    text, b = _set_if_empty(text, "HMAC_KEY", secrets.token_urlsafe(48))
    ENV_FILE.write_text(text, encoding="utf-8")
    # Values are written to the file only, never printed.
    print("FIELD_KEY_B64: " + ("generated" if a else "already set, kept"))
    print("HMAC_KEY:      " + ("generated" if b else "already set, kept"))
    get_settings.cache_clear()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m probity.bootstrap", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generate-secrets", action="store_true", help="create apps/api/.env if needed and fill empty app secrets")
    ap.add_argument("--reset", action="store_true", help="dev only: drop all tables, then create an empty schema")
    ap.add_argument("--yes", action="store_true", help="confirm --reset")
    args = ap.parse_args(argv)

    if args.generate_secrets:
        generate_secrets()
    st = get_settings()
    print(st.checklist_text())
    if not st.database_url:
        print("\nDATABASE_URL is required to create the schema. Set it in apps/api/.env and run this again.")
        return 2

    from probity.db import migrate

    if args.reset:
        if st.env == "prod":
            print("refusing to reset a production database")
            return 2
        if not args.yes:
            print("--reset deletes ALL data in this database. Re-run with --reset --yes to confirm.")
            return 2
        migrate.reset_postgres()
        print("\ndatabase reset: empty schema at", migrate.head_revision())
    else:
        before = migrate.current_revision()
        migrate.upgrade()
        after = migrate.head_revision()
        print("\ndatabase schema " + ("created" if before is None else ("upgraded" if before != after else "already up to date")) + f" (revision {after})")

    problems = st.required_problems()
    if problems:
        print(f"\nThe API will not start until the {len(problems)} required setting(s) above are set (in the environment or {ENV_FILE}).")
        return 1
    print("\nAll required settings are present. Start the API: python -m uvicorn probity.api.main:app --port 8010")
    return 0


if __name__ == "__main__":
    sys.exit(main())
