"""Authentication, JWT handling and role-based access control.

Security notes:
- Passwords are hashed with bcrypt; plaintext passwords are never stored.
- JWTs are signed with FINAUDIT_JWT_SECRET. A development-only fallback is
  allowed solely in local environments; every non-local environment must
  provide a strong secret or startup/token operations fail closed with a 503.
- Tokens carry jti, sub (user id), role, and exp. Expired/invalid/revoked
  tokens are rejected.
- Reviewer identity is derived from the authenticated user, never from
  client-supplied free-text reviewer fields.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_FINANCE_ANALYST,
    ROLE_LENDER,
    ROLE_REVIEWER,
    ROLE_USER,
    RevokedToken,
    User,
)
from .ownership import (
    RAG_DEBUG_PERMISSION,
    RAG_GLOBAL_READ_PERMISSION,
    RAG_GLOBAL_WRITE_PERMISSION,
    RAG_READ,
    RAG_SCOPE_SOURCE_REQUEST,
    RAG_WRITE,
    RagAccessDenied,
    RagScope,
    reset_data_scope,
    set_data_scope,
    validate_rag_scope,
)

logger = logging.getLogger(__name__)

JWT_ALGORITHM = os.getenv("FINAUDIT_JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("FINAUDIT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
JWT_SECRET_MIN_LENGTH = 32
# This value is a public, insecure placeholder for local development only.
# It must never be used to protect a hosted database or a real deployment.
_DEV_JWT_SECRET = "finaudit-dev-insecure-secret-change-me"
_WEAK_JWT_SECRETS = frozenset({
    _DEV_JWT_SECRET,
    "change-me",
    "changeme",
    "secret",
    "your-secret-key",
    "replace-with-strong-secret",
})
_local_fallback_warned = False

# Local demonstration mode is an explicit, opt-in developer convenience. It
# never creates users and never relaxes a route policy: the browser receives a
# short-lived signed JWT for an in-memory demo principal, then every endpoint
# continues through the normal authentication/RBAC dependencies.
DEMO_USER_ID = -1
DEMO_USERNAME = "local-demo"
DEMO_TOKEN_EXPIRE_MINUTES = max(1, int(os.getenv("FINAUDIT_DEMO_TOKEN_MINUTES", "30")))
_TRUTHY = {"1", "true", "yes", "on"}
_LOCAL_ENVIRONMENTS = {"local", "dev", "development", "test"}


def _environment_name() -> str:
    """Return the normalized environment name; local-first when unset."""
    return os.getenv("FINAUDIT_ENV", os.getenv("APP_ENV", "local")).strip().lower()


def demo_mode_enabled() -> bool:
    """Return True only for an explicit local demo opt-in in a local environment.

    Absence of ``FINAUDIT_ENV`` is treated as local because this is a
    local-first application. Any production-like environment name disables
    demo mode even if the opt-in flag is accidentally present.
    """
    enabled = os.getenv("FINAUDIT_LOCAL_DEMO_MODE", "").strip().lower() in _TRUTHY
    if not enabled:
        return False
    return _environment_name() in _LOCAL_ENVIRONMENTS

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PASSWORD_RULE = "at least 8 characters with uppercase, lowercase, a digit and a special character"

# Self-service registration accepts only an account type (User or Lender).
# The server maps it to a role; no client-supplied role is ever trusted.
ACCOUNT_TYPE_ROLES = {
    "user": ROLE_USER,
    "lender": ROLE_LENDER,
}
# Legacy roles stay approvable so accounts created by older builds can still be
# processed. Admin is deliberately absent: it is never approvable or self-served.
APPROVABLE_ROLES = (
    ROLE_USER,
    ROLE_LENDER,
    ROLE_AUDITOR,
    ROLE_FINANCE_ANALYST,
    ROLE_REVIEWER,
)

ROLE_DASHBOARDS = {
    ROLE_ADMIN: "admin-dashboard",
    ROLE_LENDER: "lender-dashboard",
    ROLE_USER: "user-dashboard",
    ROLE_AUDITOR: "user-dashboard",
    ROLE_FINANCE_ANALYST: "user-dashboard",
    ROLE_REVIEWER: "user-dashboard",
}


def dashboard_for_role(role: str) -> str:
    return ROLE_DASHBOARDS.get(str(role), "user-dashboard")


class JWTConfigurationError(RuntimeError):
    """Raised when a non-local deployment has no usable JWT signing secret."""


def _jwt_is_local_environment() -> bool:
    """Return True only for a local environment backed by local storage.

    An unset environment name is local because this is a local-first app. A
    non-SQLite database URL is treated as a hosted deployment even when the
    environment name was forgotten, so the development secret cannot silently
    protect a real database.
    """
    if _environment_name() not in _LOCAL_ENVIRONMENTS:
        return False
    database_url = os.getenv("FINAUDIT_DATABASE_URL", "").strip().lower()
    return not database_url or database_url.startswith("sqlite")


def validate_jwt_configuration() -> str:
    """Return a usable signing secret or fail closed outside local development.

    The secret value is never included in logs or error messages.
    """
    global _local_fallback_warned
    secret = os.getenv("FINAUDIT_JWT_SECRET", "").strip()
    environment = _environment_name() or "unset"

    if not secret and _jwt_is_local_environment():
        if not _local_fallback_warned:
            logger.warning(
                "FINAUDIT_JWT_SECRET is not set; using a development-only secret "
                "because FINAUDIT_ENV=%r is local. Set a strong secret before deploying.",
                environment,
            )
            _local_fallback_warned = True
        return _DEV_JWT_SECRET

    if not secret:
        raise JWTConfigurationError(
            "FINAUDIT_JWT_SECRET is required in non-local environments "
            f"(FINAUDIT_ENV={environment!r}). Generate one with "
            "'python -c \"import secrets; print(secrets.token_urlsafe(48))\"' "
            "and configure it as a deployment secret."
        )

    if (
        len(secret) < JWT_SECRET_MIN_LENGTH
        or secret.lower() in _WEAK_JWT_SECRETS
        or len(set(secret)) < 4
    ):
        raise JWTConfigurationError(
            "FINAUDIT_JWT_SECRET is too weak for a non-local environment "
            f"(FINAUDIT_ENV={environment!r}). Use at least {JWT_SECRET_MIN_LENGTH} "
            "random characters from secrets.token_urlsafe(48); the value is never logged."
        )
    return secret


def _jwt_secret() -> str:
    """Return the configured secret, with the local-only development fallback."""
    return validate_jwt_configuration()


def _jwt_secret_for_crypto() -> str:
    """Convert secret misconfiguration into a safe 503 for request handling."""
    try:
        return _jwt_secret()
    except JWTConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Role / permission model
# ---------------------------------------------------------------------------

PERMISSIONS: dict[str, set[str]] = {
    ROLE_ADMIN: {
        "users:read", "users:write",
        "data:import", "data:read", "data:export",
        "audit:read", "audit:run",
        "reconciliation:read", "reconciliation:run", "reconciliation:review", "reconciliation:approve",
        "risk:read", "risk:analyze", "risk:review",
        "investigation:read", "investigation:write",
        "invoice:read", "invoice:write", "invoice:review",
        "reports:read", "assistant:use", "documents:write", "documents:read",
        RAG_DEBUG_PERMISSION, RAG_GLOBAL_READ_PERMISSION, RAG_GLOBAL_WRITE_PERMISSION,
        "settings:read", "settings:write",
    },
    # New self-service account type: internal company user. Deliberately has no
    # approval/review or user-management powers.
    ROLE_USER: {
        "data:import", "data:read", "data:export",
        "audit:read", "audit:run",
        "reconciliation:read", "reconciliation:run",
        "risk:read", "risk:analyze",
        "investigation:read", "investigation:write",
        "invoice:read", "invoice:write",
        "reports:read", "assistant:use", "documents:write", "documents:read",
        RAG_DEBUG_PERMISSION,
        "settings:read",
    },
    # Lenders only work with invoice evidence for their own account. They do not
    # receive company-wide transaction, reconciliation, risk, case or report data.
    ROLE_LENDER: {
        "invoice:read", "invoice:write",
        "documents:read", "documents:write",
        "settings:read",
    },
    ROLE_FINANCE_ANALYST: {
        "data:import", "data:read", "data:export",
        "audit:read", "audit:run",
        "reconciliation:read", "reconciliation:run",
        "risk:read", "risk:analyze",
        "investigation:read", "investigation:write",
        "invoice:read", "invoice:write",
        "reports:read", "assistant:use", "documents:write", "documents:read",
        RAG_DEBUG_PERMISSION,
        "settings:read",
    },
    ROLE_AUDITOR: {
        "data:read", "data:export",
        "audit:read",
        "reconciliation:read",
        "risk:read",
        "investigation:read", "investigation:write",
        "invoice:read",
        "reports:read", "assistant:use", "documents:read",
        RAG_DEBUG_PERMISSION,
        "settings:read",
    },
    ROLE_REVIEWER: {
        "data:read", "data:export",
        "audit:read",
        "reconciliation:read", "reconciliation:review", "reconciliation:approve",
        "risk:read", "risk:review",
        "investigation:read", "investigation:write",
        "invoice:read", "invoice:review",
        "reports:read", "assistant:use", "documents:read",
        RAG_DEBUG_PERMISSION,
        "settings:read",
    },
}


def role_permissions(role: str) -> set[str]:
    return set(PERMISSIONS.get(role, set()))


def has_permission(user: User, permission: str) -> bool:
    return permission in role_permissions(str(user.role))


def rag_scope_for_user(
    user: User,
    *,
    operation: str = RAG_READ,
    global_access: bool = False,
    audit_reason: str | None = None,
) -> RagScope:
    """Build a trusted RAG scope from an already-authenticated user.

    Call this only with the principal resolved by ``get_current_active_user``
    (or a permission dependency). The owner id and permissions come from that
    principal, never from request payload data. Global access additionally
    requires the explicit RAG global permission for the requested operation.
    """
    if operation not in (RAG_READ, RAG_WRITE):
        raise RagAccessDenied("Unrecognized RAG operation.")
    if getattr(user, "is_active", True) is False:
        raise RagAccessDenied("Inactive users cannot establish a RAG scope.")

    permissions = role_permissions(str(user.role))
    if global_access:
        required = (
            RAG_GLOBAL_WRITE_PERMISSION if operation == RAG_WRITE else RAG_GLOBAL_READ_PERMISSION
        )
        if required not in permissions:
            raise RagAccessDenied(f"RAG global access requires {required}.")

    try:
        owner_user_id = int(user.id)
    except (TypeError, ValueError) as exc:
        raise RagAccessDenied("Authenticated user has no valid integer id.") from exc

    scope = RagScope(
        owner_user_id=owner_user_id,
        permissions=frozenset(permissions),
        global_access=global_access,
        source=RAG_SCOPE_SOURCE_REQUEST,
        audit_reason=audit_reason,
    )
    return validate_rag_scope(scope, operation=operation)


def approval_status(user: User) -> str:
    """Derive provisioning state from the existing, additive user columns.

    ``is_active`` is the access gate. ``is_provisioned`` distinguishes a brand
    new self-registration (pending) from an approved account that an admin has
    temporarily disabled. No schema change is required, so existing users and
    their data are untouched.
    """
    if user.is_active:
        return "approved"
    if not user.is_provisioned:
        return "pending_approval"
    return "disabled"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if len(password.encode("utf-8")) > 72:
        # bcrypt truncates at 72 bytes; reject rather than silently truncate.
        raise ValueError("Password must be at most 72 bytes when UTF-8 encoded.")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    if not isinstance(password, str) or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def ensure_unique_identity(db: Session, username: str, email: str | None, *, exclude_user_id: int | None = None) -> None:
    """Reject duplicate usernames/emails case-insensitively with a clear 409.

    SQLite's UNIQUE constraint is case-sensitive, so checking only exact matches
    would allow ``Admin`` and ``admin`` to coexist and make login ambiguous.
    """
    username_query = db.query(User.id).filter(func.lower(User.username) == username.strip().lower())
    if exclude_user_id is not None:
        username_query = username_query.filter(User.id != exclude_user_id)
    if username_query.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists.")

    if email:
        email_query = db.query(User.id).filter(func.lower(User.email) == email.strip().lower())
        if exclude_user_id is not None:
            email_query = email_query.filter(User.id != exclude_user_id)
        if email_query.first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists.")


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user: User, expires_minutes: int | None = None) -> tuple[str, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "type": "access",
    }
    token = jwt.encode(payload, _jwt_secret_for_crypto(), algorithm=JWT_ALGORITHM)
    return token, payload


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _jwt_secret_for_crypto(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "jti", "role"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.") from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    return payload


def build_demo_user() -> User:
    """Return an unsaved admin-shaped principal for local demonstration only.

    The object is never added to a Session, so no user row, password hash,
    revoked-token row or financial record is created by demo mode. Its id does
    not exist in the users table; ownership code already treats such a scope as
    legacy/shared data rather than attempting to stamp a foreign key.
    """
    return User(
        id=DEMO_USER_ID,
        username=DEMO_USERNAME,
        email=None,
        full_name="Local Demo",
        hashed_password="",
        role=ROLE_ADMIN,
        is_active=True,
        is_provisioned=True,
    )


def create_demo_token() -> tuple[str, dict[str, Any]]:
    if not demo_mode_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Local demo mode is disabled.")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=DEMO_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(DEMO_USER_ID),
        "username": DEMO_USERNAME,
        "role": ROLE_ADMIN,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "type": "demo",
        "demo": True,
    }
    token = jwt.encode(payload, _jwt_secret_for_crypto(), algorithm=JWT_ALGORITHM)
    return token, payload


def decode_demo_token(token: str) -> dict[str, Any]:
    if not demo_mode_enabled():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Local demo mode is disabled.")
    try:
        payload = jwt.decode(
            token,
            _jwt_secret_for_crypto(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "jti", "role", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Demo session has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid demo session.") from exc
    if payload.get("type") != "demo" or not payload.get("demo"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid demo session.")
    return payload


def _looks_like_demo_token(token: str) -> bool:
    """Inspect claims without trusting them; used only to reject demo tokens.

    This never authenticates anything. It lets a disabled demo principal be
    rejected as an invalid token before any signing-secret lookup, so demo-mode
    behaviour is unchanged even when a hosted deployment is misconfigured.
    """
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return False
    return payload.get("type") == "demo" or bool(payload.get("demo"))


def decode_request_token(token: str) -> tuple[dict[str, Any], bool]:
    """Decode a normal access token, falling back to a gated demo token.

    A failed normal decode is only replaced by demo validation when local demo
    mode is explicitly enabled. With demo mode disabled this behaves exactly as
    the original access-token path, so production security is unchanged.
    """
    if not demo_mode_enabled() and _looks_like_demo_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    try:
        return decode_access_token(token), False
    except HTTPException as access_error:
        if not demo_mode_enabled():
            raise
        try:
            return decode_demo_token(token), True
        except HTTPException:
            # Preserve the generic auth error for an invalid/expired token.
            raise access_error


def is_token_revoked(db: Session, jti: str) -> bool:
    return db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first() is not None


def revoke_token(db: Session, payload: dict[str, Any], *, user_id: int | None, reason: str) -> None:
    jti = str(payload.get("jti") or "")
    if not jti:
        return
    if is_token_revoked(db, jti):
        return
    exp = payload.get("exp")
    expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    db.add(
        RevokedToken(
            jti=jti,
            user_id=user_id,
            token_type=str(payload.get("type") or "access"),
            revoked_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            reason=reason,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _user_from_payload(db: Session, payload: dict[str, Any], *, is_demo: bool = False) -> User:
    jti = str(payload.get("jti") or "")
    if not jti or is_token_revoked(db, jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication token has been revoked.")
    if is_demo or payload.get("type") == "demo":
        if not demo_mode_enabled():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Local demo mode is disabled.")
        return build_demo_user()
    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.") from exc
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload, is_demo = decode_request_token(credentials.credentials)
    return _user_from_payload(db, payload, is_demo=is_demo)


async def get_current_active_user(user: User = Depends(get_current_user)) -> AsyncIterator[User]:
    """Return the active user and install the request's row-level data scope.

    This is a yield dependency so the ContextVar is always reset, even when an
    endpoint raises. Every protected router depends on it.
    """
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")
    token = set_data_scope(user)
    try:
        yield user
    finally:
        reset_data_scope(token)


# ---------------------------------------------------------------------------
# Centralized route authorization
# ---------------------------------------------------------------------------
# Rules are evaluated in order against the request path. ``None`` means any
# authenticated active account may call the route. An unmatched protected route
# is denied, so adding a new endpoint cannot silently become world-readable to
# every role.

ROUTE_PERMISSIONS: tuple[tuple[str, str, str | None], ...] = (
    # Main application: data import, audit, reconciliation and analytics.
    ("GET", r"/api/system/health", None),
    ("POST", r"/api/transactions/preview", "data:import"),
    ("POST", r"/api/audits", "audit:run"),
    ("GET", r"/api/audits", "audit:read"),
    ("GET", r"/api/audits/\d+", "audit:read"),
    ("POST", r"/api/findings/\d+/explain", "audit:read"),
    ("GET", r"/api/audits/\d+/export\.csv", "data:export"),
    ("POST", r"/api/reconciliations/preview", "reconciliation:run"),
    ("POST", r"/api/reconciliations", "reconciliation:run"),
    ("GET", r"/api/reconciliations", "reconciliation:read"),
    ("GET", r"/api/reconciliations/\d+", "reconciliation:read"),
    ("GET", r"/api/reconciliations/\d+/export\.csv", "data:export"),
    ("POST", r"/api/reconciliations/results/\d+/review", "reconciliation:review"),
    ("POST", r"/api/reconciliations/results/\d+/confirm", "reconciliation:approve"),
    ("POST", r"/api/reconciliations/\d+/manual-link", "reconciliation:approve"),
    ("GET", r"/api/analytics/export\.csv", "data:export"),
    ("GET", r"/api/analytics/.+", "data:read"),
    # Main application: agents, cases, transactions, documents and reports.
    ("GET", r"/api/agents/.+", "assistant:use"),
    ("POST", r"/api/agents/.+", "assistant:use"),
    ("GET", r"/api/cases", "investigation:read"),
    ("POST", r"/api/cases", "investigation:write"),
    ("GET", r"/api/dashboard/overview", "data:read"),
    ("GET", r"/api/transactions", "data:read"),
    ("GET", r"/api/transactions/export\.csv", "data:export"),
    ("GET", r"/api/transactions/[^/]+", "data:read"),
    ("POST", r"/api/documents/(ingest|sync-transactions|reindex)", "documents:write"),
    ("DELETE", r"/api/documents/.+", "documents:write"),
    ("GET", r"/api/documents", "documents:read"),
    ("GET", r"/api/documents/.+", "documents:read"),
    ("POST", r"/api/documents/retrieval-test", RAG_DEBUG_PERMISSION),
    ("GET", r"/api/assistant/suggestions", "assistant:use"),
    ("GET", r"/api/assistant/config", "settings:read"),
    ("POST", r"/api/assistant/query", "assistant:use"),
    ("POST", r"/api/vendors/\d+/investigate", "assistant:use"),
    ("GET", r"/api/reports/.+", "data:export"),
    # Invoice / lender-facing evidence surface.
    ("POST", r"/api/(vendors|purchase-orders|goods-receipts)(/import)?", "invoice:write"),
    ("GET", r"/api/(vendors|purchase-orders|goods-receipts)(/.+)?", "invoice:read"),
    ("POST", r"/api/invoices/upload", "invoice:write"),
    ("POST", r"/api/invoices", "invoice:write"),
    ("GET", r"/api/invoices(.+)?", "invoice:read"),
    ("POST", r"/api/invoices/\d+/(payments|audit)", "invoice:write"),
    ("POST", r"/api/invoice-payments/\d+/verify", "invoice:write"),
    ("POST", r"/api/invoice-audits/run", "invoice:write"),
    ("GET", r"/api/invoice-audits/.+", "invoice:read"),
    ("GET", r"/api/discrepancies(.+)?", "invoice:read"),
    ("POST", r"/api/discrepancies/\d+/review", "invoice:review"),
    ("GET", r"/api/invoice-analytics/.+", "invoice:read"),
    # Risk intelligence.
    ("GET", r"/api/risk/.+", "risk:read"),
    ("POST", r"/api/risk/alerts/[^/]+/review", "risk:review"),
    ("POST", r"/api/risk/analyze", "risk:analyze"),
    ("POST", r"/api/risk/investigate", "investigation:write"),
    # Investigation cases.
    ("GET", r"/api/investigations", "investigation:read"),
    ("POST", r"/api/investigations", "investigation:write"),
    ("POST", r"/api/investigations/from-alert/.+", "investigation:write"),
    ("GET", r"/api/investigations/.+", "investigation:read"),
    ("POST", r"/api/investigations/.+/(notes|evidence|explain)", "investigation:write"),
    ("PATCH", r"/api/investigations/.+", "investigation:write"),
)


async def enforce_route_permissions(
    request: Request,
    user: User = Depends(get_current_active_user),
) -> User:
    """Apply the central route policy in addition to endpoint dependencies."""
    path = request.url.path.rstrip("/") or "/"
    for method, pattern, permission in ROUTE_PERMISSIONS:
        if method != request.method or re.fullmatch(pattern, path) is None:
            continue
        if permission is None or permission in role_permissions(str(user.role)):
            return user
        logger.warning(
            "Authorization denied: user=%s role=%s method=%s path=%s required=%s",
            user.username, user.role, request.method, path, permission,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")
    # Unknown protected routes fail closed rather than inheriting broad access.
    logger.warning(
        "Authorization denied (no route policy): user=%s role=%s method=%s path=%s",
        user.username, user.role, request.method, path,
    )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")


def require_roles(*roles: str):
    allowed = set(roles)

    async def _dep(user: User = Depends(get_current_active_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role permissions.")
        return user

    return _dep


def require_permissions(*permissions: str):
    required = set(permissions)

    async def _dep(user: User = Depends(get_current_active_user)) -> User:
        if not required.issubset(role_permissions(str(user.role))):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")
        return user

    return _dep


def actor_name(user: User) -> str:
    return user.username or f"user-{user.id}"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=128)


class UserPublic(BaseModel):
    id: int
    username: str
    email: str | None = None
    full_name: str | None = None
    role: str
    dashboard: str
    permissions: list[str] = Field(default_factory=list)
    is_active: bool
    is_provisioned: bool = False
    is_demo: bool = False
    approval_status: str = "approved"
    created_at: str | None = None
    last_login_at: str | None = None

    @classmethod
    def from_model(cls, user: User) -> "UserPublic":
        role = str(user.role)
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=role,
            dashboard=dashboard_for_role(role),
            permissions=sorted(role_permissions(role)),
            is_active=bool(user.is_active),
            is_provisioned=bool(user.is_provisioned),
            is_demo=str(user.username) == DEMO_USERNAME,
            approval_status=approval_status(user),
            created_at=user.created_at.isoformat() if user.created_at else None,
            last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        )


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserPublic


class DemoSessionResponse(LoginResponse):
    demo: bool = True


class RegisterRequest(BaseModel):
    """Public self-service account request. A role can never be supplied here."""

    # Reject unexpected fields (notably ``role``) instead of silently ignoring
    # them. The server derives the role exclusively from account_type.
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)
    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)
    account_type: str = Field(..., min_length=1, max_length=20)

    @field_validator("account_type")
    @classmethod
    def _validate_account_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ACCOUNT_TYPE_ROLES:
            raise ValueError("Account type must be either User or Lender.")
        return normalized

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Full name is required.")
        return value

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str) -> str:
        value = value.strip()
        if not _USERNAME_RE.fullmatch(value):
            raise ValueError("Username may contain only letters, numbers, dots, underscores and hyphens.")
        return value

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL_RE.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value

    @model_validator(mode="after")
    def _validate_password(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        if len(self.password.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes when UTF-8 encoded.")
        if not (
            any(char.isupper() for char in self.password)
            and any(char.islower() for char in self.password)
            and any(char.isdigit() for char in self.password)
            and any(not char.isalnum() for char in self.password)
        ):
            raise ValueError(f"Password must contain {_PASSWORD_RULE}.")
        return self


class RegisterResponse(BaseModel):
    user: UserPublic
    message: str


class ApprovalRequest(BaseModel):
    approved: bool
    role: str | None = Field(default=None, max_length=40)


class UpdateUserRequest(BaseModel):
    is_active: bool | None = None
    role: str | None = Field(default=None, max_length=40)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    role: str = Field(..., min_length=1, max_length=40)
    is_active: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    username = payload.username.strip()
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        # Do not reveal whether username or password was wrong.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    if not user.is_active:
        if not user.is_provisioned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is pending administrator approval.",
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive.")
    user.last_login_at = datetime.now(timezone.utc)
    token, _payload = create_access_token(user)
    db.commit()
    db.refresh(user)
    return LoginResponse(
        access_token=token,
        expires_in_minutes=ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserPublic.from_model(user),
    )


@router.get("/demo/status")
def demo_status() -> dict[str, bool]:
    """Report whether this server opted into local demonstration mode."""
    return {"enabled": demo_mode_enabled()}


@router.post("/demo/session", response_model=DemoSessionResponse)
def demo_session() -> DemoSessionResponse:
    """Issue a short-lived signed demo session without touching the database.

    This route is intentionally inert unless the local-only opt-in is enabled.
    The returned token is a real JWT that passes the same authentication and
    RBAC dependencies as a login token; only its principal is in-memory.
    """
    if not demo_mode_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Local demo mode is disabled.")
    token, _payload = create_demo_token()
    return DemoSessionResponse(
        access_token=token,
        expires_in_minutes=DEMO_TOKEN_EXPIRE_MINUTES,
        user=UserPublic.from_model(build_demo_user()),
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create an unapproved, least-privilege account from public sign-up data.

    The account is deliberately inactive and unprovisioned: it cannot obtain a
    token until an administrator approves it. No role or active flag is accepted
    from the client.
    """
    username = payload.username.strip()
    email = payload.email.strip().lower()
    ensure_unique_identity(db, username, email)

    try:
        hashed = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    user = User(
        username=username,
        email=email,
        full_name=payload.full_name.strip(),
        hashed_password=hashed,
        role=ACCOUNT_TYPE_ROLES[payload.account_type],
        is_active=False,
        is_provisioned=False,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists.",
        ) from exc
    db.refresh(user)
    logger.info(
        "Self-registration submitted: id=%s username=%s email=%s role=%s status=pending_approval",
        user.id, user.username, user.email, user.role,
    )
    return RegisterResponse(
        user=UserPublic.from_model(user),
        message="Registration submitted. An administrator must approve your account before you can sign in.",
    )


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    payload, is_demo = decode_request_token(credentials.credentials)
    # Demo sessions are in-memory and intentionally leave no revocation row.
    if is_demo:
        return {"detail": "Demo session cleared."}
    # If user is missing/inactive, still allow revocation of the token.
    user_id = None
    try:
        user_id = int(payload.get("sub"))
    except Exception:
        user_id = None
    revoke_token(db, payload, user_id=user_id, reason="logout")
    return {"detail": "Logged out."}


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_active_user)):
    return UserPublic.from_model(user)


