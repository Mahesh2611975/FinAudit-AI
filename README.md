# FinAudit AI

**Local-first financial audit exception analysis with RAG-assisted investigation.**

FinAudit AI is a portfolio MVP that imports transaction CSVs, applies explainable audit rules, stores findings in SQLite, retrieves relevant audit-policy snippets, and optionally asks a Groq-hosted model to explain an exception. It is an educational prototype, not a certified accounting or fraud-detection system.

## Features

- CSV upload and schema validation through FastAPI
- Deterministic checks for possible duplicate payments, high-value transactions, and negative amounts
- SQLite persistence for audit runs and findings
- Local TF-IDF retrieval over built-in audit policy snippets and finding evidence (no embedding API)
- Optional Groq LLM explanations; clear fallback and safe error category when AI is unavailable
- React dashboard, recent audit history, and CSV export
- Pytest unit tests for core audit rules

## Architecture

React + Vite → FastAPI → CSV validation / audit engine → SQLite. For an explanation request, the backend retrieves relevant policy snippets with TF-IDF and sends only the retrieved context and finding evidence to Groq. The default model is `openai/gpt-oss-20b`.

## Requirements

- Python 3.11 or 3.12 recommended
- Node.js 20+ recommended
- Git
- A Groq API key (optional for non-AI audit checks; required for LLM explanations)

## Quick start (Windows PowerShell)

### 1. Backend

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload
```

If PowerShell blocks venv activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, then activate again.

Open http://127.0.0.1:8000/docs to explore the API.

### 2. Frontend (new terminal)

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal, usually http://localhost:5173.

### 3. Enable Groq AI explanations (optional)

Create `backend/.env` from `backend/.env.example`, then add your own key:

```powershell
Copy-Item backend/.env.example backend/.env
notepad backend/.env
```

Set `GROQ_API_KEY` to your key from https://console.groq.com/keys and keep `GROQ_MODEL=openai/gpt-oss-20b` unless your Groq account uses a different supported model. The backend loads `backend/.env` relative to the Python files, not the current working directory.

Never commit `backend/.env`. If the key is missing or Groq is unavailable, core audits still work and the API returns a safe fallback explanation with an error category.

### 4. Try the sample data

Choose `sample_data/transactions.csv` in the dashboard. The sample intentionally includes a duplicate pair, a high-value payment, and a negative amount.

Required CSV columns: `transaction_id,date,vendor,amount,description`.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/audits` | Upload and audit a CSV |
| GET | `/api/audits` | List recent audit runs |
| GET | `/api/audits/{id}` | Read an audit and findings |
| POST | `/api/findings/{id}/explain` | Retrieve context and ask Groq for an explanation |
| GET | `/api/audits/{id}/export.csv` | Download findings as CSV |

## Tests

From the `backend` directory with the virtual environment active:

```powershell
$env:PYTHONPATH = "."
pytest -q
```

## Known limitations / next steps

- The initial release uses TF-IDF retrieval, not a neural embedding model; this keeps the setup lighter on an 8 GB RAM laptop.
- The current CSV schema is intentionally small. Invoice-to-payment reconciliation, authentication, multi-user access control, and PDF reports are future extensions.
- The high-value threshold is a simple configurable code default, not a universal accounting rule.
- Findings may overlap; the flagged amount KPI can double-count the same transaction across different rules.
- Do not upload confidential production finance data. Use synthetic or anonymized examples.
- LLM output can be incorrect. Review source documents and human-approve any financial action.
- Groq explanations send retrieved policy snippets and finding evidence to Groq's API. Do not use confidential production data unless that transfer is approved.

## Deployment notes

The frontend can be built with `npm run build` and deployed as static assets to a free static host. The backend can be deployed to a free Python web service if the provider supports persistent storage, but free instances may sleep or reset local files. SQLite is suitable for a demo, not a multi-instance production deployment. Configure `GROQ_API_KEY` as a backend environment variable/secret on the host. For a shareable demo, deploy the UI and API separately, configure `VITE_API_URL` to the API base URL, update backend CORS to the deployed frontend origin, and use synthetic sample data. Verify current free-tier limits before choosing a provider.

## Resume bullet (edit to match what you actually complete)

**FinAudit AI | Python, FastAPI, SQLite, React, RAG, Groq**
- Built a local-first financial audit application that validates CSV transactions and flags potential duplicate, high-value, and negative-amount exceptions using explainable Python rules.
- Implemented retrieval-augmented investigation notes using TF-IDF retrieval over audit policies and transaction evidence, with optional Groq LLM explanations and source references.
- Developed REST APIs, SQLite persistence, CSV exports, a React dashboard, and automated tests for audit-rule behavior.

## License

MIT — see `LICENSE`.
