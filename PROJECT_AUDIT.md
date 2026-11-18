# FinAudit AI — Project Audit (Phase A: baseline, repairs, and verification)

**Baseline audit date:** 2026-11-18 (environment)  
**Last updated:** 2026-11-18 (Phase A repair/verification pass)  
**Working directory:** `/mnt/c/Users/Mahesh Yadav/Downloads/FinAudit-AI-starter-v2/FinAudit-AI`  
**Git state:** pre-existing modified/untracked working files were present at session start and preserved. This session additionally modified `PROJECT_AUDIT.md`, `backend/app/{dashboard,llm,main,risk_engine,vector_store}.py`, and `backend/app/agents/assistant_graph.py`; the live DB was updated by controlled, backed-up repairs (see below). No existing user work was deleted or reverted.

## 1. Backup and data preservation

- **Live database preserved:** `backend/data/finaudit.db` (SQLite). **Not deleted, reset, or recreated.**
- **Backups created before data-modifying repairs:**  
  - `backend/data/backups/finaudit-pre-audit-phaseA-20261118-065357.db` (pre-audit copy)  
  - `backend/data/backups/finaudit-pre-dedup-txns-20261118-065934.db` (pre-transaction dedup)  
  - `backend/data/backups/finaudit-pre-risk-recompute-20261118-070625.db` (pre-risk recompute/stale-alert cleanup)
- Existing historical backups in `backend/data/backups/` retained.
- Additive migration mechanism remains in place (`backend/app/migrations.py`, startup in `backend/app/main.py`). RAG store is separate from main DB: `backend/data/rag/rag.sqlite3`.

## 2. Phase A actions completed (verified)

### 2.1 Transaction deduplication / data integrity repair
- **Issue (baseline):** `transaction_records` had 2160 rows with heavy repeated imports (many `transaction_id`/`duplicate_key` repeats across 18 audit runs).
- **Repair:** Removed **1645 exact provenance-duplicate** rows where both `source_file` and `source_row` matched, keeping the lowest `id` per `(source_file, source_row)` group (controlled, evidence-preserving for unique source rows). **No ambiguous rows were deleted.**
- **Result:** `transaction_records` reduced **2160 → 515**. Post-repair checks: 0 duplicate `(source_file, source_row)` pairs, 0 duplicate `transaction_id` values, 13 weak `duplicate_key` groups remain (2 rows each, distinct provenance/source rows; preserved).
- **Current transaction scope:** 515 rows across `transactions (5).csv` (410), `finaudit_geographic_transactions_demo.csv` (100), `transactions.csv` (5); date range `2025-01-12` to `2026-12-02` (future-dated rows remain as source data); coordinates present for 100 / missing for 415 (not fabricated).

### 2.2 Risk analysis consistency repair
- **Issue (baseline):** Risk metrics/alerts were stale relative to deduped transaction state (496 alerts computed on 415 analyzed txns; 65 "Unusual Transaction Amount" alerts no longer reproduced by current deterministic rules after dedup; alert-transaction linkage needed re-verification).
- **Code fix:** `backend/app/risk_engine.py` `run_analysis` now removes **stale machine-generated alerts not reproduced by the current deterministic analysis only when they are unreviewed/open with no assignee/comments/resolution/events**; reviewed/investigating/resolved/dismissed alerts and alerts with recorded events are preserved as evidence/history. Run result now reports `removed_stale`.
- **Repair executed:** Backed up DB, then ran deterministic risk analysis (`triggered_by='phase-a-consistency-repair'`, run id 3).
- **Result:** analyzed 515 transactions; alert state **496 → 529** (created 98, updated 431, removed stale 65 unreviewed non-reproduced). Current breakdown: Bank Reconciliation Anomaly 415, Unusual Transaction Amount 80, Missing Approval Evidence 18, Duplicate Payment 11, Unusual Transaction Frequency 5 (critical 23, high 506, medium/low 0), all open/unresolved. All 415 reconciliation-anomaly alerts reference latest reconciliation run (id 4) results and valid `reconciliation_results` rows. Note: earlier query flagging 176 reconciliation alerts as "missing txn links" was a misinterpretation of `transaction_id` semantics for reconciliation alerts (those IDs are statement references, not `transaction_records` keys); their source reconciliation evidence remains intact.

