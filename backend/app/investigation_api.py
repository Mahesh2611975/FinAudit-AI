"""Investigation case management: alerts to cases, evidence, notes and history.

All financial values are read from persisted records. The LLM is used only to
narrate the evidence collected here; it cannot change a case status or score.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .auth import actor_name, get_current_active_user, rag_scope_for_user, require_permissions
from .database import get_db
from .llm import explain_finding
from .models import (
    InvestigationCase,
    InvestigationEvent,
    InvestigationEvidence,
    InvestigationNote,
    RiskAlert,
    RiskAlertEvent,
    TransactionRecord,
    User,
)
from .ownership import RagAccessDenied

router = APIRouter()


def _request_rag_scope(user: User, reason: str):
    """Build a read-only RAG scope from the authenticated principal."""
    try:
        return rag_scope_for_user(user, audit_reason=reason)
    except RagAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

CASE_STATUSES = ("open", "under_review", "resolved", "dismissed")
_STATUS_ALIASES = {
    "investigating": "under_review",
    "in_review": "under_review",
    "under investigation": "under_review",
    "under review": "under_review",
    "closed": "resolved",
    "false_positive": "dismissed",
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _normalize_status(value: str | None, default: str = "open") -> str:
    status = str(value or default).strip().lower()
    return _STATUS_ALIASES.get(status, status)


class InvestigationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    severity: str = "medium"
    status: str = "open"
    transaction_id: str | None = Field(default=None, max_length=100)
    vendor_name: str | None = Field(default=None, max_length=255)
    source_file: str | None = Field(default=None, max_length=255)
    source_row: int | None = None
    assigned_to: str | None = Field(default=None, max_length=255)
    reviewer_notes: str | None = None
    created_by: str | None = "system"
    audit_run_id: int | None = None


class InvestigationUpdate(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    severity: str | None = None
    resolution: str | None = None
    reviewer_notes: str | None = None
    actor: str | None = "reviewer"


class NoteCreate(BaseModel):
    author: str | None = Field(default=None, max_length=255)
    note: str = Field(..., min_length=1)


class EvidenceCreate(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=50)
    source_id: str | None = Field(default=None, max_length=120)
    source_reference: str | None = Field(default=None, max_length=255)
    excerpt: str | None = None
    metadata: dict[str, Any] | None = None
    added_by: str | None = Field(default=None, max_length=255)


def _case_or_404(db: Session, case_ref: str | int) -> InvestigationCase:
    query = db.query(InvestigationCase)
    ref = str(case_ref)
    if ref.isdigit():
        case = query.filter(InvestigationCase.id == int(ref)).first()
    else:
        case = query.filter(InvestigationCase.case_number == ref).first()
    if not case:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return case


def _alert_or_404(db: Session, alert_ref: str | int) -> RiskAlert:
    query = db.query(RiskAlert)
    ref = str(alert_ref)
    if ref.isdigit():
        alert = query.filter(RiskAlert.id == int(ref)).first()
    else:
        alert = query.filter(RiskAlert.alert_code == ref).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Risk alert not found")
    return alert


def _event(
    db: Session,
    case: InvestigationCase,
    action: str,
    actor: str | None,
    comment: str | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_user_id: int | None = None,
) -> None:
    db.add(
        InvestigationEvent(
            case_id=case.id,
            action=action,
            actor=actor,
            actor_user_id=actor_user_id,
            comment=comment,
            previous_status=previous_status,
            new_status=new_status,
            details_json=metadata,
        )
    )


def _add_evidence(
    db: Session,
    case: InvestigationCase,
    *,
    source_type: str,
    source_id: str | None,
    source_reference: str | None,
    excerpt: str | None,
    metadata: dict[str, Any] | None,
    added_by: str | None,
    added_by_user_id: int | None = None,
) -> InvestigationEvidence:
    item = InvestigationEvidence(
        case_id=case.id,
        source_type=source_type,
        source_id=source_id,
        source_reference=source_reference,
        excerpt=excerpt,
        details_json=metadata,
        added_by=added_by,
        added_by_user_id=added_by_user_id,
    )
    db.add(item)
    return item


def _serialize_case(db: Session, case: InvestigationCase, *, detailed: bool = False) -> dict[str, Any]:
    payload = {
        "id": case.id,
        "case_number": case.case_number,
        "title": case.title,
        "description": case.description,
        "severity": case.severity,
        "status": case.status,
        "review_status": case.status,
        "audit_run_id": case.audit_run_id,
        "risk_alert_id": case.risk_alert_id,
        "transaction_id": case.transaction_id,
        "vendor_name": case.vendor_name,
        "source_file": case.source_file,
        "source_row": case.source_row,
        "assigned_to": case.assigned_to,
        "created_by": case.created_by,
        "reviewer_notes": case.reviewer_notes,
        "resolution": case.resolution,
        "ai_explanation": case.ai_explanation,
        "resolved_at": _iso(case.resolved_at),
        "created_at": _iso(case.created_at),
        "updated_at": _iso(case.updated_at),
    }
    if not detailed:
        return payload

    transactions = []
    if case.transaction_id:
        transactions = [
            {
                "id": row.id,
                "transaction_id": row.transaction_id,
                "date": row.date,
                "amount": row.amount,
                "direction": row.direction,
                "vendor": row.vendor,
                "description": row.description,
                "source_file": row.source_file,
                "source_row": row.source_row,
            }
            for row in db.query(TransactionRecord)
            .filter(TransactionRecord.transaction_id == case.transaction_id)
            .limit(20)
            .all()
        ]
    vendors = []
    if case.vendor_name:
        vendors = sorted(
            {
                row.vendor
                for row in db.query(TransactionRecord)
                .filter(TransactionRecord.vendor.ilike(f"%{case.vendor_name}%"))
                .limit(200)
                .all()
                if row.vendor
            }
        )
    alert = db.query(RiskAlert).filter(RiskAlert.id == case.risk_alert_id).first() if case.risk_alert_id else None
    payload.update(
        {
            "transactions": transactions,
            "related_vendors": vendors,
            "alert": {
                "id": alert.id,
                "alert_code": alert.alert_code,
                "risk_category": alert.risk_category,
                "severity": alert.severity,
                "risk_score": alert.risk_score,
                "explanation": alert.explanation,
                "review_status": alert.review_status,
            } if alert else None,
            "notes": [
                {"id": item.id, "author": item.author, "note": item.note, "created_at": _iso(item.created_at)}
                for item in sorted(case.notes, key=lambda item: item.id)
            ],
            "evidence": [
                {
                    "id": item.id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "source_reference": item.source_reference,
                    "excerpt": item.excerpt,
                    "metadata": item.details_json,
                    "added_by": item.added_by,
                    "created_at": _iso(item.created_at),
                }
                for item in sorted(case.evidence, key=lambda item: item.id)
            ],
            "history": [
                {
                    "id": item.id,
                    "action": item.action,
                    "actor": item.actor,
                    "comment": item.comment,
                    "previous_status": item.previous_status,
                    "new_status": item.new_status,
                    "metadata": item.details_json,
                    "created_at": _iso(item.created_at),
                }
                for item in sorted(case.events, key=lambda item: item.id)
            ],
        }
    )
    return payload


def list_investigations(db: Session, limit: int, offset: int, status: str | None) -> dict[str, Any]:
    query = db.query(InvestigationCase)
    if status:
        query = query.filter(InvestigationCase.status == _normalize_status(status))
    total = query.count()
    cases = (
        query.order_by(InvestigationCase.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return {
        "total": total,
        "items": [_serialize_case(db, case) for case in cases],
        "statuses": list(CASE_STATUSES),
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def create_investigation(db: Session, payload: InvestigationCreate, current_user: User | None = None) -> dict[str, Any]:
    status = _normalize_status(payload.status)
    if status not in CASE_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(CASE_STATUSES)}")
    creator = actor_name(current_user) if current_user else (payload.created_by or "system")
    case = InvestigationCase(
        title=(payload.title or "Financial investigation").strip(),
        description=payload.description,
        severity=(payload.severity or "medium").strip().lower(),
        status=status,
        audit_run_id=payload.audit_run_id,
        transaction_id=payload.transaction_id,
        vendor_name=payload.vendor_name,
        source_file=payload.source_file,
        source_row=payload.source_row,
        assigned_to=payload.assigned_to,
        created_by=creator,
        reviewer_notes=payload.reviewer_notes,
    )
    db.add(case)
    db.flush()
    case.case_number = f"INV-{case.id:06d}"
    if payload.reviewer_notes:
        db.add(
            InvestigationNote(
                case_id=case.id,
                author=creator,
                author_user_id=current_user.id if current_user else None,
                note=payload.reviewer_notes,
            )
        )
    _event(db, case, "created", creator, previous_status=None, new_status=status, metadata={"actor_user_id": current_user.id} if current_user else None)
    if current_user:
        # store actor_user_id on the event just created
        evt = db.query(InvestigationEvent).filter(InvestigationEvent.case_id == case.id).order_by(InvestigationEvent.id.desc()).first()
        if evt:
            evt.actor_user_id = current_user.id
    db.commit()
    db.refresh(case)
    return _serialize_case(db, case, detailed=True)


@router.get("/api/investigations")
def list_cases_endpoint(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return list_investigations(db, limit, offset, status)


@router.post("/api/investigations", status_code=201)
def create_case_endpoint(
    payload: InvestigationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return create_investigation(db, payload, current_user=current_user)


@router.post("/api/investigations/from-alert/{alert_ref}", status_code=201)
def create_case_from_alert(
    alert_ref: str,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    payload = payload or {}
    alert = _alert_or_404(db, alert_ref)
    evidence = alert.evidence if isinstance(alert.evidence, dict) else {}
    source_file = evidence.get("source_file")
    source_row = evidence.get("source_row")
    severity = "high" if alert.severity in {"critical", "high"} else alert.severity
    creator = actor_name(current_user)
    case = InvestigationCase(
        title=str(payload.get("title") or f"Investigation: {alert.risk_category}").strip(),
        description=str(payload.get("description") or alert.explanation),
        severity=severity,
        status="open",
        risk_alert_id=alert.id,
        transaction_id=alert.transaction_id,
        vendor_name=alert.vendor_name,
        source_file=str(source_file) if source_file else None,
        source_row=int(source_row) if str(source_row or "").isdigit() else None,
        assigned_to=payload.get("assigned_to"),
        created_by=creator,
    )
    db.add(case)
    db.flush()
    case.case_number = f"INV-{case.id:06d}"
    _add_evidence(
        db,
        case,
        source_type="risk_alert",
        source_id=alert.alert_code,
        source_reference=alert.transaction_id or alert.vendor_name,
        excerpt=alert.explanation,
        metadata={
            "risk_score": alert.risk_score,
            "severity": alert.severity,
            "category": alert.risk_category,
            "rule": evidence.get("rule"),
            "source_file": source_file,
            "source_row": source_row,
        },
        added_by=creator,
        added_by_user_id=current_user.id,
    )
    transaction = (
        db.query(TransactionRecord)
        .filter(TransactionRecord.transaction_id == alert.transaction_id)
        .first()
        if alert.transaction_id
        else None
    )
    if transaction:
        _add_evidence(
            db,
            case,
            source_type="transaction_record",
            source_id=transaction.transaction_id,
            source_reference=f"{transaction.source_file or 'unknown'}:{transaction.source_row or '?'}",
            excerpt=f"{transaction.date or 'unknown date'} {transaction.direction or ''} {transaction.amount} {transaction.vendor or transaction.description or ''}",
            metadata={
                "transaction_id": transaction.transaction_id,
                "amount": transaction.amount,
                "date": transaction.date,
                "vendor": transaction.vendor,
            },
            added_by=creator,
            added_by_user_id=current_user.id,
        )
    _event(
        db,
        case,
        "created_from_alert",
        creator,
        comment=f"Created from alert {alert.alert_code}",
        previous_status=None,
        new_status="open",
        metadata={"alert_code": alert.alert_code, "actor_user_id": current_user.id},
        actor_user_id=current_user.id,
    )
    if alert.review_status == "open":
        alert.review_status = "investigating"
        alert.updated_at = datetime.now(timezone.utc)
        db.add(
            RiskAlertEvent(
                alert_id=alert.id,
                action="investigation_created",
                actor=creator,
                actor_user_id=current_user.id,
                comment=f"Investigation {case.case_number} created",
                previous_status="open",
                new_status="investigating",
            )
        )
    db.commit()
    db.refresh(case)
    return _serialize_case(db, case, detailed=True)


@router.get("/api/investigations/{case_ref}")
def get_case(case_ref: str, db: Session = Depends(get_db)):
    return _serialize_case(db, _case_or_404(db, case_ref), detailed=True)


@router.patch("/api/investigations/{case_ref}")
def update_case(
    case_ref: str,
    payload: InvestigationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    case = _case_or_404(db, case_ref)
    previous_status = case.status
    actor = actor_name(current_user)
    if payload.status is not None:
        status = _normalize_status(payload.status)
        if status not in CASE_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {list(CASE_STATUSES)}")
        case.status = status
        case.resolved_at = datetime.now(timezone.utc) if status in {"resolved", "dismissed"} else None
    if payload.assigned_to is not None:
        case.assigned_to = str(payload.assigned_to).strip() or None
    if payload.severity is not None:
        case.severity = payload.severity.strip().lower()
    if payload.resolution is not None:
        case.resolution = payload.resolution
    if payload.reviewer_notes is not None:
        case.reviewer_notes = payload.reviewer_notes
        db.add(
            InvestigationNote(
                case_id=case.id,
                author=actor,
                author_user_id=current_user.id,
                note=payload.reviewer_notes,
            )
        )
    _event(
        db,
        case,
        "status_change" if payload.status is not None else "updated",
        actor,
        comment=payload.resolution or payload.reviewer_notes,
        previous_status=previous_status,
        new_status=case.status,
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(case)
    return _serialize_case(db, case, detailed=True)


@router.post("/api/investigations/{case_ref}/notes", status_code=201)
def add_note(
    case_ref: str,
    payload: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    case = _case_or_404(db, case_ref)
    author = actor_name(current_user)
    note = InvestigationNote(case_id=case.id, author=author, author_user_id=current_user.id, note=payload.note)
    db.add(note)
    _event(db, case, "note_added", author, comment=payload.note[:300], actor_user_id=current_user.id)
    db.commit()
    db.refresh(note)
    return {"id": note.id, "author": note.author, "note": note.note, "created_at": _iso(note.created_at)}


@router.post("/api/investigations/{case_ref}/evidence", status_code=201)
def add_evidence(
    case_ref: str,
    payload: EvidenceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    case = _case_or_404(db, case_ref)
    actor = actor_name(current_user)
    item = _add_evidence(
        db,
        case,
        source_type=payload.source_type,
        source_id=payload.source_id,
        source_reference=payload.source_reference,
        excerpt=payload.excerpt,
        metadata=payload.metadata,
        added_by=actor,
        added_by_user_id=current_user.id,
    )
    _event(
        db,
        case,
        "evidence_added",
        actor,
        comment=payload.source_reference,
        metadata={"source_type": payload.source_type, "source_id": payload.source_id},
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "source_reference": item.source_reference,
        "excerpt": item.excerpt,
        "metadata": item.details_json,
    }


@router.post("/api/investigations/{case_ref}/explain")
async def explain_case(
    case_ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permissions("investigation:write")),
):
    scope = _request_rag_scope(current_user, "investigation case explanation")
    case = _case_or_404(db, case_ref)
    detail = _serialize_case(db, case, detailed=True)
    evidence_lines = [
        f"Case {case.case_number}: {case.title}",
        f"Status: {case.status}; severity: {case.severity}",
    ]
    if detail.get("alert"):
        alert = detail["alert"]
        evidence_lines.append(
            f"Alert {alert['alert_code']}: {alert['risk_category']} score {alert['risk_score']} "
            f"severity {alert['severity']}. {alert['explanation']}"
        )
    for item in detail["transactions"][:10]:
        evidence_lines.append(
            f"Transaction {item['transaction_id']}: {item['date']} {item['direction']} "
            f"{item['amount']} {item['vendor'] or item['description']} "
            f"source {item['source_file']}:{item['source_row']}"
        )
    for item in detail["evidence"][:10]:
        evidence_lines.append(f"Evidence [{item['source_type']}] {item['source_reference']}: {item['excerpt']}")
    for item in detail["notes"][-5:]:
        evidence_lines.append(f"Reviewer note by {item['author']}: {item['note']}")
    question = f"Explain the stored evidence for investigation {case.case_number} and state what a reviewer should verify."
    result = await explain_finding(question, "\n".join(evidence_lines), scope=scope)
    case.ai_explanation = result.get("answer") or "No explanation was generated."
    _event(
        db,
        case,
        "ai_explanation_generated",
        "system",
        metadata={
            "fallback": result.get("fallback"),
            "error_category": result.get("error_category"),
            "sources": result.get("retrieved_sources"),
        },
    )
    db.commit()
    db.refresh(case)
    return {
        "case_number": case.case_number,
        "explanation": case.ai_explanation,
        "fallback": result.get("fallback", False),
        "error_category": result.get("error_category"),
        "model": result.get("model"),
        "retrieved_sources": result.get("retrieved_sources", []),
        "disclaimer": "Automated investigation lead for human review, not proof of fraud.",
    }


@router.get("/api/investigations/{case_ref}/history")
def case_history(case_ref: str, db: Session = Depends(get_db)):
    case = _case_or_404(db, case_ref)
    return {
        "case_number": case.case_number,
        "events": _serialize_case(db, case, detailed=True)["history"],
    }


@router.get("/api/investigations/{case_ref}/export.csv")
def export_case(case_ref: str, db: Session = Depends(get_db)):
    case = _case_or_404(db, case_ref)
    detail = _serialize_case(db, case, detailed=True)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["record_type", "reference", "author", "created_at", "description", "metadata"])
    writer.writerow(
        [
            "investigation",
            case.case_number,
            case.created_by,
            _iso(case.created_at),
            f"{case.title} | status={case.status} | resolution={case.resolution or ''}",
            json.dumps({"severity": case.severity, "alert": detail["alert"]}, ensure_ascii=False),
        ]
    )
    for item in detail["transactions"]:
        writer.writerow(["transaction", item["transaction_id"], "", item["date"], f"{item['amount']} {item['direction']} {item['vendor'] or ''}", f"{item['source_file']}:{item['source_row']}"])
    for item in detail["evidence"]:
        writer.writerow(["evidence", item["source_reference"], item["added_by"], item["created_at"], item["excerpt"], json.dumps(item["metadata"] or {}, ensure_ascii=False)])
    for item in detail["notes"]:
        writer.writerow(["note", case.case_number, item["author"], item["created_at"], item["note"], ""])
    for item in detail["history"]:
        writer.writerow(["event", case.case_number, item["actor"], item["created_at"], item["action"], json.dumps({"previous": item["previous_status"], "new": item["new_status"]}, ensure_ascii=False)])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={case.case_number or 'investigation'}-report.csv"},
    )
