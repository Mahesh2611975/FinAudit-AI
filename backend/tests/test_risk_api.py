from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth as auth_module
from app import risk_api
from app.database import Base, get_db
from app.main import app
from app.models import TransactionRecord
from app.ownership import reset_data_scope, set_data_scope


@pytest.fixture
def risk_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'risk-api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@contextmanager
def acting_as(user):
    """Temporarily authenticate endpoint calls as ``user``."""
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


def seed_duplicate(session_factory):
    db = session_factory()
    try:
        for index in (1, 2):
            db.add(
                TransactionRecord(
                    transaction_id=f"API-TX-{index}",
                    date="2026-04-01",
                    amount=7500.0,
                    signed_amount=-7500.0,
                    direction="debit",
                    vendor="Duplicate Vendor",
                    description="NEFT transfer",
                    source_file="api-statement.csv",
                    channel="NEFT",
                    country="India",
                    state="Maharashtra",
                    city="Mumbai",
                    latitude=19.076,
                    longitude=72.8777,
                )
            )
        db.commit()
    finally:
        db.close()


def analyze(client):
    response = client.post("/api/risk/analyze", json={})
    assert response.status_code == 200, response.text
    return response.json()


def first_alert(client):
    response = client.get("/api/risk/alerts?q=Duplicate%20Vendor")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    return items[0]


def test_risk_endpoints_return_database_backed_data(risk_client):
    client, session_factory = risk_client
    seed_duplicate(session_factory)
    result = analyze(client)
    assert result["alert_count"] >= 1
    assert result["overview"]["total_alerts"] == result["alert_count"]

    overview = client.get("/api/risk/overview")
    assert overview.status_code == 200
    assert overview.json()["transactions_analyzed"] == 2

    alerts = client.get("/api/risk/alerts")
    assert alerts.status_code == 200
    assert alerts.json()["total"] >= 1

    alert = first_alert(client)
    assert alert["channel"] == "NEFT"

    filtered = client.get("/api/risk/alerts?severity=critical,high&category=Duplicate%20Payment&q=Duplicate")
    assert filtered.status_code == 200
    assert filtered.json()["total"] >= 1

    empty = client.get("/api/risk/alerts?severity=does-not-exist")
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


