"""Dashboard overview: DB-backed KPIs and a stable refresh payload.

These tests use a fresh temporary SQLite database per test. They never touch
the project database or existing records.
"""
import os
import tempfile
from datetime import datetime, timezone

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-dashboard-tests-")
os.environ.setdefault(
    "FINAUDIT_DATABASE_URL",
    f"sqlite:///{os.path.join(TEST_DB_DIR, 'dashboard.db')}",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.category import categorize_transaction  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    ReconciliationResult,
    ReconciliationRun,
    RiskAlert,
    TransactionRecord,
)


@pytest.fixture
def dashboard_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dashboard.db'}",
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
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def seed_transactions(factory):
    db = factory()
    try:
        db.add_all([
            TransactionRecord(
                transaction_id="DASH-1", date="2041-01-05", amount=120.0,
                signed_amount=-120.0, direction="debit", vendor="OfficeMart",
                category="Office", source_file="jan.csv", source_row=1,
            ),
            TransactionRecord(
                transaction_id="DASH-2", date="2041-01-20", amount=80.0,
                signed_amount=-80.0, direction="debit", vendor="OfficeMart",
                category="Office", source_file="jan.csv", source_row=2,
            ),
            TransactionRecord(
                transaction_id="DASH-3", date="2041-01-25", amount=500.0,
                signed_amount=500.0, direction="credit", vendor="Client One",
                category="Income", source_file="jan.csv", source_row=3,
            ),
            TransactionRecord(
                transaction_id="DASH-4", date="2041-02-02", amount=300.0,
                signed_amount=-300.0, direction="debit", vendor="TravelCo",
                category="Travel", source_file="feb.csv", source_row=1,
            ),
        ])
        db.commit()
    finally:
        db.close()


def seed_alerts(factory):
    db = factory()
    try:
        db.add_all([
            RiskAlert(
                alert_code="DASH-ALERT-1", fingerprint="dash-fp-1",
                source_type="transaction", transaction_id="DASH-1",
                risk_category="Duplicate Payment", severity="critical",
                risk_score=95, amount_value=120.0, explanation="Seeded alert",
                detected_at=datetime(2041, 1, 6, tzinfo=timezone.utc),
            ),
            RiskAlert(
                alert_code="DASH-ALERT-2", fingerprint="dash-fp-2",
                source_type="transaction", transaction_id="DASH-4",
                risk_category="Unusual Vendor", severity="high",
                risk_score=80, amount_value=300.0, explanation="Seeded alert",
                detected_at=datetime(2041, 2, 3, tzinfo=timezone.utc),
            ),
            RiskAlert(
                alert_code="DASH-ALERT-3", fingerprint="dash-fp-3",
                source_type="transaction", transaction_id="DASH-3",
                risk_category="Unusual Timing", severity="medium",
                risk_score=55, amount_value=500.0, explanation="Seeded alert",
                detected_at=datetime(2041, 2, 4, tzinfo=timezone.utc),
            ),
        ])
        db.commit()
    finally:
        db.close()


def seed_reconciliation(factory):
    """Persist an older run and a newer run; only the newer one must be used."""
    db = factory()
    try:
        old_run = ReconciliationRun(
            bank_filename="old-bank.csv", ledger_filename="old-ledger.csv",
            bank_record_count=10, ledger_record_count=10,
            unreconciled_value=999.0, difference=999.0,
        )
        new_run = ReconciliationRun(
            bank_filename="bank.csv", ledger_filename="ledger.csv",
            bank_record_count=4, ledger_record_count=4,
            unreconciled_value=12.5, difference=7.5,
        )
        db.add_all([old_run, new_run])
        db.flush()
        db.add_all([
            ReconciliationResult(run_id=old_run.id, status="bank_only"),
            ReconciliationResult(run_id=new_run.id, status="matched"),
            ReconciliationResult(run_id=new_run.id, status="matched"),
            ReconciliationResult(run_id=new_run.id, status="amount_mismatch"),
            ReconciliationResult(run_id=new_run.id, status="ambiguous"),
        ])
        db.commit()
        return new_run.id
    finally:
        db.close()


def chart_row(rows, key, value):
    return next(row for row in rows if row[key] == value)


