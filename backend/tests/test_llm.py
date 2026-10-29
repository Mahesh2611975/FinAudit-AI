import asyncio
from types import SimpleNamespace

import groq
import httpx
import pytest

from app import llm
from app.rag import POLICIES, retrieve_context


def run_explain(question="Possible duplicate payment to Acme", evidence="Vendor=Acme; amount=120.00"):
    return asyncio.run(llm.explain_finding(question, evidence))


def fake_client(content="The payment [POLICY-DUP-01] needs review.", model="mock-model"):
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model=model,
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, captured


def http_error(error_type, status_code, request):
    return error_type(
        "provider failure",
        response=httpx.Response(status_code, request=request),
        body={"error": "provider failure"},
    )


def test_missing_api_key_returns_safe_configuration_fallback(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert llm.get_api_key() is None
    assert llm.create_client() is None

    monkeypatch.setattr(llm, "client", None)
    result = run_explain()

    assert result["fallback"] is True
    assert result["error_category"] == "configuration"
    assert "GROQ_API_KEY" in result["answer"]
    assert result["retrieved_sources"]
    assert result["model"] == llm.MODEL


def test_successful_groq_response_uses_retrieved_context(monkeypatch):
    client, captured = fake_client()
    monkeypatch.setattr(llm, "client", client)

    result = run_explain()

    assert result["fallback"] is False
    assert result["answer"] == "The payment [POLICY-DUP-01] needs review."
    assert result["model"] == "mock-model"
    assert {"source": "POLICY-DUP-01"}.items() <= next(
        item for item in result["retrieved_sources"] if item["source"] == "POLICY-DUP-01"
    ).items()
    assert all(set(item) == {"source", "score"} for item in result["retrieved_sources"])

    prompt = captured["messages"][1]["content"]
    assert "POLICY-DUP-01" in prompt
    assert "Vendor=Acme" in prompt
    assert "Never invent or cite any other source" in prompt


@pytest.mark.parametrize(
    ("make_error", "expected_category"),
    [
        (lambda req: http_error(groq.AuthenticationError, 401, req), "authentication"),
        (lambda req: http_error(groq.PermissionDeniedError, 403, req), "permission"),
        (lambda req: http_error(groq.RateLimitError, 429, req), "rate_limit"),
        (lambda req: http_error(groq.NotFoundError, 404, req), "model_or_resource_not_found"),
        (lambda req: groq.APIConnectionError(request=req), "network"),
        (lambda req: groq.APITimeoutError(req), "timeout"),
    ],
)
def test_provider_errors_return_safe_fallback(monkeypatch, caplog, make_error, expected_category):
    request = httpx.Request("POST", "https://api.groq.example/chat/completions")

    class FailingCompletions:
        async def create(self, **kwargs):
            raise make_error(request)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FailingCompletions())
    )
    monkeypatch.setattr(llm, "client", client)

    result = run_explain()

    assert result["fallback"] is True
    assert result["error_category"] == expected_category
    assert "provider failure" not in result["answer"]
    assert "GROQ_API_KEY" not in result["answer"]
    assert expected_category in caplog.text


def test_empty_model_response_returns_fallback(monkeypatch):
    client, _ = fake_client(content=None)
    monkeypatch.setattr(llm, "client", client)

    result = run_explain()

    assert result["fallback"] is True
    assert result["error_category"] == "empty_response"
    assert "empty response" in result["answer"].lower()


def test_retrieval_returns_known_source_metadata(monkeypatch):
    contexts = retrieve_context(
        "Possible duplicate payment to Acme",
        "Vendor=Acme; amount=120.00",
    )
    source_ids = {item["source"] for item in contexts}
    allowed_ids = {item["source"] for item in POLICIES} | {"TRANSACTION-EVIDENCE"}

    assert "POLICY-DUP-01" in source_ids
    assert "TRANSACTION-EVIDENCE" in source_ids
    assert source_ids <= allowed_ids
    assert all(set(item) == {"source", "text", "score"} for item in contexts)
    assert all(0 <= item["score"] <= 1 for item in contexts)


def test_irrelevant_or_empty_evidence_retrieval_is_safe():
    assert retrieve_context("zzzxq plugh") == []

    contexts = retrieve_context("duplicate payment", "")
    assert all(item["source"] != "TRANSACTION-EVIDENCE" for item in contexts)
