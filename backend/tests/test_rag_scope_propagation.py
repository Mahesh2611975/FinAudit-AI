"""Stage 5 propagation tests for trusted RAG scopes.

These tests exercise the public API, the assistant/agent graphs, health and
status reporting, background scopes, client-controlled field rejection and
provenance preservation. Every RAG store used here is a temporary, migrated
store; the real RAG database and its backup are never touched.
"""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth as auth_module
from app import llm, rag
from app.agents import orchestrator as orchestrator_module
from app.agents.assistant_graph import AssistantGraph
from app.agents.invoice_workflow import InvoiceAuditOrchestrator
from app.agents.orchestrator import MultiAgentOrchestrator
from app.auth import rag_scope_for_user
from app.database import Base, get_db
from app.main import app
from app.ownership import (
    RAG_GLOBAL_READ_PERMISSION,
    RAG_READ,
    RAG_SCOPE_SOURCE_BACKGROUND,
    RAG_SCOPE_SOURCE_REQUEST,
    RAG_WRITE,
    RagAccessDenied,
    rag_scope_for_background,
    reset_data_scope,
    set_data_scope,
    validate_rag_scope,
)
from app.vector_store import VectorStore


USER_A = SimpleNamespace(
    id=41, username="stage5-user-a", email=None, full_name="Stage 5 User A",
    role="user", is_active=True,
)
USER_B = SimpleNamespace(
    id=42, username="stage5-user-b", email=None, full_name="Stage 5 User B",
    role="user", is_active=True,
)
ADMIN = SimpleNamespace(
    id=43, username="stage5-admin", email=None, full_name="Stage 5 Admin",
    role="admin", is_active=True,
)
LENDER = SimpleNamespace(
    id=44, username="stage5-lender", email=None, full_name="Stage 5 Lender",
    role="lender", is_active=True,
)

ALPHA_TEXT = "alphatoken alphamarker evidence belongs to account alpha."
BETA_TEXT = "betatoken betamarker evidence belongs to account beta."
PROVENANCE_TEXT = "provtoken provmarker evidence was reviewed on page seven for TXN-PROV."


