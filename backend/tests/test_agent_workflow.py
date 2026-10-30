import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-agent-workflow-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"

from fastapi.testclient import TestClient  # noqa: E402

import app.agents.orchestrator as orchestrator_module  # noqa: E402
from app import llm  # noqa: E402
from app.agents.orchestrator import MultiAgentOrchestrator  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def upload_csv(csv_text: str, filename: str) -> dict:
    response = client.post(
        "/api/audits",
        files={"files": (filename, csv_text.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def disable_llm(monkeypatch):
    monkeypatch.setattr(llm, "get_api_key", lambda: None)
    monkeypatch.setattr(llm, "client", None)


JUNE_CSV = (
    "transaction_id,date,type,vendor,amount,description\n"
    "D1,2032-06-01,debit,Grocery Mart,100.25,Grocery store purchase\n"
    "D2,2032-06-01,debit,Grocery Mart,100.25,Grocery store purchase\n"
    "D3,2032-06-05,credit,Employer,5000.00,Salary credit\n"
    "D4,2032-06-10,debit,Amazon,15000.00,Laptop purchase\n"
)


def test_monthly_agent_executes_real_nodes_and_persists_results(monkeypatch):
    disable_llm(monkeypatch)
    audit = upload_csv(JUNE_CSV, "june-2032.csv")
    assert audit["finding_count"] == 3

    response = client.post(
        "/api/agents/run",
        json={"task_type": "monthly_analysis", "year": 2032, "month": 6},
    )
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["status"] == "completed"
    assert result["run_id"]
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0
    assert result["started_at"] and result["finished_at"]

    timeline = result["timeline"]
    assert len(timeline) == 7
    assert len({item["agent_name"] for item in timeline}) == 7
    assert all(item["status"] == "completed" for item in timeline)
    assert all(item["status"] != "running" for item in timeline)
    assert all(isinstance(item["duration_ms"], int) for item in timeline)
    assert all(item["started_at"] and item["finished_at"] for item in timeline)
    assert len(result["events"]) == 14

    analysis = result["outputs"]["analysis"]
    summary = analysis["summary"]
    assert analysis["source"] == "deterministic_monthly_analysis"
    assert analysis["has_transactions"] is True
    assert summary["total_spending"] == 15200.5
    assert summary["total_credits"] == 5000.0
    assert summary["net_cash_flow"] == -10200.5
    assert summary["transaction_count"] == 4
    categories = {item["category"]: item["amount"] for item in analysis["category_totals"]}
    assert categories["Groceries"] == 200.5
    assert categories["Shopping"] == 15000.0

    anomaly = result["outputs"]["anomaly"]
    assert anomaly["summary"]["finding_count"] == 3
    finding_ids = {item["id"] for item in anomaly["findings"]}
    assert len(finding_ids) == 3
    assert all(item["transaction_id"] for item in anomaly["findings"])

    evidence = result["evidence"]
    transaction_evidence = [
        item for item in evidence if item.get("source_type") == "transaction_record"
    ]
    policy_evidence = [
        item for item in evidence if item.get("source_type") == "audit_policy"
    ]
    assert transaction_evidence
    assert all(item.get("source_file") == "june-2032.csv" for item in transaction_evidence)
    assert all(item.get("source_row") is not None for item in transaction_evidence)
    assert policy_evidence
    assert all(item.get("source_id") for item in policy_evidence)

    synthesis = result["outputs"]["synthesis"]
    assert synthesis["llm"]["configured"] is False
    assert any("GROQ_API_KEY" in item for item in synthesis["limitations"])
    assert synthesis["facts"]
    assert synthesis["hypotheses"]
    assert synthesis["recommendations"]

    report = result["outputs"]["report"]
    assert report["financial_results"]["summary"] == summary
    assert report["anomaly_summary"] == anomaly["summary"]
    assert report["evidence_references"]
    assert report["conclusion_status"] == "requires_human_review"
    assert "does not prove fraud" in report["disclaimer"]

    detail = client.get(f"/api/agents/runs/{result['run_id']}").json()
    assert detail["status"] == "completed"
    assert detail["selected_year"] == 2032
    assert detail["selected_month"] == 6
    assert detail["duration_ms"] == result["duration_ms"]
    assert detail["started_at"] and detail["finished_at"]
    assert detail["timeline"] == result["timeline"]
    assert detail["events"] == result["events"]
    assert detail["evidence"] == result["evidence"]
    assert detail["output_state"]["outputs"]["analysis"]["summary"] == summary

    history = client.get("/api/agents/runs").json()
    persisted = next(item for item in history if item["id"] == result["run_id"])
    assert persisted["status"] == "completed"
    assert persisted["duration_ms"] == result["duration_ms"]
    assert persisted["selected_year"] == 2032
    assert persisted["selected_month"] == 6
    assert persisted["evidence_count"] == len(result["evidence"])


def test_monthly_agent_returns_truthful_empty_state(monkeypatch):
    disable_llm(monkeypatch)
    response = client.post(
        "/api/agents/run",
        json={"task_type": "monthly_analysis", "year": 2034, "month": 1},
    )
    assert response.status_code == 200, response.text
    result = response.json()

    analysis = result["outputs"]["analysis"]
    assert result["status"] == "completed"
    assert analysis["has_transactions"] is False
    assert analysis["summary"]["transaction_count"] == 0
    assert analysis["summary"]["total_spending"] == 0.0
    assert analysis["summary"]["total_credits"] == 0.0
    assert analysis["anomalies"] == []
    assert any("No transactions were found" in note for note in analysis["data_quality"]["notes"])
    assert result["missing_evidence"]
    assert result["outputs"]["synthesis"]["conclusion_status"] == "insufficient_evidence"
    assert result["require_human_review"] is True


def test_invalid_month_is_recorded_as_failed_run(monkeypatch):
    disable_llm(monkeypatch)
    response = client.post(
        "/api/agents/run",
        json={"task_type": "monthly_analysis", "year": 2032, "month": 13},
    )
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["status"] == "failed"
    assert result["errors"]
    assert result["timeline"][0]["agent_name"] == "Transaction Intake Agent"
    assert result["timeline"][0]["status"] == "failed"
    assert result["run_id"]
    detail = client.get(f"/api/agents/runs/{result['run_id']}").json()
    assert detail["status"] == "failed"
    assert detail["errors"]


def test_node_failure_is_recorded_and_downstream_nodes_are_skipped(monkeypatch):
    disable_llm(monkeypatch)

    def fail_analysis(db, year, month):
        raise RuntimeError("deterministic analytics unavailable")

    monkeypatch.setattr(orchestrator_module, "monthly_analysis", fail_analysis)
    result = MultiAgentOrchestrator().run(
        {"task_type": "monthly_analysis", "year": 2035, "month": 2},
        persist=False,
    )

    assert result["status"] == "failed"
    assert any("deterministic analytics unavailable" in item for item in result["errors"])
    timeline = {item["agent_name"]: item for item in result["timeline"]}
    assert timeline["Financial Analysis Agent"]["status"] == "failed"
    assert "deterministic analytics unavailable" in timeline["Financial Analysis Agent"]["error"]
    for agent in (
        "Evidence Retrieval / RAG Agent",
        "Investigation Synthesis Agent",
        "Reporting Agent",
    ):
        assert timeline[agent]["status"] == "skipped"


def test_reconciliation_review_without_id_reports_missing_input(monkeypatch):
    disable_llm(monkeypatch)
    response = client.post(
        "/api/agents/run",
        json={"task_type": "reconciliation_review"},
    )
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["status"] == "needs_human_review"
    assert result["outputs"]["reconciliation"]["available"] is False
    assert "reconciliation_id" in result["outputs"]["reconciliation"]["reason"]
    assert result["outputs"]["analysis"]["available"] is False
    assert result["missing_evidence"]
    assert result["outputs"]["synthesis"]["conclusion_status"] == "insufficient_evidence"


def test_transient_llm_failure_is_retried_once(monkeypatch):
    upload_csv(
        "transaction_id,date,type,vendor,amount,description\n"
        "R1,2036-08-01,debit,Amazon,20000.00,Laptop purchase\n",
        "retry-2036-08.csv",
    )
    calls = []

    async def fake_explain(question, evidence=""):
        calls.append(question)
        if len(calls) == 1:
            return {
                "answer": "temporary provider failure",
                "fallback": True,
                "error_category": "timeout",
                "model": "mock-model",
                "retrieved_sources": [],
            }
        return {
            "answer": "retried narrative",
            "fallback": False,
            "model": "mock-model",
            "retrieved_sources": [],
        }

    monkeypatch.setattr(llm, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(llm, "client", object())
    monkeypatch.setattr(llm, "explain_finding", fake_explain)

    response = client.post(
        "/api/agents/run",
        json={"task_type": "monthly_analysis", "year": 2036, "month": 8},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    synthesis = result["outputs"]["synthesis"]

    assert len(calls) == 2
    assert synthesis["llm"]["attempts"] == 2
    assert synthesis["llm"]["used"] is True
    assert synthesis["llm"]["fallback"] is False
    assert synthesis["narrative"] == "retried narrative"


def test_reconciliation_review_agent_uses_persisted_reconciliation_data(monkeypatch):
    disable_llm(monkeypatch)
    bank_csv = (
        "date,type,amount,account,recipient,upi_ref,balance_after,source_sms\n"
        "2033-07-01,debit,100.00,XXXX,Shop,REF-1,900.00,Paid\n"
        "2033-07-02,debit,50.00,XXXX,Vendor,REF-2,850.00,Paid\n"
    )
    ledger_csv = (
        "date,type,amount,account,recipient,upi_ref,balance_after,source_sms\n"
        "2033-07-01,debit,100.00,XXXX,Shop,REF-1,900.00,Paid\n"
    )
    recon_response = client.post(
        "/api/reconciliations",
        files={
            "bank_file": ("bank-2033-07.csv", bank_csv.encode("utf-8"), "text/csv"),
            "ledger_file": ("ledger-2033-07.csv", ledger_csv.encode("utf-8"), "text/csv"),
        },
        data={"date_tolerance_days": "3"},
    )
    assert recon_response.status_code == 200, recon_response.text
    reconciliation_id = recon_response.json()["reconciliation_id"]

    response = client.post(
        "/api/agents/run",
        json={
            "task_type": "reconciliation_review",
            "reconciliation_id": reconciliation_id,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()

    recon = result["outputs"]["reconciliation"]
    assert recon["available"] is True
    assert recon["reconciliation_id"] == reconciliation_id
    assert recon["summary"]["result_count"] == 2
    assert result["outputs"]["anomaly"]["summary"]["finding_count"] == 1
    assert result["outputs"]["analysis"]["source"] == "persisted_reconciliation_run"
    assert result["evidence"]
    assert all(item.get("source_type") == "reconciliation_result" for item in result["evidence"] if item.get("kind") != "policy")
    assert result["status"] == "needs_human_review"
    assert result["outputs"]["report"]["conclusion_status"] == "requires_human_review"
