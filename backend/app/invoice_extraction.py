"""Invoice document ingestion and deterministic extraction.

The module deliberately separates document storage from financial interpretation.
Every extracted value keeps its source file/row and every missing field is reported
for human review instead of being silently invented.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from .csv_normalizer import safe_filename

MAX_INVOICE_FILE_BYTES = 15 * 1024 * 1024
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads" / "invoice_documents"

INVOICE_ALIASES = {
    "invoice_number": {"invoice_number", "invoice_no", "invoice#", "bill_number", "bill_no", "invoice_id"},
    "vendor_name": {"vendor_name", "vendor", "supplier", "supplier_name", "bill_from", "seller"},
    "vendor_code": {"vendor_code", "supplier_code", "vendor_code_number"},
    "tax_registration_number": {"tax_registration_number", "gstin", "gst_number", "vat_number", "tax_id"},
    "invoice_date": {"invoice_date", "bill_date", "invoice_dt"},
    "due_date": {"due_date", "payment_due_date"},
    "currency": {"currency", "curr", "currency_code"},
    "purchase_order_number": {"purchase_order_number", "po_number", "po_no", "purchase_order", "po"},
    "subtotal": {"subtotal", "sub_total", "taxable_amount", "net_amount"},
    "tax": {"tax_amount", "tax", "gst_amount", "vat_amount"},
    "total": {"total_amount", "total", "invoice_total", "grand_total", "gross_amount", "amount"},
    "paid": {"paid_amount", "amount_paid", "paid"},
    "subtotal_minor": {"subtotal_minor", "taxable_amount_minor", "net_amount_minor"},
    "tax_minor": {"tax_minor", "tax_amount_minor", "gst_amount_minor"},
    "total_minor": {"total_minor", "total_amount_minor", "invoice_total_minor"},
    "item_code": {"item_code", "sku", "product_code", "item_no"},
    "description": {"description", "item_description", "item", "product", "particulars", "line_description"},
    "quantity": {"quantity", "qty", "units"},
    "unit_price": {"unit_price", "rate", "price", "item_rate"},
    "tax_rate": {"tax_rate", "gst_rate", "vat_rate", "tax_percent"},
    "line_total": {"line_total", "item_total", "line_amount", "extended_amount"},
    "line_total_minor": {"line_total_minor", "item_total_minor", "line_amount_minor"},
}

DATE_ALIASES = INVOICE_ALIASES["invoice_date"]


class InvoiceExtractionError(ValueError):
    pass


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")


def _canonical_columns(columns: list[Any]) -> dict[str, str]:
    normalized = {_header(column): _text(column) for column in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in INVOICE_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[canonical] = normalized[alias]
                break
    return mapping


def _decimal(value: Any) -> Decimal | None:
    text = _text(value).replace(",", "").replace("₹", "").replace("$", "").replace("€", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def to_minor(value: Any) -> int | None:
    amount = _decimal(value)
    if amount is None:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = text.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None


def _currency(value: Any) -> str:
    text = _text(value).upper()
    return text[:3] if len(text) >= 3 and text[:3].isalpha() else "INR"


def file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def store_document(raw: bytes, filename: str) -> tuple[str, str]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    digest = file_sha256(raw)
    suffix = Path(safe_filename(filename)).suffix.lower() or ".bin"
    stored = f"{digest}{suffix}"
    path = UPLOAD_DIR / stored
    if not path.exists():
        path.write_bytes(raw)
    return stored, digest


def _read_frame(filename: str, raw: bytes) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix in {".csv", ".txt"}:
        try:
            return pd.read_csv(BytesIO(raw), encoding="utf-8-sig")
        except Exception as exc:
            raise InvoiceExtractionError(f"{filename}: could not read CSV ({exc})") from exc
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        try:
            return pd.read_excel(BytesIO(raw))
        except Exception as exc:
            raise InvoiceExtractionError(f"{filename}: could not read Excel ({exc})") from exc
    raise InvoiceExtractionError(f"{filename}: unsupported tabular file type")


def _group_key(row: dict[str, Any], fallback: str) -> tuple[str, str]:
    invoice_number = _text(row.get("invoice_number"))
    vendor = _text(row.get("vendor_name"))
    if invoice_number or vendor:
        return (invoice_number.lower(), vendor.lower())
    return (fallback.lower(), "")


def extract_tabular_invoices(filename: str, raw: bytes) -> list[dict[str, Any]]:
    frame = _read_frame(filename, raw)
    if frame.empty:
        raise InvoiceExtractionError(f"{filename}: the file contains no rows")
    mapping = _canonical_columns(list(frame.columns))
    if not mapping:
        raise InvoiceExtractionError(
            f"{filename}: no invoice columns found. Include invoice_number, vendor_name, total_amount and optional line items."
        )

    groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    identifiers = ("invoice_number", "vendor_name", "vendor_code", "tax_registration_number", "invoice_date", "due_date", "currency", "purchase_order_number")
    last_identifiers: dict[str, Any] = {}
    for offset, (_, series) in enumerate(frame.iterrows(), start=2):
        row = {canonical: series.get(original) for canonical, original in mapping.items()}
        for field in identifiers:
            if _text(row.get(field)):
                last_identifiers[field] = row.get(field)
            elif field in last_identifiers:
                row[field] = last_identifiers[field]
        key = _group_key(row, filename)
        groups.setdefault(key, []).append((offset, row))

    invoices: list[dict[str, Any]] = []
    for (_, _vendor), rows in groups.items():
        first = rows[0][1]
        missing: list[str] = []
        invoice = {
            "invoice_number": _text(first.get("invoice_number")) or None,
            "vendor_name": _text(first.get("vendor_name")) or None,
            "vendor_code": _text(first.get("vendor_code")) or None,
            "tax_registration_number": _text(first.get("tax_registration_number")) or None,
            "invoice_date": normalize_date(first.get("invoice_date")),
            "due_date": normalize_date(first.get("due_date")),
            "currency": _currency(first.get("currency")),
            "purchase_order_number": _text(first.get("purchase_order_number")) or None,
            "subtotal_minor": _first_minor(rows, "subtotal_minor", "subtotal"),
            "tax_minor": _first_minor(rows, "tax_minor", "tax"),
            "total_minor": _first_minor(rows, "total_minor", "total"),
            "paid_minor": _first_minor(rows, "paid", None),
            "source_filename": filename,
            "source_rows": [row_number for row_number, _ in rows],
        }
        for field in ("invoice_number", "vendor_name", "invoice_date", "total_minor"):
            if invoice.get(field) in (None, ""):
                missing.append(field)
        if not _text(first.get("currency")):
            missing.append("currency")

        items: list[dict[str, Any]] = []
        for row_number, row in rows:
            description = _text(row.get("description"))
            item_code = _text(row.get("item_code")) or None
            quantity = _decimal(row.get("quantity"))
            unit_price = _decimal(row.get("unit_price"))
            tax_rate = _decimal(row.get("tax_rate"))
            line_total_minor = _first_minor([(row_number, row)], "line_total_minor", "line_total")
            if not description and not item_code and line_total_minor is None and quantity is None:
                continue
            if line_total_minor is None and quantity is not None and unit_price is not None:
                line_total_minor = int((quantity * unit_price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            items.append(
                {
                    "item_code": item_code,
                    "description": description or item_code or "Unspecified item",
                    "quantity": quantity if quantity is not None else Decimal("0"),
                    "unit_price": unit_price,
                    "tax_rate": tax_rate,
                    "line_total_minor": line_total_minor,
                    "source_row": row_number,
                }
            )

        if not items:
            missing.append("line_items")
        else:
            if invoice["subtotal_minor"] is None:
                line_totals = [item["line_total_minor"] for item in items if item["line_total_minor"] is not None]
                if len(line_totals) == len(items):
                    invoice["subtotal_minor"] = sum(line_totals)
            if invoice["total_minor"] is None and invoice["subtotal_minor"] is not None:
                invoice["total_minor"] = invoice["subtotal_minor"] + (invoice["tax_minor"] or 0)
                missing.append("total_amount")

        invoice["items"] = items
        invoice["missing_fields"] = sorted(set(missing))
        invoice["extraction_status"] = "extracted" if not invoice["missing_fields"] else "needs_review"
        invoices.append(invoice)
    return invoices


def _first_minor(rows: list[tuple[int, dict[str, Any]]], minor_key: str, amount_key: str | None) -> int | None:
    for _, row in rows:
        value = to_minor(row.get(minor_key)) if minor_key else None
        if value is None and amount_key:
            value = to_minor(row.get(amount_key))
        if value is not None:
            return value
    return None


def extract_pdf_invoice(filename: str, raw: bytes) -> list[dict[str, Any]]:
    text = ""
    page_count = None
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(raw))
        page_count = len(reader.pages)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        text = ""

    invoice: dict[str, Any] = {
        "invoice_number": None,
        "vendor_name": None,
        "vendor_code": None,
        "tax_registration_number": None,
        "invoice_date": None,
        "due_date": None,
        "currency": "INR",
        "purchase_order_number": None,
        "subtotal_minor": None,
        "tax_minor": None,
        "total_minor": None,
        "paid_minor": None,
        "items": [],
        "source_filename": filename,
        "source_rows": [],
        "page_count": page_count,
    }
    if text:
        patterns = {
            "invoice_number": r"(?:invoice|bill)\s*(?:no|number|#)\s*[:#\-]?\s*([A-Za-z0-9/\-]+)",
            "purchase_order_number": r"(?:purchase\s*order|po)\s*(?:no|number|#)?\s*[:#\-]?\s*([A-Za-z0-9/\-]+)",
            "vendor_name": r"(?:vendor|supplier|bill\s*from|seller)\s*[:\-]\s*([^\n\r]+)",
            "tax_registration_number": r"(?:gstin|gst|vat|tax\s*id)\s*[:\-]?\s*([A-Za-z0-9\-]+)",
        }
        for field, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                invoice[field] = match.group(1).strip()
        for field, pattern in {
            "invoice_date": r"(?:invoice|bill)\s*date\s*[:\-]?\s*([0-9A-Za-z\-/\.]+)",
            "due_date": r"due\s*date\s*[:\-]?\s*([0-9A-Za-z\-/\.]+)",
        }.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                invoice[field] = normalize_date(match.group(1))
        for field, pattern in {
            "subtotal_minor": r"(?:sub\s*total|taxable\s*amount)\s*[:\-]?\s*[₹$€]?\s*([0-9,]+\.?[0-9]*)",
            "tax_minor": r"(?:tax|gst|vat)\s*(?:amount)?\s*[:\-]?\s*[₹$€]?\s*([0-9,]+\.?[0-9]*)",
            "total_minor": r"(?:grand\s*total|total\s*amount|invoice\s*total)\s*[:\-]?\s*[₹$€]?\s*([0-9,]+\.?[0-9]*)",
        }.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                invoice[field] = to_minor(match.group(1))
        currency = re.search(r"\b(INR|USD|EUR|GBP|AED|SGD)\b", text, re.IGNORECASE)
        if currency:
            invoice["currency"] = currency.group(1).upper()

    missing = [
        field
        for field in ("invoice_number", "vendor_name", "invoice_date", "total_minor")
        if invoice.get(field) in (None, "")
    ]
    if not re.search(r"\b(INR|USD|EUR|GBP|AED|SGD)\b", text, re.IGNORECASE):
        missing.append("currency")
    invoice["missing_fields"] = sorted(missing)
    if not invoice["items"]:
        invoice["missing_fields"].append("line_items")
    invoice["extraction_status"] = "extracted" if not invoice["missing_fields"] else "needs_review"
    invoice["extraction_metadata"] = {"text_extracted": bool(text), "page_count": page_count}
    return [invoice]


def extract_invoices(filename: str, raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        raise InvoiceExtractionError(f"{filename}: file is empty")
    if len(raw) > MAX_INVOICE_FILE_BYTES:
        raise InvoiceExtractionError(f"{filename}: file must be 15 MB or smaller")
    suffix = Path(filename).suffix.lower()
    if suffix in {".csv", ".txt", ".xlsx", ".xlsm", ".xls"}:
        return extract_tabular_invoices(filename, raw)
    if suffix == ".pdf":
        return extract_pdf_invoice(filename, raw)
    raise InvoiceExtractionError(f"{filename}: supported formats are CSV, Excel and PDF")
