import sqlite3

import pytest

from app import rag
from app.ownership import (
    RAG_GLOBAL_READ_PERMISSION,
    RAG_GLOBAL_WRITE_PERMISSION,
    RAG_SCOPE_SOURCE_REQUEST,
    RagAccessDenied,
    RagScope,
    rag_scope_for_background,
)
from app.vector_store import VectorStore

OWNER_A = 301
OWNER_B = 302


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
        audit_reason="rag facade isolation test",
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
                (
                    "legacy-doc",
                    "legacy.txt",
                    "text_report",
                    "legacy-hash",
                    1,
                    "2025-12-31T00:00:00",
                    None,
                    None,
                ),
            ],
        )
        con.execute(
            "INSERT INTO rag_chunks (id, doc_id, source_filename, document_type, "
            "page_number, transaction_id, chunk_index, text, text_hash, "
            "upload_timestamp, owner_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "legacy-doc",
                "legacy.txt",
                "text_report",
                9,
                "TXN-LEGACY",
                0,
                "legacytoken evidence",
                "legacy-th",
                "2025-12-31T00:00:00",
                None,
            ),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def facade(tmp_path, monkeypatch):
    data_dir = tmp_path / "rag"
    data_dir.mkdir()
    db_path = data_dir / "rag.sqlite3"
    _create_migrated_schema(db_path)
    store = VectorStore(data_dir=data_dir)
    monkeypatch.setattr(rag, "_store", store)
    return store, db_path


def test_same_user_ingestion_search_and_retrieval_test(facade):
    scope = _owner(OWNER_A)
    ingested = rag.ingest_text(
        "alphatoken marker evidence",
        scope=scope,
        source_filename="alpha.txt",
        metadata={"page_number": 4, "transaction_id": "TXN-A"},
    )

    results = rag.search("alphatoken", scope=scope)
    assert [item["doc_id"] for item in results] == [ingested["doc_id"]]
    assert results[0]["source_filename"] == "alpha.txt"
    assert results[0]["page_number"] == 4
    assert results[0]["transaction_id"] == "TXN-A"
    assert results[0]["owner_user_id"] == OWNER_A

    report = rag.retrieval_test("alphatoken", scope=scope)
    assert report["has_evidence"] is True
    assert report["result_count"] >= 1
    assert report["chunks"][0]["doc_id"] == ingested["doc_id"]
    assert report["stats"]["documents"] == 1
    assert report["stats"]["chunks"] == 1


def test_different_users_identical_uploads_get_distinct_owner_scoped_ids(facade):
    text = "sharedtoken marker evidence"
    a = rag.ingest_text(text, scope=_owner(OWNER_A), source_filename="shared.txt")
    b = rag.ingest_text(text, scope=_owner(OWNER_B), source_filename="shared.txt")

    assert a["doc_id"] != b["doc_id"]
    assert {doc["doc_id"] for doc in rag.list_documents(scope=_owner(OWNER_A))} == {a["doc_id"]}
    assert {doc["doc_id"] for doc in rag.list_documents(scope=_owner(OWNER_B))} == {b["doc_id"]}

    a_results = rag.search("sharedtoken", scope=_owner(OWNER_A))
    b_results = rag.search("sharedtoken", scope=_owner(OWNER_B))
    assert {item["doc_id"] for item in a_results} == {a["doc_id"]}
    assert {item["doc_id"] for item in b_results} == {b["doc_id"]}


