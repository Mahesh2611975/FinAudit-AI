# FinAudit AI

**Local-first financial audit exception analysis with RAG-assisted investigation.**

FinAudit AI is a local-first financial audit application that combines transaction exception analysis with an accounts-payable invoice audit workflow. It imports transaction and invoice data, applies deterministic audit rules, stores findings and evidence in SQLite, retrieves relevant audit-policy snippets, and optionally asks a Groq-hosted model to explain an exception. It is an educational prototype, not a certified accounting or fraud-detection system.

## Features

- CSV upload and schema validation through FastAPI
- Deterministic checks for possible duplicate payments, high-value transactions, and negative amounts
- SQLite persistence for audit runs and findings
- Local TF-IDF retrieval over built-in audit policy snippets and finding evidence (no embedding API)
- Optional Groq LLM explanations; clear fallback and safe error category when AI is unavailable
- React dashboard, recent audit history, and CSV export
- **Invoice audit module:** vendor, purchase-order and goods-receipt masters; CSV/Excel/PDF invoice ingestion; SHA-256 document provenance; deterministic duplicate, PO, receipt, tax, payment and vendor-pattern checks; human review queue; invoice audit history; CSV and PDF report exports
- **LangGraph invoice workflow:** ten real nodes from document intake through reporting, persisted with the same timeline/event/evidence model as the existing investigation workflow
- **Financial risk and fraud intelligence:** deterministic 0–100 risk scoring, duplicate/unusual/frequency/vendor/invoice/reconciliation detectors, alert review workflow, channel analytics, trend analytics, coordinate-only geography, and AI-assisted investigation grounded in stored evidence
- Pytest coverage for core audit rules, analytics, the multi-agent workflow, the invoice audit module, and the risk intelligence engine/API

## Architecture

React + Vite → FastAPI → deterministic validation/matching engines → SQLite. For an explanation request, the backend retrieves relevant policy snippets with TF-IDF and sends only the retrieved context and finding evidence to Groq. The default model is `openai/gpt-oss-20b`.

The invoice workflow uses LangGraph with ten nodes: Document Intake, Invoice Extraction, Duplicate Detection, Purchase Order Matching, Goods Receipt, Payment Verification, Discrepancy Investigation, Evidence/RAG, Financial Impact and Invoice Reporting. All arithmetic and matching are deterministic. The LLM is optional and is never used to calculate amounts or decide a match. Invoice audit runs also create an `InvestigationRun`, so they appear in the existing AI Agent Center history.

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

For the invoice module, import the files in `sample_data/invoice_audit/` in this order: `vendors.csv`, `purchase_orders.csv`, `goods_receipts.csv`, then upload `invoices.csv` from Invoice Management. The sample includes a deliberate possible duplicate invoice for review.

## Invoice / AP audit workflow

1. Create or import vendors.
2. Create or import purchase orders and goods receipts.
3. Upload invoices (CSV, Excel or PDF). Original files are stored under `backend/uploads/invoice_documents/` and hashed for duplicate-document evidence. These files and SQLite databases are ignored by Git.
4. Run an invoice audit from **Invoice Management**, **Invoice Matching** or **Invoice Agent Center**.
5. Review discrepancies under **Discrepancies**. A reviewer name, decision and comment are persisted. The workflow never approves a payment or makes a final accounting decision.
6. Download the audit result as CSV or PDF from the Invoice Agent Center.

Example invoice CSV columns:

```text
invoice_number,vendor_name,invoice_date,due_date,currency,purchase_order_number,item_code,description,quantity,unit_price,tax_rate,line_total,total_amount
INV-100,Acme Supplies,2099-01-03,2099-12-31,INR,PO-100,A1,Paper,10,100,18,1000,1180
```

Money is stored as integer minor units (for example paise/cents). A missing field is reported for human review rather than invented. Different currencies are not compared without a documented conversion; the engine records a `currency_mismatch` exception instead.

