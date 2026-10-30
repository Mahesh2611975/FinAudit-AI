"""Deterministic financial analytics derived from persisted transaction records.

Every number returned by this module is computed from database rows. No LLM is
involved in any calculation, so the same inputs always produce the same totals.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from .finance_utils import money_to_decimal, round_currency
from .models import Finding, TransactionRecord

VALID_MONTHS = range(1, 13)


class AnalyticsInputError(ValueError):
    """Raised when a requested reporting period is invalid."""


def _validate_period(year: int, month: int) -> str:
    if not isinstance(year, int) or year < 1900 or year > 2200:
        raise AnalyticsInputError("year must be an integer between 1900 and 2200")
    if not isinstance(month, int) or month not in VALID_MONTHS:
        raise AnalyticsInputError("month must be between 1 and 12")
    return f"{year:04d}-{month:02d}"


def _previous_period(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def load_month_transactions(db: Session, year: int, month: int) -> list[TransactionRecord]:
    prefix = _validate_period(year, month)
    return (
        db.query(TransactionRecord)
        .filter(TransactionRecord.date.like(f"{prefix}%"))
        .order_by(TransactionRecord.date.asc(), TransactionRecord.id.asc())
        .all()
    )


def signed_amount(transaction: TransactionRecord) -> Decimal:
    """Return the cash-flow sign for a transaction using persisted fields only."""
    if transaction.signed_amount is not None:
        return money_to_decimal(transaction.signed_amount)
    amount = money_to_decimal(transaction.amount)
    direction = (transaction.direction or "").strip().lower()
    if direction == "debit":
        return -abs(amount)
    if direction == "credit":
        return abs(amount)
    return amount


def _money(value: Decimal) -> float:
    return float(round_currency(value))


def _category_bucket(
    buckets: dict[str, dict[str, object]], category: str, value: Decimal
) -> None:
    bucket = buckets.setdefault(
        category, {"amount": Decimal("0"), "transaction_count": 0}
    )
    bucket["amount"] = bucket["amount"] + value  # type: ignore[operator]
    bucket["transaction_count"] = int(bucket["transaction_count"]) + 1


def summarise_transactions(transactions: Iterable[TransactionRecord]) -> dict:
    total_spending = Decimal("0")
    total_credits = Decimal("0")
    outgoing: list[float] = []
    spending_count = 0
    credit_count = 0
    zero_count = 0
    buckets: dict[str, dict[str, object]] = {}
    uncategorized: list[TransactionRecord] = []
    missing_direction: list[TransactionRecord] = []
    missing_amount: list[TransactionRecord] = []
    transaction_count = 0

    for transaction in transactions:
        transaction_count += 1
        if transaction.amount is None:
            missing_amount.append(transaction)
        direction = (transaction.direction or "").strip().lower()
        if not direction or direction == "unknown":
            missing_direction.append(transaction)
        if not (transaction.category or "").strip() or transaction.category == "Uncategorized":
            uncategorized.append(transaction)

        value = signed_amount(transaction)
        if value < 0:
            spend = abs(value)
            total_spending += spend
            outgoing.append(float(spend))
            spending_count += 1
            _category_bucket(
                buckets, transaction.category or "Uncategorized", spend
            )
        elif value > 0:
            total_credits += value
            credit_count += 1
        else:
            zero_count += 1

    net_cash_flow = total_credits - total_spending
    average_outgoing = (
        sum(outgoing) / len(outgoing) if outgoing else 0.0
    )
    largest_expense = max(outgoing) if outgoing else 0.0
    categories = [
        {
            "category": category,
            "amount": _money(bucket["amount"]),  # type: ignore[arg-type]
            "transaction_count": int(bucket["transaction_count"]),
        }
        for category, bucket in buckets.items()
    ]
    categories.sort(key=lambda item: item["amount"], reverse=True)

    return {
        "total_spending": _money(total_spending),
        "total_credits": _money(total_credits),
        "net_cash_flow": _money(net_cash_flow),
        "transaction_count": transaction_count,
        "spending_transaction_count": spending_count,
        "credit_transaction_count": credit_count,
        "zero_amount_count": zero_count,
        "average_outgoing": round(average_outgoing, 2),
        "largest_expense": round(largest_expense, 2),
        "category_totals": categories,
        "uncategorized_count": len(uncategorized),
        "uncategorized_transaction_ids": [
            item.transaction_id for item in uncategorized[:20] if item.transaction_id
        ],
        "missing_direction_count": len(missing_direction),
        "missing_amount_count": len(missing_amount),
    }


def month_stats(db: Session, year: int, month: int) -> dict:
    transactions = load_month_transactions(db, year, month)
    summary = summarise_transactions(transactions)
    return {
        "year": year,
        "month": month,
        "spend": summary["total_spending"],
        "received": summary["total_credits"],
        "net": summary["net_cash_flow"],
        "transaction_count": summary["transaction_count"],
        "has_transactions": bool(transactions),
    }


def monthly_comparison(db: Session, year: int, month: int) -> dict:
    current = month_stats(db, year, month)
    previous_year, previous_month = _previous_period(year, month)
    previous = month_stats(db, previous_year, previous_month)
    available = previous["transaction_count"] > 0

    result = {
        "current": current,
        "previous": previous,
        "available": available,
    }
    if available:
        spend_change = round(current["spend"] - previous["spend"], 2)
        received_change = round(current["received"] - previous["received"], 2)
        net_change = round(current["net"] - previous["net"], 2)
        result.update(
            {
                "spend_change": spend_change,
                "spend_change_pct": (
                    round(spend_change / previous["spend"] * 100, 2)
                    if previous["spend"]
                    else None
                ),
                "received_change": received_change,
                "net_change": net_change,
            }
        )
    else:
        result.update(
            {
                "spend_change": None,
                "spend_change_pct": None,
                "received_change": None,
                "net_change": None,
                "note": (
                    f"No transactions recorded for {previous_year:04d}-"
                    f"{previous_month:02d}; month-over-month comparison is unavailable."
                ),
            }
        )
    return result


def monthly_anomalies(
    db: Session,
    year: int,
    month: int,
    transactions: list[TransactionRecord] | None = None,
) -> dict:
    transactions = transactions if transactions is not None else load_month_transactions(db, year, month)
    run_ids = sorted(
        {item.audit_run_id for item in transactions if item.audit_run_id is not None}
    )
    if not run_ids:
        return {
            "findings": [],
            "summary": {
                "finding_count": 0,
                "high_risk_count": 0,
                "medium_risk_count": 0,
                "low_risk_count": 0,
                "by_type": {},
            },
            "linked_audit_run_ids": [],
            "notes": [
                "No saved audit runs are linked to transactions in this period, "
                "so no saved findings could be matched."
            ],
        }

    transaction_ids = {
        item.transaction_id for item in transactions if item.transaction_id
    }
    source_files = {item.source_file for item in transactions if item.source_file}
    findings = (
        db.query(Finding)
        .filter(Finding.run_id.in_(run_ids))
        .order_by(Finding.id.asc())
        .all()
    )

    linked: list[Finding] = []
    unmatched = 0
    for finding in findings:
        if finding.transaction_id and finding.transaction_id in transaction_ids:
            linked.append(finding)
        elif not finding.transaction_id and finding.source_file in source_files:
            linked.append(finding)
        else:
            unmatched += 1

    by_type: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for finding in linked:
        by_type[finding.finding_type] = by_type.get(finding.finding_type, 0) + 1
        by_risk[finding.risk_level] = by_risk.get(finding.risk_level, 0) + 1

    notes: list[str] = []
    if not linked:
        notes.append(
            "Saved audit findings exist for linked audit runs, but none could be "
            "matched to a transaction in this period."
        )
    if unmatched:
        notes.append(
            f"{unmatched} saved finding(s) from linked audit runs were not tied to "
            "this period's transaction IDs and are excluded from the anomaly list."
        )

    return {
        "findings": [
            {
                "id": finding.id,
                "run_id": finding.run_id,
                "transaction_id": finding.transaction_id,
                "source_file": finding.source_file,
                "finding_type": finding.finding_type,
                "risk_level": finding.risk_level,
                "amount": finding.amount,
                "summary": finding.summary,
                "evidence": finding.evidence,
                "created_at": (
                    finding.created_at.isoformat() if finding.created_at else None
                ),
            }
            for finding in linked
        ],
        "summary": {
            "finding_count": len(linked),
            "high_risk_count": by_risk.get("high", 0),
            "medium_risk_count": by_risk.get("medium", 0),
            "low_risk_count": by_risk.get("low", 0),
            "by_type": by_type,
        },
        "linked_audit_run_ids": run_ids,
        "notes": notes,
    }


def monthly_analysis(db: Session, year: int, month: int) -> dict:
    _validate_period(year, month)
    transactions = load_month_transactions(db, year, month)
    summary = summarise_transactions(transactions)
    comparison = monthly_comparison(db, year, month)
    anomalies = monthly_anomalies(db, year, month, transactions)

    data_quality_notes: list[str] = []
    if not transactions:
        data_quality_notes.append(
            f"No transactions were found for {year:04d}-{month:02d}."
        )
    if summary["uncategorized_count"]:
        data_quality_notes.append(
            f"{summary['uncategorized_count']} transaction(s) are uncategorized; "
            "category totals exclude no rows but their labels are uncertain."
        )
    if summary["missing_direction_count"]:
        data_quality_notes.append(
            f"{summary['missing_direction_count']} transaction(s) have no debit/credit "
            "direction; their sign was inferred from the signed amount, so positive "
            "amounts were treated as credits and negative amounts as spending."
        )
    if summary["missing_amount_count"]:
        data_quality_notes.append(
            f"{summary['missing_amount_count']} transaction(s) have no amount and "
            "contribute zero to the totals."
        )
    data_quality_notes.extend(anomalies["notes"])

    return {
        "period": {
            "year": year,
            "month": month,
            "label": f"{year:04d}-{month:02d}",
        },
        "has_transactions": bool(transactions),
        "summary": summary,
        "category_totals": summary["category_totals"],
        "comparison": comparison,
        "anomalies": anomalies["findings"],
        "anomaly_summary": anomalies["summary"],
        "linked_audit_run_ids": anomalies["linked_audit_run_ids"],
        "data_quality": {
            "transaction_count": summary["transaction_count"],
            "uncategorized_count": summary["uncategorized_count"],
            "uncategorized_transaction_ids": summary[
                "uncategorized_transaction_ids"
            ],
            "missing_direction_count": summary["missing_direction_count"],
            "missing_amount_count": summary["missing_amount_count"],
            "linked_audit_run_ids": anomalies["linked_audit_run_ids"],
            "notes": data_quality_notes,
        },
    }
