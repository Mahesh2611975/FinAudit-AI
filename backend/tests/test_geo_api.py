from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.geocoding import location_key, resolve_locations
from app.main import app
from app.models import GeocodeCache, RiskAlert, TransactionRecord

import app.geocoding as geocoding


@pytest.fixture
def geo_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'geo-api.db'}",
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


def seed_geo(session_factory):
    db = session_factory()
    try:
        mumbai = [
            TransactionRecord(
                transaction_id=f"GEO-MUM-{index}",
                date="2026-04-01",
                amount=7500.0,
                currency="INR",
                signed_amount=-7500.0,
                direction="debit",
                vendor="Mumbai Duplicate Vendor",
                description="NEFT transfer",
                source_file="mumbai.csv",
                channel="NEFT",
                country="India",
                state="Maharashtra",
                city="Mumbai",
                latitude=19.076,
                longitude=72.8777,
            )
            for index in (1, 2)
        ]
        bengaluru = TransactionRecord(
            transaction_id="GEO-BLR-1",
            date="2026-04-02",
            amount=20000.0,
            currency="INR",
            signed_amount=-20000.0,
            direction="debit",
            vendor="Bengaluru Vendor",
            description="UPI payment",
            source_file="bengaluru.csv",
            channel="UPI",
            country="India",
            state="Karnataka",
            city="Bengaluru",
            latitude=12.9716,
            longitude=77.5946,
        )
        pune = TransactionRecord(
            transaction_id="GEO-PUNE-1",
            date="2026-04-03",
            amount=1500.0,
            currency="INR",
            signed_amount=-1500.0,
            direction="debit",
            vendor="Pune Vendor",
            description="Card payment",
            source_file="pune.csv",
            channel="POS",
            country="India",
            state="Maharashtra",
            city="Pune",
            latitude=None,
            longitude=None,
        )
        no_location = TransactionRecord(
            transaction_id="GEO-NOLOC-1",
            date="2026-04-04",
            amount=5000.0,
            currency="INR",
            signed_amount=-5000.0,
            direction="debit",
            vendor="Unknown Vendor",
            description="ATM withdrawal",
            source_file="nolocation.csv",
            channel="ATM",
        )
        db.add_all([*mumbai, bengaluru, pune, no_location])
        db.commit()
        for record in [*mumbai, bengaluru, pune, no_location]:
            db.refresh(record)
        return {
            "mumbai": [record.id for record in mumbai],
            "bengaluru": bengaluru.id,
            "pune": pune.id,
            "no_location": no_location.id,
        }
    finally:
        db.close()


def seed_medium_alert(session_factory, record_id):
    db = session_factory()
    try:
        record = db.get(TransactionRecord, record_id)
        db.add(
            RiskAlert(
                alert_code="RA-GEO-MEDIUM",
                fingerprint="geo-medium-alert",
                source_type="transaction",
                source_record_id=record.id,
                transaction_id=record.transaction_id,
                risk_category="Unusual Transaction Amount",
                severity="medium",
                risk_score=55,
                amount_value=record.amount,
                currency=record.currency,
                channel=record.channel,
                explanation="Amount is above the stored vendor baseline.",
                evidence={"record_ids": [record.id]},
                country=record.country,
                state=record.state,
                city=record.city,
                latitude=record.latitude,
                longitude=record.longitude,
                detected_at=datetime.now(timezone.utc),
                review_status="open",
            )
        )
        db.commit()
    finally:
        db.close()


def seed_geocode_cache(session_factory):
    db = session_factory()
    try:
        db.add(
            GeocodeCache(
                query_key=location_key("Pune", "Maharashtra", "India"),
                city="Pune",
                state="Maharashtra",
                country="India",
                display_name="Pune, Maharashtra, India",
                latitude=18.5204,
                longitude=73.8567,
                provider="nominatim",
                status="success",
            )
        )
        db.commit()
    finally:
        db.close()