PDF text extraction requires the optional `pypdf` package listed in `backend/requirements.txt`. Scanned-image OCR is not included; those documents remain in the human-review queue with missing extracted fields.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/audits` | Upload and audit a CSV |
| GET | `/api/audits` | List recent audit runs |
| GET | `/api/audits/{id}` | Read an audit and findings |
| POST | `/api/findings/{id}/explain` | Retrieve context and ask Groq for an explanation |
| GET | `/api/audits/{id}/export.csv` | Download findings as CSV |
| POST | `/api/vendors`, `/api/vendors/import` | Create or import vendors |
| GET | `/api/vendors`, `/api/vendors/{id}`, `/api/vendors/analytics` | Vendor master and analytics |
| POST | `/api/purchase-orders`, `/api/purchase-orders/import` | Create or import purchase orders |
| GET | `/api/purchase-orders`, `/api/purchase-orders/{id}` | Read purchase orders and line items |
| POST | `/api/goods-receipts`, `/api/goods-receipts/import` | Create or import goods receipts |
| GET | `/api/goods-receipts`, `/api/goods-receipts/{id}` | Read receipts and accepted/rejected quantities |
| POST | `/api/invoices`, `/api/invoices/upload` | Create an invoice manually or ingest CSV/Excel/PDF |
| GET | `/api/invoices`, `/api/invoices/{id}` | List invoices or read full evidence detail |
| POST | `/api/invoices/{id}/payments` | Record a payment (currency must match) |
| POST | `/api/invoice-payments/{id}/verify` | Mark a recorded payment as verified |
| POST | `/api/invoices/{id}/audit`, `/api/invoice-audits/run` | Run the ten-node invoice audit workflow |
| GET | `/api/risk/config` | Read risk thresholds, score bands, channels and review statuses |
| POST | `/api/risk/analyze` | Run deterministic risk analysis and upsert alerts |
| GET | `/api/risk/overview`, `/api/risk/alerts`, `/api/risk/alerts/{id}` | Risk KPIs and filterable alert review queue |
| POST | `/api/risk/alerts/{id}/review` | Assign, investigate, resolve, dismiss or comment on an alert |
| GET | `/api/risk/channels`, `/api/risk/trends`, `/api/risk/geography` | Channel, trend and stored-coordinate analytics |
| POST | `/api/risk/investigate` | Build deterministic evidence context and request an AI explanation |
| GET | `/api/invoice-audits/runs`, `/api/invoice-audits/runs/{id}` | Audit-run history and structured result |
| GET | `/api/invoice-audits/runs/{id}/export.csv` | Download invoice audit CSV |
| GET | `/api/invoice-audits/runs/{id}/export.pdf` | Download invoice audit PDF |
| GET | `/api/discrepancies`, `/api/discrepancies/{id}` | List or read exceptions |
| POST | `/api/discrepancies/{id}/review` | Persist a human review decision |
| GET | `/api/invoice-analytics/monthly`, `/api/invoice-analytics/vendors` | Invoice and vendor analytics |

## Tests

From the `backend` directory with the virtual environment active:

```powershell
$env:PYTHONPATH = "."
pytest -q
```

The current suite contains 91 passing backend tests, including 30 invoice-audit scenarios covering extraction, duplicate documents, PO/vendor/currency/item/quantity/price/tax/total mismatches, missing/partial/rejected receipts, payment states, duplicate bank transactions, missing documents, review actions, exports, analytics and workflow persistence.

Frontend production build:

```powershell
cd frontend
npm run build
```

## Known limitations / next steps

- The initial release uses TF-IDF retrieval, not a neural embedding model; this keeps the setup lighter on an 8 GB RAM laptop.
- The application has no authentication or multi-user access control; run it locally or add access controls before exposing it.
- The transaction CSV schema is intentionally small. Invoice audit covers invoice-to-PO/receipt/payment matching, but it is not a full ERP or general ledger.
- Scanned-image OCR is not included. PDF text extraction requires `pypdf`; image-only PDFs are routed to human review.
- The high-value threshold is a simple configurable code default, not a universal accounting rule.
- Findings may overlap; the flagged amount KPI can double-count the same transaction across different rules.
- Currencies are never converted automatically. Different currencies produce a review exception.
- Do not upload confidential production finance data. Use synthetic or anonymized examples.
- LLM output can be incorrect. Review source documents and human-approve any financial action.
- Groq explanations send retrieved policy snippets and finding evidence to Groq's API. Do not use confidential production data unless that transfer is approved.

## Deployment notes

The frontend can be built with `npm run build` and deployed as static assets to a free static host. The backend can be deployed to a free Python web service if the provider supports persistent storage, but free instances may sleep or reset local files. SQLite is suitable for a demo, not a multi-instance production deployment. Configure `GROQ_API_KEY` as a backend environment variable/secret on the host. For a shareable demo, deploy the UI and API separately, configure `VITE_API_URL` to the API base URL, update backend CORS to the deployed frontend origin, and use synthetic sample data. Verify current free-tier limits before choosing a provider.

## Resume bullet (edit to match what you actually complete)

**FinAudit AI | Python, FastAPI, SQLite, React, RAG, Groq**
- Built a local-first financial audit application that validates CSV transactions and flags potential duplicate, high-value, and negative-amount exceptions using explainable Python rules.
- Implemented retrieval-augmented investigation notes using TF-IDF retrieval over audit policies and transaction evidence, with optional Groq LLM explanations and source references.
- Built an accounts-payable invoice audit module with deterministic duplicate, PO, goods-receipt, tax, payment and vendor-pattern checks, human review actions, CSV/PDF exports and 30 additional regression tests.
- Implemented a ten-node LangGraph invoice workflow with persisted timelines, evidence and human-review checkpoints, plus REST APIs, SQLite persistence, CSV/PDF exports, a React dashboard, and automated tests for audit-rule behavior.

## License

MIT — see `LICENSE`.
