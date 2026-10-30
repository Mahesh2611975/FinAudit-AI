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


def upload_csv(csv_text: str, filename: str) -> None:
    response = client.post(
        "/api/audits",
        files={"files": (filename, csv_text.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200, response.text


def test_monthly_summary_matches_deterministic_transaction_totals():
    upload_csv(
        "transaction_id,date,type,vendor,amount,description\n"
        "P1,2031-03-02,debit,Store,300.00,Grocery purchase\n",
        "march-2031.csv",
    )
    upload_csv(
        "transaction_id,date,type,vendor,amount,description\n"
        "A1,2031-04-01,debit,Store,100.50,Grocery purchase\n"
        "A2,2031-04-02,debit,Restaurant,200.00,Dinner\n"
        "A3,2031-04-03,credit,Employer,1000.00,Salary\n"
        "A4,2031-04-04,debit,Store,50.00,Grocery purchase\n",
        "april-2031.csv",
    )

    summary = client.get(
        "/api/analytics/monthly/summary?year=2031&month=4"
    ).json()
    assert summary["has_transactions"] is True
    assert summary["transaction_count"] == 4
    assert summary["total_spend"] == 350.5
    assert summary["total_received"] == 1000.0
    assert summary["net_cash_flow"] == 649.5
    assert summary["spending_transaction_count"] == 3
    assert summary["credit_transaction_count"] == 1

    categories = client.get(
        "/api/analytics/monthly/categories?year=2031&month=4"
    ).json()
    category_totals = {item["category"]: item["amount"] for item in categories}
    assert category_totals["Groceries"] == 150.5
    assert category_totals["Food and dining"] == 200.0

    comparison = client.get(
        "/api/analytics/monthly/comparison?year=2031&month=4"
    ).json()
    assert comparison["available"] is True
    assert comparison["current"]["spend"] == 350.5
    assert comparison["previous"]["spend"] == 300.0
    assert comparison["spend_change"] == 50.5


def test_monthly_summary_rejects_invalid_month():
    response = client.get("/api/analytics/monthly/summary?year=2031&month=13")
    assert response.status_code == 400
    assert "month must be between 1 and 12" in response.json()["detail"]
