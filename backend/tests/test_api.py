import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-api-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

BANK_CSV = (
    "date,type,amount,account,recipient,upi_ref,balance_after,source_sms\n"
    "2026-02-01,debit,250.50,XXXX1234,Swiggy,UPI-001,10000.00,Paid Rs 250.50\n"
    "2026-02-01,credit,500.00,XXXX1234,Employer,UPI-002,10500.00,Received Rs 500.00\n"
)

SAMPLE_CSV = (
    "transaction_id,date,vendor,amount,description\n"
    "T001,2026-01-02,Acme,120.00,Office supplies\n"
    "T002,2026-01-02,Acme,120.00,Office supplies\n"
)


def test_single_sample_upload_uses_legacy_field_name():
    response = client.post(
        "/api/audits",
        files={"file": ("sample.csv", SAMPLE_CSV.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["files"] == ["sample.csv"]
    assert data["row_count"] == 2
    assert data["finding_count"] == 2


def test_multiple_bank_files_are_combined_and_keep_filenames():
    response = client.post(
        "/api/audits",
        files=[
            ("files", ("bank-a.csv", BANK_CSV.encode("utf-8"), "text/csv")),
            ("files", ("bank-b.csv", BANK_CSV.encode("utf-8"), "text/csv")),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["files"] == ["bank-a.csv", "bank-b.csv"]
    assert data["row_count"] == 4
    duplicate_findings = [
        item for item in data["findings"] if item["finding_type"] == "possible_duplicate"
    ]
    assert len(duplicate_findings) == 4
    assert {item["source_file"] for item in duplicate_findings} == {"bank-a.csv", "bank-b.csv"}
    assert "XXXX1234" not in response.text
    assert "Paid Rs" not in response.text


def test_validation_errors_are_returned_to_the_client():
    bad_csv = (
        "date,type,amount,account,recipient,upi_ref,balance_after,source_sms\n"
        "not-a-date,debit,not-a-number,XXXX1234,Shop,UPI-003,1000.00,Paid\n"
    )

    response = client.post(
        "/api/audits",
        files={"files": ("bad.csv", bad_csv.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 422
    detail = " ".join(response.json()["detail"]).lower()
    assert "invalid date" in detail
    assert "invalid amount" in detail


def test_missing_columns_are_returned_to_the_client():
    response = client.post(
        "/api/audits",
        files={"files": ("missing.csv", b"vendor\nAcme\n", "text/csv")},
    )

    assert response.status_code == 422
    assert "Missing required columns" in " ".join(response.json()["detail"])
