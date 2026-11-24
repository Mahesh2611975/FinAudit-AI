"""Additive, idempotent ownership migration for the separate RAG SQLite store.

The main application database is migrated by ``app.migrations`` and must not be
used here. This module operates only on ``rag.sqlite3`` and deliberately leaves
``tfidf.pkl`` untouched.

Stage 2 scope:

* add nullable ``owner_user_id`` to ``rag_documents`` and ``rag_chunks``;
* create non-unique owner indexes for scoped queries;
* leave every legacy row with ``owner_user_id IS NULL``;
* preserve all rows, primary keys, unique constraints and foreign keys;
* detect invalid or partially applied ownership state safely.

For compatibility with older SQLite builds, the migration does not rely on
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` (which SQLite does not support) or
``CREATE INDEX IF NOT EXISTS``. It checks ``sqlite_master`` through PRAGMAs and
wraps all DDL in an explicit ``BEGIN IMMEDIATE`` transaction. Python's legacy
sqlite3 transaction handling does not open a transaction for DDL automatically,
so the explicit transaction is required for all-or-nothing behavior.

Importing this module never contacts a database. Call ``migrate_rag_ownership``
explicitly from authorized tooling after taking a verified backup.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RAG_DOCUMENTS_TABLE = "rag_documents"
RAG_CHUNKS_TABLE = "rag_chunks"
RAG_OWNER_COLUMN = "owner_user_id"
RAG_TABLES = (RAG_DOCUMENTS_TABLE, RAG_CHUNKS_TABLE)

# Original columns are used for a before/after data fingerprint. The added
# owner column is intentionally excluded because it legitimately appears in the
# "after" image.
_BASE_COLUMNS: dict[str, tuple[str, ...]] = {
    RAG_DOCUMENTS_TABLE: (
        "doc_id",
        "source_filename",
        "document_type",
        "file_hash",
        "chunk_count",
        "upload_timestamp",
        "metadata",
    ),
    RAG_CHUNKS_TABLE: (
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
    ),
}


@dataclass(frozen=True)
class RagOwnerIndex:
    name: str
    table: str
    columns: tuple[str, ...]


OWNER_INDEXES: tuple[RagOwnerIndex, ...] = (
    RagOwnerIndex(
        "ix_rag_documents_owner_user_id",
        RAG_DOCUMENTS_TABLE,
        (RAG_OWNER_COLUMN,),
    ),
    RagOwnerIndex(
        "ix_rag_documents_owner_upload",
        RAG_DOCUMENTS_TABLE,
        (RAG_OWNER_COLUMN, "upload_timestamp"),
    ),
    RagOwnerIndex(
        "ix_rag_chunks_owner_user_id",
        RAG_CHUNKS_TABLE,
        (RAG_OWNER_COLUMN,),
    ),
    RagOwnerIndex(
        "ix_rag_chunks_owner_doc",
        RAG_CHUNKS_TABLE,
        (RAG_OWNER_COLUMN, "doc_id"),
    ),
)

__all__ = [
    "OWNER_INDEXES",
    "RAG_DOCUMENTS_TABLE",
    "RAG_OWNER_COLUMN",
    "RAG_TABLES",
    "RAG_CHUNKS_TABLE",
    "RAGMigrationError",
    "inspect_rag_migration_state",
    "migrate_rag_ownership",
]


class RAGMigrationError(RuntimeError):
    """Raised when the RAG schema is unsafe, invalid, or cannot be migrated."""


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"RAG database not found: {path}")
    con = sqlite3.connect(_readonly_uri(path), uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        # query_only is unavailable on very old SQLite builds; its absence does
        # not affect correctness because the connection is opened mode=ro and no
        # write statements are issued here.
        con.execute("PRAGMA query_only=ON")
    except sqlite3.OperationalError:
        pass
    return con


def _connect_write(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"RAG database not found: {path}")
    # isolation_level=None gives explicit control over BEGIN/COMMIT/ROLLBACK.
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def _table_info(con: sqlite3.Connection, table: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in con.execute(f"PRAGMA table_info({_quote(table)})"):
        result[str(row[1])] = {
            "type": str(row[2] or ""),
            "notnull": bool(row[3]),
            "default": row[4],
            "primary_key_position": int(row[5]),
        }
    return result


def _index_info(con: sqlite3.Connection, table: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in con.execute(f"PRAGMA index_list({_quote(table)})"):
        name = str(row[1])
        columns = [
            str(info[2])
            for info in con.execute(f"PRAGMA index_info({_quote(name)})")
        ]
        # ``origin`` was added to PRAGMA index_list in later SQLite builds.
        origin = str(row[3] or "") if len(row) > 3 else ""
        result[name] = {
            "unique": bool(row[2]),
            "origin": origin,
            "columns": columns,
        }
    return result


def _foreign_key_info(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in con.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
        result.append({
            "references_table": str(row[2]),
            "from": str(row[3]),
            "to": str(row[4]),
            "on_update": str(row[5]),
            "on_delete": str(row[6]),
        })
    return sorted(result, key=lambda item: (item["references_table"], item["from"], item["to"]))


def _counts(con: sqlite3.Connection) -> dict[str, int]:
    return {
        "documents": int(con.execute(f"SELECT COUNT(*) FROM {_quote(RAG_DOCUMENTS_TABLE)}").fetchone()[0]),
        "chunks": int(con.execute(f"SELECT COUNT(*) FROM {_quote(RAG_CHUNKS_TABLE)}").fetchone()[0]),
    }


def _base_data_digest(con: sqlite3.Connection) -> str:
    """Fingerprint pre-existing columns only, so added columns do not alter it."""
    digest = hashlib.sha256()
    for table in RAG_TABLES:
        columns = _BASE_COLUMNS[table]
        column_sql = ", ".join(_quote(column) for column in columns)
        digest.update(table.encode("utf-8"))
        for row in con.execute(
            f"SELECT {column_sql} FROM {_quote(table)} ORDER BY rowid"
        ):
            digest.update(
                json.dumps(tuple(row), default=str, sort_keys=True).encode("utf-8")
            )
    return digest.hexdigest()


def _schema_snapshot(con: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        name: con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
        for name in RAG_TABLES
    }
    snapshot: dict[str, Any] = {
        "sqlite_version": sqlite3.sqlite_version,
        "tables": {},
    }
    for name, exists in tables.items():
        if not exists:
            continue
        snapshot["tables"][name] = {
            "columns": _table_info(con, name),
            "indexes": _index_info(con, name),
            "foreign_keys": _foreign_key_info(con, name),
        }
    return snapshot


def _constraint_fingerprint(snapshot: dict[str, Any]) -> dict[str, Any]:
    fingerprint: dict[str, Any] = {}
    for table in RAG_TABLES:
        details = snapshot["tables"].get(table)
        if not details:
            continue
        columns = details["columns"]
        primary_key = tuple(
            name
            for name, meta in sorted(
                columns.items(), key=lambda item: item[1]["primary_key_position"]
            )
            if meta["primary_key_position"] > 0
        )
        unique_indexes = sorted(
            tuple(index["columns"])
            for index in details["indexes"].values()
            if index["unique"]
        )
        fingerprint[table] = {
            "primary_key": primary_key,
            "unique_indexes": unique_indexes,
            "foreign_keys": details["foreign_keys"],
        }
    return fingerprint


def _base_schema_errors(snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for table in RAG_TABLES:
        details = snapshot["tables"].get(table)
        if not details:
            errors.append(f"Required table is missing: {table}")
            continue
        for column in _BASE_COLUMNS[table]:
            if column not in details["columns"]:
                errors.append(f"Required base column is missing: {table}.{column}")
    return errors


def _evaluate_ownership_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = _base_schema_errors(snapshot)
    present: list[bool] = []

    for table in RAG_TABLES:
        details = snapshot["tables"].get(table)
        if not details:
            present.append(False)
            continue
        column = details["columns"].get(RAG_OWNER_COLUMN)
        if column is None:
            present.append(False)
            continue
        present.append(True)
        if column["type"].strip().upper() != "INTEGER":
            errors.append(f"{table}.{RAG_OWNER_COLUMN} must be INTEGER.")
        if column["notnull"]:
            errors.append(f"{table}.{RAG_OWNER_COLUMN} must be nullable.")
        if column["default"] is not None:
            errors.append(f"{table}.{RAG_OWNER_COLUMN} must not have a default value.")

    for spec in OWNER_INDEXES:
        details = snapshot["tables"].get(spec.table)
        if not details:
            present.append(False)
            continue
        index = details["indexes"].get(spec.name)
        if index is None:
            present.append(False)
            continue
        present.append(True)
        if index["unique"]:
            errors.append(f"Owner index must be non-unique: {spec.name}")
        if tuple(index["columns"]) != spec.columns:
            errors.append(
                f"Owner index {spec.name} has unexpected columns: {', '.join(index['columns'])}"
            )

    if errors:
        status = "invalid"
    elif not any(present):
        status = "missing"
    elif all(present):
        status = "current"
    else:
        status = "partial"

    return {"status": status, "errors": tuple(errors)}


def _owner_null_counts_from_db(con: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for table in RAG_TABLES:
        details = snapshot["tables"].get(table)
        if not details or RAG_OWNER_COLUMN not in details["columns"]:
            result[table] = None
            continue
        result[table] = int(
            con.execute(
                f"SELECT COUNT(*) FROM {_quote(table)} WHERE {_quote(RAG_OWNER_COLUMN)} IS NULL"
            ).fetchone()[0]
        )
    return result


def inspect_rag_migration_state(db_path: str | Path) -> dict[str, Any]:
    """Read-only inspection of the RAG ownership migration state."""
    path = Path(db_path)
    con = _connect_readonly(path)
    try:
        snapshot = _schema_snapshot(con)
        state = _evaluate_ownership_state(snapshot)
        counts = _counts(con) if not state["errors"] else {"documents": None, "chunks": None}
        return {
            "database": str(path),
            "sqlite_version": sqlite3.sqlite_version,
            "status": state["status"],
            "errors": list(state["errors"]),
            "tables": sorted(snapshot["tables"]),
            "counts": counts,
            "data_digest": _base_data_digest(con) if not state["errors"] else None,
            "owner_null_counts": _owner_null_counts_from_db(con, snapshot),
            "schema": snapshot,
        }
    finally:
        con.close()


def migrate_rag_ownership(db_path: str | Path) -> dict[str, Any]:
    """Apply the additive RAG ownership migration to one SQLite file.

    The caller is responsible for ensuring the backend is stopped and a verified
    backup exists. This function never creates, replaces, resets, or drops the
    database file, and never touches ``tfidf.pkl``.
    """
    path = Path(db_path)
    started_at = _utc_now()
    before = inspect_rag_migration_state(path)
    if before["status"] == "invalid":
        raise RAGMigrationError(
            "Refusing to migrate invalid RAG schema: " + "; ".join(before["errors"])
        )

    before_snapshot = before["schema"]
    before_counts = before["counts"]
    before_digest = before["data_digest"]
    before_constraints = _constraint_fingerprint(before_snapshot)

    added_columns: list[str] = []
    created_indexes: list[str] = []
    integrity_check = "not-run"

    con = _connect_write(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            tx_snapshot = _schema_snapshot(con)
            tx_state = _evaluate_ownership_state(tx_snapshot)
            if tx_state["status"] == "invalid":
                raise RAGMigrationError(
                    "Refusing to migrate invalid RAG schema: " + "; ".join(tx_state["errors"])
                )

            for table in RAG_TABLES:
                columns = tx_snapshot["tables"][table]["columns"]
                if RAG_OWNER_COLUMN not in columns:
                    con.execute(
                        f"ALTER TABLE {_quote(table)} ADD COLUMN {_quote(RAG_OWNER_COLUMN)} INTEGER"
                    )
                    added_columns.append(f"{table}.{RAG_OWNER_COLUMN}")

            for spec in OWNER_INDEXES:
                indexes = tx_snapshot["tables"][spec.table]["indexes"]
                if spec.name not in indexes:
                    column_sql = ", ".join(_quote(column) for column in spec.columns)
                    con.execute(
                        f"CREATE INDEX {_quote(spec.name)} "
                        f"ON {_quote(spec.table)} ({column_sql})"
                    )
                    created_indexes.append(spec.name)

            after_snapshot = _schema_snapshot(con)
            after_state = _evaluate_ownership_state(after_snapshot)
            if after_state["status"] != "current":
                raise RAGMigrationError(
                    "RAG migration did not reach a valid current state: "
                    + "; ".join(after_state["errors"])
                )

            after_counts = _counts(con)
            after_digest = _base_data_digest(con)
            after_constraints = _constraint_fingerprint(after_snapshot)
            after_owner_nulls = _owner_null_counts_from_db(con, after_snapshot)
            integrity_check = str(con.execute("PRAGMA integrity_check").fetchone()[0])

            if after_counts != before_counts:
                raise RAGMigrationError("Row counts changed during RAG migration.")
            if after_digest != before_digest:
                raise RAGMigrationError("Existing RAG row data changed during migration.")
            if after_constraints != before_constraints:
                raise RAGMigrationError("Primary keys, unique indexes, or foreign keys changed.")
            if integrity_check != "ok":
                raise RAGMigrationError(f"SQLite integrity_check failed: {integrity_check}")
            if after_owner_nulls.get(RAG_DOCUMENTS_TABLE) != after_counts["documents"]:
                raise RAGMigrationError("Not all legacy documents remain NULL-owned.")
            if after_owner_nulls.get(RAG_CHUNKS_TABLE) != after_counts["chunks"]:
                raise RAGMigrationError("Not all legacy chunks remain NULL-owned.")

            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        con.close()

    after = inspect_rag_migration_state(path)
    if before["status"] == "missing":
        final_status = "applied"
    elif before["status"] == "partial":
        final_status = "repaired_partial"
    else:
        final_status = "already_current"

    return {
        "database": str(path),
        "sqlite_version": sqlite3.sqlite_version,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "status": final_status,
        "initial_status": before["status"],
        "added_columns": added_columns,
        "created_indexes": created_indexes,
        "counts_before": before_counts,
        "counts_after": after["counts"],
        "data_digest_before": before_digest,
        "data_digest_after": after["data_digest"],
        "rows_unchanged": before_digest == after["data_digest"],
        "constraints_preserved": before_constraints
        == _constraint_fingerprint(after["schema"]),
        "owner_null_counts_after": after["owner_null_counts"],
        "integrity_check": integrity_check,
        "final_status": after["status"],
    }
