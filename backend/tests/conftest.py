import os
import tempfile
from types import SimpleNamespace

import pytest

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-pytest-")
os.environ.setdefault("FINAUDIT_DATABASE_URL", f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}")

# Import app after env var is set.
from app import auth as auth_module  # noqa: E402
from app.main import app  # noqa: E402
from app.ownership import reset_data_scope, set_data_scope  # noqa: E402
from app.rag_migrations import migrate_rag_ownership  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


DEFAULT_ADMIN = SimpleNamespace(
    id=1,
    username="test-admin",
    email="test-admin@example.test",
    full_name="Test Admin",
    role="admin",
    is_active=True,
)


@pytest.fixture
def scoped_vector_store(tmp_path):
    """A temporary RAG store with the additive ownership schema applied.

    This is test-only setup; the real RAG database and its backup are never
    migrated by the test suite.
    """
    store = VectorStore(tmp_path / "rag")
    migrate_rag_ownership(store.db_path)
    return store


@pytest.fixture(autouse=True)
def _default_admin_auth_override():
    """Keep existing endpoint tests functional after auth enforcement.

    Security-specific tests clear this override and exercise real auth.
    """
    async def _override_user():
        # Mirror the real dependency's request scope so ownership filters and
        # owner stamping remain active in endpoint regression tests.
        token = set_data_scope(DEFAULT_ADMIN)
        try:
            yield DEFAULT_ADMIN
        finally:
            reset_data_scope(token)

    app.dependency_overrides[auth_module.get_current_active_user] = _override_user
    yield
    app.dependency_overrides.pop(auth_module.get_current_active_user, None)
