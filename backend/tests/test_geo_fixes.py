"""Regression coverage for the Geographic Risk Intelligence fixes.

Every test exercises deterministic Python calculations against an isolated
SQLite database. No financial amount, risk score or coordinate is invented by
the test itself beyond explicit fixture values that it then verifies.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import csv_normalizer
from app.database import Base, get_db
from app.main import app
from app.models import RiskAlert, TransactionRecord


@pytest.fixture
def geo_fix_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'geo-fixes.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _add_record(session_factory, **kwargs):
    db = session_factory()
    try:
        defaults = dict(
            date="2026-07-01",
            amount=1000.0,
            signed_amount=-1000.0,
            currency="INR",
            direction="debit",
            source_file="fixes.csv",
        )
        defaults.update(kwargs)
        record = TransactionRecord(**defaults)
        db.add(record)
        db.commit()
        db.refresh(record)
        return record.id
    finally:
        db.close()


def _add_alert(session_factory, record_id, *, code, severity, score, **kwargs):
    db = session_factory()
    try:
        record = db.get(TransactionRecord, record_id)
        evidence = {"record_ids": [record_id]}
        evidence.update(kwargs.pop("extra_evidence", {}))
        alert = RiskAlert(
            alert_code=code,
            fingerprint=code,
            source_type="transaction",
            source_record_id=record_id,
            transaction_id=kwargs.pop("transaction_id", record.transaction_id),
            risk_category="Anomaly",
            severity=severity,
            risk_score=score,
            amount_value=record.amount,
            currency=record.currency,
            explanation=kwargs.pop("explanation", f"{code} explanation"),
            evidence=evidence,
            detected_at=datetime.now(timezone.utc),
            review_status="open",
            **kwargs,
        )
        db.add(alert)
        db.commit()
        return alert.id
    finally:
        db.close()


def _summary(client, query=""):
    response = client.get(f"/api/risk/geography/summary{query}")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1. Geographic CSV import: risk label + original channel are stored.
# ---------------------------------------------------------------------------
def test_geo_csv_import_stores_imported_risk_label_and_original_channel(geo_fix_client):
    client, session_factory = geo_fix_client
    csv_text = (
        "Transaction ID,Transaction Date,Transaction Amount,Payment Channel,"
        "Risk Level,Transaction City,Transaction Country,Latitude,Longitude\n"
        "GEO-CSV-1,2026-07-01,1250.00,Crypto Wallet,HIGH,Bengaluru,India,12.9716,77.5946\n"
        "GEO-CSV-2,2026-07-02,500.00,UPI,MEDIUM,Mumbai,India,19.076,72.8777\n"
    )
    upload = client.post(
        "/api/audits",
        files={"files": ("geo-import.csv", csv_text.encode(), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["row_count"] == 2
    assert any("not recognised" in warning for warning in upload.json()["warnings"])

    db = session_factory()
    try:
        rows = {
            row.transaction_id: row
            for row in db.query(TransactionRecord).all()
        }
        first = rows["GEO-CSV-1"]
        assert first.imported_risk_level == "high"
        assert first.channel_original == "Crypto Wallet"
        # Unmapped channel normalises to Unknown but the original is preserved.
        assert first.channel == "Unknown"
        assert first.channel_source == "explicit_unmapped"
        assert first.latitude == pytest.approx(12.9716)
        assert first.longitude == pytest.approx(77.5946)
        assert first.location_status == "mapped"

        second = rows["GEO-CSV-2"]
        assert second.imported_risk_level == "medium"
        assert second.channel == "UPI"
        assert second.channel_original == "UPI"
    finally:
        db.close()

    # Imported labels are stored but must NOT create alerts or flagged rows.
    payload = _summary(client, "?risk_source=calculated")
    assert payload["summary"]["flagged_transactions"] == 0
    assert payload["summary"]["flagged_value"] == 0.0
    assert payload["summary"]["transactions_with_linked_alerts"] == 0
    assert payload["summary"]["imported_label_transactions"] == 2


# ---------------------------------------------------------------------------
# 2. Latitude / longitude validation (including valid 0.0 coordinates).
# ---------------------------------------------------------------------------
def test_coordinate_validation_accepts_zero_and_rejects_invalid_values():
    zero_row = {"latitude": "0.0", "longitude": "0.0"}
    values, warnings = csv_normalizer._location_value(zero_row)
    assert values["latitude"] == 0.0
    assert values["longitude"] == 0.0
    assert values["location_status"] == "mapped"
    assert warnings == []

    # Only one coordinate in a pair is discarded.
    values, warnings = csv_normalizer._location_value({"latitude": "12.5", "longitude": ""})
    assert values["latitude"] is None
    assert values["longitude"] is None
    assert any("Incomplete" in warning for warning in warnings)

    # Out-of-range values are rejected; text is parsed deterministically.
    values, warnings = csv_normalizer._location_value({"latitude": "91", "longitude": "200"})
    assert values["latitude"] is None
    assert values["longitude"] is None
    assert any("Latitude" in warning for warning in warnings)
    assert any("Longitude" in warning for warning in warnings)

    values, _ = csv_normalizer._location_value({"latitude": "abc", "longitude": "not-a-number"})
    assert values["latitude"] is None
    assert values["longitude"] is None

    assert csv_normalizer._parse_coordinate(True) is None
    assert csv_normalizer._parse_coordinate("  ") is None


def test_csv_import_keeps_valid_zero_coordinates(geo_fix_client):
    client, session_factory = geo_fix_client
    csv_text = (
        "Transaction ID,Transaction Date,Transaction Amount,Latitude,Longitude\n"
        "GEO-ZERO-1,2026-07-01,250.00,0.0,0.0\n"
    )
    upload = client.post(
        "/api/audits",
        files={"files": ("zero.csv", csv_text.encode(), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    db = session_factory()
    try:
        row = db.query(TransactionRecord).one()
        assert row.latitude == pytest.approx(0.0)
        assert row.longitude == pytest.approx(0.0)
    finally:
        db.close()

    payload = _summary(client)
    assert payload["summary"]["transactions_with_coordinates"] == 1
    marker = payload["markers"][0]
    assert marker["latitude"] == pytest.approx(0.0)
    assert marker["longitude"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. Risk classification: calculated vs imported labels.
# ---------------------------------------------------------------------------
def test_risk_classification_calculated_default_and_imported_option(geo_fix_client):
    client, session_factory = geo_fix_client
    record_id = _add_record(
        session_factory,
        transaction_id="GEO-CLASS-1",
        imported_risk_level="critical",
        latitude=10.0,
        longitude=20.0,
        country="India",
        city="Test City",
    )
    # No alerts are linked, so the calculated level is low and not flagged.

    calculated = _summary(client)
    marker = calculated["markers"][0]
    assert marker["risk_level"] == "low"
    assert marker["risk_classification_source"] == "calculated"
    assert marker["calculated_risk_level"] == "low"
    assert marker["imported_risk_level"] == "critical"
    assert marker["flagged"] is False
    assert calculated["summary"]["classification_source"] == "calculated"
    assert calculated["summary"]["risk_distribution"] == {
        "low": 1, "medium": 0, "high": 0, "critical": 0,
    }

    imported = _summary(client, "?risk_source=imported")
    marker = imported["markers"][0]
    assert marker["risk_level"] == "critical"
    assert marker["risk_classification_source"] == "imported"
    assert marker["calculated_risk_level"] == "low"
    # An imported label is NEVER treated as proof of fraud.
    assert marker["flagged"] is False
    assert imported["summary"]["flagged_transactions"] == 0
    assert imported["summary"]["flagged_value"] == 0.0
    assert imported["summary"]["risk_distribution"] == {
        "low": 0, "medium": 0, "high": 0, "critical": 1,
    }
    # Distribution always adds up to the total for the selected source.
    assert sum(imported["summary"]["risk_distribution"].values()) == 1
    assert sum(calculated["summary"]["risk_distribution"].values()) == 1


def test_invalid_risk_source_is_rejected(geo_fix_client):
    client, _ = geo_fix_client
    response = client.get("/api/risk/geography/summary?risk_source=fabricated")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 4-6. Alert linking, no double counting, country/city aggregation.
# ---------------------------------------------------------------------------
def test_multiple_alerts_on_one_transaction_count_value_once(geo_fix_client):
    client, session_factory = geo_fix_client
    record_id = _add_record(
        session_factory,
        transaction_id="GEO-MULTI-1",
        amount=7500.0,
        signed_amount=-7500.0,
        country="India",
        city="Pune",
        latitude=18.5204,
        longitude=73.8567,
    )
    _add_alert(session_factory, record_id, code="RA-MULTI-1", severity="medium", score=55)
    _add_alert(session_factory, record_id, code="RA-MULTI-2", severity="high", score=85)

    payload = _summary(client)
    marker = payload["markers"][0]
    assert marker["linked_alert_count"] == 2
    assert len(marker["alert_ids"]) == 2
    assert marker["risk_level"] == "high"
    assert marker["risk_score"] == 85

    summary = payload["summary"]
    assert summary["flagged_transactions"] == 1
    assert summary["flagged_value"] == pytest.approx(7500.0)
    assert summary["transactions_with_linked_alerts"] == 1
    assert summary["transactions_without_linked_alerts"] == 0

    country = payload["countries"][0]
    assert country["transaction_count"] == 1
    assert country["flagged_count"] == 1
    assert country["flagged_value"] == pytest.approx(7500.0)

    city = payload["cities"][0]
    assert city["transaction_count"] == 1
    assert city["flagged_count"] == 1
    assert city["flagged_value"] == pytest.approx(7500.0)

    # Country and city flagged values must equal the global flagged value.
    assert country["flagged_value"] == pytest.approx(summary["flagged_value"])
    assert city["flagged_value"] == pytest.approx(summary["flagged_value"])


def test_alert_linking_by_record_id_and_transaction_id(geo_fix_client):
    client, session_factory = geo_fix_client
    record_id = _add_record(
        session_factory,
        transaction_id="GEO-LINK-1",
        amount=4200.0,
        signed_amount=-4200.0,
        country="Kenya",
        city="Nairobi",
        latitude=-1.2921,
        longitude=36.8219,
    )
    # Two alerts reference the same record: one by record id, one by the
    # transaction id carried in evidence. Both must link to the same row and
    # the row must still be counted once.
    _add_alert(
        session_factory,
        record_id,
        code="RA-LINK-1",
        severity="high",
        score=80,
        transaction_id=None,
    )
    _add_alert(
        session_factory,
        record_id,
        code="RA-LINK-2",
        severity="low",
        score=20,
        transaction_id="GEO-LINK-1",
        extra_evidence={"transaction_ids": ["GEO-LINK-1"]},
    )

    payload = _summary(client)
    marker = payload["markers"][0]
    assert marker["linked_alert_count"] == 2
    assert set(marker["alert_ids"]) == {"RA-LINK-1", "RA-LINK-2"}
    assert payload["summary"]["flagged_value"] == pytest.approx(4200.0)
    assert payload["countries"][0]["flagged_value"] == pytest.approx(4200.0)


def test_country_and_city_distributions_are_internally_consistent(geo_fix_client):
    client, session_factory = geo_fix_client
    # Two Indian transactions: one high (alert), one with no alert (low).
    high_id = _add_record(
        session_factory,
        transaction_id="GEO-AGG-1",
        amount=100.0,
        signed_amount=-100.0,
        country="India",
        city="Mumbai",
        latitude=19.076,
        longitude=72.8777,
    )
    _add_alert(session_factory, high_id, code="RA-AGG-1", severity="high", score=80)
    _add_record(
        session_factory,
        transaction_id="GEO-AGG-2",
        amount=200.0,
        signed_amount=-200.0,
        country="India",
        city="Mumbai",
        latitude=19.076,
        longitude=72.8777,
    )

    payload = _summary(client)
    country = payload["countries"][0]
    total = country["transaction_count"]
    assert total == country["critical_count"] + country["high_count"] + country["medium_count"] + country["low_count"]
    assert country["high_risk_count"] == country["high_count"] + country["critical_count"]

    city = payload["cities"][0]
    assert city["transaction_count"] == (
        city["critical_count"] + city["high_count"] + city["medium_count"] + city["low_count"]
    )
    assert city["high_risk_count"] == city["high_count"] + city["critical_count"]

    distribution = payload["summary"]["risk_distribution"]
    assert sum(distribution.values()) == payload["summary"]["total_transactions"]
    assert distribution["high"] == 1
    assert distribution["low"] == 1


def test_flagged_value_uses_actual_stored_amounts(geo_fix_client):
    client, session_factory = geo_fix_client
    first = _add_record(
        session_factory,
        transaction_id="GEO-AMT-1",
        amount=1234.56,
        signed_amount=-1234.56,
        country="India",
        city="Delhi",
        latitude=28.6139,
        longitude=77.209,
    )
    second = _add_record(
        session_factory,
        transaction_id="GEO-AMT-2",
        amount=99.99,
        signed_amount=-99.99,
        country="India",
        city="Delhi",
        latitude=28.6139,
        longitude=77.209,
    )
    _add_alert(session_factory, first, code="RA-AMT-1", severity="critical", score=95)
    _add_alert(session_factory, second, code="RA-AMT-2", severity="medium", score=50)

    payload = _summary(client)
    expected = round(1234.56 + 99.99, 2)
    assert payload["summary"]["flagged_value"] == pytest.approx(expected)
    assert payload["summary"]["flagged_transactions"] == 2
    assert payload["countries"][0]["flagged_value"] == pytest.approx(expected)
    assert payload["cities"][0]["flagged_value"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 7. Transaction counts (with / without coordinates and alerts).
# ---------------------------------------------------------------------------
def test_separate_transaction_counts_with_and_without_location_and_alerts(geo_fix_client):
    client, session_factory = geo_fix_client
    mapped = _add_record(
        session_factory,
        transaction_id="GEO-COUNT-1",
        amount=100.0,
        country="India",
        city="Pune",
        latitude=18.5204,
        longitude=73.8567,
    )
    _add_alert(session_factory, mapped, code="RA-COUNT-1", severity="high", score=80)
    # Named location but no coordinates, and no alert.
    _add_record(
        session_factory,
        transaction_id="GEO-COUNT-2",
        amount=200.0,
        country="India",
        city="Pune",
        latitude=None,
        longitude=None,
    )
    # No location at all.
    _add_record(
        session_factory,
        transaction_id="GEO-COUNT-3",
        amount=300.0,
        latitude=None,
        longitude=None,
    )

    summary = _summary(client)["summary"]
    assert summary["total_transactions"] == 3
    assert summary["transactions_with_coordinates"] == 1
    assert summary["transactions_without_coordinates"] == 2
    assert summary["transactions_with_linked_alerts"] == 1
    assert summary["transactions_without_linked_alerts"] == 2
    assert summary["transactions_with_coordinates"] + summary["transactions_without_coordinates"] == 3
    assert summary["transactions_with_linked_alerts"] + summary["transactions_without_linked_alerts"] == 3


def test_transactions_without_location_are_not_fabricated(geo_fix_client):
    client, session_factory = geo_fix_client
    _add_record(
        session_factory,
        transaction_id="GEO-NOLOC-1",
        amount=500.0,
        country=None,
        city=None,
        latitude=None,
        longitude=None,
    )
    payload = _summary(client)
    assert payload["available"] is False
    assert payload["markers"] == []
    assert "location" in payload["reason"].lower()
    assert payload["summary"]["transactions_without_coordinates"] == 1
    assert payload["summary"]["countries_count"] == 0
    assert payload["summary"]["cities_count"] == 0


# ---------------------------------------------------------------------------
# 8. Duplicate import prevention keeps existing records intact.
# ---------------------------------------------------------------------------
def test_repeated_csv_import_does_not_create_duplicate_transactions(geo_fix_client):
    client, session_factory = geo_fix_client
    csv_text = (
        "Transaction ID,Transaction Date,Transaction Amount,Risk Level,Latitude,Longitude\n"
        "GEO-DUP-1,2026-07-01,800.00,HIGH,12.9716,77.5946\n"
        "GEO-DUP-2,2026-07-02,900.00,LOW,19.076,72.8777\n"
    )
    first = client.post(
        "/api/audits",
        files={"files": ("dup.csv", csv_text.encode(), "text/csv")},
    )
    assert first.status_code == 200, first.text
    assert first.json()["row_count"] == 2

    db = session_factory()
    try:
        before = {
            row.id: (row.transaction_id, row.amount, row.imported_risk_level)
            for row in db.query(TransactionRecord).all()
        }
    finally:
        db.close()
    assert len(before) == 2

    # Re-upload with default skip_duplicates (backend default is now True).
    second = client.post(
        "/api/audits",
        files={"files": ("dup.csv", csv_text.encode(), "text/csv")},
    )
    assert second.status_code == 200, second.text
    assert second.json()["skipped_duplicates"] == 2
    assert second.json()["row_count"] == 0

    db = session_factory()
    try:
        rows = db.query(TransactionRecord).all()
        assert len(rows) == 2
        after = {
            row.id: (row.transaction_id, row.amount, row.imported_risk_level)
            for row in rows
        }
        # Existing records are untouched.
        assert after == before
    finally:
        db.close()


def test_options_expose_critical_level_and_risk_sources(geo_fix_client):
    client, _ = geo_fix_client
    response = client.get("/api/risk/geography/options")
    assert response.status_code == 200
    options = response.json()
    assert options["risk_levels"] == ["low", "medium", "high", "critical"]
    assert options["risk_sources"] == ["calculated", "imported"]
    assert options["options"]["risk_levels"] == ["low", "medium", "high", "critical"]
