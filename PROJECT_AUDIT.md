# FinAudit AI — Project audit and status checklist

Status: **Phases 1–6 complete (implementation and local verification)**. This
checklist is based on direct inspection of the repository, the live SQLite
schema, configuration and the executed test suite. Actual cloud deployment is
prepared but not published (see Blocker).

## Repository state

- Started from commit `d70060c`. All work since is uncommitted in the working
  tree and must be preserved (no files were deleted or rebuilt).
- Stack preserved: React/Vite frontend, Python/FastAPI/SQLAlchemy backend,
  SQLite local database, LangGraph agents, local TF-IDF RAG, optional Ollama.
- Live database `backend/data/finaudit.db` was preserved. It had 25 tables at
  audit start; additive migrations raised it to **28 tables** (investigation
  notes/evidence/events added). No data was dropped.
- Pre-refactor backup retained: `backend/data/backups/finaudit-pre-refactor-20261117-135303.db`.
- `backend/.venv` is Windows-only; system `python3` is used in this environment.
- `backend/.env` contains legacy `GROQ_*` keys; unused by the current Ollama-only
  implementation. No credential values appear in source files.

## Working features (verified)

### Core backend and data integrity

- [x] FastAPI app with 100+ REST routes; invoice, risk and investigation routers.
- [x] DB-backed dashboard aggregates and five chart payloads (`dashboard.py`).
- [x] CSV normalizer, deterministic audit engine, transaction persistence,
      preview and duplicate-safe import.
- [x] Transaction search, date/amount/source filters and detail with provenance.
- [x] Dedicated filtered transaction CSV export with `generated_at`, filters and
      provenance (`GET /api/transactions/export.csv`).
- [x] Deterministic reconciliation engine with independent per-file column
      mapping, configurable date tolerance (0–365 days), status evidence,
      human confirm/reject endpoint and CSV export.
- [x] Dedicated filtered risk alert CSV export with `generated_at`, filters and
      provenance (`GET /api/risk/alerts/export.csv`).
- [x] Deterministic risk engine with fingerprints, severity, score, review
      statuses and alert events; geography endpoints with cached geocoding.
- [x] Investigation workflow: create, list, detail, from-alert, status/resolution,
      notes, evidence, explain, history and CSV export. `/api/cases` is a
      compatibility alias delegating to the same helpers.
- [x] Consolidated report summary, PDF and CSV exports now include generation
      time, applied filters and calculation provenance.
- [x] Additive schema reconciliation with SQLite backups before changes.

### GenAI, RAG and assistant

- [x] Local Ollama-only LLM client with grounding rules and an explicit
      evidence-only fallback when Ollama is offline. No paid LLM API.
- [x] Local TF-IDF + SQLite RAG store with full source metadata for citations.
- [x] RAG management: delete document, re-index, per-document status, collection
      status and a retrieval-test endpoint (no LLM call, no fabricated evidence).
- [x] Selective LangGraph assistant aligned to the five required roles:
      Financial Analyst, Risk Investigation, Reconciliation, Evidence Retrieval
      and Investigation Summary. Legacy route keys retained for compatibility.
- [x] Per-agent failure trace (`agent_failures`, failed timeline entries) and
      assistant answers persisted as `InvestigationRun(task_type="assistant_query")`.
- [x] Consolidated secret-free `GET /api/system/health` with real DB probe and
      counts, LLM provider/availability/model, RAG counts/status and config flags.
- [x] Legacy multi-agent orchestrator and invoice workflow preserved.

### Frontend

- [x] `CasesPage` is a full investigation workspace wired to `/api/investigations`
      (create, detail, status, notes, evidence, explain, history, export).
- [x] `InvestigationsPage` reads persisted cases from `/api/investigations`.
- [x] Reconciliation page exposes independent column mapping, date tolerance and
      working confirm/reject actions.
- [x] Settings uses `/api/system/health`; Reports surfaces the dedicated filtered
      exports and report metadata.
- [x] Assistant exposes RAG counts/status/retrieval-test/delete/re-index plus
      five-role and agent-failure trace.
- [x] Production build succeeds (`frontend/dist`).

### Deployment preparation

- [x] Backend `Dockerfile` (Render-compatible), `.dockerignore`, `render.yaml`
      blueprint, Vercel `vercel.json`, production env template
      (`deploy/.env.production.example`), PostgreSQL driver in requirements.
- [x] `DEPLOYMENT.md` with Neon/Render/Vercel steps, additive migration notes and
      a 16-point smoke-test checklist.
- [x] Deterministic end-to-end test covering import → reconciliation → risk →
      investigation → evidence → RAG retrieval → assistant → report export.

## Verified results

- Backend pytest (before the final E2E test and review-field additions):
  **124 passed, 1 warning in 307.31s**.
- Focused post-change tests: management/RAG/roles **4 passed**; E2E workflow
  **1 passed**; exports/mapping/investigation **6 passed**.
- Frontend production build: **succeeded** (warnings only: lucide-react
  `"use client"` directives and a >500 kB bundle).

## Verification still to run

- [ ] One final full `cd backend && python3 -m pytest -q` after all Phase 5/6
      changes (expected 125 tests) and a final `npm run build` if sources change.

## Security checks

- [x] Upload endpoints enforce extension/type and size limits.
- [x] ORM queries are parameterized; untrusted document text is supplied as data,
      never as system instructions.
- [x] CORS origins come from `FINAUDIT_CORS_ORIGINS` with localhost defaults.
- [x] No secrets in frontend source or committed env files; `/api/system/health`
      explicitly reports `secrets_exposed: false`.
- [x] Authentication is **not** implemented; no production auth claim is made.

## Blocker

- No hosting credentials, PostgreSQL connection string, public inference host or
  user authorization are available. Deployment configuration is complete, but
  publishing to Vercel/Render/Neon cannot be performed here. No live URLs exist.
