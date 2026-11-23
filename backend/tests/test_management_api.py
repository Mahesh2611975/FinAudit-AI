"""Tests for system health, RAG management and the five-role assistant."""
import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-management-tests-")
os.environ.setdefault(
    "FINAUDIT_DATABASE_URL",
    f"sqlite:///{os.path.join(TEST_DB_DIR, 'management.db')}",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import llm, rag  # noqa: E402
from app.agents import assistant_graph as graph_module  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


def build_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'management.db'}",
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


def test_health_is_a_fast_database_backed_readiness_probe(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        response = client.get("/api/health")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"]["status"] == "connected"
        # Deliberately lightweight: no LLM/RAG probes that can stall the UI.
        assert "llm" not in data
        assert "rag" not in data
    finally:
        teardown(engine)


def test_system_health_reports_real_status_without_secrets(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        response = client.get("/api/system/health")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] in {"operational", "degraded"}
        assert data["database"]["status"] == "connected"
        assert data["database"]["backend"] == "sqlite"
        assert data["llm"]["provider"] == "ollama"
        assert isinstance(data["llm"]["model"], str)
        assert data["rag"]["backend"] == "local-tfidf-sqlite"
        assert data["configuration"]["authentication_enabled"] is True
        assert data["configuration"]["secrets_exposed"] is False
        body = response.text.lower()
        assert "groq_api_key" not in body
        assert "password" not in body
    finally:
        teardown(engine)


def test_rag_management_ingest_status_retrieval_delete_and_reindex(
    tmp_path, monkeypatch
):
    client, _, engine = build_client(tmp_path)
    monkeypatch.setattr(rag, "_store", VectorStore(tmp_path / "rag"))
    try:
        ingested = client.post(
            "/api/documents/ingest",
            files={"files": ("policy.txt", b"Invoice INV-7788 was paid twice on 2040-05-01.", "text/plain")},
        )
        assert ingested.status_code == 200, ingested.text
        doc = ingested.json()["results"][0]
        assert doc["status"] == "ingested"

        status = client.get("/api/documents/status").json()
        assert status["status"] == "indexed"
        assert status["documents"] == 1
        assert status["chunks"] > 0
        assert status["healthy"] is True

        doc_status = client.get(f"/api/documents/{doc['doc_id']}/status").json()
        assert doc_status["status"] == "indexed"
        assert doc_status["source_filename"] == "policy.txt"

        retrieved = client.post(
            "/api/documents/retrieval-test",
            json={"query": "INV-7788 duplicate payment", "top_k": 5},
        )
        assert retrieved.status_code == 200, retrieved.text
        data = retrieved.json()
        assert data["has_evidence"] is True
        assert data["chunks"]
        assert data["chunks"][0]["source_filename"] == "policy.txt"
        assert "chunk_id" in data["chunks"][0]

        reindexed = client.post("/api/documents/reindex")
        assert reindexed.status_code == 200
        assert reindexed.json()["stats"]["chunks"] > 0

        deleted = client.delete(f"/api/documents/{doc['doc_id']}")
        assert deleted.status_code == 200, deleted.text
        assert client.get("/api/documents/status").json()["status"] == "empty"
        assert client.get(f"/api/documents/{doc['doc_id']}/status").status_code == 404
    finally:
        teardown(engine)


REQUIRED = {
    "Financial Analyst Agent",
    "Risk Investigation Agent",
    "Reconciliation Agent",
    "Evidence Retrieval Agent",
    "Investigation Summary Agent",
}


def test_assistant_exposes_five_roles_and_persists_history(tmp_path, monkeypatch):
    client, factory, engine = build_client(tmp_path)
    monkeypatch.setattr(rag, "_store", VectorStore(tmp_path / "rag"))
    monkeypatch.setattr(graph_module, "SessionLocal", lambda: factory())
    monkeypatch.setattr(llm, "is_available", lambda: False)
    try:
        response = client.post(
            "/api/assistant/query",
            json={"question": "Generate an investigation report."},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert set(data["required_roles"]) == REQUIRED
        assert "reporting" in data["agents_selected"]
        assert "evidence" in data["agents_selected"]
        assert set(data["agent_roles"]) == REQUIRED
        assert data["agent_failures"] == []
        assert all(item["status"] == "completed" for item in data["timeline"])
        assert data["history_id"] is not None

        detail = client.get(f"/api/agents/runs/{data['history_id']}").json()
        assert detail["task_type"] == "assistant_query"
        assert detail["output_state"]["answer"] == data["answer"]
    finally:
        teardown(engine)


def test_assistant_records_individual_agent_failure(tmp_path, monkeypatch):
    client, factory, engine = build_client(tmp_path)
    monkeypatch.setattr(rag, "_store", VectorStore(tmp_path / "rag"))
    monkeypatch.setattr(graph_module, "SessionLocal", lambda: factory())
    monkeypatch.setattr(llm, "is_available", lambda: False)

    def failing_search(*args, **kwargs):
        raise RuntimeError("rag store offline")

    monkeypatch.setattr(rag, "search", failing_search)
    try:
        response = client.post(
            "/api/assistant/query",
            json={"question": "What evidence supports this alert?"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["agent_failures"]
        assert any(item["status"] == "failed" for item in data["timeline"])
        assert data["answer"]
    finally:
        teardown(engine)
