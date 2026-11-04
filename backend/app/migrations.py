"""Safe, repeatable SQLite schema reconciliation.

The application historically created tables with ``Base.metadata.create_all``.
That creates missing tables but does not add newly introduced columns to tables
that already exist.  This module compares the SQLAlchemy metadata with the live
database and applies additive ``ALTER TABLE ... ADD COLUMN`` / ``CREATE INDEX``
statements.  It never drops tables, columns or data.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import MetaData


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _default_sql(column) -> str | None:
    server_default = getattr(column, "server_default", None)
    if server_default is not None:
        arg = getattr(server_default, "arg", None)
        if arg is not None and not callable(arg):
            return str(arg)
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        arg = getattr(default, "arg", None)
        if arg is not None and not callable(arg):
            return _literal(arg)
    return None


def _add_column_sql(engine: Engine, table_name: str, column) -> str:
    type_sql = column.type.compile(dialect=engine.dialect)
    statement = (
        f"ALTER TABLE {_quote(table_name)} ADD COLUMN {_quote(column.name)} {type_sql}"
    )
    if column.nullable:
        return statement
    default_sql = _default_sql(column)
    if default_sql is not None:
        return statement + f" NOT NULL DEFAULT {default_sql}"
    # SQLite cannot add a NOT NULL column without a default.  Add it as nullable
    # rather than losing data or recreating the table, and report the warning.
    return statement


def _create_index_sql(table_name: str, name: str, columns: list[str], unique: bool) -> str:
    prefix = "CREATE UNIQUE INDEX" if unique else "CREATE INDEX"
    column_sql = ", ".join(_quote(column) for column in columns)
    return (
        f"{prefix} IF NOT EXISTS {_quote(name)} ON {_quote(table_name)} ({column_sql})"
    )


def ensure_database_schema(engine: Engine, metadata: MetaData) -> dict[str, list[str]]:
    """Create missing tables/columns/indexes without destructive operations."""
    metadata.create_all(bind=engine)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    report: dict[str, list[str]] = {
        "added_columns": [],
        "created_indexes": [],
        "warnings": [],
    }

    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue

            existing_columns = {
                column["name"] for column in inspector.get_columns(table.name)
            }
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                if not column.nullable and _default_sql(column) is None:
                    report["warnings"].append(
                        f"{table.name}.{column.name} was added as nullable because "
                        "SQLite cannot add a NOT NULL column without a default."
                    )
                connection.execute(text(_add_column_sql(engine, table.name, column)))
                existing_columns.add(column.name)
                report["added_columns"].append(f"{table.name}.{column.name}")

            existing_indexes = {
                index["name"] for index in inspector.get_indexes(table.name)
            }

            for column in table.columns:
                if not column.index:
                    continue
                name = f"ix_{table.name}_{column.name}"
                if name in existing_indexes:
                    continue
                connection.execute(
                    text(
                        _create_index_sql(
                            table.name,
                            name,
                            [column.name],
                            unique=bool(column.unique),
                        )
                    )
                )
                existing_indexes.add(name)
                report["created_indexes"].append(name)

            for index in table.indexes:
                if index.name in existing_indexes:
                    continue
                columns = [column.name for column in index.columns]
                connection.execute(
                    text(
                        _create_index_sql(
                            table.name,
                            index.name,
                            columns,
                            unique=bool(index.unique),
                        )
                    )
                )
                existing_indexes.add(index.name)
                report["created_indexes"].append(index.name)

    return report


def has_pending_schema_changes(engine: Engine, metadata: MetaData) -> bool:
    """Return True when additive schema changes are required.

    This inspection is read-only and is used to decide whether the SQLite file
    should be backed up before ``ensure_database_schema`` runs.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            return True
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        if any(column.name not in existing_columns for column in table.columns):
            return True
    return False


def backup_sqlite_database(engine: Engine, label: str = "pre-migration") -> str | None:
    """Create a timestamped SQLite backup using the online backup API.

    The original database is never modified, dropped or recreated by this
    function. Non-SQLite databases and in-memory databases are ignored.
    """
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    if engine.url.get_backend_name() != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    source_path = Path(database)
    if not source_path.exists() or source_path.stat().st_size == 0:
        return None

    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"{source_path.stem}-{label}-{stamp}.db"

    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return str(destination)
