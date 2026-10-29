import os
import tempfile

import pandas as pd
import pytest

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-reconciliation-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = (
    f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"
)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.reconciliation import ReconciliationError, reconcile_frames  # noqa: E402

client = TestClient(app)


def frame(rows, columns=None):
    return pd.DataFrame(rows, columns=columns)


def result_for(outcome, **matches):
    for item in outcome.results:
        if all(item.get(key) == value for key, value in matches.items()):
            return item
    raise AssertionError(f"No result matched {matches}: {outcome.results}")


def test_exact_reference_match_is_preferred():
    bank = frame(
        [
            {
                "date": "2026-04-01",
                "type": "debit",
                "amount": "100.00",
                "reference": "UPI-XYZ-001",
                "description": "Office supplies",
            }
        ]
    )
    ledger = frame(
        [
            {
                "date": "2026-04-01",
                "type": "debit",
                "amount": "100.00",
                "reference": "UPI-XYZ-001",
                "description": "Office supplies",
            }
        ]
    )

    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])

    assert outcome.summary["matched_count"] == 1
    item = outcome.results[0]
    assert item["status"] == "matched"
    assert item["bank_source_file"] == "bank.csv"
    assert item["ledger_source_file"] == "ledger.csv"
    assert item["bank_source_row"] == 1
    assert item["ledger_source_row"] == 1


def test_match_without_reference_uses_amount_direction_and_date():
    bank = frame(
        [{"date": "2026-04-02", "type": "credit", "amount": "250.00", "narration": "Client payment"}]
    )
    ledger = frame(
        [{"date": "2026-04-03", "type": "credit", "amount": "250.00", "details": "Client payment"}]
    )

    outcome = reconcile_frames(
        [("bank.csv", bank)],
        [("ledger.csv", ledger)],
        date_tolerance_days=1,
    )

    assert outcome.summary["matched_count"] == 1
    assert outcome.results[0]["status"] == "matched"
    assert outcome.results[0]["date_difference_days"] == 1


def test_debit_and_credit_direction_are_not_matched():
    bank = frame(
        [{"date": "2026-04-04", "type": "debit", "amount": "75.00"}]
    )
    ledger = frame(
        [{"date": "2026-04-04", "type": "credit", "amount": "75.00"}]
    )

    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])

    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["bank_only_count"] == 1
    assert outcome.summary["ledger_only_count"] == 1
    assert {item["status"] for item in outcome.results} == {"bank_only", "ledger_only"}


def test_duplicate_amounts_are_ambiguous_not_forced():
    duplicate_bank = [
        {"date": "2026-04-05", "type": "debit", "amount": "500.00", "description": "Transfer"},
        {"date": "2026-04-05", "type": "debit", "amount": "500.00", "description": "Transfer"},
    ]
    bank = frame(duplicate_bank)
    ledger = frame(
        [{"date": "2026-04-05", "type": "debit", "amount": "500.00", "description": "Transfer"}]
    )

    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])

    assert outcome.summary["matched_count"] == 0
    assert outcome.summary["ambiguous_count"] == 3
    assert {item["status"] for item in outcome.results} == {"ambiguous"}


def test_amount_mismatch_is_classified_when_reference_matches():
    bank = frame(
        [{"date": "2026-04-06", "type": "debit", "amount": "100.00", "reference": "REF-MISMATCH"}]
    )
    ledger = frame(
        [{"date": "2026-04-06", "type": "debit", "amount": "90.00", "reference": "REF-MISMATCH"}]
    )

    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])

    assert outcome.summary["amount_mismatch_count"] == 1
    item = outcome.results[0]
    assert item["status"] == "amount_mismatch"
    assert item["amount_difference"] == 10.0


