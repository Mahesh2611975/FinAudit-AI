import json
import os
import re
from datetime import datetime, timezone
from io import BytesIO, StringIO

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect, or_, text
from sqlalchemy.orm import Session

from .analytics import AnalyticsInputError, monthly_analysis, monthly_comparison
from .audit_engine import detect_findings
from .csv_normalizer import COLUMN_ALIASES, normalize_files, safe_filename
from .database import Base, engine, get_db
from .llm import explain_finding
from .migrations import backup_sqlite_database, ensure_database_schema, has_pending_schema_changes
from .models import AuditRun, Finding, ReconciliationResult, ReconciliationRun, ReconciliationReviewAction, RiskAlert, TransactionRecord, InvestigationRun, InvestigationCase
from .invoice_api import router as invoice_router
from .investigation_api import (
    InvestigationCreate,
    create_investigation,
    list_investigations,
    router as investigation_router,
)
from .risk_api import router as risk_router
from .reconciliation import ReconciliationError, reconcile_files, STATUSES as RECON_STATUSES
from .category import categorize_transaction
from .dashboard import dashboard_overview
from . import llm, rag
from .agents.assistant_graph import get_assistant_graph
from .pdf_utils import build_text_pdf

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 20


def _optional_float(value):
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

if has_pending_schema_changes(engine, Base.metadata):
    backup_sqlite_database(engine, label="pre-migration")
SCHEMA_REPORT = ensure_database_schema(engine, Base.metadata)


def _cors_origins() -> list[str]:
    configured = os.getenv("FINAUDIT_CORS_ORIGINS", "")
    origins = [item.strip() for item in configured.split(",") if item.strip()]
    if origins:
        return origins
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]


app = FastAPI(title="FinAudit AI", version="1.0.0", description="Local-first financial audit exception analysis with RAG-assisted explanations.")
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(invoice_router)
app.include_router(risk_router)
app.include_router(investigation_router)


@app.get("/")
def root():
    return {"name": "FinAudit AI", "status": "running", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {"status": "healthy"}


@app.get("/api/system/health")
def system_health(db: Session = Depends(get_db)):
    """Consolidated, secret-free status for the Settings screen.

    Every reported value is derived from an actual probe; no credentials,
    connection strings or environment values are exposed.
    """
    database = {"status": "unavailable", "backend": engine.url.get_backend_name()}
    try:
        db.execute(text("SELECT 1")).scalar()
        database = {
            "status": "connected",
            "backend": engine.url.get_backend_name(),
            "tables": len(inspect(engine).get_table_names()),
            "transaction_records": int(db.query(TransactionRecord).count()),
            "risk_alerts": int(db.query(RiskAlert).count()),
            "reconciliation_runs": int(db.query(ReconciliationRun).count()),
            "investigation_cases": int(db.query(InvestigationCase).count()),
        }
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        database["error"] = f"{type(exc).__name__}: {exc}"

    llm_available = llm.is_available()
    rag_stats = rag.store_stats()
    rag_chunks = int(rag_stats.get("chunks") or 0)
    configured = bool(os.getenv("FINAUDIT_CORS_ORIGINS", "").strip())
    return {
        "status": "operational" if database["status"] == "connected" else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "backend": {"status": "running", "api": "healthy"},
        "database": database,
        "llm": {
            "provider": "ollama",
            "backend": "ollama",
            "available": llm_available,
            "connected": llm_available,
            "model": llm.get_model(),
            "status": "available" if llm_available else "unavailable",
            "note": None if llm_available else "Local Ollama is not reachable; deterministic rules and evidence-only answers remain available.",
        },
        "rag": {
            "backend": rag_stats.get("backend", "local-tfidf-sqlite"),
            "documents": int(rag_stats.get("documents") or 0),
            "chunks": rag_chunks,
            "index_exists": bool(rag_stats.get("index_exists")),
            "status": "indexed" if rag_chunks > 0 else "empty",
        },
        "configuration": {
            "database_url_configured": bool(os.getenv("FINAUDIT_DATABASE_URL", "").strip()),
            "cors_origins_configured": configured,
            "ai_provider_configurable": True,
            "authentication_enabled": False,
            "secrets_exposed": False,
        },
        "deterministic_engine": "operational",
    }


def _files_for_run(run: AuditRun) -> list[str]:
    if run.source_files:
        try:
            parsed = json.loads(run.source_files)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (TypeError, ValueError):
            pass
    return [run.filename] if run.filename else []


def _header_mapping(columns) -> list[dict[str, str]]:
    """Show the automatic column mapping applied to a uploaded CSV."""
    reverse: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in (*aliases, canonical):
            normalized = re.sub(r"[^a-z0-9]+", "_", str(alias).lower()).strip("_")
            reverse.setdefault(normalized, canonical)
    mapping = []
    for column in columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
        mapping.append({"source": str(column), "canonical": reverse.get(normalized, normalized)})
    return mapping


def _existing_import_keys(db: Session) -> tuple[set[str], set[tuple[str, int]]]:
    rows = db.query(TransactionRecord).all()
    keys = {str(row.duplicate_key) for row in rows if row.duplicate_key}
    provenance = {
        (str(row.source_file), int(row.source_row))
        for row in rows
        if row.source_file and row.source_row is not None
    }
    return keys, provenance


def _is_duplicate_row(row, keys: set[str], provenance: set[tuple[str, int]]) -> bool:
    key = str(row.get("duplicate_key") or "")
    source_file = str(row.get("source_file") or "")
    source_row = row.get("source_row")
    if key and key in keys:
        return True
    if source_file and source_row is not None:
        try:
            if (source_file, int(source_row)) in provenance:
                return True
        except (TypeError, ValueError):
            pass
    return False


@app.post("/api/transactions/preview")
async def preview_transactions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Validate a CSV and preview normalized rows before importing it."""
    filename = safe_filename(file.filename)
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Upload a .csv file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="CSV is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="CSV must be 5 MB or smaller.")
    try:
        frame = pd.read_csv(BytesIO(raw), encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read CSV: {exc}") from exc

    normalized = normalize_files([(filename, frame)])
    keys, provenance = _existing_import_keys(db)
    rows = normalized.frame.to_dict("records")
    duplicate_count = sum(1 for row in rows if _is_duplicate_row(row, keys, provenance))
    preview = []
    for row in rows[:10]:
        item = {
            key: (None if pd.isna(value) else value)
            for key, value in row.items()
            if key in {
                "transaction_id", "date", "amount", "direction", "vendor", "description",
                "source_file", "source_row", "duplicate_key",
            }
        }
        item["duplicate"] = _is_duplicate_row(row, keys, provenance)
        preview.append(item)
    return {
        "valid": not normalized.errors,
        "filename": filename,
        "schema": normalized.schema,
        "mapping": _header_mapping(frame.columns),
        "row_count": len(normalized.frame),
        "duplicate_count": duplicate_count,
        "errors": normalized.errors,
        "warnings": normalized.warnings,
        "preview": preview,
    }


@app.post("/api/audits")
async def create_audit(
    files: list[UploadFile] | None = File(None),
    file: UploadFile | None = File(None),
    # Repeated uploads are skipped by default so re-importing a statement never
    # creates duplicate transaction rows. Callers may opt out explicitly when a
    # genuinely fresh copy of the same file must be analysed as a new snapshot.
    skip_duplicates: bool = Form(True),
    db: Session = Depends(get_db),
):
    uploads = []
    if file is not None:
        uploads.append(file)
    if files:
        uploads.extend(files)
    if not uploads:
        raise HTTPException(status_code=400, detail="Upload at least one .csv file.")
    if len(uploads) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Upload no more than {MAX_FILES} CSV files at once.")

    frames: list[tuple[str, pd.DataFrame]] = []
    for upload in uploads:
        filename = safe_filename(upload.filename)
        if not filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail=f"{filename}: upload a .csv file.")
        raw = await upload.read()
        if not raw:
            raise HTTPException(status_code=422, detail=f"{filename}: CSV is empty.")
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{filename}: CSV must be 5 MB or smaller.")
        try:
            frame = pd.read_csv(BytesIO(raw), encoding="utf-8-sig")
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{filename}: could not read CSV. Check that it is a comma-separated UTF-8 file.",
            ) from exc
        frames.append((filename, frame))

    normalized = normalize_files(frames)
    if normalized.errors:
        raise HTTPException(status_code=422, detail=normalized.errors)

    skipped_duplicates = 0
    if skip_duplicates:
        keys, provenance = _existing_import_keys(db)
        mask = normalized.frame.apply(
            lambda row: not _is_duplicate_row(row, keys, provenance), axis=1
        )
        skipped_duplicates = int((~mask).sum())
        normalized.frame = normalized.frame.loc[mask].reset_index(drop=True)

    findings = detect_findings(normalized.frame) if not normalized.frame.empty else []

    display_name = ", ".join(normalized.files)
    if len(display_name) > 255:
        display_name = display_name[:252] + "..."
    run = AuditRun(
        filename=display_name,
        source_files=json.dumps(normalized.files),
        row_count=len(normalized.frame),
        finding_count=len(findings),
    )
    db.add(run)
    db.flush()
    for item in findings:
        db.add(Finding(run_id=run.id, **item))
    # Persist normalized transactions for analytics
    for _, row in normalized.frame.iterrows():
        try:
            cat, _ = categorize_transaction(
                description=str(row.get("description") or ""),
                vendor=str(row.get("vendor") or ""),
                upi_ref=str(row.get("upi_ref") or ""),
            )
        except Exception:
            cat = "Uncategorized"
        db.add(
            TransactionRecord(
                audit_run_id=run.id,
                transaction_id=str(row.get("transaction_id") or "") or None,
                date=str(row.get("date") or "") or None,
                amount=float(row.get("amount")) if row.get("amount") is not None else None,
                currency=str(row.get("currency") or "").upper() or None,
                signed_amount=float(row.get("signed_amount")) if row.get("signed_amount") is not None else None,
                direction=str(row.get("direction") or "") or None,
                type=str(row.get("type") or "") or None,
                vendor=str(row.get("vendor") or "") or None,
                merchant=str(row.get("merchant") or "") or None,
                description=str(row.get("description") or "") or None,
                category=cat,
                account=str(row.get("account") or "") or None,
                upi_ref=str(row.get("upi_ref") or "") or None,
                reference=str(row.get("upi_ref") or "") or None,
                channel=str(row.get("channel") or "") or None,
                channel_source=str(row.get("channel_source") or "unknown") or "unknown",
                channel_original=str(row.get("channel_original") or "") or None,
                imported_risk_level=str(row.get("imported_risk_level") or "") or None,
                country=str(row.get("country") or "") or None,
                state=str(row.get("state") or "") or None,
                city=str(row.get("city") or "") or None,
                latitude=_optional_float(row.get("latitude")),
                longitude=_optional_float(row.get("longitude")),
                location_status=str(row.get("location_status") or "unavailable") or "unavailable",
                source_file=str(row.get("source_file") or "") or None,
                source_row=int(row.get("source_row")) if row.get("source_row") is not None else None,
                duplicate_key=str(row.get("duplicate_key") or "") or None,
            )
        )
    db.commit()
    db.refresh(run)
    return {
        "audit_id": run.id,
        "filename": run.filename,
        "files": normalized.files,
        "row_count": run.row_count,
        "finding_count": run.finding_count,
        "skipped_duplicates": skipped_duplicates,
        "warnings": normalized.warnings,
        "findings": [finding_dict(item) for item in run.findings],
    }


def finding_dict(f: Finding):
    return {
        "id": f.id,
        "run_id": f.run_id,
        "transaction_id": f.transaction_id,
        "source_file": f.source_file,
        "finding_type": f.finding_type,
        "risk_level": f.risk_level,
        "amount": f.amount,
        "summary": f.summary,
        "evidence": f.evidence,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


@app.get("/api/audits")
def list_audits(db: Session = Depends(get_db)):
    runs = db.query(AuditRun).order_by(AuditRun.id.desc()).limit(50).all()
    return [
        {
            "id": run.id,
            "audit_id": run.id,
            "filename": run.filename,
            "files": _files_for_run(run),
            "row_count": run.row_count,
            "finding_count": run.finding_count,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "findings": [finding_dict(item) for item in run.findings],
        }
        for run in runs
    ]


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: int, db: Session = Depends(get_db)):
    run = db.query(AuditRun).filter(AuditRun.id == audit_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Audit not found")
    return {
        "audit_id": run.id,
        "filename": run.filename,
        "files": _files_for_run(run),
        "row_count": run.row_count,
        "finding_count": run.finding_count,
        "findings": [finding_dict(item) for item in run.findings],
    }


@app.post("/api/findings/{finding_id}/explain")
async def explain(finding_id: int, db: Session = Depends(get_db)):
    finding = db.query(Finding).filter(Finding.id == finding_id).first()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return await explain_finding(
        f"Explain this audit finding: {finding.finding_type}. {finding.summary}",
        finding.evidence,
    )


@app.get("/api/audits/{audit_id}/export.csv")
def export_audit(audit_id: int, db: Session = Depends(get_db)):
    run = db.query(AuditRun).filter(AuditRun.id == audit_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Audit not found")
    rows = [finding_dict(item) for item in run.findings]
    columns = [
        "id",
        "transaction_id",
        "source_file",
        "finding_type",
        "risk_level",
        "amount",
        "summary",
        "evidence",
    ]
    output = StringIO()
    pd.DataFrame(rows, columns=columns).to_csv(output, index=False)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=finaudit_findings_{audit_id}.csv"},
    )


async def _read_upload_csv(upload: UploadFile, label: str) -> tuple[str, pd.DataFrame]:
    filename = safe_filename(upload.filename)
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail=f"{label}: upload a .csv file.")
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=422, detail=f"{label}: CSV is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{label}: CSV must be 5 MB or smaller.",
        )
    try:
        frame = pd.read_csv(BytesIO(raw), encoding="utf-8-sig")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{label}: could not read CSV. Check that it is a comma-separated "
                "UTF-8 file."
            ),
        ) from exc
    return filename, frame


RECONCILIATION_MAPPING_KEYS = {
    "date", "amount", "debit", "credit", "withdrawal", "deposit",
    "reference", "description", "type", "direction",
}


def _apply_reconciliation_mapping(
    frame: pd.DataFrame, raw_mapping: str | None, label: str
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Rename user-supplied source columns to the canonical engine names."""
    if not raw_mapping:
        return frame.copy(), {}
    try:
        mapping = json.loads(raw_mapping)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{label}: mapping must be valid JSON.") from exc
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=422, detail=f"{label}: mapping must be a JSON object.")
    canonical: dict[str, str] = {}
    for key, source in mapping.items():
        key_text = str(key).strip().lower()
        source_text = str(source or "").strip()
        if key_text not in RECONCILIATION_MAPPING_KEYS:
            raise HTTPException(
                status_code=422,
                detail=f"{label}: unsupported mapping field '{key}'.",
            )
        if source_text not in frame.columns:
            raise HTTPException(
                status_code=422,
                detail=f"{label}: mapped column '{source_text}' was not found in the CSV.",
            )
        if key_text in canonical and canonical[key_text] != source_text:
            raise HTTPException(
                status_code=422,
                detail=f"{label}: only one source column can map to '{key_text}'.",
            )
        canonical[key_text] = source_text
    if not canonical:
        return frame.copy(), {}
    reverse = {source: target for target, source in canonical.items()}
    if len(reverse) != len(canonical):
        raise HTTPException(
            status_code=422,
            detail=f"{label}: one source column cannot be mapped to multiple fields.",
        )
    return frame.rename(columns=reverse).copy(), canonical


