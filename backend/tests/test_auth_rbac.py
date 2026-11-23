import asyncio
import sys
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth as auth_module
from app import create_admin as bootstrap
from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditRun,
    Base,
    Finding,
    InvestigationCase,
    ReconciliationResult,
    ReconciliationReviewAction,
    ReconciliationRun,
    RevokedToken,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_FINANCE_ANALYST,
    ROLE_LENDER,
    ROLE_REVIEWER,
    ROLE_USER,
    User,
)


client = TestClient(app)


def _clear_auth_overrides():
    app.dependency_overrides.pop(auth_module.get_current_active_user, None)
    app.dependency_overrides.pop(auth_module.get_current_user, None)


@pytest.fixture(autouse=True)
def _real_auth(_default_admin_auth_override):
    # Security tests exercise real auth; clear the conftest admin override.
    _clear_auth_overrides()
    yield
    _clear_auth_overrides()


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _create_user(db, username, password, role, *, is_active=True):
    user = User(
        username=username,
        email=f"{username}@example.test",
        full_name=username.replace("-", " ").title(),
        hashed_password=auth_module.hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _seed_reconciliation(db):
    run = ReconciliationRun(
        bank_filename="bank.csv",
        ledger_filename="ledger.csv",
        date_tolerance_days=3,
        amount_tolerance=0.005,
        run_status="completed",
        bank_record_count=1,
        ledger_record_count=1,
        result_count=1,
        matched_count=0,
        amount_mismatch_count=1,
        exception_count=1,
        bank_total=100.0,
        ledger_total=90.0,
        difference=10.0,
    )
    db.add(run)
    db.flush()
    result = ReconciliationResult(
        run_id=run.id,
        status="amount_mismatch",
        bank_date="2026-01-01",
        bank_amount=100.0,
        bank_direction="credit",
        bank_reference="R1",
        bank_description="Bank",
        bank_source_file="bank.csv",
        bank_source_row=2,
        ledger_date="2026-01-01",
        ledger_amount=90.0,
        ledger_direction="credit",
        ledger_reference="R1",
        ledger_description="Ledger",
        ledger_source_file="ledger.csv",
        ledger_source_row=2,
        amount_difference=10.0,
        review_status="pending",
    )
    db.add(result)
    db.commit()
    db.refresh(run)
    db.refresh(result)
    return run, result


def test_password_hashing_and_verification():
    hashed = auth_module.hash_password("StrongPass123!")
    assert hashed != "StrongPass123!"
    assert auth_module.verify_password("StrongPass123!", hashed) is True
    assert auth_module.verify_password("wrong-password", hashed) is False


def test_login_valid_credentials(db_session):
    _create_user(db_session, "analyst1", "StrongPass123!", ROLE_FINANCE_ANALYST)
    res = _login("analyst1", "StrongPass123!")
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "analyst1"
    assert body["user"]["role"] == ROLE_FINANCE_ANALYST
    assert body["access_token"]


def test_login_invalid_password(db_session):
    _create_user(db_session, "analyst2", "StrongPass123!", ROLE_FINANCE_ANALYST)
    res = _login("analyst2", "WrongPass123!")
    assert res.status_code == 401
    assert "Invalid" in res.json()["detail"]


def test_missing_token_rejected():
    res = client.get("/api/audits")
    assert res.status_code == 401


def test_invalid_token_rejected():
    res = client.get("/api/audits", headers=_auth("not-a-real-jwt"))
    assert res.status_code == 401


def test_expired_token_rejected(db_session):
    user = _create_user(db_session, "analyst3", "StrongPass123!", ROLE_FINANCE_ANALYST)
    token, payload = auth_module.create_access_token(user, expires_minutes=-1)
    assert payload["exp"] < int(datetime.now(timezone.utc).timestamp())
    res = client.get("/api/audits", headers=_auth(token))
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


def test_unauthorized_endpoint_access_for_role(db_session):
    auditor = _create_user(db_session, "auditor1", "StrongPass123!", ROLE_AUDITOR)
    token, _ = auth_module.create_access_token(auditor)
    # Auditor lacks audit:run/data:import permission.
    files = {"files": ("test.csv", "date,amount,description\n2026-01-01,100,test\n", "text/csv")}
    res = client.post("/api/audits", files=files, headers=_auth(token))
    assert res.status_code == 403


def test_reviewer_only_approval_and_actor_identity(db_session):
    reviewer = _create_user(db_session, "reviewer1", "StrongPass123!", ROLE_REVIEWER)
    analyst = _create_user(db_session, "analyst4", "StrongPass123!", ROLE_FINANCE_ANALYST)
    run, result = _seed_reconciliation(db_session)

    analyst_token, _ = auth_module.create_access_token(analyst)
    res_forbidden = client.post(
        f"/api/reconciliations/results/{result.id}/review",
        json={"action": "confirm", "reviewer": "spoofed-reviewer"},
        headers=_auth(analyst_token),
    )
    assert res_forbidden.status_code == 403

    reviewer_token, _ = auth_module.create_access_token(reviewer)
    res_ok = client.post(
        f"/api/reconciliations/results/{result.id}/review",
        json={"action": "confirm", "reviewer": "spoofed-reviewer", "comment": "looks good"},
        headers=_auth(reviewer_token),
    )
    assert res_ok.status_code == 200
    body = res_ok.json()
    assert body["result"]["reviewed_by"] == "reviewer1"
    assert body["result"]["review_status"] == "confirmed"

    db_session.expire_all()
    updated = db_session.query(ReconciliationResult).filter(ReconciliationResult.id == result.id).first()
    action = db_session.query(ReconciliationReviewAction).filter(ReconciliationReviewAction.result_id == result.id).order_by(ReconciliationReviewAction.id.desc()).first()
    assert updated.reviewed_by == "reviewer1"
    assert action.actor == "reviewer1"
    assert action.actor_user_id == reviewer.id
    assert "spoofed-reviewer" not in (action.actor or "")


def test_audit_actor_identity_recorded_for_investigation_note(db_session):
    admin = _create_user(db_session, "admin1", "StrongPass123!", ROLE_ADMIN)
    token, _ = auth_module.create_access_token(admin)
    # Create investigation
    create_res = client.post(
        "/api/investigations",
        json={"title": "Test case", "severity": "high", "description": "desc"},
        headers=_auth(token),
    )
    assert create_res.status_code == 201
    case_number = create_res.json()["case_number"]
    note_res = client.post(
        f"/api/investigations/{case_number}/notes",
        json={"author": "spoofed", "note": "Real note"},
        headers=_auth(token),
    )
    assert note_res.status_code == 201
    assert note_res.json()["author"] == "admin1"


def test_logout_revokes_token(db_session):
    user = _create_user(db_session, "analyst5", "StrongPass123!", ROLE_FINANCE_ANALYST)
    login = _login("analyst5", "StrongPass123!")
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    out = client.post("/api/auth/logout", headers=_auth(token))
    assert out.status_code == 200
    me2 = client.get("/api/auth/me", headers=_auth(token))
    assert me2.status_code == 401


def test_existing_api_regression_with_override(db_session):
    # Re-enable the conftest-style override to confirm regression path still works.
    from types import SimpleNamespace
    async def _override_user():
        return SimpleNamespace(id=1, username="test-admin", email="a@b.c", full_name="Admin", role=ROLE_ADMIN, is_active=True)
    app.dependency_overrides[auth_module.get_current_active_user] = _override_user
    try:
        res = client.get("/api/audits")
        assert res.status_code == 200
    finally:
        _clear_auth_overrides()


def _register(username, email, *, password="StrongPass123!", confirm=None, full_name="New User", account_type="User", **extra):
    payload = {
        "full_name": full_name,
        "email": email,
        "username": username,
        "password": password,
        "confirm_password": confirm if confirm is not None else password,
        "account_type": account_type,
    }
    payload.update(extra)
    return client.post("/api/auth/register", json=payload)


def test_registration_creates_pending_user_account():
    res = _register("newuser1", "New.User1@Example.com")
    assert res.status_code == 201
    body = res.json()
    assert "access_token" not in body
    user = body["user"]
    assert user["role"] == ROLE_USER
    assert user["dashboard"] == "user-dashboard"
    assert user["is_active"] is False
    assert user["is_provisioned"] is False
    assert user["approval_status"] == "pending_approval"

    pending_login = _login("newuser1", "StrongPass123!")
    assert pending_login.status_code == 403
    assert "pending" in pending_login.json()["detail"].lower()


def test_registration_duplicate_identity_is_case_insensitive(db_session):
    _create_user(db_session, "DuplicateUser", "StrongPass123!", ROLE_AUDITOR)

    same_username = _register("duplicateuser", "different@example.test")
    assert same_username.status_code == 409
    assert "username" in same_username.json()["detail"].lower()

    same_email = _register("differentuser", "DUPLICATEUSER@EXAMPLE.TEST")
    assert same_email.status_code == 409
    assert "email" in same_email.json()["detail"].lower()


def test_registration_password_validation():
    mismatch = _register("mismatchuser", "mismatch@example.test", confirm="Different123!")
    assert mismatch.status_code == 422

    weak = _register("weakuser", "weak@example.test", password="password", confirm="password")
    assert weak.status_code == 422


def test_approval_requires_admin_and_never_grants_admin_implicitly(db_session):
    res = _register("approvaluser", "approval@example.test")
    assert res.status_code == 201
    user_id = res.json()["user"]["id"]

    other = _create_user(db_session, "approver-not-admin", "StrongPass123!", ROLE_AUDITOR)
    other_token, _ = auth_module.create_access_token(other)
    forbidden = client.post(
        f"/api/auth/users/{user_id}/approval",
        json={"approved": True, "role": ROLE_AUDITOR},
        headers=_auth(other_token),
    )
    assert forbidden.status_code == 403

    admin = _create_user(db_session, "approver-admin", "StrongPass123!", ROLE_ADMIN)
    admin_token, _ = auth_module.create_access_token(admin)

    admin_role = client.post(
        f"/api/auth/users/{user_id}/approval",
        json={"approved": True, "role": ROLE_ADMIN},
        headers=_auth(admin_token),
    )
    assert admin_role.status_code == 422

    approved = client.post(
        f"/api/auth/users/{user_id}/approval",
        json={"approved": True, "role": ROLE_REVIEWER},
        headers=_auth(admin_token),
    )
    assert approved.status_code == 200
    assert approved.json()["role"] == ROLE_REVIEWER
    assert approved.json()["approval_status"] == "approved"

    login = _login("approvaluser", "StrongPass123!")
    assert login.status_code == 200
    assert login.json()["user"]["role"] == ROLE_REVIEWER


def test_admin_can_list_users_and_provision_account(db_session):
    admin = _create_user(db_session, "list-admin", "StrongPass123!", ROLE_ADMIN)
    admin_token, _ = auth_module.create_access_token(admin)

    listed = client.get("/api/auth/users", headers=_auth(admin_token))
    assert listed.status_code == 200
    assert any(item["username"] == admin.username for item in listed.json())

    non_admin = _create_user(db_session, "list-not-admin", "StrongPass123!", ROLE_AUDITOR)
    non_admin_token, _ = auth_module.create_access_token(non_admin)
    assert client.get("/api/auth/users", headers=_auth(non_admin_token)).status_code == 403

    provisioned = client.post(
        "/api/auth/users",
        json={
            "username": "provisioned1",
            "email": "provisioned1@example.test",
            "full_name": "Provisioned User",
            "password": "StrongPass123!",
            "role": ROLE_FINANCE_ANALYST,
        },
        headers=_auth(admin_token),
    )
    assert provisioned.status_code == 200
    assert provisioned.json()["role"] == ROLE_FINANCE_ANALYST
    assert provisioned.json()["approval_status"] == "approved"


def test_admin_can_disable_and_reactivate_approved_user(db_session):
    registered = _register("toggleuser", "toggle@example.test")
    user_id = registered.json()["user"]["id"]
    admin = _create_user(db_session, "toggle-admin", "StrongPass123!", ROLE_ADMIN)
    admin_token, _ = auth_module.create_access_token(admin)

    approve = client.post(
        f"/api/auth/users/{user_id}/approval",
        json={"approved": True, "role": ROLE_FINANCE_ANALYST},
        headers=_auth(admin_token),
    )
    assert approve.status_code == 200

    disabled = client.patch(
        f"/api/auth/users/{user_id}",
        json={"is_active": False},
        headers=_auth(admin_token),
    )
    assert disabled.status_code == 200
    assert disabled.json()["approval_status"] == "disabled"
    assert _login("toggleuser", "StrongPass123!").status_code == 403

    reactivated = client.patch(
        f"/api/auth/users/{user_id}",
        json={"is_active": True},
        headers=_auth(admin_token),
    )
    assert reactivated.status_code == 200
    assert _login("toggleuser", "StrongPass123!").status_code == 200


# ---------------------------------------------------------------------------
# Finalized account-type workflow, role dashboards and data isolation
# ---------------------------------------------------------------------------

def test_lender_registration_and_dashboard_assignment():
    res = _register("lender-one", "lender.one@example.test", account_type="Lender")
    assert res.status_code == 201
    user = res.json()["user"]
    assert user["role"] == ROLE_LENDER
    assert user["dashboard"] == "lender-dashboard"
    assert "data:read" not in user["permissions"]
    assert "invoice:read" in user["permissions"]
    assert user["approval_status"] == "pending_approval"


@pytest.mark.parametrize("payload", [
    {"account_type": "Admin"},
    {"role": ROLE_ADMIN},
    {"account_type": "user", "role": ROLE_ADMIN},
])
def test_registration_never_accepts_admin_role(payload):
    res = client.post(
        "/api/auth/register",
        json={
            "full_name": "Not Admin",
            "email": "no-admin@example.test",
            "username": "no-admin",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
            **payload,
        },
    )
    assert res.status_code == 422
    session = SessionLocal()
    try:
        assert session.query(User).filter(User.username == "no-admin").first() is None
    finally:
        session.close()


def test_approved_login_returns_backend_verified_dashboard(db_session):
    admin = _create_user(db_session, "dash-admin", "StrongPass123!", ROLE_ADMIN)
    admin_token, _ = auth_module.create_access_token(admin)

    for username, account_type, expected_role, expected_dashboard in (
        ("dash-user", "User", ROLE_USER, "user-dashboard"),
        ("dash-lender", "Lender", ROLE_LENDER, "lender-dashboard"),
    ):
        registered = _register(username, f"{username}@example.test", account_type=account_type)
        assert registered.status_code == 201
        approved = client.post(
            f"/api/auth/users/{registered.json()['user']['id']}/approval",
            json={"approved": True},
            headers=_auth(admin_token),
        )
        assert approved.status_code == 200
        login = _login(username, "StrongPass123!")
        assert login.status_code == 200
        assert login.json()["user"]["role"] == expected_role
        assert login.json()["user"]["dashboard"] == expected_dashboard


def test_token_role_claim_cannot_escalate_privileges(db_session):
    user = _create_user(db_session, "escalation-user", "StrongPass123!", ROLE_USER)
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": ROLE_ADMIN,
            "jti": "forged-admin-claim",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "type": "access",
        },
        auth_module._jwt_secret(),
        algorithm=auth_module.JWT_ALGORITHM,
    )
    # Authorization is resolved from the database role, never the JWT claim.
    assert client.get("/api/auth/users", headers=_auth(forged)).status_code == 403


