import json
import sqlite3
from pathlib import Path

import pytest

from app.rag_migrations import (
    OWNER_INDEXES,
    RAG_DOCUMENTS_TABLE,
    RAG_OWNER_COLUMN,
    RAG_CHUNKS_TABLE,
    RAGMigrationError,
    inspect_rag_migration_state,
    migrate_rag_ownership,
)

DOCUMENT_COLUMNS = (
    "doc_id",
    "source_filename",
    "document_type",
    "file_hash",
    "chunk_count",
    "upload_timestamp",
    "metadata",
)
CHUNK_COLUMNS = (
    "id",
    "doc_id",
    "source_filename",
    "document_type",
    "page_number",
    "transaction_id",
    "chunk_index",
    "text",
    "text_hash",
    "upload_timestamp",
)


def _create_old_schema(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            """
            CREATE TABLE rag_documents (
                doc_id TEXT PRIMARY KEY,
                source_filename TEXT NOT NULL,
                document_type TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                upload_timestamp TEXT NOT NULL,
                metadata TEXT
            );
            CREATE TABLE rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                document_type TEXT NOT NULL,
                page_number INTEGER,
                transaction_id TEXT,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                upload_timestamp TEXT NOT NULL,
                UNIQUE(doc_id, chunk_index, text_hash)
            );
            CREATE INDEX ix_rag_chunks_doc ON rag_chunks(doc_id);
            CREATE INDEX ix_rag_chunks_txn ON rag_chunks(transaction_id);
            """
        )
        con.executemany(
            "INSERT INTO rag_documents ("
            + ", ".join(DOCUMENT_COLUMNS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("doc-a", "alpha.txt", "text_report", "hash-a", 2, "2026-01-01T00:00:00", None),
                ("doc-b", "beta.txt", "invoice", "hash-b", 2, "2026-01-02T00:00:00", "{}"),
            ],
        )
        con.executemany(
            "INSERT INTO rag_chunks ("
            + ", ".join(CHUNK_COLUMNS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "doc-a", "alpha.txt", "text_report", None, None, 0, "alpha one", "th-a1", "2026-01-01T00:00:00"),
                (2, "doc-a", "alpha.txt", "text_report", None, None, 1, "alpha two", "th-a2", "2026-01-01T00:00:00"),
                (3, "doc-b", "beta.txt", "invoice", 1, "TXN-1", 0, "beta one", "th-b1", "2026-01-02T00:00:00"),
                (4, "doc-b", "beta.txt", "invoice", 1, "TXN-1", 1, "beta two", "th-b2", "2026-01-02T00:00:00"),
            ],
        )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path) -> dict[str, list[tuple]]:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            RAG_DOCUMENTS_TABLE: con.execute(
                f"SELECT {', '.join(DOCUMENT_COLUMNS)} FROM {RAG_DOCUMENTS_TABLE} ORDER BY doc_id"
            ).fetchall(),
            RAG_CHUNKS_TABLE: con.execute(
                f"SELECT {', '.join(CHUNK_COLUMNS)} FROM {RAG_CHUNKS_TABLE} ORDER BY id"
            ).fetchall(),
        }
    finally:
        con.close()


def _indexes(db_path: Path, table: str) -> dict[str, tuple[str, ...]]:
    con = sqlite3.connect(str(db_path))
    try:
        result = {}
        for row in con.execute(f'PRAGMA index_list("{table}")'):
            name = str(row[1])
            result[name] = tuple(
                str(info[2]) for info in con.execute(f'PRAGMA index_info("{name}")')
            )
        return result
    finally:
        con.close()


def test_old_schema_migration_adds_columns_and_indexes_without_changing_rows(tmp_path):
    db_path = tmp_path / "rag.sqlite3"
    _create_old_schema(db_path)
    rows_before = _rows(db_path)

    before = inspect_rag_migration_state(db_path)
    assert before["status"] == "missing"
    assert before["counts"] == {"documents": 2, "chunks": 4}

    report = migrate_rag_ownership(db_path)

    assert report["status"] == "applied"
    assert report["initial_status"] == "missing"
    assert report["final_status"] == "current"
    assert report["counts_before"] == {"documents": 2, "chunks": 4}
    assert report["counts_after"] == {"documents": 2, "chunks": 4}
    assert report["rows_unchanged"] is True
    assert report["constraints_preserved"] is True
    assert report["integrity_check"] == "ok"
    assert set(report["added_columns"]) == {
        f"{RAG_DOCUMENTS_TABLE}.{RAG_OWNER_COLUMN}",
        f"{RAG_CHUNKS_TABLE}.{RAG_OWNER_COLUMN}",
    }
    assert set(report["created_indexes"]) == {spec.name for spec in OWNER_INDEXES}
    assert report["owner_null_counts_after"] == {
        RAG_DOCUMENTS_TABLE: 2,
        RAG_CHUNKS_TABLE: 4,
    }

    after = inspect_rag_migration_state(db_path)
    assert after["status"] == "current"
    assert after["errors"] == []
    assert _rows(db_path) == rows_before

    con = sqlite3.connect(str(db_path))
    try:
        for table in (RAG_DOCUMENTS_TABLE, RAG_CHUNKS_TABLE):
            owner = con.execute(f'PRAGMA table_info("{table}")').fetchall()
            match = [column for column in owner if column[1] == RAG_OWNER_COLUMN]
            assert len(match) == 1
            assert match[0][2].upper() == "INTEGER"
            assert match[0][3] == 0
            assert match[0][4] is None
            assert con.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE {RAG_OWNER_COLUMN} IS NOT NULL'
            ).fetchone()[0] == 0

        assert con.execute(
            "SELECT COUNT(*) FROM (SELECT doc_id FROM rag_documents GROUP BY doc_id HAVING COUNT(*) > 1)"
        ).fetchone()[0] == 0
    finally:
        con.close()

    for spec in OWNER_INDEXES:
        assert _indexes(db_path, spec.table)[spec.name] == spec.columns


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "rag.sqlite3"
    _create_old_schema(db_path)
    rows_before = _rows(db_path)

    first = migrate_rag_ownership(db_path)
    second = migrate_rag_ownership(db_path)

    assert first["status"] == "applied"
    assert second["status"] == "already_current"
    assert second["initial_status"] == "current"
    assert second["added_columns"] == []
    assert second["created_indexes"] == []
    assert second["counts_before"] == second["counts_after"] == {"documents": 2, "chunks": 4}
    assert second["integrity_check"] == "ok"
    assert _rows(db_path) == rows_before


