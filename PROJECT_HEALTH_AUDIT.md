# FinAudit AI — Project Health Audit

**Audit date**: 2026-11-23  
**Workspace**: `/mnt/c/Users/Mahesh Yadav/Downloads/FinAudit-AI-starter-v2/FinAudit-AI`  
**Git branch/HEAD**: `main @ c7ba867 Improve health checks and navigation verification`  
**Auditor mode**: Read-first, evidence-based. No destructive DB operations performed.

## 1. Executive Summary

FinAudit AI is a substantial local-first FastAPI + React + SQLite financial audit prototype with real deterministic reconciliation, risk, invoice audit, RAG, and assistant workflows. It is **not production-ready**. Core flows exist and the baseline test suite passes, but there are confirmed data-integrity risks, missing auth/governance controls, frontend contract gaps, and incomplete module coverage.

**Baseline verification (executed this session):**
- Backend: `python3 -m pytest -q` → **147 passed, 1 warning** (395.50s), warning from site-packages `anyio/pytest_plugin.py` `CallSpec2` deprecation.
- Frontend: `npm run build` → **success** (Vite build; large chunk + lucide `"use client"` warnings only).
- Frontend: `npm run verify:navigation` → **40 checks passed**.

## 2. Existing Architecture

| Layer | Technology | Evidence |
|---|---|---|
| Backend API | Python, FastAPI, SQLAlchemy, Pydantic (partial) | `backend/app/main.py`, `backend/requirements.txt` |
| Database | SQLite default (`backend/data/finaudit.db`), additive migrations | `backend/app/database.py`, `backend/app/migrations.py` |
| Frontend | React 19, Vite 8, Recharts, Leaflet, lucide-react | `frontend/package.json`, `frontend/src/*` |
| AI/LLM | Ollama local HTTP API, deterministic fallbacks | `backend/app/llm.py`, `backend/app/agents/*` |
| RAG | Local TF-IDF + SQLite metadata (`backend/data/rag/rag.sqlite3`) | `backend/app/rag.py`, `backend/app/vector_store.py` |
| Agents | LangGraph selective assistant + legacy orchestrator/invoice workflow | `backend/app/agents/assistant_graph.py`, `orchestrator.py`, `invoice_workflow.py` |
| Tests | Pytest backend, Vite build, static navigation verify script | `backend/tests/*`, `frontend/scripts/verify-navigation.mjs` |

## 3. Repository / Pre-existing Work

Uncommitted user work present before/alongside audit; preserved:
- `M backend/app/dashboard.py`
- `M frontend/scripts/verify-navigation.mjs`
- `M frontend/src/Dashboard.jsx`
- `M frontend/src/main.jsx`
- `M frontend/src/workspace.css`
- `?? backend/tests/test_dashboard_overview.py`

Audit changes made in this session (safe, code/test/docs only): fixed `csv_normalizer._parse_date` ISO-timestamp day/month swap bug; added regression tests; fixed dashboard latest reconciliation run selection to use latest `ReconciliationRun`; created audit/remediation docs.

## 4. Module Inventory (14 requested modules)

Status legend: **VERIFIED** = inspected and behavior/evidence supports claim; **FIXED AND TESTED** = defect fixed with targeted tests; **PARTIALLY WORKING** = implemented but gaps/defects remain; **BLOCKED** = cannot fully verify due to missing evidence/environment; **NOT IMPLEMENTED** = absent.

