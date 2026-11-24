"""Ownership-aware, dependency-free local vector store for RAG evidence.

SQLite stores chunk metadata. TF-IDF indexes are built in memory from only the
rows authorized by a supplied :class:`~app.ownership.RagScope`; the legacy
global ``tfidf.pkl`` file is neither read nor written. This keeps each user's
vocabulary, IDF weights and ranking corpus isolated from every other account.

The in-memory index is cached by ``(global_access, owner_user_id, corpus
fingerprint)``. A cache entry can never be used for another owner, and any
ingest/delete invalidates cached indexes so stale entries are not reused. The
trade-off is that each process rebuilds an owner's index after restarts or
corpus changes; for the current small local corpus this is preferable to
cross-tenant pickle indexes.

Retrieval deliberately requires the query's content terms to be present in the
authorized corpus before any similarity is returned. Without that check, a
query about another account can still match a generic shared word (for example
"marker") and surface weak, unsupported evidence. The cost is reduced recall
for queries mixing in terms that are absent from the authorized corpus.

Compatibility for Stage 3: every storage/retrieval method now requires an
explicit keyword-only ``scope``; omitting it raises ``RagAccessDenied`` rather
than silently using a global view. Existing callers in ``app.rag`` (ingest_text,
ingest_csv_bytes, ingest_pdf_bytes, ingest_transactions,
ingest_investigation_reports, search, retrieve_context, store_stats,
list_documents, delete_document, reindex, document_status, retrieval_test) do
not yet pass one, and the endpoints/agents/LLM that call those helpers also
remain unchanged. They fail closed until Stage 4 propagates a trusted scope;
there is intentionally no ambient or global fallback.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from .ownership import RAG_READ, RAG_WRITE, RagAccessDenied, RagScope, validate_rag_scope

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "rag"
OWNER_COLUMN = "owner_user_id"
_DOCUMENTS_TABLE = "rag_documents"
_CHUNKS_TABLE = "rag_chunks"
_INDEX_CACHE_LIMIT = 8


class RAGOwnershipSchemaError(RuntimeError):
    """Raised when the RAG database has not been migrated for ownership."""


def text_hash(text: str | bytes) -> str:
    if isinstance(text, bytes):
        return hashlib.sha256(text).hexdigest()
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


@dataclass(frozen=True)
class _ScopedIndex:
    chunks: tuple[dict[str, Any], ...]
    vectorizer: Any
    matrix: Any


class VectorStore:
    """SQLite metadata + in-memory, owner-scoped TF-IDF retrieval."""

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "rag.sqlite3"
        # Retained only for path compatibility. Scoped retrieval never reads or
        # writes this legacy global pickle.
        self.index_path = self.data_dir / "tfidf.pkl"
        self._lock = threading.RLock()
        self._index_cache: OrderedDict[tuple[bool, int | None, str], _ScopedIndex] = OrderedDict()
        self._init_db()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        """Create the original tables if absent; never adds ownership columns.

        Ownership columns are added only by the explicit Stage 2 migration.
        """
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS rag_documents (
                    doc_id TEXT PRIMARY KEY,
                    source_filename TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    upload_timestamp TEXT NOT NULL,
                    metadata TEXT
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS rag_chunks (
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
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS ix_rag_chunks_doc ON rag_chunks(doc_id)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS ix_rag_chunks_txn ON rag_chunks(transaction_id)"
            )

    @staticmethod
    def _require_owner_schema(con: sqlite3.Connection) -> None:
        for table in (_DOCUMENTS_TABLE, _CHUNKS_TABLE):
            columns = {str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")')}
            if OWNER_COLUMN not in columns:
                raise RAGOwnershipSchemaError(
                    f"RAG table {table} is missing {OWNER_COLUMN}; "
                    "run the approved additive RAG migration first."
                )

    @staticmethod
    def _validate_scope(scope: RagScope | None, operation: str) -> RagScope:
        return validate_rag_scope(scope, operation=operation)

    @staticmethod
    def _write_owner(scope: RagScope, owner_user_id: int | None = None) -> int:
        """Resolve the owner to stamp on an insert without guessing for globals.

        Ordinary scopes already name exactly one owner. A permission-backed
        global writer must supply the target owner explicitly; the scope's own
        id is the administrator/job id, not evidence of the document's owner.
        """
        if scope.global_access:
            if owner_user_id is None:
                raise RagAccessDenied("Global RAG writes must identify the owner to stamp.")
            if isinstance(owner_user_id, bool) or not isinstance(owner_user_id, int):
                raise RagAccessDenied("RAG write owner_user_id must be an integer user id.")
            return owner_user_id
        if owner_user_id is not None and owner_user_id != scope.owner_user_id:
            raise RagAccessDenied("RAG scope does not authorize writing for another owner.")
        # validate_rag_scope has already checked that this is a non-boolean int.
        return int(scope.owner_user_id)

    @staticmethod
    def _read_where(scope: RagScope) -> tuple[str, tuple[Any, ...]]:
        if scope.global_access:
            return "", ()
        return f" WHERE {OWNER_COLUMN} = ?", (scope.owner_user_id,)

    @staticmethod
    def _extra_read_clause(scope: RagScope, column: str) -> tuple[str, tuple[Any, ...]]:
        if scope.global_access:
            return "", ()
        return f" AND {column} = ?", (scope.owner_user_id,)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def add_document(
        self,
        *,
        scope: RagScope | None = None,
        doc_id: str,
        source_filename: str,
        document_type: str,
        file_hash: str,
        chunks: Iterable[dict[str, Any]],
        upload_timestamp: str,
        metadata: dict[str, Any] | None = None,
        owner_user_id: int | None = None,
    ) -> int:
        """Insert one owner's document and chunks, scoped by ``scope``.

        ``owner_user_id`` is only meaningful for a permission-backed global
        writer: it names the account whose rows are being created. Ordinary
        callers must not supply it, and writes that would create NULL-owned
        legacy rows are always rejected.
        """
        scope = self._validate_scope(scope, RAG_WRITE)
        owner = self._write_owner(scope, owner_user_id)

        chunk_list = [chunk for chunk in chunks if str(chunk.get("text") or "").strip()]
        with self._lock, self._connect() as con:
            self._require_owner_schema(con)
            existing = con.execute(
                f"SELECT {OWNER_COLUMN}, chunk_count FROM {_DOCUMENTS_TABLE} WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            if existing is not None:
                # doc_id remains a global primary key. Never let a different
                # owner's document resolve to this one, even for global writers.
                if existing[OWNER_COLUMN] != owner:
                    raise RagAccessDenied("Document id is already owned by another account.")
                return int(existing["chunk_count"] or 0)

            con.execute(
                f"""INSERT INTO {_DOCUMENTS_TABLE}
                    (doc_id, source_filename, document_type, file_hash, chunk_count,
                     upload_timestamp, metadata, {OWNER_COLUMN})
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    source_filename,
                    document_type,
                    file_hash,
                    len(chunk_list),
                    upload_timestamp,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    owner,
                ),
            )
            for index, chunk in enumerate(chunk_list):
                text = " ".join(str(chunk["text"]).split())
                con.execute(
                    f"""INSERT OR IGNORE INTO {_CHUNKS_TABLE}
                        (doc_id, source_filename, document_type, page_number,
                         transaction_id, chunk_index, text, text_hash, upload_timestamp,
                         {OWNER_COLUMN})
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        doc_id,
                        source_filename,
                        document_type,
                        chunk.get("page_number"),
                        chunk.get("transaction_id"),
                        index,
                        text,
                        text_hash(text),
                        upload_timestamp,
                        owner,
                    ),
                )
            stored = con.execute(
                f"SELECT COUNT(*) FROM {_CHUNKS_TABLE} WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]
            con.execute(
                f"UPDATE {_DOCUMENTS_TABLE} SET chunk_count = ? WHERE doc_id = ?",
                (int(stored), doc_id),
            )

        self._invalidate_index()
        return int(stored)

    def delete_document(self, *, scope: RagScope | None = None, doc_id: str) -> bool:
        """Delete a document only if ``scope`` is authorized for its rows."""
        scope = self._validate_scope(scope, RAG_WRITE)
        doc_clause, doc_params = self._extra_read_clause(scope, OWNER_COLUMN)
        chunk_clause, chunk_params = self._extra_read_clause(scope, OWNER_COLUMN)
        with self._lock, self._connect() as con:
            self._require_owner_schema(con)
            owned_doc = con.execute(
                f"SELECT doc_id FROM {_DOCUMENTS_TABLE} WHERE doc_id = ?{doc_clause}",
                (doc_id, *doc_params),
            ).fetchone()
            if owned_doc is None:
                return False
            # Chunks are removed with the same ownership predicate, so a mixed-
            # owner doc_id can never delete another account's evidence.
            con.execute(
                f"DELETE FROM {_CHUNKS_TABLE} WHERE doc_id = ?{chunk_clause}",
                (doc_id, *chunk_params),
            )
            con.execute(
                f"DELETE FROM {_DOCUMENTS_TABLE} WHERE doc_id = ?{doc_clause}",
                (doc_id, *doc_params),
            )

        self._invalidate_index()
        return True

    def reindex(self, *, scope: RagScope | None = None) -> dict[str, Any]:
        """Rebuild only the corpus authorized by ``scope``."""
        scope = self._validate_scope(scope, RAG_WRITE)
        self._rebuild_index(scope=scope, validate=False)
        return self._stats(scope=scope, validate=False)

    def get_document(self, *, scope: RagScope | None = None, doc_id: str) -> dict[str, Any] | None:
        """Return one document's status only when the scope can access it."""
        scope = self._validate_scope(scope, RAG_READ)
        clause, params = self._extra_read_clause(scope, OWNER_COLUMN)
        with self._connect() as con:
            self._require_owner_schema(con)
            row = con.execute(
                f"""SELECT doc_id, source_filename, document_type, file_hash,
                           chunk_count, upload_timestamp, metadata, {OWNER_COLUMN}
                    FROM {_DOCUMENTS_TABLE} WHERE doc_id = ?{clause}""",
                (doc_id, *params),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except (TypeError, ValueError):
            item["metadata"] = {}
        item["status"] = "indexed" if item.get("chunk_count") else "empty"
        return item

    # ------------------------------------------------------------------
    # Scoped corpus + index + retrieval
    # ------------------------------------------------------------------
    def _all_chunks(self, *, scope: RagScope | None = None, validate: bool = True) -> list[dict[str, Any]]:
        if validate:
            scope = self._validate_scope(scope, RAG_READ)
        where, params = self._read_where(scope)
        with self._connect() as con:
            self._require_owner_schema(con)
            rows = con.execute(
                f"SELECT * FROM {_CHUNKS_TABLE}{where} ORDER BY id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _index_key(scope: RagScope, chunks: list[dict[str, Any]]) -> tuple[bool, int | None, str]:
        digest = hashlib.sha256()
        for chunk in chunks:
            # Fingerprint the actual retrieved text, not the stored text_hash:
            # a stale hash must never let a caller reuse an old index entry.
            digest.update(
                (
                    f"{chunk.get(OWNER_COLUMN)}|{chunk.get('id')}|{chunk.get('doc_id')}|"
                    f"{chunk.get('chunk_index')}|{text_hash(chunk.get('text') or '')}"
                ).encode("utf-8")
            )
        return (bool(scope.global_access), scope.owner_user_id, digest.hexdigest())

    def _build_index(self, scope: RagScope, chunks: list[dict[str, Any]]) -> _ScopedIndex:
        import numpy as np

        if not chunks:
            return _ScopedIndex(chunks=(), vectorizer=None, matrix=np.empty((0, 0), dtype=float))

        vectorizer = TfidfVectorizer(
            stop_words="english", max_features=20000, ngram_range=(1, 2), sublinear_tf=True
        )
        matrix = normalize(vectorizer.fit_transform([chunk["text"] for chunk in chunks]))
        return _ScopedIndex(
            chunks=tuple(chunks),
            vectorizer=vectorizer,
            matrix=matrix,
        )

    @staticmethod
    def _query_is_supported(index: _ScopedIndex, query: str) -> bool:
        """Require every content term of a query to exist in the scoped corpus.

        Scoring on a single generic shared term would return weakly supported
        evidence when a query really concerns material outside this scope.
        Bigrams are derived terms and are intentionally not required.
        """
        if index.vectorizer is None:
            return False
        content_terms = [
            token
            for token in index.vectorizer.build_analyzer()(query)
            if " " not in token
        ]
        return bool(content_terms) and all(
            token in index.vectorizer.vocabulary_ for token in content_terms
        )

    def _rebuild_index(self, *, scope: RagScope | None = None, validate: bool = True) -> None:
        if validate:
            scope = self._validate_scope(scope, RAG_WRITE)
        chunks = self._all_chunks(scope=scope, validate=False)
        index = self._build_index(scope, chunks)
        key = self._index_key(scope, chunks)
        with self._lock:
            self._index_cache.clear()
            self._index_cache[key] = index

    def _load_index(
        self,
        *,
        scope: RagScope | None = None,
        chunks: list[dict[str, Any]] | None = None,
        validate: bool = True,
    ) -> _ScopedIndex:
        if validate:
            scope = self._validate_scope(scope, RAG_READ)
        if chunks is None:
            chunks = self._all_chunks(scope=scope, validate=False)
        key = self._index_key(scope, chunks)
        with self._lock:
            index = self._index_cache.get(key)
            if index is not None:
                self._index_cache.move_to_end(key)
                return index
            index = self._build_index(scope, chunks)
            self._index_cache[key] = index
            while len(self._index_cache) > _INDEX_CACHE_LIMIT:
                self._index_cache.popitem(last=False)
            return index

    def _invalidate_index(self, scope: RagScope | None = None) -> None:
        with self._lock:
            if scope is None:
                self._index_cache.clear()
                return
            prefix = (bool(scope.global_access), scope.owner_user_id)
            for key in list(self._index_cache):
                if key[:2] == prefix:
                    self._index_cache.pop(key, None)

    def _has_cached_index(self, scope: RagScope) -> bool:
        prefix = (bool(scope.global_access), scope.owner_user_id)
        with self._lock:
            return any(key[:2] == prefix for key in self._index_cache)

    def search(
        self,
        query: str,
        *,
        scope: RagScope | None = None,
        top_k: int = 5,
        source_filename: str | None = None,
        document_type: str | None = None,
        transaction_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scope = self._validate_scope(scope, RAG_READ)
        if not str(query or "").strip():
            return []

        index = self._load_index(scope=scope, validate=False)
        if not index.chunks or not self._query_is_supported(index, str(query)):
            return []

        query_matrix = normalize(index.vectorizer.transform([str(query)]))
        scores = cosine_similarity(query_matrix, index.matrix).flatten()

        results: list[dict[str, Any]] = []
        for position, score in enumerate(scores):
            chunk = index.chunks[position]
            if source_filename and chunk["source_filename"] != source_filename:
                continue
            if document_type and chunk["document_type"] != document_type:
                continue
            if transaction_id and chunk["transaction_id"] != transaction_id:
                continue
            if float(score) <= 0:
                continue
            results.append(
                {
                    "chunk_id": chunk["id"],
                    "doc_id": chunk["doc_id"],
                    "source": f"{chunk['source_filename']}#chunk-{chunk['chunk_index']}",
                    "source_filename": chunk["source_filename"],
                    "document_type": chunk["document_type"],
                    "page_number": chunk["page_number"],
                    "transaction_id": chunk["transaction_id"],
                    "text": chunk["text"],
                    "score": float(score),
                    "owner_user_id": chunk.get(OWNER_COLUMN),
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def _stats(self, *, scope: RagScope | None = None, validate: bool = True) -> dict[str, Any]:
        if validate:
            scope = self._validate_scope(scope, RAG_READ)
        where, params = self._read_where(scope)
        with self._connect() as con:
            self._require_owner_schema(con)
            docs = con.execute(
                f"SELECT COUNT(*) FROM {_DOCUMENTS_TABLE}{where}", params
            ).fetchone()[0]
            chunks = con.execute(
                f"SELECT COUNT(*) FROM {_CHUNKS_TABLE}{where}", params
            ).fetchone()[0]
        return {
            "documents": int(docs),
            "chunks": int(chunks),
            "index_exists": self._has_cached_index(scope),
            # Retained for existing health/API consumers; the actual builder is
            # described by index_backend.
            "backend": "local-tfidf-sqlite",
            "index_backend": "scoped-memory-tfidf",
            "data_dir": str(self.data_dir),
        }

    def stats(self, *, scope: RagScope | None = None) -> dict[str, Any]:
        return self._stats(scope=scope, validate=True)

    def list_documents(self, *, scope: RagScope | None = None, limit: int = 50) -> list[dict[str, Any]]:
        scope = self._validate_scope(scope, RAG_READ)
        where, params = self._read_where(scope)
        with self._connect() as con:
            self._require_owner_schema(con)
            rows = con.execute(
                f"""SELECT doc_id, source_filename, document_type, file_hash,
                           chunk_count, upload_timestamp, metadata, {OWNER_COLUMN}
                    FROM {_DOCUMENTS_TABLE}{where}
                    ORDER BY upload_timestamp DESC LIMIT ?""",
                (*params, min(int(limit), 200)),
            ).fetchall()
        docs = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, ValueError):
                item["metadata"] = {}
            docs.append(item)
        return docs


def clean_text(text: str) -> str:
    """Normalize whitespace and drop control characters without losing meaning."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int = 900,
    overlap: int = 150,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Split text into overlapping word chunks while preserving metadata."""
    cleaned = clean_text(text)
    if not cleaned:
        return []
    words = cleaned.split()
    if len(words) <= chunk_size:
        return [{**(metadata or {}), "text": cleaned}]
    step = max(1, chunk_size - overlap)
    chunks: list[dict[str, Any]] = []
    for start in range(0, len(words), step):
        part = words[start : start + chunk_size]
        if not part:
            continue
        chunks.append({**(metadata or {}), "text": " ".join(part)})
        if start + chunk_size >= len(words):
            break
    return chunks