def test_partial_migration_is_detected_and_repaired(tmp_path):
    db_path = tmp_path / "rag.sqlite3"
    _create_old_schema(db_path)
    rows_before = _rows(db_path)

    con = sqlite3.connect(str(db_path))
    try:
        con.execute(f"ALTER TABLE {RAG_DOCUMENTS_TABLE} ADD COLUMN {RAG_OWNER_COLUMN} INTEGER")
        con.execute(
            f"CREATE INDEX ix_rag_documents_owner_user_id "
            f"ON {RAG_DOCUMENTS_TABLE} ({RAG_OWNER_COLUMN})"
        )
        con.commit()
    finally:
        con.close()

    partial = inspect_rag_migration_state(db_path)
    assert partial["status"] == "partial"
    assert partial["errors"] == []

    report = migrate_rag_ownership(db_path)

    assert report["initial_status"] == "partial"
    assert report["status"] == "repaired_partial"
    assert report["added_columns"] == [f"{RAG_CHUNKS_TABLE}.{RAG_OWNER_COLUMN}"]
    assert set(report["created_indexes"]) == {
        spec.name for spec in OWNER_INDEXES if spec.name != "ix_rag_documents_owner_user_id"
    }
    assert inspect_rag_migration_state(db_path)["status"] == "current"
    assert _rows(db_path) == rows_before


@pytest.mark.parametrize("invalid_kind", ["wrong_column", "wrong_index"])
def test_invalid_partial_migration_is_rejected_without_writing(tmp_path, invalid_kind):
    db_path = tmp_path / "rag.sqlite3"
    _create_old_schema(db_path)

    con = sqlite3.connect(str(db_path))
    try:
        if invalid_kind == "wrong_column":
            con.execute(
                f"ALTER TABLE {RAG_CHUNKS_TABLE} "
                f"ADD COLUMN {RAG_OWNER_COLUMN} TEXT NOT NULL DEFAULT ''"
            )
        else:
            con.execute(
                f"CREATE INDEX ix_rag_chunks_owner_doc ON {RAG_CHUNKS_TABLE}(doc_id)"
            )
        con.commit()
    finally:
        con.close()

    before = inspect_rag_migration_state(db_path)
    assert before["status"] == "invalid"
    assert before["errors"]

    rows_before = _rows(db_path)
    with pytest.raises(RAGMigrationError, match="invalid RAG schema"):
        migrate_rag_ownership(db_path)

    after = inspect_rag_migration_state(db_path)
    assert after["status"] == "invalid"
    assert after["errors"] == before["errors"]
    assert _rows(db_path) == rows_before

    con = sqlite3.connect(str(db_path))
    try:
        document_columns = {
            row[1] for row in con.execute(f'PRAGMA table_info("{RAG_DOCUMENTS_TABLE}")')
        }
        assert RAG_OWNER_COLUMN not in document_columns
        assert "ix_rag_chunks_owner_user_id" not in _indexes(db_path, RAG_CHUNKS_TABLE)
    finally:
        con.close()


def test_missing_table_is_detected_as_invalid(tmp_path):
    db_path = tmp_path / "rag.sqlite3"
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            f"CREATE TABLE {RAG_DOCUMENTS_TABLE} (doc_id TEXT PRIMARY KEY, source_filename TEXT NOT NULL)"
        )
        con.commit()
    finally:
        con.close()

    state = inspect_rag_migration_state(db_path)
    assert state["status"] == "invalid"
    assert any("Required table is missing" in error for error in state["errors"])

    with pytest.raises(RAGMigrationError, match="invalid RAG schema"):
        migrate_rag_ownership(db_path)


def test_migration_report_is_json_serializable(tmp_path):
    db_path = tmp_path / "rag.sqlite3"
    _create_old_schema(db_path)

    report = migrate_rag_ownership(db_path)
    encoded = json.dumps(report, sort_keys=True)
    assert "documents" in encoded
    assert "chunks" in encoded