def test_dashboard_overview_reflects_persisted_sqlite_records(dashboard_client):
    client, factory = dashboard_client
    seed_transactions(factory)
    seed_alerts(factory)
    latest_run_id = seed_reconciliation(factory)

    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200, response.text
    payload = response.json()
    metrics = payload["metrics"]
    charts = payload["charts"]

    # Counts and cash flow derived from the four stored transactions.
    assert metrics["total_transactions"] == 4
    assert metrics["total_spending"] == 500.0
    assert metrics["total_received"] == 500.0
    assert metrics["net_cash_flow"] == 0.0
    assert payload["scope"]["no_fake_data"] is True

    # Risk alerts and their severity distribution come from stored alerts.
    assert metrics["total_risk_alerts"] == 3
    assert metrics["critical_alerts"] == 1
    assert metrics["high_risk_alerts"] == 1
    distribution = {row["severity"]: row["count"] for row in charts["risk_distribution"]}
    assert distribution == {"critical": 1, "high": 1, "medium": 1, "low": 0}

    # Monthly activity and spending trend come from transaction dates.
    january = chart_row(charts["transaction_activity"], "month", "2041-01")
    february = chart_row(charts["transaction_activity"], "month", "2041-02")
    assert (january["transactions"], january["spending"], january["received"]) == (3, 200.0, 500.0)
    assert (february["transactions"], february["spending"], february["received"]) == (1, 300.0, 0.0)
    assert chart_row(charts["spending_trends"], "month", "2041-01")["spending"] == 200.0

    # Reconciliation metrics use the latest run only, not accumulated history.
    assert metrics["latest_reconciliation_run_id"] == latest_run_id
    assert metrics["matched_transactions"] == 2
    assert metrics["possible_matches"] == 1
    assert metrics["reconciliation_match_rate"] == 50.0
    assert metrics["unreconciled_value"] == 12.5
    assert metrics["reconciliation_total_difference"] == 7.5
    recon_status = {row["status"]: row["count"] for row in charts["reconciliation_status"]}
    assert recon_status["matched"] == 2
    assert recon_status["amount_mismatch"] == 1
    assert recon_status["ambiguous"] == 1
    assert recon_status["bank_only"] == 0

    # Category mix and recent activity are read from the same stored rows.
    assert chart_row(charts["category_distribution"], "category", "Office")["count"] == 2
    assert {item["reference"] for item in payload["recent_activity"]} >= {
        "DASH-1", "DASH-4", "DASH-ALERT-1",
    }


def test_refresh_recomputes_from_new_records_without_mutating_old_rows(dashboard_client):
    client, factory = dashboard_client
    seed_transactions(factory)
    seed_alerts(factory)

    before = client.get("/api/dashboard/overview").json()
    assert before["metrics"]["total_transactions"] == 4
    assert before["metrics"]["total_spending"] == 500.0

    db = factory()
    try:
        original = {
            row.transaction_id: (row.amount, row.signed_amount, row.date)
            for row in db.query(TransactionRecord).all()
        }
        # Refresh simulation: add new persisted records only. Existing rows are
        # deliberately left untouched.
        db.add(TransactionRecord(
            transaction_id="DASH-5", date="2041-03-01", amount=25.0,
            signed_amount=-25.0, direction="debit", vendor="NewVendor",
            category="Travel", source_file="mar.csv", source_row=1,
        ))
        db.add(RiskAlert(
            alert_code="DASH-ALERT-4", fingerprint="dash-fp-4",
            source_type="transaction", transaction_id="DASH-5",
            risk_category="New Pattern", severity="low", risk_score=20,
            amount_value=25.0, explanation="Seeded alert",
            detected_at=datetime(2041, 3, 2, tzinfo=timezone.utc),
        ))
        db.commit()
        after_rows = {
            row.transaction_id: (row.amount, row.signed_amount, row.date)
            for row in db.query(TransactionRecord).all()
        }
    finally:
        db.close()

    assert {key: after_rows[key] for key in original} == original
    assert "DASH-5" in after_rows

    after = client.get("/api/dashboard/overview").json()
    assert after["metrics"]["total_transactions"] == 5
    assert after["metrics"]["total_spending"] == 525.0
    assert chart_row(after["charts"]["transaction_activity"], "month", "2041-03")["spending"] == 25.0
    assert chart_row(after["charts"]["category_distribution"], "category", "Travel")["count"] == 2
    assert after["metrics"]["total_risk_alerts"] == 4
    assert {row["severity"]: row["count"] for row in after["charts"]["risk_distribution"]}["low"] == 1
    assert any(item["reference"] == "DASH-5" for item in after["recent_activity"])


def test_dashboard_overview_with_empty_database_has_no_fake_values(dashboard_client):
    client, _ = dashboard_client

    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["metrics"]["total_transactions"] == 0
    assert payload["metrics"]["total_spending"] == 0.0
    assert payload["metrics"]["total_received"] == 0.0
    assert payload["metrics"]["net_cash_flow"] == 0.0
    assert payload["metrics"]["total_risk_alerts"] == 0
    assert payload["metrics"]["latest_reconciliation_run_id"] is None
    assert payload["recent_activity"] == []
    assert payload["scope"]["no_fake_data"] is True