### 2.3 Reconciliation / dashboard metric consistency
- **Observed:** Reconciliation engine behavior for dissimilar datasets is deterministic (runs 2–4: `transactions (5).csv` vs `transactions.csv` → 0 matched / 410 bank_only / 5 ledger_only; run 1 self-match produced 5 matched). 1250 `reconciliation_results` remain as preserved historical run evidence.
- **Issue (baseline):** `backend/app/dashboard.py` aggregated reconciliation counts across **all historical** `reconciliation_results`, inflating dashboard matched/unmatched totals.
- **Fix:** Dashboard reconciliation KPIs now derive from the **latest reconciliation run's** results only (with empty state when no runs), while preserving historical results/runs in DB.
- **Verified current dashboard scope (latest run):** matched 0, unmatched 415 (bank_only 410, ledger_only 5), possible/ambiguous 0; total transactions 515; spending/received/net computed from persisted transactions; total risk alerts 529.

### 2.4 LLM / Ollama and assistant consistency
- **Fixes:** `backend/app/llm.py` now reports the active model via `get_model()` in successful explanation responses (instead of stale module-level constant usage in response payload). `backend/app/agents/assistant_graph.py` reporting metadata now uses `llm.get_model()` consistently for fallback/success states.
- **Behavior:** `llm.is_available()` remains a reachability probe to local Ollama (`/api/tags`); generation can still fail if the daemon is reachable but the configured model/response fails, in which case deterministic evidence-only fallbacks are returned. `/api/system/health` and assistant config reflect reachability/model config; frontend/assistant handling should continue to treat LLM output as optional narration over deterministic evidence.

### 2.5 RAG ingestion/retrieval
- **Issue found:** CSV/PDF ingestion hashed raw bytes via `vector_store.text_hash` which assumed `str`, causing `AttributeError` on bytes input.
- **Fix:** `backend/app/vector_store.py` `text_hash` now accepts `str | bytes` safely.
- **Verification:** Local smoke test ingested a CSV, retrieved matching chunk(s), confirmed `retrieval_test` reports evidence, then deleted the test document (RAG store returned to 0 docs/chunks). RAG APIs/implementation (`rag.py`, `vector_store.py`, document endpoints in `main.py`) support CSV/PDF/TXT/MD/LOG/JSON ingestion, chunk metadata/citations, stats/status, delete/reindex/retrieval-test.

### 2.6 Import behavior
- **Verified:** `create_audit` skips duplicates against existing DB by `duplicate_key` and `(source_file, source_row)` provenance when `skip_duplicates=True` (default), supporting idempotent re-imports (e.g. audit run 18 re-imported demo file with 0 new rows). A temporary intra-batch duplicate-key skipping change that broke duplicate-detection/import semantics was reverted after tests exposed regressions; current behavior matches existing API expectations/tests.

## 3. Verification results

- **Backend tests (targeted, passed):**  
  - `test_risk_engine.py` — 5 passed  
  - `test_risk_api.py`, `test_reconciliation.py` — 18 passed  
  - `test_csv_normalizer.py`, `test_llm.py`, `test_api.py` — 28 passed total in batch (after reverting import regression)  
  - Environment warning observed from site-packages pytest plugin (`CallSpec2` deprecation notice), not from project code.
- **Full backend suite:** not fully executed to completion in this pass (earlier broad run timed out in environment); additional existing tests remain to be run/fixed as Phase A/B work continues.
- **Frontend production build:** `npm run build` in `frontend/` completed successfully (exit 0, Vite). Warnings only: large chunk size and `lucide-react` `"use client"` directive notices (non-fatal).

