# FinAudit AI — Phase 1 Application Health Audit (V2)

**Audit date:** 2026-11-23
**Scope:** Inspection and reporting only. No application code, database records, migrations, configuration, or functionality was modified.
**Repository state at audit start:** clean working tree at `469d378` (`Update FinAudit AI landing page branding`).

---

## 1. Executive summary

FinAudit AI has a **broad, non-trivial implementation** across all 14 requested modules. The backend includes persisted models, explicit API surface, centralized fail-closed route policy, role-based access control, deterministic audit/reconciliation/risk/invoice engines, LangGraph-based agents, RAG ingestion/retrieval, and a large pytest suite. The frontend builds successfully and passes 102 static navigation-contract checks.

The evidence does **not** support a “Verified working” classification for any module. All 14 modules are classified **Partially verified**. No module is “Not verified” or “Blocked”: source, tests, and configuration were available for inspection, and the safe test suites executed successfully. However, verification is limited by:
- no frontend runtime/browser tests and no CI pipeline;
- no live Ollama/LLM validation (tests mock or disable the model);
- no cross-user/module-specific owner-isolation tests for most modules;
- multiple documentation contradictions and a blocked historical-date backfill;
- material security, financial-correctness, and AI-grounding defects.

The most serious findings are a **hardcoded JWT development fallback** that can allow token forgery if deployment secrets are omitted, and a **global, ownerless RAG corpus** that can expose documents and evidence across users. High-priority functional and financial risks include a broken exact `GET /api/documents` permission rule, advisory-only human approval, manual reconciliation link double-counting, unlabelled/ignored reconciliation amount tolerance, inflated risk/vendor/invoice aggregates, mixed-currency totals, and uploads persisted before validation.

**Production-readiness assessment: Not production-ready.** The application appears substantially engineered and testable, but it should not be exposed to untrusted or multi-tenant users until the Critical and High findings below are fixed and re-tested.

---

## 2. Test results

All commands were run without changing source, configuration, or data to influence the outcome.

| Command | Result | Notes |
|---|---|---|
| `python3 -m pytest -q` (from `backend/`) | **196 passed**, 2 warnings in 150.89 s | Warnings are third-party deprecations (`anyio`/pytest `CallSpec2`, FastAPI `HTTP_422_UNPROCESSABLE_ENTITY`). |
| `npm run build` (from `frontend/`) | **Passed** (22.98 s) | 2,543 modules transformed. Warnings only: `lucide-react` `"use client"` directive may not survive bundling; bundle chunk >500 kB (1,094 kB JS / 296 kB gzip). |
| `npm run verify:navigation` (from `frontend/`) | **102 checks passed** | Static source/regex contract only — **not** a browser or runtime test. See §3.4. |

Static inventory: **22 test files, 194 `def test_*` functions** under `backend/tests/` (pytest reports 196 collected/executed cases, including parametrization). The frontend has **no test runner** (`frontend/package.json:6-11` defines only `dev`, `build`, `preview`, `verify:navigation`).

---

## 3. Cross-cutting findings

### 3.1 Documentation contradicts the shipped authentication implementation

Multiple documents claim the application has **no authentication**:
- `README.md:228`, `FINAL_DELIVERABLES.md:249`, `DEPLOYMENT.md:30-33,147`
- `PROJECT_HEALTH_AUDIT.md:60,67,117,137`, `PROJECT_REMEDIATION_PLAN.md:101`, `PROJECT_AUDIT.md:84`
- `deploy/.env.production.example` still states: *“No authentication is implemented. Do not expose this deployment to untrusted users without adding an auth layer…”*

Current code contradicts this:
- Protected routers are mounted at `backend/app/main.py:98-103`.
- Centralized route permissions and enforcement exist at `backend/app/auth.py:459-566`.
- `User` / `RevokedToken` models exist at `backend/app/models.py:710-745`.
- `authentication_enabled: True` is reported at `backend/app/main.py:182`.
- `backend/tests/test_auth_rbac.py` (31 tests) exercises hashing, login, JWT failure modes, RBAC denials, approval workflow, cross-account isolation, and demo-mode gating.

**Risk:** operators following the stale production template may apply unnecessary network restrictions, or conversely may distrust/misconfigure the auth layer. Documentation cannot be used as a production-readiness source until corrected.

### 3.2 Stale test-count and capability claims

- Docs claim “125 passing backend tests” (`README.md:24,215,243`, `FINAL_DELIVERABLES.md:234`), “147 passed” (`PROJECT_HEALTH_AUDIT.md:13,147`, `PROJECT_REMEDIATION_PLAN.md:4,10,78`), and “40 navigation checks” (`PROJECT_HEALTH_AUDIT.md:15,149`, `PROJECT_REMEDIATION_PLAN.md:12,80`). Current reality: 196 pytest cases and 102 static navigation checks.
- `RISK_INTELLIGENCE_COMPLETION.md:25,90` claims Groq/RAG; current LLM configuration is Ollama-only (`backend/app/llm.py`, `backend/tests/test_llm.py:47-145`).
- `PROJECT_HEALTH_AUDIT.md:73` (H1: reconciliation tolerance bypass) is **fixed/stale**: `_pair_status` validates amount first (`backend/app/reconciliation.py:438-496`) and regressions assert the gate survives bad tolerance values (`backend/tests/test_reconciliation.py:369-389,585-605,639-667`).
- Geographic record counts differ between snapshot documents; no live read-only DB query was performed in this audit, so neither count is asserted as current.

### 3.3 Demo-mode configuration drift

- `backend/.env.example:35` ships `FINAUDIT_LOCAL_DEMO_MODE=false`.
- `frontend/.env.example:8` ships `VITE_LOCAL_DEMO_MODE=true`.
- Root `.env.example:27-28` comments both as `false`.

The backend is authoritative for session issuance; the frontend flag only hides auth screens. The drift can produce a landing page that advertises demo mode while the backend refuses to issue a demo token (or vice versa). Tests confirm the backend refuses demo mode in `production`/`prod`/`staging` (`backend/tests/test_auth_rbac.py:616-678`), but the shipped example files are inconsistent.

### 3.4 `verify:navigation` is a static contract, not runtime verification

`frontend/scripts/verify-navigation.mjs:13-27` reads source files as text (including backend source and tests); `check()` at `29-33` applies regex/`includes` assertions. It does not render React, exercise routing, click buttons, call APIs, or verify applied CSS. Passing this script supports “the expected strings and wiring are present,” not “the UI works at runtime.”

### 3.5 Database/schema inventory

31 models/tables in `backend/app/models.py`:

| Domain | Tables |
|---|---|
| Core audit | `audit_runs` (8), `findings` (23) |
| Reconciliation | `reconciliation_runs` (41), `reconciliation_results` (90), `reconciliation_review_actions` (133) |
| Transactions | `transaction_records` (153) |
| Investigation | `investigation_runs` (195), `investigation_cases` (222), `investigation_notes` (253), `investigation_evidence` (265), `investigation_events` (281) |
| Vendor/procurement | `vendors` (303), `purchase_orders` (325), `purchase_order_items` (350), `goods_receipts` (366), `goods_receipt_items` (385) |
| AP/invoice | `invoices` (401), `invoice_items` (437), `invoice_documents` (454), `invoice_matches` (475), `invoice_discrepancies` (492), `invoice_payments` (516), `invoice_audit_runs` (533), `invoice_audit_evidence` (554), `invoice_review_actions` (568) |
| Risk | `risk_analysis_runs` (587), `risk_alerts` (609), `risk_alert_events` (648) |
| Geographic | `geocode_cache` (670) |
| Auth | `users` (711), `revoked_tokens` (737) |

Observations:
- 16 root/aggregate models carry nullable `owner_user_id`; row scoping is applied globally via SQLAlchemy ORM events in `backend/app/ownership.py:62-120` and installed in `backend/app/auth.py:436-448`. Admins bypass the filter.
- **No `org_id`/tenant column exists.** Isolation is per-user ownership only; `NULL` owner preserves legacy shared rows.
- Child tables (`purchase_order_items`, `goods_receipt_items`, `invoice_items`, `invoice_matches`, `invoice_payments`, `invoice_audit_evidence`, `geocode_cache`) have no owner column and rely on parent scoping or are global caches.
- RAG data lives in a separate store (`backend/data/rag/rag.sqlite3`, per `PROJECT_AUDIT.md:16`), not in these models.
- Migrations are additive only (`backend/app/migrations.py:68-140`); `NOT NULL` columns without defaults are added nullable with a warning (`91-98`). Backup/pending-change helpers exist (`143-191`) but are not directly tested. `has_pending_schema_changes` ignores missing indexes (`149-157`), backup is skipped for non-SQLite/empty DBs (`170-177`), and the documented restore filename does not match the code's backup path (M-15).

### 3.6 Test-coverage gaps

- Migration helpers (`ensure_database_schema`, `has_pending_schema_changes`, `backup_sqlite_database`) are only mocked in tests (`backend/tests/test_auth_rbac.py:580-582`); no test exercises `ALTER TABLE`, index creation, backup creation, or rollback.
- No test for `POST /api/documents/sync-transactions` (`backend/app/main.py:1733`) despite it being in the permission matrix (`backend/app/auth.py:487`).
- No tests for `GET /api/assistant/suggestions` / `GET /api/assistant/config` (`backend/app/main.py:1812-1830`).
- No exhaustive table-driven “every role × every route” RBAC test for the 55 rules in `backend/app/auth.py:459-520`; a static match of all 105 non-auth decorated routes found `GET /api/documents` unmatched (plus the intentionally public `/` and `/api/health`).
- `/api/agents/runs` and `/api/agents/runs/{id}` (`backend/app/main.py:1388-1440`) have no direct list/detail, limit, 404, or cross-account tests.
- Transaction-side vendor intelligence has essentially no dedicated tests (only AP-side vendor analytics in `backend/tests/test_invoice_audit.py:353-361`).
- Export tests assert status/content-type/substrings, not full row counts, column sets, or rendered PDF content.
- No frontend runtime tests of any kind.
- Historical transaction date backfill is blocked: parser fix and regressions exist (`backend/app/csv_normalizer.py:529-555`, `backend/tests/test_csv_normalizer.py:321,336`), but the source `transactions (5).csv` is absent; 132 ambiguous rows and 28 inversions/7 heuristic swaps are explicitly **not** corrections (`DATE_PARSING_CORRECTION_REPORT.md:1-4,21-29,48-53`).

### 3.7 Deployment/build hygiene

- `render.yaml` and `backend/Dockerfile` define Docker deployment with health check `/api/health`, PostgreSQL via `FINAUDIT_DATABASE_URL`, and 1 uvicorn worker.
- Dependencies are range-pinned/pinned to `"latest"` with no backend lockfile or hashes (`backend/requirements.txt:1-23`, `frontend/package.json:13-20`); only `npm ci` is reproducible from `frontend/package-lock.json`.
- `FINAUDIT_JWT_SECRET`, `FINAUDIT_ENV`, and demo flags are absent from `render.yaml:13-33`, `deploy/.env.production.example:1-45`, and the `DEPLOYMENT.md:90-94` checklist; the container runs as root with 1 worker (`backend/Dockerfile:6-33`).
- No CI workflow exists (`.github/` absent). No automated gate runs pytest/build/navigation checks on push.
- Only `.env.example` files are tracked; no populated `.env` is in git (`.gitignore` excludes them). `frontend/.env.example:5-8` nevertheless enables demo navigation by default.

---

## 4. Module-by-module assessment

