"""Stage 8 tests for deterministic, owner-scoped vendor intelligence.

Every RAG store used here is temporary and migrated for ownership; the real
RAG database and its backup are never touched.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app import rag as rag_module
from app.auth import rag_scope_for_user
from app.agents.assistant_graph import AGENT_VENDOR, AssistantGraph
from app.agents.orchestrator import AGENT_VENDOR as ORCH_AGENT_VENDOR
from app.agents.orchestrator import MultiAgentOrchestrator
from app.agents.vendor_intelligence import (
    VendorIntelligenceAgent,
    VendorIntelligenceError,
)
from app.database import SessionLocal
from app.main import app
from app.models import (
    Invoice,
    InvoiceDiscrepancy,
    InvoicePayment,
    InvestigationRun,
    TransactionRecord,
    User,
    Vendor,
)
from app.ownership import reset_data_scope, set_data_scope
from app.rag import ingest_text


@pytest.fixture(autouse=True)
def _temporary_rag_store(monkeypatch, scoped_vector_store):
    monkeypatch.setattr(rag_module, "_store", scoped_vector_store)
    return scoped_vector_store


def uid() -> str:
    return uuid.uuid4().hex[:10]


@contextmanager
def acting_as(user):
    """Authenticate endpoint calls as ``user`` with a real data scope."""
    dependency = auth_module.get_current_active_user
    previous = app.dependency_overrides.get(dependency)

    async def _current_user():
        token = set_data_scope(user)
        try:
            yield user
        finally:
            reset_data_scope(token)

    app.dependency_overrides[dependency] = _current_user
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(dependency, None)
        else:
            app.dependency_overrides[dependency] = previous


def create_user(role: str):
    db = SessionLocal()
    try:
        suffix = uid()
        user = User(
            username=f"vendor-agent-{suffix}",
            email=f"vendor-agent-{suffix}@example.test",
            hashed_password="test-only-hash",
            full_name=f"Vendor Agent {suffix}",
            role=role,
            is_active=True,
            is_provisioned=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def scope_for(user):
    return rag_scope_for_user(user, audit_reason="vendor intelligence tests")


def create_vendor(name: str, owner_id: int) -> int:
    db = SessionLocal()
    try:
        vendor = Vendor(
            vendor_name=name,
            vendor_code=f"VA-{uid()}",
            currency="INR",
            owner_user_id=owner_id,
        )
        db.add(vendor)
        db.commit()
        db.refresh(vendor)
        return vendor.id
    finally:
        db.close()


def create_invoice(
    vendor_id: int,
    owner_id: int,
    *,
    number: str,
    total_minor: int,
    paid_minor: int = 0,
) -> int:
    db = SessionLocal()
    try:
        invoice = Invoice(
            vendor_id=vendor_id,
            invoice_number=number,
            invoice_date="2041-05-01",
            due_date="2041-05-31",
            currency="INR",
            subtotal_minor=total_minor,
            tax_minor=0,
            total_minor=total_minor,
            paid_minor=paid_minor,
            outstanding_minor=total_minor - paid_minor,
            status="received",
            extraction_status="complete",
            source_filename=f"{number}.pdf",
            owner_user_id=owner_id,
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        return invoice.id
    finally:
        db.close()


def create_discrepancy(
    invoice_id: int,
    owner_id: int,
    *,
    kind: str,
    severity: str,
    difference_minor: int,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            InvoiceDiscrepancy(
                invoice_id=invoice_id,
                discrepancy_type=kind,
                severity=severity,
                expected_value="Matched value",
                actual_value="Different value",
                difference_minor=difference_minor,
                currency="INR",
                explanation=f"{kind} requires human verification.",
                status="open",
                owner_user_id=owner_id,
            )
        )
        db.commit()
    finally:
        db.close()


def create_payment(invoice_id: int, amount_minor: int) -> None:
    db = SessionLocal()
    try:
        db.add(
            InvoicePayment(
                invoice_id=invoice_id,
                payment_date="2041-05-05",
                amount_minor=amount_minor,
                currency="INR",
                payment_reference=f"PAY-{uid()}",
                payment_method="bank_transfer",
                status="verified",
            )
        )
        db.commit()
    finally:
        db.close()


def create_transactions(vendor_name: str, owner_id: int, count: int = 1) -> None:
    db = SessionLocal()
    try:
        base = date(2041, 5, 1)
        for index in range(count):
            db.add(
                TransactionRecord(
                    transaction_id=f"TXN-{uid()}-{index}",
                    date=(base + timedelta(days=index)).isoformat(),
                    amount=250000.0,
                    signed_amount=-250000.0,
                    direction="debit",
                    type="vendor_payment",
                    vendor=vendor_name,
                    description="Contracted vendor payment",
                    currency="INR",
                    owner_user_id=owner_id,
                )
            )
        db.commit()
    finally:
        db.close()


def test_vendor_intelligence_normal_records_are_factual_and_scoped():
    user = create_user("user")
    scope = scope_for(user)
    name = f"Normal Vendor {uid()}"
    vendor_id = create_vendor(name, user.id)
    invoice_id = create_invoice(vendor_id, user.id, number=f"INV-{uid()}", total_minor=500000, paid_minor=200000)
    create_payment(invoice_id, 200000)
    create_transactions(name, user.id, count=1)

    result = VendorIntelligenceAgent().run(vendor_id, scope=scope, persist=False)

    assert result["status"] != "failed"
    assert result["task_type"] == "vendor_intelligence"
    assert result["vendor_id"] == vendor_id
    assert result["vendor_profile"]["vendor_name"] == name
    assert result["financial_exposure"]["total_invoiced_minor"] == 500000
    assert result["financial_exposure"]["outstanding_minor"] == 300000
    assert len(result["invoice_history"]) == 1
    assert result["verified_database_facts"]
    assert result["calculated_metrics"]["financial_exposure"]["currency"] == "INR"
    assert result["retrieved_evidence"] == [] or all(
        "owner_user_id" not in item for item in result["retrieved_evidence"]
    )
    assert result["llm"] == {"used": False, "backend": "deterministic_rules"}
    assert "no overall vendor risk score" in result["disclaimer"].lower()


def test_vendor_intelligence_suspicious_patterns_are_rule_derived():
    user = create_user("user")
    scope = scope_for(user)
    name = f"Suspicious Vendor {uid()}"
    vendor_id = create_vendor(name, user.id)
    number = f"DUP-{uid()}"
    first = create_invoice(vendor_id, user.id, number=number, total_minor=25000000)
    second = create_invoice(vendor_id, user.id, number=number, total_minor=25000000)
    create_discrepancy(first, user.id, kind="total_mismatch", severity="high", difference_minor=15000000)
    create_discrepancy(second, user.id, kind="total_mismatch", severity="high", difference_minor=15000000)
    create_discrepancy(first, user.id, kind="goods_not_received", severity="medium", difference_minor=25000000)
    create_transactions(name, user.id, count=3)

    result = VendorIntelligenceAgent().run(vendor_id, scope=scope, persist=False)

    rules = {item["rule"] for item in result["risk_indicators"]}
    assert "Suspicious Vendor Payment Pattern" in rules
    assert any(
        item["rule"] == "Suspicious Vendor Payment Pattern"
        and item["rule_score"]
        and item["explanation"]
        and item["source_name"] == "suspicious_vendor_payment_pattern"
        for item in result["risk_indicators"]
    )
    assert result["duplicate_invoice_patterns"]["per_invoice_matches"]
    assert result["invoice_po_mismatches"]["po_discrepancies"]
    assert len(result["invoice_discrepancies"]) == 3
    assert result["financial_exposure"]["open_discrepancy_exposure_minor"] == 55000000
    assert result["conclusion_status"] == "requires_human_review"


def test_vendor_intelligence_without_transactions_reports_missing_evidence():
    user = create_user("user")
    scope = scope_for(user)
    vendor_id = create_vendor(f"No Transactions {uid()}", user.id)

    result = VendorIntelligenceAgent().run(vendor_id, scope=scope, persist=False)

    assert result["status"] != "failed"
    assert result["transaction_history"]["summary"]["count"] == 0
    assert "No transaction records matched this vendor's name or code." in result["missing_evidence"]
    assert result["conclusion_status"] == "insufficient_evidence"


@pytest.mark.parametrize("vendor_id", [0, -1, "abc", None, True])
def test_vendor_intelligence_rejects_invalid_ids(vendor_id):
    user = create_user("user")
    with pytest.raises(VendorIntelligenceError):
        VendorIntelligenceAgent().run(vendor_id, scope=scope_for(user), persist=False)


def test_cross_user_vendor_is_denied():
    owner = create_user("user")
    other = create_user("user")
    vendor_id = create_vendor(f"Private Vendor {uid()}", owner.id)

    with pytest.raises(VendorIntelligenceError) as exc:
        VendorIntelligenceAgent().run(
            vendor_id, scope=scope_for(other), persist=False
        )
    assert "not accessible" in str(exc.value)

    client = TestClient(app)
    with acting_as(other):
        response = client.post(f"/api/vendors/{vendor_id}/investigate")
    assert response.status_code == 404


def test_lender_cannot_call_vendor_investigation_endpoint():
    lender = create_user("lender")
    owner = create_user("user")
    vendor_id = create_vendor(f"Lender Denied {uid()}", owner.id)
    client = TestClient(app)

    with acting_as(lender):
        response = client.post(f"/api/vendors/{vendor_id}/investigate")

    assert response.status_code == 403


def test_rag_evidence_is_owner_scoped_and_preserves_provenance():
    owner = create_user("user")
    other = create_user("user")
    owner_scope = scope_for(owner)
    other_scope = scope_for(other)
    name = f"RAG Vendor {uid()}"
    vendor_id = create_vendor(name, owner.id)

    ingest_text(
        f"Vendor {name} contract approved. Unique owner marker {uid()}.",
        scope=owner_scope,
        source_filename="owner-vendor-contract.txt",
        document_type="contract",
        metadata={"page_number": 4, "transaction_id": "TXN-OWNER-EVIDENCE"},
    )
    ingest_text(
        f"Vendor {name} unrelated file from another account.",
        scope=other_scope,
        source_filename="other-vendor-contract.txt",
        document_type="contract",
        metadata={"page_number": 9, "transaction_id": "TXN-OTHER-EVIDENCE"},
    )

    result = VendorIntelligenceAgent().run(vendor_id, scope=owner_scope, persist=False)

    evidence = result["retrieved_evidence"]
    assert evidence
    assert {item["source_filename"] for item in evidence} == {"owner-vendor-contract.txt"}
    assert all(
        key in item
        for item in evidence
        for key in ("doc_id", "chunk_id", "source_filename", "page_number", "transaction_id", "score")
    )
    assert all("owner_user_id" not in item for item in evidence)
    assert all(item["page_number"] == 4 for item in evidence)
    assert all(item["transaction_id"] == "TXN-OWNER-EVIDENCE" for item in evidence)


def test_vendor_investigation_persists_investigation_run():
    user = create_user("user")
    scope = scope_for(user)
    vendor_id = create_vendor(f"Persist Vendor {uid()}", user.id)
    result = VendorIntelligenceAgent().run(vendor_id, scope=scope, persist=True)

    assert result["run_id"]
    db = SessionLocal()
    try:
        run = db.query(InvestigationRun).filter(InvestigationRun.id == result["run_id"]).first()
        assert run is not None
        assert run.task_type == "vendor_intelligence"
        assert run.input_state == {"vendor_id": vendor_id}
        assert run.output_state["vendor_id"] == vendor_id
        assert "rag_scope" not in run.output_state
        assert run.owner_user_id == user.id
        assert run.timeline
        assert run.evidence == result["evidence"]
    finally:
        db.close()


def test_assistant_and_orchestrator_route_vendor_tasks():
    user = create_user("user")
    scope = scope_for(user)
    name = f"Assistant Vendor {uid()}"
    vendor_id = create_vendor(name, user.id)

    answer = AssistantGraph().run(
        f"Investigate vendor {name}", scope=scope, vendor_id=vendor_id
    )
    assert answer["intent"] == "vendor"
    assert "vendor" in answer["route"]
    assert AGENT_VENDOR in answer["agent_roles"]
    assert answer["agent_outputs"][AGENT_VENDOR]["data"]["vendor_id"] == vendor_id
    assert len(answer["required_roles"]) == 5

    workflow = MultiAgentOrchestrator().run(
        {"task_type": "vendor_intelligence", "vendor_id": vendor_id},
        scope=scope,
        persist=False,
    )
    assert ORCH_AGENT_VENDOR in [item["agent_name"] for item in workflow["timeline"]]
    assert workflow["outputs"]["report"]["vendor_intelligence"]["vendor_id"] == vendor_id


def test_vendor_investigation_endpoint_failure_is_transparent(monkeypatch):
    user = create_user("user")
    vendor_id = create_vendor(f"Failure Vendor {uid()}", user.id)

    def _fail(self, vendor_id, *, scope, persist=True):
        raise RuntimeError("simulated vendor workflow failure")

    monkeypatch.setattr("app.invoice_api.VendorIntelligenceAgent.run", _fail)
    client = TestClient(app)

    with acting_as(user):
        response = client.post(f"/api/vendors/{vendor_id}/investigate")

    assert response.status_code == 502
    assert "simulated vendor workflow failure" in response.text
