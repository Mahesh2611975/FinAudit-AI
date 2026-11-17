# Production image for the FinAudit AI FastAPI backend.
# Designed for Render Web Services, but works on any container host.
#
# Build from the REPOSITORY ROOT so the backend sources are available:
#   docker build -f backend/Dockerfile -t finaudit-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq is needed by psycopg2 (PostgreSQL). psycopg2-binary ships manylinux
# wheels, so no compiler toolchain is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend/app/ ./app/

# Runtime data directory (SQLite fallback / RAG store). On Render this is
# ephemeral; set FINAUDIT_DATABASE_URL to PostgreSQL for durable storage.
RUN mkdir -p /app/data /app/data/rag

EXPOSE 8000

# The app applies additive migrations and creates missing tables at import
# time (see app/main.py). Uvicorn binds to 0.0.0.0 for container networking.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