def test_wrong_signature_and_deactivated_account_are_rejected(db_session):
    user = _create_user(db_session, "status-user", "StrongPass123!", ROLE_USER)
    now = datetime.now(timezone.utc)
    wrong_signature = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "jti": "wrong-signature",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "type": "access",
        },
        "definitely-not-the-server-secret",
        algorithm=auth_module.JWT_ALGORITHM,
    )
    assert client.get("/api/audits", headers=_auth(wrong_signature)).status_code == 401

    token, _ = auth_module.create_access_token(user)
    assert client.get("/api/audits", headers=_auth(token)).status_code == 200
    user.is_active = False
    db_session.commit()
    assert client.get("/api/audits", headers=_auth(token)).status_code == 403


def test_lender_role_cannot_access_company_financial_data(db_session):
    lender = _create_user(db_session, "lender-scope", "StrongPass123!", ROLE_LENDER)
    token, _ = auth_module.create_access_token(lender)

    assert client.get("/api/invoices", headers=_auth(token)).status_code == 200
    assert client.get("/api/transactions", headers=_auth(token)).status_code == 403
    assert client.get("/api/reports/summary", headers=_auth(token)).status_code == 403
    assert client.get("/api/risk/overview", headers=_auth(token)).status_code == 403
    assert client.post("/api/audits", headers=_auth(token)).status_code == 403