def _recon_count(run: ReconciliationRun, status: str) -> int:
    attr = f"{status}_count"
    if hasattr(run, attr):
        value = getattr(run, attr)
        if value is not None:
            return int(value)
    return 0


def reconciliation_summary(run: ReconciliationRun) -> dict:
    matched_count = _recon_count(run, "matched")
    bank_only_count = _recon_count(run, "bank_only")
    ledger_only_count = _recon_count(run, "ledger_only")
    amount_mismatch_count = _recon_count(run, "amount_mismatch")
    date_mismatch_count = _recon_count(run, "date_mismatch")
    ambiguous_count = _recon_count(run, "ambiguous")
    invalid_record_count = _recon_count(run, "invalid_records") or int(getattr(run, "invalid_record_count", 0) or 0)
    duplicate_candidate_count = _recon_count(run, "duplicate_candidate") or int(getattr(run, "duplicate_candidate_count", 0) or 0)
    unmatched_count = int(getattr(run, "unmatched_count", 0) or (bank_only_count + ledger_only_count))
    exception_count = int(getattr(run, "exception_count", 0) or (amount_mismatch_count + date_mismatch_count + ambiguous_count + invalid_record_count + duplicate_candidate_count))
    bank_record_count = int(getattr(run, "bank_record_count", 0) or 0)
    ledger_record_count = int(getattr(run, "ledger_record_count", 0) or 0)
    denom = max(1.0, (bank_record_count + ledger_record_count) / 2.0) if (bank_record_count or ledger_record_count) else max(1.0, float(run.result_count or 1))
    match_rate = round((matched_count / denom) * 100, 2)
    return {
        "bank_total": run.bank_total,
        "ledger_total": run.ledger_total,
        "matched_total": run.matched_total,
        "bank_unmatched_total": run.bank_unmatched_total,
        "ledger_unmatched_total": run.ledger_unmatched_total,
        "unmatched_total": run.unmatched_total,
        "difference": run.difference,
        "reconciliation_difference": getattr(run, "difference", 0),
        "total_difference": getattr(run, "difference", 0),
        "result_count": run.result_count,
        "matched_count": matched_count,
        "bank_only_count": bank_only_count,
        "ledger_only_count": ledger_only_count,
        "amount_mismatch_count": amount_mismatch_count,
        "date_mismatch_count": date_mismatch_count,
        "ambiguous_count": ambiguous_count,
        "invalid_record_count": invalid_record_count,
        "duplicate_candidate_count": duplicate_candidate_count,
        "unmatched_count": unmatched_count,
        "exception_count": exception_count,
        "unreconciled_value": getattr(run, "unreconciled_value", None),
        "bank_record_count": bank_record_count,
        "ledger_record_count": ledger_record_count,
        "match_rate": match_rate,
    }