def test_date_tolerance_boundaries():
    def pair(bank_date, ledger_date):
        return (
            frame(
                [{"date": bank_date, "type": "debit", "amount": "20.00", "reference": "REF-DATE"}]
            ),
            frame(
                [{"date": ledger_date, "type": "debit", "amount": "20.00", "reference": "REF-DATE"}]
            ),
        )

    bank, ledger = pair("2026-04-07", "2026-04-10")
    within = reconcile_frames(
        [("bank.csv", bank)], [("ledger.csv", ledger)], date_tolerance_days=3
    )
    assert within.results[0]["status"] == "matched"

    bank, ledger = pair("2026-04-07", "2026-04-11")
    outside = reconcile_frames(
        [("bank.csv", bank)], [("ledger.csv", ledger)], date_tolerance_days=3
    )
    assert outside.results[0]["status"] == "date_mismatch"


def test_split_debit_and_credit_columns_preserve_direction():
    bank = frame(
        [
            {"txn_date": "2026-04-08", "withdrawal": "40.00", "deposit": "", "utr": "S-1"},
            {"txn_date": "2026-04-08", "withdrawal": "", "deposit": "80.00", "utr": "S-2"},
        ]
    )
    ledger = frame(
        [
            {"transaction_date": "2026-04-08", "debit": "40.00", "credit": "", "reference": "S-1"},
            {"transaction_date": "2026-04-08", "debit": "", "credit": "80.00", "reference": "S-2"},
        ]
    )

    outcome = reconcile_frames([("bank.csv", bank)], [("ledger.csv", ledger)])

    assert outcome.summary["matched_count"] == 2
    assert {item["bank_direction"] for item in outcome.results} == {"debit", "credit"}
    assert outcome.summary["bank_total"] == 120.0
    assert outcome.summary["ledger_total"] == 120.0


def test_missing_and_invalid_columns_are_reported():
    with pytest.raises(ReconciliationError) as missing_date:
        reconcile_frames(
            [("bank.csv", frame([{"amount": "10.00"}]))],
            [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))],
        )
    assert "Missing required columns" in " ".join(missing_date.value.errors)

    with pytest.raises(ReconciliationError) as missing_amount:
        reconcile_frames(
            [("bank.csv", frame([{"date": "2026-04-09"}]))],
            [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))],
        )
    assert "amount" in " ".join(missing_amount.value.errors).lower()

    with pytest.raises(ReconciliationError) as invalid:
        reconcile_frames(
            [("bank.csv", frame([{"date": "2026-04-09", "amount": "not-a-number"}]))],
            [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))],
        )
    text = " ".join(invalid.value.errors).lower()
    assert "invalid amount" in text

    with pytest.raises(ReconciliationError) as bad_date:
        reconcile_frames(
            [("bank.csv", frame([{"date": "not-a-date", "amount": "10.00"}]))],
            [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))],
        )
    assert "invalid date" in " ".join(bad_date.value.errors).lower()


def test_empty_files_are_rejected():
    with pytest.raises(ReconciliationError) as empty:
        reconcile_frames(
            [("bank.csv", frame([], columns=["date", "amount"]))],
            [("ledger.csv", frame([{"date": "2026-04-09", "amount": "10.00"}]))],
        )
    assert "no transaction rows" in " ".join(empty.value.errors).lower()


def test_conflicting_references_are_rejected():
    conflicting = frame(
        [
            {"date": "2026-04-10", "amount": "10.00", "reference": "SAME-REF", "description": "A"},
            {"date": "2026-04-10", "amount": "99.00", "reference": "SAME-REF", "description": "B"},
        ]
    )
    ledger = frame([{"date": "2026-04-10", "amount": "10.00", "reference": "SAME-REF"}])

    with pytest.raises(ReconciliationError) as error:
        reconcile_frames([("bank.csv", conflicting)], [("ledger.csv", ledger)])

    assert "conflicting transactions" in " ".join(error.value.errors).lower()


