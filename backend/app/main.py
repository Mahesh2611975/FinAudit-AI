import json
from io import BytesIO, StringIO

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .audit_engine import detect_findings
from .csv_normalizer import normalize_files, safe_filename
from .database import Base, engine, get_db
from .llm import explain_finding
from .models import AuditRun, Finding

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
            "filename": run.filename,
            "files": _files_for_run(run),
            "row_count": run.row_count,
            "finding_count": run.finding_count,
            "created_at": run.created_at.isoformat() if run.created_at else None,
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
