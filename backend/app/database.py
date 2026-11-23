import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]

# Load backend/.env once, before any module reads configuration. load_dotenv()
# never overrides variables already provided by the shell, so explicit exports
# (and test fixtures) keep precedence.
load_dotenv(dotenv_path=BASE_DIR / ".env")

DATABASE_URL = os.getenv("FINAUDIT_DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'finaudit.db'}")
if DATABASE_URL.startswith("sqlite"):
    Path(DATABASE_URL.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _owned_entities():
    """All mapped tables carrying the additive owner_user_id column."""
    return tuple(
        mapper.class_
        for mapper in Base.registry.mappers
        if hasattr(mapper.class_, "owner_user_id")
    )


# Install request-scoped row filtering/stamping before any model queries run.
from .ownership import install_ownership_events  # noqa: E402

install_ownership_events(_owned_entities)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
