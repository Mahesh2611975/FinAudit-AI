from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, JSON, Numeric
from sqlalchemy.orm import relationship
from .database import Base


class AuditRun(Base):
    __tablename__ = "audit_runs"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    source_files = Column(Text, nullable=True)
    row_count = Column(Integer, default=0)
    finding_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    findings = relationship("Finding", back_populates="run", cascade="all, delete-orphan")
    transactions = relationship("TransactionRecord", back_populates="audit_run", cascade="all, delete-orphan")


class Finding(Base):
    __tablename__ = "findings"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("audit_runs.id"), nullable=False)
    transaction_id = Column(String(100), nullable=True)
    source_file = Column(String(255), nullable=True)
    finding_type = Column(String(80), nullable=False)
    risk_level = Column(String(20), nullable=False)
    amount = Column(Float, nullable=True)
    summary = Column(String(500), nullable=False)
    evidence = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    run = relationship("AuditRun", back_populates="findings")


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id = Column(Integer, primary_key=True, index=True)
    bank_filename = Column(String(255), nullable=False)
    ledger_filename = Column(String(255), nullable=False)
    date_tolerance_days = Column(Integer, default=3)
    bank_total = Column(Float, default=0)
    ledger_total = Column(Float, default=0)
    matched_total = Column(Float, default=0)
    bank_unmatched_total = Column(Float, default=0)
    ledger_unmatched_total = Column(Float, default=0)
    unmatched_total = Column(Float, default=0)
    difference = Column(Float, default=0)
    result_count = Column(Integer, default=0)
    matched_count = Column(Integer, default=0)
    bank_only_count = Column(Integer, default=0)
    ledger_only_count = Column(Integer, default=0)
    amount_mismatch_count = Column(Integer, default=0)
    date_mismatch_count = Column(Integer, default=0)
    ambiguous_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    results = relationship(
        "ReconciliationResult",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class ReconciliationResult(Base):
    __tablename__ = "reconciliation_results"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("reconciliation_runs.id"), nullable=False)
    status = Column(String(30), nullable=False)
    bank_date = Column(String(10), nullable=True)
    bank_amount = Column(Float, nullable=True)
    bank_direction = Column(String(20), nullable=True)
    bank_reference = Column(String(255), nullable=True)
    bank_description = Column(Text, nullable=True)
    bank_source_file = Column(String(255), nullable=True)
    bank_source_row = Column(Integer, nullable=True)
    ledger_date = Column(String(10), nullable=True)
    ledger_amount = Column(Float, nullable=True)
    ledger_direction = Column(String(20), nullable=True)
    ledger_reference = Column(String(255), nullable=True)
    ledger_description = Column(Text, nullable=True)
    ledger_source_file = Column(String(255), nullable=True)
    ledger_source_row = Column(Integer, nullable=True)
    amount_difference = Column(Float, nullable=True)
    date_difference_days = Column(Integer, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    run = relationship("ReconciliationRun", back_populates="results")


class TransactionRecord(Base):
    __tablename__ = "transaction_records"
    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id"), nullable=True)
    transaction_id = Column(String(100), nullable=True)
    date = Column(String(10), nullable=True)
    amount = Column(Float, nullable=True)
    signed_amount = Column(Float, nullable=True)
    direction = Column(String(20), nullable=True)
    type = Column(String(50), nullable=True)
    vendor = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(80), nullable=True)
    account = Column(String(100), nullable=True)
    upi_ref = Column(String(255), nullable=True)
    reference = Column(String(255), nullable=True)
    source_file = Column(String(255), nullable=True)
    source_row = Column(Integer, nullable=True)
    duplicate_key = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    audit_run = relationship("AuditRun", back_populates="transactions")


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    task_type = Column(String(80), nullable=True)
    status = Column(String(40), nullable=True)
    input_state = Column(JSON, nullable=True)
    output_state = Column(JSON, nullable=True)
    timeline = Column(JSON, nullable=True)
    events = Column(JSON, nullable=True)
    evidence = Column(JSON, nullable=True)
    errors = Column(JSON, nullable=True)
    selected_year = Column(Integer, nullable=True)
    selected_month = Column(Integer, nullable=True)
    audit_run_id = Column(Integer, nullable=True)
    reconciliation_id = Column(Integer, nullable=True)
    case_id = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class InvestigationCase(Base):
    __tablename__ = "investigation_cases"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=True)
    status = Column(String(40), nullable=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id"), nullable=True)
    transaction_id = Column(String(100), nullable=True)
    source_file = Column(String(255), nullable=True)
    source_row = Column(Integer, nullable=True)
    reviewer_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Invoice / accounts-payable audit models. All monetary amounts are stored as
# integer minor units (for example paise/cents) to avoid floating-point drift.
# ---------------------------------------------------------------------------


class Vendor(Base):
    __tablename__ = "vendors"
    id = Column(Integer, primary_key=True, index=True)
    vendor_code = Column(String(80), nullable=True, unique=True, index=True)
    vendor_name = Column(String(255), nullable=False, index=True)
    tax_registration_number = Column(String(80), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    payment_terms = Column(String(80), nullable=True)
    currency = Column(String(3), nullable=True)
    status = Column(String(30), default="active")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    invoices = relationship("Invoice", back_populates="vendor")
    purchase_orders = relationship("PurchaseOrder", back_populates="vendor")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, index=True)
    purchase_order_number = Column(String(100), nullable=False, unique=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    order_date = Column(String(10), nullable=True)
    expected_delivery_date = Column(String(10), nullable=True)
    currency = Column(String(3), nullable=False, default="INR")
    subtotal_minor = Column(Integer, nullable=True)
    tax_minor = Column(Integer, nullable=True)
    total_minor = Column(Integer, nullable=True)
    status = Column(String(30), default="open")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    vendor = relationship("Vendor", back_populates="purchase_orders")
    items = relationship("PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan")
    goods_receipts = relationship("GoodsReceipt", back_populates="purchase_order")
    invoices = relationship("Invoice", back_populates="purchase_order")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    item_code = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=False)
    quantity = Column(Numeric(18, 6), nullable=False, default=0)
    unit_price = Column(Numeric(18, 6), nullable=True)
    tax_rate = Column(Numeric(9, 6), nullable=True)
    line_total_minor = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    purchase_order = relationship("PurchaseOrder", back_populates="items")
    goods_receipt_items = relationship("GoodsReceiptItem", back_populates="purchase_order_item")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id = Column(Integer, primary_key=True, index=True)
    receipt_number = Column(String(100), nullable=False, unique=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    receipt_date = Column(String(10), nullable=True)
    received_by = Column(String(255), nullable=True)
    status = Column(String(30), default="received")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    purchase_order = relationship("PurchaseOrder", back_populates="goods_receipts")
    items = relationship("GoodsReceiptItem", back_populates="goods_receipt", cascade="all, delete-orphan")


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"
    id = Column(Integer, primary_key=True, index=True)
    goods_receipt_id = Column(Integer, ForeignKey("goods_receipts.id"), nullable=False, index=True)
    purchase_order_item_id = Column(Integer, ForeignKey("purchase_order_items.id"), nullable=True, index=True)
    item_code = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=True)
    quantity_received = Column(Numeric(18, 6), nullable=False, default=0)
    quantity_accepted = Column(Numeric(18, 6), nullable=True)
    quantity_rejected = Column(Numeric(18, 6), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    goods_receipt = relationship("GoodsReceipt", back_populates="items")
    purchase_order_item = relationship("PurchaseOrderItem", back_populates="goods_receipt_items")


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True, index=True)
    purchase_order_number = Column(String(100), nullable=True, index=True)
    invoice_number = Column(String(120), nullable=True, index=True)
    invoice_date = Column(String(10), nullable=True)
    due_date = Column(String(10), nullable=True)
    currency = Column(String(3), nullable=False, default="INR")
    subtotal_minor = Column(Integer, nullable=True)
    tax_minor = Column(Integer, nullable=True)
    total_minor = Column(Integer, nullable=True)
    paid_minor = Column(Integer, default=0)
    outstanding_minor = Column(Integer, nullable=True)
    status = Column(String(30), default="received")
    extraction_status = Column(String(30), default="pending")
    audit_status = Column(String(40), default="not_audited")
    source_filename = Column(String(255), nullable=True)
    extraction_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    vendor = relationship("Vendor", back_populates="invoices")
    purchase_order = relationship("PurchaseOrder", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    documents = relationship("InvoiceDocument", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", back_populates="invoice", cascade="all, delete-orphan")
    matches = relationship("InvoiceMatch", back_populates="invoice", cascade="all, delete-orphan")
    discrepancies = relationship("InvoiceDiscrepancy", back_populates="invoice", cascade="all, delete-orphan")
    audit_runs = relationship("InvoiceAuditRun", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    item_code = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=False)
    quantity = Column(Numeric(18, 6), nullable=False, default=0)
    unit_price = Column(Numeric(18, 6), nullable=True)
    tax_rate = Column(Numeric(9, 6), nullable=True)
    tax_minor = Column(Integer, nullable=True)
    line_total_minor = Column(Integer, nullable=True)
    source_row = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="items")


class InvoiceDocument(Base):
    __tablename__ = "invoice_documents"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=True)
    document_type = Column(String(50), nullable=False, default="invoice")
    mime_type = Column(String(120), nullable=True)
    file_hash = Column(String(64), nullable=False, index=True)
    file_size = Column(Integer, nullable=True)
    page_count = Column(Integer, nullable=True)
    extraction_status = Column(String(30), default="pending")
    extraction_metadata = Column(JSON, nullable=True)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="documents")