def test_review_workflow_persists_event_and_status(risk_client):
    client, session_factory = risk_client
    seed_duplicate(session_factory)
    analyze(client)
    alert = first_alert(client)

    response = client.post(
        f"/api/risk/alerts/{alert['id']}/review",
        json={
            "action": "investigate",
            "reviewer_name": "Risk Analyst",
            "assigned_to": "Fraud Team",
            "comment": "Requesting supporting documents.",
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["review_status"] == "investigating"
    assert updated["assigned_to"] == "Fraud Team"
    assert updated["reviewer_comments"] == "Requesting supporting documents."
    assert len(updated["events"]) == 1
    assert updated["events"][0]["new_status"] == "investigating"

    resolved = client.post(
        f"/api/risk/alerts/{alert['alert_id']}/review",
        json={"action": "resolve", "reviewer_name": "Risk Analyst", "comment": "Documents verified."},
    )
    assert resolved.status_code == 200
    assert resolved.json()["review_status"] == "resolved"
    assert resolved.json()["resolved_at"]

    dismissed = client.post(
        f"/api/risk/alerts/{alert['id']}/review",
        json={"action": "status", "review_status": "dismissed", "reviewer_name": "Risk Analyst"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["review_status"] == "dismissed"

    missing_actor = client.post(
        f"/api/risk/alerts/{alert['id']}/review",
        json={"action": "comment", "comment": "No actor"},
    )
    # Actor identity is derived from the authenticated user; client-supplied actor is not required.
    assert missing_actor.status_code == 200


def test_review_event_uses_authenticated_user_not_client_supplied_identity(risk_client):
    client, session_factory = risk_client
    seed_duplicate(session_factory)
    analyze(client)
    alert = first_alert(client)
    reviewer = SimpleNamespace(
        id=777,
        username="signed.in.reviewer",
        full_name="Signed In Reviewer",
        role="reviewer",
        is_active=True,
    )

    with acting_as(reviewer):
        response = client.post(
            f"/api/risk/alerts/{alert['id']}/review",
            json={
                "action": "investigate",
                "reviewer_name": "spoofed.reviewer",
                "actor": "spoofed.reviewer",
                "comment": "Reviewed by the signed-in account.",
            },
        )

    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert events[0]["actor"] == "signed.in.reviewer"
    assert all(event["actor"] != "spoofed.reviewer" for event in events)


def test_investigate_uses_deterministic_context_and_ai(monkeypatch, risk_client):
    client, session_factory = risk_client
    seed_duplicate(session_factory)
    analyze(client)
    alert = first_alert(client)
    captured = {}

    async def fake_explain(question, evidence, *, scope):
        captured["question"] = question
        captured["evidence"] = evidence
        captured["scope"] = scope
        return {
            "answer": "Human review is required.",
            "model": "mock",
            "retrieved_sources": [],
            "fallback": False,
        }

    monkeypatch.setattr(risk_api, "explain_finding", fake_explain)
    response = client.post("/api/risk/investigate", json={"alert_id": alert["id"]})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["verified_facts"]
    assert payload["alert"]["alert_id"] == alert["alert_id"]
    assert payload["ai"]["answer"] == "Human review is required."
    assert "not proof of fraud" in captured["question"]
    assert "Duplicate Vendor" in captured["evidence"]
    assert captured["scope"].owner_user_id == 1
    assert captured["scope"].global_access is False


def test_risk_explanation_scope_follows_authenticated_user(monkeypatch, risk_client):
    client, session_factory = risk_client
    seed_duplicate(session_factory)
    analyze(client)
    alert = first_alert(client)
    owners = []

    async def fake_explain(question, evidence, *, scope):
        owners.append((scope.owner_user_id, scope.global_access))
        return {
            "answer": "Human review is required.",
            "model": "mock",
            "retrieved_sources": [],
            "fallback": False,
        }

    monkeypatch.setattr(risk_api, "explain_finding", fake_explain)
    with acting_as(SimpleNamespace(id=201, username="risk-user-201", role="user", is_active=True)):
        rejected = client.post(
            "/api/risk/investigate",
            json={"alert_id": alert["id"], "global_access": True},
        )
    assert rejected.status_code == 400
    assert owners == []

    for user_id in (201, 202):
        user = SimpleNamespace(
            id=user_id,
            username=f"risk-user-{user_id}",
            role="user",
            is_active=True,
        )
        with acting_as(user):
            response = client.post("/api/risk/investigate", json={"alert_id": alert["id"]})
        assert response.status_code == 200, response.text

    assert owners == [(201, False), (202, False)]


def test_config_channels_trends_and_geography_endpoints(risk_client):
    client, session_factory = risk_client
    seed_duplicate(session_factory)
    analyze(client)

    config = client.get("/api/risk/config")
    assert config.status_code == 200
    assert config.json()["thresholds"]["low_max"] == 29
    assert "UPI" in config.json()["supported_channels"]
    assert "dismissed" in config.json()["review_statuses"]

    channels = client.get("/api/risk/channels")
    assert channels.status_code == 200
    assert any(item["channel"] == "NEFT" for item in channels.json()["channels"])

    trends = client.get("/api/risk/trends?granularity=daily")
    assert trends.status_code == 200
    assert trends.json()["series"]

    bad_trends = client.get("/api/risk/trends?granularity=hourly")
    assert bad_trends.status_code == 422

    geography = client.get("/api/risk/geography")
    assert geography.status_code == 200
    assert geography.json()["available"] is True
    assert geography.json()["items"][0]["city"] == "Mumbai"

    runs = client.get("/api/risk/analysis-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["status"] == "completed"
