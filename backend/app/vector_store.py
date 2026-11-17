"""Lightweight, dependency-free persistent vector store for RAG evidence.

Designed for a small laptop (8 GB RAM): TF-IDF vectors are computed locally
with scikit-learn, stored as a sparse matrix on disk, and chunk metadata lives
in SQLite.  There is no embedding API, no model download and no paid service.
ChromaDB is intentionally avoided; this store is smaller and fully offline.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "rag"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class VectorStore:
    """SQLite metadata + on-disk TF-IDF index."""

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "rag.sqlite3"
        self.index_path = self.data_dir / "tfidf.pkl"
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
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

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def add_document(
        self,
        *,
        doc_id: str,
        source_filename: str,
        document_type: str,
        file_hash: str,
        chunks: Iterable[dict[str, Any]],
        upload_timestamp: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert a document and its chunks, then rebuild the TF-IDF index.

        Returns the number of inserted chunks.  Re-uploading identical content
        is idempotent (same doc_id) and does not duplicate chunks.
        """
        chunk_list = [chunk for chunk in chunks if str(chunk.get("text") or "").strip()]
        with self._lock, self._connect() as con:
            existing = con.execute(
                "SELECT 1 FROM rag_documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if existing:
                return int(
                    con.execute(
                        "SELECT chunk_count FROM rag_documents WHERE doc_id = ?", (doc_id,)
                    ).fetchone()[0]
                )

            con.execute(
                """INSERT INTO rag_documents
                   (doc_id, source_filename, document_type, file_hash, chunk_count,
                    upload_timestamp, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    source_filename,
                    document_type,
                    file_hash,
                    len(chunk_list),
                    upload_timestamp,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            for index, chunk in enumerate(chunk_list):
                text = " ".join(str(chunk["text"]).split())
                con.execute(
                    """INSERT OR IGNORE INTO rag_chunks
                       (doc_id, source_filename, document_type, page_number,
                        transaction_id, chunk_index, text, text_hash, upload_timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    ),
                )
            stored = con.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]
            con.execute(
                "UPDATE rag_documents SET chunk_count = ? WHERE doc_id = ?",
                (int(stored), doc_id),
            )
        self._rebuild_index()
        return int(stored)

    def delete_document(self, doc_id: str) -> bool:
        with self._lock, self._connect() as con:
            cursor = con.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (doc_id,))
            con.execute("DELETE FROM rag_documents WHERE doc_id = ?", (doc_id,))
            deleted = cursor.rowcount > 0
        if deleted:
            self._rebuild_index()
        return deleted

    def reindex(self) -> dict[str, Any]:
        """Rebuild the TF-IDF index from stored chunks (safe and repeatable)."""
        self._rebuild_index()
        return self.stats()

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """Return one document's ingestion status, or None when absent."""
        with self._connect() as con:
            row = con.execute(
                """SELECT doc_id, source_filename, document_type, file_hash,
                          chunk_count, upload_timestamp, metadata
                   FROM rag_documents WHERE doc_id = ?""",
                (doc_id,),
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
    # Index + retrieval
    # ------------------------------------------------------------------
    def _all_chunks(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM rag_chunks ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def _rebuild_index(self) -> None:
        chunks = self._all_chunks()
        payload: dict[str, Any] = {"ids": [c["id"] for c in chunks]}
        if chunks:
            vectorizer = TfidfVectorizer(
                stop_words="english", max_features=20000, ngram_range=(1, 2), sublinear_tf=True
            )
            matrix = normalize(vectorizer.fit_transform([c["text"] for c in chunks]))
            payload["vocabulary"] = vectorizer.vocabulary_
            payload["idf"] = vectorizer.idf_.tolist()
            payload["matrix"] = matrix.toarray().tolist()
        else:
            payload["vocabulary"] = {}
            payload["idf"] = []
            payload["matrix"] = []
        with self._lock:
            with open(self.index_path, "wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)

    def _load_index(self) -> dict[str, Any] | None:
        if not self.index_path.exists():
            self._rebuild_index()
        try:
            with open(self.index_path, "rb") as handle:
                return pickle.load(handle)
        except (OSError, EOFError, pickle.UnpicklingError):
            self._rebuild_index()
            with open(self.index_path, "rb") as handle:
                return pickle.load(handle)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_filename: str | None = None,
        document_type: str | None = None,
        transaction_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not str(query or "").strip():
            return []
        chunks = self._all_chunks()
        if not chunks:
            return []
        index = self._load_index()
        ids = index.get("ids") or []
        matrix = index.get("matrix") or []
        vocabulary = index.get("vocabulary") or {}
        if not matrix or not vocabulary:
            return []

        import numpy as np

        vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=20000,
            ngram_range=(1, 2),
            sublinear_tf=True,
            vocabulary=vocabulary,
        )
        vectorizer.idf_ = np.asarray(index.get("idf") or [], dtype=float)
        query_matrix = normalize(vectorizer.transform([str(query)]))
        doc_matrix = np.asarray(matrix, dtype=float)
        scores = cosine_similarity(query_matrix, doc_matrix).flatten()

        by_id = {c["id"]: c for c in chunks}
        results: list[dict[str, Any]] = []
        for position, score in enumerate(scores):
            if position >= len(ids):
                break
            chunk = by_id.get(ids[position])
            if not chunk:
                continue
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
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._connect() as con:
            docs = con.execute("SELECT COUNT(*) FROM rag_documents").fetchone()[0]
            chunks = con.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
        return {
            "documents": int(docs),
            "chunks": int(chunks),
            "index_exists": self.index_path.exists(),
            "backend": "local-tfidf-sqlite",
            "data_dir": str(self.data_dir),
        }

    def list_documents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT doc_id, source_filename, document_type, file_hash,
                          chunk_count, upload_timestamp, metadata
                   FROM rag_documents ORDER BY upload_timestamp DESC LIMIT ?""",
                (min(int(limit), 200),),
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
