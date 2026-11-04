import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import RiskAlert, TransactionRecord
from app.risk_engine import (
    channel_analytics,
    classify_risk_score,
    geography_analytics,
    overview,
    resolve_thresholds,
    run_analysis,
    serialize_alert,
    trend_analytics,
)


def make_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'risk-engine.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_transaction(db, **overrides):
    values = {
        "transaction_id": "TX-1",
        "date": "2026-03-01",
        "amount": 5000.0,
        "signed_amount": -5000.0,
        "direction": "debit",
        "vendor": "Acme Traders",
        "description": "UPI payment",
        "source_file": "statement.csv",
        "channel": "UPI",
        "country": "India",
        "state": "Karnataka",
        "city": "Bengaluru",
        "latitude": 12.9716,
        "longitude": 77.5946,
    }
    values.update(overrides)
    record = TransactionRecord(**values)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def test_thresholds_are_configurable_and_validated():
    defaults = resolve_thresholds()
    assert defaults.low_max == 29
    assert classify_risk_score(29, defaults) == "low"
    assert classify_risk_score(30, defaults) == "medium"
    assert classify_risk_score(60, defaults) == "high"
    assert classify_risk_score(80, defaults) == "critical"

    custom = resolve_thresholds({"low_max": 20, "medium_max": 50, "high_max": 70})
    assert classify_risk_score(20, custom) == "low"
    assert classify_risk_score(21, custom) == "medium"
    assert classify_risk_score(71, custom) == "critical"

    with pytest.raises(ValueError):
        resolve_thresholds({"low_max": 60, "medium_max": 40, "high_max": 20})
    with pytest.raises(ValueError):
        resolve_thresholds({"unusual_amount_multiplier": 0})


def test_duplicate_payment_creates_critical_alert_and_analysis_is_idempotent(tmp_path):
    db = make_session(tmp_path)
    try:
        for index in (1, 2):
            add_transaction(db, transaction_id=f"TX-{index}")

        first = run_analysis(db)
        assert first["status"] == "completed"
        assert first["alert_count"] >= 1
        assert first["created"] >= 1

        alert = db.query(RiskAlert).filter(RiskAlert.risk_category == "Duplicate Payment").one()
        assert alert.severity in {"high", "critical"}
        assert alert.channel == "UPI"
        assert alert.country == "India"
        assert alert.fingerprint
        assert alert.evidence["record_ids"]

        second = run_analysis(db)
        assert second["alert_count"] == first["alert_count"]
        assert second["created"] == 0
        assert db.query(RiskAlert).count() == first["alert_count"]
    finally:
        db.close()


def test_missing_channel_and_location_stay_unknown_and_empty(tmp_path):
    db = make_session(tmp_path)
    try:
        add_transaction(
            db,
            channel=None,
            country=None,
            state=None,
            city=None,
            latitude=None,
            longitude=None,
            description="Payment",
            transaction_id="TX-NO-LOCATION",
        )

        run_analysis(db)
        channels = channel_analytics(db)
        assert channels["unknown_channel_count"] == 1
        assert channels["channels"][0]["channel"] == "Unknown"

        geography = geography_analytics(db)
        assert geography["available"] is False
        assert geography["items"] == []
        assert geography["coordinate_location_count"] == 0
    finally:
        db.close()


def test_channel_geography_and_trend_analytics_use_stored_records(tmp_path):
    db = make_session(tmp_path)
    try:
        add_transaction(db, transaction_id="TX-A")
        add_transaction(
            db,
            transaction_id="TX-B",
            channel="Mobile Banking",
            city="Mumbai",
            latitude=19.076,
            longitude=72.8777,
        )
        run_analysis(db)

        channels = {item["channel"]: item for item in channel_analytics(db)["channels"]}
        assert channels["UPI"]["transaction_count"] == 1
        assert channels["Mobile Banking"]["transaction_count"] == 1

        geography = geography_analytics(db)
        assert geography["available"] is True
        assert len(geography["items"]) == 2
        assert {item["city"] for item in geography["items"]} == {"Bengaluru", "Mumbai"}

        trends = trend_analytics(db, granularity="daily")
        assert trends["total_alerts"] >= 1
        assert trends["series"]

        data = overview(db)
        assert data["total_alerts"] >= 1
        assert data["transactions_analyzed"] == 2
        assert data["last_analysis"]["status"] == "completed"
    finally:
        db.close()


def test_alert_serialization_and_review_event_shape(tmp_path):
    db = make_session(tmp_path)
    try:
        add_transaction(db, transaction_id="TX-1")
        add_transaction(db, transaction_id="TX-2")
        run_analysis(db)
        alert = db.query(RiskAlert).first()
        payload = serialize_alert(alert)
        assert payload["alert_id"].startswith("RA-")
        assert payload["category"] == payload["risk_category"]
        assert payload["channel"] == "UPI"
        assert payload["events"] == []
    finally:
        db.close()
