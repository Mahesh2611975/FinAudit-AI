from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

class AuditRun(Base):
    __tablename__ = "audit_runs"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    row_count = Column(Integer, default=0)
    finding_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    findings = relationship("Finding", back_populates="run", cascade="all, delete-orphan")

class Finding(Base):
    __tablename__ = "findings"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("audit_runs.id"), nullable=False)
    transaction_id = Column(String(100), nullable=True)
    finding_type = Column(String(80), nullable=False)
    risk_level = Column(String(20), nullable=False)
    amount = Column(Float, nullable=True)
    summary = Column(String(500), nullable=False)
    evidence = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    run = relationship("AuditRun", back_populates="findings")