@router.get("/roles")
async def roles(user: User = Depends(get_current_active_user)):
    return {
        "roles": [
            {"role": role, "permissions": sorted(perms)}
            for role, perms in PERMISSIONS.items()
        ]
    }


@router.get("/users", response_model=list[UserPublic])
async def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(ROLE_ADMIN)),
):
    """List accounts and their approval status (admin only)."""
    logger.info("Admin %s listed users", admin.username)
    return [UserPublic.from_model(user) for user in db.query(User).order_by(User.id).all()]


@router.post("/users", response_model=UserPublic)
async def create_user(
    payload: CreateUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Explicitly provision an account. Only an admin may choose the role."""
    role = payload.role.strip().lower()
    if role not in ALL_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {list(ALL_ROLES)}")
    username = payload.username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=422, detail="Username may contain only letters, numbers, dots, underscores and hyphens.")
    email = payload.email.strip().lower() if payload.email else None
    ensure_unique_identity(db, username, email)
    try:
        hashed = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = User(
        username=username,
        email=email,
        full_name=payload.full_name.strip() if payload.full_name else None,
        hashed_password=hashed,
        role=role,
        is_active=bool(payload.is_active),
        is_provisioned=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email already exists.") from exc
    db.refresh(user)
    logger.info(
        "Admin %s provisioned user %s with role %s and active=%s",
        admin.username, user.username, user.role, user.is_active,
    )
    return UserPublic.from_model(user)


@router.post("/users/{user_id}/approval", response_model=UserPublic)
async def set_user_approval(
    user_id: int,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Approve or reject a self-registered account (admin only).

    Approval assigns a non-admin role by default. Granting admin is a separate,
    explicit action via ``PATCH /api/auth/users/{user_id}`` or the create_admin
    CLI; it is never implied by this workflow.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    role = user.role
    if payload.role is not None:
        role = payload.role.strip().lower()
        if role not in APPROVABLE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Approval role must be one of {list(APPROVABLE_ROLES)}.",
            )

    action = "approved" if payload.approved else "rejected"
    logger.info(
        "Admin %s %s user %s (id=%s); previous role=%s active=%s provisioned=%s",
        admin.username, action, user.username, user.id, user.role, user.is_active, user.is_provisioned,
    )
    user.is_active = bool(payload.approved)
    user.is_provisioned = bool(payload.approved)
    user.role = role
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return UserPublic.from_model(user)


@router.patch("/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Explicitly change a user's role or active state (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    changes: list[str] = []
    if payload.role is not None:
        role = payload.role.strip().lower()
        if role not in ALL_ROLES:
            raise HTTPException(status_code=422, detail=f"role must be one of {list(ALL_ROLES)}")
        changes.append(f"role {user.role}->{role}")
        user.role = role
    if payload.is_active is not None:
        changes.append(f"active {user.is_active}->{payload.is_active}")
        user.is_active = bool(payload.is_active)
        if payload.is_active:
            user.is_provisioned = True
    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied.")
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    logger.info("Admin %s updated user %s (id=%s): %s", admin.username, user.username, user.id, ", ".join(changes))
    return UserPublic.from_model(user)
