"""Deterministic financial risk and fraud-intelligence calculations.

Every score, total and count in this module is derived from persisted database
rows.  Alerts are anomaly indicators for human review, never fraud convictions.
No LLM participates in any calculation.
"""
from __future__ import annotations

import hashlib
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import (
    Invoice,
    InvoiceDiscrepancy,
    InvoicePayment,
    ReconciliationResult,
    ReconciliationRun,
    RiskAlert,
    RiskAlertEvent,
    RiskAnalysisRun,
    TransactionRecord,
    Vendor,
)


SUPPORTED_CHANNELS = (
    "Online Banking",
    "Mobile Banking",
    "ATM",
    "UPI",
    "NEFT",
    "RTGS",
    "IMPS",
    "Wire Transfer",
    "POS",
)

CHANNEL_KEYWORDS = {
    "UPI": ("upi", "@upi", "vpa", "gpay", "google pay", "phonepe", "paytm", "bhim"),
    "NEFT": ("neft",),
    "RTGS": ("rtgs",),
    "IMPS": ("imps",),
    "ATM": ("atm", "cash withdrawal", "cash withdraw"),
    "POS": ("pos", "point of sale", "card swipe", "merchant terminal"),
    "Wire Transfer": ("wire transfer", "wire", "swift", "international transfer"),
    "Mobile Banking": ("mobile banking", "mobile app"),
    "Online Banking": ("online banking", "netbanking", "internet banking", "net banking"),
}

CATEGORY_LABELS = (
    "Duplicate Payment",
    "Duplicate Invoice",
    "Unusual Transaction Amount",
    "Invoice / PO Mismatch",
    "Goods Receipt Mismatch",
    "Suspicious Vendor Payment Pattern",
    "Repeated Vendor Discrepancies",
    "Missing Approval Evidence",
    "Unusual Transaction Frequency",
    "Bank Reconciliation Anomaly",
    "Payment Anomaly",
)

PO_MISMATCH_TYPES = {
    "total_mismatch",
    "subtotal_mismatch",
    "tax_mismatch",
    "unit_price_mismatch",
    "quantity_mismatch",
    "currency_mismatch",
    "vendor_mismatch",
    "item_not_on_po",
}
GR_MISMATCH_TYPES = {
    "goods_not_received",
    "partial_delivery",
    "goods_rejected",
    "missing_goods_receipt",
}
MISSING_EVIDENCE_TYPES = {
    "missing_supporting_document",
    "missing_purchase_order",
    "extraction_incomplete",
}
VENDOR_PATTERN_TYPES = {"unusual_vendor_pattern"}
PAYMENT_ANOMALY_TYPES = {
    "overpayment",
    "partial_payment",
    "unpaid_invoice",
    "unverified_payment_candidate",
    "missing_invoice_total",
}
DUPLICATE_INVOICE_TYPES = {"duplicate_invoice", "possible_duplicate_invoice"}

SEVERITY_BASE = {"low": 30, "medium": 50, "high": 72, "critical": 88}
REVIEW_STATUSES = ("open", "investigating", "resolved", "dismissed")


@dataclass(frozen=True)
class RiskThresholds:
    low_max: int = 29
    medium_max: int = 59
    high_max: int = 79
    approval_value: float = 100000.0
    unusual_amount_multiplier: float = 3.0
    frequency_per_day: int = 5
    duplicate_window_days: int = 7

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def critical_min(self) -> int:
        return min(self.high_max + 1, 100)


DEFAULT_THRESHOLDS = RiskThresholds()

_ENV_MAP = {
    "low_max": "RISK_LOW_MAX",
    "medium_max": "RISK_MEDIUM_MAX",
    "high_max": "RISK_HIGH_MAX",
    "approval_value": "RISK_APPROVAL_VALUE",
    "unusual_amount_multiplier": "RISK_UNUSUAL_AMOUNT_MULTIPLIER",
    "frequency_per_day": "RISK_FREQUENCY_PER_DAY",
    "duplicate_window_days": "RISK_DUPLICATE_WINDOW_DAYS",
}

_INT_FIELDS = {"low_max", "medium_max", "high_max", "frequency_per_day", "duplicate_window_days"}
_FLOAT_FIELDS = {"approval_value", "unusual_amount_multiplier"}