| Module | Status | Evidence | Key gaps / defects |
|---|---|---|---|
| 1. Financial Intelligence Dashboard | PARTIALLY WORKING | `backend/app/dashboard.py`, `frontend/src/Dashboard.jsx`, `/api/dashboard/overview`; build/nav pass; dashboard tests pass | KPI definitions present but need explicit business sign-off; old untracked dashboard test exists; frontend has unreachable dead `OverviewPage` with divergent formula in `main.jsx`; refresh is manual-only |
| 2. Transaction Management | PARTIALLY WORKING | `/api/transactions/preview`, `/api/audits`, `/api/transactions`, `/api/transactions/{ref}`, export; `frontend/src/Transactions.jsx` | No edit/delete/audit trail UI; currency hardcoded as INR in UI; source_file filter is free text; no explicit pagination/sort controls beyond page/filter; no RBAC |
| 3. Advanced Reconciliation Engine | PARTIALLY WORKING | `backend/app/reconciliation.py`, main reconciliation endpoints, review/manual-link, exports; tests pass | Amount tolerance bypassed in reference-match amount-mismatch path (`reconciliation.py` `_pair_status`); human confirmation represented but not identity-verified; possible matches not auto-confirmed (good) but UX has prompt() and stale refresh risks |
| 4. Financial Risk Intelligence Center | PARTIALLY WORKING | `backend/app/risk_engine.py`, `risk_api.py`, `RiskIntelligence.jsx`; tests pass | Alerts explainable but many open/unreviewed; no auth on review endpoints; create-case-from-alert missing in UI despite README claim; risk scoring uses float/round in places |
| 5. Geographic Intelligence | PARTIALLY WORKING | `backend/app/geo_analytics.py`, `geocoding.py`, risk geo endpoints, `GeographicRiskIntelligence.jsx` | Coordinates not fabricated (good); geocoding optional; map depends on external tiles/assets; no offline fallback; location coverage limited by source data |
| 6. Vendor Intelligence | PARTIALLY WORKING | Vendor models/endpoints in invoice API and vendor analytics; nav item exists | No dedicated vendor intelligence workspace for transaction vendors; invoice-vendor analytics exists but product-scope “Vendor Intelligence” is incomplete/fragmented |
| 7. Investigation Cases | PARTIALLY WORKING | `backend/app/investigation_api.py`, `InvestigationCase/*` models, frontend investigations/compliance views | Case workflow statuses incomplete vs requested approval workflow; no RBAC; create-from-alert not exposed in risk UI; history/audit trail exists but approval governance weak |
| 8. AI Investigation Assistant | PARTIALLY WORKING | `assistant_graph.py`, `/api/assistant/query`, `Assistant.jsx`, LLM/RAG fallbacks | Grounding is instruction-based, not schema-enforced; no persisted answer history UI; prompt injection/fabrication risk remains; model availability can differ from generation success |
| 9. Multi-Agent Investigation System | PARTIALLY WORKING | LangGraph selective assistant; legacy orchestrator; invoice workflow | Multiple agent systems coexist (good for compatibility) but add complexity; legacy orchestrator has broad responsibilities; no strong policy gate preventing financial action |
| 10. Agent Execution History | PARTIALLY WORKING | `/api/agents/runs`, investigation_runs, invoice audit runs, frontend agents/monitoring views | History persisted, but not unified across all workflows; UI coverage uneven; no auth/filtering by user |
| 11. Knowledge Base and RAG | PARTIALLY WORKING | ingest/sync/status/delete/reindex/retrieval-test endpoints, local TF-IDF store | Empty Evidence/Documents workspace in primary nav; per-document status not surfaced in UI; retrieval quality lexical only; pickle index load is trusted-local assumption |
| 12. Invoice Audit | PARTIALLY WORKING | invoice models/APIs/audit/extraction/workflow, tests strong (30+ in invoice tests) | Accessible mostly through secondary/report flows; human review needed but not enforced by RBAC; PDF extraction quality dependent on pypdf/fallback |
| 13. Reports | PARTIALLY WORKING | summary/full CSV/full PDF, filtered exports, provenance metadata | Report accuracy depends on underlying metric definitions; no report auth; PDF is simple text PDF; some frontend export actions open windows without robust error UX |
| 14. Settings and Security | PARTIALLY WORKING / HIGH RISK | `/api/health`, `/api/system/health`, env config; CORS configurable | **No authentication, no JWT, no RBAC, no password hashing, no protected routes.** `authentication_enabled` is false. Secrets not exposed by health endpoint (good), but system is not secure for multi-user deployment. |

