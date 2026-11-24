import sqlite3

import pytest

from app.ownership import (
    RAG_GLOBAL_READ_PERMISSION,
    RAG_GLOBAL_WRITE_PERMISSION,
    RAG_SCOPE_SOURCE_REQUEST,
    RagAccessDenied,
    RagScope,
)
from app.vector_store import RAGOwnershipSchemaError, VectorStore

OWNER_A = 101
OWNER_B = 202


def _owner(user_id: int) -> RagScope:
    return RagScope(
        owner_user_id=user_id,
        permissions=frozenset(),
        source=RAG_SCOPE_SOURCE_REQUEST,
    )


def _global(*permissions: str) -> RagScope:
    return RagScope(
        owner_user_id=999,
        permissions=frozenset(permissions),
        global_access=True,
        source=RAG_SCOPE_SOURCE_REQUEST,
        audit_reason="vector-store isolation test",
    )


def _global_without_owner(*permissions: str) -> RagScope:
    return RagScope(
        owner_user_id=None,
        permissions=frozenset(permissions),
        global_access=True,
        source=RAG_SCOPE_SOURCE_REQUEST,
        audit_reason="vector-store isolation test",
    )


def _create_migrated_schema(db_path) -> None:
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
                metadata TEXT,
                owner_user_id INTEGER
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
                owner_user_id INTEGER,
                UNIQUE(doc_id, chunk_index, text_hash)
            );
            CREATE INDEX ix_rag_chunks_doc ON rag_chunks(doc_id);
            CREATE INDEX ix_rag_chunks_txn ON rag_chunks(transaction_id);
            CREATE INDEX ix_rag_chunks_owner_doc ON rag_chunks(owner_user_id, doc_id);
            """
        )
        con.executemany(
            "INSERT INTO rag_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("doc-a", "alpha.txt", "text_report", "hash-a", 2, "2026-01-01T00:00:00", '{"team":"a"}', OWNER_A),
                ("doc-b", "bravo.txt", "invoice", "hash-b", 1, "2026-01-02T00:00:00", '{"team":"b"}', OWNER_B),
                ("doc-legacy", "legacy.txt", "text_report", "hash-l", 1, "2025-12-31T00:00:00", None, None),
            ],
        )
        con.executemany(
            "INSERT INTO rag_chunks (id, doc_id, source_filename, document_type, "
            "page_number, transaction_id, chunk_index, text, text_hash, upload_timestamp, owner_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "doc-a", "alpha.txt", "text_report", 1, "TXN-A", 0, "alpha marker alpha marker zebra", "ha1", "2026-01-01T00:00:00", OWNER_A),
                (2, "doc-a", "alpha.txt", "text_report", 2, None, 1, "alpha secondary details", "ha2", "2026-01-01T00:00:00", OWNER_A),
                (3, "doc-b", "bravo.txt", "invoice", 3, "TXN-B", 0, "bravo marker yak", "hb1", "2026-01-02T00:00:00", OWNER_B),
                (4, "doc-legacy", "legacy.txt", "text_report", 4, None, 0, "legacy marker quokka", "hl1", "2025-12-31T00:00:00", None),
            ],
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def store_and_db(tmp_path):
    data_dir = tmp_path / "rag"
    data_dir.mkdir()
    db_path = data_dir / "rag.sqlite3"
    _create_migrated_schema(db_path)
    return VectorStore(data_dir=data_dir), db_path


def test_missing_scope_fails_closed_for_every_operation(store_and_db):
    store, _ = store_and_db
    calls = [
        lambda s: s.add_document(
            scope=None,
            doc_id="new-doc",
            source_filename="new.txt",
            document_type="text_report",
            file_hash="hash-new",
            chunks=[{"text": "new evidence"}],
            upload_timestamp="2026-01-03T00:00:00",
        ),
        lambda s: s.delete_document(scope=None, doc_id="doc-a"),
        lambda s: s.get_document(scope=None, doc_id="doc-a"),
        lambda s: s.list_documents(scope=None),
        lambda s: s.stats(scope=None),
        lambda s: s.search("alpha", scope=None),
        lambda s: s.reindex(scope=None),
        lambda s: s._all_chunks(scope=None),
        lambda s: s._rebuild_index(scope=None),
        lambda s: s._load_index(scope=None),
    ]
    for call in calls:
        with pytest.raises(RagAccessDenied):
            call(store)


def test_owner_a_cannot_search_owner_b_and_indexes_are_owner_scoped(store_and_db):
    store, _ = store_and_db

    leaked = store.search("bravo marker", scope=_owner(OWNER_A))
    assert all(item["doc_id"] != "doc-b" for item in leaked)
    assert all(item["source_filename"] != "bravo.txt" for item in leaked)

    a_results = store.search("alpha marker", scope=_owner(OWNER_A))
    b_results = store.search("bravo marker", scope=_owner(OWNER_B))

    assert {item["doc_id"] for item in a_results} == {"doc-a"}
    assert {item["doc_id"] for item in b_results} == {"doc-b"}
    assert {item["owner_user_id"] for item in a_results} == {OWNER_A}
    assert {item["owner_user_id"] for item in b_results} == {OWNER_B}

    a_keys = {key[:2] for key in store._index_cache if key[0] is False and key[1] == OWNER_A}
    b_keys = {key[:2] for key in store._index_cache if key[0] is False and key[1] == OWNER_B}
    assert a_keys == {(False, OWNER_A)}
    assert b_keys == {(False, OWNER_B)}
    assert a_keys.isdisjoint(b_keys)


def test_owner_a_cannot_get_delete_list_or_count_owner_b(store_and_db):
    store, _ = store_and_db
    a_scope = _owner(OWNER_A)

    assert store.get_document(scope=a_scope, doc_id="doc-b") is None
    assert store.delete_document(scope=a_scope, doc_id="doc-b") is False

    global_read = _global(RAG_GLOBAL_READ_PERMISSION)
    assert store.get_document(scope=global_read, doc_id="doc-b")["doc_id"] == "doc-b"

    assert [doc["doc_id"] for doc in store.list_documents(scope=a_scope)] == ["doc-a"]
    assert store.stats(scope=a_scope)["documents"] == 1
    assert store.stats(scope=a_scope)["chunks"] == 2
    assert store.stats(scope=global_read)["documents"] == 3
    assert store.stats(scope=global_read)["chunks"] == 4


def test_legacy_null_rows_are_excluded_from_ordinary_access(store_and_db):
    store, _ = store_and_db
    a_scope = _owner(OWNER_A)
    global_read = _global(RAG_GLOBAL_READ_PERMISSION)

    legacy_leak = store.search("legacy marker", scope=a_scope)
    assert all(item["doc_id"] != "doc-legacy" for item in legacy_leak)
    assert all(item["source_filename"] != "legacy.txt" for item in legacy_leak)
    assert store.get_document(scope=a_scope, doc_id="doc-legacy") is None
    assert store.delete_document(scope=a_scope, doc_id="doc-legacy") is False
    assert "doc-legacy" not in [doc["doc_id"] for doc in store.list_documents(scope=a_scope)]

    legacy = store.search("legacy marker", scope=global_read, top_k=10)
    assert "doc-legacy" in {item["doc_id"] for item in legacy}
    legacy_items = [item for item in legacy if item["doc_id"] == "doc-legacy"]
    assert legacy_items and legacy_items[0]["owner_user_id"] is None


def test_global_read_access_requires_explicit_read_permission(store_and_db):
    store, _ = store_and_db
    write_only = _global(RAG_GLOBAL_WRITE_PERMISSION)

    with pytest.raises(RagAccessDenied, match="rag:global_read"):
        store.get_document(scope=write_only, doc_id="doc-a")
    with pytest.raises(RagAccessDenied, match="rag:global_read"):
        store.list_documents(scope=write_only)
    with pytest.raises(RagAccessDenied, match="rag:global_read"):
        store.stats(scope=write_only)
    with pytest.raises(RagAccessDenied, match="rag:global_read"):
        store.search("alpha", scope=write_only)

    read_scope = _global(RAG_GLOBAL_READ_PERMISSION)
    assert store.get_document(scope=read_scope, doc_id="doc-a") is not None
    assert store.stats(scope=read_scope)["documents"] == 3


def test_global_write_operations_require_rag_global_write(store_and_db):
    store, _ = store_and_db
    read_only = _global(RAG_GLOBAL_READ_PERMISSION)

    with pytest.raises(RagAccessDenied, match="rag:global_write"):
        store.delete_document(scope=read_only, doc_id="doc-b")
    with pytest.raises(RagAccessDenied, match="rag:global_write"):
        store.reindex(scope=read_only)
    with pytest.raises(RagAccessDenied, match="rag:global_write"):
        store.add_document(
            scope=read_only,
            doc_id="new-doc",
            source_filename="new.txt",
            document_type="text_report",
            file_hash="hash-new",
            chunks=[{"text": "new evidence"}],
            upload_timestamp="2026-01-03T00:00:00",
        )

    write_scope = _global(RAG_GLOBAL_WRITE_PERMISSION)
    assert store.delete_document(scope=write_scope, doc_id="doc-b") is True
    assert store.reindex(scope=write_scope)["documents"] == 2


def test_retrieval_preserves_provenance_ranking_and_filters(store_and_db):
    store, _ = store_and_db
    scope = _owner(OWNER_A)

    results = store.search("alpha marker", scope=scope, top_k=2)
    assert len(results) == 2
    assert results[0]["chunk_id"] == 1
    assert results[0]["score"] >= results[1]["score"]
    for item in results:
        assert item["doc_id"] == "doc-a"
        assert item["owner_user_id"] == OWNER_A
        assert item["source"] in {"alpha.txt#chunk-0", "alpha.txt#chunk-1"}
        assert item["source_filename"] == "alpha.txt"
        assert item["document_type"] == "text_report"
        assert item["page_number"] in {1, 2}
        assert isinstance(item["text"], str) and item["text"]

    top_only = store.search("alpha marker", scope=scope, top_k=1)
    assert len(top_only) == 1
    assert top_only[0]["chunk_id"] == 1

    filtered = store.search(
        "alpha marker",
        scope=scope,
        transaction_id="TXN-A",
        source_filename="alpha.txt",
        document_type="text_report",
    )
    assert [item["chunk_id"] for item in filtered] == [1]
    assert filtered[0]["transaction_id"] == "TXN-A"


def test_add_document_stamps_owner_and_blocks_cross_owner_doc_id(store_and_db):
    store, _ = store_and_db

    assert store.add_document(
        scope=_owner(OWNER_A),
        doc_id="doc-a",
        source_filename="alpha.txt",
        document_type="text_report",
        file_hash="hash-a",
        chunks=[{"text": "duplicate upload"}],
        upload_timestamp="2026-01-04T00:00:00",
    ) == 2

    with pytest.raises(RagAccessDenied, match="another account"):
        store.add_document(
            scope=_owner(OWNER_B),
            doc_id="doc-a",
            source_filename="alpha.txt",
            document_type="text_report",
            file_hash="hash-a",
            chunks=[{"text": "cross owner upload"}],
            upload_timestamp="2026-01-04T00:00:00",
        )

    store.add_document(
        scope=_owner(OWNER_A),
        doc_id="doc-a2",
        source_filename="alpha-2.txt",
        document_type="text_report",
        file_hash="hash-a2",
        chunks=[{"text": "alpha second document"}],
        upload_timestamp="2026-01-05T00:00:00",
    )
    assert "doc-a2" in [doc["doc_id"] for doc in store.list_documents(scope=_owner(OWNER_A))]
    assert "doc-a2" not in [doc["doc_id"] for doc in store.list_documents(scope=_owner(OWNER_B))]

    with pytest.raises(RagAccessDenied, match="identify the owner"):
        store.add_document(
            scope=_global_without_owner(RAG_GLOBAL_READ_PERMISSION, RAG_GLOBAL_WRITE_PERMISSION),
            doc_id="doc-nobody",
            source_filename="nobody.txt",
            document_type="text_report",
            file_hash="hash-nobody",
            chunks=[{"text": "unowned evidence"}],
            upload_timestamp="2026-01-06T00:00:00",
        )


def test_legacy_global_pickle_is_never_read_or_modified(store_and_db):
    store, db_path = store_and_db
    sentinel = b"legacy-global-index-must-not-be-loaded"
    legacy_index = db_path.parent / "tfidf.pkl"
    legacy_index.write_bytes(sentinel)

    assert store.search("alpha marker", scope=_owner(OWNER_A))
    store.reindex(scope=_owner(OWNER_A))

    assert legacy_index.read_bytes() == sentinel


def test_unmigrated_schema_fails_closed(tmp_path):
    data_dir = tmp_path / "rag"
    data_dir.mkdir()
    db_path = data_dir / "rag.sqlite3"
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
                upload_timestamp TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()

    store = VectorStore(data_dir=data_dir)
    with pytest.raises(RAGOwnershipSchemaError, match="additive RAG migration"):
        store.search("alpha", scope=_owner(OWNER_A))


def test_global_writer_must_name_the_owner_and_cannot_reuse_another_doc_id(store_and_db):
    store, _ = store_and_db
    global_write = _global(RAG_GLOBAL_WRITE_PERMISSION)

    with pytest.raises(RagAccessDenied, match="identify the owner"):
        store.add_document(
            scope=global_write,
            doc_id="doc-global",
            source_filename="global.txt",
            document_type="text_report",
            file_hash="hash-global",
            chunks=[{"text": "global evidence"}],
            upload_timestamp="2026-01-07T00:00:00",
        )

    inserted = store.add_document(
        scope=global_write,
        doc_id="doc-global",
        source_filename="global.txt",
        document_type="text_report",
        file_hash="hash-global",
        chunks=[{"text": "global evidence"}],
        upload_timestamp="2026-01-07T00:00:00",
        owner_user_id=OWNER_A,
    )
    assert inserted == 1
    assert store.get_document(scope=_owner(OWNER_A), doc_id="doc-global") is not None
    assert store.get_document(scope=_owner(OWNER_B), doc_id="doc-global") is None

    with pytest.raises(RagAccessDenied, match="another account"):
        store.add_document(
            scope=global_write,
            doc_id="doc-global",
            source_filename="global.txt",
            document_type="text_report",
            file_hash="hash-global",
            chunks=[{"text": "overwrite attempt"}],
            upload_timestamp="2026-01-08T00:00:00",
            owner_user_id=OWNER_B,
        )


def test_ordinary_writer_cannot_stamp_another_owner(store_and_db):
    store, _ = store_and_db

    with pytest.raises(RagAccessDenied, match="another owner"):
        store.add_document(
            scope=_owner(OWNER_A),
            doc_id="doc-forged",
            source_filename="forged.txt",
            document_type="text_report",
            file_hash="hash-forged",
            chunks=[{"text": "forged evidence"}],
            upload_timestamp="2026-01-09T00:00:00",
            owner_user_id=OWNER_B,
        )


def test_write_invalidates_cached_scoped_indexes(store_and_db):
    store, _ = store_and_db
    a_scope = _owner(OWNER_A)
    global_read = _global(RAG_GLOBAL_READ_PERMISSION)

    assert store.search("alpha marker", scope=a_scope)
    assert store.search("alpha marker", scope=global_read)
    assert store._has_cached_index(a_scope) is True
    assert store._has_cached_index(global_read) is True

    store.delete_document(scope=a_scope, doc_id="doc-a")

    assert store._has_cached_index(a_scope) is False
    assert store._has_cached_index(global_read) is False
    assert store.search("alpha marker", scope=a_scope) == []
    assert [item["doc_id"] for item in store.search("alpha marker", scope=global_read)] == []


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.add_document(
            doc_id="new-doc",
            source_filename="new.txt",
            document_type="text_report",
            file_hash="hash-new",
            chunks=[{"text": "new evidence"}],
            upload_timestamp="2026-01-10T00:00:00",
        ),
        lambda s: s.delete_document(doc_id="doc-a"),
        lambda s: s.get_document(doc_id="doc-a"),
        lambda s: s.list_documents(),
        lambda s: s.stats(),
        lambda s: s.search("alpha"),
        lambda s: s.reindex(),
        lambda s: s._all_chunks(),
        lambda s: s._rebuild_index(),
        lambda s: s._load_index(),
    ],
)
def test_omitted_scope_fails_closed_with_rag_access_denied(store_and_db, call):
    store, _ = store_and_db

    with pytest.raises(RagAccessDenied):
        call(store)
