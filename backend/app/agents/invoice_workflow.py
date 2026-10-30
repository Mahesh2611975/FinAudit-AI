"""LangGraph invoice-audit workflow.

This uses the same LangGraph library, timeline/event persistence and human-review
contract as the existing investigation orchestrator. The invoice graph adds the
ten AP-specific stages requested by the module while keeping all arithmetic and
matching deterministic.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from ..database import SessionLocal
from ..invoice_audit import (
    detect_duplicate_invoice,
    document_quality_check,
    financial_analysis,
    json_safe,
    match_goods_receipts,
    match_purchase_order,
    persist_audit_records,
    verify_payments,
    vendor_pattern_analysis,
)
from ..models import (
    Invoice,
    InvoiceAuditEvidence,
    InvoiceAuditRun,
    InvestigationRun,
)
from ..rag import retrieve_context

AGENT_DOCUMENT = "Document Intake Agent"
AGENT_EXTRACTION = "Invoice Extraction Agent"
AGENT_DUPLICATE = "Duplicate Detection Agent"
AGENT_PO = "Purchase Order Matching Agent"
AGENT_GR = "Goods Receipt Agent"
AGENT_PAYMENT = "Payment Verification Agent"
AGENT_DISCREPANCY = "Discrepancy Investigation Agent"
AGENT_EVIDENCE = "Evidence / RAG Agent"
AGENT_FINANCIAL = "Financial Impact Agent"
AGENT_REPORT = "Invoice Reporting Agent"


class InvoiceWorkflowState(TypedDict, total=False):
    input: dict[str, Any]
    invoice_id: int
    audit_run_id: int
    timeline: list[dict[str, Any]]
    events: list[dict[str, Any]]
    status: str
    errors: list[str]
    outputs: dict[str, Any]
    evidence: list[dict[str, Any]]
    missing_evidence: list[str]
    require_human_review: bool


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


def _invoice_summary(invoice: Invoice) -> dict[str, Any]:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal_minor": invoice.subtotal_minor,
        "tax_minor": invoice.tax_minor,
        "total_minor": invoice.total_minor,
        "paid_minor": invoice.paid_minor,
        "outstanding_minor": invoice.outstanding_minor,
        "status": invoice.status,
        "audit_status": invoice.audit_status,
        "extraction_status": invoice.extraction_status,
        "vendor_id": invoice.vendor_id,
        "vendor_name": invoice.vendor.vendor_name if invoice.vendor else None,
        "purchase_order_id": invoice.purchase_order_id,
        "purchase_order_number": invoice.purchase_order_number,
        "source_filename": invoice.source_filename,
        "extraction_metadata": invoice.extraction_metadata,
    }


class InvoiceAuditOrchestrator:
    def __init__(self) -> None:
        self.graph = self._build_graph()

    def _record(
        self,
        state: InvoiceWorkflowState,
        agent_name: str,
        status: str,
        started: datetime,
        finished: datetime,
        *,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        duration = _duration_ms(started, finished)
        entry = {
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
        timeline = [item for item in (state.get("timeline") or []) if item.get("agent_name") != agent_name]
        timeline.append(entry)
        state["timeline"] = timeline
        events = list(state.get("events") or [])
        events.append({"agent_name": agent_name, "event": "started", "timestamp": started.isoformat()})
        events.append(
            {
                "agent_name": agent_name,
                "event": status,
                "timestamp": finished.isoformat(),
                "duration_ms": duration,
                "summary": summary,
                "error": error,
            }
        )
        state["events"] = events

    def _execute(
        self,
        state: InvoiceWorkflowState,
        agent_name: str,
        handler: Callable[[InvoiceWorkflowState], dict[str, Any]],
    ) -> InvoiceWorkflowState:
        if state.get("status") == "failed":
            timestamp = _now()
            self._record(
                state,
                agent_name,
                "skipped",
                timestamp,
                timestamp,
                summary="Skipped because an upstream node failed.",
            )
            return state
        started = _now()
        try:
            result = handler(state) or {}
        except Exception as exc:  # noqa: BLE001 - recorded for the audit trail
            finished = _now()
            message = f"{type(exc).__name__}: {exc}"
            self._record(state, agent_name, "failed", started, finished, error=message)
            state["errors"] = list(state.get("errors") or []) + [f"{agent_name}: {message}"]
            state["status"] = "failed"
            return state
        finished = _now()
        self._record(state, agent_name, "completed", started, finished, summary=result.get("summary"))
        outputs = dict(state.get("outputs") or {})
        outputs[result.get("key", agent_name)] = result.get("output")
        state["outputs"] = outputs
        if "evidence" in result:
            state["evidence"] = list(result.get("evidence") or [])
        if "missing_evidence" in result:
            state["missing_evidence"] = list(result.get("missing_evidence") or [])
        if "require_human_review" in result:
            state["require_human_review"] = bool(result.get("require_human_review"))
        return state

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _document_intake(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                if not invoice:
                    raise ValueError("Invoice not found")
                documents = [
                    {
                        "id": document.id,
                        "filename": document.filename,
                        "document_type": document.document_type,
                        "mime_type": document.mime_type,
                        "file_hash": document.file_hash,
                        "file_size": document.file_size,
                        "page_count": document.page_count,
                        "extraction_status": document.extraction_status,
                    }
                    for document in invoice.documents
                ]
                missing = []
                if not documents:
                    missing.append("No supporting invoice document is linked to this invoice.")
                evidence = [
                    {
                        "kind": "invoice_document",
                        "source_id": f"invoice-document:{document['id']}",
                        "source_type": "invoice_document",
                        "source_reference": document["filename"],
                        "excerpt": f"{document['filename']} ({document['document_type']}, SHA-256 {document['file_hash'][:16]}…)",
                        "metadata": document,
                    }
                    for document in documents
                ]
                return {
                    "key": "document_intake",
                    "summary": f"Loaded {len(documents)} supporting document(s) for invoice {invoice.invoice_number or invoice.id}.",
                    "output": {"invoice": _invoice_summary(invoice), "documents": documents, "document_count": len(documents)},
                    "evidence": evidence,
                    "missing_evidence": missing,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_DOCUMENT, handler)

    def _invoice_extraction(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                if not invoice:
                    raise ValueError("Invoice not found")
                items = [
                    {
                        "id": item.id,
                        "item_code": item.item_code,
                        "description": item.description,
                        "quantity": str(item.quantity),
                        "unit_price": str(item.unit_price) if item.unit_price is not None else None,
                        "tax_rate": str(item.tax_rate) if item.tax_rate is not None else None,
                        "tax_minor": item.tax_minor,
                        "line_total_minor": item.line_total_minor,
                        "source_row": item.source_row,
                    }
                    for item in invoice.items
                ]
                metadata = invoice.extraction_metadata if isinstance(invoice.extraction_metadata, dict) else {}
                missing_fields = list(metadata.get("missing_fields") or [])
                derived_missing = []
                for field, value in (
                    ("invoice_number", invoice.invoice_number),
                    ("vendor", invoice.vendor_id),
                    ("invoice_date", invoice.invoice_date),
                    ("currency", invoice.currency),
                    ("total_amount", invoice.total_minor),
                ):
                    if value in (None, ""):
                        derived_missing.append(field)
                if not items:
                    derived_missing.append("line_items")
                missing_fields = sorted(set(missing_fields + derived_missing))
                confidence = round(max(0.0, 1.0 - (len(missing_fields) / 6.0)), 2)
                evidence = [
                    {
                        "kind": "invoice_line_item",
                        "source_id": f"invoice-item:{item['id']}",
                        "source_type": "invoice_item",
                        "source_reference": f"{invoice.source_filename or 'invoice'} row {item['source_row'] or '?'}",
                        "excerpt": f"{item['description']} | qty {item['quantity']} | unit {item['unit_price'] or 'n/a'} | line total minor {item['line_total_minor']}",
                        "metadata": item,
                    }
                    for item in items
                ]
                missing_evidence = []
                if missing_fields:
                    missing_evidence.append("Invoice fields require human verification: " + ", ".join(missing_fields))
                return {
                    "key": "invoice_extraction",
                    "summary": (
                        f"Extraction status {invoice.extraction_status}; {len(items)} line item(s); "
                        f"{len(missing_fields)} field(s) need review."
                    ),
                    "output": {
                        "extraction_status": invoice.extraction_status,
                        "confidence": confidence,
                        "missing_fields": missing_fields,
                        "items": items,
                        "source_filename": invoice.source_filename,
                        "source_rows": metadata.get("source_rows") or [],
                    },
                    "evidence": list(current.get("evidence") or []) + evidence,
                    "missing_evidence": missing_evidence,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_EXTRACTION, handler)

    def _duplicate_detection(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                result = detect_duplicate_invoice(db, invoice)
                evidence = [
                    {
                        "kind": "duplicate_invoice",
                        "source_id": f"duplicate-candidate:{match['invoice_id']}",
                        "source_type": "invoice",
                        "source_reference": f"Invoice {match.get('invoice_number') or match['invoice_id']}",
                        "excerpt": "; ".join(match.get("reasons") or []),
                        "metadata": match,
                    }
                    for match in result["matches"]
                ]
                return {
                    "key": "duplicate_detection",
                    "summary": f"Duplicate check status: {result['status']} ({len(result['matches'])} candidate(s)).",
                    "output": result,
                    "evidence": list(current.get("evidence") or []) + evidence,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_DUPLICATE, handler)

    def _purchase_order_matching(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                result = match_purchase_order(db, invoice)
                evidence = []
                if result.get("purchase_order"):
                    po = result["purchase_order"]
                    evidence.append(
                        {
                            "kind": "purchase_order",
                            "source_id": f"purchase-order:{po['id']}",
                            "source_type": "purchase_order",
                            "source_reference": po["purchase_order_number"],
                            "excerpt": f"PO {po['purchase_order_number']} | vendor {po.get('vendor_name')} | total minor {po.get('total_minor')} | currency {po.get('currency')}",
                            "metadata": po,
                        }
                    )
                return {
                    "key": "purchase_order_match",
                    "summary": f"PO match status: {result['status']}.",
                    "output": result,
                    "evidence": list(current.get("evidence") or []) + evidence,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_PO, handler)

    def _goods_receipt(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                po_result = (current.get("outputs") or {}).get("purchase_order_match") or {}
                result = match_goods_receipts(db, invoice, po_result)
                evidence = [
                    {
                        "kind": "goods_receipt",
                        "source_id": f"goods-receipt:{receipt['id']}",
                        "source_type": "goods_receipt",
                        "source_reference": receipt["receipt_number"],
                        "excerpt": f"Receipt {receipt['receipt_number']} on {receipt.get('receipt_date') or 'unknown date'} with {len(receipt.get('items') or [])} line(s).",
                        "metadata": receipt,
                    }
                    for receipt in result.get("receipts") or []
                ]
                return {
                    "key": "goods_receipt_match",
                    "summary": f"Goods receipt status: {result['status']}.",
                    "output": result,
                    "evidence": list(current.get("evidence") or []) + evidence,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_GR, handler)

    def _payment_verification(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                result = verify_payments(db, invoice)
                evidence = [
                    {
                        "kind": "invoice_payment",
                        "source_id": f"invoice-payment:{payment['id']}",
                        "source_type": "invoice_payment",
                        "source_reference": payment.get("payment_reference") or f"payment {payment['id']}",
                        "excerpt": f"Payment {payment.get('payment_date') or 'unknown date'} | minor {payment['amount_minor']} | status {payment['status']}",
                        "metadata": payment,
                    }
                    for payment in result.get("payments") or []
                ]
                evidence.extend(
                    {
                        "kind": "payment_candidate",
                        "source_id": f"transaction:{candidate['transaction_id']}",
                        "source_type": "transaction_record",
                        "source_reference": candidate.get("reference"),
                        "excerpt": f"Candidate bank payment {candidate.get('date')} | amount {candidate.get('amount')} | {candidate.get('description') or ''}",
                        "metadata": candidate,
                    }
                    for candidate in result.get("candidate_transactions") or []
                )
                return {
                    "key": "payment_verification",
                    "summary": f"Payment status: {result['status']}; paid minor {result.get('paid_minor')}.",
                    "output": result,
                    "evidence": list(current.get("evidence") or []) + evidence,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_PAYMENT, handler)

    def _discrepancy_investigation(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                outputs = current.get("outputs") or {}
                vendor_result = vendor_pattern_analysis(db, invoice)
                quality_result = document_quality_check(invoice)
                discrepancies = []
                for key in ("duplicate_detection", "purchase_order_match", "goods_receipt_match", "payment_verification"):
                    discrepancies.extend((outputs.get(key) or {}).get("discrepancies") or [])
                discrepancies.extend(vendor_result.get("discrepancies") or [])
                discrepancies.extend(quality_result.get("discrepancies") or [])
                facts = [
                    {
                        "type": item["discrepancy_type"],
                        "severity": item["severity"],
                        "expected": item.get("expected_value"),
                        "actual": item.get("actual_value"),
                        "difference_minor": item.get("difference_minor"),
                        "explanation": item["explanation"],
                    }
                    for item in discrepancies
                ]
                hypotheses = []
                for item in discrepancies:
                    if item["discrepancy_type"] in {"duplicate_invoice", "possible_duplicate_invoice"}:
                        hypotheses.append("The invoice may be a duplicate submission, a credit note, or a separate legitimate purchase. Evidence is required to distinguish these.")
                    elif item["discrepancy_type"] in {"goods_not_received", "partial_delivery", "goods_rejected"}:
                        hypotheses.append("A receipt may be missing, delayed, or incomplete. Verify the goods receipt and any rejection/credit note.")
                    elif item["discrepancy_type"] in {"unit_price_mismatch", "quantity_mismatch", "total_mismatch", "subtotal_mismatch", "tax_mismatch"}:
                        hypotheses.append("The invoice may contain a pricing, quantity, tax, or data-entry difference. Compare the approved PO and supplier confirmation.")
                    elif item["discrepancy_type"] in {"possible_duplicate_payment", "overpayment", "partial_payment", "unpaid_invoice"}:
                        hypotheses.append("A payment may be missing, duplicated, partially applied, or recorded against the wrong invoice. Verify the bank statement and payment ledger.")
                return {
                    "key": "discrepancy_investigation",
                    "summary": f"{len(discrepancies)} discrepancy indicator(s) documented as facts and hypotheses.",
                    "output": {
                        "discrepancies": discrepancies,
                        "facts": facts,
                        "hypotheses": sorted(set(hypotheses)),
                        "vendor_pattern": vendor_result,
                        "document_quality": quality_result,
                        "limitations": [
                            "Automated indicators are not proof of fraud or error.",
                            "A reviewer must validate source documents before payment or accounting action.",
                        ],
                    },
                    "require_human_review": True,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_DISCREPANCY, handler)

    def _evidence_retrieval(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            outputs = current.get("outputs") or {}
            invoice = (outputs.get("document_intake") or {}).get("invoice") or {}
            discrepancy_data = outputs.get("discrepancy_investigation") or {}
            question = (
                f"Invoice audit evidence for invoice {invoice.get('invoice_number')} from {invoice.get('vendor_name')}. "
                "What documents and controls should a reviewer check?"
            )
            evidence_text = " ".join(item.get("excerpt") or "" for item in current.get("evidence") or [])
            policies = retrieve_context(question, evidence_text, top_k=3)
            policy_evidence = [
                {
                    "kind": "policy",
                    "source_id": policy["source"],
                    "source_type": "policy",
                    "source_reference": policy["source"],
                    "excerpt": policy["text"],
                    "metadata": {"score": policy.get("score")},
                }
                for policy in policies
            ]
            discrepancy_evidence = [
                {
                    "kind": "discrepancy",
                    "source_id": f"discrepancy:{index}",
                    "source_type": "invoice_discrepancy",
                    "source_reference": item.get("discrepancy_type"),
                    "excerpt": item.get("explanation"),
                    "metadata": item,
                }
                for index, item in enumerate(discrepancy_data.get("discrepancies") or [], start=1)
            ]
            combined = list(current.get("evidence") or []) + policy_evidence + discrepancy_evidence
            missing = list(current.get("missing_evidence") or [])
            if not policy_evidence:
                missing.append("No relevant policy text was retrieved.")
            return {
                "key": "evidence_retrieval",
                "summary": f"Evidence set contains {len(combined)} item(s), including {len(policy_evidence)} policy reference(s).",
                "output": {
                    "evidence_count": len(combined),
                    "policy_references": policies,
                    "source_ids": [item.get("source_id") for item in combined],
                },
                "evidence": combined,
                "missing_evidence": missing,
            }

        return self._execute(state, AGENT_EVIDENCE, handler)

    def _financial_impact(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            db = SessionLocal()
            try:
                invoice = db.get(Invoice, current["invoice_id"])
                outputs = current.get("outputs") or {}
                discrepancy_data = outputs.get("discrepancy_investigation") or {}
                payment_result = outputs.get("payment_verification") or {}
                discrepancies = discrepancy_data.get("discrepancies") or []
                result = financial_analysis(db, invoice, payment_result, discrepancies)
                persisted = persist_audit_records(
                    db,
                    invoice,
                    current.get("audit_run_id"),
                    duplicate_result=outputs.get("duplicate_detection") or {},
                    po_result=outputs.get("purchase_order_match") or {},
                    gr_result=outputs.get("goods_receipt_match") or {},
                    payment_result=payment_result,
                    vendor_result=discrepancy_data.get("vendor_pattern") or {},
                    financial_result=result,
                    extra_discrepancies=(discrepancy_data.get("document_quality") or {}).get("discrepancies") or [],
                )
                db.commit()
                result["persisted_discrepancies"] = persisted["discrepancies"]
                return {
                    "key": "financial_analysis",
                    "summary": (
                        f"Outstanding minor {result.get('outstanding_minor')}; "
                        f"discrepancy exposure minor {result.get('discrepancy_exposure_minor')}; "
                        f"{result.get('high_discrepancy_count')} high-severity indicator(s)."
                    ),
                    "output": result,
                    "require_human_review": True,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_FINANCIAL, handler)

    def _report(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        def handler(current: InvoiceWorkflowState) -> dict[str, Any]:
            outputs = current.get("outputs") or {}
            invoice = (outputs.get("document_intake") or {}).get("invoice") or {}
            discrepancy_data = outputs.get("discrepancy_investigation") or {}
            financial = outputs.get("financial_analysis") or {}
            evidence = current.get("evidence") or []
            report = {
                "invoice": invoice,
                "executive_summary": (
                    f"Invoice {invoice.get('invoice_number') or invoice.get('id')} from {invoice.get('vendor_name') or 'an unresolved vendor'} "
                    f"has {len(discrepancy_data.get('discrepancies') or [])} documented discrepancy indicator(s). "
                    f"Outstanding amount minor: {financial.get('outstanding_minor')}. "
                    "No payment or accounting action has been approved by this workflow."
                ),
                "facts": discrepancy_data.get("facts") or [],
                "hypotheses": discrepancy_data.get("hypotheses") or [],
                "limitations": discrepancy_data.get("limitations") or [],
                "financial_impact": {
                    "invoice_total_minor": financial.get("invoice_total_minor"),
                    "verified_paid_minor": financial.get("verified_paid_minor"),
                    "outstanding_minor": financial.get("outstanding_minor"),
                    "discrepancy_exposure_minor": financial.get("discrepancy_exposure_minor"),
                    "high_severity_exposure_minor": financial.get("high_severity_exposure_minor"),
                    "currency": financial.get("currency"),
                },
                "recommended_actions": [
                    financial.get("recommended_action") or "Route to an authorised reviewer.",
                    "Request missing documents and validate the purchase order, receipt, invoice and payment evidence.",
                    "Record the reviewer decision before paying, rejecting, or adjusting the invoice.",
                ],
                "discrepancies": financial.get("persisted_discrepancies") or [],
                "evidence_references": [item.get("source_id") for item in evidence],
                "missing_evidence": current.get("missing_evidence") or [],
                "human_review_required": True,
                "disclaimer": (
                    "This report contains automated indicators for human review. It does not prove fraud, "
                    "approve payment, or make a final accounting decision."
                ),
            }
            return {
                "key": "report",
                "summary": "Invoice audit report assembled from deterministic matches and retrieved evidence.",
                "output": report,
                "require_human_review": True,
            }

        return self._execute(state, AGENT_REPORT, handler)

    def _route_after_intake(self, state: InvoiceWorkflowState):
        if state.get("status") == "failed":
            return END
        return "extraction"

    def _build_graph(self):
        graph = StateGraph(InvoiceWorkflowState)
        graph.add_node("intake", self._document_intake)
        graph.add_node("extraction", self._invoice_extraction)
        graph.add_node("duplicate", self._duplicate_detection)
        graph.add_node("purchase_order", self._purchase_order_matching)
        graph.add_node("goods_receipt", self._goods_receipt)
        graph.add_node("payment", self._payment_verification)
        graph.add_node("discrepancy", self._discrepancy_investigation)
        graph.add_node("evidence", self._evidence_retrieval)
        graph.add_node("financial", self._financial_impact)
        graph.add_node("report", self._report)
        graph.add_edge(START, "intake")
        graph.add_conditional_edges("intake", self._route_after_intake, {"extraction": "extraction", END: END})
        graph.add_edge("extraction", "duplicate")
        graph.add_edge("duplicate", "purchase_order")
        graph.add_edge("purchase_order", "goods_receipt")
        graph.add_edge("goods_receipt", "payment")
        graph.add_edge("payment", "discrepancy")
        graph.add_edge("discrepancy", "evidence")
        graph.add_edge("evidence", "financial")
        graph.add_edge("financial", "report")
        graph.add_edge("report", END)
        return graph.compile()

    # ------------------------------------------------------------------
    # Execution / persistence
    # ------------------------------------------------------------------
    def run(self, input_data: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        started = _now()
        started_perf = time.perf_counter()
        try:
            invoice_id = int(input_data.get("invoice_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invoice_id is required") from exc

        db = SessionLocal()
        try:
            invoice = db.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError("Invoice not found")
            audit_run = InvoiceAuditRun(invoice_id=invoice_id, status="running", started_at=started)
            db.add(audit_run)
            db.commit()
            db.refresh(audit_run)
            audit_run_id = audit_run.id
        finally:
            db.close()

        state: InvoiceWorkflowState = {
            "input": input_data,
            "invoice_id": invoice_id,
            "audit_run_id": audit_run_id,
            "timeline": [],
            "events": [],
            "status": "running",
            "errors": [],
            "outputs": {},
            "evidence": [],
            "missing_evidence": [],
            "require_human_review": False,
        }
        try:
            result = dict(self.graph.invoke(state))
        except Exception as exc:  # noqa: BLE001
            result = dict(state)
            result["status"] = "failed"
            result["errors"] = list(result.get("errors") or []) + [str(exc)]

        finished = _now()
        result["started_at"] = started.isoformat()
        result["finished_at"] = finished.isoformat()
        result["duration_ms"] = max(0, int(round((time.perf_counter() - started_perf) * 1000)))
        result.setdefault("timeline", [])
        result.setdefault("events", [])
        result.setdefault("errors", [])
        result.setdefault("evidence", [])
        result.setdefault("missing_evidence", [])
        result.setdefault("outputs", {})
        result["invoice_audit_run_id"] = audit_run_id
        if result.get("status") != "failed":
            # An invoice audit never authorises payment on its own; a reviewer must
            # validate evidence and record a decision even when no exception is found.
            result["status"] = "needs_human_review"
        result = json_safe(result)

        if persist:
            self._persist(input_data, result)
        return result

    def _persist(self, input_data: dict[str, Any], result: dict[str, Any]) -> None:
        db = SessionLocal()
        try:
            audit_run = db.get(InvoiceAuditRun, result["invoice_audit_run_id"])
            if not audit_run:
                result["persistence_error"] = "Invoice audit run disappeared before persistence."
                return
            outputs = result.get("outputs") or {}
            invoice = (outputs.get("document_intake") or {}).get("invoice") or {}
            report = outputs.get("report") or {}
            discrepancies = (outputs.get("financial_analysis") or {}).get("persisted_discrepancies") or []
            audit_run.status = "failed" if result.get("status") == "failed" else "needs_human_review"

            run = InvestigationRun(
                name=f"Invoice audit {invoice.get('invoice_number') or result.get('invoice_id')}",
                task_type="invoice_audit",
                status=audit_run.status,
                input_state=input_data,
                output_state=result,
                timeline=result.get("timeline"),
                events=result.get("events"),
                evidence=result.get("evidence"),
                errors=result.get("errors"),
                started_at=_parse_iso(result.get("started_at")),
                finished_at=_parse_iso(result.get("finished_at")),
                duration_ms=result.get("duration_ms"),
            )
            db.add(run)
            db.flush()
            result["run_id"] = run.id
            run.output_state = result

            audit_run.workflow_run_id = run.id
            audit_run.completed_at = _parse_iso(result.get("finished_at"))
            audit_run.duration_ms = result.get("duration_ms")
            audit_run.summary = (report.get("executive_summary") or "")[:2000]
            audit_run.structured_result = result
            audit_run.errors = result.get("errors")

            for item in result.get("evidence") or []:
                db.add(
                    InvoiceAuditEvidence(
                        audit_run_id=audit_run.id,
                        source_type=item.get("source_type") or "unknown",
                        source_id=item.get("source_id"),
                        source_reference=item.get("source_reference"),
                        description=item.get("excerpt") or item.get("kind") or "Evidence item",
                        evidence_metadata=json_safe(item),
                    )
                )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            result["persistence_error"] = f"{type(exc).__name__}: {exc}"
            result.setdefault("errors", []).append(f"Persistence failed: {type(exc).__name__}: {exc}")
        finally:
            db.close()