@pytest.fixture
def stage5_client(tmp_path, monkeypatch, scoped_vector_store):
    """A temporary application database plus a migrated, scoped RAG store."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stage5.db'}",
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

    previous_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.rag._store", scoped_vector_store)
    monkeypatch.setattr(llm, "is_available", lambda: False)
    try:
        with TestClient(app) as client:
            yield client, factory, scoped_vector_store
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_db
        engine.dispose()


@contextmanager
def acting_as(user):
    """Temporarily authenticate API calls as ``user``, restoring the override."""
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


def ingest_document(client, user, filename: str, text: str) -> str:
    with acting_as(user):
        response = client.post(
            "/api/documents/ingest",
            files={"files": (filename, text.encode("utf-8"), "text/plain")},
        )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "ingested", result
    return result["doc_id"]


def test_document_management_is_owner_scoped_and_rejects_client_scope(stage5_client):
    client, _, _ = stage5_client
    doc_a = ingest_document(client, USER_A, "alpha.txt", ALPHA_TEXT)
    doc_b = ingest_document(client, USER_B, "bravo.txt", BETA_TEXT)

    with acting_as(USER_A):
        listed = client.get("/api/documents")
        assert listed.status_code == 200, listed.text
        documents = listed.json()["documents"]
        assert [item["doc_id"] for item in documents] == [doc_a]
        assert listed.json()["stats"]["documents"] == 1

        alpha = client.post(
            "/api/documents/retrieval-test",
            json={"query": "alphatoken alphamarker", "top_k": 5},
        )
        assert alpha.status_code == 200, alpha.text
        assert alpha.json()["has_evidence"] is True
        assert {item["owner_user_id"] for item in alpha.json()["chunks"]} == {USER_A.id}

        beta = client.post(
            "/api/documents/retrieval-test",
            json={"query": "betatoken betamarker", "top_k": 5},
        )
        assert beta.status_code == 200, beta.text
        assert beta.json()["has_evidence"] is False

        foreign_status = client.get(f"/api/documents/{doc_b}/status")
        foreign_delete = client.delete(f"/api/documents/{doc_b}")
        assert foreign_status.status_code == 404
        assert foreign_delete.status_code == 404

        own_status = client.get(f"/api/documents/{doc_a}/status")
        assert own_status.status_code == 200
        assert own_status.json()["doc_id"] == doc_a

        rejected = client.post(
            "/api/documents/retrieval-test",
            json={"query": "alphatoken", "global_access": True},
        )
        assert rejected.status_code == 400

    with acting_as(USER_B):
        documents = client.get("/api/documents").json()["documents"]
        assert [item["doc_id"] for item in documents] == [doc_b]
        foreign_status = client.get(f"/api/documents/{doc_a}/status")
        assert foreign_status.status_code == 404

    with acting_as(USER_B):
        deleted = client.delete(f"/api/documents/{doc_b}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] == doc_b


def test_assistant_and_agent_workflows_use_authenticated_scope(stage5_client, monkeypatch):
    client, _, _ = stage5_client
    ingest_document(client, USER_A, "assistant-alpha.txt", ALPHA_TEXT)
    ingest_document(client, USER_B, "assistant-bravo.txt", BETA_TEXT)

    with acting_as(USER_A):
        assistant = client.post(
            "/api/assistant/query",
            json={"question": "alphatoken alphamarker evidence"},
        )
    assert assistant.status_code == 200, assistant.text
    citations = assistant.json()["citations"]
    assert any(item.get("source_file") == "assistant-alpha.txt" for item in citations)
    assert all(item.get("source_file") != "assistant-bravo.txt" for item in citations)
    assert "rag_scope" not in assistant.json()

    with acting_as(USER_B):
        assistant = client.post(
            "/api/assistant/query",
            json={"question": "betatoken betamarker evidence"},
        )
    assert assistant.status_code == 200, assistant.text
    citations = assistant.json()["citations"]
    assert any(item.get("source_file") == "assistant-bravo.txt" for item in citations)
    assert all(item.get("source_file") != "assistant-alpha.txt" for item in citations)

    observed_scopes = []

    def scoped_retrieval(question, evidence="", *, scope, top_k=3):
        observed_scopes.append((scope.owner_user_id, scope.global_access, scope.source))
        return []

    monkeypatch.setattr(orchestrator_module, "retrieve_context", scoped_retrieval)
    for user in (USER_A, USER_B):
        with acting_as(user):
            response = client.post(
                "/api/agents/run",
                json={"task_type": "monthly_analysis", "year": 2041, "month": 1},
            )
        assert response.status_code == 200, response.text
        assert "rag_scope" not in response.json()

    assert observed_scopes == [
        (USER_A.id, False, RAG_SCOPE_SOURCE_REQUEST),
        (USER_B.id, False, RAG_SCOPE_SOURCE_REQUEST),
    ]

    with acting_as(USER_A):
        rejected = client.post(
            "/api/agents/run",
            json={
                "task_type": "monthly_analysis",
                "year": 2041,
                "month": 1,
                "scope": {"global_access": True},
            },
        )
    assert rejected.status_code == 400
    assert len(observed_scopes) == 2


def test_status_config_and_debug_permissions_are_explicit(stage5_client, monkeypatch):
    client, _, scoped_vector_store = stage5_client
    ingest_document(client, USER_A, "health-alpha.txt", ALPHA_TEXT)
    ingest_document(client, USER_B, "health-bravo.txt", BETA_TEXT)

    with acting_as(USER_A):
        health = client.get("/api/system/health")
        assert health.status_code == 200, health.text
        rag_status = health.json()["rag"]
        assert rag_status["visibility"] == "user"
        assert rag_status["documents"] == 1

        config = client.get("/api/assistant/config")
        assert config.status_code == 200, config.text
        assert config.json()["rag"]["visibility"] == "user"
        assert config.json()["rag"]["documents"] == 1

    with acting_as(LENDER):
        health = client.get("/api/system/health").json()["rag"]
        assert health["visibility"] == "user"
        assert health["documents"] == 0
        config = client.get("/api/assistant/config").json()["rag"]
        assert config["visibility"] == "user"
        assert config["documents"] == 0
        debug = client.post(
            "/api/documents/retrieval-test",
            json={"query": "alphatoken"},
        )
        assert debug.status_code == 403

    with acting_as(ADMIN):
        health = client.get("/api/system/health").json()["rag"]
        assert health["visibility"] == "global"
        assert health["documents"] == 2

    ordinary_scope = rag_scope_for_user(USER_A)
    with pytest.raises(RagAccessDenied):
        rag_scope_for_user(USER_A, global_access=True)

    # An unmigrated store must fail safe in status reporting, not expose counts.
    monkeypatch.setattr(
        "app.rag._store",
        VectorStore(scoped_vector_store.data_dir.parent / "unmigrated-rag"),
    )
    with acting_as(USER_A):
        health = client.get("/api/system/health").json()["rag"]
    assert health["status"] == "unavailable"
    assert health["documents"] == 0


def test_background_scopes_are_explicit_and_fail_closed(stage5_client):
    client, _, _ = stage5_client

    with pytest.raises(RagAccessDenied):
        rag_scope_for_background(None)
    with pytest.raises(RagAccessDenied):
        rag_scope_for_background(None, global_access=True)

    background = rag_scope_for_background(
        USER_A.id, audit_reason="background stage 5 test"
    )
    assert background.source == RAG_SCOPE_SOURCE_BACKGROUND
    with pytest.raises(RagAccessDenied):
        validate_rag_scope(background, expected_source=RAG_SCOPE_SOURCE_REQUEST)

    global_background = rag_scope_for_background(
        None,
        permissions={RAG_GLOBAL_READ_PERMISSION},
        global_access=True,
        audit_reason="background global status",
    )
    assert validate_rag_scope(global_background, operation=RAG_READ).global_access is True

    with pytest.raises(RagAccessDenied):
        AssistantGraph().run("alphatoken alphamarker")
    with pytest.raises(RagAccessDenied):
        MultiAgentOrchestrator().run(
            {"task_type": "monthly_analysis", "year": 2042, "month": 1},
            persist=False,
        )
    with pytest.raises(RagAccessDenied):
        InvoiceAuditOrchestrator().run({"invoice_id": 1}, persist=False)

    assistant_result = AssistantGraph().run(
        "alphatoken alphamarker evidence", scope=background
    )
    assert "rag_scope" not in assistant_result

    monkeypatch_session = MultiAgentOrchestrator()
    result = monkeypatch_session.run(
        {"task_type": "monthly_analysis", "year": 2042, "month": 1},
        scope=background,
        persist=False,
    )
    assert "rag_scope" not in result
    assert result["status"] in {"completed", "needs_human_review", "failed"}

    with pytest.raises(RagAccessDenied):
        MultiAgentOrchestrator().run(
            {"task_type": "monthly_analysis", "year": 2042, "month": 1, "owner_user_id": USER_B.id},
            scope=background,
            persist=False,
        )


def test_retrieval_provenance_is_preserved(stage5_client):
    client, _, scoped_vector_store = stage5_client
    scope = rag_scope_for_user(USER_A, audit_reason="provenance test")
    ingested = rag.ingest_text(
        PROVENANCE_TEXT,
        scope=scope,
        source_filename="provenance.txt",
        metadata={"page_number": 7, "transaction_id": "TXN-PROV"},
    )

    explanation = asyncio.run(
        llm.explain_finding("provtoken provmarker", "TXN-PROV", scope=scope)
    )
    evidence = explanation["retrieved_evidence"]
    assert any(
        item.get("doc_id") == ingested["doc_id"]
        and item.get("chunk_id")
        and item.get("source_filename") == "provenance.txt"
        and item.get("page_number") == 7
        and item.get("transaction_id") == "TXN-PROV"
        for item in evidence
    )
    assert explanation["retrieved_sources"]

    with acting_as(USER_A):
        response = client.post(
            "/api/assistant/query",
            json={"question": "provtoken provmarker evidence"},
        )
    assert response.status_code == 200, response.text
    citations = response.json()["citations"]
    assert any(
        item.get("source_file") == "provenance.txt"
        and item.get("page_number") == 7
        and item.get("transaction_id") == "TXN-PROV"
        for item in citations
    )