def test_cross_account_data_isolation(db_session):
    first = _create_user(db_session, "scope-first", "StrongPass123!", ROLE_USER)
    second = _create_user(db_session, "scope-second", "StrongPass123!", ROLE_USER)
    for owner, number in ((first, "CASE-FIRST"), (second, "CASE-SECOND")):
        db_session.add(
            InvestigationCase(
                case_number=number,
                title=number,
                description="Isolation test",
                severity="low",
                status="open",
                owner_user_id=owner.id,
            )
        )
    db_session.commit()

    first_token, _ = auth_module.create_access_token(first)
    second_token, _ = auth_module.create_access_token(second)
    first_cases = client.get("/api/investigations", headers=_auth(first_token)).json()["items"]
    second_cases = client.get("/api/investigations", headers=_auth(second_token)).json()["items"]
    assert [item["case_number"] for item in first_cases] == ["CASE-FIRST"]
    assert [item["case_number"] for item in second_cases] == ["CASE-SECOND"]
    assert client.get(
        "/api/investigations/CASE-SECOND", headers=_auth(first_token)
    ).status_code == 404
    # Admins retain the global view for system administration.
    admin = _create_user(db_session, "scope-admin", "StrongPass123!", ROLE_ADMIN)
    admin_token, _ = auth_module.create_access_token(admin)
    admin_cases = client.get("/api/investigations", headers=_auth(admin_token)).json()["items"]
    assert {item["case_number"] for item in admin_cases} >= {"CASE-FIRST", "CASE-SECOND"}


