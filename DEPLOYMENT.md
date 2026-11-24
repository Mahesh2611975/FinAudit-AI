# FinAudit AI — Deployment Guide (Vercel + Render + Neon PostgreSQL)

This document describes how to **prepare and publish** FinAudit AI using:

- **Frontend:** Vite/React on **Vercel**
- **Backend:** FastAPI in a container on **Render Web Services**
- **Database:** **Neon** PostgreSQL (or Render PostgreSQL)

> **Deployment status:** configuration is prepared and verified locally. No live
> deployment has been published because hosting credentials, a database
> connection string, a reachable inference host and explicit authorization were
> not available. Do not treat this document as proof of a running deployment.

---

## 1. Architecture and constraints

```
Browser ──HTTPS──> Vercel (React/Vite static build)
                        │  VITE_API_URL
                        ▼
                  Render (FastAPI, Docker)
                        │  FINAUDIT_DATABASE_URL
                        ▼
                  Neon PostgreSQL
```

Important constraints (read before deploying):

1. **Authentication is enabled and enforced.** Sign-in issues signed JWTs and
   protected API routes use authentication and the configured role-based
   permission policies. A non-local
   deployment **must** set a strong `FINAUDIT_JWT_SECRET` (at least 32 random
   characters). The backend fails at startup if the secret is missing or weak;
   there is no production fallback to a development secret. Demo sessions are
   always refused outside local environments. Enforce HTTPS and keep secrets
   in the hosting dashboards, never in source control.
2. **Ollama is local by default.** A `127.0.0.1` Ollama on a developer laptop is
   not reachable from Render. Either point `OLLAMA_HOST` at a hosted,
   Ollama-compatible endpoint you control, or leave it unavailable: the app then
   uses deterministic rules and evidence-only answers and clearly reports AI as
   unavailable. It never fabricates results.
3. **The RAG store is local SQLite files** (`data/rag`). On Render's ephemeral
   filesystem these files do not survive a redeploy. Re-ingest documents after
   each deploy, or mount persistent storage. Structured evidence (transactions,
   alerts, cases, reconciliation) lives in PostgreSQL and is durable.
4. **No secrets in source.** All secrets are environment variables set in the
   hosting dashboards. The frontend only receives the public backend URL.

---

## 2. Local verification (do this first)

Run these from the repository root before any deployment:

```bash
# Backend: all tests (isolated temp databases, no Ollama required)
cd backend && python3 -m pytest -q

# Frontend: production build
cd ../frontend && npm run build
```

Both must pass. The frontend build output is `frontend/dist`.

---

## 3. Neon PostgreSQL setup

1. Create a Neon project and a database (e.g. `finaudit`).
2. In the Neon dashboard, copy the **pooled** connection string. It looks like:
   ```
   postgresql://USER:PASSWORD@HOST-POOLER.REGION.aws.neon.tech/finaudit?sslmode=require
   ```
3. Keep this value secret. It becomes `FINAUDIT_DATABASE_URL` on Render.
4. **Schema creation is automatic and additive.** On backend startup the app
   calls `ensure_database_schema()`, which:
   - creates any missing tables via `SQLAlchemy create_all`,
   - adds missing columns with `ALTER TABLE ... ADD COLUMN`,
   - creates missing indexes,
   - never drops tables, columns or data.
   A SQLite backup is only created for local SQLite databases; for PostgreSQL,
   take a Neon snapshot manually before any upgrade that changes the schema.

---

## 4. Backend deployment on Render

### Option A — Render Blueprint (recommended)

1. Push this repository to your Git provider (GitHub/GitLab).
2. Render Dashboard → **Blueprints** → **New Blueprint Instance** → select the repo.
3. `render.yaml` defines a Docker web service using `backend/Dockerfile`.
4. Fill in the secret environment variables when prompted:
   - `FINAUDIT_ENV` — `production` (already set in `render.yaml`). For staging,
     use `staging`; the same fail-closed JWT rules apply.
   - `FINAUDIT_JWT_SECRET` — **required**. Generate a unique value with
     `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste it
     into the Render dashboard only. Use a different secret for staging and
     production. Rotating it signs out all existing sessions.
   - `FINAUDIT_LOCAL_DEMO_MODE` — `false` (already set in `render.yaml`).
   - `FINAUDIT_DATABASE_URL` — the Neon pooled connection string.
   - `FINAUDIT_CORS_ORIGINS` — your Vercel frontend origin (add after step 5).
   - `OLLAMA_HOST` — your hosted inference endpoint, or leave blank for
     evidence-only mode.
5. Deploy. Render builds the image and starts uvicorn on port 8000. If the JWT
   secret is missing or too weak, startup aborts with a clear
   `FINAUDIT_JWT_SECRET` error in the service log and the health check fails.
6. Health check: `GET https://<your-render-host>/api/health` → `{"status":"healthy"}`.
   Detailed status is at `GET /api/system/health`.

### Option B — Manual Render Web Service

1. Render Dashboard → **New** → **Web Service** → connect the repo.
2. Environment: **Docker**. Docker File Path: `backend/Dockerfile`.
   Build Context: repository root (`.`).
3. Plan: Starter or above. Health Check Path: `/api/health`.
4. Add the same environment variables as Option A.
5. Deploy.

### Verify the backend directly

