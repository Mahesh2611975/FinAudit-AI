import json
from io import BytesIO, StringIO

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .analytics import AnalyticsInputError, monthly_analysis, monthly_comparison
from .audit_engine import detect_findings
from .csv_normalizer import normalize_files, safe_filename
from .database import Base, engine, get_db
from .llm import explain_finding
from .models import AuditRun, Finding, ReconciliationResult, ReconciliationRun, TransactionRecord, InvestigationRun, InvestigationCase
from .invoice_api import router as invoice_router
from .reconciliation import ReconciliationError, reconcile_files
from .category import categorize_transaction

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 20

Base.metadata.create_all(bind=engine)


def ensure_schema() -> None:
    """Add columns introduced after the initial SQLite database was created."""
    if engine.dialect.name != "sqlite":
        return

    additions = {
        "audit_runs": {"source_files": "TEXT"},
        "findings": {"source_file": "VARCHAR(255)"},
        "investigation_runs": {
            "events": "TEXT",
            "evidence": "TEXT",
            "selected_year": "INTEGER",
            "selected_month": "INTEGER",
            "audit_run_id": "INTEGER",
            "reconciliation_id": "INTEGER",
            "case_id": "INTEGER",
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
            "duration_ms": "INTEGER",
        },
    }
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


ensure_schema()

