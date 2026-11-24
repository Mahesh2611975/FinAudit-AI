"""Deterministic, owner-scoped vendor intelligence workflow.

The agent reads only persisted database rows.  All amounts, counts and rule
scores come from the existing invoice-audit and risk-engine helpers; this module
does not invent financial records or calculate an overall vendor risk score.
RAG chunks are retrieved with the caller's trusted ``RagScope`` and returned
with their full citation metadata (owner metadata is deliberately removed).
"""
from __future__ import annotations

import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import rag
from ..database import SessionLocal
from ..invoice_audit import (
    detect_duplicate_invoice,
    financial_analysis,
    json_safe,
    match_goods_receipts,
    match_purchase_order,
    verify_payments,
    vendor_analytics,
    vendor_pattern_analysis,
)
from ..models import (
    Invoice,
    InvoiceDiscrepancy,
    InvoiceDocument,
    InvoicePayment,
    InvestigationRun,
    TransactionRecord,
    Vendor,
)
from ..ownership import (
    RAG_GLOBAL_READ_PERMISSION,
    RAG_READ,
    RagScope,
    reset_data_scope,
    set_data_scope,
    validate_rag_scope,
)
from ..risk_engine import (
    detect_duplicate_invoices,
    detect_duplicate_payments,
    detect_invoice_discrepancies,
    detect_repeated_vendor_discrepancies,
    detect_suspicious_vendor_patterns,
    resolve_thresholds,
)

AGENT_VENDOR = "Vendor Intelligence Agent"

BALANCE_DISCREPANCY_TYPES = {"unpaid_invoice", "partial_payment"}


class VendorIntelligenceError(ValueError):
    """Base class for recoverable vendor-intelligence input errors."""


class VendorNotFoundError(VendorIntelligenceError):
    """Raised when the vendor is missing or not visible to the caller."""


class VendorState(TypedDict, total=False):
    vendor_id: int
    rag_scope: RagScope
    vendor_profile: dict[str, Any]
    transaction_ids: list[int]
    status: str
    errors: list[str]
    timeline: list[dict[str, Any]]
    events: list[dict[str, Any]]
    outputs: dict[str, Any]
    evidence: list[dict[str, Any]]
    missing_evidence: list[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(started: datetime, finished: datetime) -> int:
    return max(0, int(round((finished - started).total_seconds() * 1000)))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _money(minor: Any, currency: str | None) -> str:
    try:
        amount = Decimal(int(minor or 0)) / Decimal(100)
    except (TypeError, ValueError):
        amount = Decimal(0)
    return f"{currency or 'INR'} {amount:.2f}"


def _data_scope_from_rag_scope(scope: RagScope) -> DataScope:
    """Translate a trusted RAG scope into a temporary ORM row scope.

    This prevents helper functions from silently using a background/global ORM
    view.  Only an explicit RAG global-read permission is treated as admin.
    """
    validated = validate_rag_scope(scope, operation=RAG_READ)
    is_admin = bool(
        validated.global_access
        or RAG_GLOBAL_READ_PERMISSION in validated.permissions
    )
    owner_id = validated.owner_user_id
    # ``set_data_scope`` accepts any user-like principal and derives role/admin
    # from it. A lightweight principal avoids mutating the ownership module and
    # keeps the temporary scope derived solely from the trusted RAG scope.
    return SimpleNamespace(
        id=int(owner_id) if isinstance(owner_id, int) and owner_id > 0 else -1,
        role="admin" if is_admin else "user",
    )


@contextmanager
def vendor_data_scope(scope: RagScope):
    """Run vendor queries under the caller's owner scope, then restore state."""
    token = set_data_scope(_data_scope_from_rag_scope(scope))
    try:
        yield
    finally:
        reset_data_scope(token)


def _transaction_evidence(record: TransactionRecord) -> dict[str, Any]:
    return {
        "kind": "transaction",
        "source_id": (
            f"transaction:{record.transaction_id}"
            if record.transaction_id
            else f"transaction-record:{record.id}"
        ),
        "source_type": "transaction_record",
        "record_id": str(record.id),
        "source_file": record.source_file,
        "source_row": record.source_row,
        "transaction_id": record.transaction_id,
        "excerpt": (
            f"{record.date or 'unknown date'} | {record.direction or 'unknown'} | "
            f"{record.amount} | {record.vendor or ''} {record.description or ''}"
        ).strip(),
    }


def _evidence_from_chunk(item: dict[str, Any]) -> dict[str, Any]:
    """Keep citation metadata but never expose the internal owner column."""
    try:
        score = round(float(item.get("score") or 0), 4)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "kind": "document_chunk",
        "source_id": item.get("source"),
        "source_type": "document_chunk",
        "doc_id": item.get("doc_id"),
        "chunk_id": item.get("chunk_id"),
        "source_filename": item.get("source_filename"),
        "document_type": item.get("document_type"),
        "page_number": item.get("page_number"),
        "transaction_id": item.get("transaction_id"),
        "excerpt": item.get("text"),
        "score": score,
    }