| Module | Classification | Primary reason |
|---|---|---|
| 1. Financial Intelligence Dashboard | Partially verified | Backend formulas tested; no frontend runtime tests; totals/date/duplicate risks. |
| 2. Transaction Management | Partially verified | Import/normalization tested; no correction path; duplicate/date risks. |
| 3. Advanced Reconciliation Engine | Partially verified | Strong engine tests; manual-link double-count and tolerance semantics defects. |
| 4. Financial Risk Intelligence Center | Partially verified | Deterministic detectors tested; historical alert double-count and exposure defects. |
| 5. Geographic Intelligence | Partially verified | Strong backend geo tests; GET-side geocoding writes and aggregate/summary mismatch. |
| 6. Vendor Intelligence | Partially verified | One narrow KPI test; currency, duplicate-status, and tenant-code risks. |
| 7. Investigation Cases | Partially verified | CRUD/tests exist; no human approval gate; alert-to-case UI missing. |
| 8. AI Investigation Assistant | Partially verified | LLM/RAG flow tested; exact documents route broken; global RAG; prompt-injection risk. |
| 9. Multi-Agent Investigation System | Partially verified | Graphs/persistence tested; advisory-only human review; raw error leakage. |
| 10. Agent Execution History | Partially verified | Main persistence tested; limit bypass, no pagination/redaction. |
| 11. Knowledge Base / RAG | Partially verified | Ingestion/retrieval tested; ownerless global corpus and pickle index. |
| 12. Invoice Audit | Partially verified | Extensive rules tests; upload validation order, currency, and tolerance defects. |
| 13. Reports and Exports | Partially verified | Provenance/filter tests; 10k caps, no report filters, weak PDF fidelity. |
| 14. Settings and Security | Partially verified | Auth/RBAC tests strong; hardcoded JWT fallback and config drift. |

No module is classified **Verified working**, **Not verified**, or **Blocked**. "Partially verified" means real backend implementation and tests exist, but evidence is insufficient for full confidence because of the cross-cutting gaps above and the module-specific defects below.

**Navigation note:** the 14-module inventory is an audit construct, not a literal sidebar list. `frontend/src/main.jsx:105-155` defines 12 primary sidebar items and `:170` defines 28 routable views. Modules 9 (Multi-Agent Investigation System) and 11 (Knowledge Base/RAG) have no distinct primary nav item — they are reached through the `agents` and `assistant` views (`main.jsx:3381-3386`).

### 1. Financial Intelligence Dashboard — **Partially verified**

- **Frontend:** `view: 'overview'` → `Dashboard` (`frontend/src/main.jsx:3337-3341`; `frontend/src/Dashboard.jsx:41-266`): KPI cards, five charts, recent activity, scope notes, manual refresh. Failed refresh preserves last successful payload (`Dashboard.jsx:66-69`).
- **Backend:** `GET /api/dashboard/overview` (`backend/app/main.py:1465-1467`) plus five analytics endpoints (`main.py:1257-1363`). Router permissions `data:read`/`data:export`.
- **Tables:** reads `transaction_records`, `reconciliation_runs`, `reconciliation_results`, `risk_alerts` (`backend/app/models.py:40-191,608-642`).
- **Formulas:** spending/received summed over **all** stored transactions (`dashboard.py:95-104`); net = received − spending (`172`); reconciliation metrics from latest run by descending id (`106-147`); match rate = `matched / max(1, avg(bank_record_count, ledger_record_count)) * 100` (`139-145`); activity chart uses last six **populated** month keys, not calendar months (`47-81`).
- **Tests:** `backend/tests/test_dashboard_overview.py:151-370` asserts persisted-record KPIs, refresh recomputation, empty state, the exact 0.48% match-rate case, six-populated-month behavior, and `Uncategorized` default; `test_finaudit_platform.py:52-75` and `test_end_to_end_deterministic.py:92-94` add integration assertions.
- **Defects/risks:** totals include duplicate and future-dated rows with no period-closure/as-of rule and no transaction correction endpoint; match-rate denominator can mislead on asymmetric sides; last-six-populated-months can be displaced by a future-date typo; float money accumulation diverges from `Decimal`-based analytics; transaction detail links reconciliation rows by reference only with arbitrary `.first()` (`main.py:1634-1661`).
- **Missing:** no frontend tests or browser verification; no per-module owner-isolation test.

### 2. Transaction Management — **Partially verified**

- **Frontend:** `view: 'transactions'` → `Transactions` (`frontend/src/main.jsx:3332-3353`; `frontend/src/Transactions.jsx:30-266`): CSV preview, import, filters, search, pagination, detail drawer.
- **Backend:** `POST /api/transactions/preview`, `POST /api/audits`, `GET /api/audits[...]`, `GET /api/transactions`, `GET /api/transactions/{ref}`, `GET /api/transactions/export.csv` (`backend/app/main.py:240-410,428-495,1511-1701`); permissions `data:import`, `audit:run/read`, `data:read/export`.
- **Workflow:** extension/size/UTF-8 validation → normalization → duplicate count against stored rows → preview → import creates `AuditRun`/`Finding`/`TransactionRecord` rows; categorization via keyword matching (`backend/app/category.py:36-42`) stores only the category, discarding rule/uncertainty provenance.
- **Validation/error handling:** `.csv`, non-empty, 5 MB cap, required columns, invalid dates/amounts, duplicate reference conflicts, ISO timestamp handling (`backend/app/csv_normalizer.py:529-555,632-815`); preview returns errors instead of failing; import rejects with 422.
- **Tests:** `test_csv_normalizer.py:41-345`, `test_finaudit_platform.py:80-122`, `test_exports_and_mapping.py:148-169`, `test_transaction_enrichment.py:24-123`, E2E import/export assertions.
- **Defects/risks:** duplicate prevention uses `duplicate_key` plus `(source_file, source_row)`; re-importing same-named/reordered files can skip genuine rows or skip genuinely repeated payments; no PATCH/PUT/DELETE to correct, reclassify, void, or delete an imported transaction; export is silently capped at 10,000 rows default / 50,000 max and lives on Reports, not the Transactions page; non-ISO dates use `dayfirst=True`, so `03/04/2025` is silently interpreted as 3 April.
- **Missing:** category override UI; frontend tests; owner-isolation tests.

### 3. Advanced Reconciliation Engine — **Partially verified**

- **Frontend:** `view: 'reconciliation'` → `ReconciliationPage` (`frontend/src/main.jsx:3364-3370`, component `932-1552`): two-file upload, tolerances, column mappings, preview, results, filters, review, manual link, history, export.
- **Backend:** preview/create/list/detail/export, result review, manual link, legacy confirm (`backend/app/main.py:848-1253,1914-1939`); permissions `reconciliation:run/read/review/approve`, with explicit admin/reviewer gate on confirm and manual link.
- **Tables:** `reconciliation_runs`, `reconciliation_results`, `reconciliation_review_actions` (`backend/app/models.py:40-149`).
- **Matching logic:** exact-reference priority → `_pair_status` validates amount first (`backend/app/reconciliation.py:438-496`) → defense-in-depth `append_pair_result` gate (`606-632`) → no-reference fallback requires identical direction, **exact equal amounts**, and compatible references/descriptions (`693-752`) → leftovers as bank/ledger-only (`754-760`). Date tolerance is inclusive days; amount tolerance is inclusive absolute currency units (`143-175`). Non-finite tolerance is rejected.
- **Tests (strongest in repo):** 28 tests in `backend/tests/test_reconciliation.py:33-667` covering priority, tolerances, direction, intra-file duplicates, ambiguity, one-to-one constraints, API flows, determinism, manual link, amount-gate defense with a faulty classifier, and non-finite tolerance attack; plus mapping tests and E2E run.
- **Defects/risks:** manual link creates a new `matched` row but sets only `review_status='linked'` while source `status` values stay unchanged, so they remain counted as unmatched/exceptions while the new row is counted as matched (`main.py:1195-1211`, `823-829`, `dashboard.py:129-145`); amount tolerance is unlabelled absolute currency units in the UI and is ignored (`0.0`) on the no-reference fallback path (`reconciliation.py:698`); direction conflicts are labelled `amount_mismatch` with zero amount difference (`461-467`); manual confirmation flips exceptions to `matched` without revalidating amounts and preserves contradictory “not confirmed” explanation text (`main.py:1027-1041`); duplicate detection is intra-file/exact-only and the `duplicates` list is never appended to (`209,330-348,570-586`); reviewer field is decorative because backend uses authenticated identity; `float(bank_amount or ledger_amount or 0.0)` (`main.py:824`) misreports genuine zero bank amounts; `_refresh_reconciliation_run` (`800-834`) does not refresh `run.difference`; create-run/audit commits (`399,961`) are unguarded and request bodies are raw dicts (`996-999,1120-1123,1914-1917`).
- **Missing:** cross-file near-duplicate detection, frontend tests, owner-isolation tests.

### 4. Financial Risk Intelligence Center — **Partially verified**

- **Frontend:** `view: 'risk-intelligence'` → `RiskIntelligence` (`frontend/src/main.jsx:3375-3377`); overview/channels/trends/config/alerts loader and review drawer (`frontend/src/RiskIntelligence.jsx:149-233,365-485,610-712`).
- **Backend:** 12 endpoints under `risk_router` (`backend/app/risk_api.py:65-684`): config, overview, alerts + CSV export, alert detail/review, analyze, channels, trends, geography, analysis runs, investigate. Router-level permission enforcement at `backend/app/main.py:102`.
- **Tables:** `risk_analysis_runs`, `risk_alerts`, `risk_alert_events`, plus source tables `transaction_records`, invoices/discrepancies/payments, reconciliation runs/results, vendors (`backend/app/models.py:40,89,152-644`).
- **Workflows:** threshold resolution → all detectors → dedupe by fingerprint → upsert alerts → delete stale unreviewed machine alerts → persist counts (`risk_engine.py:1024-1148`); review writes an append-only event; investigation builds deterministic evidence then calls guarded LLM.
- **Validation/error handling:** thresholds, review action/status, filters, and granularity are validated; invalid trend dates are silently ignored (`risk_engine.py:1432-1447`); LLM failure falls back safely.
- **Auth:** `risk:read` for GETs, `risk:review` for review, `risk:analyze` for analyze, `investigation:write` for investigate (`backend/app/auth.py:508-512`); actor derived from authenticated user.
- **Tests:** `test_risk_engine.py:53-174` and `test_risk_api.py:78-208` cover thresholds, duplicate-payment detection/idempotence, analytics, review persistence, investigation evidence, and invalid granularity; lender denial at `test_auth_rbac.py:513-521`.
- **Financial-correctness defects:** overview counts/sums **all historical alerts**, not just the latest run, inflating KPIs (`risk_engine.py:1172-1215`); duplicate-invoice exposure sums the whole duplicate group instead of incremental value (`728,742,788`); `suspicious_vendors` mixes `id:<id>` and raw names (`1179-1184,1208`); missing-approval detector infers approval from reference/vendor text rather than approval records (`548-563`); only latest reconciliation run is used (`938`).
- **Missing:** no scheduler/background analysis, no UI threshold overrides, no manual alert creation, no persisted AI investigation result, no create-case-from-alert action in the Risk UI.

### 5. Geographic Intelligence — **Partially verified**

