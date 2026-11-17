"""End-to-end coverage for the dashboard, transaction import, RAG and assistant."""
import os
import tempfile
from pathlib import Path

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-platform-tests-")
os.environ.setdefault("FINAUDIT_DATABASE_URL", f"sqlite:///{os.path.join(TEST_DB_DIR, 'platform.db')}")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import llm, rag  # noqa: E402
from app.agents import assistant_graph as graph_module  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import InvestigationRun, TransactionRecord  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402

CSV_TEXT = (
    "transaction_id,date,type,vendor,amount,description\n"
    "TXN-A,2040-01-01,debit,Acme Supplies,100.00,Office supplies\n"
    "TXN-B,2040-01-01,debit,Acme Supplies,100.00,Office supplies\n"
    "TXN-C,2040-01-03,credit,Employer,5000.00,Salary credit\n"
)


def build_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'platform.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), session_factory, engine


def teardown_client(engine):
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def test_dashboard_is_database_backed_and_has_five_charts(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        upload = client.post("/api/audits", files={"files": ("jan-2040.csv", CSV_TEXT.encode(), "text/csv")})
        assert upload.status_code == 200, upload.text
        analysis = client.post("/api/risk/analyze", json={})
        assert analysis.status_code == 200, analysis.text

        response = client.get("/api/dashboard/overview")
        assert response.status_code == 200
        payload = response.json()
        metrics = payload["metrics"]
        assert metrics["total_transactions"] == 3
        assert metrics["total_spending"] == 200.0
        assert metrics["total_received"] == 5000.0
        assert metrics["net_cash_flow"] == 4800.0
        assert metrics["total_risk_alerts"] > 0
        assert len(payload["charts"]) == 5
        assert set(payload["charts"]) == {
            "transaction_activity", "spending_trends", "risk_distribution",
            "reconciliation_status", "category_distribution",
        }
        assert payload["scope"]["no_fake_data"] is True
        assert any(item["reference"] == "TXN-A" for item in payload["recent_activity"])
    finally:
        teardown_client(engine)


def test_transaction_preview_and_duplicate_import_prevention(tmp_path):
    client, session_factory, engine = build_client(tmp_path)
    try:
        first = client.post("/api/audits", files={"files": ("preview-2040.csv", CSV_TEXT.encode(), "text/csv")})
        assert first.status_code == 200, first.text

        preview = client.post(
            "/api/transactions/preview",
            files={"file": ("preview-2040.csv", CSV_TEXT.encode(), "text/csv")},
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["valid"] is True
        assert data["row_count"] == 3
        assert data["duplicate_count"] == 3
        assert any(item["source"] == "transaction_id" and item["canonical"] == "transaction_id" for item in data["mapping"])
        assert all(item["duplicate"] for item in data["preview"])

        duplicate_import = client.post(
            "/api/audits",
            files={"files": ("preview-2040.csv", CSV_TEXT.encode(), "text/csv")},
            data={"skip_duplicates": "true"},
        )
        assert duplicate_import.status_code == 200, duplicate_import.text
        assert duplicate_import.json()["skipped_duplicates"] == 3
        db = session_factory()
        try:
            assert db.query(TransactionRecord).count() == 3
        finally:
            db.close()

        filtered = client.get("/api/transactions?q=TXN-A&date_from=2040-01-01&min_amount=50")
        assert filtered.status_code == 200
        result = filtered.json()
        assert result["total"] == 1
        assert result["items"][0]["source_file"] == "preview-2040.csv"
        assert result["items"][0]["source_row"] is not None

        detail = client.get("/api/transactions/TXN-A")
        assert detail.status_code == 200
        assert detail.json()["transaction"]["vendor"] == "Acme Supplies"
    finally:
        teardown_client(engine)


def test_rag_ingestion_preserves_metadata_and_retrieves_context(tmp_path, monkeypatch):
    monkeypatch.setattr("app.rag._store", VectorStore(tmp_path / "rag"))
    text = (
        "Investigation note for TXN-A. The reviewer verified the purchase order and invoice. "
        "The duplicate-looking payment was a legitimate recurring supplier payment."
    )
    ingested = rag.ingest_text(
        text,
        source_filename="review-notes.txt",
        document_type="investigation_report",
        metadata={"transaction_id": "TXN-A", "page_number": 1},
    )
    assert ingested["status"] == "ingested"
    assert ingested["chunks"] == 1

    results = rag.search("TXN-A reviewer verified invoice", top_k=3)
    assert results
    assert results[0]["source_filename"] == "review-notes.txt"
    assert results[0]["transaction_id"] == "TXN-A"
    assert results[0]["page_number"] == 1

    contexts = rag.retrieve_context("TXN-A", "amount=100.00")
    assert any(item["source"] == "STRUCTURED-EVIDENCE" for item in contexts)
    docs = rag.list_documents()
    assert docs[0]["document_type"] == "investigation_report"


def test_assistant_graph_selects_agents_and_returns_citations(tmp_path, monkeypatch):
    client, session_factory, engine = build_client(tmp_path)
    monkeypatch.setattr("app.rag._store", VectorStore(tmp_path / "rag"))
    monkeypatch.setattr(graph_module, "SessionLocal", lambda: session_factory())
    monkeypatch.setattr(llm, "is_available", lambda: False)
    try:
        upload = client.post("/api/audits", files={"files": ("assistant-2040.csv", CSV_TEXT.encode(), "text/csv")})
        assert upload.status_code == 200, upload.text
        client.post("/api/risk/analyze", json={})
        rag.ingest_text(
            "TXN-A was reviewed against the original invoice and purchase order.",
            source_filename="txn-a-review.txt",
            metadata={"transaction_id": "TXN-A"},
        )

        response = client.post("/api/assistant/query", json={"question": "Explain transaction TXN-A and its risk evidence."})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["intent"] == "transaction"
        assert "transaction" in data["agents_selected"]
        assert "reconciliation" not in data["agents_selected"]
        assert "reporting" in data["agents_selected"]
        assert data["sufficiency"] in {"partial", "sufficient"}
        assert any("TXN-A" in str(item.get("source")) for item in data["citations"])
        assert any(item.get("source_filename") == "txn-a-review.txt" for item in data["document_evidence"])
        assert "not proof of fraud" in data["disclaimer"].lower() or "no financial action" in data["disclaimer"].lower()
        assert len(data["timeline"]) >= 3
    finally:
        teardown_client(engine)


def test_report_summary_and_openapi_expose_new_workflows(tmp_path):
    client, session_factory, engine = build_client(tmp_path)
    try:
        with session_factory() as db:
            db.add(
                InvestigationRun(
                    name="investigation",
                    task_type="investigation",
                    status="needs_human_review",
                    output_state={
                        "outputs": {
                            "report": {
                                "output": {
                                    "ai_narrative": "Grounded narrative for human review.",
                                    "conclusion_status": "requires_human_review",
                                    "verified_facts": ["Transaction TXN-A was imported."],
                                    "evidence_references": [{"source_file": "jan-2040.csv"}],
                                }
                            }
                        }
                    },
                )
            )
            db.commit()

        response = client.get("/api/reports/summary")
        assert response.status_code == 200
        assert "financial_summary" in response.json()
        assert "scope" in response.json()
        explanations = response.json()["ai_explanations"]
        assert explanations[0]["ai_narrative"] == "Grounded narrative for human review."
        assert explanations[0]["evidence_references"][0]["source_file"] == "jan-2040.csv"

        spec = client.get("/openapi.json")
        assert spec.status_code == 200
        paths = spec.json()["paths"]
        for path in (
            "/api/dashboard/overview",
            "/api/transactions/preview",
            "/api/transactions/{transaction_ref}",
            "/api/assistant/query",
            "/api/documents/ingest",
            "/api/reports/summary",
        ):
            assert path in paths
    finally:
        teardown_client(engine)
