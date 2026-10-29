from __future__ import annotations
import pandas as pd

REQUIRED_COLUMNS = {"transaction_id", "date", "vendor", "amount", "description"}

def validate_transactions(df: pd.DataFrame) -> list[str]:
    errors = []
    missing = sorted(REQUIRED_COLUMNS - set(df.columns.str.strip().str.lower()))
    if missing:
        errors.append("Missing required columns: " + ", ".join(missing))
        return errors
    if df.empty:
        errors.append("CSV contains no transaction rows.")
    if "amount" in df.columns:
        amounts = pd.to_numeric(df["amount"], errors="coerce")
        if amounts.isna().any(): errors.append(f"{int(amounts.isna().sum())} row(s) have invalid amount values.")
    return errors

def detect_findings(df: pd.DataFrame, high_amount_threshold: float = 10000.0) -> list[dict]:
    """Rule-based exception detection. Findings are leads for review, not proof of fraud."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    errors = validate_transactions(df)
    if errors:
        raise ValueError(" ".join(errors))
    df["amount"] = pd.to_numeric(df["amount"], errors="raise")
    findings: list[dict] = []

    duplicate_mask = df.duplicated(subset=["vendor", "date", "amount", "description"], keep=False)
    for _, row in df[duplicate_mask].iterrows():
        findings.append({
            "transaction_id": str(row.get("transaction_id", "")),
            "finding_type": "possible_duplicate", "risk_level": "high",
            "amount": float(row["amount"]),
            "summary": f"Possible duplicate payment to {row['vendor']}",
            "evidence": f"Vendor={row['vendor']}; date={row['date']}; amount={float(row['amount']):.2f}; description={row['description']}. Another row has the same vendor, date, amount, and description."
        })

    for _, row in df[df["amount"] >= high_amount_threshold].iterrows():
        findings.append({
            "transaction_id": str(row.get("transaction_id", "")),
            "finding_type": "high_amount", "risk_level": "medium",
            "amount": float(row["amount"]),
            "summary": f"High-value transaction: {row['vendor']}",
            "evidence": f"Transaction {row.get('transaction_id', '')} has amount {float(row['amount']):.2f}, meeting the configured threshold {high_amount_threshold:.2f}. Review against approval policy and supporting documents."
        })

    for _, row in df[df["amount"] < 0].iterrows():
        findings.append({
            "transaction_id": str(row.get("transaction_id", "")),
            "finding_type": "negative_amount", "risk_level": "medium",
            "amount": float(row["amount"]),
            "summary": f"Negative amount requires classification: {row['vendor']}",
            "evidence": f"Transaction {row.get('transaction_id', '')} has a negative amount ({float(row['amount']):.2f}). Confirm whether this is a valid refund, reversal, or data-entry error."
        })
    return findings