class InvoiceMatch(Base):
    __tablename__ = "invoice_matches"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    audit_run_id = Column(Integer, ForeignKey("invoice_audit_runs.id"), nullable=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    match_type = Column(String(40), nullable=False, default="purchase_order")
    match_status = Column(String(40), nullable=False)
    matched_amount_minor = Column(Integer, nullable=True)
    difference_minor = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="matches")


class InvoiceDiscrepancy(Base):
    __tablename__ = "invoice_discrepancies"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    audit_run_id = Column(Integer, ForeignKey("invoice_audit_runs.id"), nullable=True, index=True)
    discrepancy_type = Column(String(80), nullable=False, index=True)
    severity = Column(String(20), nullable=False, default="medium")
    expected_value = Column(String(255), nullable=True)
    actual_value = Column(String(255), nullable=True)
    difference_minor = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=True)
    explanation = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    status = Column(String(30), nullable=False, default="open", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="discrepancies")
    review_actions = relationship("InvoiceReviewAction", back_populates="discrepancy", cascade="all, delete-orphan")


class InvoicePayment(Base):
    __tablename__ = "invoice_payments"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    bank_transaction_id = Column(Integer, ForeignKey("transaction_records.id"), nullable=True, index=True)
    payment_date = Column(String(10), nullable=True)
    amount_minor = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default="INR")
    payment_reference = Column(String(255), nullable=True)
    payment_method = Column(String(80), nullable=True)
    status = Column(String(30), default="unverified")
    match_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="payments")


