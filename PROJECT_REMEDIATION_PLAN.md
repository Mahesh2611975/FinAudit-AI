# FinAudit AI — Remediation Plan

**Date**: 2026-11-23  
**Baseline**: `main @ c7ba867`, 147 backend tests passing, frontend build passing, 40/40 navigation checks passing.  
**Rule**: Preserve existing DB and user work. No destructive DB changes. No backfill without verified mapping and approval.

## Stage 1 — Discovery & Baseline (Completed)

- [x] Inspected repo structure, docs, backend, frontend, tests, DB schema/counts.
- [x] Ran baseline: `python3 -m pytest -q` → 147 passed, 1 warning.
- [x] Ran `npm run build` → pass.
- [x] Ran `npm run verify:navigation` → 40 passed.
- [x] Created `PROJECT_HEALTH_AUDIT.md`.

## Stage 2 — Confirmed Defects Prioritized (Completed)

See `PROJECT_HEALTH_AUDIT.md` for full list. Priority order:
1. **C2/H5 Date parsing / historical correction mapping** - parser fixed; source CSV missing blocks backfill.
2. **C1 Auth/RBAC** - open, requires product decision.
3. **H1 Reconciliation reference+amount tolerance** - open.
4. **H3 Frontend API base normalization** - fixed.
5. **H2 Dashboard latest-run selection** - fixed.

## Stage 3 — Safe Fixes Applied This Session

| Fix | Files | Verification | Risk |
|---|---|---|---|
| ISO timestamp date parsing no longer swaps day/month | `backend/app/csv_normalizer.py`, `backend/tests/test_csv_normalizer.py` | `pytest tests/test_csv_normalizer.py -q` → 17 passed | Low; preserves existing YYYY-MM-DD and dayfirst behavior for non-ISO inputs |
| Dashboard reconciliation scope uses latest `ReconciliationRun` | `backend/app/dashboard.py` | `pytest tests/test_dashboard_overview.py tests/test_reconciliation.py -q` → 21 passed | Low; aligns with report/risk semantics |
| Frontend API base strips trailing `/api` consistently | `frontend/src/Dashboard.jsx`, `frontend/src/Transactions.jsx`, `frontend/src/Assistant.jsx` | `npm run build` → pass | Low; config-tolerant normalization |
| Category keyword expansion for future imports | `backend/app/category.py` | Existing tests unaffected; no DB overwrite | Low; affects future categorization only |

## Stage 4 — Blocked / Requires Approval

| Item | Why blocked | Required action |
|---|---|---|
| Backfill date corrections for `transactions (5).csv` | Original source CSV not found in workspace/Downloads; cannot produce verified old->new mapping | Locate original `transactions (5).csv`, generate record-by-record mapping (source_file, source_row, stored_date, source_date, proposed_date, confidence), get explicit approval before any DB update |
| Any DB schema/data migration affecting historical records | Financial data integrity rule | Backup, dry-run report, explicit approval, rollback plan |
| Auth/RBAC implementation | Product/security design decision; affects all clients | Define roles, token model, migration path, endpoint policy matrix |

## Stage 5 — Next Engineering Work (Prioritized)

### P0 - Data integrity
- [ ] Locate `transactions (5).csv`; produce `DATE_PARSING_CORRECTION_REPORT.csv/md` with row-level mapping.
- [ ] Add DB constraints/indexes for `transaction_records(transaction_id unique where not null)`, `(source_file, source_row)` unique where present; additive migration with backup.
- [ ] Fix reconciliation `_pair_status` to respect `amount_tolerance` for reference-matched candidates or document intended business rule; add tests.

### P1 - Security/governance
- [ ] Add authentication (JWT/session), password hashing, user model, RBAC roles.
- [ ] Protect mutating endpoints; enforce verified actor identity for reviews/approvals.
- [ ] Add audit trail for financial record changes and review actions.

### P2 - Frontend contracts/UX
- [ ] Fix stale nav highlight for secondary/invoice views.
- [ ] Add case-from-alert action in Risk drawer.
- [ ] Add assistant answer history and per-document RAG status UI.
- [ ] Remove dead `OverviewPage` or consolidate.
- [ ] Add global `:focus-visible`, keyboard row handling, dialog semantics.
- [ ] Avoid hardcoded INR where currency is known/unknown.

### P3 - Module completeness
- [ ] Vendor intelligence workspace (transaction vendors, not only invoice vendors).
- [ ] Evidence/Documents primary workspace.
- [ ] Investigation workflow statuses/approval gates per spec.
- [ ] Reports: validate calculations against documented definitions.

### P4 - Quality/deployment
- [ ] Convert raw dict endpoints to Pydantic schemas.
- [ ] Add API contract tests, browser smoke tests (evaluate Playwright scope).
- [ ] Code-split frontend bundle.
- [ ] PostgreSQL migration/runbook; Docker Compose; persistent volume/storage.
- [ ] Observability: structured logs, error IDs, health semantics.

## Test Results Log

| Date | Command | Result |
|---|---|---|
| 2026-11-23 | `cd backend && python3 -m pytest -q` | 147 passed, 1 warning (395.50s) |
| 2026-11-23 | `cd frontend && npm run build` | Pass (vite v8.3.5, 16.12s) |
| 2026-11-23 | `cd frontend && npm run verify:navigation` | 40 checks passed |
| 2026-11-23 | `cd backend && python3 -m pytest tests/test_csv_normalizer.py -q` | 17 passed, 1 warning (23.74s) |
| 2026-11-23 | `cd backend && python3 -m pytest tests/test_dashboard_overview.py tests/test_reconciliation.py -q` | 21 passed, 1 warning (75.06s) |
| 2026-11-23 | `cd frontend && npm run build` (after API base fix) | Pass (see command output) |

## Files Changed This Session

- `backend/app/csv_normalizer.py` - strict ISO date/timestamp parsing.
- `backend/tests/test_csv_normalizer.py` - regression tests for ISO timestamp parsing.
- `backend/app/dashboard.py` - latest reconciliation run selected by `ReconciliationRun.id`.
- `backend/app/category.py` - conservative keyword additions for future imports.
- `frontend/src/Dashboard.jsx` - normalize API base with optional `/api` suffix.
- `frontend/src/Transactions.jsx` - normalize API base with optional `/api` suffix.
- `frontend/src/Assistant.jsx` - normalize API base with optional `/api` suffix.
- `PROJECT_HEALTH_AUDIT.md` - created.
- `PROJECT_REMEDIATION_PLAN.md` - created.
- `DATE_PARSING_CORRECTION_REPORT.md` - created (blocked).

## Residual Risks

- Historical date corruption in `transactions (5).csv` rows cannot be confirmed/corrected without source file.
- No auth/RBAC - do not expose beyond trusted local demo.
- Reconciliation tolerance/reference behavior may surprise users; needs product decision + tests.
- Frontend has accessibility and contract gaps; static nav checks are not browser E2E.