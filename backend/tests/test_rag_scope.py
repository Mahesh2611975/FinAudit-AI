from types import SimpleNamespace

import pytest

from app.auth import rag_scope_for_user, role_permissions
from app.models import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_FINANCE_ANALYST,
    ROLE_LENDER,
    ROLE_REVIEWER,
    ROLE_USER,
)
from app.ownership import (
    RAG_GLOBAL_READ_PERMISSION,
    RAG_GLOBAL_WRITE_PERMISSION,
    RAG_READ,
    RAG_SCOPE_SOURCE_BACKGROUND,
    RAG_WRITE,
    RagAccessDenied,
    RagScope,
    rag_scope_for_background,
    reject_client_rag_scope_input,
    validate_rag_scope,
)


def _user(user_id: int, role: str) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role, is_active=True)


def test_valid_owner_scope_from_authenticated_user():
    scope = rag_scope_for_user(_user(7, ROLE_USER))

    assert scope.owner_user_id == 7
    assert scope.global_access is False
    assert validate_rag_scope(scope) is scope


def test_missing_scope_is_rejected():
    with pytest.raises(RagAccessDenied, match="trusted RAG scope"):
        validate_rag_scope(None)


def test_null_owner_is_rejected_for_ordinary_operations():
    scope = RagScope(owner_user_id=None)

    with pytest.raises(RagAccessDenied, match="explicit owner"):
        validate_rag_scope(scope)

    with pytest.raises(RagAccessDenied, match="explicit owner"):
        rag_scope_for_background(None)


def test_global_access_without_explicit_permission_is_rejected():
    forged = RagScope(
        owner_user_id=7,
        permissions=frozenset({"documents:read"}),
        global_access=True,
        audit_reason="review",
    )

    for operation in (RAG_READ, RAG_WRITE):
        with pytest.raises(RagAccessDenied, match="requires rag:global"):
            validate_rag_scope(forged, operation=operation)


def test_explicit_admin_global_scope_is_accepted():
    admin = _user(1, ROLE_ADMIN)

    read_scope = rag_scope_for_user(
        admin,
        operation=RAG_READ,
        global_access=True,
        audit_reason="legacy evidence review",
    )
    write_scope = rag_scope_for_user(
        admin,
        operation=RAG_WRITE,
        global_access=True,
        audit_reason="legacy evidence maintenance",
    )

    assert RAG_GLOBAL_READ_PERMISSION in read_scope.permissions
    assert RAG_GLOBAL_WRITE_PERMISSION in write_scope.permissions
    assert validate_rag_scope(read_scope, operation=RAG_READ) is read_scope
    assert validate_rag_scope(write_scope, operation=RAG_WRITE) is write_scope


def test_client_supplied_rag_scope_fields_are_rejected():
    reject_client_rag_scope_input({"query": "duplicate payment", "top_k": 3})

    with pytest.raises(RagAccessDenied, match="owner_user_id"):
        reject_client_rag_scope_input({"owner_user_id": 99})

    with pytest.raises(RagAccessDenied, match="global_access"):
        reject_client_rag_scope_input({"filters": {"global_access": True}})

    with pytest.raises(RagAccessDenied, match="user_id"):
        reject_client_rag_scope_input({"filters": [{"user_id": 99}]})


def test_background_jobs_require_explicit_scope_and_cannot_inherit_global_access():
    with pytest.raises(RagAccessDenied, match="trusted RAG scope"):
        validate_rag_scope(None, expected_source=RAG_SCOPE_SOURCE_BACKGROUND)

    job_scope = rag_scope_for_background(42)
    assert validate_rag_scope(
        job_scope,
        operation=RAG_READ,
        expected_source=RAG_SCOPE_SOURCE_BACKGROUND,
    ).owner_user_id == 42

    # A request-created scope must not satisfy a background job's source check.
    request_scope = rag_scope_for_user(_user(42, ROLE_USER))
    with pytest.raises(RagAccessDenied, match="source does not match"):
        validate_rag_scope(
            request_scope,
            expected_source=RAG_SCOPE_SOURCE_BACKGROUND,
        )

    global_job = rag_scope_for_background(
        None,
        permissions=frozenset({RAG_GLOBAL_READ_PERMISSION}),
        global_access=True,
        audit_reason="scheduled legacy audit",
    )
    assert validate_rag_scope(
        global_job,
        operation=RAG_READ,
        expected_source=RAG_SCOPE_SOURCE_BACKGROUND,
    ).is_global

    with pytest.raises(RagAccessDenied, match="requires rag:global_write"):
        validate_rag_scope(global_job, operation=RAG_WRITE)


def test_global_scope_requires_audit_reason_when_supplied():
    with pytest.raises(RagAccessDenied, match="audit reason"):
        RagScope(owner_user_id=1, audit_reason="   ")


@pytest.mark.parametrize(
    ("role", "expect_debug", "expect_global"),
    [
        (ROLE_ADMIN, True, True),
        (ROLE_USER, True, False),
        (ROLE_FINANCE_ANALYST, True, False),
        (ROLE_AUDITOR, True, False),
        (ROLE_REVIEWER, True, False),
        (ROLE_LENDER, False, False),
    ],
)
def test_rag_role_grants_follow_approved_policy(role, expect_debug, expect_global):
    permissions = role_permissions(role)

    assert ("rag:debug" in permissions) is expect_debug
    assert (RAG_GLOBAL_READ_PERMISSION in permissions) is expect_global
    assert (RAG_GLOBAL_WRITE_PERMISSION in permissions) is expect_global
