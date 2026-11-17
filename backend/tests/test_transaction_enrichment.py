"""End-to-end tests for enriched transaction ingestion and geo analytics.

The synthetic CSV used here is fictional, explicitly labelled, and uploaded
into a temporary test database. It is never mixed with real records.
"""
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import TransactionRecord

SYNTHETIC_CSV = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "synthetic"
    / "fictional_geo_transactions.csv"
)


def test_synthetic_upload_to_geo_dashboard_pipeline(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'enrichment.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            payload = SYNTHETIC_CSV.read_bytes()
            upload = client.post(
                "/api/audits",
                files={"file": ("fictional_geo_transactions.csv", payload, "text/csv")},
            )
            assert upload.status_code == 200, upload.text
            result = upload.json()
            assert result["row_count"] == 26
            assert any("Latitude 999" in item for item in result["warnings"])

            db = TestingSession()
            try:
                records = db.query(TransactionRecord).all()
                by_id = {item.transaction_id: item for item in records}
                assert by_id["SYN-001"].merchant == "Aurora Cafe"
                assert by_id["SYN-001"].channel == "Point of Sale"
                assert by_id["SYN-001"].channel_source == "explicit"
                assert by_id["SYN-001"].location_status == "mapped"
                assert by_id["SYN-001"].latitude is not None
                assert by_id["SYN-011"].channel == "Wire Transfer"
                assert by_id["SYN-011"].channel_source == "inferred"
                assert "RTGS" in by_id["SYN-011"].description
                assert by_id["SYN-014"].location_status == "named_only"
                assert by_id["SYN-014"].latitude is None
                assert by_id["SYN-015"].location_status == "invalid_coordinates"
                assert by_id["SYN-016"].location_status == "unavailable"
            finally:
                db.close()

            analysis = client.post("/api/risk/analyze", json={})
            assert analysis.status_code == 200, analysis.text
            assert analysis.json()["transaction_count"] == 26
            assert analysis.json()["alert_count"] > 0

            geo = client.get("/api/risk/geography/summary?geocode=false")
            assert geo.status_code == 200, geo.text
            data = geo.json()
            assert data["available"] is True
            assert data["summary"]["countries_count"] >= 5
            assert data["summary"]["cities_count"] >= 5
            assert data["summary"]["high_risk_transactions"] > 0
            assert data["summary"]["flagged_value"] > 0
            assert data["summary"]["location_status_counts"]["invalid_coordinates"] >= 1
            assert data["summary"]["location_status_counts"]["named_only"] >= 1
            assert data["summary"]["location_status_counts"]["unavailable"] >= 1
            assert data["trend"]
            assert data["markers"]
            assert any(marker["channel_source"] == "inferred" for marker in data["markers"])
            assert all("location_status" in marker for marker in data["markers"])

            analytics = data["channel_analytics"]
            channels = {item["channel"]: item for item in analytics["channels"]}
            assert set(
                [
                    "Online Banking",
                    "Mobile App",
                    "ATM",
                    "Wire Transfer",
                    "Point of Sale",
                    "UPI",
                    "Card Payment",
                    "Cash",
                    "Unknown",
                ]
            ).issubset(channels)
            assert channels["UPI"]["transaction_count"] > 0
            assert channels["UPI"]["flagged_amount"] > 0
            assert channels["Wire Transfer"]["inferred_count"] > 0
            assert analytics["unknown_channel_count"] >= 1
            assert analytics["inferred_count"] > 0

            filtered = client.get(
                "/api/risk/geography/summary?channel=UPI&geocode=false"
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["markers"]
            assert {
                item["channel"] for item in filtered.json()["markers"]
            } == {"UPI"}
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_legacy_explicit_channels_remain_visible(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        db = TestingSession()
        try:
            db.add(
                TransactionRecord(
                    transaction_id="LEGACY-NEFT-1",
                    date="2026-07-01",
                    amount=1000,
                    direction="debit",
                    vendor="Legacy Vendor",
                    channel="NEFT",
                    country="India",
                    city="Mumbai",
                    latitude=19.076,
                    longitude=72.8777,
                    source_file="legacy.csv",
                )
            )
            db.commit()
        finally:
            db.close()

        with TestClient(app) as client:
            options = client.get("/api/risk/geography/options")
            assert options.status_code == 200
            assert "NEFT" in options.json()["options"]["channels"]
            assert "Unknown" in options.json()["options"]["channels"]

            analytics = client.get("/api/risk/channels")
            assert analytics.status_code == 200
            channels = {item["channel"]: item for item in analytics.json()["channels"]}
            assert channels["NEFT"]["transaction_count"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