def test_match_rate_is_percentage_of_average_side_record_counts(dashboard_client):
    """Regression for the documented formula: value is already a percentage."""
    client, factory = dashboard_client
    db = factory()
    try:
        run = ReconciliationRun(
            bank_filename="bank.csv", ledger_filename="ledger.csv",
            bank_record_count=410, ledger_record_count=5,
            result_count=416, unmatched_count=403,
            unreconciled_value=472106.94, difference=434074.04,
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.add_all(
            [ReconciliationResult(run_id=run_id, status="matched")]
            + [ReconciliationResult(run_id=run_id, status="bank_only") for _ in range(398)]
            + [ReconciliationResult(run_id=run_id, status="ledger_only") for _ in range(5)]
            + [ReconciliationResult(run_id=run_id, status="duplicate_candidate") for _ in range(12)]
        )
        db.commit()
    finally:
        db.close()

    payload = client.get("/api/dashboard/overview").json()
    metrics = payload["metrics"]
    assert "percentage" in payload["scope"]["reconciliation_metrics"]
    assert metrics["latest_reconciliation_run_id"] == run_id
    assert metrics["matched_transactions"] == 1
    assert metrics["bank_only_count"] == 398
    assert metrics["ledger_only_count"] == 5
    assert metrics["duplicate_candidate_count"] == 12
    assert metrics["reconciliation_match_rate"] == 0.48
    assert metrics["reconciliation_match_rate"] == round(1 / ((410 + 5) / 2) * 100, 2)


def test_activity_chart_covers_last_six_populated_months_only(dashboard_client):
    """Scope is the last six *populated* months, including future-dated rows."""
    client, factory = dashboard_client
    month_counts = [
        ("2042-01", 1), ("2042-02", 2), ("2042-03", 3), ("2042-04", 4),
        ("2042-05", 5), ("2042-06", 6), ("2099-12", 7),
    ]
    db = factory()
    try:
        rows = []
        source_row = 1
        for month, count in month_counts:
            for index in range(count):
                rows.append(TransactionRecord(
                    transaction_id=f"MONTH-{month}-{index}",
                    date=f"{month}-01", amount=1.0, signed_amount=-1.0,
                    direction="debit", category="Other",
                    source_file="monthly.csv", source_row=source_row,
                ))
                source_row += 1
        db.add_all(rows)
        db.commit()
    finally:
        db.close()

    payload = client.get("/api/dashboard/overview").json()
    activity = payload["charts"]["transaction_activity"]
    assert [row["month"] for row in activity] == [
        "2042-02", "2042-03", "2042-04", "2042-05", "2042-06", "2099-12",
    ]
    assert sum(row["transactions"] for row in activity) == 27
    assert payload["metrics"]["total_transactions"] == 28
    assert payload["metrics"]["total_transactions"] > sum(row["transactions"] for row in activity)


def test_import_defaults_to_uncategorized_when_no_keyword_matches(dashboard_client):
    """Documents why most imported rows stay Uncategorized; no data is rewritten."""
    assert categorize_transaction("", "Unknown Vendor", "") == ("Uncategorized", "uncertain")
    assert categorize_transaction("Pharmacy visit", "", "")[0] == "Healthcare"

    client, _ = dashboard_client
    csv = (
        "transaction_id,date,type,vendor,amount,description,category\n"
        "CAT-1,2043-01-01,debit,Unknown Vendor,10.00,,Healthcare\n"
        "CAT-2,2043-01-02,debit,Apollo Pharmacy,20.00,Prescription,Healthcare\n"
    )
    upload = client.post(
        "/api/audits",
        files={"files": ("categories.csv", csv.encode(), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    payload = client.get("/api/dashboard/overview").json()
    counts = {row["category"]: row["count"] for row in payload["charts"]["category_distribution"]}
    assert counts["Uncategorized"] == 1
    assert counts["Healthcare"] == 1


def test_audit_and_reconciliation_loading_still_work_with_dashboard(dashboard_client):
    """The single dashboard action must not replace the workspace loaders."""
    client, _ = dashboard_client
    audit_csv = (
        "transaction_id,date,type,vendor,amount,description\n"
        "DASH-AUDIT-1,2041-04-01,debit,OfficeMart,10.00,Office\n"
    )
    bank_csv = "date,type,amount,reference,description\n2041-04-01,debit,10.00,REF-1,Office\n"
    ledger_csv = "date,type,amount,reference,description\n2041-04-01,debit,10.00,REF-1,Office\n"

    audit_upload = client.post(
        "/api/audits",
        files={"files": ("dashboard-audit.csv", audit_csv.encode(), "text/csv")},
    )
    assert audit_upload.status_code == 200, audit_upload.text
    audits = client.get("/api/audits")
    assert audits.status_code == 200
    assert audits.json()

    recon = client.post(
        "/api/reconciliations",
        files={
            "bank_file": ("bank.csv", bank_csv.encode(), "text/csv"),
            "ledger_file": ("ledger.csv", ledger_csv.encode(), "text/csv"),
        },
    )
    assert recon.status_code == 200, recon.text
    recs = client.get("/api/reconciliations")
    assert recs.status_code == 200
    assert recs.json()

    overview = client.get("/api/dashboard/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["metrics"]["total_transactions"] == 1
    assert overview.json()["metrics"]["latest_reconciliation_run_id"] is not None
