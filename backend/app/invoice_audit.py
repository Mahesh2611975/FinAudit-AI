"""Deterministic accounts-payable invoice audit rules.

No LLM is used in this module. Amounts are compared in integer minor units and
quantities as Decimal values. Findings are exception indicators that require an
authorised human reviewer before any payment or accounting action.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .invoice_extraction import to_minor
from .models import (
    Invoice,
    InvoiceAuditRun,
    InvoiceDiscrepancy,
    InvoiceDocument,
    InvoiceMatch,
    InvoicePayment,
    PurchaseOrder,
    TransactionRecord,
    Vendor,
)


class InvoiceAuditError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_number(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _money(minor: int | None) -> str:
    if minor is None:
        return ""
    return f"{Decimal(minor) / Decimal(100):.2f}"


def _date(value: Any) -> date | None:
    text = _text(value)[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_between(first: Any, second: Any) -> int | None:
    left, right = _date(first), _date(second)
    if not left or not right:
        return None
    return abs((left - right).days)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def _discrepancy(
    discrepancy_type: str,
    severity: str,
    explanation: str,
    *,
    expected: Any = None,
    actual: Any = None,
    difference_minor: int | None = None,
    currency: str | None = None,
    evidence: dict | None = None,
) -> dict[str, Any]:
    return {
        "discrepancy_type": discrepancy_type,
        "severity": severity,
        "expected_value": None if expected is None else _text(expected),
        "actual_value": None if actual is None else _text(actual),
        "difference_minor": difference_minor,
        "currency": currency,
        "explanation": explanation,
        "evidence": json_safe(evidence or {}),
    }


def _vendor_name(invoice: Invoice) -> str:
    return invoice.vendor.vendor_name if invoice.vendor else "Unresolved vendor"



def document_quality_check(invoice: Invoice) -> dict[str, Any]:
    discrepancies: list[dict[str, Any]] = []
    if not invoice.documents:
        discrepancies.append(
            _discrepancy(
                "missing_supporting_document",
                "medium",
                "No original invoice document is linked to this record. Obtain the source document before approving or paying.",
                expected="Original invoice document",
                actual="None linked",
                currency=invoice.currency,
                evidence={"invoice_id": invoice.id},
            )
        )
    metadata = invoice.extraction_metadata if isinstance(invoice.extraction_metadata, dict) else {}
    missing_fields = list(metadata.get("missing_fields") or [])
    if missing_fields:
        discrepancies.append(
            _discrepancy(
                "extraction_incomplete",
                "medium",
                "Some invoice fields could not be extracted deterministically and require human verification.",
                expected="Complete invoice fields",
                actual=", ".join(missing_fields),
                currency=invoice.currency,
                evidence={"invoice_id": invoice.id, "missing_fields": missing_fields},
            )
        )
    return {
        "status": "review" if discrepancies else "complete",
        "missing_fields": missing_fields,
        "document_count": len(invoice.documents),
        "discrepancies": discrepancies,
    }

def detect_duplicate_invoice(db: Session, invoice: Invoice) -> dict[str, Any]:
    candidates: dict[int, dict[str, Any]] = {}
    normalized = _normalize_number(invoice.invoice_number)

    query = db.query(Invoice).filter(Invoice.id != invoice.id)
    if invoice.vendor_id:
        query = query.filter(Invoice.vendor_id == invoice.vendor_id)
    for other in query.all():
        reasons: list[str] = []
        match_type = "possible"
        if normalized and _normalize_number(other.invoice_number) == normalized:
            reasons.append("Same vendor and same normalized invoice number")
            match_type = "exact"
        if (
            invoice.total_minor is not None
            and other.total_minor == invoice.total_minor
            and invoice.currency == other.currency
        ):
            day_gap = _days_between(invoice.invoice_date, other.invoice_date)
            if day_gap is not None and day_gap <= 7:
                reasons.append(f"Same vendor, amount and currency with invoice dates {day_gap} day(s) apart")
            if invoice.purchase_order_id and invoice.purchase_order_id == other.purchase_order_id:
                reasons.append("Same purchase order and invoice amount")
        if reasons:
            candidates[other.id] = {
                "match_type": match_type,
                "invoice_id": other.id,
                "invoice_number": other.invoice_number,
                "invoice_date": other.invoice_date,
                "total_minor": other.total_minor,
                "currency": other.currency,
                "reasons": reasons,
            }

    current_hashes = {document.file_hash for document in invoice.documents if document.file_hash}
    current_rows = set((invoice.extraction_metadata or {}).get("source_rows") or []) if isinstance(invoice.extraction_metadata, dict) else set()
    if current_hashes:
        documents = (
            db.query(InvoiceDocument)
            .filter(InvoiceDocument.file_hash.in_(current_hashes), InvoiceDocument.invoice_id.isnot(None))
            .all()
        )
        for document in documents:
            if document.invoice_id == invoice.id:
                continue
            other = db.get(Invoice, document.invoice_id)
            if not other:
                continue
            other_rows = set((other.extraction_metadata or {}).get("source_rows") or []) if isinstance(other.extraction_metadata, dict) else set()
            if current_rows and other_rows and current_rows.isdisjoint(other_rows):
                # The same workbook/CSV can legitimately contain several invoices on different rows.
                continue
            entry = candidates.setdefault(
                other.id,
                {
                    "match_type": "exact",
                    "invoice_id": other.id,
                    "invoice_number": other.invoice_number,
                    "invoice_date": other.invoice_date,
                    "total_minor": other.total_minor,
                    "currency": other.currency,
                    "reasons": [],
                },
            )
            entry["match_type"] = "exact"
            entry["reasons"].append(f"Identical document hash {document.file_hash[:12]}…")
            entry["document_hash"] = document.file_hash

    matches = sorted(candidates.values(), key=lambda item: (item["match_type"] != "exact", item["invoice_id"]))
    discrepancies = []
    for match in matches:
        exact = match["match_type"] == "exact"
        discrepancies.append(
            _discrepancy(
                "duplicate_invoice" if exact else "possible_duplicate_invoice",
                "high" if exact else "medium",
                "Invoice appears to duplicate an existing invoice. Confirm whether it is a re-submission, credit note, or a separate legitimate purchase.",
                expected=f"Unique invoice for {_vendor_name(invoice)}",
                actual=f"Invoice {invoice.invoice_number or invoice.id} resembles invoice {match['invoice_number'] or match['invoice_id']}",
                difference_minor=invoice.total_minor,
                currency=invoice.currency,
                evidence={"candidate": match},
            )
        )
    return {
        "status": "duplicate" if any(item["match_type"] == "exact" for item in matches) else ("possible" if matches else "clear"),
        "matches": matches,
        "discrepancies": discrepancies,
    }


def _find_purchase_order(db: Session, invoice: Invoice) -> PurchaseOrder | None:
    if invoice.purchase_order_id:
        po = db.get(PurchaseOrder, invoice.purchase_order_id)
        if po:
            return po
    number = invoice.purchase_order_number
    if not number and isinstance(invoice.extraction_metadata, dict):
        number = invoice.extraction_metadata.get("purchase_order_number")
    if number:
        return db.query(PurchaseOrder).filter(PurchaseOrder.purchase_order_number == _text(number)).first()
    return None


def _po_item_for(invoice_item, po_items):
    if invoice_item.item_code:
        code = _normalize_number(invoice_item.item_code)
        for item in po_items:
            if _normalize_number(item.item_code) == code:
                return item
    description = _normalize_number(invoice_item.description)
    for item in po_items:
        if _normalize_number(item.description) == description:
            return item
    return None


def _po_line_total(item) -> int | None:
    if item.line_total_minor is not None:
        return item.line_total_minor
    quantity = _decimal(item.quantity)
    price = _decimal(item.unit_price)
    if quantity is None or price is None:
        return None
    return to_minor(quantity * price)


def match_purchase_order(db: Session, invoice: Invoice, tolerance_minor: int = 0) -> dict[str, Any]:
    po = _find_purchase_order(db, invoice)
    currency = invoice.currency
    if not po:
        discrepancy = _discrepancy(
            "missing_purchase_order",
            "medium",
            "No matching purchase order was found. Verify whether a PO is required for this vendor and amount before payment.",
            expected="Approved purchase order",
            actual=invoice.purchase_order_number or "No PO reference on invoice",
            currency=currency,
            evidence={"invoice_id": invoice.id, "po_reference": invoice.purchase_order_number},
        )
        return {
            "status": "no_purchase_order",
            "purchase_order": None,
            "item_results": [],
            "discrepancies": [discrepancy],
            "matched_amount_minor": None,
            "difference_minor": None,
        }

    discrepancies: list[dict[str, Any]] = []
    if po.vendor_id != invoice.vendor_id:
        discrepancies.append(
            _discrepancy(
                "vendor_mismatch",
                "high",
                "The purchase order belongs to a different vendor than the invoice.",
                expected=po.vendor.vendor_name if po.vendor else po.vendor_id,
                actual=_vendor_name(invoice),
                currency=currency,
                evidence={"purchase_order_id": po.id, "po_vendor_id": po.vendor_id, "invoice_vendor_id": invoice.vendor_id},
            )
        )
    if po.currency != invoice.currency:
        discrepancies.append(
            _discrepancy(
                "currency_mismatch",
                "high",
                "Invoice and purchase order currencies differ. Do not compare or pay without a documented conversion.",
                expected=po.currency,
                actual=invoice.currency,
                currency=invoice.currency,
                evidence={"purchase_order_id": po.id},
            )
        )

    po_items = list(po.items)
    item_results: list[dict[str, Any]] = []
    for invoice_item in invoice.items:
        po_item = _po_item_for(invoice_item, po_items)
        if not po_item:
            discrepancies.append(
                _discrepancy(
                    "item_not_on_po",
                    "medium",
                    "An invoice line item was not found on the purchase order.",
                    expected="Item listed on purchase order",
                    actual=invoice_item.item_code or invoice_item.description,
                    difference_minor=invoice_item.line_total_minor,
                    currency=currency,
                    evidence={"invoice_item_id": invoice_item.id, "purchase_order_id": po.id},
                )
            )
            item_results.append({"invoice_item_id": invoice_item.id, "status": "not_on_po"})
            continue
        result = {"invoice_item_id": invoice_item.id, "po_item_id": po_item.id, "status": "matched"}
        invoice_quantity = _decimal(invoice_item.quantity) or Decimal("0")
        po_quantity = _decimal(po_item.quantity) or Decimal("0")
        if invoice_quantity > po_quantity:
            result["status"] = "quantity_mismatch"
            discrepancies.append(
                _discrepancy(
                    "quantity_mismatch",
                    "high",
                    "Invoiced quantity is greater than the quantity ordered.",
                    expected=str(po_quantity),
                    actual=str(invoice_quantity),
                    difference_minor=invoice_item.line_total_minor,
                    currency=currency,
                    evidence={"invoice_item_id": invoice_item.id, "po_item_id": po_item.id},
                )
            )
        invoice_price = _decimal(invoice_item.unit_price)
        po_price = _decimal(po_item.unit_price)
        if invoice_price is not None and po_price is not None and invoice_price != po_price:
            price_difference_minor = to_minor(abs(invoice_price - po_price) * invoice_quantity)
            result["status"] = "unit_price_mismatch"
            discrepancies.append(
                _discrepancy(
                    "unit_price_mismatch",
                    "medium",
                    "Invoice unit price differs from the purchase order price.",
                    expected=str(po_price),
                    actual=str(invoice_price),
                    difference_minor=price_difference_minor,
                    currency=currency,
                    evidence={"invoice_item_id": invoice_item.id, "po_item_id": po_item.id},
                )
            )
        invoice_tax_rate = _decimal(invoice_item.tax_rate)
        po_tax_rate = _decimal(po_item.tax_rate)
        if invoice_tax_rate is not None and po_tax_rate is not None and invoice_tax_rate != po_tax_rate:
            result["status"] = "tax_rate_mismatch"
            line_base = invoice_item.line_total_minor or 0
            difference_minor = int(
                (Decimal(line_base) * abs(invoice_tax_rate - po_tax_rate) / Decimal(100)).quantize(Decimal("1"))
            )
            discrepancies.append(
                _discrepancy(
                    "tax_mismatch",
                    "medium",
                    "Invoice tax rate differs from the purchase order tax rate.",
                    expected=f"{po_tax_rate}%",
                    actual=f"{invoice_tax_rate}%",
                    difference_minor=difference_minor,
                    currency=currency,
                    evidence={"invoice_item_id": invoice_item.id, "po_item_id": po_item.id},
                )
            )
        item_results.append(result)

    po_subtotal = po.subtotal_minor
    if po_subtotal is None:
        line_totals = [_po_line_total(item) for item in po_items]
        po_subtotal = sum(item for item in line_totals if item is not None) if any(item is not None for item in line_totals) else None
    po_tax = po.tax_minor
    po_total = po.total_minor
    if po_total is None and po_subtotal is not None:
        po_total = po_subtotal + (po_tax or 0)

    for label, invoice_value, po_value, discrepancy_type in (
        ("subtotal", invoice.subtotal_minor, po_subtotal, "subtotal_mismatch"),
        ("tax", invoice.tax_minor, po_tax, "tax_mismatch"),
        ("total", invoice.total_minor, po_total, "total_mismatch"),
    ):
        if invoice_value is None or po_value is None:
            continue
        if abs(invoice_value - po_value) > tolerance_minor:
            discrepancies.append(
                _discrepancy(
                    discrepancy_type,
                    "high" if label == "total" else "medium",
                    f"Invoice {label} differs from the purchase order {label}.",
                    expected=_money(po_value),
                    actual=_money(invoice_value),
                    difference_minor=invoice_value - po_value,
                    currency=currency,
                    evidence={"purchase_order_id": po.id, "tolerance_minor": tolerance_minor},
                )
            )

    matched_amount = None
    difference = None
    if invoice.total_minor is not None and po_total is not None:
        matched_amount = min(invoice.total_minor, po_total)
        difference = invoice.total_minor - po_total
    status = "matched"
    if any(item["severity"] == "high" for item in discrepancies):
        status = "mismatched"
    elif discrepancies:
        status = "partial"
    return {
        "status": status,
        "purchase_order": {
            "id": po.id,
            "purchase_order_number": po.purchase_order_number,
            "vendor_id": po.vendor_id,
            "vendor_name": po.vendor.vendor_name if po.vendor else None,
            "currency": po.currency,
            "subtotal_minor": po.subtotal_minor,
            "tax_minor": po.tax_minor,
            "total_minor": po_total,
        },
        "item_results": item_results,
        "discrepancies": discrepancies,
        "matched_amount_minor": matched_amount,
        "difference_minor": difference,
        "tolerance_minor": tolerance_minor,
    }


def match_goods_receipts(db: Session, invoice: Invoice, po_result: dict[str, Any]) -> dict[str, Any]:
    po_info = po_result.get("purchase_order")
    if not po_info:
        return {"status": "no_purchase_order", "receipts": [], "item_results": [], "discrepancies": []}
    po = db.get(PurchaseOrder, po_info["id"])
    if not po:
        return {"status": "no_purchase_order", "receipts": [], "item_results": [], "discrepancies": []}

    receipts = list(po.goods_receipts)
    receipt_summary = [
        {
            "id": receipt.id,
            "receipt_number": receipt.receipt_number,
            "receipt_date": receipt.receipt_date,
            "status": receipt.status,
            "items": [
                {
                    "purchase_order_item_id": item.purchase_order_item_id,
                    "item_code": item.item_code,
                    "description": item.description,
                    "quantity_received": str(item.quantity_received),
                    "quantity_accepted": str(item.quantity_accepted if item.quantity_accepted is not None else item.quantity_received),
                    "quantity_rejected": str(item.quantity_rejected or 0),
                }
                for item in receipt.items
            ],
        }
        for receipt in receipts
    ]
    if not receipts:
        discrepancy = _discrepancy(
            "goods_not_received",
            "high",
            "No goods receipt exists for the purchase order. Do not pay until receipt or an approved service confirmation is evidenced.",
            expected="Goods receipt",
            actual="None recorded",
            currency=invoice.currency,
            evidence={"purchase_order_id": po.id, "purchase_order_number": po.purchase_order_number},
        )
        return {
            "status": "missing",
            "receipts": [],
            "item_results": [],
            "discrepancies": [discrepancy],
        }

    po_items = {item.id: item for item in po.items}
    item_results: list[dict[str, Any]] = []
    discrepancies: list[dict[str, Any]] = []
    for invoice_item in invoice.items:
        po_item = _po_item_for(invoice_item, list(po_items.values()))
        if not po_item:
            continue
        received = Decimal("0")
        accepted = Decimal("0")
        rejected = Decimal("0")
        used_receipts = []
        for receipt in receipts:
            for item in receipt.items:
                same_item = item.purchase_order_item_id == po_item.id
                same_code = (
                    item.item_code
                    and po_item.item_code
                    and _normalize_number(item.item_code) == _normalize_number(po_item.item_code)
                )
                if same_item or same_code:
                    received += _decimal(item.quantity_received) or Decimal("0")
                    accepted += _decimal(item.quantity_accepted if item.quantity_accepted is not None else item.quantity_received) or Decimal("0")
                    rejected += _decimal(item.quantity_rejected) or Decimal("0")
                    used_receipts.append(receipt.receipt_number)
        invoice_quantity = _decimal(invoice_item.quantity) or Decimal("0")
        result = {
            "invoice_item_id": invoice_item.id,
            "po_item_id": po_item.id,
            "quantity_invoiced": str(invoice_quantity),
            "quantity_received": str(received),
            "quantity_accepted": str(accepted),
            "quantity_rejected": str(rejected),
            "receipts": sorted(set(used_receipts)),
            "status": "matched",
        }
        if accepted < invoice_quantity:
            result["status"] = "goods_not_received" if accepted == 0 else "partial_delivery"
            discrepancies.append(
                _discrepancy(
                    "goods_not_received" if accepted == 0 else "partial_delivery",
                    "high",
                    "Accepted goods quantity is lower than the invoiced quantity.",
                    expected=str(invoice_quantity),
                    actual=str(accepted),
                    difference_minor=invoice_item.line_total_minor,
                    currency=invoice.currency,
                    evidence={
                        "invoice_item_id": invoice_item.id,
                        "po_item_id": po_item.id,
                        "receipts": sorted(set(used_receipts)),
                    },
                )
            )
        if rejected > 0:
            discrepancies.append(
                _discrepancy(
                    "goods_rejected",
                    "medium",
                    "Some received goods were rejected. Verify credit notes or replacement before paying the rejected quantity.",
                    expected="No rejected quantity",
                    actual=str(rejected),
                    currency=invoice.currency,
                    evidence={"invoice_item_id": invoice_item.id, "po_item_id": po_item.id, "receipts": sorted(set(used_receipts))},
                )
            )
        item_results.append(result)

    status = "matched"
    if any(item["severity"] == "high" for item in discrepancies):
        status = "partial" if any(item["discrepancy_type"] == "partial_delivery" for item in discrepancies) else "missing"
    elif discrepancies:
        status = "partial"
    return {"status": status, "receipts": receipt_summary, "item_results": item_results, "discrepancies": discrepancies}


def _candidate_bank_transactions(db: Session, invoice: Invoice) -> list[TransactionRecord]:
    linked_ids = {payment.bank_transaction_id for payment in invoice.payments if payment.bank_transaction_id}
    query = db.query(TransactionRecord)
    if linked_ids:
        query = query.filter(~TransactionRecord.id.in_(linked_ids))
    reference = _text(invoice.invoice_number)
    candidates: list[TransactionRecord] = []
    if reference:
        candidates = (
            query.filter(
                or_(
                    TransactionRecord.upi_ref.ilike(f"%{reference}%"),
                    TransactionRecord.reference.ilike(f"%{reference}%"),
                    TransactionRecord.transaction_id.ilike(f"%{reference}%"),
                    TransactionRecord.description.ilike(f"%{reference}%"),
                )
            )
            .limit(10)
            .all()
        )
    if not candidates and invoice.total_minor is not None and invoice.invoice_date:
        target = Decimal(invoice.total_minor) / Decimal(100)
        invoice_day = _date(invoice.invoice_date)
        if invoice_day:
            start = (invoice_day - timedelta(days=7)).isoformat()
            end = (invoice_day + timedelta(days=7)).isoformat()
            candidates = (
                query.filter(
                    TransactionRecord.date >= start,
                    TransactionRecord.date <= end,
                    TransactionRecord.amount == float(target),
                )
                .limit(5)
                .all()
            )
    return candidates


def verify_payments(db: Session, invoice: Invoice) -> dict[str, Any]:
    payments = list(invoice.payments)
    discrepancies: list[dict[str, Any]] = []
    applied_statuses = {"recorded", "verified", "matched", "reconciled"}
    applied_payments = [payment for payment in payments if payment.status in applied_statuses]
    paid_minor = sum(payment.amount_minor for payment in applied_payments)
    total = invoice.total_minor
    outstanding = max(total - paid_minor, 0) if total is not None else None
    overpayment = max(paid_minor - total, 0) if total is not None else None

    candidates = _candidate_bank_transactions(db, invoice)
    suggested: list[dict[str, Any]] = []
    for transaction in candidates:
        suggested.append(
            {
                "transaction_id": transaction.id,
                "date": transaction.date,
                "amount": transaction.amount,
                "direction": transaction.direction,
                "reference": transaction.reference or transaction.upi_ref or transaction.transaction_id,
                "description": transaction.description,
            }
        )
    if candidates and not applied_payments:
        discrepancies.append(
            _discrepancy(
                "unverified_payment_candidate",
                "low",
                "A possible bank payment was found but has not been verified or linked to this invoice.",
                expected="Verified payment or no payment",
                actual=f"{len(candidates)} candidate bank transaction(s)",
                currency=invoice.currency,
                evidence={"candidates": suggested},
            )
        )

    linked_to_other: dict[int, list[int]] = {}
    for payment in payments:
        if not payment.bank_transaction_id:
            continue
        others = (
            db.query(InvoicePayment)
            .filter(
                InvoicePayment.bank_transaction_id == payment.bank_transaction_id,
                InvoicePayment.invoice_id != invoice.id,
            )
            .all()
        )
        if others:
            linked_to_other[payment.bank_transaction_id] = sorted({item.invoice_id for item in others} | {invoice.id})
    if linked_to_other:
        discrepancies.append(
            _discrepancy(
                "possible_duplicate_payment",
                "high",
                "The same bank transaction is linked to more than one invoice.",
                expected="One bank payment linked to one invoice",
                actual=f"Bank transaction(s) {sorted(linked_to_other)} linked to multiple invoices",
                currency=invoice.currency,
                evidence={"links": linked_to_other},
            )
        )

    seen: dict[tuple[str, int, str], list[int]] = {}
    for payment in payments:
        key = (_text(payment.payment_reference).lower(), payment.amount_minor, _text(payment.payment_date))
        seen.setdefault(key, []).append(payment.id)
    repeated = {key: ids for key, ids in seen.items() if len(ids) > 1 and key[0]}
    if repeated:
        discrepancies.append(
            _discrepancy(
                "possible_duplicate_payment",
                "medium",
                "Multiple payments share the same reference, amount and date.",
                expected="Unique payment entry",
                actual=f"{sum(len(ids) for ids in repeated.values())} matching payment rows",
                currency=invoice.currency,
                evidence={"payment_groups": {str(key): ids for key, ids in repeated.items()}},
            )
        )

    today = date.today()
    due = _date(invoice.due_date)
    if total is None:
        payment_status = "unknown"
        discrepancies.append(
            _discrepancy(
                "missing_invoice_total",
                "high",
                "Invoice total is missing, so payment status cannot be determined.",
                expected="Invoice total",
                actual="Missing",
                currency=invoice.currency,
                evidence={"invoice_id": invoice.id},
            )
        )
    elif paid_minor == 0:
        payment_status = "unpaid"
        overdue = bool(due and due < today)
        discrepancies.append(
            _discrepancy(
                "unpaid_invoice",
                "medium" if overdue else "low",
                "No verified payment is recorded for this invoice." + (" The due date has passed." if overdue else ""),
                expected=_money(total),
                actual="0.00",
                difference_minor=total,
                currency=invoice.currency,
                evidence={"invoice_id": invoice.id, "due_date": invoice.due_date},
            )
        )
    elif paid_minor < total:
        payment_status = "partially_paid"
        discrepancies.append(
            _discrepancy(
                "partial_payment",
                "medium",
                "Recorded payments are lower than the invoice total.",
                expected=_money(total),
                actual=_money(paid_minor),
                difference_minor=total - paid_minor,
                currency=invoice.currency,
                evidence={"invoice_id": invoice.id, "payment_ids": [payment.id for payment in applied_payments]},
            )
        )
    elif paid_minor > total:
        payment_status = "overpaid"
        discrepancies.append(
            _discrepancy(
                "overpayment",
                "high",
                "Recorded payments exceed the invoice total.",
                expected=_money(total),
                actual=_money(paid_minor),
                difference_minor=paid_minor - total,
                currency=invoice.currency,
                evidence={"invoice_id": invoice.id, "payment_ids": [payment.id for payment in applied_payments]},
            )
        )
    else:
        payment_status = "paid"

    return {
        "status": payment_status,
        "paid_minor": paid_minor,
        "outstanding_minor": outstanding,
        "overpayment_minor": overpayment,
        "payments": [
            {
                "id": payment.id,
                "payment_date": payment.payment_date,
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "payment_reference": payment.payment_reference,
                "payment_method": payment.payment_method,
                "status": payment.status,
                "bank_transaction_id": payment.bank_transaction_id,
            }
            for payment in payments
        ],
        "candidate_transactions": suggested,
        "discrepancies": discrepancies,
    }


def vendor_pattern_analysis(db: Session, invoice: Invoice) -> dict[str, Any]:
    if not invoice.vendor_id:
        return {"status": "unknown", "prior_invoice_count": 0, "discrepancies": []}
    prior = (
        db.query(Invoice)
        .filter(Invoice.vendor_id == invoice.vendor_id, Invoice.id != invoice.id, Invoice.total_minor.isnot(None))
        .order_by(Invoice.created_at.desc())
        .all()
    )
    totals = [item.total_minor for item in prior if item.total_minor is not None]
    if not totals:
        return {"status": "insufficient_history", "prior_invoice_count": 0, "average_total_minor": None, "discrepancies": []}
    average = sum(totals) // len(totals)
    discrepancies: list[dict[str, Any]] = []
    if len(totals) >= 3 and invoice.total_minor is not None and average > 0 and invoice.total_minor > average * 2:
        discrepancies.append(
            _discrepancy(
                "unusual_vendor_pattern",
                "low",
                "Invoice amount is more than twice this vendor's historical average. Verify scope, approvals and supporting documents.",
                expected=f"Typical average {_money(average)}",
                actual=_money(invoice.total_minor),
                difference_minor=invoice.total_minor - average,
                currency=invoice.currency,
                evidence={"prior_invoice_count": len(totals), "prior_invoice_ids": [item.id for item in prior[:10]]},
            )
        )
    return {
        "status": "review" if discrepancies else "normal",
        "prior_invoice_count": len(totals),
        "average_total_minor": average,
        "max_total_minor": max(totals),
        "min_total_minor": min(totals),
        "discrepancies": discrepancies,
    }


def financial_analysis(db: Session, invoice: Invoice, payment_result: dict[str, Any], discrepancies: list[dict[str, Any]]) -> dict[str, Any]:
    balance_types = {"unpaid_invoice", "partial_payment"}
    exposure = sum(
        abs(item.get("difference_minor") or 0)
        for item in discrepancies
        if item.get("discrepancy_type") not in balance_types
    )
    high_exposure = sum(
        abs(item.get("difference_minor") or 0)
        for item in discrepancies
        if item.get("severity") == "high" and item.get("discrepancy_type") not in balance_types
    )
    prior_count = 0
    vendor_average = None
    if invoice.vendor_id:
        prior = (
            db.query(Invoice)
            .filter(Invoice.vendor_id == invoice.vendor_id, Invoice.id != invoice.id, Invoice.total_minor.isnot(None))
            .all()
        )
        totals = [item.total_minor for item in prior]
        prior_count = len(totals)
        vendor_average = sum(totals) // len(totals) if totals else None
    month = (invoice.invoice_date or "")[:7]
    monthly = {"month": month or None, "invoice_count": 1, "total_minor": invoice.total_minor or 0}
    if invoice.vendor_id and month:
        peers = (
            db.query(Invoice)
            .filter(Invoice.vendor_id == invoice.vendor_id, Invoice.invoice_date.like(f"{month}%"))
            .all()
        )
        monthly["invoice_count"] = len(peers)
        monthly["total_minor"] = sum(item.total_minor or 0 for item in peers)
    return {
        "currency": invoice.currency,
        "invoice_total_minor": invoice.total_minor,
        "verified_paid_minor": payment_result.get("paid_minor"),
        "outstanding_minor": payment_result.get("outstanding_minor"),
        "discrepancy_exposure_minor": exposure,
        "high_severity_exposure_minor": high_exposure,
        "discrepancy_count": len(discrepancies),
        "high_discrepancy_count": sum(1 for item in discrepancies if item.get("severity") == "high"),
        "vendor_prior_invoice_count": prior_count,
        "vendor_average_total_minor": vendor_average,
        "monthly": monthly,
        "recommended_action": (
            "Hold payment and request reviewer evidence for high-severity exceptions."
            if any(item.get("severity") == "high" for item in discrepancies)
            else "Route to an authorised reviewer before payment. No high-severity exception was detected."
        ),
    }


def persist_audit_records(
    db: Session,
    invoice: Invoice,
    audit_run_id: int | None,
    *,
    duplicate_result: dict[str, Any],
    po_result: dict[str, Any],
    gr_result: dict[str, Any],
    payment_result: dict[str, Any],
    vendor_result: dict[str, Any],
    financial_result: dict[str, Any],
    extra_discrepancies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for existing in (
        db.query(InvoiceDiscrepancy)
        .filter(InvoiceDiscrepancy.invoice_id == invoice.id, InvoiceDiscrepancy.status == "open")
        .all()
    ):
        existing.status = "superseded"
    db.query(InvoiceMatch).filter(InvoiceMatch.invoice_id == invoice.id).delete(synchronize_session=False)

    all_discrepancies: list[dict[str, Any]] = []
    for source in (duplicate_result, po_result, gr_result, payment_result, vendor_result):
        all_discrepancies.extend(source.get("discrepancies") or [])
    all_discrepancies.extend(extra_discrepancies or [])

    deduped: list[dict[str, Any]] = []
    seen = set()
    for item in all_discrepancies:
        key = (
            item.get("discrepancy_type"),
            item.get("expected_value"),
            item.get("actual_value"),
            item.get("difference_minor"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    created = []
    for item in deduped:
        record = InvoiceDiscrepancy(
            invoice_id=invoice.id,
            audit_run_id=audit_run_id,
            discrepancy_type=item["discrepancy_type"],
            severity=item["severity"],
            expected_value=item.get("expected_value"),
            actual_value=item.get("actual_value"),
            difference_minor=item.get("difference_minor"),
            currency=item.get("currency"),
            explanation=item["explanation"],
            evidence=json_safe(item.get("evidence") or {}),
            status="open",
        )
        db.add(record)
        created.append(record)

    if po_result.get("purchase_order"):
        db.add(
            InvoiceMatch(
                invoice_id=invoice.id,
                audit_run_id=audit_run_id,
                purchase_order_id=po_result["purchase_order"]["id"],
                match_type="purchase_order",
                match_status=po_result["status"],
                matched_amount_minor=po_result.get("matched_amount_minor"),
                difference_minor=po_result.get("difference_minor"),
                currency=invoice.currency,
                details=json_safe(po_result),
            )
        )
    db.add(
        InvoiceMatch(
            invoice_id=invoice.id,
            audit_run_id=audit_run_id,
            purchase_order_id=po_result.get("purchase_order", {}).get("id") if po_result.get("purchase_order") else None,
            match_type="goods_receipt",
            match_status=gr_result["status"],
            matched_amount_minor=None,
            difference_minor=None,
            currency=invoice.currency,
            details=json_safe(gr_result),
        )
    )
    db.add(
        InvoiceMatch(
            invoice_id=invoice.id,
            audit_run_id=audit_run_id,
            purchase_order_id=po_result.get("purchase_order", {}).get("id") if po_result.get("purchase_order") else None,
            match_type="payment",
            match_status=payment_result["status"],
            matched_amount_minor=payment_result.get("paid_minor"),
            difference_minor=payment_result.get("outstanding_minor"),
            currency=invoice.currency,
            details=json_safe(payment_result),
        )
    )

    invoice.paid_minor = payment_result.get("paid_minor") or 0
    invoice.outstanding_minor = payment_result.get("outstanding_minor")
    invoice.audit_status = "needs_human_review" if deduped else "no_discrepancies"
    db.flush()
    return {
        "discrepancies": [
            {
                "id": item.id,
                "discrepancy_type": item.discrepancy_type,
                "severity": item.severity,
                "expected_value": item.expected_value,
                "actual_value": item.actual_value,
                "difference_minor": item.difference_minor,
                "currency": item.currency,
                "explanation": item.explanation,
                "evidence": item.evidence,
                "status": item.status,
            }
            for item in created
        ],
        "financial_analysis": financial_result,
    }


def run_deterministic_audit(db: Session, invoice_id: int, audit_run_id: int | None = None) -> dict[str, Any]:
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise InvoiceAuditError("Invoice not found")
    duplicate_result = detect_duplicate_invoice(db, invoice)
    quality_result = document_quality_check(invoice)
    po_result = match_purchase_order(db, invoice)
    gr_result = match_goods_receipts(db, invoice, po_result)
    payment_result = verify_payments(db, invoice)
    vendor_result = vendor_pattern_analysis(db, invoice)
    all_discrepancies = (
        duplicate_result["discrepancies"]
        + quality_result["discrepancies"]
        + po_result["discrepancies"]
        + gr_result["discrepancies"]
        + payment_result["discrepancies"]
        + vendor_result["discrepancies"]
    )
    financial_result = financial_analysis(db, invoice, payment_result, all_discrepancies)
    persisted = persist_audit_records(
        db,
        invoice,
        audit_run_id,
        duplicate_result=duplicate_result,
        po_result=po_result,
        gr_result=gr_result,
        payment_result=payment_result,
        vendor_result=vendor_result,
        financial_result=financial_result,
        extra_discrepancies=quality_result["discrepancies"],
    )
    db.commit()
    return {
        "invoice_id": invoice.id,
        "document_quality": quality_result,
        "invoice_number": invoice.invoice_number,
        "currency": invoice.currency,
        "duplicate_detection": duplicate_result,
        "purchase_order_match": po_result,
        "goods_receipt_match": gr_result,
        "payment_verification": payment_result,
        "vendor_pattern": vendor_result,
        "financial_analysis": financial_result,
        **persisted,
    }


def vendor_analytics(db: Session, vendor_id: int | None = None) -> list[dict[str, Any]]:
    query = db.query(Vendor)
    if vendor_id is not None:
        query = query.filter(Vendor.id == vendor_id)
    vendors = query.order_by(Vendor.vendor_name.asc()).all()
    results = []
    for vendor in vendors:
        invoices = list(vendor.invoices)
        totals = [invoice.total_minor for invoice in invoices if invoice.total_minor is not None]
        paid = sum(invoice.paid_minor or 0 for invoice in invoices)
        outstanding = sum(invoice.outstanding_minor or 0 for invoice in invoices)
        open_discrepancies = (
            db.query(InvoiceDiscrepancy)
            .join(Invoice, Invoice.id == InvoiceDiscrepancy.invoice_id)
            .filter(Invoice.vendor_id == vendor.id, InvoiceDiscrepancy.status == "open")
            .count()
        )
        duplicate_count = (
            db.query(InvoiceDiscrepancy)
            .join(Invoice, Invoice.id == InvoiceDiscrepancy.invoice_id)
            .filter(
                Invoice.vendor_id == vendor.id,
                InvoiceDiscrepancy.discrepancy_type.in_(["duplicate_invoice", "possible_duplicate_invoice"]),
            )
            .count()
        )
        results.append(
            {
                "vendor_id": vendor.id,
                "vendor_code": vendor.vendor_code,
                "vendor_name": vendor.vendor_name,
                "currency": vendor.currency,
                "status": vendor.status,
                "invoice_count": len(invoices),
                "total_invoiced_minor": sum(totals),
                "paid_minor": paid,
                "outstanding_minor": outstanding,
                "average_invoice_minor": (sum(totals) // len(totals)) if totals else None,
                "open_discrepancy_count": open_discrepancies,
                "duplicate_count": duplicate_count,
            }
        )
    return results


def invoice_monthly_analytics(db: Session, year: int, month: int) -> dict[str, Any]:
    prefix = f"{year:04d}-{month:02d}"
    invoices = db.query(Invoice).filter(Invoice.invoice_date.like(f"{prefix}%")).order_by(Invoice.invoice_date.asc()).all()
    totals = [invoice.total_minor or 0 for invoice in invoices]
    paid = sum(invoice.paid_minor or 0 for invoice in invoices)
    outstanding = sum(invoice.outstanding_minor or 0 for invoice in invoices)
    discrepancy_rows = (
        db.query(InvoiceDiscrepancy)
        .join(Invoice, Invoice.id == InvoiceDiscrepancy.invoice_id)
        .filter(Invoice.invoice_date.like(f"{prefix}%"))
        .all()
    )
    vendor_totals: dict[str, int] = {}
    for invoice in invoices:
        name = invoice.vendor.vendor_name if invoice.vendor else "Unresolved vendor"
        vendor_totals[name] = vendor_totals.get(name, 0) + (invoice.total_minor or 0)
    type_counts: dict[str, int] = {}
    for item in discrepancy_rows:
        type_counts[item.discrepancy_type] = type_counts.get(item.discrepancy_type, 0) + 1
    return {
        "month": prefix,
        "currency": "MIXED" if len({invoice.currency for invoice in invoices}) > 1 else (invoices[0].currency if invoices else None),
        "invoice_count": len(invoices),
        "total_invoiced_minor": sum(totals),
        "paid_minor": paid,
        "outstanding_minor": outstanding,
        "average_invoice_minor": (sum(totals) // len(totals)) if totals else None,
        "open_discrepancy_count": sum(1 for item in discrepancy_rows if item.status == "open"),
        "high_severity_count": sum(1 for item in discrepancy_rows if item.severity == "high" and item.status == "open"),
        "duplicate_count": sum(
            1 for item in discrepancy_rows if item.discrepancy_type in {"duplicate_invoice", "possible_duplicate_invoice"}
        ),
        "discrepancy_exposure_minor": sum(
            abs(item.difference_minor or 0)
            for item in discrepancy_rows
            if item.status == "open" and item.discrepancy_type not in {"unpaid_invoice", "partial_payment"}
        ),
        "vendor_totals": [
            {"vendor_name": name, "total_minor": total}
            for name, total in sorted(vendor_totals.items(), key=lambda pair: pair[1], reverse=True)
        ],
        "discrepancy_types": [
            {"type": name, "count": count} for name, count in sorted(type_counts.items(), key=lambda pair: pair[1], reverse=True)
        ],
    }
