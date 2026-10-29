import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-analytics-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_monthly_summary_empty():
    resp = client.get("/api/analytics/monthly/summary?year=2026&month=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] == 2026
    assert data["month"] == 1
    assert "total_spend" in data
    assert "total_received" in data
    assert "net_cash_flow" in data


def test_monthly_categories_empty():
    resp = client.get("/api/analytics/monthly/categories?year=2026&month=1")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_analytics_transactions_filter():
    resp = client.get("/api/analytics/transactions?year=2026&month=1&limit=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_analytics_comparison_empty():
    resp = client.get("/api/analytics/monthly/comparison?year=2026&month=2")
    assert resp.status_code == 200
    data = resp.json()
    assert "current" in data
    assert "previous" in data
