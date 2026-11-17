"""Deterministic tests for reconciliation mapping/date tolerance and CSV exports."""
import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-exports-tests-")
os.environ.setdefault(
    "FINAUDIT_DATABASE_URL",
    f"sqlite:///{os.path.join(TEST_DB_DIR, 'exports.db')}",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


def build_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'exports.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), factory, engine


def teardown(engine):
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


# Independent column names on each side; the engine default aliases do not match.
BANK_CSV = (
    "Posting Date,Debit Amt,Credit Amt,UTR,Particulars\n"
    "2026-04-01,100.00,,REF-100,Office supplies\n"
    "2026-04-05,250.00,,REF-250,Clients payment\n"
)
LEDGER_CSV = (
    "Booking Date,Dr Value,Cr Value,Voucher,Narration\n"
    "2026-04-01,100.00,,REF-100,Office supplies\n"
    "2026-04-06,250.00,,REF-250,Clients payment\n"
)
BANK_MAPPING = '{"date":"Posting Date","debit":"Debit Amt","credit":"Credit Amt","reference":"UTR","description":"Particulars"}'
LEDGER_MAPPING = '{"date":"Booking Date","debit":"Dr Value","credit":"Cr Value","reference":"Voucher","description":"Narration"}'


def test_reconciliation_independent_mapping_and_date_tolerance(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        # Tolerance 0: the 1-day-differing row must not be a clean match.
        strict = client.post(
            "/api/reconciliations",
            files={
                "bank_file": ("bank.csv", BANK_CSV.encode(), "text/csv"),
                "ledger_file": ("ledger.csv", LEDGER_CSV.encode(), "text/csv"),
            },
            data={
                "date_tolerance_days": "0",
                "bank_mapping": BANK_MAPPING,
                "ledger_mapping": LEDGER_MAPPING,
            },
        )
        assert strict.status_code == 200, strict.text
        strict_data = strict.json()
        assert strict_data["bank_mapping"]["date"] == "Posting Date"
        assert strict_data["ledger_mapping"]["date"] == "Booking Date"
        assert strict_data["date_tolerance_days"] == 0
        assert strict_data["summary"]["matched_count"] == 1
        statuses = {item["status"] for item in strict_data["results"]}
        assert statuses != {"matched"}, (
            "With zero tolerance, the 1-day-differing row must not be a clean match"
        )

        # Tolerance 1: the same row is now a clean match.
        tolerant = client.post(
            "/api/reconciliations",
            files={
                "bank_file": ("bank.csv", BANK_CSV.encode(), "text/csv"),
                "ledger_file": ("ledger.csv", LEDGER_CSV.encode(), "text/csv"),
            },
            data={
                "date_tolerance_days": "1",
                "bank_mapping": BANK_MAPPING,
                "ledger_mapping": LEDGER_MAPPING,
            },
        )
        assert tolerant.status_code == 200, tolerant.text
        tolerant_data = tolerant.json()
        assert tolerant_data["date_tolerance_days"] == 1
        assert tolerant_data["summary"]["matched_count"] == 2
        assert all(item["status"] == "matched" for item in tolerant_data["results"])

        # Run detail persists applied mappings.
        detail = client.get(
            f"/api/reconciliations/{tolerant_data['reconciliation_id']}"
        ).json()
        assert detail["bank_mapping"]["reference"] == "UTR"
        assert detail["ledger_mapping"]["reference"] == "Voucher"
    finally:
        teardown(engine)


def test_invalid_mapping_is_rejected(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        bad_field = client.post(
            "/api/reconciliations",
            files={
                "bank_file": ("bank.csv", BANK_CSV.encode(), "text/csv"),
                "ledger_file": ("ledger.csv", LEDGER_CSV.encode(), "text/csv"),
            },
            data={"bank_mapping": '{"unknown_field":"Posting Date"}'},
        )
        assert bad_field.status_code == 422

        bad_column = client.post(
            "/api/reconciliations",
            files={
                "bank_file": ("bank.csv", BANK_CSV.encode(), "text/csv"),
                "ledger_file": ("ledger.csv", LEDGER_CSV.encode(), "text/csv"),
            },
            data={"bank_mapping": '{"date":"No Such Column"}'},
        )
        assert bad_column.status_code == 422
    finally:
        teardown(engine)


TXN_CSV = (
    "transaction_id,date,type,vendor,amount,description\n"
    "TXN-X,2040-02-01,debit,Alpha Mart,120.00,Office supplies\n"
    "TXN-Y,2040-02-02,credit,Beta Client,900.00,Invoice payment\n"
    "TXN-Z,2040-03-03,debit,Alpha Mart,75.00,Cleaning\n"
)


def test_transaction_export_filters_provenance_and_generated_at(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        upload = client.post(
            "/api/audits",
            files={"files": ("txn-2040.csv", TXN_CSV.encode(), "text/csv")},
        )
        assert upload.status_code == 200, upload.text

        exported = client.get(
            "/api/transactions/export.csv?source_file=txn-2040&date_from=2040-02-01&date_to=2040-02-28&direction=debit"
        )
        assert exported.status_code == 200, exported.text
        text = exported.content.decode("utf-8")
        assert "generated_at" in text
        assert "provenance" in text
        assert "TXN-X" in text
        assert "TXN-Y" not in text
        assert "TXN-Z" not in text
        assert "txn-2040.csv" in text
        assert "2040-02-01" in text
    finally:
        teardown(engine)


def test_risk_alert_export_filters_and_provenance(tmp_path):
    client, _, engine = build_client(tmp_path)
    try:
        # Two identical vendor/amount/date debits deterministically produce a
        # critical duplicate-payment alert.
        duplicate_csv = (
            "transaction_id,date,type,vendor,amount,description\n"
            "TXN-D1,2040-04-01,debit,Duplicate Vendor,500.00,Same invoice\n"
            "TXN-D2,2040-04-01,debit,Duplicate Vendor,500.00,Same invoice\n"
        )
        upload = client.post(
            "/api/audits",
            files={"files": ("alerts-2040.csv", duplicate_csv.encode(), "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        analysis = client.post("/api/risk/analyze", json={})
        assert analysis.status_code == 200, analysis.text
        assert client.get("/api/risk/alerts").json()["total"] > 0

        exported = client.get("/api/risk/alerts/export.csv?severity=critical")
        assert exported.status_code == 200, exported.text
        text = exported.content.decode("utf-8")
        assert "generated_at" in text
        assert "filters" in text
        assert "provenance" in text
        assert "risk_alert" in text
        assert "Duplicate Vendor" in text

        filtered = client.get("/api/risk/alerts/export.csv?severity=low")
        assert filtered.status_code == 200
        assert "risk_alert" not in filtered.content.decode("utf-8")
    finally:
        teardown(engine)
