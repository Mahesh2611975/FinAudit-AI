# FinAudit AI

**Local-first financial intelligence and audit investigation with deterministic rules, selective multi-agent AI and local RAG.**

FinAudit AI imports transaction and invoice data, applies explainable Python rules, stores findings and evidence in SQLite, retrieves relevant context with a local TF-IDF vector store, and optionally asks a locally running Ollama model to narrate that evidence. It is an educational prototype, not a certified accounting or fraud-detection system.

See [`FINAL_DELIVERABLES.md`](FINAL_DELIVERABLES.md) for architecture diagrams, workflows, the full API table, migration details, verification results and limitations.

## Features

- React workspace with a seven-item sidebar: Dashboard, Transactions, Reconciliation, Risk Alerts, AI Assistant, Reports and Settings.
- Dashboard KPIs and five charts computed from SQLite records, with documented metric scope and no fake data.
- Transaction CSV preview with automatic column mapping, validation, duplicate-safe import, search/date/amount filters, detail view and source file/row provenance.
- Deterministic bank/ledger reconciliation with matched, unmatched, amount/date difference and possible-match results; uncertain matches require human confirmation.
- Explainable 0–100 risk alerts with severity, reason, amount, timestamp, source evidence and review statuses: Open, Under investigation, Resolved, False positive and Dismissed.
- Selective LangGraph assistant with five required roles: Financial Analyst, Risk Investigation, Reconciliation, Evidence Retrieval and Investigation Summary (an Orchestrator selects a subset per question). Per-agent failures and answer history are persisted.
- Investigation workspace: create cases from risk alerts, assign reviewers, record status/resolution, attach evidence, add notes, request grounded AI explanations, review history and export a case CSV. `/api/cases` remains a compatibility alias.
- Local RAG over CSV, PDF, text reports and stored investigation reports. Chunks keep source filename, page, row and transaction IDs for citations. Management UI/API supports delete, re-index, per-document status and a retrieval test.
- Local Ollama generation with grounding rules; no paid API key. When Ollama is offline, answers fall back to explicit evidence-only text.
- Consolidated PDF/CSV reports containing financial summary, reconciliation, alerts, investigation narratives and evidence references, plus generation time, filters and calculation provenance.
- Dedicated filtered CSV exports for transactions and risk alerts, plus a consolidated, secret-free system health endpoint (`/api/system/health`).
- Invoice/AP audit module remains available through internal navigation from Reports: vendor, PO and receipt masters, CSV/Excel/PDF invoice ingestion, deterministic checks, human review, history and exports.
- Additive SQLite migrations with automatic pre-migration backups; no destructive schema changes. Deployment config for Vercel + Render + Neon PostgreSQL is included (see `DEPLOYMENT.md`).
- 125 passing backend tests and a successful frontend production build.

## Architecture

```text
React/Vite → FastAPI → deterministic rules + SQL aggregates
                    → LangGraph selective agents
                    → local TF-IDF RAG (SQLite metadata + tfidf.pkl)
                    → Ollama HTTP API (optional, local only)
                    → SQLite
```

All amounts, scores, matches and counts are calculated by deterministic Python code. The LLM only narrates supplied evidence; it never computes a value or decides a match. The legacy ten-node invoice workflow and `MultiAgentOrchestrator` are preserved for existing endpoints.

## Requirements