- **Frontend:** `view: 'geographic-intelligence'` → `GeographicRiskIntelligence` (`frontend/src/main.jsx:3378-3380`), Leaflet + markercluster map and panels (`frontend/src/GeographicRiskIntelligence.jsx:496-963`).
- **Backend:** eight endpoints under `risk_router` (`backend/app/risk_api.py:383-617`): options, summary, map, countries, cities, high-risk-locations, trend, legacy geography. All require `risk:read`.
- **Tables:** `transaction_records`, `risk_alerts`, global `geocode_cache` (`backend/app/models.py:152-644,662-686`).
- **Workflow:** filter validation → snapshot entries → alert linking by record/transaction ID → coordinate resolution (cached geocoding when enabled) → markers/aggregates/summary.
- **Validation/error handling:** ISO date order, risk level, and negative amount return 422 (`backend/tests/test_geo_api.py:449-495`); imported risk labels never fabricate alerts or coordinates (`test_geo_fixes.py:105-149,427-485`).
- **Auth/scoping:** `risk:read` on all endpoints; owner filtering on transactions/alerts; geocode cache is global and contains only place names/coordinates.
- **Tests:** `test_geo_api.py:184-495` and `test_geo_fixes.py:105-545` cover filters, cache behavior, zero coordinates, alert linking, distribution consistency, no fabricated locations, import idempotence, and invalid inputs. Real Nominatim behavior is mocked.
- **Defects/risks:** country/city/trend panels skip unlocated rows while summary flagged/high-risk values include all matched entries, so exposure totals can diverge (`geo_analytics.py:519,604,707` vs `759-765,813-824`); country centroids are unweighted means (`577-585`); “unusual location” is volume-only (`677-701`); GET requests can contact Nominatim and write `geocode_cache` when enabled (`409-455`, `859-861`); OSM tiles require network at runtime.
- **Missing:** no geocoding toggle/max-marker UI, no cache administration, no offline map fallback, no dedicated unusual-location endpoint.

### 6. Vendor Intelligence — **Partially verified**

- **Frontend:** `view: 'vendor-intelligence'` → `VendorIntelligencePage` (`frontend/src/main.jsx:3401`, component `2028-2070`): add-vendor form and analytics table. Uses only `GET /api/vendors/analytics` and `POST /api/vendors`.
- **Backend:** `POST /api/vendors`, `GET /api/vendors/analytics|/vendors|/vendors/{id}`, `POST /api/vendors/import`, `GET /api/invoice-analytics/vendors` (`backend/app/invoice_api.py:607-683,1368-1370`); `invoice:write` for mutations, `invoice:read` for reads (`backend/app/auth.py:495-497,507`).
- **Tables:** `vendors`, `invoices`, `invoice_discrepancies`, `invoice_payments` (`backend/app/models.py:302-529`).
- **Calculation:** sums cached `total_minor`/`paid_minor`/`outstanding_minor` per vendor; averages by integer floor; counts open discrepancies and duplicate-type discrepancies (`backend/app/invoice_audit.py:1044-1086`).
- **Validation:** vendor name required, duplicate code → 409, currency shape checked; no check that all invoices share the vendor currency or that cached balances reconcile.
- **Tests:** only `test_invoice_audit.py:353-361` asserts `invoice_count == 1` and `total_invoiced_minor == 25000`. Paid/outstanding/average/discrepancy KPIs, currency handling, import validation, owner isolation, and role denials are untested.
- **Risks:** `duplicate_count` ignores review status, so dismissed duplicates remain in the KPI (`invoice_audit.py:1061-1069`); cached balances are trusted without validation; mixed currencies are summed and formatted with one vendor code; globally unique `vendor_code` (`models.py:308`) can reveal another tenant’s code via 409; no vendor-level risk score, trend, drilldown, import UI, or date filter.

### 7. Investigation Cases — **Partially verified**

- **Frontend:** sidebar `view: 'cases'` (`frontend/src/main.jsx:124-131`), `CasesPage` (`3970-4142`) and `InvestigationsPage` (`3358-3360`): list/detail/create, status update, notes, evidence, AI explanation, CSV export.
- **Backend:** `GET/POST /api/investigations`, `POST /api/investigations/from-alert/{alert_ref}`, `GET/PATCH /api/investigations/{case_ref}`, `POST .../{case_ref}/notes|evidence|explain`, `GET .../{case_ref}/history|export.csv` (`backend/app/investigation_api.py:338-658`). Route policy requires `investigation:read`/`investigation:write` (`backend/app/auth.py:513-519`).
- **Tables:** `investigation_cases` and `investigation_runs` are owner-aware; `investigation_notes`, `investigation_evidence`, `investigation_events` are not (`backend/app/models.py:194-293`).
- **Workflows:** manual create; alert-to-case with evidence import and alert status transition; review loop with event history; AI explanation with fallback metadata; CSV export.
- **Validation/error handling:** status is normalized/validated; severity, source type, resolution, and note text are weakly constrained or unbounded (`investigation_api.py:56-91,482-487`).
- **Auth/approval:** actor identity comes from the authenticated user (`investigation_api.py:300,473,519,536`), and RBAC tests prove note authorship and cross-account case isolation (`test_auth_rbac.py:214-231,524-569`). However, any `investigation:write` role can set status to `resolved`/`dismissed` and set `resolved_at` without a reviewer/admin approval gate (`investigation_api.py:36-44,464-508`).
- **Tests:** `backend/tests/test_investigation_api.py:88-170` and `test_end_to_end_deterministic.py:123-149,218-231` cover create-from-alert, evidence copy, alert status change, notes, explanation, export, and resolution.
- **Missing/risks:** the backend alert-to-case endpoint has **no frontend caller** despite UI copy promising it (`frontend/src/RiskIntelligence.jsx:209-216`); no mandatory transition rules or second-person approval; no original-file attachment workflow.

### 8. AI Investigation Assistant — **Partially verified**

- **Frontend:** `view: 'assistant'` → `Assistant` (`frontend/src/main.jsx:3381-3383`; `frontend/src/Assistant.jsx:48-280`): metadata load, ask, document ingest/sync/delete/reindex, retrieval test, answer/citations/SQL evidence display.
- **Backend:** `GET /api/assistant/suggestions|config`, `POST /api/assistant/query`, document ingest/status/list/delete/reindex/retrieval-test (`backend/app/main.py:1707-1908`).
- **Storage:** query history persisted to `investigation_runs`; RAG chunks in a separate store (`rag_documents`, `rag_chunks` — `backend/app/vector_store.py:54-77`).
- **Workflow:** keyword intent scoring → specialist agents → SQL evidence + RAG retrieval → reporting prompt → Ollama if available → answer with citations/timeline/disclaimer.
- **Validation/error handling:** question required and ≤1000 chars (`main.py:1840-1844`); LLM unavailable/empty response produces evidence-only fallback (`backend/app/llm.py:57-63,84-92,158-195`); persistence failures are silently swallowed (`main.py:1856-1887`).
- **Auth:** `assistant:use` granted to admin/user/finance analyst/auditor/reviewer, not lender (`backend/app/auth.py:127-188`). RAG retrieval is **not owner-scoped**.
- **Tests:** `backend/tests/test_llm.py:47-181` and `test_end_to_end_deterministic.py:151-189` cover config, availability probe, prompt construction, failure fallbacks, retrieval metadata, and an end-to-end query with `history_id`.
- **Blocking defect:** `frontend/src/Assistant.jsx:50-55` calls exact `GET /api/documents`, but no matching route-policy entry exists (`backend/app/auth.py:487-493`); unmatched protected routes fail closed with 403 (`auth.py:539-544`). The whole metadata `Promise.all` rejects, so the assistant page can fail to load config/documents/suggestions.
- **Missing/risks:** no owner-scoped RAG index, no citation post-validation, no answer feedback/correction flow, no UI link to persisted `history_id`, no live Ollama validation.

### 9. Multi-Agent Investigation System — **Partially verified**

- **Frontend:** `AgentsPage` (`frontend/src/main.jsx:3613-3751`) exposes `monthly_analysis`, `investigation`, `reconciliation_review`; a separate Invoice Agent Center exists (`main.jsx:2141-2155,3402`).
- **Backend:** `POST /api/agents/run`, `GET /api/agents/runs`, `GET /api/agents/runs/{id}` (`backend/app/main.py:1372-1443`), all requiring `assistant:use`.
- **Graphs:** generic orchestrator intake→reconciliation→anomaly→analysis→evidence→synthesis→report (`backend/app/agents/orchestrator.py:1275-1294`) and invoice workflow intake→extraction→duplicate→PO→GR→payment→discrepancy→evidence→financial→report (`backend/app/agents/invoice_workflow.py:634-657`).
- **Tables:** `investigation_runs`, plus `InvoiceAuditRun`/`InvoiceAuditEvidence` for invoice workflows.
- **Validation/error handling:** deterministic input validation is recorded as a failed run (HTTP 200 with failed internal status) rather than HTTP 422 (`orchestrator.py:278-286`); node failures are captured with downstream skips (`210-268`); LLM fallback/retry metadata is recorded (`1106-1161`). `POST /api/agents/run` returns raw `str(exc)` as HTTP 500 detail (`main.py:1372-1385`).
- **Tests:** `backend/tests/test_agent_workflow.py:44-304` and `agents/test_orchestrator.py:10-30` assert timeline/event counts, persisted detail equality, truthful empty state, failed-run recording, downstream skipping, LLM retry, and reconciliation-review evidence.
- **Missing/risks:** human-review flags are advisory only — no approval/rejection endpoint exists; raw exception strings can leak; persistence failures are swallowed; UI does not expose `case_id`, `focus_transaction_id`, or `invoice_id` although the schema supports them; no run cancellation/retry-from-node.

### 10. Agent Execution History — **Partially verified**

- **Frontend:** history embedded in `AgentsPage` (`frontend/src/main.jsx:3729-3750`) and Invoice Agent Center (`2154-2155`).
- **Backend:** `GET /api/agents/runs` and `/runs/{id}` (`backend/app/main.py:1388-1443`), returning stored timeline, events, evidence, errors, and input/output state.
- **Table:** shared `investigation_runs` (`backend/app/models.py:194-218`), written by orchestrator, invoice workflow, and assistant query.
- **Validation:** `run_id` typed int; missing run returns 404; `limit` is capped above at 200 but **not lower-bounded** — `?limit=-1` yields effectively unlimited rows (`main.py:1388-1413`).
- **Tests:** `test_agent_workflow.py:117-134,173-175` verify persisted list/detail equality and failed-run errors; `test_end_to_end_deterministic.py:189` checks a non-null `history_id`. No direct tests of negative `limit`, owner filtering, invoice-run visibility, or assistant-history UI linkage.
- **Missing/risks:** no pagination/filtering/sorting/export/deletion/retention; no redaction of stored prompts or error strings; mixed run semantics (orchestrator, invoice, assistant) distinguished only by `task_type`; legacy ownerless rows remain broadly visible by design (`backend/app/ownership.py:7-11,82-94`).

### 11. Knowledge Base / RAG — **Partially verified**

- **Frontend:** document upload, transaction sync, delete, reindex, retrieval test, and status display are implemented in `frontend/src/Assistant.jsx:48-167`; the assistant view is routable for general users (`frontend/src/main.jsx:188-194`).
- **Backend:** `POST /api/documents/ingest`, `POST /api/documents/sync-transactions`, `GET /api/documents`, `GET /api/documents/status`, `GET /api/documents/{id}/status`, `DELETE /api/documents/{id}`, `POST /api/documents/reindex`, `POST /api/documents/retrieval-test` (`backend/app/main.py:1707-1806`); plus assistant suggestions/config/query (`1812-1845`).
- **Storage:** separate RAG SQLite store with `rag_documents` and `rag_chunks` (`backend/app/vector_store.py:54-77`), TF-IDF index pickled to local storage (`202-230`), global singleton (`backend/app/rag.py:23`). Chunks have **no owner metadata**.
- **Workflow:** suffix-based file routing → chunk/embedding → SQLite + index persistence → retrieval filtered only by document/type/transaction fields (`backend/app/rag.py:211-222,289-290`; `backend/app/vector_store.py:232-297`) → assistant graph uses chunks as evidence.
- **Validation/error handling:** at least one file, max 20 files, empty-file checks, 5 MB per-file cap, per-file errors returned in batch (`main.py:1712-1729`); retrieval query required and top-k errors caught (`1794-1806`). No MIME/content sniffing.
- **Auth/scoping:** writes require `documents:write`; list/status/retrieval-test require `documents:read` (`auth.py:487-490`). Lender has both permissions (`auth.py:153-157`). Retrieval is **global**, so one user can receive another user’s document excerpts/citations.
- **Tests:** `test_management_api.py:82-126` verifies ingest/status/retrieval/delete/reindex; `test_finaudit_platform.py:125-150` verifies metadata and context retrieval; `test_llm.py:47-181` verifies Ollama config, prompt grounding, and fallback behavior.
- **Risks:** cross-user document leakage; pickle-based index load can execute arbitrary code if the local file is replaceable; storage path `data_dir` is exposed in stats/introspection (`vector_store.py:302-312`); suffix-only validation.
- **Missing:** owner-aware chunks and retrieval filters, safe non-pickle index format, content/MIME validation, per-document access control.