def reconciliation_result_dict(item: ReconciliationResult) -> dict:
    explanation = getattr(item, "explanation", None) or item.note
    return {
        "id": item.id,
        "run_id": item.run_id,
        "status": item.status,
        "match_method": getattr(item, "match_method", None),
        "explanation": explanation,
        "bank_date": item.bank_date,
        "bank_amount": item.bank_amount,
        "bank_direction": item.bank_direction,
        "bank_reference": item.bank_reference,
        "bank_description": item.bank_description,
        "bank_source_file": item.bank_source_file,
        "bank_source_row": item.bank_source_row,
        "ledger_date": item.ledger_date,
        "ledger_amount": item.ledger_amount,
        "ledger_direction": item.ledger_direction,
        "ledger_reference": item.ledger_reference,
        "ledger_description": item.ledger_description,
        "ledger_source_file": item.ledger_source_file,
        "ledger_source_row": item.ledger_source_row,
        "amount_difference": item.amount_difference,
        "date_difference_days": item.date_difference_days,
        "note": item.note,
        "review_status": item.review_status,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "reviewer_comment": item.reviewer_comment,
        "resolution_note": getattr(item, "resolution_note", None),
        "is_confirmed_match": bool(getattr(item, "is_confirmed_match", 0)),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if getattr(item, "updated_at", None) else None,
    }


RECONCILIATION_EXPORT_COLUMNS = [
    "id",
    "status",
    "match_method",
    "explanation",
    "bank_date",
    "bank_amount",
    "bank_direction",
    "bank_reference",
    "bank_description",
    "bank_source_file",
    "bank_source_row",
    "ledger_date",
    "ledger_amount",
    "ledger_direction",
    "ledger_reference",
    "ledger_description",
    "ledger_source_file",
    "ledger_source_row",
    "amount_difference",
    "date_difference_days",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "reviewer_comment",
    "resolution_note",
    "note",
]


def reconciliation_run_dict(run: ReconciliationRun, include_results: bool = False, include_actions: bool = False) -> dict:
    data = {
        "id": run.id,
        "reconciliation_id": run.id,
        "run_id": run.id,
        "bank_file": run.bank_filename,
        "ledger_file": run.ledger_filename,
        "files": [run.bank_filename, run.ledger_filename],
        "date_tolerance_days": run.date_tolerance_days,
        "amount_tolerance": getattr(run, "amount_tolerance", 0.005),
        "run_status": getattr(run, "run_status", "completed"),
        "bank_mapping": run.bank_mapping or {},
        "ledger_mapping": run.ledger_mapping or {},
        "warnings": getattr(run, "warnings", None) or [],
        "summary": reconciliation_summary(run),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if getattr(run, "updated_at", None) else None,
    }
    if include_results:
        data["results"] = [reconciliation_result_dict(item) for item in run.results]
    if include_actions:
        actions = getattr(run, "review_actions", []) or []
        data["review_actions"] = [reconciliation_action_dict(a) for a in sorted(actions, key=lambda x: (x.created_at or datetime.min, x.id or 0), reverse=True)]
    return data


def reconciliation_action_dict(action: ReconciliationReviewAction) -> dict:
    return {
        "id": action.id,
        "run_id": action.run_id,
        "result_id": action.result_id,
        "action": action.action,
        "actor": action.actor,
        "comment": action.comment,
        "previous_status": action.previous_status,
        "new_status": action.new_status,
        "previous_review_status": action.previous_review_status,
        "new_review_status": action.new_review_status,
        "details": action.details_json or {},
        "created_at": action.created_at.isoformat() if action.created_at else None,
    }


def _record_source_key(result: ReconciliationResult, side: str) -> tuple[str, str, int] | None:
    if side == "bank":
        if result.bank_source_file and result.bank_source_row:
            return ("bank", str(result.bank_source_file), int(result.bank_source_row))
        return None
    if side == "ledger":
        if result.ledger_source_file and result.ledger_source_row:
            return ("ledger", str(result.ledger_source_file), int(result.ledger_source_row))
        return None
    return None


def _recon_confirmed_conflict(db: Session, run_id: int, candidate: ReconciliationResult, exclude_ids: set[int] | None = None) -> str | None:
    exclude_ids = exclude_ids or set()
    confirmed = (
        db.query(ReconciliationResult)
        .filter(
            ReconciliationResult.run_id == run_id,
            ReconciliationResult.is_confirmed_match == 1,
        )
        .all()
    )
    candidate_bank = _record_source_key(candidate, "bank")
    candidate_ledger = _record_source_key(candidate, "ledger")
    for row in confirmed:
        if row.id in exclude_ids or row.id == candidate.id:
            continue
        if candidate_bank and candidate_bank == _record_source_key(row, "bank"):
            return "Bank record is already linked to a confirmed match."
        if candidate_ledger and candidate_ledger == _record_source_key(row, "ledger"):
            return "Ledger record is already linked to a confirmed match."
    return None


def _log_reconciliation_action(
    db: Session,
    run_id: int,
    result_id: int | None,
    action: str,
    actor: str,
    comment: str | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    previous_review_status: str | None = None,
    new_review_status: str | None = None,
    details: dict | None = None,
):
    db.add(
        ReconciliationReviewAction(
            run_id=run_id,
            result_id=result_id,
            action=action,
            actor=actor,
            comment=comment,
            previous_status=previous_status,
            new_status=new_status,
            previous_review_status=previous_review_status,
            new_review_status=new_review_status,
            details_json=details or {},
            created_at=datetime.now(timezone.utc),
        )
    )


def _refresh_reconciliation_run(db: Session, run: ReconciliationRun):
    results = list(run.results or db.query(ReconciliationResult).filter(ReconciliationResult.run_id == run.id).all())
    counts = {status: 0 for status in RECON_STATUSES}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    run.matched_count = counts.get("matched", 0)
    run.bank_only_count = counts.get("bank_only", 0)
    run.ledger_only_count = counts.get("ledger_only", 0)
    run.amount_mismatch_count = counts.get("amount_mismatch", 0)
    run.date_mismatch_count = counts.get("date_mismatch", 0)
    run.ambiguous_count = counts.get("ambiguous", 0)
    run.invalid_record_count = counts.get("invalid_records", 0)
    run.duplicate_candidate_count = counts.get("duplicate_candidate", 0)
    run.result_count = len(results)
    run.unmatched_count = run.bank_only_count + run.ledger_only_count
    run.exception_count = (
        run.amount_mismatch_count
        + run.date_mismatch_count
        + run.ambiguous_count
        + run.invalid_record_count
        + run.duplicate_candidate_count
    )
    # derive totals from results where possible if counts fields missing; keep stored bank/ledger totals/record counts
    reconciled_results = [r for r in results if r.status == "matched" or bool(getattr(r, "is_confirmed_match", 0))]
    run.matched_total = round(sum(float(r.bank_amount or r.ledger_amount or 0.0) for r in reconciled_results), 2)
    bank_open = [r for r in results if r.bank_amount is not None and r.status not in {"matched", "invalid_records", "duplicate_candidate"} and not bool(getattr(r, "is_confirmed_match", 0))]
    ledger_open = [r for r in results if r.ledger_amount is not None and r.status not in {"matched", "invalid_records", "duplicate_candidate"} and not bool(getattr(r, "is_confirmed_match", 0))]
    run.bank_unmatched_total = round(sum(float(r.bank_amount or 0.0) for r in bank_open), 2)
    run.ledger_unmatched_total = round(sum(float(r.ledger_amount or 0.0) for r in ledger_open), 2)
    run.unmatched_total = round(run.bank_unmatched_total + run.ledger_unmatched_total, 2)
    # unreconciled value based on difference between totals and reconciled amounts
    bank_reconciled = sum(float(r.bank_amount or 0.0) for r in reconciled_results if r.bank_amount is not None)
    ledger_reconciled = sum(float(r.ledger_amount or 0.0) for r in reconciled_results if r.ledger_amount is not None)
    run.unreconciled_value = round(abs(float(run.bank_total or 0.0) - bank_reconciled) + abs(float(run.ledger_total or 0.0) - ledger_reconciled), 2)
    run.updated_at = datetime.now(timezone.utc)


