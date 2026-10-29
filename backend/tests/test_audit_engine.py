import pandas as pd
import pytest
from app.audit_engine import detect_findings, validate_transactions

SAMPLE = pd.DataFrame([
    {"transaction_id":"T001", "date":"2026-01-02", "vendor":"Acme", "amount":120.0, "description":"Office supplies"},
    {"transaction_id":"T002", "date":"2026-01-02", "vendor":"Acme", "amount":120.0, "description":"Office supplies"},
    {"transaction_id":"T003", "date":"2026-01-03", "vendor":"Big Vendor", "amount":15000.0, "description":"Equipment"},
    {"transaction_id":"T004", "date":"2026-01-04", "vendor":"Refund Co", "amount":-50.0, "description":"Refund"},
])

def test_duplicate_rows_are_flagged():
    findings = detect_findings(SAMPLE)
    assert sum(f["finding_type"] == "possible_duplicate" for f in findings) == 2

def test_high_amount_is_flagged():
    findings = detect_findings(SAMPLE)
    assert any(f["finding_type"] == "high_amount" and f["transaction_id"] == "T003" for f in findings)

def test_negative_amount_is_flagged():
    findings = detect_findings(SAMPLE)
    assert any(f["finding_type"] == "negative_amount" for f in findings)

def test_missing_columns_reported():
    assert "Missing required columns" in validate_transactions(pd.DataFrame({"vendor": ["Acme"]}))[0]



def test_invalid_amount_rejected():
    bad = pd.DataFrame([
        {"transaction_id":"T001", "date":"2026-01-02", "vendor":"Acme", "amount":120.0, "description":"Office supplies"},
        {"transaction_id":"T002", "date":"2026-01-03", "vendor":"Acme", "amount":"not-a-number", "description":"Office supplies"},
    ])

    errors = validate_transactions(bad)

    assert any(
        "invalid amount" in error.lower()
        for error in errors
    )
