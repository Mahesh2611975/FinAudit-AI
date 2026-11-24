# Auth/RBAC Migration Backup and Rollback Plan

## Scope

This migration adds authentication and role-based access control (RBAC) to FinAudit AI.

- Additive schema changes only.
- No destructive table drops.
- No deletion of existing financial records.
- No fabricated users and no silent admin grants.

## Schema Changes (Additive)

Applied via `app.migrations.ensure_database_schema()` / SQLAlchemy metadata create-all with additive SQLite `ALTER TABLE ADD COLUMN` behavior:

- New table: `users`
  - Stores provisioned users, bcrypt password hashes, role, active flag, timestamps.
- New table: `revoked_tokens`
  - Stores revoked JWT `jti` values for logout/invalidation.
- Additive nullable actor identity columns on existing tables:
  - `reconciliation_review_actions.actor_user_id`
  - `investigation_cases.created_by_user_id` (if present in model/migration)
  - `investigation_notes.author_user_id`
  - `investigation_evidence.added_by_user_id`
  - `investigation_events.actor_user_id`
  - `risk_alert_events.actor_user_id`
  - `invoice_discrepancy_actions.reviewer_user_id`
- Additive nullable `owner_user_id` columns on 16 owner-aware financial tables (`audit_runs`, `findings`, `reconciliation_runs`, `reconciliation_results`, `transaction_records`, `investigation_runs`, `investigation_cases`, `vendors`, `purchase_orders`, `goods_receipts`, `invoices`, `invoice_documents`, `invoice_audit_runs`, `invoice_discrepancies`, `risk_analysis_runs`, `risk_alerts`). `NULL` preserves legacy/shared rows; authenticated requests are filtered to their own rows plus shared rows, and admins bypass the filter.

> Exact columns depend on the current model metadata. All are nullable/additive and do not remove or rewrite existing financial data.

## Pre-Migration Backup (Required)

Before starting the app with the new schema or before provisioning users:

1. Stop the backend process.
2. Back up the SQLite database file:

```bash
cp backend/data/finaudit.db backend/data/finaudit.db.bak.$(date +%Y%m%d_%H%M%S)
```

If using a custom `FINAUDIT_DATABASE_URL`, back up that SQLite file instead.

3. Optionally back up the full backend data directory:

```bash
tar -czf finaudit-backup-$(date +%Y%m%d_%H%M%S).tar.gz backend/data backend/uploads
```

## Safe Migration Procedure

1. Confirm backup exists and is readable.
2. Deploy code changes.
3. Install dependencies:

```bash
cd backend
pip install -r requirements.txt
```

4. Start the backend once; startup runs additive schema reconciliation.
5. Verify health:

```bash
curl http://127.0.0.1:8000/api/health
```

6. Provision the first admin explicitly (no silent grant):

```bash
cd backend
# FINAUDIT_JWT_SECRET is read from backend/.env (or the deployment environment).
# Never inline a real secret in a shell command or source file.
FINAUDIT_ADMIN_PASSWORD='ReplaceWithStrongPassword!' \
python -m app.create_admin --username admin --email admin@example.com --full-name "System Administrator"
```

Notes:
- The script is one-time only: it refuses to run once any admin account exists. Later administrators/role changes must go through authenticated admin APIs.
- It never hardcodes credentials; the password comes from `FINAUDIT_ADMIN_PASSWORD` or an interactive prompt.
- It creates a SQLite backup before applying additive schema changes when the DB file exists.

## Post-Migration Verification

- Login with the provisioned admin.
- Confirm `/api/auth/me` returns the authenticated user.
- Confirm protected endpoints reject missing/invalid/expired tokens.
- Confirm reviewer-only reconciliation confirm/reject is enforced.
- Confirm audit/review records store authenticated actor identity.

## Rollback Plan

### Application Rollback (preferred if schema already applied)

Additive columns/tables are safe to keep. Roll back application code to previous version while keeping DB:

1. Stop backend.
2. Restore previous code revision.
3. Restart backend.

Because schema changes are additive, old code may ignore new tables/columns. Verify old code compatibility before doing this in production.

### Full Rollback (restore DB backup)

Use only if migration caused unacceptable issues and you accept loss of data created after backup:

1. Stop backend/frontend.
2. Restore DB backup:

```bash
cp backend/data/finaudit.db.bak.<timestamp> backend/data/finaudit.db
```

3. Restore previous code revision.
4. Reinstall previous dependencies if needed.
5. Start services and verify `/api/health`.

## Security Notes

- Set `FINAUDIT_JWT_SECRET` in every non-local environment. Use at least 32
  random characters (`python -c "import secrets; print(secrets.token_urlsafe(48))"`);
  a missing or weak value aborts startup and token operations instead of falling
  back to a development secret.
- Access tokens expire by `FINAUDIT_ACCESS_TOKEN_EXPIRE_MINUTES` (default 60).
- Logout revokes token JTI in `revoked_tokens`.
- Reviewer identity for approvals/reviews is derived from authenticated user identity, not client-supplied free text.
- Existing financial records are never deleted by auth migration.