def _coerce_number(key: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        if key in _INT_FIELDS:
            return int(float(value))
        if key in _FLOAT_FIELDS:
            number = float(value)
            return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
    return value


def resolve_thresholds(overrides: dict[str, Any] | None = None) -> RiskThresholds:
    values = DEFAULT_THRESHOLDS.as_dict()
    for key, env_name in _ENV_MAP.items():
        parsed = _coerce_number(key, os.getenv(env_name))
        if parsed is not None:
            values[key] = parsed
    if overrides:
        for key, raw in overrides.items():
            if key not in values:
                continue
            parsed = _coerce_number(key, raw)
            if parsed is not None:
                values[key] = parsed

    thresholds = RiskThresholds(**values)
    if not (0 <= thresholds.low_max < thresholds.medium_max < thresholds.high_max < 100):
        raise ValueError(
            "Risk thresholds must satisfy 0 <= low_max < medium_max < high_max < 100"
        )
    if thresholds.approval_value < 0 or thresholds.unusual_amount_multiplier <= 0:
        raise ValueError("Risk thresholds must be positive")
    if thresholds.frequency_per_day < 1 or thresholds.duplicate_window_days < 1:
        raise ValueError("Frequency and duplicate window thresholds must be at least 1")
    return thresholds


def clamp_score(value: Any) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def classify_risk_score(score: Any, thresholds: RiskThresholds | None = None) -> str:
    threshold = thresholds or DEFAULT_THRESHOLDS
    value = clamp_score(score)
    if value <= threshold.low_max:
        return "low"
    if value <= threshold.medium_max:
        return "medium"
    if value <= threshold.high_max:
        return "high"
    return "critical"


def risk_score_bands(thresholds: RiskThresholds | None = None) -> list[dict[str, Any]]:
    threshold = thresholds or DEFAULT_THRESHOLDS
    return [
        {"name": "Low", "min": 0, "max": threshold.low_max},
        {"name": "Medium", "min": threshold.low_max + 1, "max": threshold.medium_max},
        {"name": "High", "min": threshold.medium_max + 1, "max": threshold.high_max},
        {"name": "Critical", "min": threshold.critical_min, "max": 100},
    ]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _norm(value: Any) -> str:
    return "".join(ch for ch in _text(value).lower() if ch.isalnum())


def _amount(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _abs_amount(record: TransactionRecord) -> float:
    return abs(_amount(record.amount))


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    return text or None


def _fingerprint(category: str, *parts: Any) -> str:
    payload = "|".join([category, *[_text(part) for part in parts]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_fingerprint(category: str, *parts: Any) -> str:
    return _fingerprint(category, *parts)[:128]


def _record_key(record: TransactionRecord) -> str:
    if _text(record.transaction_id):
        return f"txn:{record.transaction_id}"
    return f"row:{record.source_file}:{record.source_row}:{record.date}:{record.amount}:{record.vendor}"


def load_analysis_transactions(db: Session) -> list[TransactionRecord]:
    """Return one current snapshot per source file.

    Users frequently re-upload the same statement.  Comparing every historical
    copy would manufacture duplicate alerts.  For each source file, the latest
    audit run is analysed; records with no audit run are retained.
    """
    records = db.query(TransactionRecord).order_by(TransactionRecord.id.asc()).all()
    groups: dict[str, list[TransactionRecord]] = defaultdict(list)
    for record in records:
        source = _text(record.source_file) or f"run:{record.audit_run_id or 'none'}"
        groups[source].append(record)

    selected: list[TransactionRecord] = []
    for items in groups.values():
        run_ids = [item.audit_run_id for item in items if item.audit_run_id is not None]
        if run_ids:
            latest = max(run_ids)
            selected.extend(item for item in items if item.audit_run_id == latest)
        else:
            selected.extend(items)
    selected.sort(key=lambda item: (item.date or "", item.id or 0))
    return selected


def is_debit_like(record: TransactionRecord) -> bool:
    direction = _text(record.direction).lower()
    record_type = _text(record.type).lower()
    signed = record.signed_amount
    if direction in {"credit", "cr", "received", "deposit"}:
        return False
    if direction in {"debit", "dr", "payment", "withdrawal", "withdraw"}:
        return True
    if "credit" in record_type:
        return False
    if "debit" in record_type or "withdraw" in record_type:
        return True
    if signed is not None:
        return _amount(signed) < 0
    return True


def infer_channel(record: TransactionRecord | None) -> str:
    if record is None:
        return "Unknown"
    explicit = _text(getattr(record, "channel", None))
    if explicit:
        normalized = explicit.lower().replace("_", " ")
        for channel in SUPPORTED_CHANNELS:
            if normalized == channel.lower() or channel.lower() in normalized:
                return channel
        for channel, keywords in CHANNEL_KEYWORDS.items():
            if any(keyword in normalized for keyword in keywords):
                return channel
    return "Unknown"


def _location_from(record: TransactionRecord | None) -> dict[str, Any]:
    if record is None:
        return {}
    latitude = getattr(record, "latitude", None)
    longitude = getattr(record, "longitude", None)
    latitude = _amount(latitude) if latitude is not None else None
    longitude = _amount(longitude) if longitude is not None else None
    if latitude is not None and not (-90 <= latitude <= 90):
        latitude = None
    if longitude is not None and not (-180 <= longitude <= 180):
        longitude = None
    return {
        "country": _text(getattr(record, "country", None)) or None,
        "state": _text(getattr(record, "state", None)) or None,
        "city": _text(getattr(record, "city", None)) or None,
        "latitude": latitude,
        "longitude": longitude,
    }


def _draft(
    thresholds: RiskThresholds,
    category: str,
    score: Any,
    explanation: str,
    *,
    source_type: str,
    fingerprint: str,
    source_record_id: int | None = None,
    transaction_id: str | None = None,
    invoice_id: int | None = None,
    vendor_id: int | None = None,
    vendor_name: str | None = None,
    amount_value: float | None = None,
    currency: str | None = "INR",
    channel: str | None = None,
    evidence: dict[str, Any] | None = None,
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_score = clamp_score(score)
    loc = location or {}
    return {
        "fingerprint": _payload_fingerprint(fingerprint, category) if len(fingerprint) > 120 else fingerprint,
        "source_type": source_type,
        "source_record_id": source_record_id,
        "transaction_id": transaction_id,
        "invoice_id": invoice_id,
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "risk_category": category,
        "severity": classify_risk_score(final_score, thresholds),
        "risk_score": final_score,
        "amount_value": round(_amount(amount_value), 2) if amount_value is not None else None,
        "currency": currency or "INR",
        "channel": channel,
        "explanation": explanation,
        "evidence": evidence or {},
        "country": loc.get("country"),
        "state": loc.get("state"),
        "city": loc.get("city"),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
    }


def detect_duplicate_payments(
    records: Iterable[TransactionRecord], thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    groups: dict[str, list[TransactionRecord]] = defaultdict(list)
    for record in records:
        if not is_debit_like(record):
            continue
        if _abs_amount(record) <= 0:
            continue
        key = _text(record.duplicate_key)
        if not key:
            key = "|".join(
                [
                    _text(record.date),
                    _text(record.direction),
                    f"{_abs_amount(record):.2f}",
                    _norm(record.vendor),
                    _norm(record.description),
                    _norm(record.reference or record.upi_ref),
                ]
            )
        groups[key].append(record)

    drafts: list[dict[str, Any]] = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        amount = _abs_amount(items[0])
        exposure = amount * (len(items) - 1)
        identifiers = sorted({_text(item.transaction_id) for item in items if _text(item.transaction_id)})
        record_ids = sorted(item.id for item in items if item.id is not None)
        score = 82 + min(12, (len(items) - 2) * 3)
        if amount >= thresholds.approval_value:
            score += 4
        vendor_name = next((_text(item.vendor) for item in items if _text(item.vendor)), None)
        drafts.append(
            _draft(
                thresholds,
                "Duplicate Payment",
                score,
                f"{len(items)} outgoing transactions share the same date, amount, vendor and reference pattern. This may be a duplicate payment, or a legitimate recurring or split payment that needs evidence.",
                source_type="transaction_group",
                fingerprint=_payload_fingerprint("duplicate-payment", key),
                source_record_id=record_ids[0] if record_ids else None,
                transaction_id=identifiers[0] if identifiers else None,
                vendor_name=vendor_name,
                amount_value=exposure,
                currency="INR",
                channel=infer_channel(items[0]),
                evidence={
                    "group_key": key,
                    "transaction_ids": identifiers,
                    "record_ids": record_ids,
                    "dates": sorted({_text(item.date) for item in items if _text(item.date)}),
                    "amounts": sorted({round(_abs_amount(item), 2) for item in items}),
                    "source_rows": [
                        {"source_file": item.source_file, "source_row": item.source_row, "id": item.id}
                        for item in items
                    ],
                    "vendor": vendor_name,
                    "match_count": len(items),
                },
                location=_location_from(items[0]),
            )
        )
    return drafts


def detect_unusual_amounts(
    records: Iterable[TransactionRecord], thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    groups: dict[str, list[TransactionRecord]] = defaultdict(list)
    for record in records:
        if not is_debit_like(record) or _abs_amount(record) <= 0:
            continue
        group = _text(record.vendor) or _text(record.account)
        if group:
            groups[group].append(record)

    drafts: list[dict[str, Any]] = []
    for group, items in groups.items():
        if len(items) < 3:
            continue
        amounts = [_abs_amount(item) for item in items]
        median = statistics.median(amounts)
        if median <= 0:
            continue
        deviations = [abs(value - median) for value in amounts]
        mad = statistics.median(deviations)
        threshold = max(median * thresholds.unusual_amount_multiplier, median + 3 * mad)
        for record in items:
            value = _abs_amount(record)
            if value <= threshold or value < max(100.0, median * 2):
                continue
            ratio = value / median if median else 0
            score = 48 + min(28, int(max(0, ratio - 1) * 6))
            if value >= thresholds.approval_value:
                score += 6
            drafts.append(
                _draft(
                    thresholds,
                    "Unusual Transaction Amount",
                    score,
                    f"Transaction amount {value:.2f} is materially above the historical median {median:.2f} for {group}. Verify the supporting invoice, approval and business purpose.",
                    source_type="transaction",
                    fingerprint=_payload_fingerprint("unusual-amount", record.id or _record_key(record)),
                    source_record_id=record.id,
                    transaction_id=record.transaction_id,
                    vendor_name=_text(record.vendor) or None,
                    amount_value=value,
                    channel=infer_channel(record),
                    evidence={
                        "vendor_or_account": group,
                        "transaction_id": record.transaction_id,
                        "date": record.date,
                        "amount": value,
                        "historical_median": round(median, 2),
                        "median_absolute_deviation": round(mad, 2),
                        "comparison_threshold": round(threshold, 2),
                        "sample_size": len(items),
                        "source_file": record.source_file,
                        "source_row": record.source_row,
                    },
                    location=_location_from(record),
                )
            )
    return drafts


def detect_missing_approval_transactions(
    records: Iterable[TransactionRecord], thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for record in records:
        if not is_debit_like(record):
            continue
        value = _abs_amount(record)
        if value < thresholds.approval_value:
            continue
        has_reference = bool(_text(record.reference) or _text(record.upi_ref))
        has_party = bool(_text(record.vendor) or _text(record.description))
        if has_reference and has_party:
            continue
        score = 62
        if not has_reference and not has_party:
            score = 70
        elif not has_reference:
            score = 64
        if value >= thresholds.approval_value * 2:
            score += 8
        missing = []
        if not has_reference:
            missing.append("external payment reference")
        if not has_party:
            missing.append("vendor or description")
        drafts.append(
            _draft(
                thresholds,
                "Missing Approval Evidence",
                score,
                f"High-value outgoing transaction of {value:.2f} is missing {', '.join(missing)}. Obtain the approval record, invoice and payment evidence before acting.",
                source_type="transaction",
                fingerprint=_payload_fingerprint("missing-approval", record.id or _record_key(record)),
                source_record_id=record.id,
                transaction_id=record.transaction_id,
                vendor_name=_text(record.vendor) or None,
                amount_value=value,
                channel=infer_channel(record),
                evidence={
                    "transaction_id": record.transaction_id,
                    "date": record.date,
                    "amount": value,
                    "missing_evidence": missing,
                    "reference": record.reference,
                    "upi_ref": record.upi_ref,
                    "vendor": record.vendor,
                    "description": record.description,
                    "account": record.account,
                    "source_file": record.source_file,
                    "source_row": record.source_row,
                    "approval_threshold": thresholds.approval_value,
                },
                location=_location_from(record),
            )
        )
    return drafts


def detect_unusual_frequency(
    records: Iterable[TransactionRecord], thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[TransactionRecord]] = defaultdict(list)
    for record in records:
        if not is_debit_like(record):
            continue
        party = _text(record.vendor) or _text(record.account)
        day = _text(record.date)
        if not party or not day:
            continue
        groups[(party, day)].append(record)

    drafts: list[dict[str, Any]] = []
    for (party, day), items in groups.items():
        if len(items) < thresholds.frequency_per_day:
            continue
        total = sum(_abs_amount(item) for item in items)
        score = 50 + min(25, len(items) * 3)
        if total >= thresholds.approval_value:
            score += 5
        drafts.append(
            _draft(
                thresholds,
                "Unusual Transaction Frequency",
                score,
                f"{len(items)} outgoing transactions were recorded for {party} on {day}. Confirm whether this is a legitimate batch or an unusual transaction pattern.",
                source_type="transaction_group",
                fingerprint=_payload_fingerprint("frequency", party.lower(), day),
                source_record_id=items[0].id,
                transaction_id=items[0].transaction_id,
                vendor_name=_text(items[0].vendor) or None,
                amount_value=total,
                channel=infer_channel(items[0]),
                evidence={
                    "party": party,
                    "date": day,
                    "transaction_count": len(items),
                    "total_amount": round(total, 2),
                    "transaction_ids": sorted({_text(item.transaction_id) for item in items if _text(item.transaction_id)}),
                    "record_ids": sorted(item.id for item in items if item.id is not None),
                    "threshold": thresholds.frequency_per_day,
                },
                location=_location_from(items[0]),
            )
        )
    return drafts


def detect_suspicious_vendor_patterns(
    records: Iterable[TransactionRecord], thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float], list[TransactionRecord]] = defaultdict(list)
    for record in records:
        if not is_debit_like(record):
            continue
        vendor = _text(record.vendor)
        value = _abs_amount(record)
        if not vendor or value <= 0:
            continue
        groups[(vendor, round(value, 2))].append(record)

    drafts: list[dict[str, Any]] = []
    for (vendor, value), items in groups.items():
        if len(items) < 3:
            continue
        dates = [_as_date(item.date) for item in items if _as_date(item.date)]
        if dates and (max(dates) - min(dates)).days > thresholds.duplicate_window_days:
            continue
        if value < 1000:
            continue
        total = value * len(items)
        score = 64 + min(18, len(items) * 2)
        if value >= thresholds.approval_value:
            score += 6
        drafts.append(
            _draft(
                thresholds,
                "Suspicious Vendor Payment Pattern",
                score,
                f"{len(items)} payments of {value:.2f} were made to {vendor} within {thresholds.duplicate_window_days} days. Verify the contract, invoices and delivery evidence for this pattern.",
                source_type="transaction_group",
                fingerprint=_payload_fingerprint("vendor-pattern", vendor.lower(), f"{value:.2f}"),
                source_record_id=items[0].id,
                transaction_id=items[0].transaction_id,
                vendor_name=vendor,
                amount_value=total,
                channel=infer_channel(items[0]),
                evidence={
                    "vendor": vendor,
                    "payment_amount": value,
                    "payment_count": len(items),
                    "total_amount": round(total, 2),
                    "dates": sorted({_text(item.date) for item in items if _text(item.date)}),
                    "transaction_ids": sorted({_text(item.transaction_id) for item in items if _text(item.transaction_id)}),
                    "record_ids": sorted(item.id for item in items if item.id is not None),
                },
                location=_location_from(items[0]),
            )
        )
    return drafts


def _invoice_amount(invoice: Invoice) -> float | None:
    if invoice.total_minor is None:
        return None
    return round(abs(invoice.total_minor) / 100, 2)


def detect_duplicate_invoices(
    db: Session, thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    invoices = db.query(Invoice).order_by(Invoice.id.asc()).all()
    drafts: list[dict[str, Any]] = []
    flagged_sets: list[set[int]] = []

    by_number: dict[tuple[str, str], list[Invoice]] = defaultdict(list)
    for invoice in invoices:
        number = _norm(invoice.invoice_number)
        if not number:
            continue
        vendor_key = f"id:{invoice.vendor_id}" if invoice.vendor_id else _norm(invoice.vendor.vendor_name if invoice.vendor else "")
        if not vendor_key:
            continue
        by_number[(vendor_key, number)].append(invoice)

    for (vendor_key, number), group in by_number.items():
        if len(group) < 2:
            continue
        ids = {invoice.id for invoice in group}
        flagged_sets.append(ids)
        amount = sum(_invoice_amount(invoice) or 0 for invoice in group)
        vendor_name = next((invoice.vendor.vendor_name for invoice in group if invoice.vendor), None)
        drafts.append(
            _draft(
                thresholds,
                "Duplicate Invoice",
                88 + min(8, (len(group) - 2) * 4),
                f"{len(group)} invoices share the same vendor and normalized invoice number {number}. Confirm whether this is a re-submission, credit note or separate legitimate purchase.",
                source_type="invoice_group",
                fingerprint=_payload_fingerprint("duplicate-invoice-number", vendor_key, number),
                source_record_id=group[0].id,
                invoice_id=group[0].id,
                vendor_id=group[0].vendor_id,
                vendor_name=vendor_name,
                amount_value=amount,
                currency=group[0].currency,
                evidence={
                    "match_type": "same_invoice_number",
                    "invoice_ids": sorted(ids),
                    "invoice_numbers": sorted({_text(item.invoice_number) for item in group}),
                    "invoice_dates": sorted({_text(item.invoice_date) for item in group if _text(item.invoice_date)}),
                    "totals_minor": [item.total_minor for item in group],
                    "currency": group[0].currency,
                    "match_count": len(group),
                },
            )
        )

    by_amount: dict[tuple[str, int, str, int | None], list[Invoice]] = defaultdict(list)
    for invoice in invoices:
        if invoice.total_minor is None:
            continue
        vendor_key = f"id:{invoice.vendor_id}" if invoice.vendor_id else _norm(invoice.vendor.vendor_name if invoice.vendor else "")
        if not vendor_key:
            continue
        by_amount[(vendor_key, invoice.total_minor, invoice.currency or "", invoice.purchase_order_id)].append(invoice)

    for (vendor_key, total_minor, currency, purchase_order_id), group in by_amount.items():
        if len(group) < 2:
            continue
        ids = {invoice.id for invoice in group}
        if any(ids <= existing for existing in flagged_sets):
            continue
        dates = [_as_date(item.invoice_date) for item in group if _as_date(item.invoice_date)]
        if dates and (max(dates) - min(dates)).days > thresholds.duplicate_window_days:
            continue
        flagged_sets.append(ids)
        vendor_name = next((invoice.vendor.vendor_name for invoice in group if invoice.vendor), None)
        drafts.append(
            _draft(
                thresholds,
                "Duplicate Invoice",
                66 + min(10, (len(group) - 2) * 5),
                f"{len(group)} invoices for the same vendor share the same total within {thresholds.duplicate_window_days} days. Verify that each invoice represents a distinct purchase.",
                source_type="invoice_group",
                fingerprint=_payload_fingerprint("duplicate-invoice-amount", vendor_key, total_minor, currency, purchase_order_id or "", sorted(ids)),
                source_record_id=group[0].id,
                invoice_id=group[0].id,
                vendor_id=group[0].vendor_id,
                vendor_name=vendor_name,
                amount_value=sum(_invoice_amount(invoice) or 0 for invoice in group),
                currency=currency or "INR",
                evidence={
                    "match_type": "same_vendor_amount_window",
                    "invoice_ids": sorted(ids),
                    "invoice_numbers": sorted({_text(item.invoice_number) for item in group if _text(item.invoice_number)}),
                    "invoice_dates": sorted({_text(item.invoice_date) for item in group if _text(item.invoice_date)}),
                    "total_minor": total_minor,
                    "currency": currency,
                    "purchase_order_id": purchase_order_id,
                    "match_count": len(group),
                },
            )
        )
    return drafts


def _discrepancy_category(discrepancy_type: str) -> str | None:
    if discrepancy_type in GR_MISMATCH_TYPES:
        return "Goods Receipt Mismatch"
    if discrepancy_type in PO_MISMATCH_TYPES:
        return "Invoice / PO Mismatch"
    if discrepancy_type in MISSING_EVIDENCE_TYPES:
        return "Missing Approval Evidence"
    if discrepancy_type in VENDOR_PATTERN_TYPES:
        return "Suspicious Vendor Payment Pattern"
    if discrepancy_type in PAYMENT_ANOMALY_TYPES:
        return "Payment Anomaly"
    return None


def detect_invoice_discrepancies(
    db: Session, thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    discrepancies = (
        db.query(InvoiceDiscrepancy)
        .filter(InvoiceDiscrepancy.status == "open")
        .order_by(InvoiceDiscrepancy.id.asc())
        .all()
    )
    drafts: list[dict[str, Any]] = []
    for discrepancy in discrepancies:
        if discrepancy.discrepancy_type in DUPLICATE_INVOICE_TYPES:
            continue
        category = _discrepancy_category(discrepancy.discrepancy_type)
        if not category:
            continue
        invoice = discrepancy.invoice
        if not invoice:
            continue
        difference = abs(_amount(discrepancy.difference_minor)) / 100
        amount = difference or _invoice_amount(invoice)
        score = SEVERITY_BASE.get(discrepancy.severity, 50)
        if difference >= thresholds.approval_value:
            score += 8
        vendor_name = invoice.vendor.vendor_name if invoice.vendor else None
        explanation = discrepancy.explanation or (
            f"Invoice exception {discrepancy.discrepancy_type.replace('_', ' ')} requires review."
        )
        drafts.append(
            _draft(
                thresholds,
                category,
                score,
                explanation,
                source_type="discrepancy",
                fingerprint=_payload_fingerprint(
                    "invoice-discrepancy",
                    discrepancy.invoice_id,
                    discrepancy.discrepancy_type,
                    discrepancy.expected_value or "",
                    discrepancy.actual_value or "",
                ),
                source_record_id=discrepancy.id,
                invoice_id=invoice.id,
                vendor_id=invoice.vendor_id,
                vendor_name=vendor_name,
                amount_value=amount,
                currency=discrepancy.currency or invoice.currency,
                evidence={
                    "discrepancy_id": discrepancy.id,
                    "discrepancy_type": discrepancy.discrepancy_type,
                    "severity": discrepancy.severity,
                    "invoice_id": invoice.id,
                    "invoice_number": invoice.invoice_number,
                    "invoice_date": invoice.invoice_date,
                    "purchase_order_number": invoice.purchase_order_number,
                    "expected_value": discrepancy.expected_value,
                    "actual_value": discrepancy.actual_value,
                    "difference_minor": discrepancy.difference_minor,
                    "currency": discrepancy.currency or invoice.currency,
                    "source_evidence": discrepancy.evidence,
                },
            )
        )
    return drafts


def detect_repeated_vendor_discrepancies(
    db: Session, thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    rows = (
        db.query(InvoiceDiscrepancy, Invoice)
        .join(Invoice, Invoice.id == InvoiceDiscrepancy.invoice_id)
        .filter(InvoiceDiscrepancy.status == "open")
        .all()
    )
    grouped: dict[int, list[tuple[InvoiceDiscrepancy, Invoice]]] = defaultdict(list)
    for discrepancy, invoice in rows:
        if invoice.vendor_id:
            grouped[invoice.vendor_id].append((discrepancy, invoice))

    drafts: list[dict[str, Any]] = []
    for vendor_id, items in grouped.items():
        if len(items) < 3:
            continue
        vendor = db.get(Vendor, vendor_id)
        vendor_name = vendor.vendor_name if vendor else None
        types = Counter(item[0].discrepancy_type for item in items)
        exposure = sum(abs(_amount(item[0].difference_minor)) / 100 for item in items)
        score = 58 + min(22, len(items) * 3)
        drafts.append(
            _draft(
                thresholds,
                "Repeated Vendor Discrepancies",
                score,
                f"{len(items)} open invoice discrepancies are linked to {vendor_name or 'this vendor'}. Review whether the vendor's invoices, receipts and approvals are consistently incomplete or mismatched.",
                source_type="vendor",
                fingerprint=_payload_fingerprint("vendor-discrepancies", vendor_id),
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                amount_value=exposure,
                currency=items[0][1].currency,
                evidence={
                    "vendor_id": vendor_id,
                    "vendor_name": vendor_name,
                    "discrepancy_count": len(items),
                    "discrepancy_types": dict(types),
                    "invoice_ids": sorted({invoice.id for _, invoice in items}),
                    "discrepancy_ids": sorted({discrepancy.id for discrepancy, _ in items}),
                    "exposure_value": round(exposure, 2),
                },
            )
        )
    return drafts


def detect_reconciliation_anomalies(
    db: Session, thresholds: RiskThresholds
) -> list[dict[str, Any]]:
    latest = db.query(ReconciliationRun).order_by(ReconciliationRun.id.desc()).first()
    if not latest:
        return []
    results = (
        db.query(ReconciliationResult)
        .filter(ReconciliationResult.run_id == latest.id, ReconciliationResult.status != "matched")
        .order_by(ReconciliationResult.id.asc())
        .all()
    )
    score_map = {
        "amount_mismatch": 72,
        "bank_only": 60,
        "ledger_only": 60,
        "date_mismatch": 48,
        "ambiguous": 44,
    }
    drafts: list[dict[str, Any]] = []
    for result in results:
        value = max(
            abs(_amount(result.bank_amount)),
            abs(_amount(result.ledger_amount)),
            abs(_amount(result.amount_difference)),
        )
        score = score_map.get(result.status, 42)
        if value >= thresholds.approval_value:
            score += 6
        reference = _text(result.bank_reference or result.ledger_reference)
        drafts.append(
            _draft(
                thresholds,
                "Bank Reconciliation Anomaly",
                score,
                f"Latest reconciliation status is {result.status.replace('_', ' ')} for reference {reference or 'unknown'}. Verify the bank statement, ledger entry and supporting payment record.",
                source_type="reconciliation",
                fingerprint=_payload_fingerprint("reconciliation", latest.id, result.id, result.status),
                source_record_id=result.id,
                transaction_id=reference or None,
                amount_value=value,
                currency="INR",
                evidence={
                    "reconciliation_run_id": latest.id,
                    "result_id": result.id,
                    "status": result.status,
                    "bank_date": result.bank_date,
                    "bank_amount": result.bank_amount,
                    "bank_direction": result.bank_direction,
                    "bank_reference": result.bank_reference,
                    "bank_description": result.bank_description,
                    "ledger_date": result.ledger_date,
                    "ledger_amount": result.ledger_amount,
                    "ledger_direction": result.ledger_direction,
                    "ledger_reference": result.ledger_reference,
                    "ledger_description": result.ledger_description,
                    "amount_difference": result.amount_difference,
                    "date_difference_days": result.date_difference_days,
                    "note": result.note,
                },
            )
        )
    return drafts


def collect_alert_drafts(
    db: Session, thresholds: RiskThresholds
) -> tuple[list[dict[str, Any]], list[TransactionRecord]]:
    records = load_analysis_transactions(db)
    drafts: list[dict[str, Any]] = []
    drafts.extend(detect_duplicate_payments(records, thresholds))
    drafts.extend(detect_unusual_amounts(records, thresholds))
    drafts.extend(detect_missing_approval_transactions(records, thresholds))
    drafts.extend(detect_unusual_frequency(records, thresholds))
    drafts.extend(detect_suspicious_vendor_patterns(records, thresholds))
    drafts.extend(detect_duplicate_invoices(db, thresholds))
    drafts.extend(detect_invoice_discrepancies(db, thresholds))
    drafts.extend(detect_repeated_vendor_discrepancies(db, thresholds))
    drafts.extend(detect_reconciliation_anomalies(db, thresholds))

    unique: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        fingerprint = draft["fingerprint"]
        existing = unique.get(fingerprint)
        if existing is None or draft["risk_score"] > existing["risk_score"]:
            unique[fingerprint] = draft
    return list(unique.values()), records


def run_analysis(
    db: Session,
    thresholds: RiskThresholds | None = None,
    triggered_by: str = "manual",
) -> dict[str, Any]:
    threshold = thresholds or resolve_thresholds()
    started = datetime.now(timezone.utc)
    run = RiskAnalysisRun(
        status="running",
        triggered_by=triggered_by,
        thresholds=threshold.as_dict(),
        started_at=started,
    )
    db.add(run)
    db.flush()

    try:
        drafts, records = collect_alert_drafts(db, threshold)
        existing_alerts = {
            alert.fingerprint: alert for alert in db.query(RiskAlert).all()
        }
        created = 0
        updated = 0
        for index, draft in enumerate(drafts):
            existing = existing_alerts.get(draft["fingerprint"])
            if existing:
                existing.source_type = draft["source_type"]
                existing.source_record_id = draft["source_record_id"]
                existing.transaction_id = draft["transaction_id"]
                existing.invoice_id = draft["invoice_id"]
                existing.vendor_id = draft["vendor_id"]
                existing.vendor_name = draft["vendor_name"]
                existing.risk_category = draft["risk_category"]
                existing.severity = draft["severity"]
                existing.risk_score = draft["risk_score"]
                existing.amount_value = draft["amount_value"]
                existing.currency = draft["currency"]
                existing.channel = draft["channel"]
                existing.explanation = draft["explanation"]
                existing.evidence = draft["evidence"]
                existing.country = draft["country"]
                existing.state = draft["state"]
                existing.city = draft["city"]
                existing.latitude = draft["latitude"]
                existing.longitude = draft["longitude"]
                existing.last_analysis_run_id = run.id
                existing.updated_at = datetime.now(timezone.utc)
                updated += 1
            else:
                alert = RiskAlert(
                    alert_code=f"RA-{run.id:04d}-{index + 1:04d}",
                    fingerprint=draft["fingerprint"],
                    detected_at=datetime.now(timezone.utc),
                    review_status="open",
                    last_analysis_run_id=run.id,
                    **{key: value for key, value in draft.items() if key != "fingerprint"},
                )
                db.add(alert)
                existing_alerts[draft["fingerprint"]] = alert
                created += 1

        db.flush()
        severity_counts = Counter(draft["severity"] for draft in drafts)
        finished = datetime.now(timezone.utc)
        run.status = "completed"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.transaction_count = len(records)
        run.alert_count = len(drafts)
        run.critical_count = severity_counts.get("critical", 0)
        run.high_count = severity_counts.get("high", 0)
        run.medium_count = severity_counts.get("medium", 0)
        run.low_count = severity_counts.get("low", 0)
        db.commit()
        return {
            "analysis_run_id": run.id,
            "status": run.status,
            "transaction_count": run.transaction_count,
            "alert_count": run.alert_count,
            "created": created,
            "updated": updated,
            "critical_count": run.critical_count,
            "high_count": run.high_count,
            "medium_count": run.medium_count,
            "low_count": run.low_count,
            "thresholds": threshold.as_dict(),
            "duration_ms": run.duration_ms,
        }
    except Exception as exc:
        db.rollback()
        failed_at = datetime.now(timezone.utc)
        failed = RiskAnalysisRun(
            status="failed",
            triggered_by=triggered_by,
            thresholds=threshold.as_dict(),
            errors=[str(exc)],
            started_at=started,
            finished_at=failed_at,
            duration_ms=int((failed_at - started).total_seconds() * 1000),
        )
        db.add(failed)
        db.commit()
        raise


def _alert_transaction_ids(alert: RiskAlert) -> set[str]:
    evidence = alert.evidence if isinstance(alert.evidence, dict) else {}
    ids = {_text(alert.transaction_id)} if _text(alert.transaction_id) else set()
    for item in evidence.get("transaction_ids") or []:
        text = _text(item)
        if text:
            ids.add(text)
    return ids


def _alert_record_ids(alert: RiskAlert) -> set[int]:
    evidence = alert.evidence if isinstance(alert.evidence, dict) else {}
    ids = {alert.source_record_id} if alert.source_record_id else set()
    for item in evidence.get("record_ids") or []:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def overview(db: Session) -> dict[str, Any]:
    thresholds = resolve_thresholds()
    alerts = db.query(RiskAlert).all()
    records = load_analysis_transactions(db)
    severity_counts = Counter(alert.severity for alert in alerts)
    status_counts = Counter(alert.review_status for alert in alerts)
    category_counts = Counter(alert.risk_category for alert in alerts)
    suspicious_vendors = set()
    for alert in alerts:
        if alert.vendor_id is not None:
            suspicious_vendors.add(f"id:{alert.vendor_id}")
        elif _text(alert.vendor_name):
            suspicious_vendors.add(_text(alert.vendor_name))
    total_volume = sum(_abs_amount(record) for record in records)
    flagged_value = sum(abs(_amount(alert.amount_value)) for alert in alerts)
    bands = []
    for band in risk_score_bands(thresholds):
        count = sum(1 for alert in alerts if band["min"] <= alert.risk_score <= band["max"])
        bands.append({**band, "count": count})
    latest_run = db.query(RiskAnalysisRun).order_by(RiskAnalysisRun.id.desc()).first()
    recent_events = (
        db.query(RiskAlertEvent, RiskAlert)
        .join(RiskAlert, RiskAlert.id == RiskAlertEvent.alert_id)
        .order_by(RiskAlertEvent.id.desc())
        .limit(8)
        .all()
    )
    return {
        "total_alerts": len(alerts),
        "critical_alerts": severity_counts.get("critical", 0),
        "high_alerts": severity_counts.get("high", 0),
        "medium_alerts": severity_counts.get("medium", 0),
        "low_alerts": severity_counts.get("low", 0),
        "total_transaction_volume": round(total_volume, 2),
        "total_flagged_value": round(flagged_value, 2),
        "transactions_analyzed": len(records),
        "suspicious_vendors": len(suspicious_vendors),
        "unresolved_alerts": status_counts.get("open", 0) + status_counts.get("investigating", 0),
        "open_alerts": status_counts.get("open", 0),
        "investigating_alerts": status_counts.get("investigating", 0),
        "resolved_alerts": status_counts.get("resolved", 0),
        "average_risk_score": round(
            sum(alert.risk_score for alert in alerts) / len(alerts), 1
        ) if alerts else 0,
        "risk_score_distribution": bands,
        "category_breakdown": [
            {"category": name, "count": count}
            for name, count in sorted(category_counts.items(), key=lambda pair: pair[1], reverse=True)
        ],
        "thresholds": thresholds.as_dict(),
        "last_analysis": {
            "id": latest_run.id,
            "status": latest_run.status,
            "alert_count": latest_run.alert_count,
            "transaction_count": latest_run.transaction_count,
            "finished_at": _iso(latest_run.finished_at),
            "duration_ms": latest_run.duration_ms,
        } if latest_run else None,
        "recent_investigations": [
            {
                "id": event.id,
                "alert_id": alert.id,
                "alert_code": alert.alert_code,
                "action": event.action,
                "actor": event.actor,
                "comment": event.comment,
                "review_status": alert.review_status,
                "severity": alert.severity,
                "risk_category": alert.risk_category,
                "created_at": _iso(event.created_at),
            }
            for event, alert in recent_events
        ],
    }


def serialize_alert(alert: RiskAlert, include_events: bool = True) -> dict[str, Any]:
    payload = {
        "id": alert.id,
        "alert_id": alert.alert_code,
        "fingerprint": alert.fingerprint,
        "source_type": alert.source_type,
        "source_record_id": alert.source_record_id,
        "transaction_id": alert.transaction_id,
        "invoice_id": alert.invoice_id,
        "vendor_id": alert.vendor_id,
        "vendor_name": alert.vendor_name,
        "risk_category": alert.risk_category,
        "category": alert.risk_category,
        "severity": alert.severity,
        "risk_score": alert.risk_score,
        "amount": alert.amount_value,
        "amount_value": alert.amount_value,
        "currency": alert.currency,
        "channel": alert.channel or "Unknown",
        "explanation": alert.explanation,
        "evidence": alert.evidence or {},
        "country": alert.country,
        "state": alert.state,
        "city": alert.city,
        "latitude": alert.latitude,
        "longitude": alert.longitude,
        "detected_at": _iso(alert.detected_at),
        "review_status": alert.review_status,
        "assigned_to": alert.assigned_to,
        "reviewer_comments": alert.reviewer_comments,
        "resolved_at": _iso(alert.resolved_at),
        "last_analysis_run_id": alert.last_analysis_run_id,
        "created_at": _iso(alert.created_at),
        "updated_at": _iso(alert.updated_at),
    }
    if include_events:
        payload["events"] = [
            {
                "id": event.id,
                "action": event.action,
                "actor": event.actor,
                "comment": event.comment,
                "previous_status": event.previous_status,
                "new_status": event.new_status,
                "created_at": _iso(event.created_at),
            }
            for event in sorted(alert.events, key=lambda item: item.id or 0, reverse=True)
        ]
    return payload


def channel_analytics(db: Session) -> dict[str, Any]:
    records = load_analysis_transactions(db)
    alerts = db.query(RiskAlert).all()
    flagged_ids: set[str] = set()
    flagged_records: set[int] = set()
    for alert in alerts:
        flagged_ids.update(_alert_transaction_ids(alert))
        flagged_records.update(_alert_record_ids(alert))

    stats: dict[str, dict[str, Any]] = {}
    trend: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        channel = infer_channel(record)
        amount = _abs_amount(record)
        flagged = (
            (record.id in flagged_records)
            or (_text(record.transaction_id) in flagged_ids)
        )
        bucket = stats.setdefault(
            channel,
            {
                "channel": channel,
                "transaction_count": 0,
                "total_amount": 0.0,
                "flagged_count": 0,
                "flagged_amount": 0.0,
            },
        )
        bucket["transaction_count"] += 1
        bucket["total_amount"] += amount
        if flagged:
            bucket["flagged_count"] += 1
            bucket["flagged_amount"] += amount
        day = _text(record.date) or "unknown"
        day_bucket = trend[channel].setdefault(
            day,
            {"date": day, "transaction_count": 0, "flagged_count": 0, "amount": 0.0, "flagged_amount": 0.0},
        )
        day_bucket["transaction_count"] += 1
        day_bucket["amount"] += amount
        if flagged:
            day_bucket["flagged_count"] += 1
            day_bucket["flagged_amount"] += amount

    channels = []
    for channel, bucket in stats.items():
        count = bucket["transaction_count"]
        bucket["total_amount"] = round(bucket["total_amount"], 2)
        bucket["flagged_amount"] = round(bucket["flagged_amount"], 2)
        bucket["risk_percentage"] = round((bucket["flagged_count"] / count) * 100, 2) if count else 0
        channel_trend = []
        for day in sorted(trend[channel]):
            item = trend[channel][day]
            item["amount"] = round(item["amount"], 2)
            item["flagged_amount"] = round(item["flagged_amount"], 2)
            channel_trend.append(item)
        bucket["trend"] = channel_trend
        channels.append(bucket)
    channels.sort(key=lambda item: item["transaction_count"], reverse=True)
    return {
        "available": bool(records),
        "supported_channels": list(SUPPORTED_CHANNELS),
        "channels": channels,
        "unknown_channel_count": next(
            (item["transaction_count"] for item in channels if item["channel"] == "Unknown"), 0
        ),
        "transactions_analyzed": len(records),
    }


def _parse_filter_date(value: Any) -> date | None:
    return _as_date(value)


def trend_analytics(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    granularity: str = "daily",
) -> dict[str, Any]:
    if granularity not in {"daily", "weekly", "monthly"}:
        raise ValueError("granularity must be daily, weekly or monthly")
    start = _parse_filter_date(start_date)
    end = _parse_filter_date(end_date)
    if start and end and start > end:
        raise ValueError("start_date must be on or before end_date")

    alerts = db.query(RiskAlert).order_by(RiskAlert.detected_at.asc()).all()
    filtered: list[RiskAlert] = []
    for alert in alerts:
        detected = _as_date(alert.detected_at)
        if not detected:
            continue
        if start and detected < start:
            continue
        if end and detected > end:
            continue
        filtered.append(alert)

    def period_start(day: date) -> date:
        if granularity == "weekly":
            return day - timedelta(days=day.weekday())
        if granularity == "monthly":
            return day.replace(day=1)
        return day

    def label(day: date) -> str:
        if granularity == "weekly":
            return f"Week of {day.isoformat()}"
        if granularity == "monthly":
            return day.strftime("%Y-%m")
        return day.isoformat()

    buckets: dict[date, dict[str, Any]] = {}
    for alert in filtered:
        detected = _as_date(alert.detected_at)
        if not detected:
            continue
        key = period_start(detected)
        bucket = buckets.setdefault(
            key,
            {
                "period": key.isoformat(),
                "label": label(key),
                "total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "flagged_value": 0.0,
                "duplicate_payments": 0,
                "invoice_mismatches": 0,
                "vendor_risk": 0,
                "categories": Counter(),
            },
        )
        bucket["total"] += 1
        if alert.severity in {"critical", "high", "medium", "low"}:
            bucket[alert.severity] += 1
        bucket["flagged_value"] += abs(_amount(alert.amount_value))
        if alert.risk_category == "Duplicate Payment":
            bucket["duplicate_payments"] += 1
        if alert.risk_category in {"Duplicate Invoice", "Invoice / PO Mismatch", "Goods Receipt Mismatch"}:
            bucket["invoice_mismatches"] += 1
        if alert.risk_category in {"Suspicious Vendor Payment Pattern", "Repeated Vendor Discrepancies"}:
            bucket["vendor_risk"] += 1
        bucket["categories"][alert.risk_category] += 1

    series = []
    for key in sorted(buckets):
        bucket = buckets[key]
        series.append(
            {
                **{name: value for name, value in bucket.items() if name != "categories"},
                "flagged_value": round(bucket["flagged_value"], 2),
                "categories": dict(bucket["categories"]),
            }
        )
    category_names = sorted({alert.risk_category for alert in filtered})
    return {
        "granularity": granularity,
        "start_date": start.isoformat() if start else (series[0]["period"] if series else None),
        "end_date": end.isoformat() if end else (series[-1]["period"] if series else None),
        "total_alerts": len(filtered),
        "categories": category_names,
        "series": series,
    }


def _valid_coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def geography_analytics(
    db: Session,
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
) -> dict[str, Any]:
    records = load_analysis_transactions(db)
    alerts = db.query(RiskAlert).all()
    alert_by_transaction: dict[str, list[RiskAlert]] = defaultdict(list)
    alert_by_record: dict[int, list[RiskAlert]] = defaultdict(list)
    for alert in alerts:
        for transaction_id in _alert_transaction_ids(alert):
            alert_by_transaction[transaction_id].append(alert)
        for record_id in _alert_record_ids(alert):
            alert_by_record[record_id].append(alert)

    filters = {
        "countries": sorted({_text(record.country) for record in records if _text(record.country)}),
        "states": sorted({_text(record.state) for record in records if _text(record.state)}),
        "cities": sorted({_text(record.city) for record in records if _text(record.city)}),
    }
    named_records = [
        record for record in records if _text(record.country) or _text(record.state) or _text(record.city)
    ]

    matches = []
    for record in records:
        if country and _text(record.country).lower() != country.lower():
            continue
        if state and _text(record.state).lower() != state.lower():
            continue
        if city and _text(record.city).lower() != city.lower():
            continue
        coords = _valid_coordinates(record.latitude, record.longitude)
        if not coords:
            continue
        matches.append((record, coords))

    if not matches:
        return {
            "available": False,
            "reason": (
                "No transaction location coordinates are stored. The map does not fabricate "
                "coordinates or locations."
                if not named_records
                else "Location names are present, but coordinates are unavailable. Markers are not fabricated."
            ),
            "items": [],
            "filters": filters,
            "named_location_count": len(named_records),
            "coordinate_location_count": 0,
        }

    clusters: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record, (latitude, longitude) in matches:
        related_alerts: list[RiskAlert] = []
        related_alerts.extend(alert_by_record.get(record.id, []))
        if _text(record.transaction_id):
            related_alerts.extend(alert_by_transaction.get(_text(record.transaction_id), []))
        unique_alerts = {alert.id: alert for alert in related_alerts}
        key = (
            _text(record.country),
            _text(record.state),
            _text(record.city),
            round(latitude, 3),
            round(longitude, 3),
        )
        cluster = clusters.setdefault(
            key,
            {
                "country": key[0] or None,
                "state": key[1] or None,
                "city": key[2] or None,
                "latitude": key[3],
                "longitude": key[4],
                "transaction_count": 0,
                "total_amount": 0.0,
                "alert_count": 0,
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "transactions": [],
            },
        )
        cluster["transaction_count"] += 1
        cluster["total_amount"] += _abs_amount(record)
        for alert in unique_alerts.values():
            cluster["alert_count"] += 1
            if alert.severity in {"critical", "high", "medium", "low"}:
                cluster[f"{alert.severity}_count"] += 1
        if len(cluster["transactions"]) < 20:
            cluster["transactions"].append(
                {
                    "id": record.id,
                    "transaction_id": record.transaction_id,
                    "date": record.date,
                    "amount": record.amount,
                    "vendor": record.vendor,
                    "description": record.description,
                    "channel": infer_channel(record),
                    "alert_ids": sorted(alert.alert_code for alert in unique_alerts.values()),
                }
            )

    items = list(clusters.values())
    for item in items:
        item["total_amount"] = round(item["total_amount"], 2)
    items.sort(key=lambda item: (item["alert_count"], item["total_amount"]), reverse=True)
    return {
        "available": True,
        "reason": None,
        "items": items,
        "filters": filters,
        "named_location_count": len(named_records),
        "coordinate_location_count": len(matches),
    }


def build_investigation(db: Session, alert: RiskAlert) -> dict[str, Any]:
    evidence_items: list[dict[str, Any]] = []
    missing: list[str] = []
    facts: list[str] = []
    transaction = None
    invoice = None

    if alert.source_record_id and alert.source_type in {"transaction", "transaction_group"}:
        transaction = db.get(TransactionRecord, alert.source_record_id)
    if transaction is None and _text(alert.transaction_id):
        transaction = (
            db.query(TransactionRecord)
            .filter(TransactionRecord.transaction_id == alert.transaction_id)
            .order_by(TransactionRecord.id.desc())
            .first()
        )
    if alert.invoice_id:
        invoice = db.get(Invoice, alert.invoice_id)

    if transaction:
        facts.append(
            f"Stored transaction {transaction.transaction_id or transaction.id} dated {transaction.date or 'unknown date'} for {transaction.amount if transaction.amount is not None else 'an unknown amount'}."
        )
        evidence_items.append(
            {
                "type": "transaction",
                "id": transaction.id,
                "transaction_id": transaction.transaction_id,
                "date": transaction.date,
                "amount": transaction.amount,
                "vendor": transaction.vendor,
                "description": transaction.description,
                "reference": transaction.reference,
                "upi_ref": transaction.upi_ref,
                "source_file": transaction.source_file,
                "source_row": transaction.source_row,
            }
        )
        if not _text(transaction.reference) and not _text(transaction.upi_ref):
            missing.append("External bank/payment reference for the transaction")
    else:
        missing.append("Linked transaction record")

    if invoice:
        facts.append(
            f"Invoice {invoice.invoice_number or invoice.id} for {invoice.total_minor if invoice.total_minor is not None else 'an unknown total'} minor units, currency {invoice.currency}."
        )
        evidence_items.append(
            {
                "type": "invoice",
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "invoice_date": invoice.invoice_date,
                "total_minor": invoice.total_minor,
                "currency": invoice.currency,
                "status": invoice.status,
                "audit_status": invoice.audit_status,
                "vendor_id": invoice.vendor_id,
                "vendor_name": invoice.vendor.vendor_name if invoice.vendor else None,
            }
        )
        if invoice.purchase_order_id or invoice.purchase_order_number:
            po = invoice.purchase_order
            evidence_items.append(
                {
                    "type": "purchase_order",
                    "id": po.id if po else None,
                    "purchase_order_number": po.purchase_order_number if po else invoice.purchase_order_number,
                    "vendor_id": po.vendor_id if po else None,
                    "total_minor": po.total_minor if po else None,
                }
            )
            if po:
                receipts = list(po.goods_receipts)
                if receipts:
                    for receipt in receipts:
                        evidence_items.append(
                            {
                                "type": "goods_receipt",
                                "id": receipt.id,
                                "receipt_number": receipt.receipt_number,
                                "receipt_date": receipt.receipt_date,
                                "status": receipt.status,
                            }
                        )
                else:
                    missing.append("Goods receipt for the purchase order")
        else:
            missing.append("Approved purchase order")

        payments = list(invoice.payments)
        for payment in payments:
            evidence_items.append(
                {
                    "type": "payment",
                    "id": payment.id,
                    "payment_date": payment.payment_date,
                    "amount_minor": payment.amount_minor,
                    "currency": payment.currency,
                    "reference": payment.payment_reference,
                    "method": payment.payment_method,
                    "status": payment.status,
                }
            )
        if not payments:
            missing.append("Verified payment record")

        discrepancies = list(invoice.discrepancies)
        for discrepancy in discrepancies[:20]:
            evidence_items.append(
                {
                    "type": "invoice_discrepancy",
                    "id": discrepancy.id,
                    "discrepancy_type": discrepancy.discrepancy_type,
                    "severity": discrepancy.severity,
                    "status": discrepancy.status,
                    "explanation": discrepancy.explanation,
                }
            )
        if not invoice.documents:
            missing.append("Original invoice document")

    if isinstance(alert.evidence, dict):
        evidence_items.append({"type": "alert_evidence", "data": alert.evidence})

    hypotheses = {
        "Duplicate Payment": [
            "The transactions may be legitimate recurring, split or scheduled payments.",
            "A duplicate posting or duplicate approval may have occurred.",
        ],
        "Duplicate Invoice": [
            "The invoice may be a re-submission, credit note or correction.",
            "The same purchase may have been invoiced more than once.",
        ],
        "Unusual Transaction Amount": [
            "A one-off purchase, annual contract or bulk order may explain the amount.",
            "The amount may be outside the approved scope or threshold.",
        ],
        "Invoice / PO Mismatch": [
            "A change order or tax adjustment may not be reflected on the purchase order.",
            "The invoice may include items or prices that were not approved.",
        ],
        "Goods Receipt Mismatch": [
            "Delivery may be partial, pending or recorded under a different receipt.",
            "The invoice may bill goods that were not received or accepted.",
        ],
        "Suspicious Vendor Payment Pattern": [
            "The vendor may have a legitimate recurring billing arrangement.",
            "The pattern may indicate split payments intended to avoid approval thresholds.",
        ],
        "Repeated Vendor Discrepancies": [
            "The vendor's documents may be systematically incomplete or late.",
            "Repeated exceptions may indicate weak controls or deliberate evasion.",
        ],
        "Missing Approval Evidence": [
            "The approval may exist outside this dataset.",
            "The payment may have been made without documented authorisation.",
        ],
        "Unusual Transaction Frequency": [
            "A batch payroll or operational run may explain the volume.",
            "High frequency may indicate structured or automated unusual activity.",
        ],
        "Bank Reconciliation Anomaly": [
            "Timing differences, bank charges or reference mismatches may explain the exception.",
            "The ledger or bank entry may be incorrect or unsupported.",
        ],
    }.get(alert.risk_category, [
        "The exception may be explained by a legitimate business process not present in the dataset.",
        "The exception may indicate a control failure that requires review.",
    ])

    recommendations = {
        "Duplicate Payment": [
            "Compare the bank statement, payment approval and invoice references for each transaction.",
            "Confirm with the vendor whether more than one payment was received.",
        ],
        "Duplicate Invoice": [
            "Compare the original invoice documents and invoice numbers.",
            "Confirm with the vendor whether a credit note or correction was issued.",
        ],
        "Invoice / PO Mismatch": [
            "Compare each invoice line with the approved purchase order and change orders.",
            "Obtain approval for any quantity, price or tax difference before payment.",
        ],
        "Goods Receipt Mismatch": [
            "Obtain the goods receipt or service confirmation for the invoiced quantity.",
            "Verify rejected or short-delivered quantities and any credit notes.",
        ],
        "Bank Reconciliation Anomaly": [
            "Compare the bank statement line with the ledger entry and source document.",
            "Obtain the missing side of the reconciliation and document any timing difference.",
        ],
    }.get(alert.risk_category, [
        "Obtain the source documents and approval evidence for the transaction.",
        "Document the reviewer conclusion and retain the evidence with the alert.",
    ])

    facts.append(
        f"Deterministic risk score is {alert.risk_score} ({alert.severity}), detected { _iso(alert.detected_at) or 'at an unknown time'}."
    )
    if alert.amount_value is not None:
        facts.append(f"Stored flagged value is {alert.amount_value:.2f} {alert.currency or 'INR'}.")

    return {
        "alert": serialize_alert(alert),
        "verified_facts": facts,
        "evidence": evidence_items,
        "hypotheses": hypotheses,
        "missing_evidence": sorted(set(missing)),
        "recommended_review_steps": recommendations,
        "notice": "This alert is a potential anomaly, not a proven fraud accusation. Human review is required before any financial action.",
    }
