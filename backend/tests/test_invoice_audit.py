import os
import tempfile
import uuid
from datetime import date

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-invoice-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.invoice_audit import run_deterministic_audit  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Invoice, InvoiceDiscrepancy, TransactionRecord  # noqa: E402

client = TestClient(app)


def uid():
    return uuid.uuid4().hex[:10]


def vendor(name=None, currency="INR"):
    payload = {"vendor_name": name or f"Vendor {uid()}", "vendor_code": f"V-{uid()}", "currency": currency}
    response = client.post("/api/vendors", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def purchase_order(vendor_id, items=None, currency="INR", number=None):
    payload = {
        "purchase_order_number": number or f"PO-{uid()}",
        "vendor_id": vendor_id,
        "order_date": "2099-01-01",
        "currency": currency,
        "items": items or [{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100, "tax_rate": 18}],
    }
    response = client.post("/api/purchase-orders", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def goods_receipt(po_id, items=None, number=None):
    payload = {
        "receipt_number": number or f"GR-{uid()}",
        "purchase_order_id": po_id,
        "receipt_date": "2099-01-02",
        "items": items or [{"item_code": "A1", "quantity_received": 10, "quantity_accepted": 10, "quantity_rejected": 0}],
    }
    response = client.post("/api/goods-receipts", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def invoice_csv(number, vendor_name, po_number=None, quantity=10, unit_price=100, tax_rate=18, total=1180, currency="INR", invoice_date="2099-01-03"):
    po = f",{po_number}" if po_number else ","
    return (
        "invoice_number,vendor_name,invoice_date,due_date,currency,purchase_order_number,"
        "item_code,description,quantity,unit_price,tax_rate,line_total,total_amount\n"
        f"{number},{vendor_name},{invoice_date},2099-12-31,{currency}{po},A1,Paper,{quantity},{unit_price},{tax_rate},{quantity * unit_price},{total}\n"
    )


def upload_invoice(number, vendor_name, po_number=None, **kwargs):
    csv = invoice_csv(number, vendor_name, po_number, **kwargs)
    response = client.post(
        "/api/invoices/upload",
        files=[("files", ("invoice.csv", csv.encode("utf-8"), "text/csv"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["invoices"][0]


def manual_invoice(vendor_id, total_minor=100000, number=None, invoice_date="2099-01-03", po_number=None, items=None):
    payload = {
        "vendor_id": vendor_id,
        "invoice_number": number or f"INV-{uid()}",
        "invoice_date": invoice_date,
        "due_date": "2099-12-31",
        "currency": "INR",
        "total_minor": total_minor,
        "purchase_order_number": po_number,
        "items": items or [{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100, "line_total_minor": 100000}],
    }
    response = client.post("/api/invoices", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def audit(invoice_id):
    response = client.post(f"/api/invoices/{invoice_id}/audit")
    assert response.status_code == 200, response.text
    return response.json()


def discrepancy_types(result):
    return {item["discrepancy_type"] for item in result["outputs"]["financial_analysis"]["persisted_discrepancies"]}


def test_upload_extracts_invoice_and_line_items():
    v = vendor()
    po = purchase_order(v["id"])
    goods_receipt(po["id"])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"])
    assert invoice["total_minor"] == 118000
    assert invoice["subtotal_minor"] == 100000
    assert invoice["item_count"] == 1
    assert invoice["extraction_status"] == "extracted"
    assert invoice["document_count"] == 1
    detail = client.get(f"/api/invoices/{invoice['id']}").json()
    assert detail["documents"][0]["file_hash"]
    assert detail["items"][0]["source_row"] == 2


def test_exact_duplicate_invoice_number_is_high_severity():
    v = vendor()
    number = f"INV-{uid()}"
    first = upload_invoice(number, v["vendor_name"])
    second = upload_invoice(number, v["vendor_name"])
    result = audit(second["id"])
    assert "duplicate_invoice" in discrepancy_types(result)
    duplicate = next(item for item in result["outputs"]["financial_analysis"]["persisted_discrepancies"] if item["discrepancy_type"] == "duplicate_invoice")
    assert duplicate["severity"] == "high"
    assert first["id"] != second["id"]


def test_possible_duplicate_same_vendor_amount_and_date():
    v = vendor()
    first = manual_invoice(v["id"], number=f"INV-{uid()}", invoice_date="2099-02-01")
    second = manual_invoice(v["id"], number=f"INV-{uid()}", invoice_date="2099-02-03")
    result = audit(second["id"])
    assert "possible_duplicate_invoice" in discrepancy_types(result)
    assert first["id"] != second["id"]


def test_missing_purchase_order_is_recorded():
    v = vendor()
    invoice = manual_invoice(v["id"])
    result = audit(invoice["id"])
    assert "missing_purchase_order" in discrepancy_types(result)


def test_vendor_mismatch_is_high_severity():
    vendor_a = vendor()
    vendor_b = vendor()
    po = purchase_order(vendor_a["id"])
    invoice = upload_invoice(f"INV-{uid()}", vendor_b["vendor_name"], po["purchase_order_number"])
    result = audit(invoice["id"])
    assert "vendor_mismatch" in discrepancy_types(result)
    assert next(item for item in result["outputs"]["financial_analysis"]["persisted_discrepancies"] if item["discrepancy_type"] == "vendor_mismatch")["severity"] == "high"


def test_currency_mismatch_is_not_compared_silently():
    v = vendor(currency="USD")
    po = purchase_order(v["id"], currency="USD")
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"], currency="INR")
    result = audit(invoice["id"])
    assert "currency_mismatch" in discrepancy_types(result)


def test_item_not_on_purchase_order():
    v = vendor()
    po = purchase_order(v["id"], items=[{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100}])
    invoice = manual_invoice(
        v["id"],
        number=f"INV-{uid()}",
        po_number=po["purchase_order_number"],
        items=[{"item_code": "B2", "description": "Ink", "quantity": 10, "unit_price": 100, "line_total_minor": 100000}],
    )
    result = audit(invoice["id"])
    assert "item_not_on_po" in discrepancy_types(result)


def test_quantity_greater_than_ordered():
    v = vendor()
    po = purchase_order(v["id"], items=[{"item_code": "A1", "description": "Paper", "quantity": 5, "unit_price": 100}])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"], quantity=10, unit_price=100, tax_rate=0, total=1000)
    result = audit(invoice["id"])
    assert "quantity_mismatch" in discrepancy_types(result)


def test_unit_price_mismatch():
    v = vendor()
    po = purchase_order(v["id"], items=[{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100}])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"], unit_price=120, tax_rate=0, total=1200)
    result = audit(invoice["id"])
    assert "unit_price_mismatch" in discrepancy_types(result)


def test_tax_rate_mismatch():
    v = vendor()
    po = purchase_order(v["id"], items=[{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100, "tax_rate": 18}])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"], tax_rate=5, total=1050)
    result = audit(invoice["id"])
    assert "tax_mismatch" in discrepancy_types(result)


def test_total_mismatch():
    v = vendor()
    po = purchase_order(v["id"], items=[{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100, "tax_rate": 0}])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"], tax_rate=0, total=1200)
    result = audit(invoice["id"])
    assert "total_mismatch" in discrepancy_types(result)


def test_missing_goods_receipt():
    v = vendor()
    po = purchase_order(v["id"])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"])
    result = audit(invoice["id"])
    assert "goods_not_received" in discrepancy_types(result)


def test_partial_delivery_is_high_severity():
    v = vendor()
    po = purchase_order(v["id"])
    goods_receipt(po["id"], items=[{"item_code": "A1", "quantity_received": 10, "quantity_accepted": 4, "quantity_rejected": 0}])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"])
    result = audit(invoice["id"])
    assert "partial_delivery" in discrepancy_types(result)
    assert next(item for item in result["outputs"]["financial_analysis"]["persisted_discrepancies"] if item["discrepancy_type"] == "partial_delivery")["severity"] == "high"


def test_rejected_goods_are_reported():
    v = vendor()
    po = purchase_order(v["id"])
    goods_receipt(po["id"], items=[{"item_code": "A1", "quantity_received": 10, "quantity_accepted": 8, "quantity_rejected": 2}])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"], quantity=8, tax_rate=0, total=800)
    result = audit(invoice["id"])
    assert "goods_rejected" in discrepancy_types(result)


def test_verified_payment_marks_invoice_paid():
    v = vendor()
    invoice = manual_invoice(v["id"], total_minor=100000)
    payment = client.post(f"/api/invoices/{invoice['id']}/payments", json={"amount_minor": 100000, "payment_reference": f"PAY-{uid()}", "status": "verified"})
    assert payment.status_code == 200, payment.text
    result = audit(invoice["id"])
    assert result["outputs"]["payment_verification"]["status"] == "paid"
    assert "unpaid_invoice" not in discrepancy_types(result)


def test_partial_payment_is_recorded():
    v = vendor()
    invoice = manual_invoice(v["id"], total_minor=100000)
    client.post(f"/api/invoices/{invoice['id']}/payments", json={"amount_minor": 40000, "status": "verified"})
    result = audit(invoice["id"])
    assert result["outputs"]["payment_verification"]["status"] == "partially_paid"
    assert "partial_payment" in discrepancy_types(result)


def test_overpayment_is_high_severity():
    v = vendor()
    invoice = manual_invoice(v["id"], total_minor=100000)
    client.post(f"/api/invoices/{invoice['id']}/payments", json={"amount_minor": 120000, "status": "verified"})
    result = audit(invoice["id"])
    assert result["outputs"]["payment_verification"]["status"] == "overpaid"
    assert next(item for item in result["outputs"]["financial_analysis"]["persisted_discrepancies"] if item["discrepancy_type"] == "overpayment")["severity"] == "high"


def test_same_bank_transaction_linked_to_multiple_invoices():
    v = vendor()
    db = SessionLocal()
    try:
        transaction = TransactionRecord(date="2099-03-01", amount=1000.0, direction="debit", description="Vendor payment")
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        transaction_id = transaction.id
    finally:
        db.close()
    first = manual_invoice(v["id"], total_minor=100000)
    second = manual_invoice(v["id"], total_minor=100000)
    for invoice in (first, second):
        response = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={"amount_minor": 100000, "status": "verified", "bank_transaction_id": transaction_id},
        )
        assert response.status_code == 200, response.text
    result = audit(second["id"])
    assert "possible_duplicate_payment" in discrepancy_types(result)


def test_missing_document_and_incomplete_extraction_are_reported():
    v = vendor()
    db = SessionLocal()
    try:
        invoice = Invoice(
            vendor_id=v["id"],
            invoice_number=f"INV-{uid()}",
            invoice_date="2099-04-01",
            currency="INR",
            total_minor=100000,
            extraction_status="needs_review",
            extraction_metadata={"missing_fields": ["line_items", "total_amount"]},
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        invoice_id = invoice.id
    finally:
        db.close()
    result = audit(invoice_id)
    types = discrepancy_types(result)
    assert "missing_supporting_document" in types
    assert "extraction_incomplete" in types


def test_review_action_persists_human_decision():
    v = vendor()
    invoice = manual_invoice(v["id"])
    audit(invoice["id"])
    discrepancy = client.get(f"/api/discrepancies?invoice_id={invoice['id']}&status=open").json()[0]
    response = client.post(
        f"/api/discrepancies/{discrepancy['id']}/review",
        json={"reviewer_name": "Reviewer One", "decision": "confirmed", "comment": "Checked source documents."},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["status"] == "confirmed"
    assert updated["review_actions"][0]["reviewer_name"] == "Reviewer One"
    assert updated["review_actions"][0]["comment"] == "Checked source documents."


def test_audit_exports_csv_and_pdf():
    v = vendor()
    invoice = manual_invoice(v["id"])
    result = audit(invoice["id"])
    run_id = result["invoice_audit_run_id"]
    csv_response = client.get(f"/api/invoice-audits/runs/{run_id}/export.csv")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    assert b"Invoice Audit Report" in csv_response.content
    pdf_response = client.get(f"/api/invoice-audits/runs/{run_id}/export.pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.content.startswith(b"%PDF-1.4")


def test_monthly_invoice_analytics():
    v = vendor()
    invoice = manual_invoice(v["id"], total_minor=50000, invoice_date="2098-03-05")
    audit(invoice["id"])
    summary = client.get("/api/invoice-analytics/monthly?year=2098&month=3")
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["invoice_count"] >= 1
    assert data["total_invoiced_minor"] >= 50000
    assert data["month"] == "2098-03"


def test_vendor_analytics_uses_actual_invoice_data():
    v = vendor()
    invoice = manual_invoice(v["id"], total_minor=25000)
    audit(invoice["id"])
    response = client.get("/api/vendors/analytics")
    assert response.status_code == 200
    row = next(item for item in response.json() if item["vendor_id"] == v["id"])
    assert row["invoice_count"] == 1
    assert row["total_invoiced_minor"] == 25000


def test_workflow_persists_investigation_run_and_evidence():
    v = vendor()
    invoice = manual_invoice(v["id"])
    result = audit(invoice["id"])
    assert result["run_id"]
    assert len(result["timeline"]) == 10
    assert result["status"] == "needs_human_review"
    investigation = client.get(f"/api/agents/runs/{result['run_id']}")
    assert investigation.status_code == 200
    data = investigation.json()
    assert data["task_type"] == "invoice_audit"
    assert len(data["timeline"]) == 10
    audit_run = client.get(f"/api/invoice-audits/runs/{result['invoice_audit_run_id']}")
    assert audit_run.status_code == 200
    assert audit_run.json()["evidence_count"] > 0
    assert audit_run.json()["workflow_run_id"] == result["run_id"]


def test_deterministic_service_runs_without_llm_configuration():
    v = vendor()
    invoice = manual_invoice(v["id"])
    db = SessionLocal()
    try:
        result = run_deterministic_audit(db, invoice["id"])
    finally:
        db.close()
    assert result["invoice_id"] == invoice["id"]
    assert "financial_analysis" in result
    assert result["payment_verification"]["status"] == "unpaid"


def test_multi_invoice_csv_rows_are_not_false_document_duplicates():
    v = vendor()
    number_one = f"INV-{uid()}"
    number_two = f"INV-{uid()}"
    csv = (
        "invoice_number,vendor_name,invoice_date,due_date,currency,purchase_order_number,"
        "item_code,description,quantity,unit_price,tax_rate,line_total,total_amount\n"
        f"{number_one},{v['vendor_name']},2099-05-01,2099-12-31,INR,,A1,Paper,1,100,0,100,100\n"
        f"{number_two},{v['vendor_name']},2099-06-01,2099-12-31,INR,,A1,Paper,2,200,0,400,400\n"
    )
    response = client.post(
        "/api/invoices/upload",
        files=[("files", ("batch.csv", csv.encode("utf-8"), "text/csv"))],
    )
    assert response.status_code == 200, response.text
    invoices = response.json()["invoices"]
    assert len(invoices) == 2
    result = audit(invoices[1]["id"])
    assert "duplicate_document" not in discrepancy_types(result)


def test_missing_currency_is_reported_for_review():
    v = vendor()
    csv = (
        "invoice_number,vendor_name,invoice_date,due_date,currency,purchase_order_number,"
        "item_code,description,quantity,unit_price,tax_rate,line_total,total_amount\n"
        f"INV-{uid()},{v['vendor_name']},2099-05-02,2099-12-31,,,A1,Paper,1,100,0,100,100\n"
    )
    response = client.post(
        "/api/invoices/upload",
        files=[("files", ("missing-currency.csv", csv.encode("utf-8"), "text/csv"))],
    )
    assert response.status_code == 200, response.text
    invoice = response.json()["invoices"][0]
    assert invoice["extraction_status"] == "needs_review"
    assert "currency" in invoice["extraction_metadata"]["missing_fields"]


def test_goods_receipt_item_must_belong_to_selected_purchase_order():
    v = vendor()
    first = purchase_order(v["id"], items=[{"item_code": "A1", "description": "Paper", "quantity": 10, "unit_price": 100}])
    second = purchase_order(v["id"], items=[{"item_code": "B2", "description": "Ink", "quantity": 5, "unit_price": 200}])
    assert second["id"] != first["id"]
    response = client.post(
        "/api/goods-receipts",
        json={
            "receipt_number": f"GR-{uid()}",
            "purchase_order_id": first["id"],
            "receipt_date": "2099-01-02",
            "items": [{"item_code": "B2", "quantity_received": 5, "quantity_accepted": 5, "quantity_rejected": 0}],
        },
    )
    assert response.status_code == 422
    assert "belonging to the selected purchase order" in response.text


def test_financial_exposure_excludes_unpaid_balance():
    v = vendor()
    invoice = manual_invoice(v["id"], total_minor=100000)
    result = audit(invoice["id"])
    assert "unpaid_invoice" in discrepancy_types(result)
    assert result["outputs"]["financial_analysis"]["discrepancy_exposure_minor"] == 0


def test_workflow_requires_human_review_even_without_discrepancies():
    v = vendor()
    po = purchase_order(v["id"])
    goods_receipt(po["id"])
    invoice = upload_invoice(f"INV-{uid()}", v["vendor_name"], po["purchase_order_number"])
    payment = client.post(
        f"/api/invoices/{invoice['id']}/payments",
        json={"amount_minor": invoice["total_minor"], "payment_reference": f"PAY-{uid()}", "status": "verified"},
    )
    assert payment.status_code == 200, payment.text
    result = audit(invoice["id"])
    assert result["outputs"]["financial_analysis"]["persisted_discrepancies"] == []
    assert result["status"] == "needs_human_review"
    assert result["require_human_review"] is True