## 4. Current database snapshot (post-repair)

| Table / metric | Value | Notes |
|---|---:|---|
| transaction_records | 515 | Deduped by exact `(source_file, source_row)`; 13 weak `duplicate_key` groups preserved |
| risk_alerts | 529 | Recomputed deterministically (run 3); 23 critical / 506 high / 0 medium / 0 low; all open |
| risk_analysis_runs | 3 | Latest: completed, 515 txns, 529 alerts, triggered by `phase-a-consistency-repair` |
| reconciliation_runs | 4 | Historical runs preserved |
| reconciliation_results | 1250 | bank_only 1230, ledger_only 15, matched 5 across runs (history preserved); latest run = 415 results |
| audit_runs | 18 | Historical import records preserved (row/finding counts reflect historical runs; not rewritten) |
| findings | 329 | Historical findings preserved; 9 findings reference `transaction_id` values not present in current deduped transactions |
| investigation_runs | 10 | Preserved |
| investigation_cases | 0 | None |
| RAG documents/chunks | 0 / 0 | Empty until user-ingested/synced |

## 5. Remaining gaps / open issues (Phase A+ / later phases)

- **Historical consistency artifacts (preserved):** `audit_runs.row_count/finding_count` and `findings` are historical records and are not recalculated to match the deduped current transaction snapshot (9 findings now reference missing transaction IDs). Dashboard/risk current-state metrics use current deterministic computations where repaired, but historical audit evidence remains as stored.
- **Reconciliation interpretation:** Latest comparison (dissimilar statement/ledger files) legitimately yields 0 matched; engine/mapping/tolerance behavior should continue to be validated against known-good paired datasets and UI messaging/contracts reviewed for clarity.
- **Ollama:** Reachability-based health can differ from generation success (model missing/timeout/error); fallbacks are deterministic/evidence-only but user-facing status can be further clarified across backend/frontend.
- **RAG:** Ingestion/retrieval primitives verified; index is empty in current environment and end-to-end assistant evidence quality/citations depend on ingested documents and query relevance. Sync/ingest UX and error reporting warrant broader UI/API contract verification.
- **Investigation workflow:** Existing case/run/note/evidence/event/assistant infrastructure present, but full target workflow/statuses (`Open, Investigating, Awaiting Review, Approved, Rejected, Resolved, Closed`), explicit human-approval governance (AI cannot approve), audit-trail completeness, and case-centric UI/persistence need alignment and hardening.
- **Frontend/API contracts:** Broad contract and UX verification (loading/empty/error states, counts/status shapes, all modules) not yet completed.
- **Schema/production readiness:** SQLite-first; PostgreSQL compatibility path, Alembic migrations, stronger constraints/indexes, tenant model/RBAC groundwork, and Docker Compose/Postgres deployment path remain for later phases.
- **Security/governance:** No auth/RBAC/tenant isolation; formal approval controls and broader security hardening remain unimplemented and must not be claimed complete.
- **Testing:** Targeted tests/build passed as noted; full backend suite and broader integration/regression coverage still required.

## 6. Recommended next steps

1. Run broader backend test suite and fix regressions; validate reconciliation with known-good matched/unmatched fixtures and confirm UI/API contracts.
2. Complete Phase A contract/UX checks (transactions, reconciliation, risk, dashboard, reports, assistant/settings, RAG document flows) without removing working features.
3. Harden investigation case workflow, statuses, human-review/approval controls, and persisted evidence/audit trails (no fabricated traces; deterministic fallback).
4. Improve Ollama/RAG status messaging and end-to-end evidence-grounded assistant behavior; add/adjust tests for ingestion/retrieval/assistant fallbacks.
5. After Phase A is stable, proceed to Phase B–F per product spec (investigation engine, evidence intelligence, advanced intelligence, governance, production readiness) with additive migrations, preserved data, and verified claims only.
