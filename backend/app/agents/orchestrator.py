from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import llm
from ..analytics import (
    AnalyticsInputError,
    load_month_transactions,
    monthly_analysis,
    monthly_anomalies,
)
from ..database import SessionLocal
from ..models import (
    Finding,
    InvestigationCase,
    InvestigationRun,
    ReconciliationResult,
    ReconciliationRun,
    TransactionRecord,
)
from ..rag import retrieve_context
from .schemas import OrchestratorInput

AGENT_INTAKE = "Transaction Intake Agent"
AGENT_RECON = "Reconciliation Agent"
AGENT_ANOMALY = "Anomaly Investigation Agent"
AGENT_ANALYSIS = "Financial Analysis Agent"
AGENT_EVIDENCE = "Evidence Retrieval / RAG Agent"
AGENT_SYNTHESIS = "Investigation Synthesis Agent"
AGENT_REPORT = "Reporting Agent"

EXCEPTION_RECON_STATUSES = {
    "bank_only",
    "ledger_only",
    "amount_mismatch",
    "date_mismatch",
    "ambiguous",
}


class WorkflowState(TypedDict, total=False):
    input: dict[str, Any]
    timeline: list[dict[str, Any]]
    events: list[dict[str, Any]]
    status: str
    errors: list[str]
    outputs: dict[str, Any]
    evidence: list[dict[str, Any]]
    missing_evidence: list[str]
    require_human_review: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(started: datetime, finished: datetime) -> int:
    return max(0, int(round((finished - started).total_seconds() * 1000)))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _recon_summary(run: ReconciliationRun) -> dict[str, Any]:
    return {
        "bank_total": run.bank_total,
        "ledger_total": run.ledger_total,
        "matched_total": run.matched_total,
        "bank_unmatched_total": run.bank_unmatched_total,
        "ledger_unmatched_total": run.ledger_unmatched_total,
        "unmatched_total": run.unmatched_total,
        "difference": run.difference,
        "result_count": run.result_count,
        "matched_count": run.matched_count,
        "bank_only_count": run.bank_only_count,
        "ledger_only_count": run.ledger_only_count,
        "amount_mismatch_count": run.amount_mismatch_count,
        "date_mismatch_count": run.date_mismatch_count,
        "ambiguous_count": run.ambiguous_count,
    }


def _recon_result_evidence(item: ReconciliationResult, kind: str) -> dict[str, Any]:
    if item.bank_source_file and item.bank_source_row is not None:
        source_file = item.bank_source_file
        source_row = item.bank_source_row
        excerpt = (
            f"{item.bank_date or 'unknown date'} | {item.bank_direction or 'unknown'} "
            f"| {item.bank_amount} | {item.bank_description or item.bank_reference or ''}"
        )
    else:
        source_file = item.ledger_source_file
        source_row = item.ledger_source_row
        excerpt = (
            f"{item.ledger_date or 'unknown date'} | {item.ledger_direction or 'unknown'} "
            f"| {item.ledger_amount} | {item.ledger_description or item.ledger_reference or ''}"
        )
    return {
        "kind": kind,
        "source_id": f"reconciliation-result:{item.id}",
        "source_type": "reconciliation_result",
        "record_id": str(item.id),
        "source_file": source_file,
        "source_row": source_row,
        "transaction_id": item.bank_reference or item.ledger_reference,
        "excerpt": excerpt,
        "status": item.status,
        "note": item.note,
    }


def _transaction_evidence(
    transaction: TransactionRecord, kind: str = "transaction"
) -> dict[str, Any]:
    excerpt = (
        f"{transaction.date or 'unknown date'} | "
        f"{transaction.direction or 'unknown'} | "
        f"{transaction.amount} | "
        f"{transaction.vendor or ''} {transaction.description or ''}"
    ).strip()
    return {
        "kind": kind,
        "source_id": (
            f"transaction:{transaction.transaction_id}"
            if transaction.transaction_id
            else f"transaction-record:{transaction.id}"
        ),
        "source_type": "transaction_record",
        "record_id": str(transaction.id),
        "source_file": transaction.source_file,
        "source_row": transaction.source_row,
        "transaction_id": transaction.transaction_id,
        "excerpt": excerpt,
        "category": transaction.category,
    }


