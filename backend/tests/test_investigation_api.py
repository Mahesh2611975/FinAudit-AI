"""Deterministic tests for the investigation case workflow."""
import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-investigation-tests-")
os.environ.setdefault("FINAUDIT_DATABASE_URL", f"sqlite:///{os.path.join(TEST_DB_DIR, 'investigations.db')}")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import investigation_api  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import RiskAlert, TransactionRecord  # noqa: E402


def build_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'investigations.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), factory, engine


def teardown(engine):
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def seed_alert_and_transaction(factory):
    with factory() as db:
        db.add(
            TransactionRecord(
                transaction_id="TXN-INV-1",
                date="2041-04-01",
                amount=12500.0,
                signed_amount=-12500.0,
                direction="debit",
                vendor="Acme Supplies",
                description="Duplicate payment review",
                source_file="bank-apr-2041.csv",
                source_row=7,
            )
        )
        db.add(
            RiskAlert(
                alert_code="RA-TEST-0001",
                fingerprint="investigation-test-fingerprint",
                source_type="transaction",
                transaction_id="TXN-INV-1",
                vendor_name="Acme Supplies",
                risk_category="duplicate_payment",
                severity="high",
                risk_score=82,
                amount_value=12500.0,
                explanation="Two payments share vendor, amount and date.",
                evidence={
                    "rule": "duplicate_payment",
                    "source_file": "bank-apr-2041.csv",
                    "source_row": 7,
                },
            )
        )
        db.commit()


async def fake_explain(question, evidence=""):
    return {
        "answer": "Grounded explanation for human review.",
        "model": "test-model",
        "retrieved_sources": [],
        "fallback": False,
    }


def test_create_from_alert_notes_status_and_export(tmp_path, monkeypatch):
    client, factory, engine = build_client(tmp_path)
    monkeypatch.setattr(investigation_api, "explain_finding", fake_explain)
    try:
        seed_alert_and_transaction(factory)

        created = client.post(
            "/api/investigations/from-alert/RA-TEST-0001",
            json={"created_by": "qa.engineer"},
        )
        assert created.status_code == 201, created.text
        case = created.json()
        assert case["case_number"].startswith("INV-")
        assert case["status"] == "open"
        assert case["risk_alert_id"] is not None
        assert len(case["evidence"]) >= 2
        assert any(item["source_type"] == "risk_alert" for item in case["evidence"])
        assert any(item["source_type"] == "transaction_record" for item in case["evidence"])

        alert = client.get("/api/risk/alerts/RA-TEST-0001").json()
        assert alert["review_status"] == "investigating"

        updated = client.patch(
            f"/api/investigations/{case['case_number']}",
            json={
                "status": "under_review",
                "assigned_to": "qa.engineer",
                "reviewer_notes": "Checking original invoice.",
                "actor": "qa.engineer",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "under_review"
        assert updated.json()["assigned_to"] == "qa.engineer"
        assert any(item["action"] == "status_change" for item in updated.json()["history"])

        note = client.post(
            f"/api/investigations/{case['case_number']}/notes",
            json={"author": "reviewer.one", "note": "Invoice copy requested."},
        )
        assert note.status_code == 201, note.text

        explained = client.post(f"/api/investigations/{case['case_number']}/explain")
        assert explained.status_code == 200, explained.text
        assert explained.json()["explanation"] == "Grounded explanation for human review."

        detail = client.get(f"/api/investigations/{case['case_number']}").json()
        assert len(detail["notes"]) == 2
        assert detail["transactions"][0]["transaction_id"] == "TXN-INV-1"
        assert detail["ai_explanation"] == "Grounded explanation for human review."
        assert any(item["action"] == "ai_explanation_generated" for item in detail["history"])

        exported = client.get(f"/api/investigations/{case['case_number']}/export.csv")
        assert exported.status_code == 200
        assert case["case_number"].encode() in exported.content
        assert b"TXN-INV-1" in exported.content

        resolved = client.patch(
            f"/api/investigations/{case['case_number']}",
            json={"status": "resolved", "resolution": "Source invoice verified.", "actor": "reviewer.one"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"] is not None
    finally:
        teardown(engine)


def test_case_validation_and_listing(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        bad = client.post("/api/investigations", json={"title": "X", "status": "unknown"})
        assert bad.status_code == 422

        created = client.post(
            "/api/investigations",
            json={"title": "Manual review", "severity": "low", "created_by": "qa"},
        )
        assert created.status_code == 201, created.text
        listed = client.get("/api/investigations").json()
        assert listed["total"] == 1
        assert listed["items"][0]["case_number"] == created.json()["case_number"]
    finally:
        teardown(engine)
