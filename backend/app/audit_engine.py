from __future__ import annotations

import pandas as pd

from .csv_normalizer import is_normalized, normalize_files

REQUIRED_COLUMNS = {"date", "amount"}


def validate_transactions(df: pd.DataFrame) -> list[str]:
    if is_normalized(df):
        if df.empty:
            return ["CSV contains no transaction rows."]
        amounts = pd.to_numeric(df["amount"], errors="coerce")
        if amounts.isna().any():
            return [f"{int(amounts.isna().sum())} row(s) have invalid amount values."]
        return []
    return normalize_files([("<memory>", df)]).errors


def _counterparty(row: pd.Series) -> str:
    vendor = str(row.get("vendor") or "").strip()
    return vendor or "counterparty not provided"


def _direction_label(row: pd.Series) -> str:
    direction = str(row.get("direction") or "").strip().lower()
    return direction if direction in {"debit", "credit"} else ""


def _source_location(row: pd.Series) -> str:
    source_file = str(row.get("source_file") or "").strip()
    source_row = row.get("source_row")
    if source_file and source_row is not None and str(source_row).strip():
        return f"{source_file} row {source_row}"
    return source_file or "uploaded data"


def _base_evidence(row: pd.Series) -> list[str]:
    parts = [
        f"Transaction={row.get('transaction_id', '')}",
        f"Source={_source_location(row)}",
        f"Date={row.get('date', '')}",
        f"Amount={abs(float(row['amount'])):.2f}",
    ]
    direction = _direction_label(row)
    if direction:
        parts.append(f"Direction={direction}")
    vendor = str(row.get("vendor") or "").strip()
    if vendor:
        parts.append(f"Counterparty={vendor}")
    description = str(row.get("description") or "").strip()
    if description:
        parts.append(f"Description={description}")
    upi_ref = str(row.get("upi_ref") or "").strip()
    if upi_ref:
        parts.append(f"UPI reference={upi_ref}")
    return parts


def detect_findings(df: pd.DataFrame, high_amount_threshold: float = 10000.0) -> list[dict]:
    """Rule-based exception detection. Findings are leads for review, not proof of fraud."""
    if not is_normalized(df):
        result = normalize_files([("<memory>", df)])
        if result.errors:
            raise ValueError(" ".join(result.errors))
        df = result.frame
    elif df.empty:
        raise ValueError("CSV contains no transaction rows.")

    df = df.copy()
    amounts = pd.to_numeric(df["amount"], errors="coerce")
    if amounts.isna().any():
        raise ValueError(f"{int(amounts.isna().sum())} row(s) have invalid amount values.")
    df["amount"] = amounts

    findings: list[dict] = []

    for duplicate_key, group in df.groupby("duplicate_key", sort=False, dropna=False):
        if not duplicate_key or len(group) < 2:
            continue

        files = sorted({str(value) for value in group["source_file"].dropna().unique() if str(value)})
        for _, row in group.iterrows():
            counterparty = _counterparty(row)
            summary = f"Possible duplicate payment to {counterparty}"
            evidence_parts = _base_evidence(row)
            if len(files) > 1:
                summary = f"Possible duplicate transaction across files: {counterparty}"
                evidence_parts.append(
                    "The same transaction key appears in multiple source files: "
                    + ", ".join(files)
                    + "."
                )
            else:
                evidence_parts.append(
                    "Another row in the same source file has the same date, direction, "
                    "amount, counterparty, description, reference, and balance."
                )
            findings.append(
                {
                    "transaction_id": str(row.get("transaction_id", "")),
                    "source_file": str(row.get("source_file", "")),
                    "finding_type": "possible_duplicate",
                    "risk_level": "high",
                    "amount": float(row["amount"]),
                    "summary": summary,
                    "evidence": "; ".join(evidence_parts),
                }
            )

    for _, row in df.iterrows():
        amount = float(row["amount"])
        if abs(amount) < high_amount_threshold:
            continue
        counterparty = _counterparty(row)
        direction = _direction_label(row)
        summary = (
            f"High-value {direction} transaction: {counterparty}"
            if direction
            else f"High-value transaction: {counterparty}"
        )
        evidence_parts = _base_evidence(row)
        evidence_parts.append(
            f"The absolute amount meets the configured review threshold "
            f"{high_amount_threshold:.2f}. Review against approval policy and supporting documents."
        )
        findings.append(
            {
                "transaction_id": str(row.get("transaction_id", "")),
                "source_file": str(row.get("source_file", "")),
                "finding_type": "high_amount",
                "risk_level": "medium",
                "amount": amount,
                "summary": summary,
                "evidence": "; ".join(evidence_parts),
            }
        )

    for _, row in df.iterrows():
        direction = _direction_label(row)
        if direction in {"debit", "credit"}:
            continue
        if float(row["amount"]) >= 0:
            continue
        counterparty = _counterparty(row)
        evidence_parts = _base_evidence(row)
        evidence_parts.append(
            "Confirm whether this is a valid refund, reversal, or data-entry error."
        )
        findings.append(
            {
                "transaction_id": str(row.get("transaction_id", "")),
                "source_file": str(row.get("source_file", "")),
                "finding_type": "negative_amount",
                "risk_level": "medium",
                "amount": float(row["amount"]),
                "summary": f"Negative amount requires classification: {counterparty}",
                "evidence": "; ".join(evidence_parts),
            }
        )

    return findings