```bash
curl https://<your-render-host>/api/health
curl https://<your-render-host>/api/system/health
curl https://<your-render-host>/api/reports/summary
```

`system/health` reports the real database backend (`postgresql`), table count and
record counts. It never returns secrets.

---

## 5. Frontend deployment on Vercel

1. Import the repository in Vercel as a new project.
2. Set the **Root Directory** to `frontend` (so Vite finds `index.html` and
   `package.json` there).
3. Framework preset: **Vite** (`vercel.json` already sets the build command
   `npm run build` and output directory `dist`).
4. Add one environment variable:
   - `VITE_API_URL` = `https://<your-render-host>`
5. Deploy.
6. Copy the Vercel deployment URL (e.g. `https://finaudit-abc123.vercel.app`) and
   set it as `FINAUDIT_CORS_ORIGINS` on Render, then redeploy the backend.

> The frontend embeds `VITE_API_URL` at build time. After changing it, trigger a
> fresh Vercel build (Deployments → Redeploy).

---

## 6. Smoke-test checklist (after both deployments)

Work through this list against the **public URLs**. Every check must pass before
calling the deployment healthy.

| # | Check | Expected |
|---|-------|----------|
| 1 | `GET /api/health` (Render) | `{"status":"healthy"}` |
| 2 | `GET /api/system/health` | `status: operational`, `database.backend: postgresql`, `database.status: connected`, `configuration.authentication_enabled: true`, no secrets |
| 2a | `GET /api/auth/demo/status` | `enabled: false` in production/staging; `POST /api/auth/demo/session` returns 403 |
| 3 | Open the Vercel URL in a browser | Dashboard renders; no blank page or console errors |
| 4 | Upload a sample CSV (Transactions/Audit page) | Rows imported; dashboard transaction count increases |
| 5 | Refresh the page | Counts persist (proves PostgreSQL durability) |
| 6 | Reconciliation: upload bank + ledger CSVs with mapping | Results show matched/unmatched; mappings echoed |
| 7 | Confirm or reject a reconciliation result as a named reviewer | Review status recorded; result export reflects it |
| 8 | Run deterministic risk analysis | Alerts appear with category, score and evidence |
| 9 | Create an investigation from a risk alert | Case created with alert + transaction evidence |
| 10 | Add a note and attach evidence to the case | Both appear in case detail and case CSV export |
| 11 | Ingest a document in AI Assistant → Knowledge sources | Chunks > 0; status `indexed` |
| 12 | Run "Test retrieval" on a known fact | Matching chunk returned with filename/chunk id |
| 13 | Ask "Generate an investigation report." | Five roles in `required_roles`; evidence-only fallback if `OLLAMA_HOST` is unavailable; answer cites real evidence |
| 14 | Settings page | Health values match `/api/system/health`; no secrets shown |
| 15 | Reports page | Full PDF/CSV, filtered transaction CSV, risk alert CSV all download; metadata rows include generated_at/provenance |
| 16 | Re-deploy the backend | Data from steps 4–10 still present; RAG document from step 11 needs re-ingestion (ephemeral store) |

If AI is unavailable (step 13), confirm the Settings page and assistant pill show
**Ollama offline / evidence-only** rather than pretending a model is running.

---

## 7. Local Docker smoke test (optional)

```bash
# From the repository root
docker build -f backend/Dockerfile -t finaudit-api .
docker run --rm -p 8000:8000 \
  -e FINAUDIT_ENV=local \
  -e FINAUDIT_DATABASE_URL=sqlite:////app/data/finaudit.db \
  finaudit-api
curl http://127.0.0.1:8000/api/health
```

Note: this uses a SQLite file inside the container (ephemeral) and the explicit
local environment, where the development-only JWT fallback is permitted. It only
validates the image and startup/migration path, not production durability. To
exercise the production configuration locally, also pass
`-e FINAUDIT_ENV=production -e FINAUDIT_JWT_SECRET=<generated-secret>`; omitting
the secret must abort startup with a clear error.

---

## 8. Troubleshooting

- **`FINAUDIT_JWT_SECRET is required` / `is too weak` at startup:** the backend is
  correctly failing closed. Set a unique secret of at least 32 random characters
  (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) in the hosting
  dashboard, confirm `FINAUDIT_ENV` is `production`/`staging`, then redeploy. Do
  not set it to a development placeholder or commit the value.
- **All sign-ins return 503 after a deploy:** the JWT secret was removed or
  changed while the app was running, or the environment changed to non-local.
  Restore the dashboard secret and redeploy; existing tokens signed with an old
  secret are intentionally invalid.
- **CORS errors in the browser:** `FINAUDIT_CORS_ORIGINS` on Render must exactly
  match the Vercel origin (scheme + host, no trailing slash). Redeploy the backend
  after changing it.
- **Database connection errors:** confirm the Neon string uses `sslmode=require`
  and the pooler host. Check Render logs for the SQLAlchemy connection error.
- **AI always "offline":** `OLLAMA_HOST` is unset or unreachable from Render.
  This is expected unless you provide a reachable inference host. Deterministic
  features still work.
- **RAG empty after redeploy:** the RAG store is a local SQLite file on the
  ephemeral Render filesystem. Re-ingest documents, or mount persistent storage.
- **Migrations:** additive only. If a column add fails on PostgreSQL, check that
  the Neon role has DDL permissions and take a snapshot before retrying.