def _parse_reconciliation_inputs(
    bank_upload: tuple[str, pd.DataFrame],
    ledger_upload: tuple[str, pd.DataFrame],
    bank_mapping_raw: str | None,
    ledger_mapping_raw: str | None,
):
    bank_frame, applied_bank_mapping = _apply_reconciliation_mapping(bank_upload[1], bank_mapping_raw, "Bank statement")
    ledger_frame, applied_ledger_mapping = _apply_reconciliation_mapping(ledger_upload[1], ledger_mapping_raw, "Accounting ledger")
    return (bank_upload[0], bank_frame), (ledger_upload[0], ledger_frame), applied_bank_mapping, applied_ledger_mapping


@app.post("/api/reconciliations/preview")
async def preview_reconciliation(
    bank_file: UploadFile = File(...),
    ledger_file: UploadFile = File(...),
    date_tolerance_days: int = Form(3),
    amount_tolerance: float = Form(0.005),
    bank_mapping: str | None = Form(None),
    ledger_mapping: str | None = Form(None),
):
    if date_tolerance_days < 0 or date_tolerance_days > 365:
        raise HTTPException(status_code=400, detail="date_tolerance_days must be between 0 and 365.")
    if amount_tolerance < 0:
        raise HTTPException(status_code=400, detail="amount_tolerance must be non-negative.")
    bank_upload = await _read_upload_csv(bank_file, "Bank statement")
    ledger_upload = await _read_upload_csv(ledger_file, "Accounting ledger")
    bank, ledger, applied_bank_mapping, applied_ledger_mapping = _parse_reconciliation_inputs(bank_upload, ledger_upload, bank_mapping, ledger_mapping)
    try:
        outcome = reconcile_files(bank, ledger, date_tolerance_days=date_tolerance_days, amount_tolerance=amount_tolerance)
    except ReconciliationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    # preview before reconciliation: show parsed/validated/detected issues and proposed matching summary/results preview
    preview = outcome.preview
    return {
        "bank_file": bank[0],
        "ledger_file": ledger[0],
        "date_tolerance_days": outcome.date_tolerance_days,
        "amount_tolerance": outcome.amount_tolerance,
        "bank_mapping": applied_bank_mapping,
        "ledger_mapping": applied_ledger_mapping,
        "preview": preview,
        "proposed_summary": outcome.summary,
        "proposed_results": outcome.results,
        "warnings": outcome.warnings,
        "validation_errors": [],
    }