### 12. Invoice Audit — **Partially verified**

- **Frontend:** invoice management, purchase orders, goods receipts, invoice matching, discrepancies, vendor analytics, invoice agent center, and CSV/PDF export buttons (`frontend/src/main.jsx:1574,1734,1823,1894,1954,2028,2073,2125-2126,2161`).
- **Backend:** 30 endpoints across vendors, POs, GRNs, invoices, payments, audits, discrepancies, and analytics (`backend/app/invoice_api.py:607-1370`); all on `invoice_router` with router-level RBAC (`backend/app/main.py:101`) and policy entries at `backend/app/auth.py:495-507`.
- **Tables:** vendors, purchase orders/items, goods receipts/items, invoices/items/documents/matches/discrepancies/payments, invoice audit runs/evidence, review actions (`backend/app/models.py:302-568`).
- **Workflow:** upload/extract → deterministic rules (duplicate, PO, GRN, payment, vendor/currency, item/quantity/price/tax/total checks) → discrepancies and exposure → human review → CSV/PDF export. The 10-node agent workflow always ends at `needs_human_review` (`backend/app/agents/invoice_workflow.py:634-657,714-718`).
- **Validation/error handling:** max 20 files; 15 MB and suffix checks; integer minor units with `ROUND_HALF_UP`; missing currency reported as `needs_review`. However, document storage occurs before size validation (`invoice_api.py:893-897`), and no MIME/content sniffing exists.
- **Auth/scoping:** invoice writes require `invoice:write`, reads `invoice:read`, discrepancy reviews `invoice:review`; reviewer identity comes from authenticated user (`test_auth_rbac.py:181-211`). Lender has invoice access but not company financial data (`test_auth_rbac.py:513-521`).
- **Tests:** `backend/tests/test_invoice_audit.py:100-472` is the strongest domain suite: extraction, duplicates, PO/GRN/payment rules, exposure, review persistence, exports, analytics, workflow persistence, deterministic operation without LLM, multi-invoice false-duplicate guard, missing currency, GRN-item validation, unpaid-balance exposure, and mandatory human review.
- **Financial/validation risks:** PO tolerance defaults to zero (`invoice_audit.py:277`) and the workflow uses that default; duplicate vendor checks skip null `vendor_id` (`154-157`); document-hash duplicate logic can false-positive (`184-216`); currency defaults to INR in API/extraction (`invoice_api.py:87-91`, `invoice_extraction.py:114-116`); analytics sum across currencies (`invoice_audit.py:1044-1133`).
- **Missing:** MIME validation, pre-storage size enforcement, configurable tolerance in workflow, non-INR default handling, and currency-normalized analytics.

### 13. Reports and Exports — **Partially verified**

- **Frontend:** Reports page (`frontend/src/main.jsx:3757-3787`) exposes summary, full PDF/CSV, transactions CSV, risk alerts CSV, latest audit/reconciliation exports; analytics CSV and invoice CSV/PDF exports are also wired (`3462`, `2125-2126`).
- **Backend:** `GET /api/reports/summary`, `GET /api/reports/full.csv`, `GET /api/reports/full.pdf`, plus module-specific exports (`backend/app/main.py:1979-2168`; `backend/app/risk_api.py:188`; `backend/app/invoice_api.py:1197,1238`). Report endpoints require `reports:read` and `data:export` (`backend/app/auth.py:494`).
- **Sources:** transactions, risk alerts, reconciliation runs, investigation runs, invoice audit runs (`backend/app/models.py:40,152,194,532,608`). Full CSV reads up to 10,000 rows per table (`main.py:2039,2046,2055`); PDF hard-codes top 30 alerts/10 investigations/20 activity rows (`2123,2136,2142-2164`).
- **Workflow/provenance:** summary includes generated timestamp, filters, provenance, calculations, financial summary, reconciliation, alerts, investigations, AI explanations, and disclaimer (`main.py:1986-2031`); full CSV inserts provenance rows for generation time/filters/calculations (`2073-2111`).
- **Validation/error handling:** no user-supplied filters for full reports; defensive AI explanation parsing (`1970-1976`); downloads use authenticated `apiFetch` helper (`frontend/src/api.js:158-169`).
- **Tests:** `test_exports_and_mapping.py:148-205` verifies transaction/risk export filters, provenance, and `generated_at`; `test_finaudit_platform.py:183-227` verifies report summary and OpenAPI exposure; `test_invoice_audit.py:326-338` verifies CSV/PDF content type and PDF magic header. These do not validate complete row counts, PDF pages, or rendered content.
- **Risks:** full CSV silently caps at 10,000 rows per table; no date/owner/entity/currency/severity filters; mixed-currency totals can be misleading; minimal PDF writer uses latin-1/ascii conversion and can corrupt non-Latin text (`backend/app/pdf_utils.py:11-66`).
- **Missing:** complete export fidelity tests, filterable reports, pagination/manifest for large exports, full financial-statement PDF rendering.

### 14. Settings and Security — **Partially verified**

- **Frontend:** auth screen, admin users page, role dashboards, settings/status page, client-side view allowlists, token storage in `localStorage`, bearer injection, and 401 handling (`frontend/src/main.jsx:177-210,2220,2479,2614,2648,3042-3048,4184-4248`; `frontend/src/api.js:19-22,43-57,106-110,118`).
- **Backend endpoints:** public login/demo status/demo session/register; authenticated logout/me/roles; admin-only users list/provision/approve/patch (`backend/app/auth.py:718-971`); protected system health and public fast health (`backend/app/main.py:111-186`).
- **Tables:** `users`, `revoked_tokens`, plus ownership columns across 16 root tables (`backend/app/models.py:710-745`; `backend/app/ownership.py:69-133`).
- **Controls implemented:** bcrypt hashing; JWT issuance with expiry/JTI revocation; login errors hide username vs password; pending/inactive accounts get explicit 403; registration cannot set role or request admin; approval cannot implicitly grant admin; one-time admin bootstrap; logout revokes tokens; fail-closed route policy (`auth.py:523-544`); demo sessions blocked outside local mode (`auth.py:54-72,749-764`); health endpoint reports real status without secrets.
- **Tests:** `backend/tests/test_auth_rbac.py:128-686` (31 tests) covers hashing, login/JWT failure modes, RBAC denials, reviewer approval and actor identity, registration/approval/provision/disable, role escalation guards, lender boundaries, cross-account isolation, owner stamping, bootstrap idempotency, and demo-mode gating including production refusal (`659-665`). `test_management_api.py:46-77` covers health probes and secret-free output.
- **Critical security gaps:** JWT signing secret falls back to a hardcoded development value when `FINAUDIT_JWT_SECRET` is missing (`backend/app/auth.py:46-48,108-116`), allowing JWT forgery in a misconfigured deployment. Local/untracked env files enable demo behavior and `frontend/.env.example:8` ships `VITE_LOCAL_DEMO_MODE=true` while `backend/.env.example:35` ships `false`.
- **Other gaps:** no org/tenant column; `NULL owner_user_id` rows are globally visible; frontend role gates are cosmetic; localStorage tokens are XSS-exposed; no exhaustive role×route test; admin bypasses owner filtering; globally unique vendor codes can leak cross-tenant existence.
- **Demo-mode verification:** backend code refuses demo sessions in `production`/`prod`/`staging` and tests assert this (`test_auth_rbac.py:659-665`). Therefore demo mode is verified blocked at the backend in those environments, but configuration drift and local env defaults remain deployment hazards.
- **Missing:** editable settings UI (page is health/status only), secrets management validation at startup, full RBAC matrix tests, migration execution/backup tests, and a frontend security boundary (backend must remain authoritative).


---

## 5. Severity-ranked findings

### 5.1 Finding classification matrix

The bullets in §5.2–§5.5 preserve the original audit wording. The matrix below classifies **every** finding against the required fields. Verification status means: **Verified** = current source inspected and, where noted, covered by the executed test suite; **Partially verified** = code-level evidence is strong but the runtime/business outcome was not executed (no browser, no live LLM, no live cross-user DB session); **Unverified** = claim originates from documentation or snapshots and was not independently confirmed in this audit.

