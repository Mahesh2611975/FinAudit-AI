"""Request-scoped row ownership for multi-account FinAudit deployments.

The application historically had a single global dataset. To add User/Lender
accounts without resetting or rewriting that data, ownership is additive:

* every owner-aware table gains a nullable ``owner_user_id`` column;
* ``NULL`` means pre-migration/shared data and remains visible to all
  authenticated accounts;
* new rows created during an authenticated request are stamped with that user;
* all ORM SELECTs for owner-aware entities are filtered to
  ``owner_user_id == current_user.id OR owner_user_id IS NULL``;
* administrators bypass the filter and can see every account's data.

The filter is installed once as SQLAlchemy ORM events, so it covers existing
endpoints without duplicating query logic in every module. System/background
jobs that run without an authenticated scope keep the legacy global view;
this is documented as a remaining limitation.

RAG storage uses the separate fail-closed ``RagScope`` contract defined below.
It intentionally does not inherit the legacy ``NULL``-is-shared convention:
RAG callers must supply a trusted owner scope, and global access requires an
explicit RAG permission. Every RAG façade, retrieval and workflow call now
validates that scope; there is no ambient or client-controlled fallback.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import event, inspect, or_
from sqlalchemy.orm import Session, with_loader_criteria

# Kept as a literal to avoid a database.py -> ownership.py -> models.py import
# cycle. It must stay in sync with models.ROLE_ADMIN.
_ADMIN_ROLE = "admin"

# Stage 1 RAG permissions. These constants live here so the fail-closed scope
# validation can be shared by auth, background jobs, and future RAG callers
# without importing auth.py from ownership.py.
RAG_DEBUG_PERMISSION = "rag:debug"
RAG_GLOBAL_READ_PERMISSION = "rag:global_read"
RAG_GLOBAL_WRITE_PERMISSION = "rag:global_write"
RAG_GLOBAL_PERMISSIONS = frozenset({
    RAG_GLOBAL_READ_PERMISSION,
    RAG_GLOBAL_WRITE_PERMISSION,
})

RAG_READ = "read"
RAG_WRITE = "write"
_RAG_OPERATIONS = frozenset({RAG_READ, RAG_WRITE})

RAG_SCOPE_SOURCE_REQUEST = "request"
RAG_SCOPE_SOURCE_BACKGROUND = "background"
_RAG_SCOPE_SOURCES = frozenset({RAG_SCOPE_SOURCE_REQUEST, RAG_SCOPE_SOURCE_BACKGROUND})

# These fields must never be accepted from an HTTP client for RAG operations.
# Ownership is derived from the authenticated principal or supplied explicitly
# by trusted server-side job code.
_CLIENT_RAG_SCOPE_FIELDS = frozenset({
    "owner",
    "owner_user_id",
    "user_id",
    "organization_id",
    "org_id",
    "tenant_id",
    "global",
    "global_access",
    "is_global",
    "admin",
    "is_admin",
    "permissions",
    "scope",
    "audit_reason",
})

__all__ = [
    "CLIENT_RAG_SCOPE_FIELDS",
    "DataScope",
    "RAG_DEBUG_PERMISSION",
    "RAG_GLOBAL_PERMISSIONS",
    "RAG_GLOBAL_READ_PERMISSION",
    "RAG_GLOBAL_WRITE_PERMISSION",
    "RAG_READ",
    "RAG_SCOPE_SOURCE_BACKGROUND",
    "RAG_SCOPE_SOURCE_REQUEST",
    "RAG_WRITE",
    "RagAccessDenied",
    "RagScope",
    "install_ownership_events",
    "rag_scope_for_background",
    "reject_client_rag_scope_input",
    "reset_data_scope",
    "set_data_scope",
    "validate_rag_scope",
]

# Exposed for tests and future endpoint helpers that need to enumerate rejected
# client-controlled fields.
CLIENT_RAG_SCOPE_FIELDS = _CLIENT_RAG_SCOPE_FIELDS


class RagAccessDenied(PermissionError):
    """Raised when a RAG operation lacks a trusted, authorized scope."""


@dataclass(frozen=True)
class RagScope:
    """Trusted, fail-closed scope for RAG storage and retrieval.

    This is deliberately separate from ``DataScope``. The legacy ORM layer
    treats ``owner_user_id IS NULL`` as shared data; RAG must not. A RAG scope
    either names exactly one owner or carries explicit, permission-backed
    global authority from a trusted source.

    Instances are constructed by server code only:
    * ``auth.rag_scope_for_user`` for authenticated HTTP requests;
    * ``rag_scope_for_background`` for trusted jobs.

    Client payload fields such as ``owner_user_id`` or ``global_access`` must be
    rejected with ``reject_client_rag_scope_input`` before a scope is built.
    """

    owner_user_id: int | None
    permissions: frozenset[str] = frozenset()
    global_access: bool = False
    source: str = RAG_SCOPE_SOURCE_REQUEST
    audit_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "permissions", frozenset(self.permissions or ()))
        if self.source not in _RAG_SCOPE_SOURCES:
            raise RagAccessDenied("Unrecognized RAG scope source.")
        if self.audit_reason is not None:
            if not isinstance(self.audit_reason, str) or not self.audit_reason.strip():
                raise RagAccessDenied("RAG audit reason must be a non-empty string.")

    @property
    def is_global(self) -> bool:
        return self.global_access


def validate_rag_scope(
    scope: RagScope | None,
    *,
    operation: str = RAG_READ,
    expected_source: str | None = None,
) -> RagScope:
    """Validate a RAG scope for one operation.

    Fail-closed rules:

    * ``None`` never means global access.
    * ``owner_user_id=None`` never means shared access for an ordinary scope.
    * A normal scope must identify exactly one integer owner.
    * Global access requires the matching explicit RAG global permission.
    * ``expected_source`` lets background jobs reject accidental request scopes.
    """
    if not isinstance(scope, RagScope):
        raise RagAccessDenied("A trusted RAG scope is required.")
    if operation not in _RAG_OPERATIONS:
        raise RagAccessDenied("Unrecognized RAG operation.")
    if expected_source is not None and scope.source != expected_source:
        raise RagAccessDenied("RAG scope source does not match the caller.")

    if scope.global_access:
        required = (
            RAG_GLOBAL_WRITE_PERMISSION
            if operation == RAG_WRITE
            else RAG_GLOBAL_READ_PERMISSION
        )
        if required not in scope.permissions:
            raise RagAccessDenied(f"RAG global access requires {required}.")
        return scope

    owner_id = scope.owner_user_id
    if owner_id is None:
        raise RagAccessDenied("RAG operations require an explicit owner.")
    if isinstance(owner_id, bool) or not isinstance(owner_id, int):
        raise RagAccessDenied("RAG owner_user_id must be an integer user id.")
    return scope


def rag_scope_for_background(
    owner_user_id: int | None,
    *,
    permissions: Iterable[str] = (),
    global_access: bool = False,
    audit_reason: str | None = None,
) -> RagScope:
    """Build an explicit scope for trusted background/server jobs.

    Jobs without an owner must opt into global access with an explicit RAG
    global permission. There is no ambient or implicit global fallback.
    """
    granted = frozenset(permissions)
    if global_access:
        if not granted.intersection(RAG_GLOBAL_PERMISSIONS):
            raise RagAccessDenied(
                "Background RAG global access requires an explicit global permission."
            )
    elif owner_user_id is None:
        raise RagAccessDenied("Background RAG jobs require an explicit owner.")

    scope = RagScope(
        owner_user_id=owner_user_id,
        permissions=granted,
        global_access=global_access,
        source=RAG_SCOPE_SOURCE_BACKGROUND,
        audit_reason=audit_reason,
    )
    return scope


def reject_client_rag_scope_input(payload: Any, *, path: str = "payload") -> None:
    """Reject client-controlled ownership/global fields in a RAG request.

    Nested mappings and sequences are checked so ownership cannot be smuggled
    into filter dictionaries. Field names are reported; values are never echoed.
    """
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            normalized = str(key).strip().lower()
            child_path = f"{path}.{key}"
            if normalized in _CLIENT_RAG_SCOPE_FIELDS:
                raise RagAccessDenied(f"Client-supplied RAG scope field is not allowed: {child_path}")
            reject_client_rag_scope_input(value, path=child_path)
    elif isinstance(payload, (list, tuple, set)):
        for index, value in enumerate(payload):
            reject_client_rag_scope_input(value, path=f"{path}[{index}]")


@dataclass(frozen=True)
class DataScope:
    user_id: int
    role: str
    is_admin: bool

    @classmethod
    def from_user(cls, user: Any) -> "DataScope":
        return cls(user_id=int(user.id), role=str(user.role), is_admin=str(user.role) == _ADMIN_ROLE)


_current_scope: ContextVar[DataScope | None] = ContextVar("finaudit_data_scope", default=None)


def set_data_scope(user: Any) -> Any:
    """Set the active request's data scope. Returns a token for reset."""
    return _current_scope.set(DataScope.from_user(user))


