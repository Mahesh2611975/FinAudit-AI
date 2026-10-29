from io import BytesIO, StringIO
from fastapi import FastAPI, Depends, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import pandas as pd
from .database import Base, engine, get_db
from .models import AuditRun, Finding
from .audit_engine import validate_transactions, detect_findings
from .llm import explain_finding

Base.metadata.create_all(bind=engine)
app = FastAPI(title="FinAudit AI", version="1.0.0", description="Local-first financial audit exception analysis with RAG-assisted explanations.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root(): return {"name": "FinAudit AI", "status": "running", "docs": "/docs"}

@app.get("/api/health")
def health(): return {"status": "healthy"}

@app.post("/api/audits")
async def create_audit(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv file.")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV must be 5 MB or smaller for this MVP.")
    try:
        df = pd.read_csv(BytesIO(raw))
        df.columns = [str(c).strip().lower() for c in df.columns]
        errors = validate_transactions(df)
        if errors: raise HTTPException(status_code=422, detail=errors)
        findings = detect_findings(df)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=422, detail=f"Could not process CSV: {exc}") from exc
    run = AuditRun(filename=file.filename[:255], row_count=len(df), finding_count=len(findings))
    db.add(run); db.flush()
    for item in findings:
        db.add(Finding(run_id=run.id, **item))
    db.commit(); db.refresh(run)
    return {"audit_id": run.id, "filename": run.filename, "row_count": run.row_count, "finding_count": run.finding_count, "findings": [finding_dict(f) for f in run.findings]}

def finding_dict(f: Finding):
    return {"id": f.id, "run_id": f.run_id, "transaction_id": f.transaction_id, "finding_type": f.finding_type, "risk_level": f.risk_level, "amount": f.amount, "summary": f.summary, "evidence": f.evidence, "created_at": f.created_at.isoformat() if f.created_at else None}

@app.get("/api/audits")
def list_audits(db: Session = Depends(get_db)):
    runs = db.query(AuditRun).order_by(AuditRun.id.desc()).limit(50).all()
    return [{"id": r.id, "filename": r.filename, "row_count": r.row_count, "finding_count": r.finding_count, "created_at": r.created_at.isoformat() if r.created_at else None} for r in runs]

@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: int, db: Session = Depends(get_db)):
    run = db.query(AuditRun).filter(AuditRun.id == audit_id).first()
    if not run: raise HTTPException(status_code=404, detail="Audit not found")
    return {"audit_id": run.id, "filename": run.filename, "row_count": run.row_count, "finding_count": run.finding_count, "findings": [finding_dict(f) for f in run.findings]}

@app.post("/api/findings/{finding_id}/explain")
async def explain(finding_id: int, db: Session = Depends(get_db)):
    finding = db.query(Finding).filter(Finding.id == finding_id).first()
    if not finding: raise HTTPException(status_code=404, detail="Finding not found")
    return await explain_finding(f"Explain this audit finding: {finding.finding_type}. {finding.summary}", finding.evidence)

@app.get("/api/audits/{audit_id}/export.csv")
def export_audit(audit_id: int, db: Session = Depends(get_db)):
    run = db.query(AuditRun).filter(AuditRun.id == audit_id).first()
    if not run: raise HTTPException(status_code=404, detail="Audit not found")
    rows = [finding_dict(f) for f in run.findings]
    columns = ["id", "transaction_id", "finding_type", "risk_level", "amount", "summary", "evidence"]
    output = StringIO(); pd.DataFrame(rows, columns=columns).to_csv(output, index=False)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=finaudit_findings_{audit_id}.csv"})