| ID / Severity | Affected file and function / endpoint | Actual problem and business impact | Verification | Recommended fix | Tests needed to confirm |
|---|---|---|---|---|---|
| **C-1 / Critical** | `backend/app/auth.py:46-48,108-116` — token signing/verification helpers; `render.yaml:13-33`, `deploy/.env.production.example:1-45`, `DEPLOYMENT.md:90-94` | Hardcoded development JWT fallback when `FINAUDIT_JWT_SECRET` is absent; the variable is also missing from the Render blueprint, production env template, and deployment secret checklist, so a default deploy signs JWTs with a public dev secret. Forged tokens bypass all authentication and RBAC. | **Verified** (static code and deployment files; no production deploy exercised) | Fail startup/token operations when the secret is missing outside local development; add it to all deployment templates/checklists; require a strong secret and rotate any exposed dev secret. | Test missing/weak secret on startup and token create/verify; test forged-token rejection in staging-like config; deployment config assertion. |
| **C-2 / Critical** | `backend/app/rag.py:23` (global singleton), `backend/app/vector_store.py:54-78,232-330` (`rag_chunks`/`search`/`list_documents`), `backend/app/rag.py:225-256` (`ingest_transactions`), `backend/app/main.py:1792-1806` (`/api/assistant/query`, `/api/documents/retrieval-test`) | RAG corpus has no owner/org metadata and retrieval is global; `ingest_transactions` uses an owner-filtered ORM query but writes into the shared store, so one user’s transactions become globally retrievable. Any role with `documents:read`/`assistant:use` (including lender) can receive another user’s excerpts and citations — cross-user/cross-tenant disclosure. | **Partially verified** (code path inspected; no live cross-user HTTP test executed) | Add owner/org columns to `rag_documents`/`rag_chunks`; stamp on ingest and filter every search/list/status/retrieval-test path; restrict retrieval-test permissions. | Negative cross-user tests for ingest, transaction sync, list, status, retrieval-test, and assistant query; migration test for new owner columns. |
| **H-1 / High** | `frontend/src/Assistant.jsx:48-62`; `backend/app/auth.py:459-520,539-544` (`ROUTE_PERMISSIONS`, 55 entries); `backend/app/main.py:1741-1748` (`GET /api/documents`) | Exact `GET /api/documents` is the only non-public decorated route absent from the 55-entry policy; a valid token with no match returns 403 for **every role, including admin**. The all-or-nothing `Promise.all` then rejects and Assistant loses config/documents/suggestions. | **Partially verified** (static route match of 105 routes + code-path inference; no browser execution) | Add the exact route rule with `documents:read`; add a catch that preserves independently successful metadata calls. | Authorized-role API test for the exact route (including admin); full role×route matrix test; frontend runtime test of Assistant metadata load and error state. |
| **H-2 / High** | `backend/app/investigation_api.py:36-44,464-508` (`PATCH /api/investigations/{case_ref}`); `backend/app/agents/orchestrator.py:866,993,1251-1258`; `backend/app/agents/invoice_workflow.py:714-718` | Any of the five roles holding `investigation:write` can set `resolved`/`dismissed`, triggering server-side `resolved_at`; there is no second-person/admin gate, no creator≠resolver check, and no transition state machine. Agent review flags are advisory with no approval/rejection endpoint — a single writer can close audited findings. Client-supplied `actor` is ignored (positive). | **Verified** (code + RBAC tests confirm actor identity and cross-account isolation but no approval gate) | Add reviewer/admin-only approval endpoint with approver identity, timestamp, rationale, transition rules, and persisted events; block closure until approved. | Tests for unauthorized resolve, self-approval denial, illegal transitions, spoofed actor rejection, and event persistence. |
| **H-3 / High** | `backend/app/llm.py:109-144`; `backend/app/agents/assistant_graph.py:624-678`; `backend/app/rag.py:315-322`; `backend/app/investigation_api.py:573-594` | Grounding is instruction-only; untrusted text is interpolated into prompts, static policy gets a fixed `0.999` score, and citations are assembled from metadata after generation without claim validation — fabricated or injected narratives can look cited. | **Partially verified** (prompt/assembly code inspected; no live model or adversarial eval) | Add untrusted-content boundaries, instruction filtering, structured output schema, and post-generation attribution checks; fall back to evidence-only when attribution fails. | Prompt-injection eval set, citation-attribution tests, schema-conformance tests, live-model smoke test. |
| **H-4 / High** | `backend/app/main.py:1195-1211,823-829` (`/api/reconciliation/manual-link`, result listing); `backend/app/dashboard.py:129-145` | Manual link inserts a new `matched` row but sets only `review_status='linked'` (`main.py:1204-1205`) while `status` stays in its original state, so source rows are counted both as matched and as exceptions, corrupting unmatched totals and match rate. | **Verified** (code inspection + existing reconciliation tests show status semantics) | Mark/settle source rows in the same transaction or exclude linked rows from exception aggregates; add invariant checks. | Regression test asserting matched + unmatched + exceptions equal the original row count after a manual link. |
| **H-5 / High** | `backend/app/reconciliation.py:143-175,450,486-496,698`; `frontend/src/main.jsx:1308-1310` | Amount tolerance is an unlabelled absolute currency value, is ignored (`0.0`) in the no-reference fallback, and direction conflicts are mislabelled `amount_mismatch` — users cannot predict or trust matching. | **Verified** (code + `test_reconciliation.py` tolerance regressions) | Label units, decide relative vs absolute semantics, apply configured tolerance consistently, and reclassify direction conflicts. | Parametrized tolerance tests for reference and no-reference paths; UI label contract test. |
| **H-6 / High** | `backend/app/risk_engine.py:728,742,788,1172-1215`; `backend/app/invoice_audit.py:1044-1133,1061-1069`; `backend/app/dashboard.py:91-104` | Risk overview counts all historical alerts; duplicate-invoice exposure sums whole groups; vendor/invoice analytics mix currencies and ignore review status; dashboard includes duplicates/future-dated rows — KPIs overstate exposure and cannot be reconciled. | **Verified** (code-level; no live DB reconciliation run performed) | Scope metrics to the current run, use incremental duplicate exposure, normalize/group by currency, honour review status, and define as-of/period closure. | Golden-fixture tests for each aggregate across reruns, currencies, and dismissed duplicates. |
| **H-7 / High** | `backend/app/invoice_api.py:893-897`; `backend/app/invoice_extraction.py:21,343-353`; `backend/app/rag.py:211-222` | Files are persisted before size/suffix validation and no MIME/content sniffing exists; rejected/oversized files remain stored and hostile content can reach parsers. | **Verified** (static code) | Validate size/extension/MIME before writing; stream to temporary storage; delete rejected files; add parser hardening. | Tests asserting no file row/bytes retained after rejection and that spoofed MIME is rejected. |
| **H-8 / High** | `backend/app/ownership.py:69-94`; `backend/app/models.py:308`; `backend/app/invoice_api.py:613-614` | No `org_id`; `NULL` owner rows are globally visible, admins bypass filters, child tables lack owner columns, and globally unique `vendor_code` leaks code existence via 409 — incomplete tenant isolation. | **Verified** (code + ownership/auth tests) | Introduce tenant/org scoping, non-null owner policy for new rows, owner columns on child tables, and per-tenant uniqueness. | Cross-tenant tests for every owner-aware entity and for vendor-code collision responses. |
| **H-9 / High** | `backend/app/vector_store.py:202-230` | RAG TF-IDF index is loaded with `pickle`; if the local file is replaceable, loading executes arbitrary code. | **Verified** (static code; exploit not executed) | Replace pickle with a validated safe format and integrity checks; restrict file permissions. | Test that a tampered index file is rejected without code execution. |
| **H-10 / High** | `frontend/scripts/verify-navigation.mjs:13-33`; `frontend/package.json:6-11`; missing `.github/`; `backend/tests/test_auth_rbac.py:572-604` (`ensure_database_schema`/`has_pending_schema_changes`/`backup_sqlite_database` all mocked at `:580-582`) | No frontend runtime tests or CI; navigation check is regex-only; migration helpers are only mocked and rollback is untested; agent-history/assistant-config and full export content are untested — 196 passing backend cases do not prove UI, migration, or deployment correctness. | **Verified** (inventory + executed baseline commands + static route match) | Add browser/component tests, a full 55-entry role×route matrix, real migration/backup/rollback tests, export fidelity tests, and CI gates. | New test suites must pass in CI on every PR; record results. |
| **H-11 / High** | `backend/app/csv_normalizer.py:529-555`; `backend/tests/test_csv_normalizer.py:321,336`; `DATE_PARSING_CORRECTION_REPORT.md:1-4,21-29,48-53` | ISO parser bug is fixed but authoritative source `transactions (5).csv` is absent; 132 ambiguous rows and 28 inversions/7 heuristic swaps remain unresolved — historical dates may be silently wrong with no verified mapping. | **Partially verified** (fix + regressions verified; historical corruption not measurable without source) | Obtain the source file, produce an approved row-level mapping, backfill only after approval; never apply heuristic swaps. | Fixture tests using the original CSV; row-level diff report reviewed and signed off before DB change. |
| **H-12 / High** | `frontend/.env.example:5-8` (sets `VITE_LOCAL_DEMO_MODE=true` while saying "disabled by default"), `backend/.env.example:30-37`, root `.env.example:25-28`, `deploy/.env.production.example:42-43`, `backend/app/main.py:178-184`, `backend/app/auth.py:61-72,315-350,743-764` | Frontend example enables demo navigation by default; production template claims "No authentication is implemented" and omits JWT secret, environment, and demo flags; health hardcodes `authentication_enabled: True` even in demo mode, without reporting `demo_mode_enabled()`. Backend correctly refuses demo sessions in production/staging, and demo principals get full admin RBAC locally. | **Verified** (config files + demo-gating tests at `test_auth_rbac.py:616-684`) | Align all examples to false, add JWT secret/env/demo flags to deployment templates, report demo state in health, add startup config checks, and correct documentation. | Config-consistency check in CI; production/staging demo-session refusal and health-output tests. |
| **M-1 / Medium** | `backend/app/main.py:1032-1037` (manual confirmation) | Confirmation flips exceptions to `matched` without revalidating amounts/directions and keeps contradictory “not confirmed” text, misleading reviewers. | **Verified** (code) | Revalidate before confirmation and regenerate explanation from the final state. | Test that a conflicting amount/direction cannot be confirmed and that text matches status. |
| **M-2 / Medium** | `backend/app/reconciliation.py:209,330-348,570-586` | Duplicate detection is intra-file/exact-only and the `duplicates` result list is never appended; genuine cross-file/near duplicates are missed. | **Verified** (code + tests show reachable paths) | Add cross-file candidate detection with human review and populate the result list. | Tests for cross-file and near-duplicate candidates and result serialization. |
| **M-3 / Medium** | `backend/app/dashboard.py:139-145` | Match rate divides by the average of two source counts; with asymmetric sides the percentage is technically correct but misleading. | **Verified** (code + exact-match-rate test) | Define and document the denominator; show both sides and matched count. | Test with asymmetric record counts and assert documented denominator. |
| **M-4 / Medium** | `backend/app/dashboard.py:23-29,95-104`; `backend/app/analytics.py:47-57,74-144` | Dashboard uses float money accumulation while analytics uses `Decimal`/`ROUND_HALF_UP`, so surfaces can disagree by cents. | **Verified** (code) | Use integer minor units or `Decimal` everywhere; single shared money helper. | Cross-surface equality tests on the same fixtures. |
| **M-5 / Medium** | `backend/app/csv_normalizer.py:529-555` | Non-ISO dates use `dayfirst=True`; `03/04/2025` silently becomes 3 April. | **Verified** (code + parser tests) | Require explicit date format per import or reject ambiguous dates with a warning/preview. | Tests for ambiguous date rejection and explicit-format imports. |
| **M-6 / Medium** | `backend/app/main.py:1573,1588,2039,2055` | Transaction/CSV exports silently cap at 10,000/50,000 rows with no filters or manifest, so exported totals may not reconcile with summaries. | **Verified** (code + export tests check substrings only) | Add filters, pagination/streaming, and an explicit truncation manifest. | Export tests with >10k rows asserting manifest and complete row counts. |
| **M-7 / Medium** | `backend/app/geo_analytics.py:519,604,707,759-765,813-824` | Aggregate panels skip unlocated rows while summary exposure includes them; totals diverge across panels. | **Verified** (code + geo tests) | Use one consistent population and label excluded rows. | Cross-panel consistency tests with unlocated rows. |
| **M-8 / Medium** | `backend/app/geo_analytics.py:409-455,859-861`; `backend/app/geocoding.py:202-254` | GET requests can contact Nominatim and write `geocode_cache`, breaking GET idempotency and creating third-party egress. | **Verified** (code; real Nominatim mocked in tests) | Resolve geocoding asynchronously or behind an explicit action; keep GET read-only. | Test that GET performs no network call or cache write when disabled. |
| **M-9 / Medium** | `backend/app/risk_engine.py:258-280` | Snapshots are selected by latest `audit_run_id` per `source_file`; reused filenames can hide older periods. | **Verified** (code) | Select by explicit run identifier/period and expose the selected run in provenance. | Tests with reused filenames and distinct periods. |
| **M-10 / Medium** | `backend/app/risk_engine.py:1179-1184,1208,1432-1447` | Invalid trend dates are silently ignored; `suspicious_vendors` mixes `id:<id>` and raw names, breaking aggregation. | **Verified** (code) | Return validation errors for bad dates; use stable vendor keys with display labels. | Tests for invalid date response and vendor-key normalization. |
| **M-11 / Medium** | `backend/app/main.py:1372-1385,1388-1443`; `backend/app/models.py:198` (`InvestigationRun.owner_user_id`); `backend/app/ownership.py:69-94`; `backend/tests/conftest.py:26-42`; `backend/tests/test_agent_workflow.py:117-134` | `limit=-1` compiles to `LIMIT -1` (unlimited on SQLite, may error on production PostgreSQL), bypassing the 200 cap; detail has no explicit owner filter but is scoped by global ORM events, which admins bypass and legacy `NULL`-owner rows escape. Existing history tests run under an autouse admin override, so ownership is **not exercised**; raw `str(exc)` is returned as HTTP 500 detail. | **Verified** (code + test-fixture inspection; negative-limit path untested) | Lower-bound and validate limit, add explicit owner checks and pagination/retention, and return generic error IDs. | Negative/zero/oversized limit tests on SQLite and PostgreSQL; cross-user list/detail 404 tests without the admin override; sanitized error-body test. |
| **M-12 / Medium** | `backend/app/agents/orchestrator.py:1332-1375`; `backend/app/agents/invoice_workflow.py:720-778`; `backend/app/main.py:1856-1887` | Persistence failures are swallowed, producing success responses with no durable history — weak audit trail. | **Verified** (code) | Surface persistence errors, retry, and fail the request when history write is mandatory. | Tests that a failed history write changes the API response. |
| **M-13 / Medium** | `backend/app/auth.py:459-566`; `backend/tests/test_auth_rbac.py` | No exhaustive role×route RBAC matrix and no module-specific cross-account isolation tests for most modules. | **Verified** (test inventory) | Generate table-driven tests from the route policy and add per-module isolation tests. | Matrix test must enumerate every role and route with expected allow/deny. |
| **M-14 / Medium** | `README.md`, `DEPLOYMENT.md`, `FINAL_DELIVERABLES.md`, `RISK_INTELLIGENCE_COMPLETION.md`, older audit docs | Documentation claims no auth, Groq instead of Ollama, stale test counts, and an already-fixed reconciliation defect. | **Verified** (document comparison) | Update all stale claims to current code and test reality. | Doc-review checklist in CI/release process. |
| **M-15 / Medium** | `backend/app/migrations.py:45-57,68-140,143-191`; `backend/tests/test_auth_rbac.py:572-604`; `docs/AUTH_MIGRATION_BACKUP_ROLLBACK.md:32-50,91-116`; `backend/app/main.py:66-68` | Additive migrations only; `NOT NULL` without default is downgraded to nullable. `has_pending_schema_changes` detects missing tables/columns but **not missing indexes**, so index-only changes get no pre-backup. Backups are skipped for non-SQLite/`:memory:`/missing/zero-byte DBs. All three helpers are mocked in tests; rollback is manual-only and the documented restore path (`.db.bak.<timestamp>`) does not match what the code creates (`backups/…`). Docs reference a non-existent `investigation_cases.created_by_user_id` column. A failed migration can leave an unrecoverable schema. | **Partially verified** (code and docs inspected; no migration/backup/rollback executed in this audit) | Include index detection in pending-change checks, make backup mandatory before any change on SQLite, align docs with actual backup naming, add an automated rollback runbook, and test apply/failure/restore on a copy. | Tests on a temp SQLite DB asserting ALTER TABLE, index creation, backup file existence, pending-change detection for index-only diffs, and rollback restoration; PostgreSQL path documented. |
| **M-16 / Medium** | `backend/requirements.txt:1-23`; `backend/Dockerfile:6-33`; `frontend/package.json:13-20`; `frontend/package-lock.json:7-22`; `backend/app/main.py:71-94,150-151`; `render.yaml:12-33` | Backend dependencies are range-pinned/unpinned with no lockfile or hashes; Docker installs ranges and runs as root with 1 worker; 6 frontend packages declare `"latest"` (only `npm ci` is reproducible); protected health endpoint can leak DB exception text; CORS uses credentials with permissive method/header defaults. No `.github/` CI gate. Supply-chain drift and configuration errors can reach production unnoticed. | **Verified** (static inspection; no dependency audit or network scan executed) | Pin/lock backend dependencies with hashes; use `npm ci`; run container as non-root; sanitize health errors; restrict CORS methods/headers; add CI security and test gates. | Build from lockfile/hashes in CI, container user assertion, health-error sanitization test, CORS contract test. |
| **M-17 / Medium** | `backend/app/main.py:824` (`/api/reconciliations/{id}` result serialization) | `float(r.bank_amount or r.ledger_amount or 0.0)` treats a legitimate `0.0` bank amount as falsy and substitutes the ledger amount, so zero-value bank rows can display the wrong amount and distort reconciliation evidence. | **Verified** (code; no dedicated test) | Use explicit `None` checks instead of truthiness (`r.bank_amount if r.bank_amount is not None else ...`); preserve zero as a valid amount. | Test with zero bank amount and non-zero ledger amount asserting the bank value is returned. |
| **M-18 / Medium** | `backend/app/main.py:800-834` (`_refresh_reconciliation_run`) | Refresh recomputes `unreconciled_value` but never updates `run.difference`, so the run-level difference can remain stale after reviews/manual links while detail rows change. | **Verified** (code; no staleness test) | Recompute every run-level aggregate in one helper from the current result set. | Test that all run metrics change consistently after a review or manual link. |
| **M-19 / Medium** | `backend/app/main.py:399,961,996-999,1120-1123,1914-1917` (audit/reconciliation create, review, confirm/manual-link) | Mutating endpoints accept raw `dict` payloads with selective manual validation, and create-run/audit `db.commit()` calls have no try/except or rollback (unlike the review path at `1110-1115`). Malformed input or a failed commit can leave partial state without a clear error. | **Verified** (code) | Introduce Pydantic request schemas and wrap all multi-row writes in transactions with rollback and typed error responses. | Schema-validation tests for malformed payloads and forced-commit-failure tests asserting rollback. |
| **M-20 / Medium** | `frontend/src/main.jsx:281-286,290-304`; `frontend/src/Dashboard.jsx:20-21`; `frontend/src/Transactions.jsx:15-16` | Currency is hardcoded to INR in several display/formatting locations even though the backend accepts other currencies; `fmtMinor` can accept a currency but callers do not pass it, so non-INR data is mislabelled. | **Verified** (code + static nav contract) | Pass the transaction/invoice currency through formatting helpers or derive it from data; show an explicit unknown-currency state. | Unit tests for formatter with INR/USD/unknown currency and a UI contract test that no hardcoded INR remains in dynamic displays. |
| **L-1 / Low** | `frontend/src/main.jsx:368-407,951`; `backend/app/main.py:1007,1128` | Dead `PlaceholderPage`/`OverviewPage` and decorative reviewer/actor fields that the backend ignores confuse maintainers and users. | **Verified** (code) | Remove dead components or wire them; remove or bind decorative fields. | Lint/dead-code check and UI contract test. |
| **L-2 / Low** | `backend/app/main.py:1860,1878-1880` | Assistant history timestamps use placeholder values and `duration_ms=0`, weakening timeline credibility. | **Verified** (code) | Persist real timestamps/durations or omit the fields. | Test that recorded values match the actual run timing. |
| **L-3 / Low** | `backend/app/vector_store.py:302-312` | RAG stats expose the storage `data_dir` path — minor information disclosure. | **Verified** (code) | Omit or redact filesystem paths in API responses. | Test that stats contain no absolute path. |
| **L-4 / Low** | `backend/app/geo_analytics.py:577-585,677-701`; `frontend/src/GeographicRiskIntelligence.jsx:589-593` | Unweighted country centroids, volume-only “unusual location”, and network-dependent OSM tiles limit accuracy/offline use. | **Verified** (code) | Use weighted centroids, define location-baseline rules, add offline tile fallback. | Tests for weighted centroids and offline map rendering. |
| **L-5 / Low** | `backend/app/pdf_utils.py:11-66` | Minimal PDF writer converts text via latin-1/ascii and can corrupt non-Latin content. | **Verified** (code) | Use a Unicode-capable PDF renderer or embed fonts. | Tests rendering non-Latin text and validating extracted content. |
| **L-6 / Low** | `frontend/src/main.jsx:4184-4248` | Settings page is a health/status viewer, not editable settings. | **Verified** (code + nav contract) | Add real, permission-gated settings controls or rename the page. | UI test for settings controls and persistence. |

