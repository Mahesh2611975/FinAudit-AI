"""Evidence-based RAG pipeline for financial documents and transactions.

Ingestion supports CSV transaction files, PDF documents, text reports and
persisted investigation reports.  Text is cleaned, chunked and embedded with a
local TF-IDF vector store (see :mod:`app.vector_store`) that keeps full source
metadata for citations.  No paid embedding API or model download is used.

Every public RAG façade operation requires an explicit trusted ``RagScope``.
There is no ambient or global fallback. Client-provided filters are checked for
ownership fields and can never widen access beyond the supplied scope.
"""
from __future__ import annotations

import io
import json
import re
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .models import InvestigationRun, TransactionRecord
from .ownership import (
    RAG_READ,
    RAG_WRITE,
    RagAccessDenied,
    RagScope,
    reject_client_rag_scope_input,
    validate_rag_scope,
)
from .vector_store import VectorStore, chunk_text, clean_text, text_hash

_store = VectorStore()

# Fallback review policies (used only when no documents have been ingested).
POLICIES = [
    {"source": "POLICY-DUP-01", "text": "Duplicate payment review: compare vendor, transaction date, amount, invoice reference, and description. Verify the original invoice and payment ledger before blocking or reversing a payment."},
    {"source": "POLICY-APP-02", "text": "High-value transaction review: compare transaction amount with the organization's approval threshold, purchase order, invoice, budget owner approval, and proof of delivery."},
    {"source": "POLICY-DATA-03", "text": "Data quality review: investigate missing transaction IDs, invalid dates, negative values, inconsistent currency, and missing invoice references. Preserve the original source file and document corrections."},
    {"source": "POLICY-REF-04", "text": "Invoice reconciliation: match invoice and payment records using invoice number, vendor, currency, and amount. Treat partial payments, taxes, discounts, and timing differences as possible explanations that require evidence."},
    {"source": "POLICY-HUMAN-05", "text": "Audit findings are indicators for investigation, not proof of fraud. An authorized human reviewer must validate source documents and approve financial actions."},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validated_scope(scope: RagScope | None, operation: str = RAG_READ) -> RagScope:
    """Validate a trusted scope at the façade boundary before any RAG work."""
    return validate_rag_scope(scope, operation=operation)


def _write_owner(scope: RagScope) -> int:
    """Resolve the only owner this façade is allowed to stamp on writes.

    Global writers do not identify a document owner.  Permission-backed global
    writes that need a target owner must use ``VectorStore`` directly with an
    explicit ``owner_user_id``; the façade never guesses or reuses the admin id.
    """
    validated = _validated_scope(scope, RAG_WRITE)
    if validated.global_access:
        raise RagAccessDenied(
            "RAG ingestion requires a non-global owner scope; global writes "
            "must name an explicit target owner at the storage layer."
        )
    return int(validated.owner_user_id)


def _doc_id(source_filename: str, file_hash: str, owner_user_id: int) -> str:
    """Derive an owner-scoped document id.

    Including the owner keeps same-user re-uploads idempotent while preventing
    two users who upload identical filename/content from colliding on the
    globally unique ``doc_id`` primary key.
    """
    return f"doc-{text_hash(f'{owner_user_id}:{source_filename}:{file_hash}')[:20]}"


# ----------------------------------------------------------------------
# Ingestion
# ----------------------------------------------------------------------
def ingest_text(
    text: str,
    *,
    scope: RagScope,
    source_filename: str,
    document_type: str = "text_report",
    metadata: dict[str, Any] | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Ingest arbitrary text with metadata; returns a summary."""
    owner_user_id = _write_owner(scope)
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("No readable text was found in the document.")
    file_hash = text_hash(cleaned)
    identifier = doc_id or _doc_id(source_filename, file_hash, owner_user_id)
    chunks = chunk_text(cleaned, metadata=metadata)
    count = _store.add_document(
        scope=scope,
        doc_id=identifier,
        source_filename=source_filename,
        document_type=document_type,
        file_hash=file_hash,
        chunks=chunks,
        upload_timestamp=_now(),
        metadata=metadata,
    )
    return {
        "doc_id": identifier,
        "source_filename": source_filename,
        "document_type": document_type,
        "chunks": count,
        "status": "ingested",
    }


def ingest_csv_bytes(raw: bytes, source_filename: str, *, scope: RagScope) -> dict[str, Any]:
    """Ingest a CSV as row-level evidence with provenance metadata."""
    _write_owner(scope)
    try:
        import pandas as pd

        frame = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not read CSV: {exc}") from exc

    chunks: list[dict[str, Any]] = []
    for row_index, row in frame.iterrows():
        fields = []
        for column, value in row.items():
            if value is None or (isinstance(value, float) and value != value):
                continue
            text_value = str(value).strip()
            if text_value:
                fields.append(f"{column}: {text_value}")
        if not fields:
            continue
        record = {
            "text": " | ".join(fields),
            "page_number": None,
            "transaction_id": None,
            "source_row": int(row_index) + 2,
        }
        for key in ("transaction_id", "txn_id", "transaction id", "reference", "upi_ref"):
            if key in frame.columns and str(row.get(key) or "").strip():
                record["transaction_id"] = str(row.get(key)).strip()
                break
        chunks.append(record)

    if not chunks:
        raise ValueError("The CSV contained no non-empty rows.")
    file_hash = text_hash(raw)
    return _finalize_csv(source_filename, file_hash, chunks, len(frame), scope=scope)


def _finalize_csv(
    source_filename: str,
    file_hash: str,
    chunks: list[dict[str, Any]],
    rows: int,
    *,
    scope: RagScope,
) -> dict[str, Any]:
    owner_user_id = _write_owner(scope)
    identifier = _doc_id(source_filename, file_hash, owner_user_id)
    count = _store.add_document(
        scope=scope,
        doc_id=identifier,
        source_filename=source_filename,
        document_type="transaction_csv",
        file_hash=file_hash,
        chunks=chunks,
        upload_timestamp=_now(),
        metadata={"rows": rows},
    )
    return {
        "doc_id": identifier,
        "source_filename": source_filename,
        "document_type": "transaction_csv",
        "chunks": count,
        "rows": rows,
        "status": "ingested",
    }


def _extract_pdf_with_pypdf(raw: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    return pages


def _extract_pdf_fallback(raw: bytes) -> list[str]:
    """Best-effort text extraction for simple/text PDFs without pypdf."""
    text = raw.decode("latin-1", "replace")
    pages: list[str] = []
    for match in re.finditer(r"stream\r?\n(.*?)\r?\nendstream", text, re.DOTALL):
        stream = match.group(1)
        try:
            stream = zlib.decompress(stream.encode("latin-1", "replace")).decode(
                "latin-1", "replace"
            )
        except zlib.error:
            pass
        parts = re.findall(r"\((?:\\.|[^\\()])*\)", stream)
        values = [part[1:-1].replace("\\(", "(").replace("\\)", ")") for part in parts]
        if values:
            pages.append(" ".join(values))
    return pages


def ingest_pdf_bytes(raw: bytes, source_filename: str, *, scope: RagScope) -> dict[str, Any]:
    """Ingest a PDF, preserving page numbers as metadata."""
    owner_user_id = _write_owner(scope)
    pages = _extract_pdf_with_pypdf(raw) or _extract_pdf_fallback(raw)
    if not any(page.strip() for page in pages):
        raise ValueError(
            "No extractable text was found in the PDF. "
            "Use a text-based PDF or upload the report as .txt/.csv."
        )
    chunks: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(pages, start=1):
        chunks.extend(
            chunk_text(
                page_text,
                metadata={"page_number": page_number, "transaction_id": None},
            )
        )
    file_hash = text_hash(raw)
    identifier = _doc_id(source_filename, file_hash, owner_user_id)
    count = _store.add_document(
        scope=scope,
        doc_id=identifier,
        source_filename=source_filename,
        document_type="pdf_document",
        file_hash=file_hash,
        chunks=chunks,
        upload_timestamp=_now(),
        metadata={"pages": len(pages)},
    )
    return {
        "doc_id": identifier,
        "source_filename": source_filename,
        "document_type": "pdf_document",
        "chunks": count,
        "pages": len(pages),
        "status": "ingested",
    }


def ingest_upload(raw: bytes, source_filename: str, *, scope: RagScope) -> dict[str, Any]:
    """Route an uploaded file to the correct ingestion path."""
    _write_owner(scope)
    filename = Path(source_filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return ingest_csv_bytes(raw, filename, scope=scope)
    if suffix == ".pdf":
        return ingest_pdf_bytes(raw, filename, scope=scope)
    if suffix in {".txt", ".md", ".log", ".json"}:
        text = raw.decode("utf-8", "replace")
        return ingest_text(
            text,
            scope=scope,
            source_filename=filename,
            document_type="text_report",
        )
    raise ValueError("Supported formats: .csv, .pdf, .txt, .md, .log, .json")


def ingest_transactions(db: Session, *, scope: RagScope, limit: int = 5000) -> dict[str, Any]:
    """Index persisted transaction records as searchable evidence."""
    _write_owner(scope)
    rows = (
        db.query(TransactionRecord)
        .order_by(TransactionRecord.id.desc())
        .limit(limit)
        .all()
    )
    chunks: list[dict[str, Any]] = []
    for row in rows:
        parts = [
            f"date: {row.date or 'unknown'}",
            f"direction: {row.direction or 'unknown'}",
            f"amount: {row.amount}",
            f"vendor: {row.vendor or ''}",
            f"description: {row.description or ''}",
            f"category: {row.category or ''}",
            f"reference: {row.transaction_id or row.reference or ''}",
            f"source_file: {row.source_file or ''}",
        ]
        chunks.append(
            {
                "text": " | ".join(parts),
                "page_number": None,
                "transaction_id": row.transaction_id,
                "source_row": row.source_row,
            }
        )
    if not chunks:
        return {"chunks": 0, "status": "empty"}
    file_hash = text_hash("|".join(str(row.id) for row in reversed(rows)))
    return _finalize_csv("transaction_recordsDB", file_hash, chunks, len(rows), scope=scope)


def ingest_investigation_reports(
    db: Session, *, scope: RagScope, limit: int = 100
) -> dict[str, Any]:
    """Index persisted investigation run reports as evidence."""
    owner_user_id = _write_owner(scope)
    runs = (
        db.query(InvestigationRun)
        .order_by(InvestigationRun.id.desc())
        .limit(limit)
        .all()
    )
    ingests = []
    for run in runs:
        output = run.output_state or {}
        report = output.get("outputs", {}).get("report", {}) if isinstance(output, dict) else {}
        text = json.dumps(report, ensure_ascii=False, default=str) if report else ""
        if not text:
            continue
        ingests.append(
            ingest_text(
                text,
                scope=scope,
                source_filename=f"investigation_run_{run.id}",
                document_type="investigation_report",
                metadata={"run_id": run.id, "task_type": run.task_type},
                doc_id=_doc_id(
                    f"investigation_run_{run.id}", text_hash(text), owner_user_id
                ),
            )
        )
    return {"reports": len(ingests), "status": "ingested"}


# ----------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------
def search(query: str, *, scope: RagScope, top_k: int = 5, **filters: Any) -> list[dict[str, Any]]:
    validated = _validated_scope(scope, RAG_READ)
    reject_client_rag_scope_input(filters, path="filters")
    return _store.search(query, scope=validated, top_k=top_k, **filters)


def retrieve_context(
    question: str,
    evidence: str = "",
    *,
    scope: RagScope,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Backward-compatible retrieval used by the LLM and legacy agents.

    Combines vector-store chunks (when documents exist) with the supplied
    structured evidence and the built-in review policies.  Returns items with
    ``source``, ``text`` and ``score`` keys. Every retrieval is scoped.
    """
    validated = _validated_scope(scope, RAG_READ)
    results = search(question, scope=validated, top_k=top_k)
    if not results:
        corpus = list(POLICIES)
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform([item["text"] for item in corpus] + [question])
        scores = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
        ranked = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)
        results = [
            {**corpus[i], "score": float(scores[i])}
            for i in ranked[:top_k]
            if scores[i] > 0
        ]
    if evidence and str(evidence).strip():
        results.append(
            {
                "source": "STRUCTURED-EVIDENCE",
                "text": str(evidence)[:4000],
                "score": 0.999,
            }
        )
    return results


def store_stats(*, scope: RagScope) -> dict[str, Any]:
    validated = _validated_scope(scope, RAG_READ)
    return _store.stats(scope=validated)


def list_documents(*, scope: RagScope, limit: int = 50) -> list[dict[str, Any]]:
    validated = _validated_scope(scope, RAG_READ)
    return _store.list_documents(scope=validated, limit=limit)


# ----------------------------------------------------------------------
# Management: deletion, re-indexing and retrieval debugging
# ----------------------------------------------------------------------
def delete_document(doc_id: str, *, scope: RagScope) -> bool:
    """Remove a document and all of its chunks, then rebuild the index."""
    validated = _validated_scope(scope, RAG_WRITE)
    return _store.delete_document(scope=validated, doc_id=doc_id)


def reindex(*, scope: RagScope) -> dict[str, Any]:
    """Rebuild the scoped local TF-IDF index from authorized chunks."""
    validated = _validated_scope(scope, RAG_WRITE)
    return _store.reindex(scope=validated)


def document_status(doc_id: str, *, scope: RagScope) -> dict[str, Any] | None:
    """Return per-document ingestion status, including chunk count."""
    validated = _validated_scope(scope, RAG_READ)
    return _store.get_document(scope=validated, doc_id=doc_id)


def retrieval_test(
    query: str,
    *,
    scope: RagScope,
    top_k: int = 5,
    **filters: Any,
) -> dict[str, Any]:
    """Return retrieved chunks and citations for debugging RAG quality.

    This is a retrieval-only helper. It never invokes the LLM and never
    fabricates evidence: an empty result is reported explicitly. Client filters
    are validated and cannot override ownership.
    """
    validated = _validated_scope(scope, RAG_READ)
    reject_client_rag_scope_input(filters, path="filters")
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required.")
    chunks = search(query, scope=validated, top_k=max(1, min(top_k, 20)), **filters)
    return {
        "query": query,
        "has_evidence": bool(chunks),
        "result_count": len(chunks),
        "chunks": chunks,
        "stats": store_stats(scope=validated),
        "message": None if chunks else "No indexed chunks matched this query.",
    }