app = FastAPI(title="FinAudit AI", version="1.0.0", description="Local-first financial audit exception analysis with RAG-assisted explanations.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(invoice_router)


@app.get("/")
def root():
    return {"name": "FinAudit AI", "status": "running", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {"status": "healthy"}


def _files_for_run(run: AuditRun) -> list[str]:
    if run.source_files:
        try:
            parsed = json.loads(run.source_files)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (TypeError, ValueError):
            pass
    return [run.filename] if run.filename else []


@app.post("/api/audits")
async def create_audit(
    files: list[UploadFile] | None = File(None),
    file: UploadFile | None = File(None),
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
    findings = detect_findings(normalized.frame)

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
                signed_amount=float(row.get("signed_amount")) if row.get("signed_amount") is not None else None,
                direction=str(row.get("direction") or "") or None,
                type=str(row.get("type") or "") or None,
                vendor=str(row.get("vendor") or "") or None,
                description=str(row.get("description") or "") or None,
                category=cat,
                account=str(row.get("account") or "") or None,
                upi_ref=str(row.get("upi_ref") or "") or None,
                reference=str(row.get("upi_ref") or "") or None,
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


def reconciliation_summary(run: ReconciliationRun) -> dict:
    return {
        "bank_total": run.bank_total,
        "ledger_total": run.ledger_total,
        "matched_total": run.matched_total,
        "bank_unmatched_total": run.bank_unmatched_total,
        "ledger_unmatched_total": run.ledger_unmatched_total,
        "unmatched_total": run.unmatched_total,
        "difference": run.difference,
        "result_count": run.result_count,
        "matched_count": run.matched_count,
        "bank_only_count": run.bank_only_count,
        "ledger_only_count": run.ledger_only_count,
        "amount_mismatch_count": run.amount_mismatch_count,
        "date_mismatch_count": run.date_mismatch_count,
        "ambiguous_count": run.ambiguous_count,
    }


def reconciliation_result_dict(item: ReconciliationResult) -> dict:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "status": item.status,
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
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


RECONCILIATION_EXPORT_COLUMNS = [
    "id",
    "status",
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
    "note",
]


@app.post("/api/reconciliations")
async def create_reconciliation(
    bank_file: UploadFile = File(...),
    ledger_file: UploadFile = File(...),
    date_tolerance_days: int = Form(3),
    db: Session = Depends(get_db),
):
    if date_tolerance_days < 0 or date_tolerance_days > 365:
        raise HTTPException(
            status_code=400,
            detail="date_tolerance_days must be between 0 and 365.",
        )

    bank = await _read_upload_csv(bank_file, "Bank statement")
    ledger = await _read_upload_csv(ledger_file, "Accounting ledger")

    try:
        outcome = reconcile_files(
            bank,
            ledger,
            date_tolerance_days=date_tolerance_days,
        )
    except ReconciliationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc

    summary = outcome.summary
    run = ReconciliationRun(
        bank_filename=bank[0],
        ledger_filename=ledger[0],
        date_tolerance_days=date_tolerance_days,
        bank_total=summary["bank_total"],
        ledger_total=summary["ledger_total"],
        matched_total=summary["matched_total"],
        bank_unmatched_total=summary["bank_unmatched_total"],
        ledger_unmatched_total=summary["ledger_unmatched_total"],
        unmatched_total=summary["unmatched_total"],
        difference=summary["difference"],
        result_count=summary["result_count"],
        matched_count=summary["matched_count"],
        bank_only_count=summary["bank_only_count"],
        ledger_only_count=summary["ledger_only_count"],
        amount_mismatch_count=summary["amount_mismatch_count"],
        date_mismatch_count=summary["date_mismatch_count"],
        ambiguous_count=summary["ambiguous_count"],
    )
    db.add(run)
    db.flush()
    for item in outcome.results:
        db.add(ReconciliationResult(run_id=run.id, **item))
    db.commit()
    db.refresh(run)

    return {
        "reconciliation_id": run.id,
        "bank_file": run.bank_filename,
        "ledger_file": run.ledger_filename,
        "date_tolerance_days": run.date_tolerance_days,
        "summary": reconciliation_summary(run),
        "results": [reconciliation_result_dict(item) for item in run.results],
        "warnings": outcome.warnings,
    }


@app.get("/api/reconciliations")
def list_reconciliations(db: Session = Depends(get_db)):
    runs = (
        db.query(ReconciliationRun)
        .order_by(ReconciliationRun.id.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": run.id,
            "reconciliation_id": run.id,
            "bank_file": run.bank_filename,
            "ledger_file": run.ledger_filename,
            "files": [run.bank_filename, run.ledger_filename],
            "date_tolerance_days": run.date_tolerance_days,
            "summary": reconciliation_summary(run),
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }
        for run in runs
    ]


@app.get("/api/reconciliations/{reconciliation_id}")
def get_reconciliation(reconciliation_id: int, db: Session = Depends(get_db)):
    run = (
        db.query(ReconciliationRun)
        .filter(ReconciliationRun.id == reconciliation_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return {
        "reconciliation_id": run.id,
        "bank_file": run.bank_filename,
        "ledger_file": run.ledger_filename,
        "date_tolerance_days": run.date_tolerance_days,
        "summary": reconciliation_summary(run),
        "results": [reconciliation_result_dict(item) for item in run.results],
    }


@app.get("/api/reconciliations/{reconciliation_id}/export.csv")
def export_reconciliation(reconciliation_id: int, db: Session = Depends(get_db)):
    run = (
        db.query(ReconciliationRun)
        .filter(ReconciliationRun.id == reconciliation_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    rows = [reconciliation_result_dict(item) for item in run.results]
    output = StringIO()
    pd.DataFrame(rows, columns=RECONCILIATION_EXPORT_COLUMNS).to_csv(
        output, index=False
    )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=finaudit_reconciliation_{reconciliation_id}.csv"
            )
        },
    )


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
def create_case(payload: dict, db: Session = Depends(get_db)):
    case = InvestigationCase(
        title=payload.get("title") or "Investigation Case",
        description=payload.get("description"),
        severity=payload.get("severity") or "medium",
        status=payload.get("status") or "open",
        audit_run_id=payload.get("audit_run_id"),
        transaction_id=payload.get("transaction_id"),
        source_file=payload.get("source_file"),
        source_row=payload.get("source_row"),
        reviewer_notes=payload.get("reviewer_notes"),
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return {"id": case.id, "title": case.title, "status": case.status, "severity": case.severity}


@app.get("/api/cases")
def list_cases(limit: int = 50, db: Session = Depends(get_db)):
    cases = db.query(InvestigationCase).order_by(InvestigationCase.id.desc()).limit(min(limit, 200)).all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "severity": c.severity,
            "status": c.status,
            "audit_run_id": c.audit_run_id,
            "transaction_id": c.transaction_id,
            "source_file": c.source_file,
            "source_row": c.source_row,
            "reviewer_notes": c.reviewer_notes,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in cases
    ]
