"""FastAPI routes for the Financial Risk & Fraud Intelligence Center."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .database import get_db
from .geo_analytics import (
    geography_cities,
    geography_countries,
    geography_filter_options,
    geography_high_risk_locations,
    geography_insights,
    geography_map,
    geography_trend,
)
from .geocoding import geocoding_status
from .llm import explain_finding
from .models import RiskAlert, RiskAlertEvent, RiskAnalysisRun
from .risk_engine import (
    CANONICAL_CHANNELS,
    CATEGORY_LABELS,
    LEGACY_CHANNELS,
    REVIEW_STATUSES,
    SUPPORTED_CHANNELS,
    build_investigation,
    channel_analytics,
    geography_analytics,
    overview,
    resolve_thresholds,
    run_analysis,
    serialize_alert,
    trend_analytics,
)

router = APIRouter()


def _alert_or_404(db: Session, alert_id: str) -> RiskAlert:
    query = db.query(RiskAlert)
    if str(alert_id).isdigit():
        alert = query.filter(RiskAlert.id == int(alert_id)).first()
    else:
        alert = query.filter(RiskAlert.alert_code == str(alert_id)).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Risk alert not found")
    return alert


def _split_filter(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@router.get("/api/risk/config")
def risk_config():
    thresholds = resolve_thresholds()
    return {
        "thresholds": thresholds.as_dict(),
        "risk_bands": {
            "low": [0, thresholds.low_max],
            "medium": [thresholds.low_max + 1, thresholds.medium_max],
            "high": [thresholds.medium_max + 1, thresholds.high_max],
            "critical": [thresholds.critical_min, 100],
        },
        "supported_channels": list(SUPPORTED_CHANNELS),
        "canonical_channels": list(CANONICAL_CHANNELS),
        "legacy_channels": list(LEGACY_CHANNELS),
        "categories": list(CATEGORY_LABELS),
        "review_statuses": list(REVIEW_STATUSES),
        "environment_variables": [
            "RISK_LOW_MAX",
            "RISK_MEDIUM_MAX",
            "RISK_HIGH_MAX",
            "RISK_APPROVAL_VALUE",
            "RISK_UNUSUAL_AMOUNT_MULTIPLIER",
            "RISK_FREQUENCY_PER_DAY",
            "RISK_DUPLICATE_WINDOW_DAYS",
        ],
    }


@router.get("/api/risk/overview")
def risk_overview(db: Session = Depends(get_db)):
    return overview(db)


def _filtered_alert_query(
    db: Session,
    *,
    q: str | None,
    severity: str | None,
    category: str | None,
    status: str | None,
    start_date: str | None,
    end_date: str | None,
):
    query = db.query(RiskAlert)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                RiskAlert.explanation.ilike(like),
                RiskAlert.vendor_name.ilike(like),
                RiskAlert.alert_code.ilike(like),
                RiskAlert.transaction_id.ilike(like),
                RiskAlert.risk_category.ilike(like),
            )
        )
    severities = _split_filter(severity)
    if severities:
        query = query.filter(RiskAlert.severity.in_(severities))
    categories = _split_filter(category)
    if categories:
        query = query.filter(RiskAlert.risk_category.in_(categories))
    statuses = _split_filter(status)
    if statuses:
        query = query.filter(RiskAlert.review_status.in_(statuses))
    if start_date:
        try:
            start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="start_date must be an ISO date") from exc
        query = query.filter(RiskAlert.detected_at >= start)
    if end_date:
        try:
            end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="end_date must be an ISO date") from exc
        if "T" not in end_date:
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.filter(RiskAlert.detected_at <= end)
    return query


@router.get("/api/risk/alerts")
def list_risk_alerts(
    q: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = _filtered_alert_query(
        db,
        q=q,
        severity=severity,
        category=category,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    total = query.count()
    alerts = (
        query.order_by(RiskAlert.detected_at.desc(), RiskAlert.id.desc())
        .offset(max(offset, 0))
        .limit(max(1, min(limit, 500)))
        .all()
    )
    all_categories = [
        row[0] for row in db.query(RiskAlert.risk_category).distinct().order_by(RiskAlert.risk_category).all()
    ]
    return {
        "items": [serialize_alert(alert) for alert in alerts],
        "total": total,
        "limit": max(1, min(limit, 500)),
        "offset": max(offset, 0),
        "categories": all_categories,
        "severities": ["critical", "high", "medium", "low"],
        "statuses": list(REVIEW_STATUSES),
    }


@router.get("/api/risk/alerts/export.csv")
def export_risk_alerts(
    q: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 10000,
    db: Session = Depends(get_db),
):
    query = _filtered_alert_query(
        db,
        q=q,
        severity=severity,
        category=category,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    alerts = (
        query.order_by(RiskAlert.detected_at.desc(), RiskAlert.id.desc())
        .limit(min(max(1, limit), 50000))
        .all()
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    filter_label = json.dumps(
        {
            "q": q,
            "severity": severity,
            "category": category,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
        },
        ensure_ascii=False,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "generated_at", "filters", "record_type", "reference", "detected_at",
        "risk_category", "severity", "risk_score", "amount", "vendor",
        "transaction_id", "review_status", "explanation", "provenance",
    ])
    for alert in alerts:
        evidence = alert.evidence if isinstance(alert.evidence, dict) else {}
        writer.writerow([
            generated_at,
            filter_label,
            "risk_alert",
            alert.alert_code,
            alert.detected_at.isoformat() if alert.detected_at else "",
            alert.risk_category,
            alert.severity,
            alert.risk_score,
            alert.amount_value,
            alert.vendor_name,
            alert.transaction_id,
            alert.review_status,
            alert.explanation,
            f"{evidence.get('source_file') or 'unknown'}:{evidence.get('source_row') or '?'}",
        ])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=finaudit_risk_alerts.csv"},
    )


@router.get("/api/risk/alerts/{alert_id}")
def get_risk_alert(alert_id: str, db: Session = Depends(get_db)):
    alert = _alert_or_404(db, alert_id)
    return serialize_alert(alert)


@router.post("/api/risk/alerts/{alert_id}/review")
def review_risk_alert(alert_id: str, payload: dict, db: Session = Depends(get_db)):
    alert = _alert_or_404(db, alert_id)
    action = str(payload.get("action") or "comment").strip().lower()
    if action not in {"assign", "resolve", "reopen", "investigate", "comment", "status"}:
        raise HTTPException(
            status_code=422,
            detail="action must be assign, resolve, reopen, investigate, comment or status",
        )
    actor = str(payload.get("reviewer_name") or payload.get("actor") or "").strip()
    if not actor:
        raise HTTPException(status_code=422, detail="reviewer_name is required")

    requested_status = payload.get("review_status") or payload.get("status")
    if requested_status is None:
        requested_status = {
            "assign": "investigating",
            "resolve": "resolved",
            "reopen": "open",
            "investigate": "investigating",
        }.get(action, alert.review_status)
    requested_status = str(requested_status).strip().lower()
    requested_status = {
        "under_investigation": "investigating",
        "under investigation": "investigating",
        "false positive": "false_positive",
    }.get(requested_status, requested_status)
    if requested_status not in REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"review_status must be one of {list(REVIEW_STATUSES)}",
        )

    assigned_to = payload.get("assigned_to")
    if assigned_to is not None:
        alert.assigned_to = str(assigned_to).strip() or None

    comment = payload.get("comment")
    previous_status = alert.review_status
    alert.review_status = requested_status
    if comment is not None:
        alert.reviewer_comments = str(comment).strip() or None
    if requested_status == "resolved":
        alert.resolved_at = datetime.now(timezone.utc)
    else:
        alert.resolved_at = None
    alert.updated_at = datetime.now(timezone.utc)

    event = RiskAlertEvent(
        alert_id=alert.id,
        action=action,
        actor=actor,
        comment=alert.reviewer_comments if comment is not None else None,
        previous_status=previous_status,
        new_status=requested_status,
    )
    db.add(event)
    db.commit()
    db.refresh(alert)
    return serialize_alert(alert)


@router.post("/api/risk/analyze")
def analyze_risk(payload: dict | None = None, db: Session = Depends(get_db)):
    payload = payload or {}
    overrides = payload.get("thresholds")
    if not isinstance(overrides, dict):
        overrides = {
            key: payload[key]
            for key in (
                "low_max",
                "medium_max",
                "high_max",
                "approval_value",
                "unusual_amount_multiplier",
                "frequency_per_day",
                "duplicate_window_days",
            )
            if key in payload
        }
    try:
        thresholds = resolve_thresholds(overrides)
        result = run_analysis(
            db,
            thresholds=thresholds,
            triggered_by=str(payload.get("triggered_by") or "manual"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["overview"] = overview(db)
    return result


@router.get("/api/risk/channels")
def risk_channels(db: Session = Depends(get_db)):
    return channel_analytics(db)


@router.get("/api/risk/trends")
def risk_trends(
    start_date: str | None = None,
    end_date: str | None = None,
    granularity: str = "daily",
    db: Session = Depends(get_db),
):
    try:
        return trend_analytics(db, start_date=start_date, end_date=end_date, granularity=granularity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/risk/geography")
def risk_geography(
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
    db: Session = Depends(get_db),
):
    return geography_analytics(db, country=country, state=state, city=city)


def _geo_kwargs(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    max_markers: int = 2000,
    geocode: bool = True,
    risk_source: str | None = None,
) -> dict:
    kwargs = {
        "start_date": start_date,
        "end_date": end_date,
        "country": country,
        "risk_level": risk_level,
        "channel": channel,
        "min_amount": min_amount,
        "max_markers": max(1, min(max_markers, 5000)),
        "allow_geocode": geocode,
        "risk_source": risk_source,
    }
    if city is not None:
        kwargs["city"] = city
    return kwargs


@router.get("/api/risk/geography/options")
def risk_geography_options(db: Session = Depends(get_db)):
    options = geography_filter_options(db)
    return {
        "options": options,
        "risk_levels": options.get("risk_levels", ["low", "medium", "high", "critical"]),
        "risk_sources": options.get("risk_sources", ["calculated", "imported"]),
        "geocoding": geocoding_status(),
    }


@router.get("/api/risk/geography/summary")
def risk_geography_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    max_markers: int = 2000,
    geocode: bool = True,
    risk_source: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return geography_insights(
            db,
            **_geo_kwargs(
                start_date,
                end_date,
                country,
                city,
                risk_level,
                channel,
                min_amount,
                max_markers,
                geocode,
                risk_source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/risk/geography/map")
def risk_geography_map(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    max_markers: int = 2000,
    geocode: bool = True,
    risk_source: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return geography_map(
            db,
            **_geo_kwargs(
                start_date,
                end_date,
                country,
                city,
                risk_level,
                channel,
                min_amount,
                max_markers,
                geocode,
                risk_source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/risk/geography/countries")
def risk_geography_countries(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    risk_source: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return geography_countries(
            db,
            **_geo_kwargs(
                start_date,
                end_date,
                country,
                city,
                risk_level,
                channel,
                min_amount,
                risk_source=risk_source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/risk/geography/cities")
def risk_geography_cities(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    risk_source: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return geography_cities(
            db,
            **_geo_kwargs(
                start_date,
                end_date,
                country,
                city,
                risk_level,
                channel,
                min_amount,
                risk_source=risk_source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/risk/geography/high-risk-locations")
def risk_geography_high_risk_locations(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    risk_source: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return geography_high_risk_locations(
            db,
            **_geo_kwargs(
                start_date,
                end_date,
                country,
                city,
                risk_level,
                channel,
                min_amount,
                risk_source=risk_source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/risk/geography/trend")
def risk_geography_trend(
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    risk_level: str | None = None,
    channel: str | None = None,
    min_amount: float | None = None,
    risk_source: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return geography_trend(
            db,
            **_geo_kwargs(
                start_date,
                end_date,
                country,
                city,
                risk_level,
                channel,
                min_amount,
                risk_source=risk_source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/risk/investigate")
async def investigate_risk(payload: dict, db: Session = Depends(get_db)):
    alert_id = payload.get("alert_id") or payload.get("id")
    if alert_id is None:
        raise HTTPException(status_code=422, detail="alert_id is required")
    alert = _alert_or_404(db, str(alert_id))
    investigation = build_investigation(db, alert)
    question = (
        f"Investigate risk alert {alert.alert_code} in category {alert.risk_category}. "
        "Explain why the alert was generated, summarise the supporting evidence, separate "
        "verified facts from hypotheses, identify missing evidence and recommend human-review "
        "steps. Do not calculate authoritative financial totals and do not invent evidence. "
        "Every alert is a potential anomaly, not proof of fraud."
    )
    evidence_text = json.dumps(
        {
            "verified_facts": investigation["verified_facts"],
            "evidence": investigation["evidence"],
            "missing_evidence": investigation["missing_evidence"],
            "alert": {
                "alert_id": alert.alert_code,
                "category": alert.risk_category,
                "severity": alert.severity,
                "risk_score": alert.risk_score,
                "amount": alert.amount_value,
                "currency": alert.currency,
                "detected_at": investigation["alert"]["detected_at"],
            },
        },
        default=str,
    )
    try:
        ai_result = await explain_finding(question, evidence_text)
    except Exception as exc:
        ai_result = {
            "answer": "AI investigation assistant is unavailable. Use the deterministic facts and evidence below for manual review.",
            "model": None,
            "retrieved_sources": [],
            "fallback": True,
            "error_category": type(exc).__name__,
        }
    investigation["ai"] = ai_result
    investigation["question"] = str(payload.get("question") or question)
    return investigation


@router.get("/api/risk/analysis-runs")
def risk_analysis_runs(limit: int = 20, db: Session = Depends(get_db)):
    runs = db.query(RiskAnalysisRun).order_by(RiskAnalysisRun.id.desc()).limit(max(1, min(limit, 100))).all()
    return [
        {
            "id": run.id,
            "status": run.status,
            "triggered_by": run.triggered_by,
            "thresholds": run.thresholds,
            "transaction_count": run.transaction_count,
            "alert_count": run.alert_count,
            "critical_count": run.critical_count,
            "high_count": run.high_count,
            "medium_count": run.medium_count,
            "low_count": run.low_count,
            "errors": run.errors,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration_ms": run.duration_ms,
        }
        for run in runs
    ]
