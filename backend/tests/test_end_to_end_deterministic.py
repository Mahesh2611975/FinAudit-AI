"""Deterministic end-to-end workflow: import → reconcile → risk → case →
evidence → RAG → assistant → report export.

Every value asserted here comes from persisted records or deterministic rules.
The LLM is disabled so the run is reproducible without Ollama; the assistant
falls back to evidence-only narration and never fabricates a fact.
"""
import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-e2e-tests-")
os.environ.setdefault(
    "FINAUDIT_DATABASE_URL",
    f"sqlite:///{os.path.join(TEST_DB_DIR, 'e2e.db')}",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import llm, rag  # noqa: E402
from app.agents import assistant_graph as graph_module  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


TXN_CSV = (
    "transaction_id,date,type,vendor,amount,description\n"
    "TXN-D1,2040-06-01,debit,Duplicate Vendor,500.00,Same invoice\n"
    "TXN-D2,2040-06-01,debit,Duplicate Vendor,500.00,Same invoice\n"
    "TXN-C1,2040-06-03,credit,Client One,2500.00,Invoice payment\n"
)
BANK_CSV = (
    "Posting Date,Debit Amt,Credit Amt,UTR,Particulars\n"
    "2040-06-01,500.00,,REF-500,Duplicate Vendor\n"
    "2040-06-03,,2500.00,REF-2500,Client One\n"
)
LEDGER_CSV = (
    "Booking Date,Dr Value,Cr Value,Voucher,Narration\n"
    "2040-06-01,500.00,,REF-500,Duplicate Vendor\n"
    "2040-06-03,,2500.00,REF-2500,Client One\n"
)
BANK_MAPPING = '{"date":"Posting Date","debit":"Debit Amt","credit":"Credit Amt","reference":"UTR","description":"Particulars"}'
LEDGER_MAPPING = '{"date":"Booking Date","debit":"Dr Value","credit":"Cr Value","reference":"Voucher","description":"Narration"}'
POLICY_TEXT = (
    "Reviewer note: invoice INV-9001 for Duplicate Vendor was paid twice on "
    "2040-06-01 (TXN-D1 and TXN-D2). The second debit should be refunded after "
    "confirmation against the bank statement."
)


def build_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'e2e.db'}",
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


