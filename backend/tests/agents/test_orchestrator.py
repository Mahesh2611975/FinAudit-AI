import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-agents-tests-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"

from app.agents.orchestrator import MultiAgentOrchestrator  # noqa: E402


def test_orchestrator_runs_full_workflow():
    orch = MultiAgentOrchestrator()
    result = orch.run({"task_type": "monthly_analysis", "year": 2026, "month": 1}, persist=False)
    assert result["status"] in ("completed", "failed")
    assert isinstance(result.get("timeline"), list)
    assert len(result.get("timeline")) >= 1
    agents = [t.get("agent_name") for t in result["timeline"]]
    assert "Transaction Intake Agent" in agents
    assert "Reconciliation Agent" in agents
    assert "Anomaly Investigation Agent" in agents
    assert "Financial Analysis Agent" in agents
    assert "Evidence Retrieval / RAG Agent" in agents
    assert "Investigation Synthesis Agent" in agents
    assert "Reporting Agent" in agents


def test_orchestrator_fails_gracefully_on_empty_input():
    orch = MultiAgentOrchestrator()
    result = orch.run({}, persist=False)
    assert result["status"] == "failed"
    assert result.get("errors")