def test_multiple_source_files_keep_provenance_and_totals():
    bank_a = frame(
        [
            {"date": "2026-04-11", "amount": "10.00", "reference": "A-1"},
            {"date": "2026-04-11", "amount": "15.00", "reference": "A-2"},
        ]
    )
    bank_b = frame(
        [{"date": "2026-04-12", "amount": "25.00", "reference": "B-1"}]
    )
    ledger = frame(
        [
            {"date": "2026-04-11", "amount": "15.00", "reference": "A-2"},
            {"date": "2026-04-12", "amount": "25.00", "reference": "B-1"},
        ]
    )

    outcome = reconcile_frames(
        [("bank-a.csv", bank_a), ("bank-b.csv", bank_b)],
        [("ledger-main.csv", ledger)],
    )

    matched = [item for item in outcome.results if item["status"] == "matched"]
    assert len(matched) == 2
    assert {item["bank_source_file"] for item in matched} == {"bank-a.csv", "bank-b.csv"}
    assert {item["ledger_source_file"] for item in matched} == {"ledger-main.csv"}

    a2 = result_for(outcome, bank_reference="A-2")
    assert a2["bank_source_file"] == "bank-a.csv"
    assert a2["bank_source_row"] == 2
    assert a2["ledger_source_row"] == 1

    assert outcome.summary["bank_total"] == 50.0
    assert outcome.summary["ledger_total"] == 40.0
    assert outcome.summary["matched_total"] == 40.0
    assert outcome.summary["difference"] == 10.0
    assert outcome.summary["bank_unmatched_total"] == 10.0


def test_api_persists_results_and_exports_csv():
    bank_csv = (
        "date,type,amount,reference,description\n"
        "2026-04-13,debit,100.00,REF-API-1,Office supplies\n"
        "2026-04-14,credit,50.00,REF-API-2,Client payment\n"
    )
    ledger_csv = (
        "date,type,amount,reference,description\n"
        "2026-04-13,debit,100.00,REF-API-1,Office supplies\n"
        "2026-04-14,credit,45.00,REF-API-2,Client payment\n"
        "2026-04-15,debit,25.00,REF-API-3,Supplies\n"
    )

    response = client.post(
        "/api/reconciliations",
        data={"date_tolerance_days": "3"},
        files={
            "bank_file": ("bank-api.csv", bank_csv.encode("utf-8"), "text/csv"),
            "ledger_file": ("ledger-api.csv", ledger_csv.encode("utf-8"), "text/csv"),
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    reconciliation_id = data["reconciliation_id"]
    assert data["summary"]["matched_count"] == 1
    assert data["summary"]["amount_mismatch_count"] == 1
    assert data["summary"]["ledger_only_count"] == 1
    assert data["results"][0]["bank_source_file"] == "bank-api.csv"

    detail = client.get(f"/api/reconciliations/{reconciliation_id}")
    assert detail.status_code == 200
    assert detail.json()["bank_file"] == "bank-api.csv"
    assert detail.json()["ledger_file"] == "ledger-api.csv"

    listing = client.get("/api/reconciliations")
    assert listing.status_code == 200
    assert any(item["id"] == reconciliation_id for item in listing.json())

    exported = client.get(f"/api/reconciliations/{reconciliation_id}/export.csv")
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "attachment;" in exported.headers["content-disposition"]
    assert "REF-API-1" in exported.text
    assert "amount_mismatch" in exported.text


def test_api_rejects_empty_and_invalid_reconciliation_files():
    empty = client.post(
        "/api/reconciliations",
        files={
            "bank_file": ("empty.csv", b"", "text/csv"),
            "ledger_file": (
                "ledger.csv",
                b"date,amount\n2026-04-16,10.00\n",
                "text/csv",
            ),
        },
    )
    assert empty.status_code == 422

    missing = client.post(
        "/api/reconciliations",
        files={
            "bank_file": ("bank.csv", b"date,amount\n2026-04-16,10.00\n", "text/csv"),
            "ledger_file": ("ledger.csv", b"vendor\nAcme\n", "text/csv"),
        },
    )
    assert missing.status_code == 422
    assert "Missing required columns" in " ".join(missing.json()["detail"])


def test_existing_audit_endpoint_still_works():
    response = client.post(
        "/api/audits",
        files={
            "file": (
                "audit-smoke.csv",
                b"transaction_id,date,vendor,amount,description\n"
                b"T900,2026-04-17,Acme,120.00,Office supplies\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["row_count"] == 1
