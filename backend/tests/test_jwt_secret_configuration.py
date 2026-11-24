"""C-1 regression tests: fail-closed JWT signing secret configuration.

These tests use obviously fake, non-deployable secrets. No real credential is
stored in source control.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.database import SessionLocal
from app.main import app
from app.models import ROLE_USER, User

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# Long, varied placeholders used only to prove signing/verification behaviour.
STRONG_TEST_SECRET = "unit-test-jwt-" + "9f8e7d6c5b4a3210" * 3
OTHER_TEST_SECRET = "unit-test-jwt-" + "0a1b2c3d4e5f6789" * 3
TEST_PASSWORD = "StrongPass123!"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_auth_environment(monkeypatch):
    """Remove deployment env overrides and exercise the real auth dependencies."""
    for key in (
        "FINAUDIT_JWT_SECRET",
        "FINAUDIT_ENV",
        "APP_ENV",
        "FINAUDIT_LOCAL_DEMO_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    app.dependency_overrides.pop(auth.get_current_active_user, None)
    app.dependency_overrides.pop(auth.get_current_user, None)
    yield
    app.dependency_overrides.pop(auth.get_current_active_user, None)
    app.dependency_overrides.pop(auth.get_current_user, None)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_user(username):
    db = SessionLocal()
    try:
        user = User(
            username=username,
            email=f"{username}@example.test",
            full_name=username.replace("-", " ").title(),
            hashed_password=auth.hash_password(TEST_PASSWORD),
            role=ROLE_USER,
            is_active=True,
            is_provisioned=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _principal(user_id, username):
    return User(
        id=user_id,
        username=username,
        role=ROLE_USER,
        is_active=True,
        is_provisioned=True,
    )


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "qa"])
def test_missing_secret_fails_closed_outside_local(monkeypatch, environment):
    monkeypatch.setenv("FINAUDIT_ENV", environment)

    with pytest.raises(auth.JWTConfigurationError, match="FINAUDIT_JWT_SECRET is required"):
        auth.validate_jwt_configuration()
    with pytest.raises(auth.JWTConfigurationError):
        auth._jwt_secret()
    with pytest.raises(HTTPException) as exc:
        auth.create_access_token(_principal(1, "no-secret"))
    assert exc.value.status_code == 503
    assert "FINAUDIT_JWT_SECRET" in exc.value.detail
    assert auth._DEV_JWT_SECRET not in exc.value.detail


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_protected_request_fails_safely_when_secret_is_missing(monkeypatch, environment):
    monkeypatch.setenv("FINAUDIT_ENV", environment)

    response = client.get("/api/audits", headers=_auth("invalid-token"))
    assert response.status_code == 503
    assert "FINAUDIT_JWT_SECRET" in response.json()["detail"]
    assert auth._DEV_JWT_SECRET not in response.text


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_startup_fails_without_secret_in_nonlocal_environment(tmp_path, environment):
    env = os.environ.copy()
    env.pop("APP_ENV", None)
    env.update(
        {
            "FINAUDIT_ENV": environment,
            "FINAUDIT_JWT_SECRET": "",
            "FINAUDIT_DATABASE_URL": f"sqlite:///{tmp_path / 'startup.db'}",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "FINAUDIT_JWT_SECRET is required" in result.stderr
    assert auth._DEV_JWT_SECRET not in result.stderr


@pytest.mark.parametrize(
    "weak_secret",
    [
        "short",
        "x" * 40,
        auth._DEV_JWT_SECRET,
        "change-me",
        "replace-with-strong-secret",
    ],
)
def test_weak_secret_is_rejected_outside_local(monkeypatch, weak_secret):
    monkeypatch.setenv("FINAUDIT_ENV", "production")
    monkeypatch.setenv("FINAUDIT_JWT_SECRET", weak_secret)

    with pytest.raises(auth.JWTConfigurationError, match="too weak"):
        auth.validate_jwt_configuration()
    response = client.get("/api/audits", headers=_auth("invalid-token"))
    assert response.status_code == 503
    assert weak_secret not in response.text


def test_valid_configured_secret_allows_token_creation_and_verification(monkeypatch):
    monkeypatch.setenv("FINAUDIT_ENV", "production")
    monkeypatch.setenv("FINAUDIT_JWT_SECRET", STRONG_TEST_SECRET)
    user_id = _create_user("jwt-config-user")

    login = client.post(
        "/api/auth/login",
        json={"username": "jwt-config-user", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    payload = auth.decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"
    assert payload["jti"]
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200


def test_local_development_still_issues_tokens_without_a_configured_secret(monkeypatch):
    monkeypatch.setenv("FINAUDIT_ENV", "local")

    token, payload = auth.create_access_token(_principal(4242, "local-dev"))
    assert auth.decode_access_token(token)["jti"] == payload["jti"]


def test_local_environment_with_hosted_database_requires_secret(monkeypatch):
    monkeypatch.setenv("FINAUDIT_ENV", "local")
    monkeypatch.setenv("FINAUDIT_DATABASE_URL", "postgresql://user:pass@localhost/finaudit")

    with pytest.raises(auth.JWTConfigurationError, match="FINAUDIT_JWT_SECRET is required"):
        auth.validate_jwt_configuration()


def test_token_signed_with_incorrect_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("FINAUDIT_ENV", "staging")
    monkeypatch.setenv("FINAUDIT_JWT_SECRET", STRONG_TEST_SECRET)
    user_id = _create_user("jwt-wrong-secret-user")
    now = datetime.now(timezone.utc)
    wrongly_signed = jwt.encode(
        {
            "sub": str(user_id),
            "username": "jwt-wrong-secret-user",
            "role": ROLE_USER,
            "jti": "wrong-secret-jti",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "type": "access",
        },
        OTHER_TEST_SECRET,
        algorithm=auth.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as exc:
        auth.decode_access_token(wrongly_signed)
    assert exc.value.status_code == 401
    assert client.get("/api/audits", headers=_auth(wrongly_signed)).status_code == 401


def test_expired_and_revoked_tokens_stay_rejected_with_configured_secret(monkeypatch):
    monkeypatch.setenv("FINAUDIT_ENV", "production")
    monkeypatch.setenv("FINAUDIT_JWT_SECRET", STRONG_TEST_SECRET)
    user_id = _create_user("jwt-expiry-user")
    principal = _principal(user_id, "jwt-expiry-user")

    expired, _ = auth.create_access_token(principal, expires_minutes=-1)
    expired_response = client.get("/api/audits", headers=_auth(expired))
    assert expired_response.status_code == 401
    assert "expired" in expired_response.json()["detail"].lower()

    token, _ = auth.create_access_token(principal)
    assert client.get("/api/audits", headers=_auth(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 200
    revoked_response = client.get("/api/audits", headers=_auth(token))
    assert revoked_response.status_code == 401
    assert "revoked" in revoked_response.json()["detail"].lower()


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "qa"])
def test_demo_mode_stays_disabled_outside_local(monkeypatch, environment):
    monkeypatch.setenv("FINAUDIT_ENV", environment)
    monkeypatch.setenv("FINAUDIT_LOCAL_DEMO_MODE", "true")
    monkeypatch.setenv("FINAUDIT_JWT_SECRET", STRONG_TEST_SECRET)

    assert client.get("/api/auth/demo/status").json()["enabled"] is False
    assert client.post("/api/auth/demo/session").status_code == 403
    assert client.get("/api/audits").status_code == 401


def test_local_demo_token_is_rejected_after_switching_to_staging(monkeypatch):
    monkeypatch.setenv("FINAUDIT_ENV", "local")
    monkeypatch.setenv("FINAUDIT_LOCAL_DEMO_MODE", "true")
    token = client.post("/api/auth/demo/session").json()["access_token"]
    assert client.get("/api/audits", headers=_auth(token)).status_code == 200

    monkeypatch.setenv("FINAUDIT_ENV", "staging")
    monkeypatch.setenv("FINAUDIT_JWT_SECRET", STRONG_TEST_SECRET)
    assert client.get("/api/audits", headers=_auth(token)).status_code == 401


def test_deployment_templates_require_jwt_secret_and_disable_demo():
    render = (REPO_ROOT / "render.yaml").read_text()
    production_example = (REPO_ROOT / "deploy/.env.production.example").read_text()
    deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text()
    frontend_example = (REPO_ROOT / "frontend/.env.example").read_text()

    for content in (render, production_example, deployment):
        assert "FINAUDIT_JWT_SECRET" in content
    assert "FINAUDIT_ENV" in render
    assert "value: production" in render
    assert "FINAUDIT_ENV=production" in production_example
    assert "FINAUDIT_LOCAL_DEMO_MODE" in render
    assert "FINAUDIT_LOCAL_DEMO_MODE" in production_example
    assert "VITE_LOCAL_DEMO_MODE=false" in frontend_example
    for content in (render, production_example, deployment):
        assert auth._DEV_JWT_SECRET not in content