def analyze(client):
    response = client.post("/api/risk/analyze", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_geo_summary_uses_stored_coordinates_and_alert_risk(geo_client):
    client, session_factory = geo_client
    ids = seed_geo(session_factory)
    analyze(client)
    seed_medium_alert(session_factory, ids["bengaluru"])

    response = client.get("/api/risk/geography/summary")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["available"] is True
    summary = data["summary"]
    assert summary["total_transactions_matched"] == 5
    assert summary["coordinate_transactions"] == 3
    assert summary["located_transactions"] == 3
    assert summary["unlocated_transactions"] == 2
    assert summary["high_risk_transactions"] >= 2
    assert summary["geocoding"]["requested"] == 1  # Pune has names but no coordinates

    markers = {item["transaction_id"]: item for item in data["markers"]}
    mumbai = markers["GEO-MUM-1"]
    assert mumbai["city"] == "Mumbai"
    assert mumbai["state"] == "Maharashtra"
    assert mumbai["country"] == "India"
    assert mumbai["source_file"] == "mumbai.csv"
    assert mumbai["date"] == "2026-04-01"
    assert mumbai["amount"] == 7500.0
    assert mumbai["currency"] == "INR"
    assert mumbai["risk_level"] in {"high", "critical"}
    assert mumbai["risk_score"] > 0
    assert "duplicate payment" in mumbai["reason"].lower()

    bengaluru = markers["GEO-BLR-1"]
    assert bengaluru["risk_level"] == "medium"
    assert bengaluru["risk_score"] == 55
    assert bengaluru["reason"] == "Amount is above the stored vendor baseline."

    countries = {item["country"]: item for item in data["countries"]}
    assert countries["India"]["transaction_count"] == 3
    assert countries["India"]["high_risk_count"] >= 2
    assert countries["India"]["flagged_value"] > 0
    assert countries["India"]["critical_count"] >= 1

    cities = {item["city"]: item for item in data["cities"]}
    assert cities["Mumbai"]["transaction_count"] == 2
    assert cities["Mumbai"]["high_risk_count"] >= 2
    assert data["high_risk_locations"][0]["high_risk_count"] >= 1
    assert data["trend"]
    assert data["options"]["countries"] == ["India"]


def test_geo_filters_apply_to_map_and_statistics(geo_client):
    client, session_factory = geo_client
    seed_geo(session_factory)
    analyze(client)

    country = client.get("/api/risk/geography/summary?country=India")
    assert country.status_code == 200
    assert country.json()["summary"]["total_transactions_matched"] == 4

    no_country = client.get("/api/risk/geography/summary?country=Nowhere")
    assert no_country.status_code == 200
    assert no_country.json()["available"] is False
    assert no_country.json()["summary"]["total_transactions_matched"] == 0
    assert "No transactions match" in no_country.json()["reason"]

    high = client.get("/api/risk/geography/summary?risk_level=high")
    assert high.status_code == 200
    assert {
        item["risk_level"] for item in high.json()["markers"]
    } <= {"high", "critical"}
    assert high.json()["summary"]["high_risk_transactions"] >= 2

    channel = client.get("/api/risk/geography/summary?channel=UPI")
    assert channel.status_code == 200
    assert [item["transaction_id"] for item in channel.json()["markers"]] == [
        "GEO-BLR-1"
    ]

    amount = client.get("/api/risk/geography/summary?min_amount=10000")
    assert amount.status_code == 200
    assert {item["transaction_id"] for item in amount.json()["markers"]} == {
        "GEO-BLR-1"
    }

    dates = client.get(
        "/api/risk/geography/summary?start_date=2026-04-02&end_date=2026-04-03"
    )
    assert dates.status_code == 200
    assert {item["transaction_id"] for item in dates.json()["markers"]} == {
        "GEO-BLR-1"
    }


def test_geo_empty_state_and_geocode_cache(geo_client):
    client, session_factory = geo_client
    seed_geo(session_factory)
    analyze(client)

    # Only the ATM transaction has no location, so the map has nothing to plot.
    empty = client.get("/api/risk/geography/summary?channel=ATM")
    assert empty.status_code == 200
    payload = empty.json()
    assert payload["available"] is False
    assert payload["markers"] == []
    assert payload["summary"]["located_transactions"] == 0
    assert "location" in payload["reason"].lower()

    seed_geocode_cache(session_factory)
    cached = client.get("/api/risk/geography/summary?country=India")
    assert cached.status_code == 200
    markers = {item["transaction_id"]: item for item in cached.json()["markers"]}
    assert markers["GEO-PUNE-1"]["latitude"] == pytest.approx(18.5204)
    assert markers["GEO-PUNE-1"]["longitude"] == pytest.approx(73.8567)
    assert markers["GEO-PUNE-1"]["location_source"] == "geocode_cache"


def test_geocode_cache_avoids_repeated_network_lookups(geo_client, monkeypatch):
    _, session_factory = geo_client
    db = session_factory()
    calls = []
    monkeypatch.setenv("FINAUDIT_GEOCODING_MAX_LOOKUPS", "10")

    try:
        def not_found(*args):
            calls.append(args)
            return True, None

        monkeypatch.setattr(geocoding, "_request_json", not_found)
        locations = [{"city": "Nowhere", "state": "", "country": "India"}]

        resolved, stats = resolve_locations(db, locations, allow_network=True)
        assert resolved == {}
        assert stats["network_lookups"] == 1
        assert stats["unresolved"] == 1
        key = location_key("Nowhere", "", "India")
        row = db.query(GeocodeCache).filter(GeocodeCache.query_key == key).one()
        assert row.status == "not_found"

        resolved_again, stats_again = resolve_locations(
            db, locations, allow_network=True
        )
        assert resolved_again == {}
        assert stats_again["network_lookups"] == 0
        assert stats_again["cache_hits"] == 1
        assert stats_again["unresolved"] == 1
        assert len(calls) == 1

        def network_error(*args):
            calls.append(args)
            return False, None

        monkeypatch.setattr(geocoding, "_request_json", network_error)
        retry_key = location_key("Retry", "", "India")
        retry_locations = [{"city": "Retry", "state": "", "country": "India"}]
        _, error_stats = resolve_locations(db, retry_locations, allow_network=True)
        assert error_stats["network_lookups"] == 1
        assert error_stats["errors"] == 1
        assert (
            db.query(GeocodeCache)
            .filter(GeocodeCache.query_key == retry_key)
            .count()
            == 0
        )

        _, second_error_stats = resolve_locations(
            db, retry_locations, allow_network=True
        )
        assert second_error_stats["network_lookups"] == 1
        assert len(calls) == 3
    finally:
        db.close()


def test_geo_country_filter_uses_alert_location_when_transaction_has_none(geo_client):
    client, session_factory = geo_client
    db = session_factory()
    try:
        record = TransactionRecord(
            transaction_id="GEO-ALERT-LOC-1",
            date="2026-05-01",
            amount=12000.0,
            currency="KES",
            signed_amount=-12000.0,
            direction="debit",
            vendor="Nairobi Vendor",
            description="Wire transfer",
            source_file="alert-location.csv",
            channel="NEFT",
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        db.add(
            RiskAlert(
                alert_code="RA-GEO-ALERT-LOC",
                fingerprint="geo-alert-location",
                source_type="transaction",
                source_record_id=record.id,
                transaction_id=record.transaction_id,
                risk_category="High Risk Jurisdiction",
                severity="critical",
                risk_score=91,
                amount_value=record.amount,
                currency=record.currency,
                channel=record.channel,
                explanation="The linked alert stores the location for this transaction.",
                evidence={"record_ids": [record.id]},
                country="Kenya",
                state="Nairobi County",
                city="Nairobi",
                latitude=-1.2921,
                longitude=36.8219,
                detected_at=datetime.now(timezone.utc),
                review_status="open",
            )
        )
        db.commit()
    finally:
        db.close()

    analyze(client)
    response = client.get("/api/risk/geography/summary?country=Kenya")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is True
    marker = next(
        item for item in payload["markers"] if item["transaction_id"] == "GEO-ALERT-LOC-1"
    )
    assert marker["country"] == "Kenya"
    assert marker["city"] == "Nairobi"
    assert marker["location_source"] == "alert"
    assert marker["risk_level"] == "critical"
    assert payload["countries"][0]["country"] == "Kenya"


def test_geo_specific_endpoints_and_validation(geo_client):
    client, session_factory = geo_client
    seed_geo(session_factory)
    analyze(client)

    options = client.get("/api/risk/geography/options")
    assert options.status_code == 200
    assert "India" in options.json()["options"]["countries"]
    assert "NEFT" in options.json()["options"]["channels"]
    assert options.json()["geocoding"]["provider"] == "nominatim"

    markers = client.get("/api/risk/geography/map")
    assert markers.status_code == 200
    assert len(markers.json()["markers"]) == 3

    countries = client.get("/api/risk/geography/countries")
    assert countries.status_code == 200
    assert countries.json()["countries"][0]["country"] == "India"

    cities = client.get("/api/risk/geography/cities")
    assert cities.status_code == 200
    assert {item["city"] for item in cities.json()["cities"]} == {
        "Mumbai",
        "Bengaluru",
    }

    ranked = client.get("/api/risk/geography/high-risk-locations")
    assert ranked.status_code == 200
    assert ranked.json()["high_risk_locations"]

    trend = client.get("/api/risk/geography/trend")
    assert trend.status_code == 200
    assert trend.json()["trend"]

    bad_date = client.get("/api/risk/geography/summary?start_date=not-a-date")
    assert bad_date.status_code == 422

    bad_order = client.get(
        "/api/risk/geography/summary?start_date=2026-05-01&end_date=2026-04-01"
    )
    assert bad_order.status_code == 422

    bad_level = client.get("/api/risk/geography/summary?risk_level=severe")
    assert bad_level.status_code == 422

    bad_amount = client.get("/api/risk/geography/summary?min_amount=-1")
    assert bad_amount.status_code == 422