class MultiAgentOrchestrator:
    def __init__(self):
        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    # Timeline / event recording
    # ------------------------------------------------------------------
    def _record(
        self,
        state: WorkflowState,
        agent_name: str,
        status: str,
        started: datetime,
        finished: datetime,
        *,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        duration = _duration_ms(started, finished)
        entry = {
            "agent_name": agent_name,
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": duration,
        }
        if summary:
            entry["summary"] = summary
        if error:
            entry["error"] = error

        timeline = [
            item
            for item in (state.get("timeline") or [])
            if item.get("agent_name") != agent_name
        ]
        timeline.append(entry)
        state["timeline"] = timeline

        events = list(state.get("events") or [])
        events.append(
            {
                "agent_name": agent_name,
                "event": "started",
                "timestamp": started.isoformat(),
            }
        )
        events.append(
            {
                "agent_name": agent_name,
                "event": status,
                "timestamp": finished.isoformat(),
                "duration_ms": duration,
                **({"error": error} if error else {}),
            }
        )
        state["events"] = events

    def _execute(
        self,
        state: WorkflowState,
        agent_name: str,
        handler: Callable[[WorkflowState], dict[str, Any]],
    ) -> WorkflowState:
        if state.get("status") == "failed":
            timestamp = _now()
            self._record(
                state,
                agent_name,
                "skipped",
                timestamp,
                timestamp,
                summary="Skipped because an upstream node failed.",
            )
            return state

        started = _now()
        try:
            result = handler(state) or {}
        except Exception as exc:  # noqa: BLE001 - recorded honestly for the run
            finished = _now()
            message = f"{type(exc).__name__}: {exc}"
            self._record(
                state,
                agent_name,
                "failed",
                started,
                finished,
                error=message,
            )
            state["errors"] = list(state.get("errors") or []) + [
                f"{agent_name}: {message}"
            ]
            state["status"] = "failed"
            return state

        finished = _now()
        self._record(
            state,
            agent_name,
            "completed",
            started,
            finished,
            summary=result.get("summary"),
        )
        outputs = dict(state.get("outputs") or {})
        outputs[result.get("key", agent_name)] = result.get("output")
        state["outputs"] = outputs
        if "evidence" in result:
            state["evidence"] = list(result.get("evidence") or [])
        if "missing_evidence" in result:
            state["missing_evidence"] = list(result.get("missing_evidence") or [])
        if "require_human_review" in result:
            state["require_human_review"] = bool(
                result.get("require_human_review")
            )
        return state

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _intake(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            payload = current.get("input") or {}
            if not payload:
                raise ValueError("No input provided")
            try:
                parsed = OrchestratorInput.model_validate(payload)
            except ValidationError as exc:
                messages = "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
                raise ValueError(f"Invalid input: {messages}") from exc

            if parsed.task_type == "monthly_analysis":
                if parsed.year is None or parsed.month is None:
                    raise ValueError(
                        "Monthly analysis requires both year and month."
                    )
                if parsed.month < 1 or parsed.month > 12:
                    raise ValueError("month must be between 1 and 12")
            elif parsed.task_type == "investigation":
                has_identifier = any(
                    value is not None
                    for value in (
                        parsed.case_id,
                        parsed.audit_run_id,
                        parsed.focus_transaction_id,
                    )
                )
                has_period = parsed.year is not None and parsed.month is not None
                if not has_identifier and not has_period:
                    raise ValueError(
                        "Investigation requires case_id, audit_run_id, "
                        "focus_transaction_id, or year and month."
                    )
            elif parsed.task_type == "reconciliation_review":
                if parsed.reconciliation_id is None:
                    # Not fatal: the reconciliation node reports the missing input.
                    pass

            output = {
                "validated": True,
                "task_type": parsed.task_type,
                "year": parsed.year,
                "month": parsed.month,
                "audit_run_id": parsed.audit_run_id,
                "reconciliation_id": parsed.reconciliation_id,
                "case_id": parsed.case_id,
                "focus_transaction_id": parsed.focus_transaction_id,
            }
            return {
                "key": "intake",
                "summary": "Input validated against the orchestrator schema.",
                "output": output,
            }

        return self._execute(state, AGENT_INTAKE, handler)

    def _recon(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            payload = current.get("input") or {}
            task_type = payload.get("task_type")
            db: Session = SessionLocal()
            try:
                if task_type == "reconciliation_review":
                    reconciliation_id = payload.get("reconciliation_id")
                    if reconciliation_id is None:
                        return {
                            "key": "reconciliation",
                            "summary": (
                                "Reconciliation review requested without a "
                                "reconciliation_id; no reconciliation data was loaded."
                            ),
                            "output": {
                                "available": False,
                                "applicable": True,
                                "reason": (
                                    "No reconciliation_id was supplied. Select a "
                                    "persisted reconciliation run to review."
                                ),
                            },
                        }
                    run = (
                        db.query(ReconciliationRun)
                        .filter(ReconciliationRun.id == reconciliation_id)
                        .first()
                    )
                    if run is None:
                        return {
                            "key": "reconciliation",
                            "summary": (
                                f"Reconciliation run {reconciliation_id} was not found."
                            ),
                            "output": {
                                "available": False,
                                "applicable": True,
                                "reason": (
                                    f"Reconciliation run {reconciliation_id} does "
                                    "not exist in the database."
                                ),
                            },
                        }
                    return {
                        "key": "reconciliation",
                        "summary": (
                            f"Loaded persisted reconciliation run {run.id} with "
                            f"{run.result_count} result row(s)."
                        ),
                        "output": {
                            "available": True,
                            "applicable": True,
                            "reconciliation_id": run.id,
                            "source": "persisted_reconciliation_run",
                            "bank_file": run.bank_filename,
                            "ledger_file": run.ledger_filename,
                            "date_tolerance_days": run.date_tolerance_days,
                            "summary": _recon_summary(run),
                        },
                    }

                year = payload.get("year")
                month = payload.get("month")
                if year is None or month is None:
                    return {
                        "key": "reconciliation",
                        "summary": (
                            "No period was supplied, so reconciliation results could "
                            "not be matched to this run."
                        ),
                        "output": {
                            "available": False,
                            "applicable": False,
                            "reason": "No reporting period was supplied.",
                        },
                    }
                prefix = f"{int(year):04d}-{int(month):02d}"
                results = (
                    db.query(ReconciliationResult)
                    .filter(
                        or_(
                            ReconciliationResult.bank_date.like(f"{prefix}%"),
                            ReconciliationResult.ledger_date.like(f"{prefix}%"),
                        )
                    )
                    .limit(1000)
                    .all()
                )
                if not results:
                    return {
                        "key": "reconciliation",
                        "summary": (
                            f"No persisted reconciliation results were recorded for {prefix}."
                        ),
                        "output": {
                            "available": False,
                            "applicable": False,
                            "period": prefix,
                            "reason": (
                                f"No reconciliation results exist for {prefix}. "
                                "This does not mean transactions reconcile."
                            ),
                        },
                    }
                run_ids = sorted({item.run_id for item in results})
                status_counts: dict[str, int] = {}
                for item in results:
                    status_counts[item.status] = (
                        status_counts.get(item.status, 0) + 1
                    )
                runs = (
                    db.query(ReconciliationRun)
                    .filter(ReconciliationRun.id.in_(run_ids))
                    .all()
                )
                return {
                    "key": "reconciliation",
                    "summary": (
                        f"Loaded {len(results)} persisted reconciliation result(s) "
                        f"for {prefix} from {len(run_ids)} run(s)."
                    ),
                    "output": {
                        "available": True,
                        "applicable": True,
                        "source": "persisted_reconciliation_results",
                        "period": prefix,
                        "reconciliation_ids": run_ids,
                        "result_count": len(results),
                        "status_counts": status_counts,
                        "runs": [
                            {
                                "reconciliation_id": run.id,
                                "bank_file": run.bank_filename,
                                "ledger_file": run.ledger_filename,
                                "summary": _recon_summary(run),
                            }
                            for run in runs
                        ],
                    },
                }
            finally:
                db.close()

        return self._execute(state, AGENT_RECON, handler)

    def _anomaly(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            payload = current.get("input") or {}
            task_type = payload.get("task_type")
            db: Session = SessionLocal()
            try:
                if task_type == "reconciliation_review":
                    reconciliation_id = payload.get("reconciliation_id")
                    if reconciliation_id is None:
                        return {
                            "key": "anomaly",
                            "summary": (
                                "No reconciliation_id was supplied; no reconciliation "
                                "exceptions could be investigated."
                            ),
                            "output": {
                                "mode": "reconciliation_exceptions",
                                "available": False,
                                "findings": [],
                                "summary": {"finding_count": 0},
                                "notes": [
                                    "No reconciliation_id was supplied, so no "
                                    "reconciliation exceptions were loaded."
                                ],
                            },
                        }
                    results = (
                        db.query(ReconciliationResult)
                        .filter(ReconciliationResult.run_id == reconciliation_id)
                        .all()
                    )
                    exceptions = [
                        item
                        for item in results
                        if item.status in EXCEPTION_RECON_STATUSES
                    ]
                    by_status: dict[str, int] = {}
                    for item in exceptions:
                        by_status[item.status] = by_status.get(item.status, 0) + 1
                    return {
                        "key": "anomaly",
                        "summary": (
                            f"Loaded {len(exceptions)} reconciliation exception(s) "
                            f"from run {reconciliation_id}."
                        ),
                        "output": {
                            "mode": "reconciliation_exceptions",
                            "available": bool(results),
                            "reconciliation_id": reconciliation_id,
                            "findings": [
                                {
                                    "id": item.id,
                                    "status": item.status,
                                    "bank_source_file": item.bank_source_file,
                                    "bank_source_row": item.bank_source_row,
                                    "ledger_source_file": item.ledger_source_file,
                                    "ledger_source_row": item.ledger_source_row,
                                    "amount_difference": item.amount_difference,
                                    "date_difference_days": item.date_difference_days,
                                    "note": item.note,
                                }
                                for item in exceptions
                            ],
                            "summary": {
                                "finding_count": len(exceptions),
                                "by_status": by_status,
                                "total_result_count": len(results),
                            },
                            "notes": (
                                []
                                if results
                                else [
                                    f"No reconciliation results exist for run "
                                    f"{reconciliation_id}."
                                ]
                            ),
                        },
                    }

                year = payload.get("year")
                month = payload.get("month")
                case_id = payload.get("case_id")
                audit_run_id = payload.get("audit_run_id")
                focus_transaction_id = payload.get("focus_transaction_id")

                if year is not None and month is not None:
                    transactions = load_month_transactions(db, int(year), int(month))
                    anomalies = monthly_anomalies(
                        db, int(year), int(month), transactions
                    )
                    return {
                        "key": "anomaly",
                        "summary": (
                            f"Matched {len(anomalies['findings'])} saved finding(s) "
                            f"to {int(year):04d}-{int(month):02d} transaction records."
                        ),
                        "output": {
                            "mode": "saved_findings",
                            "available": True,
                            "period": f"{int(year):04d}-{int(month):02d}",
                            **anomalies,
                        },
                    }

                if case_id is not None:
                    case = (
                        db.query(InvestigationCase)
                        .filter(InvestigationCase.id == case_id)
                        .first()
                    )
                    if case is None:
                        return {
                            "key": "anomaly",
                            "summary": f"Investigation case {case_id} was not found.",
                            "output": {
                                "mode": "investigation_case",
                                "available": False,
                                "findings": [],
                                "summary": {"finding_count": 0},
                                "notes": [
                                    f"Investigation case {case_id} does not exist."
                                ],
                            },
                        }
                    query = db.query(Finding)
                    if case.audit_run_id is not None:
                        query = query.filter(Finding.run_id == case.audit_run_id)
                    if case.transaction_id:
                        query = query.filter(
                            Finding.transaction_id == case.transaction_id
                        )
                    findings = query.order_by(Finding.id.asc()).all()
                    return {
                        "key": "anomaly",
                        "summary": (
                            f"Loaded {len(findings)} saved finding(s) for case "
                            f"{case.id}."
                        ),
                        "output": {
                            "mode": "investigation_case",
                            "available": True,
                            "case": {
                                "id": case.id,
                                "title": case.title,
                                "status": case.status,
                                "severity": case.severity,
                                "audit_run_id": case.audit_run_id,
                                "transaction_id": case.transaction_id,
                            },
                            "findings": [
                                {
                                    "id": item.id,
                                    "run_id": item.run_id,
                                    "transaction_id": item.transaction_id,
                                    "source_file": item.source_file,
                                    "finding_type": item.finding_type,
                                    "risk_level": item.risk_level,
                                    "amount": item.amount,
                                    "summary": item.summary,
                                    "evidence": item.evidence,
                                }
                                for item in findings
                            ],
                            "summary": {"finding_count": len(findings)},
                            "notes": [],
                        },
                    }

                if audit_run_id is not None or focus_transaction_id is not None:
                    query = db.query(Finding)
                    if audit_run_id is not None:
                        query = query.filter(Finding.run_id == audit_run_id)
                    if focus_transaction_id is not None:
                        query = query.filter(
                            Finding.transaction_id == focus_transaction_id
                        )
                    findings = query.order_by(Finding.id.asc()).all()
                    return {
                        "key": "anomaly",
                        "summary": (
                            f"Loaded {len(findings)} saved finding(s) for the "
                            "requested investigation scope."
                        ),
                        "output": {
                            "mode": "saved_findings",
                            "available": True,
                            "audit_run_id": audit_run_id,
                            "focus_transaction_id": focus_transaction_id,
                            "findings": [
                                {
                                    "id": item.id,
                                    "run_id": item.run_id,
                                    "transaction_id": item.transaction_id,
                                    "source_file": item.source_file,
                                    "finding_type": item.finding_type,
                                    "risk_level": item.risk_level,
                                    "amount": item.amount,
                                    "summary": item.summary,
                                    "evidence": item.evidence,
                                }
                                for item in findings
                            ],
                            "summary": {"finding_count": len(findings)},
                            "notes": [],
                        },
                    }

                return {
                    "key": "anomaly",
                    "summary": (
                        "No period, case, audit run, or transaction was supplied; "
                        "no anomaly data could be loaded."
                    ),
                    "output": {
                        "mode": "saved_findings",
                        "available": False,
                        "findings": [],
                        "summary": {"finding_count": 0},
                        "notes": [
                            "No anomaly scope was supplied to the investigation."
                        ],
                    },
                }
            finally:
                db.close()

        return self._execute(state, AGENT_ANOMALY, handler)

    def _analysis(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            payload = current.get("input") or {}
            task_type = payload.get("task_type")
            outputs = current.get("outputs") or {}
            db: Session = SessionLocal()
            try:
                if task_type == "reconciliation_review":
                    recon = outputs.get("reconciliation") or {}
                    if not recon.get("available"):
                        return {
                            "key": "analysis",
                            "summary": (
                                "No persisted reconciliation data was available for "
                                "deterministic analysis."
                            ),
                            "output": {
                                "source": "none",
                                "available": False,
                                "verified": False,
                                "reason": recon.get("reason")
                                or "Reconciliation data is unavailable.",
                                "summary": {},
                            },
                        }
                    summary = recon.get("summary") or {}
                    return {
                        "key": "analysis",
                        "summary": (
                            "Used persisted reconciliation totals; no LLM generated "
                            "any financial number."
                        ),
                        "output": {
                            "source": "persisted_reconciliation_run",
                            "available": True,
                            "verified": True,
                            "reconciliation_id": recon.get("reconciliation_id"),
                            "summary": {
                                "bank_total": summary.get("bank_total"),
                                "ledger_total": summary.get("ledger_total"),
                                "difference": summary.get("difference"),
                                "matched_total": summary.get("matched_total"),
                                "unmatched_total": summary.get("unmatched_total"),
                                "result_count": summary.get("result_count"),
                            },
                            "reconciliation_summary": summary,
                            "notes": [
                                "Reconciliation totals are deterministic outputs from "
                                "the persisted reconciliation engine.",
                                "A human reviewer must approve any reconciliation "
                                "adjustment.",
                            ],
                        },
                    }

                year = payload.get("year")
                month = payload.get("month")
                if year is None or month is None:
                    return {
                        "key": "analysis",
                        "summary": (
                            "No reporting period was supplied; deterministic financial "
                            "analysis could not run."
                        ),
                        "output": {
                            "source": "none",
                            "available": False,
                            "verified": False,
                            "reason": "A year and month are required for monthly analysis.",
                            "summary": {},
                        },
                    }
                analysis = monthly_analysis(db, int(year), int(month))
                return {
                    "key": "analysis",
                    "summary": (
                        f"Computed deterministic totals for {analysis['period']['label']} "
                        f"from {analysis['summary']['transaction_count']} persisted "
                        "transaction(s)."
                    ),
                    "output": {
                        "source": "deterministic_monthly_analysis",
                        "available": True,
                        "verified": True,
                        **analysis,
                    },
                }
            finally:
                db.close()

        return self._execute(state, AGENT_ANALYSIS, handler)

    def _evidence(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            payload = current.get("input") or {}
            task_type = payload.get("task_type")
            outputs = current.get("outputs") or {}
            items: list[dict[str, Any]] = []
            missing: list[str] = []
            retrieval: list[dict[str, Any]] = []
            sufficient = False
            db: Session = SessionLocal()
            try:
                if task_type == "reconciliation_review":
                    recon = outputs.get("reconciliation") or {}
                    if recon.get("available"):
                        results = (
                            db.query(ReconciliationResult)
                            .filter(
                                ReconciliationResult.run_id
                                == recon.get("reconciliation_id")
                            )
                            .order_by(ReconciliationResult.id.asc())
                            .limit(50)
                            .all()
                        )
                        items = [
                            _recon_result_evidence(item, "reconciliation_result")
                            for item in results
                        ]
                        sufficient = bool(results)
                        if not results:
                            missing.append(
                                f"No result rows exist for reconciliation run "
                                f"{recon.get('reconciliation_id')}."
                            )
                    else:
                        missing.append(
                            recon.get("reason")
                            or "No reconciliation evidence is available."
                        )
                    query = (
                        "reconciliation bank ledger unmatched amount mismatch "
                        "human review"
                    )
                    retrieval = retrieve_context(query)
                    for policy in retrieval:
                        items.append(
                            {
                                "kind": "policy",
                                "source_id": policy["source"],
                                "source_type": "audit_policy",
                                "excerpt": policy["text"],
                                "score": round(policy["score"], 3),
                            }
                        )
                    return {
                        "key": "evidence",
                        "summary": (
                            f"Collected {len(items)} evidence item(s) for the "
                            "reconciliation review."
                        ),
                        "output": {
                            "items": items,
                            "missing_evidence": missing,
                            "sufficient": sufficient,
                            "retrieval": retrieval,
                        },
                        "evidence": items,
                        "missing_evidence": missing,
                        "require_human_review": True,
                    }

                year = payload.get("year")
                month = payload.get("month")
                anomalies = outputs.get("anomaly") or {}
                findings = anomalies.get("findings") or []
                transactions: list[TransactionRecord] = []
                if year is not None and month is not None:
                    transactions = load_month_transactions(
                        db, int(year), int(month)
                    )

                txn_by_id = {
                    item.transaction_id: item
                    for item in transactions
                    if item.transaction_id
                }
                seen: set[tuple[Any, ...]] = set()
                for finding in findings:
                    transaction = txn_by_id.get(finding.get("transaction_id"))
                    if transaction is not None:
                        item = _transaction_evidence(transaction, "finding_source")
                        item["finding_id"] = finding.get("id")
                        item["finding_type"] = finding.get("finding_type")
                        item["excerpt"] = (
                            f"{item['excerpt']} | finding: "
                            f"{finding.get('summary')}"
                        )
                    else:
                        item = {
                            "kind": "finding",
                            "source_id": f"finding:{finding.get('id')}",
                            "source_type": "audit_finding",
                            "record_id": str(finding.get("id")),
                            "source_file": finding.get("source_file"),
                            "source_row": None,
                            "transaction_id": finding.get("transaction_id"),
                            "excerpt": finding.get("evidence"),
                            "finding_type": finding.get("finding_type"),
                        }
                    key = (
                        item.get("source_file"),
                        item.get("source_row"),
                        item.get("transaction_id"),
                        item.get("finding_id"),
                    )
                    if key not in seen:
                        seen.add(key)
                        items.append(item)

                if transactions:
                    ranked = sorted(
                        transactions,
                        key=lambda item: abs(
                            float(item.signed_amount or item.amount or 0)
                        ),
                        reverse=True,
                    )[:10]
                    for transaction in ranked:
                        item = _transaction_evidence(transaction)
                        key = (
                            item.get("source_file"),
                            item.get("source_row"),
                            item.get("transaction_id"),
                        )
                        if key not in seen:
                            seen.add(key)
                            items.append(item)

                query = (
                    f"monthly financial analysis {year}-{month} duplicate payment "
                    "high value transaction source evidence"
                )
                evidence_text = " ".join(
                    str(finding.get("evidence") or "") for finding in findings[:5]
                )
                retrieval = retrieve_context(query, evidence_text)
                for policy in retrieval:
                    items.append(
                        {
                            "kind": "policy",
                            "source_id": policy["source"],
                            "source_type": "audit_policy",
                            "excerpt": policy["text"],
                            "score": round(policy["score"], 3),
                        }
                    )

                if year is not None and month is not None and not transactions:
                    missing.append(
                        f"No transaction records exist for {int(year):04d}-"
                        f"{int(month):02d}."
                    )
                if not findings:
                    missing.append(
                        "No saved audit findings are linked to this period; "
                        "anomaly conclusions cannot be verified."
                    )
                if transactions and not any(
                    item.get("source_file") for item in items
                ):
                    missing.append(
                        "Transaction records exist but none include source-file "
                        "provenance."
                    )
                if not retrieval:
                    missing.append(
                        "No audit policy excerpt matched the evidence text."
                    )

                sufficient = bool(transactions) and bool(findings)
                return {
                    "key": "evidence",
                    "summary": (
                        f"Collected {len(items)} evidence item(s); "
                        f"{len(missing)} missing-evidence issue(s) recorded."
                    ),
                    "output": {
                        "items": items,
                        "missing_evidence": missing,
                        "sufficient": sufficient,
                        "retrieval": retrieval,
                        "source_count": len(items),
                    },
                    "evidence": items,
                    "missing_evidence": missing,
                    "require_human_review": True,
                }
            finally:
                db.close()

        return self._execute(state, AGENT_EVIDENCE, handler)

    def _synthesis(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            outputs = current.get("outputs") or {}
            analysis = outputs.get("analysis") or {}
            anomaly = outputs.get("anomaly") or {}
            evidence = current.get("evidence") or []
            missing = list(current.get("missing_evidence") or [])
            facts: list[str] = []
            hypotheses: list[str] = []
            limitations: list[str] = list(missing)
            recommendations: list[str] = []

            summary = analysis.get("summary") or {}
            if analysis.get("source") == "deterministic_monthly_analysis":
                period = analysis.get("period", {}).get("label", "the selected period")
                if analysis.get("has_transactions"):
                    facts.append(
                        f"{period}: total spending was {summary.get('total_spending')} "
                        f"across {summary.get('transaction_count')} transaction(s)."
                    )
                    facts.append(
                        f"{period}: total credits were {summary.get('total_credits')} "
                        f"and net cash flow was {summary.get('net_cash_flow')}."
                    )
                    if summary.get("uncategorized_count"):
                        facts.append(
                            f"{summary['uncategorized_count']} transaction(s) are "
                            "uncategorized."
                        )
                else:
                    limitations.append(
                        f"{period} contains no transaction records; totals are zero "
                        "because the dataset is empty, not because spending was zero "
                        "in a verified complete dataset."
                    )
            elif analysis.get("source") == "persisted_reconciliation_run":
                facts.append(
                    "Persisted reconciliation totals were loaded from the "
                    "deterministic reconciliation engine."
                )
                if summary.get("difference") not in (None, 0, 0.0):
                    facts.append(
                        f"Reconciliation difference is {summary.get('difference')}."
                    )
            else:
                limitations.append(
                    analysis.get("reason")
                    or "No deterministic financial analysis was available."
                )

            anomaly_summary = anomaly.get("summary") or {}
            finding_count = anomaly_summary.get("finding_count", 0)
            if finding_count:
                facts.append(
                    f"{finding_count} saved finding(s) were linked to the selected "
                    "scope."
                )
                by_type = anomaly_summary.get("by_type") or {}
                for finding_type, count in sorted(by_type.items()):
                    hypotheses.append(
                        f"{count} {finding_type} finding(s) may indicate a control "
                        "exception, a data-quality issue, or a legitimate transaction; "
                        "the cause is not established."
                    )
            else:
                limitations.append(
                    "No saved findings were linked to the selected scope; no anomaly "
                    "conclusion can be verified."
                )
            for note in anomaly.get("notes") or []:
                if note not in limitations:
                    limitations.append(note)
            for note in (analysis.get("data_quality") or {}).get("notes") or []:
                if note not in limitations:
                    limitations.append(note)

            source_files = sorted(
                {
                    item.get("source_file")
                    for item in evidence
                    if item.get("source_file")
                }
            )
            if evidence:
                facts.append(
                    f"{len(evidence)} evidence item(s) were collected from "
                    f"{len(source_files)} source file(s)."
                )
            else:
                limitations.append("No evidence records were retrieved.")

            if summary.get("uncategorized_count"):
                recommendations.append(
                    "Categorize uncategorized transactions and re-run the analysis "
                    "before relying on category totals."
                )
            recommendations.extend(
                [
                    "A human reviewer must verify source invoices, ledger entries, "
                    "approvals, and vendor records before any financial action.",
                    "Treat findings as investigation leads, not proof of fraud.",
                    "Do not approve reconciliation adjustments or close a case based "
                    "solely on this automated output.",
                ]
            )

            llm_meta: dict[str, Any] = {
                "configured": llm.get_api_key() is not None,
                "client_available": llm.client is not None,
                "used": False,
                "fallback": None,
                "model": llm.MODEL,
                "error_category": None,
                "attempts": 0,
            }
            narrative: str | None = None
            if llm.get_api_key() and llm.client is not None:
                evidence_text = "\n".join(
                    f"[{item.get('source_id')}] {item.get('excerpt')}"
                    for item in evidence[:20]
                )
                question = (
                    f"Summarize this {current.get('input', {}).get('task_type')} "
                    f"workflow for {analysis.get('period', {}).get('label', 'the selected scope')}. "
                    f"Verified facts: {facts}. Hypotheses: {hypotheses}. "
                    f"Limitations: {limitations}."
                )
                transient_errors = {
                    "rate_limit",
                    "timeout",
                    "network",
                    "provider_server",
                }
                for attempt in range(1, 3):
                    llm_meta["attempts"] = attempt
                    try:
                        llm_result = asyncio.run(
                            llm.explain_finding(question, evidence_text)
                        )
                        narrative = llm_result.get("answer")
                        llm_meta.update(
                            {
                                "used": not llm_result.get("fallback", False),
                                "fallback": bool(llm_result.get("fallback", False)),
                                "model": llm_result.get("model"),
                                "error_category": llm_result.get("error_category"),
                            }
                        )
                        if not llm_result.get("fallback", False) or (
                            llm_result.get("error_category") not in transient_errors
                        ):
                            break
                    except Exception as exc:  # noqa: BLE001
                        llm_meta["error_category"] = "unexpected"
                        llm_meta["error"] = str(exc)
                        break
                if llm_meta["fallback"]:
                    limitations.append(
                        "LLM narrative fell back after "
                        f"{llm_meta['attempts']} attempt(s); deterministic "
                        "results are unaffected."
                    )
            elif not llm.get_api_key():
                limitations.append(
                    "LLM narrative is unavailable because GROQ_API_KEY is not "
                    "configured. Deterministic results are unaffected."
                )
            elif llm.client is None:
                limitations.append(
                    "GROQ_API_KEY is configured but the Groq client is unavailable; "
                    "deterministic results are unaffected."
                )

            data_evidence = [
                item for item in evidence if item.get("kind") != "policy"
            ]
            human_review_required = bool(
                finding_count
                or missing
                or not analysis.get("available")
                or not data_evidence
            )
            conclusion_status = (
                "insufficient_evidence"
                if not data_evidence
                else "requires_human_review"
                if finding_count or human_review_required
                else "informational"
            )

            return {
                "key": "synthesis",
                "summary": (
                    "Separated verified facts, hypotheses, limitations and review "
                    f"actions; conclusion status: {conclusion_status}."
                ),
                "output": {
                    "facts": facts,
                    "hypotheses": hypotheses,
                    "limitations": limitations,
                    "recommendations": recommendations,
                    "narrative": narrative,
                    "llm": llm_meta,
                    "human_review_required": human_review_required,
                    "conclusion_status": conclusion_status,
                    "evidence_references": [
                        {
                            "source_id": item.get("source_id"),
                            "source_type": item.get("source_type"),
                            "source_file": item.get("source_file"),
                            "source_row": item.get("source_row"),
                            "transaction_id": item.get("transaction_id"),
                        }
                        for item in evidence
                    ],
                },
                "require_human_review": human_review_required,
            }

        return self._execute(state, AGENT_SYNTHESIS, handler)

    def _report(self, state: WorkflowState) -> WorkflowState:
        def handler(current: WorkflowState) -> dict[str, Any]:
            outputs = current.get("outputs") or {}
            analysis = outputs.get("analysis") or {}
            anomaly = outputs.get("anomaly") or {}
            synthesis = outputs.get("synthesis") or {}
            evidence = current.get("evidence") or []
            task_type = (current.get("input") or {}).get("task_type")

            report = {
                "task_type": task_type,
                "period": analysis.get("period"),
                "conclusion_status": synthesis.get("conclusion_status"),
                "verified_facts": synthesis.get("facts") or [],
                "hypotheses": synthesis.get("hypotheses") or [],
                "limitations": synthesis.get("limitations") or [],
                "recommended_review_actions": synthesis.get("recommendations") or [],
                "ai_narrative": synthesis.get("narrative"),
                "llm": synthesis.get("llm"),
                "financial_results": {
                    "source": analysis.get("source"),
                    "available": analysis.get("available"),
                    "verified": analysis.get("verified"),
                    "summary": analysis.get("summary"),
                    "category_totals": analysis.get("category_totals"),
                    "comparison": analysis.get("comparison"),
                    "data_quality": analysis.get("data_quality"),
                },
                "anomalies": anomaly.get("findings") or [],
                "anomaly_summary": anomaly.get("summary"),
                "evidence_references": synthesis.get("evidence_references") or [],
                "evidence_item_count": len(evidence),
                "missing_evidence": current.get("missing_evidence") or [],
                "disclaimer": (
                    "This report contains automated indicators for human review. "
                    "It does not prove fraud, resolve a case, or approve any "
                    "reconciliation adjustment."
                ),
            }

            if current.get("status") != "failed":
                if (
                    task_type in {"investigation", "reconciliation_review"}
                    and current.get("require_human_review")
                ):
                    current["status"] = "needs_human_review"
                else:
                    current["status"] = "completed"
            return {
                "key": "report",
                "summary": (
                    "Draft report assembled from verified structured outputs with "
                    "facts, hypotheses, limitations and review actions."
                ),
                "output": report,
            }

        return self._execute(state, AGENT_REPORT, handler)

    def _route_after_intake(self, state: WorkflowState):
        if state.get("status") == "failed":
            return END
        return "recon"

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("intake", self._intake)
        graph.add_node("recon", self._recon)
        graph.add_node("anomaly", self._anomaly)
        graph.add_node("analysis", self._analysis)
        graph.add_node("evidence", self._evidence)
        graph.add_node("synthesis", self._synthesis)
        graph.add_node("report", self._report)
        graph.add_edge(START, "intake")
        graph.add_conditional_edges(
            "intake", self._route_after_intake, {"recon": "recon", END: END}
        )
        graph.add_edge("recon", "anomaly")
        graph.add_edge("anomaly", "analysis")
        graph.add_edge("analysis", "evidence")
        graph.add_edge("evidence", "synthesis")
        graph.add_edge("synthesis", "report")
        graph.add_edge("report", END)
        return graph.compile()

    # ------------------------------------------------------------------
    # Execution / persistence
    # ------------------------------------------------------------------
    def run(self, input_data: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        started = _now()
        started_perf = time.perf_counter()
        state: WorkflowState = {
            "input": input_data,
            "timeline": [],
            "events": [],
            "status": "running",
            "errors": [],
            "outputs": {},
            "evidence": [],
            "missing_evidence": [],
            "require_human_review": False,
        }
        try:
            result = dict(self.graph.invoke(state))
        except Exception as exc:  # noqa: BLE001
            result = dict(state)
            result["status"] = "failed"
            result["errors"] = list(result.get("errors") or []) + [str(exc)]

        finished = _now()
        result["started_at"] = started.isoformat()
        result["finished_at"] = finished.isoformat()
        result["duration_ms"] = max(
            0, int(round((time.perf_counter() - started_perf) * 1000))
        )
        result.setdefault("timeline", [])
        result.setdefault("events", [])
        result.setdefault("errors", [])
        result.setdefault("evidence", [])
        result.setdefault("missing_evidence", [])

        if persist:
            self._persist(input_data, result)
        return result

    def _persist(self, input_data: dict[str, Any], result: dict[str, Any]) -> None:
        intake = (result.get("outputs") or {}).get("intake") or {}
        task_type = intake.get("task_type") or input_data.get("task_type")
        db: Session = SessionLocal()
        try:
            run = InvestigationRun(
                name=task_type or "investigation",
                task_type=task_type,
                status=result.get("status"),
                input_state=input_data,
                output_state=result,
                timeline=result.get("timeline"),
                events=result.get("events"),
                evidence=result.get("evidence"),
                errors=result.get("errors"),
                selected_year=intake.get("year", input_data.get("year")),
                selected_month=intake.get("month", input_data.get("month")),
                audit_run_id=intake.get(
                    "audit_run_id", input_data.get("audit_run_id")
                ),
                reconciliation_id=intake.get(
                    "reconciliation_id", input_data.get("reconciliation_id")
                ),
                case_id=intake.get("case_id", input_data.get("case_id")),
                started_at=_parse_iso(result.get("started_at")),
                finished_at=_parse_iso(result.get("finished_at")),
                duration_ms=result.get("duration_ms"),
            )
            db.add(run)
            db.flush()
            result["run_id"] = run.id
            run.output_state = result
            db.commit()
            db.refresh(run)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            result["persistence_error"] = f"{type(exc).__name__}: {exc}"
            result.setdefault("errors", []).append(
                f"Persistence failed: {type(exc).__name__}: {exc}"
            )
        finally:
            db.close()