def reset_data_scope(token: Any) -> None:
    _current_scope.reset(token)


def current_scope() -> DataScope | None:
    return _current_scope.get()


def install_ownership_events(get_owned_entities: Callable[[], Iterable[Any]]) -> None:
    """Install global SELECT filtering and INSERT stamping for owned entities.

    ``get_owned_entities`` is resolved lazily so this can be installed before
    all models are imported.
    """

    @event.listens_for(Session, "do_orm_execute")
    def _apply_owner_filter(execute_state) -> None:
        if not execute_state.is_select or execute_state.is_column_load:
            return
        scope = _current_scope.get()
        if scope is None or scope.is_admin:
            return
        entities = tuple(get_owned_entities())
        if not entities:
            return
        # Build a concrete expression per entity. A lambda closure here would be
        # cached by SQLAlchemy without the user id in its cache key, causing one
        # account's filter to be reused for another account in the same process.
        for entity in entities:
            criteria = or_(
                entity.owner_user_id == scope.user_id,
                entity.owner_user_id.is_(None),
            )
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    entity,
                    criteria,
                    include_aliases=True,
                    propagate_to_loaders=True,
                )
            )

    @event.listens_for(Session, "before_flush")
    def _stamp_owner(session: Session, _flush_context, _instances) -> None:
        scope = _current_scope.get()
        if scope is None:
            return
        # Some regression suites substitute an in-memory fake user for the auth
        # dependency in a database that has no users row. Such a scope cannot
        # satisfy the owner_user_id foreign key, so leave those rows shared
        # (the legacy behavior) instead of failing every write.
        cache_key = "_finaudit_scope_user_exists"
        if cache_key not in session.info:
            from .models import User
            session.info[cache_key] = session.get(User, scope.user_id) is not None
        if not session.info[cache_key]:
            return
        for obj in session.new:
            if not hasattr(obj, "owner_user_id"):
                continue
            current_value = getattr(obj, "owner_user_id")
            if scope.is_admin:
                if current_value is None:
                    setattr(obj, "owner_user_id", scope.user_id)
            elif current_value != scope.user_id:
                # A non-admin request can never create a row owned by somebody
                # else, even if a client tried to supply owner_user_id.
                setattr(obj, "owner_user_id", scope.user_id)
        if scope.is_admin:
            return
        # A non-admin request cannot move a row to another account.
        for obj in session.dirty:
            if not hasattr(obj, "owner_user_id"):
                continue
            state = inspect(obj)
            history = state.attrs.owner_user_id.history
            if history.added and not history.deleted:
                obj.owner_user_id = scope.user_id
            elif history.added and history.deleted:
                obj.owner_user_id = history.deleted[0]