**Totals: Critical 2, High 12, Medium 20, Low 6 — 40 findings.** No finding is classified `Unverified`; items depending on live browser/LLM/cross-user/migration sessions are explicitly marked `Partially verified`.

### 5.2 Critical

- **C-1 — Hardcoded JWT development fallback.** If `FINAUDIT_JWT_SECRET` is missing, token creation/verification falls back to a hardcoded development value (`backend/app/auth.py:46-48`, `108-116`). A misconfigured deployment can be compromised by forged JWTs that bypass authentication and RBAC. This must fail closed outside local development.
- **C-2 — Global, ownerless RAG corpus.** `rag` is a global singleton (`backend/app/rag.py:23`), chunks have no owner field (`backend/app/vector_store.py:65-77`), and retrieval filters only document/type/transaction fields (`rag.py:289-290`). Roles with `documents:read`/`assistant:use`, including lenders, can query evidence derived from other users’ documents (`backend/app/auth.py:153-157,479-493`; `main.py:1792-1806`). This is a cross-user/cross-tenant disclosure risk.

### High

- **H-1 — Assistant metadata loading is broken by route-policy mismatch.** `frontend/src/Assistant.jsx:50-55` calls exact `GET /api/documents`, but the permission map has rules only for `/api/documents/...` variants (`backend/app/auth.py:487-493`). Unmatched protected routes fail closed with 403 (`auth.py:539-544`), so the whole `Promise.all` can reject and the Assistant page loses config, documents, and suggestions.
- **H-2 — Human approval is advisory, not enforced.** Any `investigation:write` role can resolve or dismiss a case and set `resolved_at` (`backend/app/investigation_api.py:36-44,464-508`). Agent runs set `require_human_review`/`needs_human_review` flags (`backend/app/agents/orchestrator.py:866,993,1251-1258`; `invoice_workflow.py:714-718`) but no approval/rejection endpoint records a second person’s decision. The UI only displays a warning (`frontend/src/main.jsx:3667`).
- **H-3 — LLM grounding is instruction-only and citations are not post-validated.** Grounding rules are prompt text (`backend/app/llm.py:109-144`), untrusted question/notes/evidence/document text is interpolated into prompts (`investigation_api.py:573-594`, `agents/assistant_graph.py:624-637`), arbitrary structured evidence is assigned a `0.999` retrieval score (`rag.py:315-322`), and citations are assembled after generation (`assistant_graph.py:643-666`). There is no verified answer-attribution check, so fabricated or prompt-injected narratives can still be presented with citations.
- **H-4 — Manual reconciliation link double-counts source rows.** The endpoint creates a new confirmed `matched` row but leaves the two source rows in their original statuses (`backend/app/main.py:1201-1211`, `823-829`). Dashboard summaries then treat the source rows as unmatched/exceptions and the new row as matched (`backend/app/dashboard.py:129-145`), corrupting unmatched totals and match rates.
- **H-5 — Reconciliation tolerance semantics are unsafe/misleading.** Amount tolerance is an absolute currency-unit difference but the UI gives no unit (`frontend/src/main.jsx:1308-1310`; `backend/app/reconciliation.py:143-175`). The no-reference fallback hardcodes `0.0` tolerance, so a 1-cent difference never matches even when the user configured a larger tolerance (`reconciliation.py:698`). Direction conflicts are mislabelled `amount_mismatch` with zero amount difference (`461-467`).
- **H-6 — Material financial aggregates are wrong or misleading.** Risk overview counts/sums all historical alerts rather than the latest run (`backend/app/risk_engine.py:1172-1215`); duplicate-invoice exposure sums the whole duplicate group instead of incremental value (`728,742,788`); vendor and monthly invoice analytics sum mixed currencies (`backend/app/invoice_audit.py:1044-1133`); vendor duplicate KPIs ignore review status (`1061-1069`); dashboard totals include duplicates/future-dated rows with no correction path (`backend/app/dashboard.py:91-104`).
- **H-7 — Upload validation happens after persistence for invoices.** `POST /api/invoices/upload` stores the document before the 15 MB/suffix checks are applied (`backend/app/invoice_api.py:893-897`; `backend/app/invoice_extraction.py:21,343-353`). No MIME/content sniffing exists in RAG or invoice ingestion. Rejected or oversized files can remain persisted.
- **H-8 — Tenant isolation model is incomplete.** There is no `org_id`; `NULL owner_user_id` rows are globally visible by design and admins bypass filtering (`backend/app/ownership.py:69-94`). Many child tables lack owner columns and rely on parent-scoped access patterns. Globally unique `vendor_code` can reveal another tenant’s code through a 409 (`backend/app/models.py:308`, `invoice_api.py:613-614`).
- **H-9 — RAG index persistence uses `pickle`.** Loading `tfidf.pkl` from local storage can execute arbitrary code if an attacker or untrusted process can replace that file (`backend/app/vector_store.py:202-230`). This is a high-severity deployment hygiene issue even though exploitation requires local file control.
- **H-10 — Verification and CI coverage are insufficient.** No frontend runtime/browser tests exist; `verify:navigation` is static only (`frontend/scripts/verify-navigation.mjs:13-33`). There is no CI workflow. Migration helpers are only mocked (`test_auth_rbac.py:580-582`), agent-history and assistant-config endpoints lack direct tests, and export tests do not validate complete content. Passing 196 backend tests cannot substantiate end-to-end UI, browser, or deployment behavior.
- **H-11 — Historical date integrity remains unresolved.** The ISO timestamp parser fix and regressions exist (`backend/app/csv_normalizer.py:529-555`; `test_csv_normalizer.py:321,336`), but the authoritative source `transactions (5).csv` is absent. The 132 ambiguous rows and 28 inversions/7 heuristic swaps are explicitly not corrections (`DATE_PARSING_CORRECTION_REPORT.md:1-4,21-29,48-53`). No heuristic backfill was performed.
- **H-12 — Demo configuration drift is a deployment hazard.** Local/untracked env files enable demo behavior and `frontend/.env.example:8` ships `VITE_LOCAL_DEMO_MODE=true` while `backend/.env.example:35` ships `false`. The backend correctly refuses demo sessions in production/staging (`test_auth_rbac.py:659-665`), but unsafe defaults and stale templates increase misconfiguration risk.

