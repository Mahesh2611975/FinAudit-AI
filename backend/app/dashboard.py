"""Dashboard aggregates computed directly from persisted records.

Every figure returned here is derived from SQLite rows.  The ``scope`` block
documents the exact calculation rules so that the UI never has to guess what a
KPI means.  Nothing is hard-coded and no fragile placeholder data is used.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import (
    ReconciliationResult,
    RiskAlert,
    TransactionRecord,
)


def _money(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:
        return 0.0


def _positive(value: Any) -> float:
    """Absolute value of a stored amount (imports may store signed or unsigned)."""
    return abs(_money(value))


def _is_debit(direction: Any, signed_amount: Any, amount: Any) -> bool:
    direction_text = str(direction or "").strip().lower()
    if direction_text in {"debit", "debit_like", "outgoing", "sent", "payment", "withdrawal", "credit_card"}:
        return True
    if direction_text in {"credit", "incoming", "received", "deposit", "refund"}:
        return False
    amount_value = _money(signed_amount if signed_amount is not None else amount)
    return amount_value < 0


def _month_key(value: Any) -> str | None:
    """Return YYYY-MM for the common date formats found in imported statements."""
    text = str(value or "").strip()
    if len(text) >= 7 and text[4] == "-" and text[5:7].isdigit():
        return text[:7]
    return None


def _monthly_series(transactions: list[TransactionRecord], months: int) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "spend": 0.0, "received": 0.0}
    )
    for item in transactions:
        key = _month_key(item.date)
        if not key:
            continue
        bucket = buckets[key]
        bucket["count"] += 1
        amount = _positive(item.amount if item.amount is not None else item.signed_amount)
        if _is_debit(item.direction, item.signed_amount, item.amount):
            bucket["spend"] += amount
        else:
            bucket["received"] += amount

    ordered = sorted(buckets)[-months:]
    return [
        {
            "month": key,
            "transactions": int(buckets[key]["count"]),
            "spending": _money(buckets[key]["spend"]),
            "received": _money(buckets[key]["received"]),
            "net": _money(buckets[key]["received"] - buckets[key]["spend"]),
        }
        for key in ordered
    ]


def _category_counts(transactions: list[TransactionRecord]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in transactions:
        counts[str(item.category or "Uncategorized")] += 1
    return dict(counts)


def dashboard_overview(db: Session, *, activity_months: int = 6, recent_limit: int = 8) -> dict[str, Any]:
    """Build a complete, database-backed dashboard payload."""
    transactions: list[TransactionRecord] = db.query(TransactionRecord).all()

    total_spending = 0.0
    total_received = 0.0
    for item in transactions:
        amount = _positive(item.amount if item.amount is not None else item.signed_amount)
        if _is_debit(item.direction, item.signed_amount, item.amount):
            total_spending += amount
        else:
            total_received += amount
    total_spending = _money(total_spending)
    total_received = _money(total_received)

    # Reconciliation metrics come from the latest persisted run so the dashboard
    # reflects current reconciliation state rather than accumulating all history.
    recon_counts: dict[str, int] = {}
    latest_recon_run_id = None
    latest_recon_run = None
    latest_recon = (
        db.query(ReconciliationResult.run_id)
        .order_by(ReconciliationResult.run_id.desc())
        .first()
    )
    if latest_recon and latest_recon[0] is not None:
        latest_recon_run_id = int(latest_recon[0])
        recon_rows = (
            db.query(ReconciliationResult.status, func.count(ReconciliationResult.id))
            .filter(ReconciliationResult.run_id == latest_recon_run_id)
            .group_by(ReconciliationResult.status)
            .all()
        )
        recon_counts = {str(status or "unknown"): int(count) for status, count in recon_rows}
        try:
            from .models import ReconciliationRun
            latest_recon_run = db.query(ReconciliationRun).filter(ReconciliationRun.id == latest_recon_run_id).first()
        except Exception:
            latest_recon_run = None

    # Confirmed matches only. Possible/suggested matches remain pending review.
    matched_transactions = recon_counts.get("matched", 0) + recon_counts.get("reconciled", 0) + recon_counts.get("exact", 0)
    unmatched_transactions = recon_counts.get("bank_only", 0) + recon_counts.get("ledger_only", 0) + recon_counts.get("unmatched", 0)
    possible_matches = recon_counts.get("ambiguous", 0) + recon_counts.get("possible", 0) + recon_counts.get("suggested", 0)
    amount_mismatch_count = recon_counts.get("amount_mismatch", 0)
    date_mismatch_count = recon_counts.get("date_mismatch", 0)
    invalid_record_count = recon_counts.get("invalid_records", 0)
    duplicate_candidate_count = recon_counts.get("duplicate_candidate", 0)
    ambiguous_count = recon_counts.get("ambiguous", 0)
    bank_record_count = int(getattr(latest_recon_run, "bank_record_count", 0) or 0) if latest_recon_run else 0
    ledger_record_count = int(getattr(latest_recon_run, "ledger_record_count", 0) or 0) if latest_recon_run else 0
    denom = max(1.0, (bank_record_count + ledger_record_count) / 2.0) if (bank_record_count or ledger_record_count) else 0.0
    match_rate = round((matched_transactions / denom) * 100, 2) if denom > 0 else None
    unreconciled_value = getattr(latest_recon_run, "unreconciled_value", None) if latest_recon_run else None
    total_difference = getattr(latest_recon_run, "difference", None) if latest_recon_run else None

    alert_rows = (
        db.query(RiskAlert.severity, func.count(RiskAlert.id))
        .group_by(RiskAlert.severity)
        .all()
    )
    alert_counts = {str(severity or "unknown"): int(count) for severity, count in alert_rows}

    recent_alerts = (
        db.query(RiskAlert)
        .order_by(RiskAlert.detected_at.desc(), RiskAlert.id.desc())
        .limit(recent_limit)
        .all()
    )
    recent_transactions = sorted(
        transactions,
        key=lambda item: (str(item.date or ""), item.id),
        reverse=True,
    )[:recent_limit]

    metrics = {
        "total_transactions": len(transactions),
        "total_spending": total_spending,
        "total_received": total_received,
        "net_cash_flow": _money(total_received - total_spending),
        "matched_transactions": matched_transactions,
        "unmatched_transactions": unmatched_transactions,
        "possible_matches": possible_matches,
        "reconciliation_match_rate": match_rate,
        "bank_only_count": recon_counts.get("bank_only", 0),
        "ledger_only_count": recon_counts.get("ledger_only", 0),
        "amount_mismatch_count": amount_mismatch_count,
        "date_mismatch_count": date_mismatch_count,
        "ambiguous_count": ambiguous_count,
        "invalid_record_count": invalid_record_count,
        "duplicate_candidate_count": duplicate_candidate_count,
        "unreconciled_value": unreconciled_value,
        "reconciliation_total_difference": total_difference,
        "latest_reconciliation_run_id": latest_recon_run_id,
        "total_risk_alerts": int(sum(alert_counts.values())),
        "high_risk_alerts": alert_counts.get("high", 0),
        "critical_alerts": alert_counts.get("critical", 0),
    }

    return {
        "metrics": metrics,
        "charts": {
            "transaction_activity": _monthly_series(transactions, activity_months),
            "spending_trends": [
                {"month": row["month"], "spending": row["spending"]}
                for row in _monthly_series(transactions, activity_months)
            ],
            "risk_distribution": [
                {"severity": name, "count": alert_counts.get(name, 0)}
                for name in ("critical", "high", "medium", "low")
            ],
            "reconciliation_status": [
                {"status": name, "count": recon_counts.get(name, 0)}
                for name in (
                    "matched",
                    "bank_only",
                    "ledger_only",
                    "amount_mismatch",
                    "date_mismatch",
                    "ambiguous",
                    "duplicate_candidate",
                    "invalid_records",
                )
            ],
            "category_distribution": [
                {"category": name, "count": count}
                for name, count in sorted(
                    _category_counts(transactions).items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )[:10]
            ],
        },
        "recent_activity": [
            {
                "kind": "transaction",
                "id": item.id,
                "reference": item.transaction_id,
                "date": item.date,
                "description": item.vendor or item.description or "—",
                "amount": _money(item.amount if item.amount is not None else item.signed_amount),
                "source_file": item.source_file,
                "source_row": item.source_row,
            }
            for item in recent_transactions
        ]
        + [
            {
                "kind": "risk_alert",
                "id": alert.id,
                "reference": alert.alert_code,
                "date": alert.detected_at.date().isoformat() if alert.detected_at else None,
                "description": alert.risk_category,
                "amount": _money(alert.amount_value),
                "severity": alert.severity,
                "risk_score": alert.risk_score,
                "source_file": (alert.evidence or {}).get("source_file")
                if isinstance(alert.evidence, dict)
                else None,
            }
            for alert in recent_alerts
        ],
        "scope": {
            "data_sources": [
                "transaction_records",
                "reconciliation_results",
                "risk_alerts",
            ],
            "spending": "Sum of absolute amounts for debit-like transactions across all stored imports.",
            "received": "Sum of absolute amounts for credit-like transactions across all stored imports.",
            "net_cash_flow": "total_received - total_spending.",
            "reconciliation_scope": f"Latest reconciliation run only (run_id={latest_recon_run_id}) when available; no history is accumulated.",
            "matched_transactions": "Confirmed matched reconciliation results in the latest run. Possible/suggested matches are not counted as confirmed.",
            "unmatched_transactions": "reconciliation_results rows with status bank_only/ledger_only/unmatched in the latest run.",
            "possible_matches": "reconciliation_results rows with status ambiguous/possible/suggested in the latest run (require human confirmation).",
            "reconciliation_metrics": "Match rate, counts, unreconciled value and total difference are derived from the latest persisted reconciliation run/results.",
            "risk_alerts": "All persisted deterministic alerts; severity counted from stored risk_engine output.",
            "activity_charts": f"Grouped by transaction date (YYYY-MM), last {activity_months} populated months.",
            "category_distribution": "Count of persisted transaction rows by stored category, top 10 categories.",
            "no_fake_data": True,
        },
    }