class InvoiceAuditRun(Base):
    __tablename__ = "invoice_audit_runs"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    workflow_run_id = Column(Integer, ForeignKey("investigation_runs.id"), nullable=True, index=True)
    status = Column(String(40), nullable=False, default="running")
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    summary = Column(Text, nullable=True)
    structured_result = Column(JSON, nullable=True)
    errors = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="audit_runs")
    evidence = relationship("InvoiceAuditEvidence", back_populates="audit_run", cascade="all, delete-orphan")


class InvoiceAuditEvidence(Base):
    __tablename__ = "invoice_audit_evidence"
    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("invoice_audit_runs.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False)
    source_id = Column(String(100), nullable=True)
    source_reference = Column(String(255), nullable=True)
    description = Column(Text, nullable=False)
    evidence_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    audit_run = relationship("InvoiceAuditRun", back_populates="evidence")


class InvoiceReviewAction(Base):
    __tablename__ = "invoice_review_actions"
    id = Column(Integer, primary_key=True, index=True)
    discrepancy_id = Column(Integer, ForeignKey("invoice_discrepancies.id"), nullable=False, index=True)
    reviewer_name = Column(String(255), nullable=False)
    decision = Column(String(40), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    discrepancy = relationship("InvoiceDiscrepancy", back_populates="review_actions")
