"""Additive FastAPI routes for invoice / accounts-payable audit."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .agents.invoice_workflow import InvoiceAuditOrchestrator
from .csv_normalizer import safe_filename
from .database import get_db
from .invoice_audit import (
    invoice_monthly_analytics,
    json_safe,
    run_deterministic_audit,
    vendor_analytics,
)
from .invoice_extraction import (
    InvoiceExtractionError,
    extract_invoices,
    store_document,
    to_minor,
)
from .models import (
    GoodsReceipt,
    GoodsReceiptItem,
    Invoice,
    InvoiceAuditRun,
    InvoiceDiscrepancy,
    InvoiceDocument,
    InvoiceItem,
    InvoiceMatch,
    InvoicePayment,
    InvoiceReviewAction,
    PurchaseOrder,
    PurchaseOrderItem,
    Vendor,
)
from .pdf_utils import build_text_pdf

router = APIRouter()
_invoice_orch = InvoiceAuditOrchestrator()

MAX_FILES = 20


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _money_minor(payload: dict[str, Any], minor_key: str, amount_key: str | None = None) -> int | None:
    if payload.get(minor_key) is not None:
        try:
            return int(payload[minor_key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{minor_key} must be an integer number of minor units")
    if amount_key and payload.get(amount_key) is not None:
        return to_minor(payload[amount_key])
    return None


def _currency(value: Any, default: str = "INR") -> str:
    text = _text(value).upper() or default
    if len(text) != 3 or not text.isalpha():
        raise HTTPException(status_code=422, detail="currency must be a three-letter ISO code")
    return text


def _today() -> str:
    return date.today().isoformat()


def _vendor_dict(vendor: Vendor) -> dict[str, Any]:
    return {
        "id": vendor.id,
        "vendor_code": vendor.vendor_code,
        "vendor_name": vendor.vendor_name,
        "tax_registration_number": vendor.tax_registration_number,
        "email": vendor.email,
        "phone": vendor.phone,
        "address": vendor.address,
        "payment_terms": vendor.payment_terms,
        "currency": vendor.currency,
        "status": vendor.status,
        "created_at": vendor.created_at.isoformat() if vendor.created_at else None,
    }


def _po_item_dict(item: PurchaseOrderItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "item_code": item.item_code,
        "description": item.description,
        "quantity": float(item.quantity) if item.quantity is not None else None,
        "unit_price": float(item.unit_price) if item.unit_price is not None else None,
        "tax_rate": float(item.tax_rate) if item.tax_rate is not None else None,
        "line_total_minor": item.line_total_minor,
    }


def _po_dict(po: PurchaseOrder, include_items: bool = True) -> dict[str, Any]:
    result = {
        "id": po.id,
        "purchase_order_number": po.purchase_order_number,
        "vendor_id": po.vendor_id,
        "vendor_name": po.vendor.vendor_name if po.vendor else None,
        "order_date": po.order_date,
        "expected_delivery_date": po.expected_delivery_date,
        "currency": po.currency,
        "subtotal_minor": po.subtotal_minor,
        "tax_minor": po.tax_minor,
        "total_minor": po.total_minor,
        "status": po.status,
        "notes": po.notes,
        "item_count": len(po.items),
        "receipt_count": len(po.goods_receipts),
        "created_at": po.created_at.isoformat() if po.created_at else None,
    }
    if include_items:
        result["items"] = [_po_item_dict(item) for item in po.items]
        result["goods_receipts"] = [
            {
                "id": receipt.id,
                "receipt_number": receipt.receipt_number,
                "receipt_date": receipt.receipt_date,
                "status": receipt.status,
            }
            for receipt in po.goods_receipts
        ]
    return result


def _gr_item_dict(item: GoodsReceiptItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "purchase_order_item_id": item.purchase_order_item_id,
        "item_code": item.item_code,
        "description": item.description,
        "quantity_received": float(item.quantity_received) if item.quantity_received is not None else None,
        "quantity_accepted": float(item.quantity_accepted) if item.quantity_accepted is not None else None,
        "quantity_rejected": float(item.quantity_rejected) if item.quantity_rejected is not None else None,
    }


def _gr_dict(receipt: GoodsReceipt, include_items: bool = True) -> dict[str, Any]:
    result = {
        "id": receipt.id,
        "receipt_number": receipt.receipt_number,
        "purchase_order_id": receipt.purchase_order_id,
        "purchase_order_number": receipt.purchase_order.purchase_order_number if receipt.purchase_order else None,
        "vendor_name": receipt.purchase_order.vendor.vendor_name if receipt.purchase_order and receipt.purchase_order.vendor else None,
        "receipt_date": receipt.receipt_date,
        "received_by": receipt.received_by,
        "status": receipt.status,
        "notes": receipt.notes,
        "item_count": len(receipt.items),
        "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
    }
    if include_items:
        result["items"] = [_gr_item_dict(item) for item in receipt.items]
    return result


def _payment_dict(payment: InvoicePayment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "invoice_id": payment.invoice_id,
        "bank_transaction_id": payment.bank_transaction_id,
        "payment_date": payment.payment_date,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "payment_reference": payment.payment_reference,
        "payment_method": payment.payment_method,
        "status": payment.status,
        "match_note": payment.match_note,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


def _discrepancy_dict(item: InvoiceDiscrepancy, include_reviews: bool = True) -> dict[str, Any]:
    result = {
        "id": item.id,
        "invoice_id": item.invoice_id,
        "invoice_number": item.invoice.invoice_number if item.invoice else None,
        "vendor_id": item.invoice.vendor_id if item.invoice else None,
        "vendor_name": item.invoice.vendor.vendor_name if item.invoice and item.invoice.vendor else None,
        "audit_run_id": item.audit_run_id,
        "discrepancy_type": item.discrepancy_type,
        "severity": item.severity,
        "expected_value": item.expected_value,
        "actual_value": item.actual_value,
        "difference_minor": item.difference_minor,
        "currency": item.currency,
        "explanation": item.explanation,
        "evidence": item.evidence,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    if include_reviews:
        result["review_actions"] = [
            {
                "id": action.id,
                "reviewer_name": action.reviewer_name,
                "decision": action.decision,
                "comment": action.comment,
                "created_at": action.created_at.isoformat() if action.created_at else None,
            }
            for action in item.review_actions
        ]
    return result


def _match_dict(item: InvoiceMatch) -> dict[str, Any]:
    return {
        "id": item.id,
        "audit_run_id": item.audit_run_id,
        "purchase_order_id": item.purchase_order_id,
        "match_type": item.match_type,
        "match_status": item.match_status,
        "matched_amount_minor": item.matched_amount_minor,
        "difference_minor": item.difference_minor,
        "currency": item.currency,
        "details": item.details,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _invoice_dict(invoice: Invoice, include_details: bool = False) -> dict[str, Any]:
    result = {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "vendor_id": invoice.vendor_id,
        "vendor_name": invoice.vendor.vendor_name if invoice.vendor else None,
        "purchase_order_id": invoice.purchase_order_id,
        "purchase_order_number": invoice.purchase_order_number,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal_minor": invoice.subtotal_minor,
        "tax_minor": invoice.tax_minor,
        "total_minor": invoice.total_minor,
        "paid_minor": invoice.paid_minor,
        "outstanding_minor": invoice.outstanding_minor,
        "status": invoice.status,
        "extraction_status": invoice.extraction_status,
        "audit_status": invoice.audit_status,
        "source_filename": invoice.source_filename,
        "extraction_metadata": invoice.extraction_metadata,
        "document_count": len(invoice.documents),
        "item_count": len(invoice.items),
        "open_discrepancy_count": sum(1 for item in invoice.discrepancies if item.status == "open"),
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
    }
    if include_details:
        result["items"] = [
            {
                "id": item.id,
                "item_code": item.item_code,
                "description": item.description,
                "quantity": float(item.quantity) if item.quantity is not None else None,
                "unit_price": float(item.unit_price) if item.unit_price is not None else None,
                "tax_rate": float(item.tax_rate) if item.tax_rate is not None else None,
                "tax_minor": item.tax_minor,
                "line_total_minor": item.line_total_minor,
                "source_row": item.source_row,
            }
            for item in invoice.items
        ]
        result["documents"] = [
            {
                "id": document.id,
                "filename": document.filename,
                "document_type": document.document_type,
                "mime_type": document.mime_type,
                "file_hash": document.file_hash,
                "file_size": document.file_size,
                "page_count": document.page_count,
                "extraction_status": document.extraction_status,
                "extraction_metadata": document.extraction_metadata,
                "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
            }
            for document in invoice.documents
        ]
        result["payments"] = [_payment_dict(payment) for payment in invoice.payments]
        result["matches"] = [_match_dict(match) for match in invoice.matches]
        result["discrepancies"] = [_discrepancy_dict(item) for item in invoice.discrepancies]
        result["vendor"] = _vendor_dict(invoice.vendor) if invoice.vendor else None
        result["purchase_order"] = _po_dict(invoice.purchase_order) if invoice.purchase_order else None
    return result


def _resolve_vendor(
    db: Session,
    *,
    vendor_id: Any = None,
    vendor_name: Any = None,
    vendor_code: Any = None,
    tax_registration_number: Any = None,
    currency: Any = None,
    create: bool = True,
) -> Vendor | None:
    if vendor_id not in (None, ""):
        try:
            vendor = db.get(Vendor, int(vendor_id))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="vendor_id must be an integer")
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return vendor
    code = _optional_text(vendor_code)
    if code:
        vendor = db.query(Vendor).filter(Vendor.vendor_code == code).first()
        if vendor:
            return vendor
    name = _optional_text(vendor_name)
    if name:
        vendor = db.query(Vendor).filter(func.lower(Vendor.vendor_name) == name.lower()).first()
        if vendor:
            if tax_registration_number and not vendor.tax_registration_number:
                vendor.tax_registration_number = _text(tax_registration_number)
            if currency and not vendor.currency:
                vendor.currency = _currency(currency)
            return vendor
        if not create:
            return None
        vendor = Vendor(
            vendor_code=code,
            vendor_name=name,
            tax_registration_number=_optional_text(tax_registration_number),
            currency=_currency(currency) if currency else None,
        )
        db.add(vendor)
        db.flush()
        return vendor
    return None


def _resolve_po(db: Session, *, purchase_order_id: Any = None, purchase_order_number: Any = None) -> PurchaseOrder | None:
    if purchase_order_id not in (None, ""):
        try:
            po = db.get(PurchaseOrder, int(purchase_order_id))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="purchase_order_id must be an integer")
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return po
    number = _optional_text(purchase_order_number)
    if number:
        return db.query(PurchaseOrder).filter(PurchaseOrder.purchase_order_number == number).first()
    return None


def _build_po(db: Session, payload: dict[str, Any]) -> PurchaseOrder:
    number = _optional_text(payload.get("purchase_order_number"))
    if not number:
        raise HTTPException(status_code=422, detail="purchase_order_number is required")
    if db.query(PurchaseOrder).filter(PurchaseOrder.purchase_order_number == number).first():
        raise HTTPException(status_code=409, detail="Purchase order number already exists")
    vendor = _resolve_vendor(
        db,
        vendor_id=payload.get("vendor_id"),
        vendor_name=payload.get("vendor_name"),
        vendor_code=payload.get("vendor_code"),
        currency=payload.get("currency"),
    )
    if not vendor:
        raise HTTPException(status_code=422, detail="vendor_id or vendor_name is required")
    currency = _currency(payload.get("currency") or vendor.currency)
    po = PurchaseOrder(
        purchase_order_number=number,
        vendor_id=vendor.id,
        order_date=_optional_text(payload.get("order_date")),
        expected_delivery_date=_optional_text(payload.get("expected_delivery_date")),
        currency=currency,
        subtotal_minor=_money_minor(payload, "subtotal_minor", "subtotal"),
        tax_minor=_money_minor(payload, "tax_minor", "tax"),
        total_minor=_money_minor(payload, "total_minor", "total"),
        status=_optional_text(payload.get("status")) or "open",
        notes=_optional_text(payload.get("notes")),
    )
    db.add(po)
    db.flush()
    line_totals: list[int] = []
    line_taxes: list[int] = []
    for item_payload in payload.get("items") or []:
        description = _optional_text(item_payload.get("description"))
        if not description:
            raise HTTPException(status_code=422, detail="Each purchase order item requires a description")
        quantity = _decimal(item_payload.get("quantity")) or Decimal("0")
        unit_price = _decimal(item_payload.get("unit_price"))
        tax_rate = _decimal(item_payload.get("tax_rate"))
        line_total = _money_minor(item_payload, "line_total_minor", "line_total")
        if line_total is None and unit_price is not None:
            line_total = to_minor(quantity * unit_price)
        item = PurchaseOrderItem(
            purchase_order_id=po.id,
            item_code=_optional_text(item_payload.get("item_code")),
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            tax_rate=tax_rate,
            line_total_minor=line_total,
        )
        db.add(item)
        if line_total is not None:
            line_totals.append(line_total)
            if tax_rate is not None:
                line_taxes.append(int((Decimal(line_total) * tax_rate / Decimal(100)).quantize(Decimal("1"))))
    if po.subtotal_minor is None and line_totals:
        po.subtotal_minor = sum(line_totals)
    if po.tax_minor is None and line_taxes:
        po.tax_minor = sum(line_taxes)
    if po.total_minor is None and po.subtotal_minor is not None:
        po.total_minor = po.subtotal_minor + (po.tax_minor or 0)
    db.commit()
    db.refresh(po)
    return po


def _build_gr(db: Session, payload: dict[str, Any]) -> GoodsReceipt:
    number = _optional_text(payload.get("receipt_number"))
    if not number:
        raise HTTPException(status_code=422, detail="receipt_number is required")
    if db.query(GoodsReceipt).filter(GoodsReceipt.receipt_number == number).first():
        raise HTTPException(status_code=409, detail="Goods receipt number already exists")
    po = _resolve_po(
        db,
        purchase_order_id=payload.get("purchase_order_id"),
        purchase_order_number=payload.get("purchase_order_number"),
    )
    if not po:
        raise HTTPException(status_code=422, detail="A valid purchase_order_id or purchase_order_number is required")
    receipt = GoodsReceipt(
        receipt_number=number,
        purchase_order_id=po.id,
        receipt_date=_optional_text(payload.get("receipt_date")) or _today(),
        received_by=_optional_text(payload.get("received_by")),
        status=_optional_text(payload.get("status")) or "received",
        notes=_optional_text(payload.get("notes")),
    )
    db.add(receipt)
    db.flush()
    po_items = list(po.items)
    for item_payload in payload.get("items") or []:
        po_item = None
        if item_payload.get("purchase_order_item_id") not in (None, ""):
            try:
                po_item = db.get(PurchaseOrderItem, int(item_payload["purchase_order_item_id"]))
            except (TypeError, ValueError):
                po_item = None
        if not po_item and item_payload.get("item_code"):
            code = re.sub(r"[^a-z0-9]", "", _text(item_payload["item_code"]).lower())
            po_item = next(
                (item for item in po_items if re.sub(r"[^a-z0-9]", "", _text(item.item_code).lower()) == code),
                None,
            )
        if not po_item or po_item.purchase_order_id != po.id:
            raise HTTPException(
                status_code=422,
                detail="Each goods receipt item must match a purchase order item belonging to the selected purchase order",
            )
        received = _decimal(item_payload.get("quantity_received"))
        if received is None or received < 0:
            raise HTTPException(status_code=422, detail="quantity_received must be zero or greater")
        accepted = _decimal(item_payload.get("quantity_accepted"))
        rejected = _decimal(item_payload.get("quantity_rejected")) or Decimal("0")
        if accepted is None:
            accepted = received
        if accepted < 0 or rejected < 0 or accepted + rejected > received:
            raise HTTPException(status_code=422, detail="accepted + rejected quantity cannot exceed received quantity")
        db.add(
            GoodsReceiptItem(
                goods_receipt_id=receipt.id,
                purchase_order_item_id=po_item.id,
                item_code=po_item.item_code,
                description=po_item.description,
                quantity_received=received,
                quantity_accepted=accepted,
                quantity_rejected=rejected,
            )
        )
    db.commit()
    db.refresh(receipt)
    return receipt


def _build_invoice(db: Session, payload: dict[str, Any]) -> Invoice:
    vendor = _resolve_vendor(
        db,
        vendor_id=payload.get("vendor_id"),
        vendor_name=payload.get("vendor_name"),
        vendor_code=payload.get("vendor_code"),
        tax_registration_number=payload.get("tax_registration_number"),
        currency=payload.get("currency"),
    )
    if not vendor:
        raise HTTPException(status_code=422, detail="vendor_id or vendor_name is required")
    po = _resolve_po(
        db,
        purchase_order_id=payload.get("purchase_order_id"),
        purchase_order_number=payload.get("purchase_order_number"),
    )
    currency = _currency(payload.get("currency") or vendor.currency)
    invoice = Invoice(
        vendor_id=vendor.id,
        purchase_order_id=po.id if po else None,
        purchase_order_number=po.purchase_order_number if po else _optional_text(payload.get("purchase_order_number")),
        invoice_number=_optional_text(payload.get("invoice_number")),
        invoice_date=_optional_text(payload.get("invoice_date")),
        due_date=_optional_text(payload.get("due_date")),
        currency=currency,
        subtotal_minor=_money_minor(payload, "subtotal_minor", "subtotal"),
        tax_minor=_money_minor(payload, "tax_minor", "tax"),
        total_minor=_money_minor(payload, "total_minor", "total"),
        paid_minor=0,
        status=_optional_text(payload.get("status")) or "received",
        extraction_status="manual",
        extraction_metadata={"missing_fields": [], "source": "manual"},
    )
    db.add(invoice)
    db.flush()
    line_totals: list[int] = []
    line_taxes: list[int] = []
    for item_payload in payload.get("items") or []:
        description = _optional_text(item_payload.get("description"))
        if not description:
            raise HTTPException(status_code=422, detail="Each invoice item requires a description")
        quantity = _decimal(item_payload.get("quantity")) or Decimal("0")
        unit_price = _decimal(item_payload.get("unit_price"))
        tax_rate = _decimal(item_payload.get("tax_rate"))
        line_total = _money_minor(item_payload, "line_total_minor", "line_total")
        if line_total is None and unit_price is not None:
            line_total = to_minor(quantity * unit_price)
        tax_minor = _money_minor(item_payload, "tax_minor", "tax")
        if tax_minor is None and line_total is not None and tax_rate is not None:
            tax_minor = int((Decimal(line_total) * tax_rate / Decimal(100)).quantize(Decimal("1")))
        db.add(
            InvoiceItem(
                invoice_id=invoice.id,
                item_code=_optional_text(item_payload.get("item_code")),
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                tax_rate=tax_rate,
                tax_minor=tax_minor,
                line_total_minor=line_total,
                source_row=item_payload.get("source_row"),
            )
        )
        if line_total is not None:
            line_totals.append(line_total)
        if tax_minor is not None:
            line_taxes.append(tax_minor)
    if invoice.subtotal_minor is None and line_totals:
        invoice.subtotal_minor = sum(line_totals)
    if invoice.tax_minor is None and line_taxes:
        invoice.tax_minor = sum(line_taxes)
    if invoice.total_minor is None and invoice.subtotal_minor is not None:
        invoice.total_minor = invoice.subtotal_minor + (invoice.tax_minor or 0)
    if invoice.total_minor is None:
        db.rollback()
        raise HTTPException(status_code=422, detail="total_minor/total or invoice line totals are required")
    invoice.outstanding_minor = invoice.total_minor
    db.commit()
    db.refresh(invoice)
    return invoice


def _recalculate_invoice_balance(db: Session, invoice: Invoice) -> None:
    applied = {"recorded", "verified", "matched", "reconciled"}
    paid = sum(payment.amount_minor for payment in invoice.payments if payment.status in applied)
    invoice.paid_minor = paid
    invoice.outstanding_minor = max((invoice.total_minor or 0) - paid, 0) if invoice.total_minor is not None else None


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------
@router.post("/api/vendors")
def create_vendor(payload: dict, db: Session = Depends(get_db)):
    name = _optional_text(payload.get("vendor_name"))
    if not name:
        raise HTTPException(status_code=422, detail="vendor_name is required")
    code = _optional_text(payload.get("vendor_code"))
    if code and db.query(Vendor).filter(Vendor.vendor_code == code).first():
        raise HTTPException(status_code=409, detail="vendor_code already exists")
    vendor = _resolve_vendor(
        db,
        vendor_name=name,
        vendor_code=code,
        tax_registration_number=payload.get("tax_registration_number"),
        currency=payload.get("currency"),
    )
    for field in ("email", "phone", "address", "payment_terms", "status"):
        if payload.get(field) is not None:
            setattr(vendor, field, _optional_text(payload.get(field)))
    db.commit()
    db.refresh(vendor)
    return _vendor_dict(vendor)


@router.get("/api/vendors/analytics")
def get_vendor_analytics(db: Session = Depends(get_db)):
    return vendor_analytics(db)


@router.get("/api/vendors")
def list_vendors(limit: int = 200, db: Session = Depends(get_db)):
    vendors = db.query(Vendor).order_by(Vendor.vendor_name.asc()).limit(min(limit, 500)).all()
    return [_vendor_dict(vendor) for vendor in vendors]


@router.get("/api/vendors/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    vendor = db.get(Vendor, vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    result = _vendor_dict(vendor)
    result["invoices"] = [_invoice_dict(invoice) for invoice in vendor.invoices]
    result["purchase_orders"] = [_po_dict(po, include_items=False) for po in vendor.purchase_orders]
    return result


@router.post("/api/vendors/import")
async def import_vendors(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = safe_filename(file.filename)
    frame = _read_upload_frame(filename, await file.read())
    mapping = _column_mapping(frame, {"vendor_name": ["vendor_name", "vendor", "supplier", "supplier_name"], "vendor_code": ["vendor_code", "supplier_code"], "tax_registration_number": ["tax_registration_number", "gstin", "vat_number", "tax_id"], "email": ["email"], "phone": ["phone"], "address": ["address"], "payment_terms": ["payment_terms"], "currency": ["currency"]})
    if "vendor_name" not in mapping:
        raise HTTPException(status_code=422, detail="vendor_name column is required")
    created = 0
    updated = 0
    errors = []
    for index, (_, row) in enumerate(frame.iterrows(), start=2):
        name = _text(row.get(mapping["vendor_name"]))
        if not name:
            errors.append(f"row {index}: vendor_name is empty")
            continue
        code = _text(row.get(mapping["vendor_code"])) if "vendor_code" in mapping else ""
        existing = None
        if code:
            existing = db.query(Vendor).filter(Vendor.vendor_code == code).first()
        if not existing:
            existing = db.query(Vendor).filter(func.lower(Vendor.vendor_name) == name.lower()).first()
        vendor = existing or Vendor(vendor_name=name, vendor_code=code or None)
        if not existing:
            db.add(vendor)
            created += 1
        else:
            updated += 1
        for field in ("tax_registration_number", "email", "phone", "address", "payment_terms", "currency"):
            if field in mapping and _text(row.get(mapping[field])):
                setattr(vendor, field, _text(row.get(mapping[field])) if field != "currency" else _currency(row.get(mapping[field])))
    db.commit()
    return {"created": created, "updated": updated, "errors": errors}


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------
@router.post("/api/purchase-orders")
def create_purchase_order(payload: dict, db: Session = Depends(get_db)):
    return _po_dict(_build_po(db, payload))


@router.get("/api/purchase-orders")
def list_purchase_orders(vendor_id: int | None = None, status: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    query = db.query(PurchaseOrder)
    if vendor_id is not None:
        query = query.filter(PurchaseOrder.vendor_id == vendor_id)
    if status:
        query = query.filter(PurchaseOrder.status == status)
    return [_po_dict(po) for po in query.order_by(PurchaseOrder.id.desc()).limit(min(limit, 500)).all()]


@router.get("/api/purchase-orders/{purchase_order_id}")
def get_purchase_order(purchase_order_id: int, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, purchase_order_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return _po_dict(po)


@router.post("/api/purchase-orders/import")
async def import_purchase_orders(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = safe_filename(file.filename)
    frame = _read_upload_frame(filename, await file.read())
    mapping = _column_mapping(
        frame,
        {
            "purchase_order_number": ["purchase_order_number", "po_number", "po_no", "purchase_order", "po"],
            "vendor_name": ["vendor_name", "vendor", "supplier", "supplier_name"],
            "vendor_code": ["vendor_code", "supplier_code"],
            "order_date": ["order_date", "date", "po_date"],
            "expected_delivery_date": ["expected_delivery_date", "delivery_date"],
            "currency": ["currency"],
            "item_code": ["item_code", "sku", "product_code"],
            "description": ["description", "item_description", "item", "product"],
            "quantity": ["quantity", "qty"],
            "unit_price": ["unit_price", "rate", "price"],
            "tax_rate": ["tax_rate", "gst_rate", "vat_rate"],
            "line_total": ["line_total", "item_total", "line_amount"],
            "subtotal": ["subtotal", "sub_total"],
            "tax": ["tax_amount", "tax", "gst_amount"],
            "total": ["total_amount", "total", "grand_total"],
        },
    )
    if "purchase_order_number" not in mapping:
        raise HTTPException(status_code=422, detail="purchase_order_number/po_number column is required")
    groups: dict[str, list[dict[str, Any]]] = {}
    for _, row in frame.iterrows():
        number = _text(row.get(mapping["purchase_order_number"]))
        if number:
            groups.setdefault(number, []).append({key: row.get(original) for key, original in mapping.items()})
    imported = 0
    skipped = []
    errors = []
    for number, rows in groups.items():
        if db.query(PurchaseOrder).filter(PurchaseOrder.purchase_order_number == number).first():
            skipped.append(number)
            continue
        first = rows[0]
        try:
            po = _build_po(
                db,
                {
                    "purchase_order_number": number,
                    "vendor_id": None,
                    "vendor_name": first.get("vendor_name"),
                    "vendor_code": first.get("vendor_code"),
                    "order_date": first.get("order_date"),
                    "expected_delivery_date": first.get("expected_delivery_date"),
                    "currency": first.get("currency"),
                    "subtotal": first.get("subtotal"),
                    "tax": first.get("tax"),
                    "total": first.get("total"),
                    "items": [
                        {
                            "item_code": row.get("item_code"),
                            "description": row.get("description") or row.get("item_code") or "Imported item",
                            "quantity": row.get("quantity"),
                            "unit_price": row.get("unit_price"),
                            "tax_rate": row.get("tax_rate"),
                            "line_total": row.get("line_total"),
                        }
                        for row in rows
                    ],
                },
            )
            imported += 1
        except HTTPException as exc:
            db.rollback()
            errors.append(f"{number}: {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append(f"{number}: {exc}")
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Goods receipts
# ---------------------------------------------------------------------------
@router.post("/api/goods-receipts")
def create_goods_receipt(payload: dict, db: Session = Depends(get_db)):
    return _gr_dict(_build_gr(db, payload))


@router.get("/api/goods-receipts")
def list_goods_receipts(purchase_order_id: int | None = None, limit: int = 200, db: Session = Depends(get_db)):
    query = db.query(GoodsReceipt)
    if purchase_order_id is not None:
        query = query.filter(GoodsReceipt.purchase_order_id == purchase_order_id)
    return [_gr_dict(receipt) for receipt in query.order_by(GoodsReceipt.id.desc()).limit(min(limit, 500)).all()]


@router.get("/api/goods-receipts/{receipt_id}")
def get_goods_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(GoodsReceipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Goods receipt not found")
    return _gr_dict(receipt)


@router.post("/api/goods-receipts/import")
async def import_goods_receipts(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = safe_filename(file.filename)
    frame = _read_upload_frame(filename, await file.read())
    mapping = _column_mapping(
        frame,
        {
            "receipt_number": ["receipt_number", "grn", "grn_number", "receipt_no"],
            "purchase_order_number": ["purchase_order_number", "po_number", "po_no", "purchase_order", "po"],
            "receipt_date": ["receipt_date", "date", "grn_date"],
            "received_by": ["received_by"],
            "status": ["status"],
            "item_code": ["item_code", "sku", "product_code"],
            "description": ["description", "item_description", "item"],
            "quantity_received": ["quantity_received", "received_qty", "quantity", "qty"],
            "quantity_accepted": ["quantity_accepted", "accepted_qty"],
            "quantity_rejected": ["quantity_rejected", "rejected_qty"],
        },
    )
    if "receipt_number" not in mapping or "purchase_order_number" not in mapping:
        raise HTTPException(status_code=422, detail="receipt_number and purchase_order_number columns are required")
    groups: dict[str, list[dict[str, Any]]] = {}
    for _, row in frame.iterrows():
        number = _text(row.get(mapping["receipt_number"]))
        if number:
            groups.setdefault(number, []).append({key: row.get(original) for key, original in mapping.items()})
    imported = 0
    skipped = []
    errors = []
    for number, rows in groups.items():
        if db.query(GoodsReceipt).filter(GoodsReceipt.receipt_number == number).first():
            skipped.append(number)
            continue
        first = rows[0]
        try:
            receipt = _build_gr(
                db,
                {
                    "receipt_number": number,
                    "purchase_order_number": first.get("purchase_order_number"),
                    "receipt_date": first.get("receipt_date"),
                    "received_by": first.get("received_by"),
                    "status": first.get("status"),
                    "items": [
                        {
                            "item_code": row.get("item_code"),
                            "description": row.get("description"),
                            "quantity_received": row.get("quantity_received"),
                            "quantity_accepted": row.get("quantity_accepted"),
                            "quantity_rejected": row.get("quantity_rejected"),
                        }
                        for row in rows
                    ],
                },
            )
            imported += 1
        except HTTPException as exc:
            db.rollback()
            errors.append(f"{number}: {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append(f"{number}: {exc}")
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
@router.post("/api/invoices/upload")
async def upload_invoices(
    files: list[UploadFile] = File(...),
    vendor_id: int | None = Form(None),
    purchase_order_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Upload no more than {MAX_FILES} files at once")
    created_invoices = []
    documents = []
    errors = []
    for upload in files:
        filename = safe_filename(upload.filename)
        raw = await upload.read()
        try:
            stored, digest = store_document(raw, filename)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{filename}: could not store document ({exc})")
            continue
        try:
            extracted = extract_invoices(filename, raw)
        except InvoiceExtractionError as exc:
            document = InvoiceDocument(
                filename=filename,
                stored_filename=stored,
                mime_type=upload.content_type,
                file_hash=digest,
                file_size=len(raw),
                extraction_status="failed",
                extraction_metadata={"error": str(exc)},
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            documents.append(document)
            errors.append(str(exc))
            continue
        for extracted_invoice in extracted:
            try:
                vendor = _resolve_vendor(
                    db,
                    vendor_id=vendor_id,
                    vendor_name=extracted_invoice.get("vendor_name"),
                    vendor_code=extracted_invoice.get("vendor_code"),
                    tax_registration_number=extracted_invoice.get("tax_registration_number"),
                    currency=extracted_invoice.get("currency"),
                )
                po = _resolve_po(
                    db,
                    purchase_order_id=purchase_order_id,
                    purchase_order_number=extracted_invoice.get("purchase_order_number"),
                )
                invoice = Invoice(
                    vendor_id=vendor.id if vendor else None,
                    purchase_order_id=po.id if po else None,
                    purchase_order_number=po.purchase_order_number if po else extracted_invoice.get("purchase_order_number"),
                    invoice_number=extracted_invoice.get("invoice_number"),
                    invoice_date=extracted_invoice.get("invoice_date"),
                    due_date=extracted_invoice.get("due_date"),
                    currency=_currency(extracted_invoice.get("currency")),
                    subtotal_minor=extracted_invoice.get("subtotal_minor"),
                    tax_minor=extracted_invoice.get("tax_minor"),
                    total_minor=extracted_invoice.get("total_minor"),
                    paid_minor=0,
                    outstanding_minor=extracted_invoice.get("total_minor"),
                    status="received",
                    extraction_status=extracted_invoice.get("extraction_status") or "needs_review",
                    source_filename=filename,
                    extraction_metadata={
                        "missing_fields": extracted_invoice.get("missing_fields") or [],
                        "source_rows": extracted_invoice.get("source_rows") or [],
                        "purchase_order_number": extracted_invoice.get("purchase_order_number"),
                        "page_count": extracted_invoice.get("page_count"),
                        **json_safe(extracted_invoice.get("extraction_metadata") or {}),
                    },
                )
                db.add(invoice)
                db.flush()
                for item in extracted_invoice.get("items") or []:
                    db.add(
                        InvoiceItem(
                            invoice_id=invoice.id,
                            item_code=item.get("item_code"),
                            description=item.get("description") or "Unspecified item",
                            quantity=item.get("quantity") or Decimal("0"),
                            unit_price=item.get("unit_price"),
                            tax_rate=item.get("tax_rate"),
                            line_total_minor=item.get("line_total_minor"),
                            source_row=item.get("source_row"),
                        )
                    )
                if extracted_invoice.get("paid_minor"):
                    db.add(
                        InvoicePayment(
                            invoice_id=invoice.id,
                            payment_date=extracted_invoice.get("invoice_date"),
                            amount_minor=int(extracted_invoice["paid_minor"]),
                            currency=invoice.currency,
                            payment_reference="Printed paid amount on invoice",
                            status="suggested",
                            match_note="Extracted paid amount is not verified. A reviewer must confirm it against bank evidence.",
                        )
                    )
                document = InvoiceDocument(
                    invoice_id=invoice.id,
                    filename=filename,
                    stored_filename=stored,
                    mime_type=upload.content_type,
                    file_hash=digest,
                    file_size=len(raw),
                    page_count=extracted_invoice.get("page_count"),
                    extraction_status=invoice.extraction_status,
                    extraction_metadata={
                        "missing_fields": extracted_invoice.get("missing_fields") or [],
                        "source_rows": extracted_invoice.get("source_rows") or [],
                        **json_safe(extracted_invoice.get("extraction_metadata") or {}),
                    },
                )
                db.add(document)
                db.commit()
                db.refresh(invoice)
                created_invoices.append(_invoice_dict(invoice))
                documents.append(document)
            except HTTPException as exc:
                db.rollback()
                errors.append(f"{filename}: {exc.detail}")
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                errors.append(f"{filename}: {type(exc).__name__}: {exc}")
    if not created_invoices and errors:
        raise HTTPException(status_code=422, detail={"message": "No invoices were created", "errors": errors})
    return {
        "invoices": created_invoices,
        "documents": [
            {
                "id": document.id,
                "invoice_id": document.invoice_id,
                "filename": document.filename,
                "file_hash": document.file_hash,
                "extraction_status": document.extraction_status,
            }
            for document in documents
        ],
        "errors": errors,
        "message": f"Created {len(created_invoices)} invoice(s) from {len(files)} file(s).",
    }


@router.post("/api/invoices")
def create_invoice(payload: dict, db: Session = Depends(get_db)):
    return _invoice_dict(_build_invoice(db, payload), include_details=True)


@router.get("/api/invoices")
def list_invoices(
    vendor_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    extraction_status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    query = db.query(Invoice)
    if vendor_id is not None:
        query = query.filter(Invoice.vendor_id == vendor_id)
    if status:
        query = query.filter(Invoice.status == status)
    if audit_status:
        query = query.filter(Invoice.audit_status == audit_status)
    if extraction_status:
        query = query.filter(Invoice.extraction_status == extraction_status)
    if search:
        query = query.filter(Invoice.invoice_number.ilike(f"%{search}%"))
    return [_invoice_dict(invoice) for invoice in query.order_by(Invoice.id.desc()).limit(min(limit, 500)).all()]


@router.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    result = _invoice_dict(invoice, include_details=True)
    latest_run = (
        db.query(InvoiceAuditRun)
        .filter(InvoiceAuditRun.invoice_id == invoice.id)
        .order_by(InvoiceAuditRun.id.desc())
        .first()
    )
    result["latest_audit_run"] = _audit_run_dict(latest_run, include_result=False) if latest_run else None
    return result


@router.post("/api/invoices/{invoice_id}/payments")
def create_invoice_payment(invoice_id: int, payload: dict, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    amount = _money_minor(payload, "amount_minor", "amount")
    if amount is None or amount <= 0:
        raise HTTPException(status_code=422, detail="amount_minor/amount must be greater than zero")
    currency = _currency(payload.get("currency") or invoice.currency)
    if currency != invoice.currency:
        raise HTTPException(status_code=422, detail="Payment currency must match the invoice currency")
    payment = InvoicePayment(
        invoice_id=invoice.id,
        bank_transaction_id=payload.get("bank_transaction_id"),
        payment_date=_optional_text(payload.get("payment_date")) or _today(),
        amount_minor=amount,
        currency=currency,
        payment_reference=_optional_text(payload.get("payment_reference")),
        payment_method=_optional_text(payload.get("payment_method")),
        status=_optional_text(payload.get("status")) or "recorded",
        match_note=_optional_text(payload.get("match_note")),
    )
    db.add(payment)
    db.flush()
    _recalculate_invoice_balance(db, invoice)
    db.commit()
    db.refresh(payment)
    return _payment_dict(payment)


@router.post("/api/invoice-payments/{payment_id}/verify")
def verify_invoice_payment(payment_id: int, payload: dict | None = None, db: Session = Depends(get_db)):
    payment = db.get(InvoicePayment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    payload = payload or {}
    payment.status = _optional_text(payload.get("status")) or "verified"
    payment.match_note = _optional_text(payload.get("match_note")) or payment.match_note
    _recalculate_invoice_balance(db, payment.invoice)
    db.commit()
    db.refresh(payment)
    return _payment_dict(payment)


@router.post("/api/invoices/{invoice_id}/audit")
def audit_invoice(invoice_id: int, db: Session = Depends(get_db)):
    if not db.get(Invoice, invoice_id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        return _invoice_orch.run({"invoice_id": invoice_id, "task_type": "invoice_audit"}, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Audit runs
# ---------------------------------------------------------------------------
def _audit_run_dict(run: InvoiceAuditRun, include_result: bool = True) -> dict[str, Any]:
    invoice = run.invoice
    result = {
        "id": run.id,
        "invoice_id": run.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else None,
        "vendor_id": invoice.vendor_id if invoice else None,
        "vendor_name": invoice.vendor.vendor_name if invoice and invoice.vendor else None,
        "workflow_run_id": run.workflow_run_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "summary": run.summary,
        "errors": run.errors,
        "evidence_count": len(run.evidence),
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if include_result:
        result["structured_result"] = run.structured_result
        result["evidence"] = [
            {
                "id": item.id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "source_reference": item.source_reference,
                "description": item.description,
                "metadata": item.evidence_metadata,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in run.evidence
        ]
    return result


@router.post("/api/invoice-audits/run")
def run_invoice_audit(payload: dict, db: Session = Depends(get_db)):
    try:
        invoice_id = int(payload.get("invoice_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invoice_id is required") from exc
    if not db.get(Invoice, invoice_id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        return _invoice_orch.run({"invoice_id": invoice_id, "task_type": "invoice_audit"}, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/invoice-audits/runs")
def list_invoice_audit_runs(invoice_id: int | None = None, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(InvoiceAuditRun)
    if invoice_id is not None:
        query = query.filter(InvoiceAuditRun.invoice_id == invoice_id)
    runs = query.order_by(InvoiceAuditRun.id.desc()).limit(min(limit, 500)).all()
    return [_audit_run_dict(run, include_result=False) for run in runs]


@router.get("/api/invoice-audits/runs/{run_id}")
def get_invoice_audit_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(InvoiceAuditRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Invoice audit run not found")
    return _audit_run_dict(run)


@router.get("/api/invoice-audits/runs/{run_id}/export.csv")
def export_invoice_audit_csv(run_id: int, db: Session = Depends(get_db)):
    run = db.get(InvoiceAuditRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Invoice audit run not found")
    output = StringIO()
    writer = csv.writer(output)
    result = run.structured_result or {}
    invoice = ((result.get("outputs") or {}).get("document_intake") or {}).get("invoice") or {}
    financial = (result.get("outputs") or {}).get("financial_analysis") or {}
    writer.writerow(["Invoice Audit Report"])
    writer.writerow(["Run ID", run.id])
    writer.writerow(["Invoice ID", run.invoice_id])
    writer.writerow(["Invoice number", invoice.get("invoice_number") or ""])
    writer.writerow(["Vendor", invoice.get("vendor_name") or ""])
    writer.writerow(["Currency", invoice.get("currency") or ""])
    writer.writerow(["Status", run.status])
    writer.writerow([])
    writer.writerow(["Financial impact"])
    writer.writerow(["Invoice total minor", financial.get("invoice_total_minor")])
    writer.writerow(["Paid minor", financial.get("verified_paid_minor")])
    writer.writerow(["Outstanding minor", financial.get("outstanding_minor")])
    writer.writerow(["Discrepancy exposure minor", financial.get("discrepancy_exposure_minor")])
    writer.writerow([])
    writer.writerow(["Discrepancy type", "Severity", "Expected", "Actual", "Difference minor", "Currency", "Status", "Explanation"])
    for item in financial.get("persisted_discrepancies") or []:
        writer.writerow([
            item.get("discrepancy_type"),
            item.get("severity"),
            item.get("expected_value"),
            item.get("actual_value"),
            item.get("difference_minor"),
            item.get("currency"),
            item.get("status"),
            item.get("explanation"),
        ])
    content = output.getvalue()
    filename = f"invoice-audit-{run.id}.csv"
    return StreamingResponse(iter([content.encode("utf-8")]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/invoice-audits/runs/{run_id}/export.pdf")
def export_invoice_audit_pdf(run_id: int, db: Session = Depends(get_db)):
    run = db.get(InvoiceAuditRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Invoice audit run not found")
    result = run.structured_result or {}
    invoice = ((result.get("outputs") or {}).get("document_intake") or {}).get("invoice") or {}
    report = (result.get("outputs") or {}).get("report") or {}
    financial = (result.get("outputs") or {}).get("financial_analysis") or {}
    lines = [
        f"Run ID: {run.id}",
        f"Invoice: {invoice.get('invoice_number') or run.invoice_id}",
        f"Vendor: {invoice.get('vendor_name') or 'Unresolved'}",
        f"Currency: {invoice.get('currency') or ''}",
        f"Status: {run.status}",
        "",
        "Executive summary",
        report.get("executive_summary") or run.summary or "",
        "",
        "Financial impact (minor units)",
        f"Invoice total: {financial.get('invoice_total_minor')}",
        f"Verified paid: {financial.get('verified_paid_minor')}",
        f"Outstanding: {financial.get('outstanding_minor')}",
        f"Discrepancy exposure: {financial.get('discrepancy_exposure_minor')}",
        "",
        "Discrepancies",
    ]
    for item in financial.get("persisted_discrepancies") or []:
        lines.append(
            f"- [{item.get('severity')}] {item.get('discrepancy_type')}: expected {item.get('expected_value')}; "
            f"actual {item.get('actual_value')}; {item.get('explanation')}"
        )
    lines.extend(["", "Limitations"])
    lines.extend(f"- {item}" for item in report.get("limitations") or [])
    lines.extend(["", "Disclaimer", report.get("disclaimer") or "Human review is required."])
    pdf = build_text_pdf("FinAudit AI - Invoice Audit Report", lines)
    filename = f"invoice-audit-{run.id}.pdf"
    return StreamingResponse(iter([pdf]), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------------------
# Discrepancies and reviews
# ---------------------------------------------------------------------------
@router.get("/api/invoices/{invoice_id}/discrepancies")
def list_invoice_discrepancies(invoice_id: int, db: Session = Depends(get_db)):
    if not db.get(Invoice, invoice_id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    items = (
        db.query(InvoiceDiscrepancy)
        .filter(InvoiceDiscrepancy.invoice_id == invoice_id)
        .order_by(InvoiceDiscrepancy.id.desc())
        .all()
    )
    return [_discrepancy_dict(item) for item in items]


@router.get("/api/discrepancies")
def list_discrepancies(
    status: str | None = None,
    severity: str | None = None,
    discrepancy_type: str | None = None,
    invoice_id: int | None = None,
    vendor_id: int | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    query = db.query(InvoiceDiscrepancy).join(Invoice, Invoice.id == InvoiceDiscrepancy.invoice_id)
    if status:
        query = query.filter(InvoiceDiscrepancy.status == status)
    if severity:
        query = query.filter(InvoiceDiscrepancy.severity == severity)
    if discrepancy_type:
        query = query.filter(InvoiceDiscrepancy.discrepancy_type == discrepancy_type)
    if invoice_id is not None:
        query = query.filter(InvoiceDiscrepancy.invoice_id == invoice_id)
    if vendor_id is not None:
        query = query.filter(Invoice.vendor_id == vendor_id)
    items = query.order_by(InvoiceDiscrepancy.id.desc()).limit(min(limit, 500)).all()
    return [_discrepancy_dict(item) for item in items]


@router.get("/api/discrepancies/{discrepancy_id}")
def get_discrepancy(discrepancy_id: int, db: Session = Depends(get_db)):
    item = db.get(InvoiceDiscrepancy, discrepancy_id)
    if not item:
        raise HTTPException(status_code=404, detail="Discrepancy not found")
    return _discrepancy_dict(item)


@router.post("/api/discrepancies/{discrepancy_id}/review")
def review_discrepancy(discrepancy_id: int, payload: dict, db: Session = Depends(get_db)):
    item = db.get(InvoiceDiscrepancy, discrepancy_id)
    if not item:
        raise HTTPException(status_code=404, detail="Discrepancy not found")
    reviewer = _optional_text(payload.get("reviewer_name"))
    decision = _optional_text(payload.get("decision"))
    if not reviewer:
        raise HTTPException(status_code=422, detail="reviewer_name is required")
    allowed = {"confirmed", "dismissed", "resolved", "needs_more_evidence", "accepted_risk"}
    if decision not in allowed:
        raise HTTPException(status_code=422, detail=f"decision must be one of {sorted(allowed)}")
    action = InvoiceReviewAction(
        discrepancy_id=item.id,
        reviewer_name=reviewer,
        decision=decision,
        comment=_optional_text(payload.get("comment")),
    )
    db.add(action)
    item.status = decision
    db.commit()
    db.refresh(item)
    return _discrepancy_dict(item)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
@router.get("/api/invoice-analytics/monthly")
def monthly_invoice_analytics(year: int, month: int, db: Session = Depends(get_db)):
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")
    return invoice_monthly_analytics(db, year, month)


@router.get("/api/invoice-analytics/vendors")
def invoice_vendor_analytics(db: Session = Depends(get_db)):
    return vendor_analytics(db)


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------
def _read_upload_frame(filename: str, raw: bytes) -> pd.DataFrame:
    if not raw:
        raise HTTPException(status_code=422, detail=f"{filename}: file is empty")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if suffix == "csv":
            return pd.read_csv(BytesIO(raw), encoding="utf-8-sig")
        if suffix in {"xlsx", "xlsm", "xls"}:
            return pd.read_excel(BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"{filename}: could not read file ({exc})") from exc
    raise HTTPException(status_code=422, detail=f"{filename}: upload a CSV or Excel file")


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")


def _column_mapping(frame: pd.DataFrame, aliases: dict[str, list[str]]) -> dict[str, str]:
    normalized = {_normalize_header(column): _text(column) for column in frame.columns}
    mapping: dict[str, str] = {}
    for canonical, names in aliases.items():
        for name in names:
            if name in normalized:
                mapping[canonical] = normalized[name]
                break
    return mapping