@app.post("/api/reconciliations")
async def create_reconciliation(
    bank_file: UploadFile = File(...),
    ledger_file: UploadFile = File(...),
    date_tolerance_days: int = Form(3),
    amount_tolerance: float = Form(0.005),
    bank_mapping: str | None = Form(None),
    ledger_mapping: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if date_tolerance_days < 0 or date_tolerance_days > 365:
        raise HTTPException(status_code=400, detail="date_tolerance_days must be between 0 and 365.")
    if amount_tolerance < 0:
        raise HTTPException(status_code=400, detail="amount_tolerance must be non-negative.")
    bank_upload = await _read_upload_csv(bank_file, "Bank statement")
    ledger_upload = await _read_upload_csv(ledger_file, "Accounting ledger")
    bank, ledger, applied_bank_mapping, applied_ledger_mapping = _parse_reconciliation_inputs(bank_upload, ledger_upload, bank_mapping, ledger_mapping)

    try:
        outcome = reconcile_files(bank, ledger, date_tolerance_days=date_tolerance_days, amount_tolerance=amount_tolerance)
    except ReconciliationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc

    summary = outcome.summary
    run = ReconciliationRun(
        bank_filename=bank[0],
        ledger_filename=ledger[0],
        date_tolerance_days=outcome.date_tolerance_days,
        amount_tolerance=outcome.amount_tolerance,
        run_status="completed",
        bank_record_count=int(summary.get("bank_record_count", 0)),
        ledger_record_count=int(summary.get("ledger_record_count", 0)),
        invalid_record_count=int(summary.get("invalid_record_count", 0) or summary.get("invalid_records_count", 0) or 0),
        duplicate_candidate_count=int(summary.get("duplicate_candidate_count", 0) or 0),
        unmatched_count=int(summary.get("unmatched_count", 0) or 0),
        exception_count=int(summary.get("exception_count", 0) or 0),
        unreconciled_value=float(summary.get("unreconciled_value", 0) or 0),
        bank_total=float(summary.get("bank_total", 0) or 0),
        ledger_total=float(summary.get("ledger_total", 0) or 0),
        matched_total=float(summary.get("matched_total", 0) or 0),
        bank_unmatched_total=float(summary.get("bank_unmatched_total", 0) or 0),
        ledger_unmatched_total=float(summary.get("ledger_unmatched_total", 0) or 0),
        unmatched_total=float(summary.get("unmatched_total", 0) or 0),
        difference=float(summary.get("difference", 0) or 0),
        result_count=int(summary.get("result_count", 0) or 0),
        matched_count=int(summary.get("matched_count", 0) or 0),
        bank_only_count=int(summary.get("bank_only_count", 0) or 0),
        ledger_only_count=int(summary.get("ledger_only_count", 0) or 0),
        amount_mismatch_count=int(summary.get("amount_mismatch_count", 0) or 0),
        date_mismatch_count=int(summary.get("date_mismatch_count", 0) or 0),
        ambiguous_count=int(summary.get("ambiguous_count", 0) or 0),
        bank_mapping=applied_bank_mapping,
        ledger_mapping=applied_ledger_mapping,
        warnings=outcome.warnings,
    )
    db.add(run)
    db.flush()
    for item in outcome.results:
        db.add(ReconciliationResult(run_id=run.id, **item))
    _log_reconciliation_action(
        db,
        run.id,
        None,
        "run_created",
        "system",
        "Reconciliation run created from uploaded bank and ledger files.",
        details={
            "bank_file": run.bank_filename,
            "ledger_file": run.ledger_filename,
            "date_tolerance_days": run.date_tolerance_days,
            "amount_tolerance": run.amount_tolerance,
            "result_count": run.result_count,
        },
    )
    db.commit()
    db.refresh(run)
    return {**reconciliation_run_dict(run, include_results=True), "preview": outcome.preview}


@app.get("/api/reconciliations")
def list_reconciliations(db: Session = Depends(get_db)):
    runs = db.query(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(50).all()
    return [reconciliation_run_dict(run, include_results=False) for run in runs]


@app.get("/api/reconciliations/{reconciliation_id}")
def get_reconciliation(reconciliation_id: int, db: Session = Depends(get_db)):
    run = db.query(ReconciliationRun).filter(ReconciliationRun.id == reconciliation_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return reconciliation_run_dict(run, include_results=True, include_actions=True)


@app.get("/api/reconciliations/{reconciliation_id}/export.csv")
def export_reconciliation(reconciliation_id: int, db: Session = Depends(get_db)):
    run = db.query(ReconciliationRun).filter(ReconciliationRun.id == reconciliation_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    rows = [reconciliation_result_dict(item) for item in run.results]
    output = StringIO()
    pd.DataFrame(rows, columns=RECONCILIATION_EXPORT_COLUMNS).to_csv(output, index=False)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=finaudit_reconciliation_{reconciliation_id}.csv"},
    )


@app.post("/api/reconciliations/results/{result_id}/review")
def review_reconciliation_result(result_id: int, payload: dict, db: Session = Depends(get_db)):
    payload = payload or {}
    action = str(payload.get("action") or payload.get("decision") or "").strip().lower()
    reviewer = str(payload.get("reviewer") or payload.get("reviewed_by") or "").strip()
    comment = payload.get("comment")
    note = payload.get("note") or payload.get("resolution_note")
    if not reviewer:
        raise HTTPException(status_code=400, detail="reviewer is required")
    allowed = {"confirm", "confirm_match", "reject", "reject_match", "mark_explained", "explained", "reopen", "add_note", "note"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported review action: {action}")

    try:
        row = db.query(ReconciliationResult).filter(ReconciliationResult.id == result_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Reconciliation result not found")
        run = db.query(ReconciliationRun).filter(ReconciliationRun.id == row.run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Reconciliation run not found")
        prev_status = row.status
        prev_review = row.review_status
        now = datetime.now(timezone.utc)

        if action in {"confirm", "confirm_match"}:
            if row.bank_source_file is None or row.ledger_source_file is None:
                raise HTTPException(status_code=400, detail="Only paired bank/ledger results can be confirmed as a match. Use manual link for separate records.")
            if row.status in {"invalid_records", "duplicate_candidate", "bank_only", "ledger_only"}:
                raise HTTPException(status_code=400, detail="This result cannot be confirmed as a match.")
            row.is_confirmed_match = 1
            if row.status in {"ambiguous", "amount_mismatch", "date_mismatch"}:
                row.status = "matched"
                row.match_method = row.match_method or "manual_confirmation"
                row.explanation = row.explanation or row.note or "Confirmed by reviewer."
                row.note = row.explanation
            conflict = _recon_confirmed_conflict(db, run.id, row, exclude_ids={row.id})
            if conflict:
                raise HTTPException(status_code=409, detail=conflict)
            row.review_status = "confirmed"
            row.reviewed_by = reviewer
            row.reviewed_at = now
            row.reviewer_comment = comment
            if note:
                row.resolution_note = str(note)
            log_action = "confirm_match"
            log_comment = comment or note
        elif action in {"reject", "reject_match"}:
            row.is_confirmed_match = 0
            row.review_status = "rejected"
            row.reviewed_by = reviewer
            row.reviewed_at = now
            row.reviewer_comment = comment
            if note:
                row.resolution_note = str(note)
            log_action = "reject_match"
            log_comment = comment or note
        elif action in {"mark_explained", "explained"}:
            row.is_confirmed_match = 0
            row.review_status = "explained"
            row.reviewed_by = reviewer
            row.reviewed_at = now
            row.reviewer_comment = comment
            if note:
                row.resolution_note = str(note)
            log_action = "mark_explained"
            log_comment = comment or note
        elif action == "reopen":
            row.is_confirmed_match = 0
            row.review_status = "pending"
            row.reviewed_by = reviewer
            row.reviewed_at = now
            row.reviewer_comment = comment
            if note:
                row.resolution_note = str(note)
            log_action = "reopen_exception"
            log_comment = comment or note
        else:
            if note:
                row.resolution_note = (f"{row.resolution_note}\n{note}" if row.resolution_note else str(note))
            if comment:
                row.reviewer_comment = comment
            row.reviewed_by = reviewer
            row.reviewed_at = now
            row.review_status = row.review_status or "pending"
            log_action = "add_note"
            log_comment = comment or note

        row.updated_at = now
        _log_reconciliation_action(
            db,
            run.id,
            row.id,
            log_action,
            reviewer,
            log_comment,
            previous_status=prev_status,
            new_status=row.status,
            previous_review_status=prev_review,
            new_review_status=row.review_status,
            details={"result_id": row.id},
        )
        db.flush()
        db.refresh(run)
        run.results  # ensure loaded
        _refresh_reconciliation_run(db, run)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return {"result": reconciliation_result_dict(row), "run_summary": reconciliation_summary(run)}


@app.post("/api/reconciliations/{reconciliation_id}/manual-link")
def manual_link_reconciliation(reconciliation_id: int, payload: dict, db: Session = Depends(get_db)):
    payload = payload or {}
    reviewer = str(payload.get("reviewer") or "").strip()
    bank_result_id = payload.get("bank_result_id")
    ledger_result_id = payload.get("ledger_result_id")
    comment = payload.get("comment")
    note = payload.get("note") or payload.get("resolution_note")
    if not reviewer:
        raise HTTPException(status_code=400, detail="reviewer is required")
    if not bank_result_id or not ledger_result_id:
        raise HTTPException(status_code=400, detail="bank_result_id and ledger_result_id are required")

    try:
        run = db.query(ReconciliationRun).filter(ReconciliationRun.id == reconciliation_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        bank_row = db.query(ReconciliationResult).filter(ReconciliationResult.id == int(bank_result_id), ReconciliationResult.run_id == reconciliation_id).first()
        ledger_row = db.query(ReconciliationResult).filter(ReconciliationResult.id == int(ledger_result_id), ReconciliationResult.run_id == reconciliation_id).first()
        if not bank_row or not ledger_row:
            raise HTTPException(status_code=404, detail="Selected bank or ledger result not found")
        if bank_row.bank_source_file is None:
            raise HTTPException(status_code=400, detail="Selected bank result does not contain a bank record")
        if ledger_row.ledger_source_file is None:
            raise HTTPException(status_code=400, detail="Selected ledger result does not contain a ledger record")
        if bank_row.id == ledger_row.id:
            raise HTTPException(status_code=400, detail="Select different bank and ledger records")

        now = datetime.now(timezone.utc)
        amount_difference = None
        if bank_row.bank_amount is not None and ledger_row.ledger_amount is not None:
            amount_difference = round(float(bank_row.bank_amount) - float(ledger_row.ledger_amount), 2)
        date_difference_days = None
        try:
            if bank_row.bank_date and ledger_row.ledger_date:
                from datetime import date as _date
                date_difference_days = abs((_date.fromisoformat(bank_row.bank_date) - _date.fromisoformat(ledger_row.ledger_date)).days)
        except Exception:
            date_difference_days = None

        explanation = "Manually linked by reviewer as a confirmed match."
        if comment:
            explanation += f" Comment: {comment}"
        manual = ReconciliationResult(
            run_id=reconciliation_id,
            status="matched",
            match_method="manual_link",
            explanation=explanation,
            note=explanation,
            bank_date=bank_row.bank_date,
            bank_amount=bank_row.bank_amount,
            bank_direction=bank_row.bank_direction,
            bank_reference=bank_row.bank_reference,
            bank_description=bank_row.bank_description,
            bank_source_file=bank_row.bank_source_file,
            bank_source_row=bank_row.bank_source_row,
            ledger_date=ledger_row.ledger_date,
            ledger_amount=ledger_row.ledger_amount,
            ledger_direction=ledger_row.ledger_direction,
            ledger_reference=ledger_row.ledger_reference,
            ledger_description=ledger_row.ledger_description,
            ledger_source_file=ledger_row.ledger_source_file,
            ledger_source_row=ledger_row.ledger_source_row,
            amount_difference=amount_difference,
            date_difference_days=date_difference_days,
            review_status="confirmed",
            reviewed_by=reviewer,
            reviewed_at=now,
            reviewer_comment=comment,
            resolution_note=str(note) if note else None,
            is_confirmed_match=1,
        )
        db.add(manual)
        db.flush()
        conflict = _recon_confirmed_conflict(db, reconciliation_id, manual, exclude_ids={manual.id})
        if conflict:
            raise HTTPException(status_code=409, detail=conflict)

        for src, side in ((bank_row, "bank"), (ledger_row, "ledger")):
            prev_status = src.status
            prev_review = src.review_status
            src.is_confirmed_match = 0
            src.review_status = "linked"
            src.reviewed_by = reviewer
            src.reviewed_at = now
            src.reviewer_comment = comment
            if note:
                src.resolution_note = (f"{src.resolution_note}\n{note}" if src.resolution_note else str(note))
            src.updated_at = now
            _log_reconciliation_action(
                db,
                reconciliation_id,
                src.id,
                "manual_link_source",
                reviewer,
                comment or note,
                previous_status=prev_status,
                new_status=src.status,
                previous_review_status=prev_review,
                new_review_status=src.review_status,
                details={"linked_result_id": manual.id, "side": side},
            )

        _log_reconciliation_action(
            db,
            reconciliation_id,
            manual.id,
            "manual_link",
            reviewer,
            comment or note,
            previous_status=None,
            new_status=manual.status,
            previous_review_status=None,
            new_review_status=manual.review_status,
            details={"bank_result_id": bank_row.id, "ledger_result_id": ledger_row.id},
        )
        db.flush()
        db.refresh(run)
        run.results
        _refresh_reconciliation_run(db, run)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(manual)
    return {"result": reconciliation_result_dict(manual), "run_summary": reconciliation_summary(run)}


# Analytics endpoints
@app.get("/api/analytics/monthly/summary")
def analytics_monthly_summary(year: int, month: int, db: Session = Depends(get_db)):
    try:
        analysis = monthly_analysis(db, year, month)
    except AnalyticsInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = analysis["summary"]
    return {
        "year": year,
        "month": month,
        "total_spend": summary["total_spending"],
        "total_received": summary["total_credits"],
        "total_spending": summary["total_spending"],
        "total_credits": summary["total_credits"],
        "net_cash_flow": summary["net_cash_flow"],
        "transaction_count": summary["transaction_count"],
        "spending_transaction_count": summary["spending_transaction_count"],
        "credit_transaction_count": summary["credit_transaction_count"],
        "average_outgoing": summary["average_outgoing"],
        "largest_expense": summary["largest_expense"],
        "has_transactions": analysis["has_transactions"],
        "uncategorized_count": summary["uncategorized_count"],
        "missing_direction_count": summary["missing_direction_count"],
        "missing_amount_count": summary["missing_amount_count"],
        "linked_audit_run_ids": analysis["linked_audit_run_ids"],
        "data_quality_notes": analysis["data_quality"]["notes"],
    }


@app.get("/api/analytics/monthly/categories")
def analytics_monthly_categories(year: int, month: int, db: Session = Depends(get_db)):
    try:
        analysis = monthly_analysis(db, year, month)
    except AnalyticsInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return analysis["category_totals"]


@app.get("/api/analytics/transactions")
def analytics_transactions(year: int | None = None, month: int | None = None, category: str | None = None, q: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    query = db.query(TransactionRecord)
    if year and month:
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="month must be between 1 and 12")
        query = query.filter(TransactionRecord.date.like(f"{year:04d}-{month:02d}%"))
    if category:
        query = query.filter(TransactionRecord.category == category)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter((TransactionRecord.description.ilike(like)) | (TransactionRecord.vendor.ilike(like)) | (TransactionRecord.transaction_id.ilike(like)))
    txns = query.order_by(TransactionRecord.id.desc()).limit(min(limit, 1000)).all()
    return [
        {
            "id": t.id,
            "transaction_id": t.transaction_id,
            "date": t.date,
            "amount": t.amount,
            "signed_amount": t.signed_amount,
            "direction": t.direction,
            "vendor": t.vendor,
            "description": t.description,
            "category": t.category,
            "account": t.account,
            "upi_ref": t.upi_ref,
            "source_file": t.source_file,
            "source_row": t.source_row,
            "audit_run_id": t.audit_run_id,
        }
        for t in txns
    ]


@app.get("/api/analytics/monthly/comparison")
def analytics_monthly_comparison(year: int, month: int, db: Session = Depends(get_db)):
    try:
        return monthly_comparison(db, year, month)
    except AnalyticsInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analytics/export.csv")
def analytics_export_csv(year: int | None = None, month: int | None = None, db: Session = Depends(get_db)):
    query = db.query(TransactionRecord)
    if year and month:
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="month must be between 1 and 12")
        query = query.filter(TransactionRecord.date.like(f"{year:04d}-{month:02d}%"))
    txns = query.order_by(TransactionRecord.id.asc()).all()
    output = StringIO()
    cols = ["transaction_id", "date", "amount", "direction", "vendor", "description", "category", "account", "source_file", "source_row"]
    rows = []
    for t in txns:
        rows.append({
            "transaction_id": t.transaction_id,
            "date": t.date,
            "amount": t.amount,
            "direction": t.direction,
            "vendor": t.vendor,
            "description": t.description,
            "category": t.category,
            "account": t.account,
            "source_file": t.source_file,
            "source_row": t.source_row,
        })
    pd.DataFrame(rows, columns=cols).to_csv(output, index=False)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=finaudit_analytics.csv"})


# Multi-agent endpoints
from .agents.orchestrator import MultiAgentOrchestrator

_orch = MultiAgentOrchestrator()


@app.post("/api/agents/run")
def run_agents(payload: dict, db: Session = Depends(get_db)):
    try:
        if payload.get("task_type") == "invoice_audit":
            from .invoice_api import _invoice_orch
            return _invoice_orch.run(payload, persist=True)
        result = _orch.run(payload, persist=True)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/agents/runs")
def list_agent_runs(limit: int = 50, db: Session = Depends(get_db)):
    runs = db.query(InvestigationRun).order_by(InvestigationRun.id.desc()).limit(min(limit, 200)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "task_type": r.task_type,
            "status": r.status,
            "selected_year": r.selected_year,
            "selected_month": r.selected_month,
            "audit_run_id": r.audit_run_id,
            "reconciliation_id": r.reconciliation_id,
            "case_id": r.case_id,
            "duration_ms": r.duration_ms,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "timeline": r.timeline,
            "errors": r.errors,
            "evidence_count": len(r.evidence or []),
        }
        for r in runs
    ]


@app.get("/api/agents/runs/{run_id}")
def get_agent_run(run_id: int, db: Session = Depends(get_db)):
    r = db.query(InvestigationRun).filter(InvestigationRun.id == run_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": r.id,
        "name": r.name,
        "task_type": r.task_type,
        "status": r.status,
        "selected_year": r.selected_year,
        "selected_month": r.selected_month,
        "audit_run_id": r.audit_run_id,
        "reconciliation_id": r.reconciliation_id,
        "case_id": r.case_id,
        "input_state": r.input_state,
        "output_state": r.output_state,
        "timeline": r.timeline,
        "events": r.events,
        "evidence": r.evidence,
        "errors": r.errors,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@app.post("/api/cases")
def create_case(payload: InvestigationCreate, db: Session = Depends(get_db)):
    """Legacy alias; full investigation workflow lives under /api/investigations."""
    return create_investigation(db, payload)


@app.get("/api/cases")
def list_cases(limit: int = 50, db: Session = Depends(get_db)):
    """Legacy alias returning the historical array shape."""
    return list_investigations(db, limit, 0, None)["items"]


# ---------------------------------------------------------------------------
# Dashboard (all figures derived from persisted records)
# ---------------------------------------------------------------------------
@app.get("/api/dashboard/overview")
def dashboard_metrics(db: Session = Depends(get_db)):
    return dashboard_overview(db)


# ---------------------------------------------------------------------------
# Transaction management: search, filter, provenance and detail
# ---------------------------------------------------------------------------
def _filtered_transaction_query(
    db: Session,
    *,
    q: str | None,
    date_from: str | None,
    date_to: str | None,
    min_amount: float | None,
    max_amount: float | None,
    direction: str | None,
    source_file: str | None,
):
    query = db.query(TransactionRecord)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                TransactionRecord.description.ilike(like),
                TransactionRecord.vendor.ilike(like),
                TransactionRecord.transaction_id.ilike(like),
                TransactionRecord.reference.ilike(like),
                TransactionRecord.category.ilike(like),
            )
        )
    if date_from:
        query = query.filter(TransactionRecord.date >= date_from)
    if date_to:
        query = query.filter(TransactionRecord.date <= date_to)
    if min_amount is not None:
        query = query.filter(TransactionRecord.amount >= min_amount)
    if max_amount is not None:
        query = query.filter(TransactionRecord.amount <= max_amount)
    if direction:
        query = query.filter(TransactionRecord.direction == direction)
    if source_file:
        query = query.filter(TransactionRecord.source_file.ilike(f"%{source_file}%"))
    return query


@app.get("/api/transactions")
def search_transactions(
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    direction: str | None = None,
    source_file: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = _filtered_transaction_query(
        db,
        q=q,
        date_from=date_from,
        date_to=date_to,
        min_amount=min_amount,
        max_amount=max_amount,
        direction=direction,
        source_file=source_file,
    )
    total = query.count()
    rows = (
        query.order_by(TransactionRecord.date.desc(), TransactionRecord.id.desc())
        .offset(max(0, offset))
        .limit(min(max(1, limit), 500))
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "transaction_id": row.transaction_id,
                "date": row.date,
                "amount": row.amount,
                "signed_amount": row.signed_amount,
                "direction": row.direction,
                "vendor": row.vendor,
                "description": row.description,
                "category": row.category,
                "source_file": row.source_file,
                "source_row": row.source_row,
                "duplicate_key": row.duplicate_key,
                "channel": row.channel,
            }
            for row in rows
        ],
    }


@app.get("/api/transactions/export.csv")
def export_transactions(
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    direction: str | None = None,
    source_file: str | None = None,
    limit: int = 10000,
    db: Session = Depends(get_db),
):
    query = _filtered_transaction_query(
        db,
        q=q,
        date_from=date_from,
        date_to=date_to,
        min_amount=min_amount,
        max_amount=max_amount,
        direction=direction,
        source_file=source_file,
    )
    rows = (
        query.order_by(TransactionRecord.date.desc(), TransactionRecord.id.desc())
        .limit(min(max(1, limit), 50000))
        .all()
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    filter_label = json.dumps(
        {
            "q": q, "date_from": date_from, "date_to": date_to,
            "min_amount": min_amount, "max_amount": max_amount,
            "direction": direction, "source_file": source_file,
        },
        ensure_ascii=False,
    )
    output = StringIO()
    pd.DataFrame(
        [
            {
                "generated_at": generated_at,
                "filters": filter_label,
                "provenance": f"{row.source_file or 'unknown'}:{row.source_row if row.source_row is not None else '?'}",
                "record_type": "transaction",
                "reference": row.transaction_id,
                "date": row.date,
                "amount": row.amount,
                "signed_amount": row.signed_amount,
                "direction": row.direction,
                "vendor": row.vendor,
                "description": row.description,
                "category": row.category,
                "duplicate_key": row.duplicate_key,
                "channel": row.channel,
            }
            for row in rows
        ],
        columns=[
            "generated_at", "filters", "provenance", "record_type", "reference",
            "date", "amount", "signed_amount", "direction", "vendor",
            "description", "category", "duplicate_key", "channel",
        ],
    ).to_csv(output, index=False)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=finaudit_transactions.csv"},
    )


@app.get("/api/transactions/{transaction_ref}")
def transaction_detail(transaction_ref: str, db: Session = Depends(get_db)):
    filters = [
        TransactionRecord.transaction_id == transaction_ref,
        TransactionRecord.reference == transaction_ref,
    ]
    if transaction_ref.isdigit():
        filters.append(TransactionRecord.id == int(transaction_ref))
    row = db.query(TransactionRecord).filter(or_(*filters)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    alerts = (
        db.query(RiskAlert)
        .filter(RiskAlert.transaction_id == row.transaction_id)
        .order_by(RiskAlert.risk_score.desc())
        .all()
    )
    matches = (
        db.query(ReconciliationResult)
        .filter(
            or_(
                ReconciliationResult.bank_reference == row.transaction_id,
                ReconciliationResult.ledger_reference == row.transaction_id,
            )
        )
        .limit(20)
        .all()
    )
    return {
        "transaction": {
            "id": row.id,
            "transaction_id": row.transaction_id,
            "date": row.date,
            "amount": row.amount,
            "signed_amount": row.signed_amount,
            "direction": row.direction,
            "vendor": row.vendor,
            "description": row.description,
            "category": row.category,
            "account": row.account,
            "upi_ref": row.upi_ref,
            "source_file": row.source_file,
            "source_row": row.source_row,
            "duplicate_key": row.duplicate_key,
        },
        "risk_alerts": [
            {
                "id": alert.id,
                "alert_code": alert.alert_code,
                "risk_category": alert.risk_category,
                "risk_score": alert.risk_score,
                "severity": alert.severity,
                "explanation": alert.explanation,
                "review_status": alert.review_status,
            }
            for alert in alerts
        ],
        "reconciliation": [
            {
                "id": item.id,
                "status": item.status,
                "amount_difference": item.amount_difference,
                "date_difference_days": item.date_difference_days,
                "note": item.note,
            }
            for item in matches
        ],
    }


# ---------------------------------------------------------------------------
# RAG document ingestion and introspection
# ---------------------------------------------------------------------------
@app.post("/api/documents/ingest")
async def ingest_documents(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one document.")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Upload no more than 20 documents at once.")
    results = []
    for upload in files:
        filename = safe_filename(upload.filename)
        raw = await upload.read()
        if not raw:
            results.append({"filename": filename, "status": "error", "error": "Empty file"})
            continue
        if len(raw) > MAX_FILE_BYTES:
            results.append({"filename": filename, "status": "error", "error": "File exceeds 5 MB"})
            continue
        try:
            results.append(rag.ingest_upload(raw, filename))
        except ValueError as exc:
            results.append({"filename": filename, "status": "error", "error": str(exc)})
    return {"results": results, "stats": rag.store_stats()}


@app.post("/api/documents/sync-transactions")
def sync_transactions_to_rag(db: Session = Depends(get_db)):
    return rag.ingest_transactions(db)


@app.get("/api/documents")
def list_documents(limit: int = 50):
    stats = rag.store_stats()
    return {
        "stats": stats,
        "documents": rag.list_documents(limit),
        "status": "indexed" if stats.get("chunks", 0) > 0 else "empty",
    }


@app.get("/api/documents/status")
def document_index_status():
    stats = rag.store_stats()
    chunks = int(stats.get("chunks") or 0)
    return {
        **stats,
        "status": "indexed" if chunks > 0 else "empty",
        "healthy": chunks > 0 and bool(stats.get("index_exists")),
        "message": (
            f"{stats.get('documents', 0)} document(s), {chunks} chunk(s) indexed."
            if chunks
            else "No documents are indexed. Upload a document or sync transactions first."
        ),
    }


@app.get("/api/documents/{doc_id}/status")
def document_status(doc_id: str):
    item = rag.document_status(doc_id)
    if not item:
        raise HTTPException(status_code=404, detail="Document not found")
    return item


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    if not rag.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": doc_id, "stats": rag.store_stats()}


@app.post("/api/documents/reindex")
def reindex_documents():
    return {"reindexed": True, "stats": rag.reindex()}


@app.post("/api/documents/retrieval-test")
def retrieval_test(payload: dict):
    query = str((payload or {}).get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required.")
    top_k = int((payload or {}).get("top_k") or 5)
    filters = {}
    for key in ("source_filename", "document_type", "transaction_id"):
        value = (payload or {}).get(key)
        if value:
            filters[key] = value
    try:
        return rag.retrieval_test(query, top_k=top_k, **filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# AI Assistant (selective LangGraph workflow + grounded Ollama answer)
# ---------------------------------------------------------------------------
@app.get("/api/assistant/suggestions")
def assistant_suggestions():
    return {
        "questions": [
            "Explain my highest-risk transaction.",
            "Why was this payment flagged?",
            "Find possible duplicate payments.",
            "Explain the latest reconciliation results.",
            "Summarize this month's financial risks.",
            "What evidence supports this alert?",
            "Generate an investigation summary.",
        ]
    }


@app.get("/api/assistant/config")
def assistant_config():
    return {
        "backend": "ollama",
        "available": llm.is_available(),
        "model": llm.get_model(),
        "host": llm.get_host(),
        "rag": rag.store_stats(),
    }


@app.post("/api/assistant/query")
def assistant_query(payload: dict, db: Session = Depends(get_db)):
    question = str((payload or {}).get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required.")
    if len(question) > 1000:
        raise HTTPException(status_code=400, detail="question must be 1000 characters or fewer.")
    result = get_assistant_graph().run(question)
    links = []
    for citation in result.get("citations", []):
        if citation.get("type") in {"risk_alert", "transaction_record"} and citation.get("transaction_id"):
            links.append(
                {
                    "label": citation.get("source"),
                    "kind": citation.get("type"),
                    "transaction_id": citation.get("transaction_id"),
                }
            )
    # Persist the assistant exchange as investigation history so reviews remain
    # traceable.  A persistence failure never hides the answer already produced.
    history_id = None
    try:
        now = datetime.now(timezone.utc)
        run = InvestigationRun(
            name=question[:120],
            task_type="assistant_query",
            status="completed" if not result.get("agent_failures") else "partial",
            input_state={"question": question},
            output_state={
                "answer": result.get("answer"),
                "intent": result.get("intent"),
                "agents_selected": result.get("route"),
                "agent_outputs": result.get("agent_outputs", {}),
                "citations": result.get("citations", []),
                "sufficiency": result.get("sufficiency"),
            },
            timeline=result.get("timeline", []),
            events=[],
            evidence=result.get("sql_evidence", [])[:20],
            errors=result.get("errors", []),
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        history_id = run.id
    except Exception:  # noqa: BLE001 - history is best-effort, not a response blocker
        db.rollback()

    return {
        "question": question,
        "answer": result.get("answer"),
        "intent": result.get("intent"),
        "agents_selected": result.get("route"),
        "agent_roles": result.get("agent_roles", []),
        "required_roles": result.get("required_roles", []),
        "agent_failures": result.get("agent_failures", []),
        "route_reason": result.get("route_reason"),
        "sufficiency": result.get("sufficiency"),
        "llm": result.get("llm"),
        "evidence": result.get("sql_evidence", []),
        "citations": result.get("citations", []),
        "document_evidence": result.get("doc_evidence", []),
        "agent_outputs": result.get("agent_outputs", {}),
        "timeline": result.get("timeline", []),
        "links": links,
        "history_id": history_id,
        "disclaimer": "AI assists human review. Findings are not proof of fraud and no financial action is authorised by this output.",
    }


# ---------------------------------------------------------------------------
# Human confirmation/review of reconciliation matches and exceptions
# ---------------------------------------------------------------------------
@app.post("/api/reconciliations/results/{result_id}/confirm")
def confirm_reconciliation_result(result_id: int, payload: dict, db: Session = Depends(get_db)):
    payload = dict(payload or {})
    decision = str(payload.get("decision") or payload.get("action") or "").strip().lower()
    if decision == "confirmed":
        payload["action"] = "confirm_match"
    elif decision == "rejected":
        payload["action"] = "reject_match"
    elif not payload.get("action"):
        raise HTTPException(status_code=400, detail="decision must be 'confirmed' or 'rejected'")
    response = review_reconciliation_result(result_id, payload, db)
    result = response.get("result", {})
    # backward-compatible flat shape for existing clients/tests
    result.update({
        "review_status": result.get("review_status"),
        "reviewed_by": result.get("reviewed_by"),
        "reviewed_at": result.get("reviewed_at"),
        "reviewer_comment": result.get("reviewer_comment"),
        "run_summary": response.get("run_summary"),
    })
    return result


# ---------------------------------------------------------------------------
# Consolidated reports (PDF + CSV) from persisted records
# ---------------------------------------------------------------------------
def _investigation_explanation(run: InvestigationRun) -> dict:
    """Extract the grounded report payload from a persisted investigation run."""
    state = run.output_state if isinstance(run.output_state, dict) else {}
    outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
    report = outputs.get("report") if isinstance(outputs.get("report"), dict) else {}
    payload = report.get("output") if isinstance(report.get("output"), dict) else state

    def strings(key: str, limit: int) -> list[str]:
        values = payload.get(key) if isinstance(payload, dict) else []
        return [str(item) for item in (values or [])[:limit]] if isinstance(values, list) else []

    references = []
    raw_references = payload.get("evidence_references") if isinstance(payload, dict) else []
    if isinstance(raw_references, list):
        for item in raw_references[:5]:
            if isinstance(item, dict):
                references.append(
                    {
                        "source_id": item.get("source_id"),
                        "source_type": item.get("source_type"),
                        "source_file": item.get("source_file"),
                        "source_row": item.get("source_row"),
                        "transaction_id": item.get("transaction_id"),
                    }
                )
    return {
        "ai_narrative": payload.get("ai_narrative") if isinstance(payload, dict) else None,
        "conclusion_status": payload.get("conclusion_status") if isinstance(payload, dict) else None,
        "verified_facts": strings("verified_facts", 5),
        "recommended_review_actions": strings("recommended_review_actions", 3),
        "evidence_references": references,
    }


@app.get("/api/reports/summary")
def report_summary(db: Session = Depends(get_db)):
    overview = dashboard_overview(db)
    latest_recon = db.query(ReconciliationRun).order_by(ReconciliationRun.id.desc()).first()
    alerts = db.query(RiskAlert).order_by(RiskAlert.risk_score.desc()).limit(20).all()
    recent_runs = db.query(InvestigationRun).order_by(InvestigationRun.id.desc()).limit(10).all()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "alert_limit": 20,
            "investigation_limit": 10,
            "scope": "all stored records",
        },
        "provenance": "Computed from persisted transaction_records, risk_alerts, reconciliation_runs and investigation_runs.",
        "calculations": {
            "financial_summary": "Deterministic SQL aggregation of transaction_records (see scope).",
            "risk_alerts": "Top 20 alerts by stored risk_score; severity/score from the risk engine.",
            "reconciliation": "Latest saved reconciliation run summary.",
        },
        "financial_summary": overview["metrics"],
        "scope": overview["scope"],
        "reconciliation": {
            "id": latest_recon.id,
            "bank_file": latest_recon.bank_filename,
            "ledger_file": latest_recon.ledger_filename,
            "summary": reconciliation_summary(latest_recon),
        } if latest_recon else None,
        "risk_alerts": [
            {
                "alert_code": alert.alert_code,
                "risk_category": alert.risk_category,
                "severity": alert.severity,
                "risk_score": alert.risk_score,
                "amount": alert.amount_value,
                "transaction_id": alert.transaction_id,
                "explanation": alert.explanation,
                "review_status": alert.review_status,
            }
            for alert in alerts
        ],
        "investigations": [
            {"id": run.id, "name": run.name, "status": run.status, "task_type": run.task_type}
            for run in recent_runs
        ],
        "ai_explanations": [
            {"investigation_id": run.id, "name": run.name, **_investigation_explanation(run)}
            for run in recent_runs
            if _investigation_explanation(run)["ai_narrative"]
            or _investigation_explanation(run)["verified_facts"]
        ],
        "disclaimer": "Automated investigation leads for human review. Not proof of fraud.",
    }


@app.get("/api/reports/full.csv")
def report_full_csv(db: Session = Depends(get_db)):
    output = StringIO()
    rows = []
    for item in db.query(TransactionRecord).limit(10000).all():
        rows.append({
            "record_type": "transaction", "reference": item.transaction_id,
            "date": item.date, "amount": item.amount, "direction": item.direction,
            "description": item.description, "severity": "", "score": "",
            "source_file": item.source_file, "source_row": item.source_row,
        })
    for alert in db.query(RiskAlert).limit(10000).all():
        rows.append({
            "record_type": "risk_alert", "reference": alert.alert_code,
            "date": alert.detected_at.date().isoformat() if alert.detected_at else "",
            "amount": alert.amount_value, "direction": "", "description": alert.explanation,
            "severity": alert.severity, "score": alert.risk_score,
            "source_file": (alert.evidence or {}).get("source_file") if isinstance(alert.evidence, dict) else "",
            "source_row": "",
        })
    for run in db.query(InvestigationRun).limit(10000).all():
        explanation = _investigation_explanation(run)
        if not explanation["ai_narrative"] and not explanation["verified_facts"]:
            continue
        rows.append({
            "record_type": "ai_explanation", "reference": f"investigation-{run.id}",
            "date": run.finished_at.date().isoformat() if run.finished_at else "",
            "amount": "", "direction": "",
            "description": explanation["ai_narrative"] or "; ".join(explanation["verified_facts"]),
            "severity": explanation["conclusion_status"] or "", "score": "",
            "source_file": ", ".join(
                str(item.get("source_file") or "")
                for item in explanation["evidence_references"]
                if item.get("source_file")
            ),
            "source_row": "",
        })
    columns = ["record_type", "reference", "date", "amount", "direction", "description", "severity", "score", "source_file", "source_row"]
    # Provenance rows carry generation time, applied filters and calculation
    # notes so an exported report is self-describing for reviewers.
    generated_at = datetime.now(timezone.utc).isoformat()
    rows.insert(0, {
        "record_type": "report_metadata",
        "reference": "generated_at",
        "date": generated_at,
        "amount": "",
        "direction": "",
        "description": "Report generated from persisted database records.",
        "severity": "",
        "score": "",
        "source_file": "finaudit.db",
        "source_row": "",
    })
    rows.insert(1, {
        "record_type": "report_metadata",
        "reference": "filters",
        "date": "",
        "amount": "",
        "direction": "",
        "description": json.dumps({"scope": "all stored records", "limit_per_table": 10000}),
        "severity": "",
        "score": "",
        "source_file": "",
        "source_row": "",
    })
    rows.insert(2, {
        "record_type": "report_metadata",
        "reference": "calculations",
        "date": "",
        "amount": "",
        "direction": "",
        "description": "Financial values from deterministic SQL aggregation; risk severity/score from the deterministic risk engine.",
        "severity": "",
        "score": "",
        "source_file": "",
        "source_row": "",
    })
    pd.DataFrame(rows, columns=columns).to_csv(output, index=False)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=finaudit_full_report.csv"})


@app.get("/api/reports/full.pdf")
def report_full_pdf(db: Session = Depends(get_db)):
    overview = dashboard_overview(db)
    lines = ["FINANCIAL INTELLIGENCE REPORT", "Automated indicators for human review - not proof of fraud", ""]
    lines.append(f"Generated at (UTC): {datetime.now(timezone.utc).isoformat()}")
    lines.append("Filters: all stored records; top 30 risk alerts; 10 most recent investigations")
    lines.append("Provenance: persisted SQLite database (transaction_records, risk_alerts, reconciliation_runs, investigation_runs)")
    lines.append("Calculations: deterministic SQL/Python aggregation; LLM only narrates evidence")
    lines.append("")
    lines.append("FINANCIAL SUMMARY")
    for key, value in overview["metrics"].items():
        lines.append(f"  {key.replace('_', ' ').title()}: {value}")
    lines.append("")
    lines.append("METRIC SCOPE")
    for key, value in overview["scope"].items():
        lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append("TOP RISK ALERTS")
    alerts = db.query(RiskAlert).order_by(RiskAlert.risk_score.desc()).limit(30).all()
    for alert in alerts:
        lines.append(f"  {alert.alert_code} | {alert.severity} | score {alert.risk_score} | {alert.risk_category} | {alert.transaction_id}")
        lines.append(f"    {alert.explanation}")
    lines.append("")
    lines.append("AI EXPLANATIONS & INVESTIGATIONS")
    investigations = (
        db.query(InvestigationRun)
        .order_by(InvestigationRun.id.desc())
        .limit(10)
        .all()
    )
    for run in investigations:
        explanation = _investigation_explanation(run)
        if not explanation["ai_narrative"] and not explanation["verified_facts"]:
            continue
        lines.append(
            f"  Investigation #{run.id} | {run.name or 'investigation'} | "
            f"{explanation['conclusion_status'] or run.status or 'unknown'}"
        )
        if explanation["ai_narrative"]:
            lines.append(f"    AI narrative: {str(explanation['ai_narrative'])[:400]}")
        for fact in explanation["verified_facts"][:3]:
            lines.append(f"    Verified fact: {fact[:240]}")
        for reference in explanation["evidence_references"][:3]:
            lines.append(f"    Evidence: {reference}")
    lines.append("")
    lines.append("EVIDENCE REFERENCES")
    for item in overview["recent_activity"][:20]:
        lines.append(f"  {item['kind']} {item['reference']} | {item['date']} | {item['description']} | source {item.get('source_file') or 'n/a'}")
    return StreamingResponse(iter([build_text_pdf("FinAudit AI - Financial Intelligence Report", lines)]),
                             media_type="application/pdf",
                             headers={"Content-Disposition": "attachment; filename=finaudit_full_report.pdf"})