def test_new_rows_are_stamped_with_authenticated_owner(db_session):
    user = _create_user(db_session, "stamp-user", "StrongPass123!", ROLE_USER)
    token, _ = auth_module.create_access_token(user)
    created = client.post(
        "/api/investigations",
        json={"title": "Owned case", "severity": "low", "description": "stamp test"},
        headers=_auth(token),
    )
    assert created.status_code == 201
    db_session.expire_all()
    case = db_session.query(InvestigationCase).filter(
        InvestigationCase.case_number == created.json()["case_number"]
    ).first()
    assert case.owner_user_id == user.id


def test_one_time_admin_bootstrap(monkeypatch, tmp_path):
    # Use an isolated database so pre-existing admins from other tests do not
    # affect the bootstrap guard.
    engine = create_engine(f"sqlite:///{tmp_path / 'bootstrap.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(bootstrap, "engine", engine)
    monkeypatch.setattr(bootstrap, "SessionLocal", session_factory)
    monkeypatch.setattr(bootstrap, "has_pending_schema_changes", lambda *a, **k: False)
    monkeypatch.setattr(bootstrap, "ensure_database_schema", lambda *a, **k: {})
    monkeypatch.setattr(bootstrap, "backup_sqlite_database", lambda *a, **k: None)
    monkeypatch.setenv("FINAUDIT_ADMIN_PASSWORD", "StrongPass123!")
    monkeypatch.setattr(
        sys, "argv",
        ["create_admin", "--username", "boot-admin", "--email", "boot@example.test"],
    )

    assert bootstrap.main() == 0
    check = session_factory()
    try:
        admins = check.query(User).filter(User.role == ROLE_ADMIN).all()
        assert [user.username for user in admins] == ["boot-admin"]
    finally:
        check.close()

    # The same command cannot be reused to mint another administrator.
    monkeypatch.setattr(sys, "argv", ["create_admin", "--username", "boot-admin-2"])
    assert bootstrap.main() == 3
    check = session_factory()
    try:
        assert [user.username for user in check.query(User).filter(User.role == ROLE_ADMIN).all()] == ["boot-admin"]
    finally:
        check.close()


# ---------------------------------------------------------------------------
# Local demonstration mode
# ---------------------------------------------------------------------------

def _enable_local_demo(monkeypatch):
    monkeypatch.setenv("FINAUDIT_LOCAL_DEMO_MODE", "true")
    monkeypatch.setenv("FINAUDIT_ENV", "local")


def test_demo_mode_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FINAUDIT_LOCAL_DEMO_MODE", raising=False)
    monkeypatch.delenv("FINAUDIT_ENV", raising=False)

    status = client.get("/api/auth/demo/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert client.post("/api/auth/demo/session").status_code == 403

    # Frontend navigation cannot turn these into public endpoints.
    assert client.get("/api/audits").status_code == 401
    assert client.get("/api/dashboard/overview").status_code == 401
    assert client.get("/api/auth/users").status_code == 401


def test_local_demo_session_uses_real_jwt_rbac_without_database_writes(monkeypatch, db_session):
    _enable_local_demo(monkeypatch)
    users_before = db_session.query(User).count()
    revocations_before = db_session.query(RevokedToken).count()

    session = client.post("/api/auth/demo/session")
    assert session.status_code == 200
    body = session.json()
    assert body["demo"] is True
    assert body["user"]["is_demo"] is True
    assert body["user"]["username"] == auth_module.DEMO_USERNAME
    assert body["user"]["role"] == ROLE_ADMIN
    assert body["user"]["dashboard"] == "admin-dashboard"
    assert body["access_token"]

    # The demo principal exists only in memory; no users/hashes/records added.
    assert db_session.query(User).count() == users_before
    assert db_session.query(RevokedToken).count() == revocations_before

    demo_headers = _auth(body["access_token"])
    assert client.get("/api/audits", headers=demo_headers).status_code == 200
    assert client.get("/api/auth/users", headers=demo_headers).status_code == 200

    # A missing token is still rejected even while local demo mode is enabled.
    assert client.get("/api/audits").status_code == 401
    assert client.get("/api/auth/users").status_code == 401


def test_demo_mode_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("FINAUDIT_LOCAL_DEMO_MODE", "true")
    monkeypatch.setenv("FINAUDIT_ENV", "production")

    assert client.get("/api/auth/demo/status").json()["enabled"] is False
    assert client.post("/api/auth/demo/session").status_code == 403
    assert client.get("/api/audits").status_code == 401


def test_demo_token_stops_working_when_mode_is_disabled(monkeypatch):
    _enable_local_demo(monkeypatch)
    token = client.post("/api/auth/demo/session").json()["access_token"]
    assert client.get("/api/audits", headers=_auth(token)).status_code == 200

    monkeypatch.setenv("FINAUDIT_ENV", "production")
    assert client.get("/api/auth/demo/status").json()["enabled"] is False
    assert client.get("/api/audits", headers=_auth(token)).status_code == 401


def test_demo_logout_leaves_no_revocation_row(monkeypatch, db_session):
    _enable_local_demo(monkeypatch)
    before = db_session.query(RevokedToken).count()
    token = client.post("/api/auth/demo/session").json()["access_token"]

    res = client.post("/api/auth/logout", headers=_auth(token))
    assert res.status_code == 200
    assert "Demo session cleared" in res.json()["detail"]
    assert db_session.query(RevokedToken).count() == before
