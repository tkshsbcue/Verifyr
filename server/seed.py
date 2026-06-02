"""Seed helpers: import checks.json into the DB, create an initial admin user.

Usage:
  python -m server.seed --checks checks.json --email you@example.com --password secret
"""

from __future__ import annotations

import argparse
import json

from .db import SessionLocal, init_db
from .models import Check, User
from .security import hash_password


def import_checks(path: str) -> int:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "checks" in data:
        data = data["checks"]
    db = SessionLocal()
    added = 0
    try:
        for c in data:
            if not isinstance(c, dict) or "name" in c is False:
                continue
            name = c.get("name")
            if not name or name.startswith("_"):
                continue
            if db.query(Check).filter(Check.name == name).first():
                continue
            db.add(
                Check(
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


def create_user(email: str, password: str) -> bool:
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            return False
        db.add(User(email=email, hashed_password=hash_password(password)))
        db.commit()
        return True
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Verifyr database")
    parser.add_argument("--checks", default=None, help="Path to a checks.json to import")
    parser.add_argument("--email", default=None, help="Create an initial user with this email")
    parser.add_argument("--password", default=None, help="Password for the initial user")
    args = parser.parse_args()

    init_db()
    if args.checks:
        n = import_checks(args.checks)
        print(f"Imported {n} new checks from {args.checks}")
    if args.email and args.password:
        ok = create_user(args.email, args.password)
        print(f"User {args.email}: {'created' if ok else 'already exists'}")


if __name__ == "__main__":
    main()
