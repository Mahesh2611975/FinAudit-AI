from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class AgentStatus(BaseModel):
    agent_name: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    duration_ms: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class EvidenceItem(BaseModel):
    source_file: str | None = None
    source_row: int | None = None
    transaction_id: str | None = None
    excerpt: str | None = None
    record_id: str | None = None


class OrchestratorInput(BaseModel):
    task_type: Literal["monthly_analysis", "investigation", "reconciliation_review", "invoice_audit"]
    year: int | None = None
    month: int | None = None
    audit_run_id: int | None = None
    reconciliation_id: int | None = None
    case_id: int | None = None
    focus_transaction_id: str | None = None
    invoice_id: int | None = None


class AgentOutput(BaseModel):
    agent_name: str
    status: str
    facts: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