- Python 3.11 or 3.12 recommended
- Node.js 20+ recommended
- Git
- Optional: [Ollama](https://ollama.com/) with `llama3.2:1b` for AI explanations. No API key or paid account is required.

## Quick start

### 1. Backend

```bash
cd backend
python3 -m venv .venv          # Windows: py -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env           # Windows: Copy-Item .env.example .env
uvicorn app.main:app --reload
```

If Windows PowerShell blocks venv activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, then activate again.

On Windows/WSL, create a Linux virtual environment if running under Linux; the checked-in Windows `backend/.venv` cannot be used from WSL.

Open <http://127.0.0.1:8000/docs> to explore the API.

### 2. Frontend (new terminal)

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Set `VITE_API_URL` in `frontend/.env` if the API is not at `http://127.0.0.1:8000`.

### 3. Enable optional local AI

Install and start Ollama, then pull a small model suitable for an 8 GB RAM laptop:

```bash
ollama pull llama3.2:1b
ollama serve
```

`backend/.env` contains:

```text
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:1b
OLLAMA_TIMEOUT=20
OLLAMA_PROBE_TIMEOUT=1.5
```

No key is needed. If Ollama is unavailable, core audits, reconciliation, risk analysis, RAG retrieval and reports still work; AI answers become evidence-only responses that say the model is unavailable.

### 4. Try sample data

Choose `sample_data/transactions.csv` in the Transactions or Dashboard workspace. It intentionally includes a duplicate pair, a high-value payment and a negative amount.

Required canonical CSV columns: `transaction_id,date,vendor,amount,description`. Common aliases are normalized automatically.

For invoice auditing, import files from `sample_data/invoice_audit/` in this order: `vendors.csv`, `purchase_orders.csv`, `goods_receipts.csv`, then `invoices.csv`.

## Primary API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Simple health check |
| GET | `/api/system/health` | Consolidated secret-free DB/LLM/RAG/config status with real probes |
| GET | `/api/dashboard/overview` | DB-backed KPIs, five charts, recent activity and scope |
| POST | `/api/transactions/preview` | Validate and preview a CSV before import |
| POST | `/api/audits` | Import CSV(s); `skip_duplicates=true` prevents re-import |
| GET | `/api/transactions` | Search and filter transactions |
| GET | `/api/transactions/{transaction_ref}` | Transaction detail with alerts and reconciliation evidence |
| GET | `/api/transactions/export.csv` | Filtered transaction CSV with generated_at, filters and provenance |
| POST | `/api/reconciliations` | Run bank/ledger reconciliation; accepts `bank_mapping`/`ledger_mapping` (JSON) and `date_tolerance_days` |
| GET | `/api/reconciliations/{id}` | Read reconciliation evidence and applied mappings |
| POST | `/api/reconciliations/results/{id}/confirm` | Record a human confirmation with reviewer and comment |
| GET/POST | `/api/risk/analyze`, `/api/risk/alerts`, `/api/risk/alerts/{id}/review` | Run and review deterministic alerts |
| GET | `/api/risk/alerts/export.csv` | Filtered risk alert CSV with generated_at, filters and provenance |
| GET/POST | `/api/investigations*`, `/api/cases` | Investigation cases, from-alert, notes, evidence, explain, history and CSV export |
| POST | `/api/documents/ingest` | Ingest CSV/PDF/text documents into local RAG |
| POST | `/api/documents/sync-transactions` | Index stored transactions into RAG |
| GET | `/api/documents/status`, `/api/documents/{id}/status` | Collection and per-document index status |
| DELETE | `/api/documents/{id}` | Delete a document and its chunks |
| POST | `/api/documents/reindex`, `/api/documents/retrieval-test` | Rebuild index; debug retrieval without the LLM |
| GET | `/api/assistant/config`, `/api/assistant/suggestions` | Assistant runtime status and prompts |
| POST | `/api/assistant/query` | Selective multi-agent answer with citations, role/failure trace and persisted history |
| GET | `/api/reports/summary`, `/api/reports/full.csv`, `/api/reports/full.pdf` | Consolidated reports and exports with generation time and provenance |
| POST | `/api/agents/run` | Run the legacy persisted investigation workflow |
| POST/GET | `/api/invoices*`, `/api/invoice-audits/run*`, `/api/discrepancies*` | Invoice audit and review |

The complete endpoint list, including invoice master data and geospatial analytics, is in the interactive `/docs` page and in `FINAL_DELIVERABLES.md`.

## Reports and exports

- **Full report PDF:** financial summary, metric scope, top alerts, AI explanations and evidence references, with generation time, filters, provenance and calculation notes.
- **Full report CSV:** transaction, risk-alert and AI-explanation rows plus leading metadata rows for generated_at, filters and calculations.
- **Focused exports:** filtered transactions, filtered risk alerts, individual audit runs and individual reconciliation runs (each self-describing with generated_at and provenance).
- **Investigation export:** case detail with linked transactions, evidence, notes and history.
- **Invoice exports:** invoice audit CSV and PDF from the Invoice Agent Center.

Automated findings are investigation leads. They are not proof of fraud and do not authorize financial action.

## Tests

From the repository root:

```bash
cd backend && python3 -m pytest -q
cd frontend && npm run build
```

Current results: **125 passed** backend tests, including a deterministic end-to-end workflow covering import → reconciliation → risk → investigation → evidence → RAG retrieval → assistant → report export (one unrelated `anyio` deprecation warning), and a successful Vite production build (lucide `"use client"` and bundle-size warnings only). Run backend tests from `backend/` so the test configuration sets an isolated `FINAUDIT_DATABASE_URL` before the app imports.

## Database safety

- The database is SQLite at `backend/data/finaudit.db` by default.
- A pre-refactor backup exists at `backend/data/backups/finaudit-pre-refactor-20261117-135303.db`.
- Startup creates a timestamped backup before pending schema changes and applies only additive columns/indexes.
- Existing tables and records are never dropped; a `NOT NULL` column without a default is added as nullable with a warning.

## Known limitations

- TF-IDF retrieval is lexical rather than semantic; it avoids model downloads and heavy dependencies.
- PDF extraction uses `pypdf` when available and a lightweight fallback otherwise; scanned/image-only PDFs need manual review.
- There is no authentication or multi-user access control. Run locally with synthetic or anonymized data; restrict any deployed instance at the network/edge layer.
- Thresholds are configurable defaults, not universal accounting rules.
- Overlapping findings can double-count a transaction in flagged-amount KPIs.
- Currencies are never converted automatically; mismatches become review exceptions.
- Ollama output can be wrong. Verify cited source rows and have an authorized human approve any financial action.
- The RAG store is local SQLite on the backend filesystem; on ephemeral hosts, re-ingest documents after each deploy or mount persistent storage.
- Deployment configuration and secrets handling are documented in `DEPLOYMENT.md`; no live deployment has been published from this environment.

## Resume summary

**FinAudit AI | Python, FastAPI, SQLite, React, LangGraph, RAG, Ollama**

- Built a local-first financial intelligence platform with database-backed KPIs, five charts, CSV import validation, duplicate prevention and full source provenance.
- Implemented deterministic bank/ledger reconciliation and an explainable risk engine with human review statuses and evidence-backed alert explanations.
- Built a selective five-role LangGraph assistant with validated outputs, SQL evidence, local TF-IDF RAG citations, per-agent failure tracing, persisted answer history and grounded Ollama narration with safe fallbacks.
- Added an alert-to-case investigation workspace, filtered exports with provenance, consolidated secret-free system health, additive SQLite migrations, invoice/AP audit workflows, deployment configuration and 125 passing backend tests.

## License

MIT — see `LICENSE`.