### Medium

- Manual reconciliation confirmation flips exceptions to `matched` without revalidating amounts/directions and preserves contradictory “not confirmed” explanation text (`backend/app/main.py:1032-1037`).
- Reconciliation duplicate detection is intra-file/exact-only and the `duplicates` list is never appended to (`backend/app/reconciliation.py:209,330-348,570-586`).
- Dashboard match rate uses average source record counts; with asymmetric sides, the displayed percentage can be technically correct but misleading (`backend/app/dashboard.py:139-145`).
- Dashboard money math uses float accumulation while analytics uses `Decimal`/`ROUND_HALF_UP`, allowing surface-to-surface divergence (`dashboard.py:23-29,95-104`; `analytics.py:47-57,74-144`).
- Non-ISO dates use `dayfirst=True`, so ambiguous values such as `03/04/2025` are silently interpreted as 3 April (`csv_normalizer.py:529-555`).
- Transaction exports are silently capped at 10,000/50,000 rows and full CSV caps each table at 10,000; there are no report filters or pagination manifests (`main.py:1573,1588,2039,2055`).
- Geographic aggregate panels skip unlocated rows while summary flags/high-risk values include all matched rows, so exposure totals can diverge (`geo_analytics.py:519,604,707` vs `759-765,813-824`).
- GET-side geocoding can contact Nominatim and write `geocode_cache` when enabled, weakening GET idempotency and creating third-party egress (`geo_analytics.py:409-455,859-861`; `geocoding.py:202-254`).
- Risk and geographic snapshots are selected by one latest `audit_run_id` per `source_file`; reused filenames can hide older periods (`risk_engine.py:258-280`).
- Invalid risk trend dates are silently ignored (`risk_engine.py:1432-1447`); `suspicious_vendors` mixes `id:<id>` and raw names (`1179-1184,1208`).
- Agent history `limit=-1` bypasses the intended 200-row cap (`backend/app/main.py:1388-1413`); raw exception strings are returned from `POST /api/agents/run` (`1372-1385`).
- Persistence failures are swallowed in orchestrator, invoice workflow, and assistant query paths, producing success responses with no durable history (`agents/orchestrator.py:1332-1375`; `agents/invoice_workflow.py:720-778`; `main.py:1856-1887`).
- No exhaustive role×route RBAC matrix and no module-specific cross-account isolation tests for dashboard, transactions, reconciliation, risk, geographic, vendor, agent history, RAG, invoice, or exports.
- Documentation is stale: auth claimed absent, Groq claimed instead of Ollama, old test/navigation counts, and a fixed reconciliation H1 still listed as open.

### Low

- Dead/unused UI components (`PlaceholderPage`, `OverviewPage`) and misleading reviewer/actor fields that the backend ignores (`frontend/src/main.jsx:368-407,951`; `backend/app/main.py:1007,1128`).
- Assistant history timestamps use placeholder values and `duration_ms=0` (`backend/app/main.py:1860,1878-1880`).
- Storage path `data_dir` is exposed in RAG stats/introspection (`backend/app/vector_store.py:302-312`).
- Country centroids are unweighted means; “unusual location” is volume-only; OSM tiles require network at runtime (`geo_analytics.py:577-585,677-701`; `GeographicRiskIntelligence.jsx:589-593`).
- Minimal PDF writer uses latin-1/ascii conversion and can corrupt non-Latin content (`backend/app/pdf_utils.py:11-66`).
- Settings page is a health/status viewer, not an editable settings page (`frontend/src/main.jsx:4184-4248`).

---

## 6. Security gaps

### Verified security controls

The following controls are implemented and have direct test evidence:
- bcrypt password hashing and verification (`backend/app/auth.py:128-132`; `test_auth_rbac.py:128`).
- JWT login, missing/invalid/expired token rejection, logout revocation, and wrong-signature rejection (`auth.py:718-740,813-831`; `test_auth_rbac.py:135-169,234-244,489-510`).
- Fail-closed route permission enforcement for protected routers (`auth.py:459-566`; `main.py:98-103`).
- Registration cannot set role or request admin; approval cannot implicitly grant admin; one-time admin bootstrap (`auth.py:86-92,628-687,767-810,901-938`; `test_auth_rbac.py:273-442,572-614`).
- Lender cannot read company financial data; cross-account case isolation and authenticated owner stamping are tested (`test_auth_rbac.py:513-569`).
- Demo mode is disabled by default and refused in `production`/`prod`/`staging`; demo tokens stop working when disabled (`auth.py:54-72,749-764`; `test_auth_rbac.py:616-686`).
- System health reports real status without secrets (`main.py:138-185`; `test_management_api.py:61-77`).

### Open gaps

1. **Secret handling:** hardcoded JWT development fallback (`auth.py:46-48,108-116`) must be removed or made fail-closed. `FINAUDIT_JWT_SECRET` is absent from `render.yaml`, `deploy/.env.production.example`, and the deployment checklist, and there is no startup assertion that production secrets are set.
2. **Cross-user RAG access:** global chunks and retrieval (`rag.py:23,289-290`; `vector_store.py:65-77`) allow document evidence to cross account boundaries. `documents:read` is too broad for retrieval-test, especially for lender.
3. **Tenant model:** no `org_id`; `NULL` owner rows are shared/visible; child tables rely on parent scoping; admins bypass filters; globally unique vendor codes leak existence (`ownership.py:69-94`; `models.py:308`).
4. **Route policy completeness:** exact `GET /api/documents` is the only non-public route missing from the 55-entry `ROUTE_PERMISSIONS` map, so even admins get 403; this demonstrates that fail-closed protection can break valid workflows when a route is omitted (`auth.py:459-520,539-544`). A full role×route matrix test is absent.
5. **Client-side gates:** frontend view allowlists and redirects are navigation conveniences only (`frontend/src/main.jsx:188-210,3042-3048`). Backend authorization is authoritative and must remain so.
6. **Token storage:** bearer tokens in `localStorage` are exposed to any successful XSS (`frontend/src/api.js:19-22,43-57`). Consider secure, HttpOnly cookies or a strict CSP if the threat model includes hostile content.
7. **Unsafe deserialization:** pickle-based RAG index (`vector_store.py:202-230`).
8. **Upload validation:** suffix-only routing/validation with no MIME/content sniffing; invoice document persisted before size validation (`rag.py:211-222`; `invoice_api.py:893-897`; `invoice_extraction.py:343-353`).
9. **Information leakage:** raw exception strings from agent runs (`main.py:1385`), document/assistant handlers (`main.py:151,258,1729,1806`), RAG CSV errors (`rag.py:85-86`), protected health DB errors (`main.py:150-151`), and RAG storage path disclosure (`vector_store.py:302-312`).
10. **Operational hygiene:** no CI, no backend dependency lock/hashes, `"latest"` frontend specs, root container, stale production template that claims no authentication and omits the JWT secret, health output that hardcodes `authentication_enabled: True` even in demo mode (`main.py:178-184`), and demo defaults that drift between frontend/backend examples.
11. **Migration/backup assurance:** helper functions are mocked in tests; index-only schema changes are not detected as pending, backups are skipped for empty/non-SQLite DBs, and rollback is manual and undocumented against the actual backup path (M-15).

**Demo-mode conclusion:** backend enforcement prevents enabling demo sessions in staging/production in code and tests (`auth.py:61-72,743-764`; `test_auth_rbac.py:616-684`). Locally, however, the demo principal is issued `role=admin` and therefore receives full admin RBAC without a DB user (`auth.py:295-350,401-408`); copied example env files can enable demo navigation by default. The remaining risk is configuration drift and deployment misuse, not a demonstrated ability to turn demo mode on in production through the API.

---

## 7. Financial correctness gaps

| Area | Gap | Impact / evidence |
|---|---|---|
| Reconciliation manual link | Source rows retain original status after linkage | Double-counts matched and unmatched/exception rows; distorts dashboard KPIs (`main.py:1201-1211`, `823-829`; `dashboard.py:129-145`). |
| Reconciliation tolerance | Absolute amount tolerance is unlabelled; no-reference fallback hardcodes `0.0` | Users may expect percentage/relative tolerance; small differences never match without references (`reconciliation.py:143-175,450,486-496,698`). |
| Reconciliation status | Direction conflicts labelled `amount_mismatch` | Amount-mismatch counts inflated despite zero amount difference (`reconciliation.py:461-467`; `test_reconciliation.py:392-408`). |
| Reconciliation duplicates | Intra-file, exact-match only; `duplicates` list not populated | Cross-file and near-duplicate payments are not detected (`reconciliation.py:209,330-348,570-586`). |
| Transaction dedupe | `duplicate_key` + `(source_file, source_row)` provenance | Reordered/same-named re-uploads can skip genuine rows or skip repeated genuine payments (`main.py:214-237,333-340`). |
| Transaction correction | No PATCH/PUT/DELETE for imported transactions | Bad rows, categories, and duplicates cannot be corrected through the application; historical integrity depends on upload correctness. |
| Historical dates | ISO parser fixed but source CSV missing; 132 ambiguous rows unresolved | No verified backfill is possible; heuristic correction explicitly rejected (`DATE_PARSING_CORRECTION_REPORT.md`; `csv_normalizer.py:529-555`). Ambiguous non-ISO dates default to day-first. |
| Dashboard totals | All stored transaction rows included; no as-of/period closure; float accumulation | Duplicates, future-dated typos, and unclosed periods inflate totals; float vs `Decimal` can disagree with analytics (`dashboard.py:23-29,91-104,156-166`; `analytics.py:47-57,74-144`). |
| Risk overview | All historical alerts counted/summed | Alert counts, severity distribution, average score, and flagged value inflate across reruns (`risk_engine.py:1172-1215`). |
| Duplicate-invoice exposure | Whole duplicate group counted | Overstates incremental financial exposure relative to duplicate-payment logic (`risk_engine.py:728,742,788` vs `437`). |
| Vendor/invoice analytics | Mixed currencies summed; cached balances trusted; duplicate KPI ignores status | Totals can be numerically meaningless or stale; dismissed duplicates remain counted (`invoice_audit.py:1044-1133,1061-1069`; `models.py:412-417`). |
| Invoice rules | PO tolerance defaults to zero; null-vendor duplicate check skipped; hash duplicate can false-positive | Immaterial variances become discrepancies; duplicate checks can miss or over-flag (`invoice_audit.py:154-157,184-216,277`; `agents/invoice_workflow.py:342`). |
| Exports | Full CSV 10,000 rows/table; transaction export cap; no report filters | Reports can silently omit records; totals shown in summary may not reconcile with capped exports (`main.py:1573,1588,2039,2055`). |
| Reconciliation amount display | Truthiness fallback `bank_amount or ledger_amount or 0.0` (`main.py:824`) | A genuine zero bank amount is replaced by the ledger amount, so evidence rows and downstream exports can show the wrong side's value. |
| Reconciliation run totals | `run.difference` is not recomputed by `_refresh_reconciliation_run` (`main.py:800-834`) | Run-level difference stays stale after result reviews or manual links, so historical run summaries disagree with current detail rows. |
| Geographic exposure | Summary includes unlocated rows; panels exclude them | Flagged/high-risk values differ across summary and aggregate panels (`geo_analytics.py:519,604,707,759-765,813-824`). |