def test_same_user_idempotent_upload(facade):
    scope = _owner(OWNER_A)
    first = rag.ingest_text("idempotenttoken evidence", scope=scope, source_filename="same.txt")
    second = rag.ingest_text("idempotenttoken evidence", scope=scope, source_filename="same.txt")

    assert first["doc_id"] == second["doc_id"]
    assert first["chunks"] == second["chunks"]
    assert [doc["doc_id"] for doc in rag.list_documents(scope=scope)] == [first["doc_id"]]
    assert rag.store_stats(scope=scope)["documents"] == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda s: rag.ingest_text("text", scope=s, source_filename="x.txt"),
        lambda s: rag.ingest_csv_bytes(b"a,b\n1,2\n", "x.csv", scope=s),
        lambda s: rag.ingest_pdf_bytes(b"not-a-pdf", "x.pdf", scope=s),
        lambda s: rag.ingest_upload(b"text", "x.txt", scope=s),
        lambda s: rag.ingest_transactions(None, scope=s),
        lambda s: rag.ingest_investigation_reports(None, scope=s),
        lambda s: rag.search("text", scope=s),
        lambda s: rag.retrieve_context("text", scope=s),
        lambda s: rag.store_stats(scope=s),
        lambda s: rag.list_documents(scope=s),
        lambda s: rag.delete_document("doc-a", scope=s),
        lambda s: rag.reindex(scope=s),
        lambda s: rag.document_status("doc-a", scope=s),
        lambda s: rag.retrieval_test("text", scope=s),
    ],
)
def test_missing_scope_rejected_across_facade(facade, call):
    with pytest.raises(RagAccessDenied):
        call(None)


def test_legacy_null_rows_are_quarantined_from_ordinary_users(facade):
    scope = _owner(OWNER_A)

    assert rag.search("legacytoken", scope=scope) == []
    assert rag.document_status("legacy-doc", scope=scope) is None
    assert rag.delete_document("legacy-doc", scope=scope) is False
    assert "legacy-doc" not in [doc["doc_id"] for doc in rag.list_documents(scope=scope)]
    assert rag.store_stats(scope=scope)["chunks"] == 0

    global_read = _global(RAG_GLOBAL_READ_PERMISSION)
    assert rag.document_status("legacy-doc", scope=global_read)["doc_id"] == "legacy-doc"
    assert "legacy-doc" in {item["doc_id"] for item in rag.search("legacytoken", scope=global_read)}


def test_user_a_cannot_retrieve_or_delete_user_b_evidence(facade):
    b_scope = _owner(OWNER_B)
    a_scope = _owner(OWNER_A)
    b_doc = rag.ingest_text("bravotoken marker evidence", scope=b_scope, source_filename="bravo.txt")

    assert rag.search("bravotoken", scope=a_scope) == []
    contexts = rag.retrieve_context("bravotoken", scope=a_scope)
    assert all(item.get("doc_id") != b_doc["doc_id"] for item in contexts)
    assert all(item.get("source_filename") != "bravo.txt" for item in contexts)

    assert rag.delete_document(b_doc["doc_id"], scope=a_scope) is False
    assert rag.document_status(b_doc["doc_id"], scope=b_scope) is not None
    assert rag.search("bravotoken", scope=b_scope)


def test_background_scope_is_explicit_and_usable(facade):
    job_scope = rag_scope_for_background(OWNER_A)
    ingested = rag.ingest_text(
        "backgroundtoken evidence",
        scope=job_scope,
        source_filename="background.txt",
    )
    assert rag.search("backgroundtoken", scope=job_scope)[0]["doc_id"] == ingested["doc_id"]
    assert rag.search("backgroundtoken", scope=_owner(OWNER_B)) == []


def test_global_write_scope_does_not_imply_an_evidence_owner(facade):
    global_write = _global(RAG_GLOBAL_WRITE_PERMISSION)
    with pytest.raises(RagAccessDenied, match="non-global owner scope"):
        rag.ingest_text("text", scope=global_write, source_filename="x.txt")


def test_client_filters_cannot_override_ownership(facade):
    rag.ingest_text("filtertoken evidence", scope=_owner(OWNER_A), source_filename="a.txt")
    with pytest.raises(RagAccessDenied, match="owner_user_id"):
        rag.search("filtertoken", scope=_owner(OWNER_A), owner_user_id=OWNER_B)
    with pytest.raises(RagAccessDenied, match="global_access"):
        rag.retrieval_test("filtertoken", scope=_owner(OWNER_A), global_access=True)
