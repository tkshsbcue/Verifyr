"""Seed helper: import checks.json into the DB, owned by a Supabase user.

Users are managed by Supabase Auth, so there is no user to create here — pass the
owner's Supabase user id (auth.users.id, a uuid) to attribute the imported checks.

Usage:
  python -m server.seed --checks checks.json --user-id <supabase-user-uuid>
"""

from __future__ import annotations

import argparse
import json

from .db import SessionLocal, init_db
from .models import Check


def import_checks(path: str, user_id: str) -> int:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "checks" in data:
        data = data["checks"]
    db = SessionLocal()
    added = 0
    try:
        for c in data:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not name or name.startswith("_"):
                continue
            if db.query(Check).filter(Check.user_id == user_id, Check.name == name).first():
                continue
            db.add(
                Check(
                    user_id=user_id,
                    name=name,
                    config={
                        "web": c.get("web", {}),
                        "api": c.get("api", {}),
                        "app_targets": c.get("app_targets", []),
                    },
                )
            )
            added += 1
        db.commit()
    finally:
        db.close()
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Verifyr database")
    parser.add_argument("--checks", required=True, help="Path to a checks.json to import")
    parser.add_argument("--user-id", required=True, help="Supabase user id (auth.users.id) to own the checks")
    args = parser.parse_args()

    init_db()
    n = import_checks(args.checks, args.user_id)
    print(f"Imported {n} new checks from {args.checks} for user {args.user_id}")


if __name__ == "__main__":
    main()