def test_deterministic_end_to_end_workflow(tmp_path, monkeypatch):
    client, factory, engine = build_client(tmp_path)
    monkeypatch.setattr(rag, "_store", VectorStore(tmp_path / "rag"))
    monkeypatch.setattr(graph_module, "SessionLocal", lambda: factory())
    monkeypatch.setattr(llm, "is_available", lambda: False)
    try:
        # 1. Import transactions (deterministic CSV normalization).
        upload = client.post(
            "/api/audits",
            files={"files": ("e2e-2040.csv", TXN_CSV.encode(), "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        audit = upload.json()
        assert audit["row_count"] >= 3

        dashboard = client.get("/api/dashboard/overview").json()
        assert dashboard["metrics"]["total_transactions"] == 3
        assert dashboard["scope"]["no_fake_data"] is True

        # 2. Reconciliation with independent column mappings.
        recon = client.post(
            "/api/reconciliations",
            files={
                "bank_file": ("bank.csv", BANK_CSV.encode(), "text/csv"),
                "ledger_file": ("ledger.csv", LEDGER_CSV.encode(), "text/csv"),
            },
            data={
                "date_tolerance_days": "0",
                "bank_mapping": BANK_MAPPING,
                "ledger_mapping": LEDGER_MAPPING,
            },
        )
        assert recon.status_code == 200, recon.text
        recon_data = recon.json()
        assert recon_data["summary"]["matched_count"] == 2
        assert recon_data["bank_mapping"]["date"] == "Posting Date"

        # 3. Deterministic risk analysis → at least one critical duplicate alert.
        analysis = client.post("/api/risk/analyze", json={})
        assert analysis.status_code == 200, analysis.text
        alerts = client.get("/api/risk/alerts?severity=critical").json()
        assert alerts["total"] >= 1
        alert = alerts["items"][0]
        assert alert["risk_category"] == "Duplicate Payment"

        # 4. Create an investigation case directly from that alert.
        case = client.post(
            f"/api/investigations/from-alert/{alert['id']}",
            json={"created_by": "e2e-reviewer", "assigned_to": "e2e-reviewer"},
        )
        assert case.status_code == 201, case.text
        case_data = case.json()
        case_ref = case_data["case_number"]
        assert case_data["risk_alert_id"] is not None

        # 5. Attach evidence to the case (source provenance required).
        evidence = client.post(
            f"/api/investigations/{case_ref}/evidence",
            json={
                "source_type": "bank_statement",
                "source_reference": "bank.csv:2",
                "excerpt": "Second debit of 500.00 on 2040-06-01 requires refund.",
                "added_by": "e2e-reviewer",
                "metadata": {"verified": False, "requires_human_review": True},
            },
        )
        assert evidence.status_code == 201, evidence.text

        note = client.post(
            f"/api/investigations/{case_ref}/notes",
            json={"author": "e2e-reviewer", "note": "Escalated for refund review."},
        )
        assert note.status_code == 201, note.text

        # 6. Ingest a document into RAG and retrieve grounded evidence.
        ingest = client.post(
            "/api/documents/ingest",
            files={"files": ("review.txt", POLICY_TEXT.encode(), "text/plain")},
        )
        assert ingest.status_code == 200, ingest.text
        status = client.get("/api/documents/status").json()
        assert status["status"] == "indexed"
        assert status["chunks"] > 0

        retrieval = client.post(
            "/api/documents/retrieval-test",
            json={"query": "INV-9001 duplicate payment refund", "top_k": 5},
        )
        assert retrieval.status_code == 200, retrieval.text
        retrieved = retrieval.json()
        assert retrieved["has_evidence"] is True
        assert any(
            item["source_filename"] == "review.txt" for item in retrieved["chunks"]
        )

        # 7. Assistant query: five roles, grounded citations, no LLM.
        answer = client.post(
            "/api/assistant/query",
            json={"question": "Generate an investigation report."},
        )
        assert answer.status_code == 200, answer.text
        answer_data = answer.json()
        assert set(answer_data["required_roles"]) == {
            "Financial Analyst Agent",
            "Risk Investigation Agent",
            "Reconciliation Agent",
            "Evidence Retrieval Agent",
            "Investigation Summary Agent",
        }
        assert "evidence" in answer_data["agents_selected"]
        assert answer_data["llm"]["fallback"] is True
        assert answer_data["answer"]
        assert answer_data["history_id"] is not None

        # 8. Report exports include provenance and the generated dataset.
        summary = client.get("/api/reports/summary").json()
        assert summary["generated_at"]
        assert summary["provenance"]
        assert summary["financial_summary"]["total_transactions"] == 3

        csv_report = client.get("/api/reports/full.csv")
        assert csv_report.status_code == 200
        csv_text = csv_report.content.decode("utf-8")
        assert "report_metadata" in csv_text
        assert "generated_at" in csv_text
        assert "TXN-D1" in csv_text

        pdf_report = client.get("/api/reports/full.pdf")
        assert pdf_report.status_code == 200
        assert pdf_report.headers["content-type"].startswith("application/pdf")

        txn_export = client.get("/api/transactions/export.csv").content.decode("utf-8")
        assert "provenance" in txn_export
        assert "TXN-D1" in txn_export

        risk_export = client.get(
            "/api/risk/alerts/export.csv?severity=critical"
        ).content.decode("utf-8")
        assert "risk_alert" in risk_export
        assert "Duplicate Vendor" in risk_export

        case_export = client.get(
            f"/api/investigations/{case_ref}/export.csv"
        ).content.decode("utf-8")
        assert case_ref in case_export
        assert "bank.csv:2" in case_export

        # 9. Final case detail confirms all work persisted together.
        detail = client.get(f"/api/investigations/{case_ref}").json()
        assert detail["alert"]["risk_category"] == "Duplicate Payment"
        assert len(detail["evidence"]) >= 3  # alert + transaction + manual
        assert len(detail["notes"]) >= 1
        assert any(
            event["action"] == "created_from_alert" for event in detail["history"]
        )
    finally:
        teardown(engine)