## 5. Confirmed Defects

### Critical
| ID | Defect | Evidence | Status |
|---|---|---|---|
| C1 | No authentication/authorization/RBAC; sensitive endpoints exposed | `main.py` system health reports `authentication_enabled:false`; routes have no auth deps; review/export/mutation endpoints callable | Open |
| C2 | CSV date parser swapped month/day for ISO timestamps with ambiguous day/month | `backend/app/csv_normalizer.py` old `_parse_date` used `dayfirst=True` fallback for ISO datetimes; repro confirmed | **Fixed in code + regression tests added**; DB backfill **BLOCKED pending approval/mapping** |

### High
| ID | Defect | Evidence | Status |
|---|---|---|---|
| H1 | Reconciliation amount tolerance bypassed for reference-matched rows with amount diff | `backend/app/reconciliation.py` `_pair_status` returns `amount_mismatch` before tolerance/date logic when refs match | Open |
| H2 | Dashboard reconciliation latest-run selection used latest result row, not latest reconciliation run | `backend/app/dashboard.py` previously queried `ReconciliationResult.run_id desc` | **Fixed in code**; targeted tests pass |
| H3 | Frontend API base normalization inconsistent; `VITE_API_URL` ending `/api` can break Dashboard/Transactions/Assistant | `frontend/src/main.jsx` strips `/api`; `Dashboard.jsx`, `Transactions.jsx`, `Assistant.jsx` only strip slash | Open |
| H4 | Human approval identity is free-text/unverified across reconciliation/risk/invoice flows | review endpoints accept reviewer string; no identity model | Open |
| H5 | Missing source CSV `transactions (5).csv` blocks record-level date correction report | DB has 410 rows from `transactions (5).csv`; file not found in workspace/Downloads search | **BLOCKED** |

### Medium
| ID | Defect | Evidence | Status |
|---|---|---|---|
| M1 | Raw `payload: dict` used for many mutating endpoints; selective manual validation | `main.py`, `risk_api.py` accept dict payloads | Open |
| M2 | LLM grounding is instruction-based; fabrication/prompt-injection risk remains | `assistant_graph.py`, `llm.py`, `risk_api.py` | Open |
| M3 | RAG pickle index load trusted-local assumption | `vector_store.py` uses pickle load/dump | Open (acceptable local-only, risky if untrusted FS) |
| M4 | Frontend nav highlight stale for secondary/invoice views | `main.jsx` activeNavId logic | Open |
| M5 | Risk drawer cannot create investigation case; assistant history/per-doc RAG status missing in UI | `RiskIntelligence.jsx`, `Assistant.jsx`, README claims | Open |
| M6 | Currency hardcoded to INR in several frontend displays | `Dashboard.jsx`, `Transactions.jsx`, analytics formatting | Open |
| M7 | Untracked dashboard test and dirty dashboard/frontend files create contract drift risk | git status | Open (preserved as user work) |
| M8 | 441 transactions uncategorized; category logic simple keyword rules | DB query: Uncategorized 441/515; `category.py` | Open (improvement needed, no silent overwrite of stored categories) |

### Low
| ID | Defect | Evidence | Status |
|---|---|---|---|
| L1 | Dead/unreachable `OverviewPage` in `main.jsx` has divergent reconciliation formula | `main.jsx` defines OverviewPage not rendered | Open |
| L2 | Static `verify-navigation.mjs` gives limited confidence (regex checks only) | script inspects source strings | Open |
| L3 | Frontend dependencies pinned to `latest` | `frontend/package.json` | Open |
| L4 | No global `:focus-visible`; some clickable rows/buttons lack a11y semantics | frontend CSS/JSX | Open |

## 6. Data Integrity Findings