No financial records or historical dates were corrected during this audit.

---

## 8. AI/RAG risks

- **Ollama integration is local/optional by design.** Configuration uses `OLLAMA_HOST`/`OLLAMA_MODEL` with no paid API key (`backend/app/llm.py`, `render.yaml`, `deploy/.env.production.example`). Tests confirm availability probing via `/api/tags`, request shape, failure fallback, empty-response fallback, and evidence-only answers (`test_llm.py:47-181`). Live Ollama behavior was not exercised in this audit.
- **Fallback behavior is generally safe.** When Ollama is unavailable or returns empty/invalid output, the system returns evidence-only answers with source/error metadata (`llm.py:57-63,84-92,158-195`; `investigation_api.py:596-605`). Deterministic invoice and agent results are kept separate from optional LLM narrative (`agents/orchestrator.py:1130-1161,1230-1248`).
- **Prompt injection is not robustly mitigated.** Grounding is instruction text only; questions, case notes, evidence excerpts, RAG chunks, and arbitrary structured evidence are interpolated into prompts (`llm.py:109-144`; `investigation_api.py:573-594`; `agents/assistant_graph.py:624-637`; `rag.py:315-322`). There is no delimiter hardening, untrusted-content role separation, instruction filtering, or tool-call restriction beyond prompt wording.
- **Citations do not prove grounding.** Citation objects are assembled from retrieved source metadata after generation (`assistant_graph.py:643-666`), and the reporting node checks only whether source IDs are present in the evidence list (`668-673`). No post-generation claim/attribution validation exists.
- **RAG leakage amplifies fabrication risk.** Because retrieval is global and unowned (`rag.py:23,289-290`), answers can cite another user’s documents. `ingest_transactions` applies owner filtering on the ORM read but writes the result into the global store (`rag.py:225-256`), so another user’s transaction-derived evidence is retrievable. Those excerpts can also be persisted into shared/ownerless investigation-run history.
- **LangGraph orchestration is real but constrained.** Generic and invoice graphs persist timelines/events/evidence and record node failures (`agents/orchestrator.py:1275-1377`; `agents/invoice_workflow.py:634-778`). However, human-review flags are advisory only, no approval endpoint exists, validation failures return HTTP 200 with failed internal status, and raw exceptions can leak from `POST /api/agents/run`.
- **Unsupported AI actions / overstated confidence.** The assistant returns disclaimers and “human review required” labels, but no workflow prevents an AI explanation from being used to resolve a case or close a discrepancy. Static policies are mixed into retrieval context with a fixed high score (`rag.py:300-322`), which can be mistaken for case-specific evidence.
- **Failure handling gaps outside the LLM.** Assistant and agent persistence failures are swallowed (`main.py:1856-1887`; `agents/orchestrator.py:1332-1375`; `agents/invoice_workflow.py:720-778`), so history can be missing without user-visible warning.
- **Verification gap.** All LLM tests mock, disable, or avoid live inference. There is no evaluation set for hallucination, prompt-injection resistance, citation correctness, or harmful/unsupported recommendations. No browser test was executed in this audit (the 102 navigation checks are regex-only).

---

## 9. Recommended remediation order

### Phase 0 — Immediate security blockers (do first)

1. **Remove the JWT fallback.** Require `FINAUDIT_JWT_SECRET` in all non-local environments; fail startup or all token operations if missing/weak. Add it to `render.yaml`, `deploy/.env.production.example`, and `DEPLOYMENT.md`; add missing-secret tests and rotate any potentially exposed development secret.
2. **Scope RAG to owners/organizations.** Add owner/org metadata to `rag_documents` and `rag_chunks`, filter retrieval and retrieval-test by active data scope, and add negative cross-user tests. Restrict retrieval-test to appropriate permissions.
3. **Replace pickle index persistence.** Use a safe serialized format (e.g., JSON/NumPy arrays with validated schema or a proper vector index format) and reject tampered/incompatible files.
4. **Fix exact `GET /api/documents` route policy** and add a representative test that the Assistant metadata load succeeds for authorized roles.
5. **Align demo configuration.** Set frontend/backend/root examples consistently to false, remove demo values from local deployment copies, and keep the verified production/staging refusal. Add a startup/deployment check for demo flags.

### Phase 1 — Human control and AI safety

6. Add a real approval workflow for case resolution/dismissal and agent conclusions: dedicated reviewer/admin-only endpoint, approver identity, timestamp, rationale, state transition rules, and event persistence.
7. Harden LLM prompts against untrusted content: explicit untrusted-content boundaries, instruction-stripping where feasible, output schemas, and post-generation citation/attribution validation. Fail closed to evidence-only when claims cannot be attributed.
8. Stop treating advisory `needs_human_review` flags as completion controls; block downstream closure actions until approval is recorded.
9. Redact raw exception details in agent APIs and history; cap/lower-bound list limits; add pagination and retention controls.

### Phase 2 — Financial correctness

10. Fix manual reconciliation link so source rows are marked linked/settled in summary logic without double-counting; add regression tests for unmatched/matched totals and dashboard KPIs.
11. Define tolerance semantics explicitly: label currency units in the UI, decide whether relative tolerance is needed, and apply the configured tolerance consistently in the no-reference fallback. Reclassify direction conflicts correctly.
12. Recompute risk overview from the current/labeled run, use incremental duplicate-invoice exposure, normalize vendor keys, and document all-time vs current-run metrics.
13. Normalize currency for vendor, invoice, and report analytics; group by currency or apply an approved FX policy with rates and provenance. Validate cached balances.
14. Move invoice file validation before persistence; add MIME/content sniffing and cleanup of rejected files.
15. Add configurable PO tolerance and review-aware duplicate KPIs.

### Phase 3 — Data integrity and exports

16. Provide a controlled transaction correction/void workflow with audit events; do not allow silent edits of historical records.
17. Resolve the historical date backfill only when the authoritative source is available and a reviewed mapping is approved. Do not apply heuristic swaps.
18. Improve duplicate detection to cross-file/near-duplicate candidates with human review, while preserving provenance.
19. Make export limits explicit, filterable, and paginated; add a manifest when records are omitted. Improve PDF rendering and export fidelity tests.

### Phase 4 — Verification, CI, and documentation

20. Add frontend runtime tests (Vitest/Playwright or equivalent) for routing, API calls, error states, role gating, and key forms/charts.
21. Add a table-driven RBAC test across every role and protected route, plus cross-account isolation tests for every owner-aware module.
22. Test migration helpers directly on a temp SQLite DB: additive columns, index creation, index-only pending-change detection, backup file creation, and failure rollback; align the documented restore path with the code and document the PostgreSQL snapshot path.
23. Add export content-fidelity tests (row counts, column sets, provenance values, PDF content).
24. Add CI that runs backend pytest, frontend build, navigation contract, and new runtime tests on every PR.
25. Update stale documentation: auth exists, Ollama is the provider, current test/navigation counts, reconciliation H1 is fixed, and production environment guidance must not say “no authentication.”

---

## 10. Production-readiness assessment

**Assessment: Not production-ready.**

The application is not a prototype — it has substantial backend logic, persisted workflows, deterministic engines, RBAC, and a passing 196-case backend suite. However, production readiness requires evidence that security boundaries, financial calculations, AI outputs, and user-facing workflows are correct. That evidence is missing or contradicted by found defects.

**Blocking conditions:**
- Critical JWT fallback and global RAG access remain unresolved (C-1, C-2).
- Human approval for case/agent/invoice conclusions is advisory only (H-2).
- Material financial aggregates and reconciliation summaries can be wrong (H-4, H-6).
- Upload validation and unsafe deserialization create deployment risk (H-7, H-9).
- No frontend runtime tests, no CI, and incomplete RBAC/isolation/migration/export tests (H-10).
- Historical date integrity is unresolved and must not be heuristically corrected (H-11).
- Production documentation and environment templates are stale and contradictory.

### 10.1 Readiness by milestone

| Milestone | Decision | Basis |
|---|---|---|
| **Local demo ready** | **Yes, with caveats** | 196 backend tests pass, frontend builds, and local demo sessions are gated to local/dev/test. Demonstrate deterministic flows with the LLM disabled. Caveats: the exact `GET /api/documents` rule breaks Assistant metadata loading for every role, local demo tokens carry full admin RBAC, `frontend/.env.example` enables demo navigation by default, and historical transaction dates remain unresolved. |
| **Internship/interview ready** | **Yes, with disclosure** | The repository shows a substantial, testable full-stack implementation (reconciliation, risk, RAG, agents, RBAC, exports) and honest audit material. Present it as a **prototype with known defects**, not as a finished product; disclose the Critical/High findings and that all 14 modules are only *Partially verified*. No browser evidence should be claimed. |
| **Production ready** | **No** | Critical secret handling and cross-user RAG exposure are unresolved; financial aggregates and reconciliation summaries can be wrong; human approval is advisory; upload/deserialization and deployment hygiene are weak; and there is no CI, browser testing, or tested migration/backup/rollback path. |

### 10.2 Five most important blockers

1. **C-1 — JWT secret fallback and omission from deployment config** (`auth.py:46-48,108-116`; `render.yaml`; `deploy/.env.production.example`): a default deploy can be compromised with forged tokens.
2. **C-2 — Global, ownerless RAG corpus** (`rag.py:23,225-256,289-290`; `vector_store.py:54-78`): cross-user document/transaction evidence disclosure.
3. **H-2 — No enforced human approval** (`investigation_api.py:36-44,464-508`; agent workflows): findings can be closed by a single writer or advisory AI flag.
4. **H-4/H-6/M-17/M-18 — Financial correctness defects** (manual-link double count, all-history risk counts, whole-group duplicate exposure, mixed-currency totals, unlabelled/ignored reconciliation tolerance, zero-amount fallthrough, stale run difference): KPIs and exports cannot be trusted for audit conclusions.
5. **H-10/M-15 — Verification and recovery gaps** (no browser tests/CI, fail-closed route omission, mocked migration/backup tests, untested rollback, unresolved historical dates): no evidence that the deployed system behaves as tested or can be recovered safely.

**What would change the assessment:** complete Phase 0–2 remediations, add the verification described in Phase 4, re-run the full backend suite plus browser/runtime tests in a staging-like environment, and produce evidence that financial totals reconcile across dashboard, exports, risk, vendor, and invoice surfaces. Until then, all modules remain **Partially verified** and the application should not be exposed to untrusted or multi-tenant users.
