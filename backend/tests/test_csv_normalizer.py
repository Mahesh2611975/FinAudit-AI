import pandas as pd

from app.audit_engine import detect_findings, validate_transactions
from app.csv_normalizer import normalize_files

SAMPLE = pd.DataFrame(
    [
        {"transaction_id": "T001", "date": "2026-01-02", "vendor": "Acme", "amount": 120.0, "description": "Office supplies"},
        {"transaction_id": "T002", "date": "2026-01-02", "vendor": "Acme", "amount": 120.0, "description": "Office supplies"},
        {"transaction_id": "T003", "date": "2026-01-03", "vendor": "Big Vendor", "amount": 15000.0, "description": "Equipment"},
        {"transaction_id": "T004", "date": "2026-01-04", "vendor": "Refund Co", "amount": -50.0, "description": "Refund"},
    ]
)

BANK = pd.DataFrame(
    [
        {
            "date": "2026-02-01",
            "type": "debit",
            "amount": "250.50",
            "account": "XXXX1234",
            "recipient": "Swiggy",
            "upi_ref": "UPI-001",
            "balance_after": "10000.00",
            "source_sms": "Paid Rs 250.50 to Swiggy",
        },
        {
            "date": "2026-02-01",
            "type": "credit",
            "amount": "500.00",
            "account": "XXXX1234",
            "recipient": "Employer",
            "upi_ref": "UPI-002",
            "balance_after": "10500.00",
            "source_sms": "Received Rs 500.00 from Employer",
        },
    ]
)


def test_original_sample_format_still_works():
    result = normalize_files([("sample.csv", SAMPLE)])

    assert result.errors == []
    assert list(result.frame["transaction_id"]) == ["T001", "T002", "T003", "T004"]
    findings = detect_findings(result.frame)
    assert sum(item["finding_type"] == "possible_duplicate" for item in findings) == 2
    assert any(item["finding_type"] == "high_amount" and item["transaction_id"] == "T003" for item in findings)
    assert any(item["finding_type"] == "negative_amount" for item in findings)


def test_bank_format_is_mapped_to_internal_fields():
    result = normalize_files([("bank.csv", BANK)])

    assert result.errors == []
    frame = result.frame
    assert list(frame["vendor"]) == ["Swiggy", "Employer"]
    assert list(frame["direction"]) == ["debit", "credit"]
    assert list(frame["signed_amount"]) == [-250.5, 500.0]
    assert frame.loc[0, "account"] == "XXXX1234"
    assert frame.loc[0, "upi_ref"] == "UPI-001"
    assert frame.loc[0, "balance_after"] == 10000.0
    assert frame.loc[0, "source_sms"] == "Paid Rs 250.50 to Swiggy"
    assert frame.loc[0, "source_file"] == "bank.csv"
    assert str(frame.loc[0, "transaction_id"]).startswith("GEN-")


def test_bank_debit_and_credit_direction_is_preserved():
    findings = detect_findings(BANK)

    assert findings == []
    assert not any(item["finding_type"] == "negative_amount" for item in findings)
    assert not any("credit" in item["summary"].lower() for item in findings)


def test_large_credit_is_high_value_but_not_treated_as_fraud():
    bank = pd.DataFrame(
        [
            {
                "date": "2026-02-02",
                "type": "credit",
                "amount": "15000.00",
                "account": "XXXX1234",
                "recipient": "Employer",
                "upi_ref": "UPI-003",
                "balance_after": "25000.00",
                "source_sms": "Salary credited",
            }
        ]
    )

    findings = detect_findings(bank)

    assert any(item["finding_type"] == "high_amount" for item in findings)
    assert not any(item["finding_type"] == "negative_amount" for item in findings)


def test_generated_ids_are_stable_and_unique_for_identical_rows():
    duplicate_rows = pd.DataFrame(
        [
            {"date": "2026-03-01", "type": "debit", "amount": "100", "recipient": "Shop", "upi_ref": "", "balance_after": "900"},
            {"date": "2026-03-01", "type": "debit", "amount": "100", "recipient": "Shop", "upi_ref": "", "balance_after": "900"},
        ]
    )

    first = normalize_files([("same.csv", duplicate_rows)]).frame
    second = normalize_files([("same.csv", duplicate_rows)]).frame

    assert list(first["transaction_id"]) == list(second["transaction_id"])
    assert first["transaction_id"].nunique() == 2


def test_missing_vendor_and_description_are_not_invented():
    rows = pd.DataFrame(
        [
            {"date": "2026-03-02", "type": "debit", "amount": "10", "recipient": "", "description": ""},
        ]
    )

    frame = normalize_files([("blank-party.csv", rows)]).frame

    assert frame.loc[0, "vendor"] == ""
    assert frame.loc[0, "description"] == ""


def test_missing_and_invalid_values_return_clear_errors():
    rows = pd.DataFrame(
        [
            {"date": "", "type": "debit", "amount": "", "recipient": "Shop"},
            {"date": "not-a-date", "type": "debit", "amount": "not-a-number", "recipient": "Shop"},
        ]
    )

    errors = normalize_files([("bad.csv", rows)]).errors
    text = " ".join(errors).lower()

    assert "missing date" in text
    assert "missing amount" in text
    assert "invalid date" in text
    assert "invalid amount" in text


def test_empty_rows_are_rejected():
    errors = normalize_files([("empty.csv", pd.DataFrame([{"date": "", "amount": ""}]))]).errors

    assert any("no transaction rows" in error.lower() for error in errors)


def test_missing_required_columns_are_reported():
    errors = validate_transactions(pd.DataFrame({"vendor": ["Acme"]}))

    assert any("Missing required columns" in error for error in errors)


def test_conflicting_duplicate_reference_is_a_validation_error():
    rows = pd.DataFrame(
        [
            {"date": "2026-03-03", "type": "debit", "amount": "10", "recipient": "A", "upi_ref": "SAME-REF"},
            {"date": "2026-03-03", "type": "debit", "amount": "99", "recipient": "B", "upi_ref": "SAME-REF"},
        ]
    )

    errors = normalize_files([("conflict.csv", rows)]).errors

    assert any("Duplicate UPI reference" in error for error in errors)


def test_same_reference_with_identical_data_is_a_finding_not_an_error():
    rows = pd.DataFrame(
        [
            {"date": "2026-03-04", "type": "debit", "amount": "10", "recipient": "A", "upi_ref": "SAME-REF"},
            {"date": "2026-03-04", "type": "debit", "amount": "10", "recipient": "A", "upi_ref": "SAME-REF"},
        ]
    )

    result = normalize_files([("same-ref.csv", rows)])
    findings = detect_findings(result.frame)

    assert result.errors == []
    assert sum(item["finding_type"] == "possible_duplicate" for item in findings) == 2


def test_cross_file_duplicates_are_detected_and_keep_source_filenames():
    rows = pd.DataFrame(
        [
            {
                "date": "2026-03-05",
                "type": "debit",
                "amount": "300",
                "recipient": "Vendor",
                "upi_ref": "UPI-CROSS-1",
                "balance_after": "700",
            }
        ]
    )

    result = normalize_files([("january.csv", rows), ("february.csv", rows)])
    findings = detect_findings(result.frame)
    duplicates = [item for item in findings if item["finding_type"] == "possible_duplicate"]

    assert result.errors == []
    assert len(duplicates) == 2
    assert {item["source_file"] for item in duplicates} == {"january.csv", "february.csv"}
    assert all(item["transaction_id"] for item in duplicates)
