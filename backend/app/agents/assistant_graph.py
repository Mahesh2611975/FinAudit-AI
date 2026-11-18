"""Selective multi-agent investigation workflow built with LangGraph.

Only the agents selected by the Orchestrator are executed for a question.
Specialist agents read exact values from SQL and the vector store; the
Investigation Summary agent is the only node that calls the LLM, and it may
only narrate the evidence supplied to it.

Required roles
--------------
* Financial Analyst Agent      - inspect transaction records and activity
* Risk Investigation Agent     - explain rules, scores and severity
* Reconciliation Agent          - explain matched/unmatched records
* Evidence Retrieval Agent      - retrieve grounded RAG document evidence
* Investigation Summary Agent   - combine evidence, citations and answer

The Orchestrator classifies the question and selects a subset of these roles.
Each role is executed independently and reports its own status; a failure in
one role is recorded without hiding it and does not fabricate evidence.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import llm, rag
from ..database import SessionLocal
from ..models import ReconciliationResult, ReconciliationRun, RiskAlert, TransactionRecord
from .schemas import AgentOutput

AGENT_ORCHESTRATOR = "Orchestrator Agent"
AGENT_FINANCIAL = "Financial Analyst Agent"
AGENT_RECONCILIATION = "Reconciliation Agent"
AGENT_RISK = "Risk Investigation Agent"
AGENT_EVIDENCE = "Evidence Retrieval Agent"
AGENT_SUMMARY = "Investigation Summary Agent"

# Backward-compatible aliases used by earlier code and tests.
AGENT_TRANSACTION = AGENT_FINANCIAL
AGENT_REPORTING = AGENT_SUMMARY

AGENT_NODE = {
    AGENT_FINANCIAL: "transaction",
    AGENT_RECONCILIATION: "reconciliation",
    AGENT_RISK: "risk",
    AGENT_EVIDENCE: "evidence",
    AGENT_SUMMARY: "reporting",
}

# Route keys exposed to clients.  "transaction"/"reporting" are retained so
# existing integrations keep working while the five required roles are exposed
# through ``agent_roles`` and the timeline.
NODE_TO_ROLE = {
    "transaction": "Financial Analyst Agent",
    "reconciliation": "Reconciliation Agent",
    "risk": "Risk Investigation Agent",
    "evidence": "Evidence Retrieval Agent",
    "reporting": "Investigation Summary Agent",
}

REQUIRED_AGENT_ROLES = tuple(NODE_TO_ROLE.values())

INTENTS = {
    "transaction": {
        "keywords": ("transaction", "payment", "duplicate", "vendor", "spend", "amount", "debit", "credit", "txn"),
        "agents": [AGENT_TRANSACTION],
    },
    "reconciliation": {
        "keywords": ("reconcil", "unmatched", "bank", "ledger", "match", "difference", "missing transaction"),
        "agents": [AGENT_RECONCILIATION],
    },
    "risk": {
        "keywords": ("risk", "alert", "score", "severity", "flag", "fraud", "anomal", "why was"),
        "agents": [AGENT_RISK],
    },
    "evidence": {
        "keywords": ("evidence", "document", "citation", "source", "supporting", "proof", "invoice", "contract", "rag"),
        "agents": [AGENT_EVIDENCE],
    },
    "reporting": {
        "keywords": ("report", "summary", "summarize", "investigation", "overview", "this month", "generate investigation report"),
        "agents": [AGENT_FINANCIAL, AGENT_RECONCILIATION, AGENT_RISK, AGENT_EVIDENCE],
    },
}

_STOP_WORDS = {
    "the", "and", "for", "with", "what", "why", "was", "were", "this", "that",
    "from", "have", "has", "how", "are", "explain", "find", "please", "about",
    "my", "me", "a", "an", "of", "in", "on", "to", "is", "it", "do", "did",
}


class AssistantState(TypedDict, total=False):
    question: str
    intent: str
    route: list[str]
    route_reason: str
    sql_evidence: list[dict[str, Any]]
    doc_evidence: list[dict[str, Any]]
    agent_outputs: dict[str, Any]
    answer: str | None
    citations: list[dict[str, Any]]
    sufficiency: str
    errors: list[str]
    timeline: list[dict[str, Any]]
    agent_failures: list[dict[str, Any]]
    agent_roles: list[str]
    llm: dict[str, Any]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _validate(output: dict[str, Any]) -> dict[str, Any]:
    """Validate specialist output; keep errors visible instead of hiding them."""
    try:
        return AgentOutput.model_validate(output).model_dump()
    except ValidationError as exc:
        return AgentOutput(
            agent_name=output.get("agent_name", "unknown"),
            status="failed",
            facts=[],
            hypotheses=[],
            missing_evidence=[f"Invalid agent output: {exc.errors()[0]['msg']}"],
            recommendations=[],
            evidence=[],
            data={},
        ).model_dump()


def _record(state: AssistantState, name: str, started: int, summary: str) -> None:
    state.setdefault("timeline", []).append(
        {
            "agent_name": name,
            "status": "completed",
            "started_at": started,
            "finished_at": _now_ms(),
            "duration_ms": max(0, _now_ms() - started),
            "summary": summary,
        }
    )
    state.setdefault("agent_roles", [])
    if name not in state["agent_roles"]:
        state["agent_roles"].append(name)


def _record_failure(state: AssistantState, name: str, started: int, error: Exception) -> None:
    """Record an individual agent failure without hiding it or stopping peers."""
    message = f"{type(error).__name__}: {error}"
    state.setdefault("timeline", []).append(
        {
            "agent_name": name,
            "status": "failed",
            "started_at": started,
            "finished_at": _now_ms(),
            "duration_ms": max(0, _now_ms() - started),
            "summary": f"Agent failed: {message}",
            "error": message,
        }
    )
    state.setdefault("agent_failures", []).append({"agent_name": name, "error": message})
    state.setdefault("errors", []).append(f"{name}: {message}")
    state.setdefault("agent_roles", [])
    if name not in state["agent_roles"]:
        state["agent_roles"].append(name)


def _guarded(agent_name: str, node_key: str):
    """Wrap a graph node so a single agent error is recorded, not propagated."""

    def decorator(fn):
        def wrapper(self, state: AssistantState) -> AssistantState:
            started = _now_ms()
            try:
                return fn(self, state)
            except Exception as exc:  # noqa: BLE001 - failure must be visible
                _record_failure(state, agent_name, started, exc)
                state.setdefault("agent_outputs", {})[node_key] = _validate(
                    {
                        "agent_name": agent_name,
                        "status": "failed",
                        "facts": [],
                        "hypotheses": [],
                        "missing_evidence": [f"Agent failed: {exc}"],
                        "recommendations": [],
                        "evidence": [],
                        "data": {},
                    }
                )
                if node_key == "reporting":
                    # Never return an empty answer when the summary agent fails.
                    state["answer"] = (
                        "The investigation summary could not be produced because a "
                        f"required evidence source failed ({exc}). The failure is recorded "
                        "in the agent trace; no evidence was invented."
                    )
                    state["sufficiency"] = "insufficient"
                    state.setdefault("citations", [])
                return state

        return wrapper

    return decorator


def _question_tokens(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]{2,}", question or "")
    return [word for word in words if word.lower() not in _STOP_WORDS]


def _evidence_item(record: TransactionRecord) -> dict[str, Any]:
    return {
        "source_id": (
            f"transaction:{record.transaction_id}" if record.transaction_id else f"transaction-record:{record.id}"
        ),
        "source_type": "transaction_record",
        "record_id": str(record.id),
        "source_file": record.source_file,
        "source_row": record.source_row,
        "transaction_id": record.transaction_id,
        "text": (
            f"{record.date or 'unknown date'} | {record.direction or 'unknown'} | "
            f"{record.amount} | {record.vendor or ''} {record.description or ''}"
        ).strip(),
    }


class AssistantGraph:
    def __init__(self) -> None:
        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _orchestrator(self, state: AssistantState) -> AssistantState:
        started = _now_ms()
        question = (state.get("question") or "").lower()
        scores: dict[str, int] = {}
        for intent, spec in INTENTS.items():
            scores[intent] = sum(2 if word in {"risk", "alert", "severity", "reconcil", "duplicate", "fraud"} else 1
                                 for word in spec["keywords"] if word in question)
        # Deterministic priority for ambiguous multi-keyword questions.
        if any(term in question for term in (
            "evidence supports", "supporting evidence", "document citation",
            "rag document", "which documents", "source documents",
        )):
            intent = "evidence"
        elif any(term in question for term in ("highest-risk", "risk alert", "risk score", "why was", "flagged", "alert")):
            intent = "risk"
        elif any(term in question for term in ("reconcil", "unmatched", "bank", "ledger", "possible match")):
            intent = "reconciliation"
        elif any(term in question for term in ("report", "summar", "investigation", "this month")):
            intent = "reporting"
        elif any(term in question for term in ("duplicate", "transaction", "payment", "vendor", "spend")):
            intent = "transaction"
        else:
            intent = max(scores, key=lambda key: scores[key]) if scores else "reporting"
            if scores.get(intent, 0) == 0:
                intent = "reporting"
        agents = [AGENT_NODE[name] for name in INTENTS[intent]["agents"]]
        if not agents:
            agents = ["transaction"]
        state["intent"] = intent
        state["route"] = agents + ["reporting"]
        state["route_reason"] = (
            f"Intent '{intent}' matched {scores[intent]} keyword(s); selected "
            f"{len(agents)} specialist agent(s) plus reporting."
        )
        _record(state, AGENT_ORCHESTRATOR, started, state["route_reason"])
        return state

    @_guarded(AGENT_FINANCIAL, "transaction")
    def _transaction(self, state: AssistantState) -> AssistantState:
        started = _now_ms()
        question = state.get("question") or ""
        db: Session = SessionLocal()
        try:
            tokens = _question_tokens(question)
            query = db.query(TransactionRecord)
            filters = []
            for token in tokens:
                like = f"%{token}%"
                filters.extend(
                    [
                        TransactionRecord.transaction_id.ilike(like),
                        TransactionRecord.vendor.ilike(like),
                        TransactionRecord.description.ilike(like),
                        TransactionRecord.reference.ilike(like),
                        TransactionRecord.source_file.ilike(like),
                    ]
                )
            records = (
                query.filter(or_(*filters)).order_by(TransactionRecord.id.desc()).limit(10).all()
                if filters
                else []
            )
            facts = [
                f"[{item.transaction_id or 'TXN-' + str(item.id)}] {item.date or 'unknown date'} "
                f"{item.direction or 'unknown'} {abs(float(item.amount or item.signed_amount or 0)):.2f} "
                f"{item.vendor or item.description or ''} (source: {item.source_file or 'unknown'} row {item.source_row or '?'})"
                for item in records
            ]
            evidence = [_evidence_item(item) for item in records]
            data = {
                "records": [
                    {
                        "id": item.id,
                        "transaction_id": item.transaction_id,
                        "date": item.date,
                        "amount": item.amount,
                        "direction": item.direction,
                        "vendor": item.vendor,
                        "description": item.description,
                        "source_file": item.source_file,
                        "source_row": item.source_row,
                    }
                    for item in records
                ]
            }
            output = _validate(
                {
                    "agent_name": AGENT_TRANSACTION,
                    "status": "completed",
                    "facts": facts or ["No transaction matched the question tokens."],
                    "hypotheses": [
                        "Repeated vendor/amount/date combinations may indicate duplicate entries "
                        "and require manual verification."
                    ] if records else [],
                    "missing_evidence": [] if records else ["No matching transaction record was found."],
                    "recommendations": [
                        "Verify the original bank statement row and the ledger entry before drawing a conclusion."
                    ],
                    "evidence": [
                        {
                            "source_file": item.get("source_file"),
                            "source_row": item.get("source_row"),
                            "transaction_id": item.get("transaction_id"),
                            "excerpt": item.get("text"),
                            "record_id": item.get("record_id"),
                        }
                        for item in evidence
                    ],
                    "data": data,
                }
            )
        finally:
            db.close()
        state.setdefault("sql_evidence", []).extend(evidence)
        state.setdefault("agent_outputs", {})[AGENT_TRANSACTION] = output
        _record(state, AGENT_TRANSACTION, started, f"Loaded {len(output['facts'])} transaction fact(s).")
        return state

    @_guarded(AGENT_RECONCILIATION, "reconciliation")
    def _reconciliation(self, state: AssistantState) -> AssistantState:
        started = _now_ms()
        db: Session = SessionLocal()
        evidence: list[dict[str, Any]] = []
        try:
            run = db.query(ReconciliationRun).order_by(ReconciliationRun.id.desc()).first()
            if run is None:
                facts = ["No reconciliation run is stored yet."]
                data: dict[str, Any] = {"available": False}
                missing = ["A bank/ledger reconciliation run is required."]
            else:
                exceptions = (
                    db.query(ReconciliationResult)
                    .filter(
                        ReconciliationResult.run_id == run.id,
                        ReconciliationResult.status.in_(
                            ["bank_only", "ledger_only", "amount_mismatch", "date_mismatch", "ambiguous"]
                        ),
                    )
                    .order_by(ReconciliationResult.id)
                    .limit(10)
                    .all()
                )
                facts = [
                    f"Reconciliation #{run.id}: matched={run.matched_count}, bank_only={run.bank_only_count}, "
                    f"ledger_only={run.ledger_only_count}, amount_mismatch={run.amount_mismatch_count}, "
                    f"date_mismatch={run.date_mismatch_count}, ambiguous={run.ambiguous_count}, "
                    f"difference={run.difference:.2f}."
                ]
                for item in exceptions:
                    excerpt = (
                        f"status={item.status} bank={item.bank_date} {item.bank_amount} "
                        f"{item.bank_description or ''} | ledger={item.ledger_date} {item.ledger_amount} "
                        f"{item.ledger_description or ''} | amount_difference={item.amount_difference} "
                        f"date_difference_days={item.date_difference_days}"
                    )
                    evidence.append(
                        {
                            "source_id": f"reconciliation-result:{item.id}",
                            "source_type": "reconciliation_result",
                            "record_id": str(item.id),
                            "source_file": item.bank_source_file or item.ledger_source_file,
                            "source_row": item.bank_source_row or item.ledger_source_row,
                            "transaction_id": item.bank_reference or item.ledger_reference,
                            "text": excerpt,
                        }
                    )
                    facts.append(excerpt)
                data = {
                    "available": True,
                    "reconciliation_id": run.id,
                    "bank_file": run.bank_filename,
                    "ledger_file": run.ledger_filename,
                    "summary": {
                        "matched": run.matched_count,
                        "bank_only": run.bank_only_count,
                        "ledger_only": run.ledger_only_count,
                        "amount_mismatch": run.amount_mismatch_count,
                        "date_mismatch": run.date_mismatch_count,
                        "ambiguous": run.ambiguous_count,
                        "difference": run.difference,
                    },
                    "exceptions": [
                        {"id": item.id, "status": item.status, "note": item.note}
                        for item in exceptions
                    ],
                }
                missing = ["Human confirmation is required before accepting any possible/ambiguous match."]
        finally:
            db.close()
        output = _validate(
            {
                "agent_name": AGENT_RECONCILIATION,
                "status": "completed",
                "facts": facts,
                "hypotheses": [
                    "Timing differences and bank fees can explain small date/amount differences "
                    "but must be evidenced."
                ],
                "missing_evidence": missing,
                "recommendations": [
                    "Review the source rows for each exception and record a reviewer decision."
                ],
                "evidence": [
                    {
                        "source_file": item.get("source_file"),
                        "source_row": item.get("source_row"),
                        "transaction_id": item.get("transaction_id"),
                        "excerpt": item.get("text"),
                        "record_id": item.get("record_id"),
                    }
                    for item in evidence
                ],
                "data": data,
            }
        )
        state.setdefault("sql_evidence", []).extend(evidence)
        state.setdefault("agent_outputs", {})[AGENT_RECONCILIATION] = output
        _record(state, AGENT_RECONCILIATION, started, f"Loaded reconciliation evidence ({len(evidence)} exception rows).")
        return state

    @_guarded(AGENT_RISK, "risk")
    def _risk(self, state: AssistantState) -> AssistantState:
        started = _now_ms()
        db: Session = SessionLocal()
        evidence: list[dict[str, Any]] = []
        try:
            query = db.query(RiskAlert)
            question = (state.get("question") or "").lower()
            tokens = _question_tokens(question)
            filters = []
            for token in tokens:
                like = f"%{token}%"
                filters.extend(
                    [
                        RiskAlert.alert_code.ilike(like),
                        RiskAlert.transaction_id.ilike(like),
                        RiskAlert.risk_category.ilike(like),
                        RiskAlert.vendor_name.ilike(like),
                    ]
                )
            alerts = (
                query.filter(or_(*filters)).order_by(RiskAlert.risk_score.desc()).limit(10).all()
                if filters
                else query.order_by(RiskAlert.risk_score.desc()).limit(10).all()
            )
            facts: list[str] = []
            for alert in alerts:
                rule = alert.evidence.get("rule") if isinstance(alert.evidence, dict) else None
                source_file = alert.evidence.get("source_file") if isinstance(alert.evidence, dict) else None
                fact = (
                    f"[{alert.alert_code}] category={alert.risk_category} severity={alert.severity} "
                    f"score={alert.risk_score} amount={alert.amount_value} transaction={alert.transaction_id} "
                    f"status={alert.review_status}. Reason: {alert.explanation}"
                )
                facts.append(fact)
                evidence.append(
                    {
                        "source_id": f"risk-alert:{alert.alert_code}",
                        "source_type": "risk_alert",
                        "record_id": str(alert.id),
                        "source_file": source_file,
                        "source_row": None,
                        "transaction_id": alert.transaction_id,
                        "text": fact,
                        "rule": rule,
                    }
                )
            data = {
                "alerts": [
                    {
                        "alert_code": alert.alert_code,
                        "risk_category": alert.risk_category,
                        "severity": alert.severity,
                        "risk_score": alert.risk_score,
                        "amount": alert.amount_value,
                        "transaction_id": alert.transaction_id,
                        "review_status": alert.review_status,
                        "rule": alert.evidence.get("rule") if isinstance(alert.evidence, dict) else None,
                        "source_file": alert.evidence.get("source_file") if isinstance(alert.evidence, dict) else None,
                    }
                    for alert in alerts
                ]
            }
        finally:
            db.close()
        output = _validate(
            {
                "agent_name": AGENT_RISK,
                "status": "completed",
                "facts": facts or ["No risk alert matched the question."],
                "hypotheses": [
                    "A deterministic score is a review indicator; it does not establish fraud "
                    "without verified source documents."
                ],
                "missing_evidence": [] if alerts else ["No matching risk alert was found."],
                "recommendations": [
                    "Open the alert, inspect the rule and source file, then record a reviewer decision."
                ],
                "evidence": [
                    {
                        "source_file": item.get("source_file"),
                        "source_row": item.get("source_row"),
                        "transaction_id": item.get("transaction_id"),
                        "excerpt": item.get("text"),
                        "record_id": item.get("record_id"),
                    }
                    for item in evidence
                ],
                "data": data,
            }
        )
        state.setdefault("sql_evidence", []).extend(evidence)
        state.setdefault("agent_outputs", {})[AGENT_RISK] = output
        _record(state, AGENT_RISK, started, f"Loaded {len(alerts)} alert(s) with rules and scores.")
        return state

    @_guarded(AGENT_EVIDENCE, "evidence")
    def _evidence(self, state: AssistantState) -> AssistantState:
        """Retrieve grounded document evidence through RAG only.

        This role never invents evidence: every fact is copied from an actually
        retrieved chunk and carries a filename/chunk source reference.
        """
        started = _now_ms()
        question = state.get("question") or ""
        chunks = rag.search(question, top_k=6)
        state["doc_evidence"] = list(chunks)
        facts = [
            f"[{item.get('source')}] {item.get('source_filename') or 'unknown file'} "
            f"chunk={item.get('chunk_id', '?')} page={item.get('page_number', '?')}: "
            f"{str(item.get('text', ''))[:220]}"
            for item in chunks
        ]
        evidence = [
            {
                "source_file": item.get("source_filename"),
                "source_row": None,
                "transaction_id": item.get("transaction_id"),
                "excerpt": item.get("text"),
                "source_id": item.get("source"),
                "chunk_id": item.get("chunk_id"),
                "page_number": item.get("page_number"),
            }
            for item in chunks
        ]
        output = _validate(
            {
                "agent_name": AGENT_EVIDENCE,
                "status": "completed",
                "facts": facts or ["No supporting document chunk was retrieved."],
                "hypotheses": [],
                "missing_evidence": [] if chunks else [
                    "No indexed document supports this question; ingest source documents first."
                ],
                "recommendations": [
                    "Verify each cited chunk against the original document before relying on it."
                ] if chunks else [],
                "evidence": evidence,
                "data": {"chunk_count": len(chunks)},
            }
        )
        state.setdefault("agent_outputs", {})[AGENT_EVIDENCE] = output
        _record(state, AGENT_EVIDENCE, started, f"Retrieved {len(chunks)} document chunk(s).")
        return state

    @_guarded(AGENT_SUMMARY, "reporting")
    def _reporting(self, state: AssistantState) -> AssistantState:
        started = _now_ms()
        question = state.get("question") or ""
        outputs = state.get("agent_outputs") or {}
        facts: list[str] = []
        for output in outputs.values():
            facts.extend(output.get("facts") or [])
        sql_evidence = state.get("sql_evidence") or []
        # Prefer evidence gathered by the dedicated Evidence Retrieval Agent;
        # fall back to a direct retrieval so questions that did not select that
        # role still receive grounded document citations.
        doc_evidence = state.get("doc_evidence") or []
        if not doc_evidence:
            doc_evidence = rag.search(question, top_k=5)
        state["doc_evidence"] = doc_evidence

        evidence_blob = "\n".join(f"[{item.get('source_id')}] {item.get('text')}" for item in sql_evidence[:15])
        prompt_contexts = [
            {"source": item["source"], "text": item["text"], "score": item.get("score", 0)}
            for item in doc_evidence
        ]
        prompt, context_text = llm.build_prompt(question, evidence_blob, prompt_contexts)

        narrative = "Evidence summary (Ollama unavailable): " + (" ".join(facts[:5]) if facts else "No stored evidence found.")
        llm_meta: dict[str, Any] = {"backend": "ollama", "fallback": True, "model": llm.get_model()}
        if llm.is_available():
            answer = llm.generate(
                prompt,
                system="You are a cautious financial audit assistant. Ground every statement in supplied evidence.",
                max_tokens=600,
            )
            if answer:
                narrative = answer
                llm_meta = {"backend": "ollama", "fallback": False, "model": llm.get_model()}

        citations: list[dict[str, Any]] = []
        for item in sql_evidence[:12]:
            citations.append(
                {
                    "source": item.get("source_id"),
                    "type": item.get("source_type"),
                    "source_file": item.get("source_file"),
                    "source_row": item.get("source_row"),
                    "transaction_id": item.get("transaction_id"),
                    "excerpt": item.get("text"),
                }
            )
        for item in doc_evidence[:6]:
            citations.append(
                {
                    "source": item["source"],
                    "type": "document_chunk",
                    "source_file": item.get("source_filename"),
                    "page_number": item.get("page_number"),
                    "transaction_id": item.get("transaction_id"),
                    "excerpt": item.get("text"),
                    "score": round(float(item.get("score", 0)), 3),
                }
            )

        if sql_evidence and doc_evidence:
            sufficiency = "sufficient"
        elif sql_evidence or doc_evidence:
            sufficiency = "partial"
        else:
            sufficiency = "insufficient"
        if sufficiency == "insufficient":
            narrative = (
                "There is not enough stored evidence to answer reliably. "
                "Import transactions, run reconciliation/risk analysis, or ingest a document first."
            )

        output = _validate(
            {
                "agent_name": AGENT_REPORTING,
                "status": "completed",
                "facts": facts[:20],
                "hypotheses": [
                    "Possible explanations require human verification and are not conclusions."
                ],
                "missing_evidence": [
                    output.get("missing_evidence", []) for output in outputs.values()
                ][0] if outputs else ["No specialist evidence was loaded."],
                "recommendations": [
                    "Verify cited source rows and record a human review decision before acting."
                ],
                "evidence": citations,
                "data": {"context_text": context_text[:2000], "sufficiency": sufficiency},
            }
        )
        state["agent_outputs"][AGENT_REPORTING] = output
        state["answer"] = narrative
        state["citations"] = citations
        state["sufficiency"] = sufficiency
        state["llm"] = llm_meta
        _record(state, AGENT_REPORTING, started, f"Combined {len(facts)} fact(s) with {len(citations)} citation(s).")
        return state

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    @staticmethod
    def _first_agent(state: AssistantState) -> str:
        route = state.get("route") or []
        return route[0] if route else "reporting"

    @staticmethod
    def _next_agent(current: str):
        def chooser(state: AssistantState) -> str:
            route = state.get("route") or []
            if current in route:
                index = route.index(current)
                if index + 1 < len(route):
                    return route[index + 1]
            return "reporting"

        return chooser

    def _build_graph(self):
        graph = StateGraph(AssistantState)
        graph.add_node("orchestrator", self._orchestrator)
        graph.add_node("transaction", self._transaction)
        graph.add_node("reconciliation", self._reconciliation)
        graph.add_node("risk", self._risk)
        graph.add_node("evidence", self._evidence)
        graph.add_node("reporting", self._reporting)

        routes = {
            "transaction": "transaction",
            "reconciliation": "reconciliation",
            "risk": "risk",
            "evidence": "evidence",
            "reporting": "reporting",
        }
        graph.add_edge(START, "orchestrator")
        graph.add_conditional_edges("orchestrator", self._first_agent, routes)
        for node in ("transaction", "reconciliation", "risk", "evidence"):
            graph.add_conditional_edges(node, self._next_agent(node), routes)
        graph.add_edge("reporting", END)
        return graph.compile()

    def run(self, question: str) -> dict[str, Any]:
        initial: AssistantState = {
            "question": question,
            "route": [],
            "sql_evidence": [],
            "doc_evidence": [],
            "agent_outputs": {},
            "citations": [],
            "errors": [],
            "timeline": [],
            "agent_failures": [],
            "agent_roles": [],
        }
        result = dict(self.graph.invoke(initial))
        result.setdefault("answer", "")
        result.setdefault("sufficiency", "insufficient")
        result.setdefault("agent_failures", [])
        result.setdefault("agent_roles", [])
        # The orchestrator is a coordinator, not one of the five specialist roles.
        result["agent_roles"] = [
            role for role in result["agent_roles"] if role != AGENT_ORCHESTRATOR
        ]
        result["required_roles"] = list(REQUIRED_AGENT_ROLES)
        return result


_assistant_graph: AssistantGraph | None = None


def get_assistant_graph() -> AssistantGraph:
    global _assistant_graph
    if _assistant_graph is None:
        _assistant_graph = AssistantGraph()
    return _assistant_graph
