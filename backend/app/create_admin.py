"""Safely provision the first FinAudit administrator.

This command is intentionally explicit: it never runs automatically at startup,
never fabricates credentials, and never silently grants admin privileges.

Usage (from backend/ directory):
  python -m app.create_admin --username admin --email admin@example.com

Password is read from FINAUDIT_ADMIN_PASSWORD or prompted interactively.
This is a ONE-TIME bootstrap: it refuses to run once any admin account exists.
Further administrators must be provisioned by an authenticated admin through
POST /api/auth/users or promoted via PATCH /api/auth/users/{id}.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from .auth import hash_password
from .database import SessionLocal, engine
from .migrations import backup_sqlite_database, ensure_database_schema, has_pending_schema_changes
from .models import ROLE_ADMIN, User, Base


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision a FinAudit admin user safely.")
    parser.add_argument("--username", required=True, help="Admin username (3-150 chars).")
    parser.add_argument("--email", default=None, help="Optional admin email.")
    parser.add_argument("--full-name", default=None, help="Optional display name.")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Explicitly reset password for an existing user with the same username. "
             "Does not change role unless --force-role is also provided.",
    )
    parser.add_argument(
        "--force-role",
        action="store_true",
        help="With --reset-password, also set the existing user's role to admin. "
             "This is an explicit administrative action; it is never implicit.",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="With --reset-password, reactivate an inactive existing user.",
    )
    return parser.parse_args()


def _read_password() -> str:
    env_password = os.getenv("FINAUDIT_ADMIN_PASSWORD")
    if env_password:
        return env_password
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("ERROR: Passwords do not match.", file=sys.stderr)
        sys.exit(2)
    return password


def main() -> int:
    args = _parse_args()
    username = args.username.strip()
    if len(username) < 3 or len(username) > 150:
        print("ERROR: --username must be 3-150 characters.", file=sys.stderr)
        return 2

    password = _read_password()
    try:
        hashed = hash_password(password)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Apply additive schema before provisioning so the users table exists.
    if has_pending_schema_changes(engine, Base.metadata):
        backup = backup_sqlite_database(engine, label="pre-auth-provision")
        if backup:
            print(f"SQLite backup created before schema migration: {backup}")
    report = ensure_database_schema(engine, Base.metadata)
    if report.get("added_columns") or report.get("created_indexes"):
        print(f"Schema reconciled: {report}")

    db = SessionLocal()
    try:
        # One-time bootstrap guard. After the first admin exists, this CLI can
        # neither mint nor modify admins. Authenticated admin APIs are the only
        # supported way to change admin accounts from then on.
        admin_count = db.query(User).filter(User.role == ROLE_ADMIN).count()
        if admin_count > 0:
            print(
                f"ERROR: {admin_count} admin account(s) already exist. One-time bootstrap is closed. "
                "Use the authenticated admin APIs to create or change administrators.",
                file=sys.stderr,
            )
            return 3

        existing = db.query(User).filter(User.username == username).first()
        if existing:
            if not args.reset_password:
                print(
                    f"ERROR: User '{username}' already exists (id={existing.id}, role={existing.role}, active={existing.is_active}). "
                    "No changes made. Pass --reset-password explicitly to reset its password.",
                    file=sys.stderr,
                )
                return 3
            existing.hashed_password = hashed
            if args.force_role:
                print(f"Explicit role change: {existing.username} {existing.role} -> {ROLE_ADMIN}")
                existing.role = ROLE_ADMIN
            if args.activate:
                existing.is_active = True
            existing.is_provisioned = True
            db.commit()
            print(
                f"Updated existing user '{existing.username}' (id={existing.id}, role={existing.role}, active={existing.is_active})."
            )
            return 0

        email = args.email.strip().lower() if args.email else None
        if email and db.query(User).filter(User.email == email).first():
            print(f"ERROR: Email '{email}' is already in use.", file=sys.stderr)
            return 3

        user = User(
            username=username,
            email=email,
            full_name=args.full_name.strip() if args.full_name else None,
            hashed_password=hashed,
            role=ROLE_ADMIN,
            is_active=True,
            is_provisioned=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created admin user '{user.username}' (id={user.id}).")
        print("Store the password securely. It is not logged or written by this script.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())