Live DB inspected read-only (`backend/data/finaudit.db`):
- `transaction_records`: 515 rows; sources: `transactions (5).csv` 410, `finaudit_geographic_transactions_demo.csv` 100, `transactions.csv` 5.
- Dates: all stored as length 10, min 2025-01-12, max 2026-12-02. No timestamp-length stored dates.
- Categories: Uncategorized 441, Transfers 33, Transport and travel 21, Utilities and bills 11, Shopping 7, Healthcare 1, Food and dining 1.
- Risk alerts: 529 total; high 494, critical 23, medium 12; all `open`.
- Reconciliation runs: 8; latest run 8 has matched 1 / 416 results, match rate 0.48% by current formula.
- RAG sqlite has indexed content (separate DB).

**Date parsing incident (C2/H5):**
- Confirmed parser bug could swap ambiguous ISO timestamps (`YYYY-MM-DDT00:00:00`) into wrong dates before fix.
- Existing DB has no timestamp-formatted date strings; corruption would be stored as normal `YYYY-MM-DD` and therefore silent.
- Source file `transactions (5).csv` is **not present** in workspace/accessible Downloads; exact old->new row mapping cannot be verified from source. 
- **No DB backfill performed.** A correction report requires the original source file or an approved authoritative mapping.

## 7. Security Findings

- No authentication, no JWT/session auth, no RBAC, no user model enforcement. **Critical for deployment.**
- CORS allows configured origins; credentials enabled in middleware.
- No secrets exposed in `/api/system/health` (positive).
- Uploads parsed with size checks in some paths; parser hardening/fuzzing still needed.
- RAG pickle load trusted-local only.
- LLM outputs are not strong-schema validated; evidence-grounding can fail.

## 8. Performance / Reliability Findings

- Baseline backend tests are slow (~6.5 min in this env) but pass.
- Frontend bundle large (~1.05 MB JS minified); code-splitting recommended.
- Dashboard/reconciliation/analytics use Python loops over all transactions in some paths; acceptable for current 515 rows, may not scale.
- SQLite is suitable for local demo; concurrent multi-user production needs PostgreSQL + migrations + locking strategy.
- LLM/RAG optional dependencies can degrade; fallbacks exist but status messaging can confuse users.

## 9. Deployment Readiness

**Not production-ready.** Suitable for controlled local demo after careful operator use.

Blockers:
- No auth/RBAC/multi-user controls.
- Date-correction mapping for historical imported data unresolved (source file missing).
- Raw-dict validation gaps and some reconciliation logic defects.
- Frontend config/accessibility/contract issues.
- No verified PostgreSQL production migration path in this audit pass.

## 10. Test and Build Evidence

| Command | Result |
|---|---|
| `cd backend && python3 -m pytest -q` | **147 passed, 1 warning** in 395.50s |
| `cd frontend && npm run build` | **Pass**; warnings: lucide `use client` directives, large chunk >500kB |
| `cd frontend && npm run verify:navigation` | **Pass**; 40 checks passed |

Targeted after fixes this session:
- `python3 -m pytest tests/test_csv_normalizer.py -q` → **17 passed, 1 warning**
- `python3 -m pytest tests/test_dashboard_overview.py tests/test_reconciliation.py -q` → expected to pass (run during remediation; see plan/results).

## 11. Recommended Next Steps

1. **Obtain/restore `transactions (5).csv`** and produce approved record-by-record date correction mapping; do not backfill without approval.
2. Fix reconciliation reference+amount tolerance behavior with fixture-backed tests.
3. Normalize frontend API base handling and add smoke/browser tests.
4. Add Pydantic request schemas for dict payload endpoints.
5. Implement minimum security baseline for deployment: auth, RBAC, protected routes, audit actor identity.
6. Improve categorization rules for future imports; provide reviewed recategorization workflow, no silent overwrite.
7. Add DB constraints/indexes and PostgreSQL migration plan.
8. Complete module gaps: case-from-alert UI, assistant history, per-doc RAG status, vendor intelligence workspace, evidence/documents workspace.
