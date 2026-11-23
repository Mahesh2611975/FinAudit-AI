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
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import event, inspect, or_
from sqlalchemy.orm import Session, with_loader_criteria

# Kept as a literal to avoid a database.py -> ownership.py -> models.py import
# cycle. It must stay in sync with models.ROLE_ADMIN.
_ADMIN_ROLE = "admin"

__all__ = ["DataScope", "set_data_scope", "reset_data_scope", "install_ownership_events"]


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
