"""Tests for the local Ollama-backed, grounded explanation layer."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app import llm
from app.auth import rag_scope_for_user
from app.ownership import RagAccessDenied
from app.rag import POLICIES, retrieve_context


SCOPE = rag_scope_for_user(
    SimpleNamespace(id=101, username="llm-user", role="user", is_active=True),
    audit_reason="llm tests",
)


def run_explain(question="Possible duplicate payment to Acme", evidence="Vendor=Acme; amount=120.00"):
    return asyncio.run(llm.explain_finding(question, evidence, scope=SCOPE))


def test_explanation_and_prompt_require_a_trusted_scope():
    with pytest.raises(RagAccessDenied):
        asyncio.run(llm.explain_finding("question", "evidence"))
    with pytest.raises(RagAccessDenied):
        llm.build_prompt("question", "evidence", contexts=CONTEXTS)


CONTEXTS = [
    {
        "source": "POLICY-DUP-01",
        "text": "Duplicate payment review: compare vendor, date, amount and reference.",
        "score": 0.42,
    },
    {
        "source": "STRUCTURED-EVIDENCE",
        "text": "Vendor=Acme; amount=120.00",
        "score": 0.999,
    },
]


class FakeResponse:
    def __init__(self, content="The payment [POLICY-DUP-01] needs review.", status_code=200):
        self._content = content
        self.status_code = status_code
        self.payload = {
            "message": {"role": "assistant", "content": self._content},
        }

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("provider failure", request=httpx.Request("POST", "http://localhost"), response=httpx.Response(self.status_code))

    def json(self):
        return self.payload


def test_ollama_configuration_uses_local_host_and_needs_no_api_key(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "phi3:mini")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:12345/")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert llm.get_model() == "phi3:mini"
    assert llm.get_host() == "http://127.0.0.1:12345"
    assert llm.get_api_key() is None
    assert llm.create_client() is None


def test_is_available_checks_local_ollama_tags(monkeypatch):
    monkeypatch.setattr(llm, "get_host", lambda: "http://ollama.local:11434")
    monkeypatch.setattr(
        llm.httpx,
        "get",
        lambda url, timeout: SimpleNamespace(status_code=200 if url.endswith("/api/tags") else 500),
    )
    assert llm.is_available() is True

    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(llm.httpx, "get", fail)
    assert llm.is_available() is False


def test_generate_posts_grounded_messages_to_ollama(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(llm, "get_model", lambda: "local-test-model")
    monkeypatch.setattr(llm, "get_host", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(llm.httpx, "post", fake_post)

    answer = llm.generate("Explain the finding", system="Cautious assistant", max_tokens=123)

    assert answer == "The payment [POLICY-DUP-01] needs review."
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["json"]["model"] == "local-test-model"
    assert captured["json"]["options"]["num_predict"] == 123
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "Cautious assistant"},
        {"role": "user", "content": "Explain the finding"},
    ]


def test_generate_returns_none_when_ollama_request_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(llm.httpx, "post", fail)
    assert llm.generate("Explain") is None


def test_unavailable_ollama_returns_evidence_only_fallback(monkeypatch):
    monkeypatch.setattr(llm, "retrieve_context", lambda *args, **kwargs: CONTEXTS)
    monkeypatch.setattr(llm, "is_available", lambda: False)

    result = run_explain()

    assert result["fallback"] is True
    assert result["error_category"] == "ollama_unavailable"
    assert "Ollama is not running" in result["answer"]
    assert result["retrieved_sources"] == [
        {"source": "POLICY-DUP-01", "score": 0.42},
        {"source": "STRUCTURED-EVIDENCE", "score": 0.999},
    ]
    assert result["retrieved_evidence"] == CONTEXTS
    assert result["model"] == llm.MODEL


def test_successful_ollama_response_is_grounded_in_retrieved_context(monkeypatch):
    captured = {}

    async def fake_agenerate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "The payment [POLICY-DUP-01] needs review."

    monkeypatch.setattr(llm, "retrieve_context", lambda *args, **kwargs: CONTEXTS)
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "agenerate", fake_agenerate)

    result = run_explain()

    assert result["fallback"] is False
    assert result["answer"] == "The payment [POLICY-DUP-01] needs review."
    assert result["model"] == llm.MODEL
    assert all(set(item) == {"source", "score"} for item in result["retrieved_sources"])
    assert result["retrieved_evidence"] == CONTEXTS
    assert "POLICY-DUP-01" in captured["prompt"]
    assert "Vendor=Acme" in captured["prompt"]
    assert "Never invent or guess transaction IDs" in captured["prompt"]
    assert "proof of fraud" in captured["kwargs"]["system"]


def test_empty_model_response_returns_safe_fallback(monkeypatch):
    monkeypatch.setattr(llm, "retrieve_context", lambda *args, **kwargs: CONTEXTS)
    monkeypatch.setattr(llm, "is_available", lambda: True)

    async def empty(*args, **kwargs):
        return None

    monkeypatch.setattr(llm, "agenerate", empty)
    result = run_explain()

    assert result["fallback"] is True
    assert result["error_category"] == "empty_response"
    assert "empty response" in result["answer"].lower()


def test_retrieval_returns_known_source_metadata_with_empty_store(monkeypatch, scoped_vector_store):
    monkeypatch.setattr("app.rag._store", scoped_vector_store)
    contexts = retrieve_context(
        "Possible duplicate payment to Acme",
        "Vendor=Acme; amount=120.00",
        scope=SCOPE,
    )
    source_ids = {item["source"] for item in contexts}
    allowed_ids = {item["source"] for item in POLICIES} | {"STRUCTURED-EVIDENCE"}

    assert "POLICY-DUP-01" in source_ids
    assert "STRUCTURED-EVIDENCE" in source_ids
    assert source_ids <= allowed_ids
    assert all(set(item) == {"source", "text", "score"} for item in contexts)
    assert all(0 <= item["score"] <= 1 for item in contexts)


def test_irrelevant_or_empty_evidence_retrieval_is_safe(monkeypatch, scoped_vector_store):
    monkeypatch.setattr("app.rag._store", scoped_vector_store)
    assert retrieve_context("zzzxq plugh", scope=SCOPE) == []

    contexts = retrieve_context("duplicate payment", "", scope=SCOPE)
    assert all(item["source"] != "STRUCTURED-EVIDENCE" for item in contexts)