class VendorIntelligenceAgent:
    """LangGraph workflow: intake -> analysis -> evidence -> synthesis -> report."""

    def __init__(self) -> None:
        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    # Timeline helpers
    # ------------------------------------------------------------------
    def _record(
        self,
        state: VendorState,
        agent_name: str,
        status: str,
        started: datetime,
        finished: datetime,
        *,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        duration = _duration_ms(started, finished)
        entry: dict[str, Any] = {
            "agent_name": agent_name,
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": duration,
        }
        if summary:
            entry["summary"] = summary
        if error:
            entry["error"] = error
        timeline = [
            item for item in (state.get("timeline") or [])
            if item.get("agent_name") != agent_name
        ]
        timeline.append(entry)
        state["timeline"] = timeline

        events = list(state.get("events") or [])
        events.append(
            {
                "agent_name": agent_name,
                "event": "started",
                "timestamp": started.isoformat(),
            }
        )
        events.append(
            {
                "agent_name": agent_name,
                "event": status,
                "timestamp": finished.isoformat(),
                "duration_ms": duration,
                **({"error": error} if error else {}),
            }
        )
        state["events"] = events

    def _execute(
        self,
        state: VendorState,
        agent_name: str,
        handler: Callable[[VendorState], dict[str, Any]],
    ) -> VendorState:
        if state.get("status") == "failed":
            timestamp = _now()
            self._record(
                state, agent_name, "skipped", timestamp, timestamp,
                summary="Skipped because an upstream node failed.",
            )
            return state

        started = _now()
        try:
            result = handler(state) or {}
        except Exception as exc:  # noqa: BLE001 - failures must stay visible
            finished = _now()
            message = f"{type(exc).__name__}: {exc}"
            self._record(state, agent_name, "failed", started, finished, error=message)
            state["errors"] = list(state.get("errors") or []) + [
                f"{agent_name}: {message}"
            ]
            state["status"] = "failed" if agent_name == "Vendor Intake Agent" else "partial"
            return state

        finished = _now()
        self._record(
            state,
            agent_name,
            "completed",
            started,
            finished,
            summary=result.get("summary"),
        )
        outputs = dict(state.get("outputs") or {})
        outputs[result.get("key", agent_name)] = result.get("output")
        state["outputs"] = outputs
        if "missing_evidence" in result:
            merged = list(state.get("missing_evidence") or [])
            for item in result["missing_evidence"]:
                if item not in merged:
                    merged.append(item)
            state["missing_evidence"] = merged
        for field in ("vendor_profile", "transaction_ids", "evidence"):
            if field in result:
                state[field] = result[field]  # type: ignore[literal-required]
        return state

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _intake(self, state: VendorState) -> VendorState:
        def handler(current: VendorState) -> dict[str, Any]:
            vendor_id = int(current.get("vendor_id") or 0)
            if vendor_id < 1:
                raise VendorIntelligenceError("vendor_id must be a positive integer.")

            db: Session = SessionLocal()
            try:
                vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
                if vendor is None:
                    raise VendorNotFoundError(
                        f"Vendor {vendor_id} was not found or is not accessible."
                    )
                analytics = vendor_analytics(db, vendor_id)
                profile = dict(analytics[0]) if analytics else {
                    "vendor_id": vendor.id,
                    "vendor_name": vendor.vendor_name,
                    "currency": vendor.currency,
                    "status": vendor.status,
                }
                profile.update(
                    {
                        "vendor_code": vendor.vendor_code,
                        "tax_registration_number": vendor.tax_registration_number,
                        "email": vendor.email,
                        "phone": vendor.phone,
                        "address": vendor.address,
                        "payment_terms": vendor.payment_terms,
                    }
                )

                name = (vendor.vendor_name or "").strip().lower()
                code = (vendor.vendor_code or "").strip().lower()
                filters = []
                if name:
                    filters.append(func.lower(func.trim(TransactionRecord.vendor)) == name)
                    filters.append(func.lower(TransactionRecord.vendor).like(f"%{name}%"))
                if code:
                    filters.append(func.lower(func.trim(TransactionRecord.vendor)) == code)
                records = (
                    db.query(TransactionRecord)
                    .filter(or_(*filters))
                    .order_by(TransactionRecord.date.desc(), TransactionRecord.id.desc())
                    .all()
                    if filters
                    else []
                )
                return {
                    "key": "intake",
                    "summary": (
                        f"Loaded vendor {vendor.vendor_name} with "
                        f"{profile.get('invoice_count', 0)} invoice(s) and "
                        f"{len(records)} transaction(s)."
                    ),
                    "output": {
                        "vendor_id": vendor_id,
                        "vendor_name": vendor.vendor_name,
                        "source": "persisted_vendor_record",
                    },
                    "vendor_profile": json_safe(profile),
                    "transaction_ids": [record.id for record in records],
                }
            finally:
                db.close()

        return self._execute(state, "Vendor Intake Agent", handler)

    def _analysis(self, state: VendorState) -> VendorState:
        def handler(current: VendorState) -> dict[str, Any]:
            vendor_id = int(current.get("vendor_id") or 0)
            profile = current.get("vendor_profile") or {}
            currency = profile.get("currency") or "INR"
            thresholds = resolve_thresholds()
            db: Session = SessionLocal()
            try:
                invoices = (
                    db.query(Invoice)
                    .filter(Invoice.vendor_id == vendor_id)
                    .order_by(Invoice.invoice_date.asc(), Invoice.id.asc())
                    .all()
                )
                transaction_ids = current.get("transaction_ids") or []
                transactions = (
                    db.query(TransactionRecord)
                    .filter(TransactionRecord.id.in_(transaction_ids))
                    .order_by(TransactionRecord.date.desc(), TransactionRecord.id.desc())
                    .all()
                    if transaction_ids
                    else []
                )
                payments = (
                    db.query(InvoicePayment)
                    .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
                    .filter(Invoice.vendor_id == vendor_id)
                    .order_by(InvoicePayment.id.asc())
                    .all()
                )
                document_count = (
                    db.query(InvoiceDocument)
                    .join(Invoice, Invoice.id == InvoiceDocument.invoice_id)
                    .filter(Invoice.vendor_id == vendor_id)
                    .count()
                )

                per_invoice: list[dict[str, Any]] = []
                payment_discrepancies: list[dict[str, Any]] = []
                po_discrepancies: list[dict[str, Any]] = []
                gr_discrepancies: list[dict[str, Any]] = []
                duplicate_matches: list[dict[str, Any]] = []
                vendor_patterns: list[dict[str, Any]] = []
                financial_rows: list[dict[str, Any]] = []
                derived_discrepancies: list[dict[str, Any]] = []

                for invoice in invoices:
                    duplicate = detect_duplicate_invoice(db, invoice)
                    po = match_purchase_order(db, invoice)
                    gr = match_goods_receipts(db, invoice, po)
                    payment = verify_payments(db, invoice)
                    pattern = vendor_pattern_analysis(db, invoice)

                    combined = []
                    for source in (duplicate, po, gr, payment, pattern):
                        combined.extend(source.get("discrepancies") or [])

                    financial = financial_analysis(db, invoice, payment, combined)
                    financial_rows.append(financial)
                    derived_discrepancies.extend(
                        {"invoice_id": invoice.id, **item} for item in combined
                    )
                    payment_discrepancies.extend(
                        {"invoice_id": invoice.id, **item}
                        for item in (payment.get("discrepancies") or [])
                    )
                    po_discrepancies.extend(
                        {"invoice_id": invoice.id, **item}
                        for item in (po.get("discrepancies") or [])
                    )
                    gr_discrepancies.extend(
                        {"invoice_id": invoice.id, **item}
                        for item in (gr.get("discrepancies") or [])
                    )
                    if duplicate.get("matches"):
                        duplicate_matches.append(
                            {
                                "invoice_id": invoice.id,
                                "invoice_number": invoice.invoice_number,
                                "status": duplicate.get("status"),
                                "matches": duplicate["matches"],
                            }
                        )
                    if pattern.get("status") == "review":
                        vendor_patterns.append(
                            {
                                "invoice_id": invoice.id,
                                "prior_invoice_count": pattern.get("prior_invoice_count"),
                                "average_total_minor": pattern.get("average_total_minor"),
                                "discrepancies": pattern.get("discrepancies") or [],
                            }
                        )
                    per_invoice.append(
                        {
                            "invoice_id": invoice.id,
                            "invoice_number": invoice.invoice_number,
                            "invoice_date": invoice.invoice_date,
                            "currency": invoice.currency,
                            "total_minor": invoice.total_minor,
                            "paid_minor": invoice.paid_minor,
                            "outstanding_minor": invoice.outstanding_minor,
                            "duplicate_status": duplicate.get("status"),
                            "po_status": po.get("status"),
                            "goods_receipt_status": gr.get("status"),
                            "payment_status": payment.get("status"),
                            "vendor_pattern_status": pattern.get("status"),
                            "source_filename": invoice.source_filename,
                            "derived_discrepancy_count": len(combined),
                        }
                    )

                open_discrepancies = (
                    db.query(InvoiceDiscrepancy)
                    .join(Invoice, Invoice.id == InvoiceDiscrepancy.invoice_id)
                    .filter(
                        Invoice.vendor_id == vendor_id,
                        InvoiceDiscrepancy.status == "open",
                    )
                    .order_by(InvoiceDiscrepancy.id.asc())
                    .all()
                )
                discrepancy_rows = [
                    {
                        "id": item.id,
                        "invoice_id": item.invoice_id,
                        "invoice_number": item.invoice.invoice_number if item.invoice else None,
                        "discrepancy_type": item.discrepancy_type,
                        "severity": item.severity,
                        "expected_value": item.expected_value,
                        "actual_value": item.actual_value,
                        "difference_minor": item.difference_minor,
                        "currency": item.currency,
                        "explanation": item.explanation,
                        "status": item.status,
                        "source": "persisted_invoice_discrepancy",
                    }
                    for item in open_discrepancies
                ]

                exposure_rows = [
                    item for item in open_discrepancies
                    if item.discrepancy_type not in BALANCE_DISCREPANCY_TYPES
                ]
                discrepancy_exposure = sum(
                    abs(item.difference_minor or 0) for item in exposure_rows
                )
                high_exposure = sum(
                    abs(item.difference_minor or 0)
                    for item in exposure_rows
                    if item.severity == "high"
                )
                overpayment = sum(
                    row.get("overpayment_minor") or 0 for row in financial_rows
                )

                payment_patterns = detect_suspicious_vendor_patterns(
                    transactions, thresholds
                )
                duplicate_payments = detect_duplicate_payments(
                    transactions, thresholds
                )
                repeated_discrepancies = [
                    draft
                    for draft in detect_repeated_vendor_discrepancies(db, thresholds)
                    if draft.get("vendor_id") == vendor_id
                ]
                duplicate_rule_drafts = [
                    draft
                    for draft in detect_duplicate_invoices(db, thresholds)
                    if draft.get("vendor_id") == vendor_id
                ]
                discrepancy_rule_drafts = [
                    draft
                    for draft in detect_invoice_discrepancies(db, thresholds)
                    if draft.get("vendor_id") == vendor_id
                ]

                indicators = []
                for source_name, drafts in (
                    ("suspicious_vendor_payment_pattern", payment_patterns),
                    ("duplicate_payment", duplicate_payments),
                    ("repeated_vendor_discrepancies", repeated_discrepancies),
                    ("duplicate_invoice", duplicate_rule_drafts),
                    ("invoice_discrepancy", discrepancy_rule_drafts),
                ):
                    for draft in drafts:
                        indicators.append(
                            {
                                "rule": draft.get("risk_category"),
                                "source_name": source_name,
                                "source_type": draft.get("source_type"),
                                "severity": draft.get("severity"),
                                "rule_score": draft.get("risk_score"),
                                "explanation": draft.get("explanation"),
                                "evidence": json_safe(draft.get("evidence") or {}),
                                "transaction_id": draft.get("transaction_id"),
                                "invoice_id": draft.get("invoice_id"),
                            }
                        )

                transaction_totals = {
                    "count": len(transactions),
                    "debit_count": sum(
                        1 for item in transactions
                        if str(item.direction or "").lower() in {"debit", "payment", "withdrawal"}
                    ),
                    "debit_amount": round(
                        sum(
                            abs(float(item.amount or 0))
                            for item in transactions
                            if str(item.direction or "").lower() in {"debit", "payment", "withdrawal"}
                        ),
                        2,
                    ),
                    "credit_amount": round(
                        sum(
                            abs(float(item.amount or 0))
                            for item in transactions
                            if str(item.direction or "").lower() in {"credit", "deposit", "refund"}
                        ),
                        2,
                    ),
                    "currencies": sorted({item.currency for item in transactions if item.currency}),
                    "source_files": sorted({item.source_file for item in transactions if item.source_file}),
                }
                payment_summary = {
                    "payment_record_count": len(payments),
                    "verified_payment_count": sum(
                        1 for item in payments if item.status == "verified"
                    ),
                    "unverified_payment_count": sum(
                        1 for item in payments if item.status != "verified"
                    ),
                    "payment_methods": dict(
                        Counter(item.payment_method for item in payments if item.payment_method)
                    ),
                    "status_counts": dict(
                        Counter(item.status for item in payments if item.status)
                    ),
                    "overpayment_minor": overpayment,
                }

                financial_exposure = {
                    "currency": currency,
                    "invoice_count": profile.get("invoice_count", len(invoices)),
                    "total_invoiced_minor": profile.get("total_invoiced_minor", 0),
                    "verified_paid_minor": profile.get("paid_minor", 0),
                    "outstanding_minor": profile.get("outstanding_minor", 0),
                    "average_invoice_minor": profile.get("average_invoice_minor"),
                    "open_discrepancy_count": len(open_discrepancies),
                    "open_discrepancy_exposure_minor": discrepancy_exposure,
                    "high_severity_exposure_minor": high_exposure,
                    "duplicate_discrepancy_count": profile.get("duplicate_count", 0),
                    "overpayment_minor": overpayment,
                    "supporting_document_count": document_count,
                }

                missing = []
                if not invoices:
                    missing.append("No invoice records are linked to this vendor.")
                if not transactions:
                    missing.append(
                        "No transaction records matched this vendor's name or code."
                    )
                if not open_discrepancies:
                    missing.append(
                        "No open invoice discrepancy was recorded for this vendor."
                    )
                if not any(item.get("po_status") not in (None, "no_purchase_order") for item in per_invoice):
                    missing.append("No purchase-order match is available for this vendor's invoices.")
                if not document_count:
                    missing.append("No supporting invoice document is linked to this vendor.")

                facts = [
                    f"Vendor profile {profile.get('vendor_name')} ({profile.get('vendor_code') or 'no code'}) was loaded from persisted records.",
                    f"{len(invoices)} invoice(s) total {_money(profile.get('total_invoiced_minor', 0), currency)}; verified paid {_money(profile.get('paid_minor', 0), currency)}; outstanding {_money(profile.get('outstanding_minor', 0), currency)}.",
                    f"{len(transactions)} transaction record(s) matched this vendor; {transaction_totals['debit_count']} are debit-like payments.",
                    f"{len(open_discrepancies)} open discrepancy record(s) were found; calculated exposure is {_money(discrepancy_exposure, currency)}.",
                    f"{len(payments)} invoice payment record(s) were found, including {payment_summary['unverified_payment_count']} unverified row(s).",
                ]
                interpretations = [
                    "Rule indicators below are deterministic review leads derived from persisted records; they are not a fraud finding."
                ]
                if indicators:
                    interpretations.append(
                        f"{len(indicators)} rule indicator(s) were triggered. Each explanation and rule score originates from the existing risk engine."
                    )
                else:
                    interpretations.append(
                        "No deterministic rule indicator was triggered; this does not prove that risk is absent."
                    )
                interpretations.append(
                    "No overall vendor risk score was calculated; reviewers must inspect the source evidence."
                )

                output = {
                    "vendor_profile": json_safe(profile),
                    "financial_exposure": json_safe(financial_exposure),
                    "invoice_discrepancies": json_safe(discrepancy_rows),
                    "derived_discrepancies": json_safe(derived_discrepancies),
                    "payment_patterns": json_safe(
                        {
                            "transaction_summary": transaction_totals,
                            "rule_indicators": json_safe(payment_patterns + duplicate_payments),
                            "invoice_payment_summary": payment_summary,
                        }
                    ),
                    "duplicate_invoice_patterns": json_safe(
                        {
                            "rule_indicators": duplicate_rule_drafts,
                            "per_invoice_matches": duplicate_matches,
                        }
                    ),
                    "invoice_po_mismatches": json_safe(
                        {
                            "po_discrepancies": po_discrepancies,
                            "goods_receipt_discrepancies": gr_discrepancies,
                            "unusual_vendor_patterns": vendor_patterns,
                        }
                    ),
                    "transaction_history": json_safe(
                        {
                            "summary": transaction_totals,
                            "records": [_transaction_evidence(item) for item in transactions],
                        }
                    ),
                    "invoice_history": json_safe(per_invoice),
                    "risk_indicators": json_safe(indicators),
                    "verified_database_facts": facts,
                    "calculated_metrics": json_safe(
                        {
                            "financial_exposure": financial_exposure,
                            "transaction_summary": transaction_totals,
                            "payment_summary": payment_summary,
                            "indicator_counts_by_severity": dict(
                                Counter(item["severity"] for item in indicators if item.get("severity"))
                            ),
                        }
                    ),
                    "ai_interpretations": interpretations,
                    "missing_evidence": missing,
                    "recommended_review_actions": [
                        "Verify each cited document or source row before approving any payment.",
                        "Confirm duplicate invoices against original purchase orders and delivery evidence.",
                        "Record a human reviewer decision for every open discrepancy.",
                    ],
                    "per_invoice_financial_analysis": json_safe(financial_rows),
                }
                return {
                    "key": "analysis",
                    "summary": (
                        f"Analyzed {len(invoices)} invoice(s), {len(transactions)} transaction(s) "
                        f"and {len(indicators)} rule indicator(s)."
                    ),
                    "output": output,
                    "missing_evidence": missing,
                }
            finally:
                db.close()

        return self._execute(state, "Deterministic Vendor Analysis Agent", handler)

    def _evidence(self, state: VendorState) -> VendorState:
        def handler(current: VendorState) -> dict[str, Any]:
            profile = current.get("vendor_profile") or {}
            outputs = current.get("outputs") or {}
            analysis = outputs.get("analysis") or {}
            history = analysis.get("invoice_history") or []
            transactions = (analysis.get("transaction_history") or {}).get("records") or []

            name = str(profile.get("vendor_name") or "").strip()
            parts = [name]
            if profile.get("vendor_code"):
                parts.append(str(profile["vendor_code"]))
            for item in history[:6]:
                if item.get("invoice_number"):
                    parts.append(str(item["invoice_number"]))
            for item in transactions[:6]:
                if item.get("transaction_id"):
                    parts.append(str(item["transaction_id"]))

            candidates: list[str] = []
            for candidate in parts:
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
            # The scoped vector store requires every query term to exist in the
            # corpus, so fall back from the broad query to vendor-name tokens.
            for token in re.findall(r"[A-Za-z0-9]{3,}", name):
                if token not in candidates:
                    candidates.append(token)
            query = " ".join(parts).strip()
            if not candidates:
                return {
                    "key": "evidence",
                    "summary": "No vendor identifier was available for RAG retrieval.",
                    "output": {"query": None, "chunks": [], "source_count": 0},
                    "evidence": [],
                    "missing_evidence": ["No vendor identifier was available for RAG retrieval."],
                }

            scope = validate_rag_scope(current.get("rag_scope"), operation=RAG_READ)
            chunks: list[dict[str, Any]] = []
            used_query = candidates[0]
            for candidate in candidates:
                used_query = candidate
                chunks = rag.search(candidate, scope=scope, top_k=8)
                if chunks:
                    break
            query = used_query
            evidence = [_evidence_from_chunk(item) for item in chunks]
            missing = []
            if not chunks:
                missing.append(
                    "No indexed document chunk matched this vendor in the caller's RAG scope."
                )
            return {
                "key": "evidence",
                "summary": f"Retrieved {len(chunks)} scoped document chunk(s).",
                "output": {"query": query, "chunks": evidence, "source_count": len(chunks)},
                "evidence": evidence,
                "missing_evidence": missing,
            }

        return self._execute(state, "Scoped Evidence Retrieval Agent", handler)

    def _synthesis(self, state: VendorState) -> VendorState:
        def handler(current: VendorState) -> dict[str, Any]:
            outputs = current.get("outputs") or {}
            analysis = outputs.get("analysis") or {}
            retrieval = outputs.get("evidence") or {}
            evidence = current.get("evidence") or []
            missing = list(current.get("missing_evidence") or [])
            errors = list(current.get("errors") or [])
            status = current.get("status") or "completed"

            data_evidence = [
                item for item in evidence if item.get("kind") == "document_chunk"
            ]
            has_records = bool(
                analysis.get("invoice_history")
                or (analysis.get("transaction_history") or {}).get("records")
            )
            if not has_records and not data_evidence:
                conclusion_status = "insufficient_evidence"
            elif analysis.get("risk_indicators") or analysis.get("invoice_discrepancies"):
                conclusion_status = "requires_human_review"
            else:
                conclusion_status = "informational"

            references = []
            for item in analysis.get("invoice_history") or []:
                if item.get("invoice_id"):
                    references.append(
                        {
                            "kind": "invoice",
                            "source_id": f"invoice:{item['invoice_id']}",
                            "source_type": "invoice",
                            "record_id": str(item["invoice_id"]),
                            "source_file": item.get("source_filename"),
                            "transaction_id": None,
                        }
                    )
            for item in (analysis.get("transaction_history") or {}).get("records") or []:
                references.append(
                    {
                        "kind": item.get("kind"),
                        "source_id": item.get("source_id"),
                        "source_type": item.get("source_type"),
                        "record_id": item.get("record_id"),
                        "source_file": item.get("source_file"),
                        "source_row": item.get("source_row"),
                        "transaction_id": item.get("transaction_id"),
                        "excerpt": item.get("excerpt"),
                    }
                )
            for item in analysis.get("invoice_discrepancies") or []:
                references.append(
                    {
                        "kind": "invoice_discrepancy",
                        "source_id": f"invoice-discrepancy:{item.get('id')}",
                        "source_type": "invoice_discrepancy",
                        "record_id": str(item.get("id")),
                        "invoice_id": item.get("invoice_id"),
                        "excerpt": item.get("explanation"),
                    }
                )
            references.extend(data_evidence)

            report = {
                "agent_name": AGENT_VENDOR,
                "task_type": "vendor_intelligence",
                "status": status,
                "vendor_id": current.get("vendor_id"),
                "vendor_profile": analysis.get("vendor_profile")
                or current.get("vendor_profile")
                or {},
                "financial_exposure": analysis.get("financial_exposure") or {},
                "risk_indicators": analysis.get("risk_indicators") or [],
                "invoice_discrepancies": analysis.get("invoice_discrepancies") or [],
                "derived_discrepancies": analysis.get("derived_discrepancies") or [],
                "payment_patterns": analysis.get("payment_patterns") or {},
                "duplicate_invoice_patterns": analysis.get("duplicate_invoice_patterns") or {},
                "invoice_po_mismatches": analysis.get("invoice_po_mismatches") or {},
                "transaction_history": analysis.get("transaction_history") or {},
                "invoice_history": analysis.get("invoice_history") or [],
                "per_invoice_financial_analysis": analysis.get("per_invoice_financial_analysis") or [],
                "verified_database_facts": analysis.get("verified_database_facts") or [],
                "calculated_metrics": analysis.get("calculated_metrics") or {},
                "retrieved_evidence": data_evidence,
                "ai_interpretations": analysis.get("ai_interpretations") or [],
                "missing_evidence": missing,
                "recommended_review_actions": analysis.get("recommended_review_actions") or [],
                "evidence_references": references,
                "evidence": references,
                "rag_query": retrieval.get("query"),
                "llm": {"used": False, "backend": "deterministic_rules"},
                "conclusion_status": conclusion_status,
                "require_human_review": True,
                "errors": errors,
                "summary": (
                    f"Vendor synthesis complete: {conclusion_status}, "
                    f"{len(references)} evidence reference(s)."
                ),
                "disclaimer": (
                    "Automated indicators for human review. No overall vendor risk score "
                    "was assigned and no financial action is authorised by this output."
                ),
            }
            return {
                "key": "synthesis",
                "summary": report["summary"],
                "output": report,
                "evidence": references,
                "missing_evidence": missing,
                "require_human_review": True,
            }

        return self._execute(state, "Investigation Synthesis Agent", handler)

    def _report(self, state: VendorState) -> VendorState:
        def handler(current: VendorState) -> dict[str, Any]:
            outputs = current.get("outputs") or {}
            report = dict(outputs.get("synthesis") or {})
            report["outputs"] = {
                "intake": outputs.get("intake") or {},
                "analysis": outputs.get("analysis") or {},
                "evidence": outputs.get("evidence") or {},
                "synthesis": outputs.get("synthesis") or {},
            }
            return {
                "key": "report",
                "summary": "Vendor intelligence report assembled from scoped deterministic evidence.",
                "output": report,
            }

        return self._execute(state, "Vendor Reporting Agent", handler)

    # ------------------------------------------------------------------
    # Graph / execution
    # ------------------------------------------------------------------
    def _route_after_intake(self, state: VendorState):
        if state.get("status") == "failed":
            return END
        return "analysis"

    def _build_graph(self):
        graph = StateGraph(VendorState)
        graph.add_node("intake", self._intake)
        graph.add_node("analysis", self._analysis)
        graph.add_node("evidence", self._evidence)
        graph.add_node("synthesis", self._synthesis)
        graph.add_node("report", self._report)
        graph.add_edge(START, "intake")
        graph.add_conditional_edges(
            "intake",
            self._route_after_intake,
            {"analysis": "analysis", END: END},
        )
        graph.add_edge("analysis", "evidence")
        graph.add_edge("evidence", "synthesis")
        graph.add_edge("synthesis", "report")
        graph.add_edge("report", END)
        return graph.compile()

    def run(
        self,
        vendor_id: int,
        *,
        scope: RagScope,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Execute one owner-scoped vendor investigation."""
        validated_scope = validate_rag_scope(scope, operation=RAG_READ)
        if isinstance(vendor_id, bool) or not isinstance(vendor_id, int) or vendor_id < 1:
            raise VendorIntelligenceError("vendor_id must be a positive integer.")

        started = _now()
        started_perf = time.perf_counter()
        state: VendorState = {
            "vendor_id": vendor_id,
            "rag_scope": validated_scope,
            "status": "running",
            "errors": [],
            "timeline": [],
            "events": [],
            "outputs": {},
            "evidence": [],
            "missing_evidence": [],
        }
        with vendor_data_scope(validated_scope):
            try:
                result = dict(self.graph.invoke(state))
            except Exception as exc:  # noqa: BLE001 - unexpected graph failures
                result = dict(state)
                result["status"] = "failed"
                result["errors"] = list(result.get("errors") or []) + [
                    f"Workflow failure: {type(exc).__name__}: {exc}"
                ]

        errors = list(result.get("errors") or [])
        denied = next(
            (item for item in errors if "not found or is not accessible" in item),
            None,
        )
        if denied:
            raise VendorNotFoundError(denied)

        # The graph state carries intermediate outputs; callers receive the
        # assembled report with the measured run metadata at the top level.
        report = dict((result.get("outputs") or {}).get("report") or {})
        if report:
            for key in (
                "status",
                "errors",
                "timeline",
                "events",
                "evidence",
                "missing_evidence",
                "outputs",
            ):
                if key in result:
                    report[key] = result[key]
            result = report

        result.pop("rag_scope", None)
        finished = _now()
        result["started_at"] = started.isoformat()
        result["finished_at"] = finished.isoformat()
        result["duration_ms"] = max(
            0, int(round((time.perf_counter() - started_perf) * 1000))
        )
        result.setdefault("timeline", [])
        result.setdefault("events", [])
        result.setdefault("errors", [])
        result.setdefault("evidence", [])
        result.setdefault("missing_evidence", [])
        result.setdefault("status", "completed")
        result.setdefault("agent_name", AGENT_VENDOR)
        result.setdefault("task_type", "vendor_intelligence")
        result.setdefault("vendor_id", vendor_id)
        if persist:
            # Persist outside the derived data scope so INSERT stamping uses the
            # trusted request principal when one is active.
            self._persist(validated_scope, result)
        return json_safe(result)

    def _persist(self, scope: RagScope, result: dict[str, Any]) -> None:
        db: Session = SessionLocal()
        try:
            profile = (
                (result.get("outputs") or {})
                .get("synthesis", {})
                .get("vendor_profile", {})
            )
            owner_id = scope.owner_user_id if scope.owner_user_id and scope.owner_user_id > 0 else None
            run = InvestigationRun(
                name=f"Vendor intelligence: {profile.get('vendor_name') or result.get('vendor_id')}",
                task_type="vendor_intelligence",
                status=result.get("status"),
                input_state={"vendor_id": result.get("vendor_id")},
                output_state=result,
                timeline=result.get("timeline"),
                events=result.get("events"),
                evidence=result.get("evidence"),
                errors=result.get("errors"),
                owner_user_id=owner_id,
                started_at=_parse_iso(result.get("started_at")),
                finished_at=_parse_iso(result.get("finished_at")),
                duration_ms=result.get("duration_ms"),
            )
            db.add(run)
            db.flush()
            result["run_id"] = run.id
            run.output_state = result
            db.commit()
            db.refresh(run)
        except Exception as exc:  # noqa: BLE001 - persistence never hides results
            db.rollback()
            result["persistence_error"] = f"{type(exc).__name__}: {exc}"
            result.setdefault("errors", []).append(
                f"Persistence failed: {type(exc).__name__}: {exc}"
            )
        finally:
            db.close()
