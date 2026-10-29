from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

class AuditRun(Base):
    __tablename__ = "audit_runs"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    source_files = Column(Text, nullable=True)
    row_count = Column(Integer, default=0)
    finding_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    findings = relationship("Finding", back_populates="run", cascade="all, delete-orphan")

class Finding(Base):
    __tablename__ = "findings"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("audit_runs.id"), nullable=False)
    transaction_id = Column(String(100), nullable=True)
    source_file = Column(String(255), nullable=True)
    finding_type = Column(String(80), nullable=False)
    risk_level = Column(String(20), nullable=False)
    amount = Column(Float, nullable=True)
    summary = Column(String(500), nullable=False)
    evidence = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    run = relationship("AuditRun", back_populates="findings")


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id = Column(Integer, primary_key=True, index=True)
    bank_filename = Column(String(255), nullable=False)
    ledger_filename = Column(String(255), nullable=False)
    date_tolerance_days = Column(Integer, default=3)
    bank_total = Column(Float, default=0)
    ledger_total = Column(Float, default=0)
    matched_total = Column(Float, default=0)
    bank_unmatched_total = Column(Float, default=0)
    ledger_unmatched_total = Column(Float, default=0)
    unmatched_total = Column(Float, default=0)
    difference = Column(Float, default=0)
    result_count = Column(Integer, default=0)
    matched_count = Column(Integer, default=0)
    bank_only_count = Column(Integer, default=0)
    ledger_only_count = Column(Integer, default=0)
    amount_mismatch_count = Column(Integer, default=0)
    date_mismatch_count = Column(Integer, default=0)
    ambiguous_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    results = relationship(
        "ReconciliationResult",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class ReconciliationResult(Base):
    __tablename__ = "reconciliation_results"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("reconciliation_runs.id"), nullable=False)
    status = Column(String(30), nullable=False)
    bank_date = Column(String(10), nullable=True)
    bank_amount = Column(Float, nullable=True)
    bank_direction = Column(String(20), nullable=True)
    bank_reference = Column(String(255), nullable=True)
    bank_description = Column(Text, nullable=True)
    bank_source_file = Column(String(255), nullable=True)
    bank_source_row = Column(Integer, nullable=True)
    ledger_date = Column(String(10), nullable=True)
    ledger_amount = Column(Float, nullable=True)
    ledger_direction = Column(String(20), nullable=True)
    ledger_reference = Column(String(255), nullable=True)
    ledger_description = Column(Text, nullable=True)
    ledger_source_file = Column(String(255), nullable=True)
    ledger_source_row = Column(Integer, nullable=True)
    amount_difference = Column(Float, nullable=True)
    date_difference_days = Column(Integer, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    run = relationship("ReconciliationRun", back_populates="results")
