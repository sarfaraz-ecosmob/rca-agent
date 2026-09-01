"""
SQLAlchemy ORM models for AI RCA Agent.

Tables: containers, alerts, incidents, analysis, notifications, feedback, knowledge_base
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    Index,
    func,
)
from sqlalchemy.orm import relationship

from src.db.database import Base


class ContainerModel(Base):
    """Monitored Docker containers."""

    __tablename__ = "containers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    container_id = Column(String(128), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    image = Column(String(512), nullable=False)
    state = Column(String(32), default="running")
    status = Column(String(256), default="")
    log_driver = Column(String(64), default="json-file")
    labels = Column(JSON, default=dict)
    hostname = Column(String(256), default="")
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_monitored = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    alerts = relationship("AlertModel", back_populates="container", cascade="all, delete-orphan")
    analyses = relationship("AnalysisModel", back_populates="container", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_container_name", "name"),
        Index("idx_container_state", "state"),
    )


class AlertModel(Base):
    """Detected error alerts."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    container_id = Column(String(128), ForeignKey("containers.container_id"), nullable=False, index=True)
    container_name = Column(String(256), nullable=False)
    error_category = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False, default="medium")
    match_pattern = Column(String(256), nullable=False)
    log_snippet = Column(Text, nullable=False)
    raw_log_line = Column(Text, default="")
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    status = Column(String(16), default="new")
    hostname = Column(String(256), default="")
    is_deduplicated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    container = relationship("ContainerModel", back_populates="alerts")
    analysis = relationship("AnalysisModel", uselist=False, back_populates="alert")

    __table_args__ = (
        Index("idx_alert_severity", "severity"),
        Index("idx_alert_status", "status"),
        Index("idx_alert_timestamp", "timestamp"),
        Index("idx_alert_category", "error_category"),
    )


class AnalysisModel(Base):
    """Root cause analysis results."""

    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    container_id = Column(String(128), ForeignKey("containers.container_id"), nullable=False, index=True)
    container_name = Column(String(256), nullable=False)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=True, index=True)
    error_type = Column(String(256), nullable=False)
    severity = Column(String(16), nullable=False)
    root_cause = Column(JSON, nullable=False)
    explanation = Column(Text, nullable=False)
    solutions = Column(JSON, nullable=False)
    confidence = Column(Float, default=0.0)
    log_snippet = Column(Text, default="")
    possible_impact = Column(Text, default="")
    references = Column(JSON, default=list)
    ai_provider = Column(String(64), default="")
    raw_ai_response = Column(JSON, default=dict)
    analyzed_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Incident workflow status: new / acknowledged / resolved / dismissed
    status = Column(String(16), default="new", server_default="new")

    # Relationships
    container = relationship("ContainerModel", back_populates="analyses")
    alert = relationship("AlertModel", back_populates="analysis")

    __table_args__ = (
        Index("idx_analysis_severity", "severity"),
        Index("idx_analysis_confidence", "confidence"),
        Index("idx_analysis_analyzed_at", "analyzed_at"),
    )


class NotificationModel(Base):
    """Notification records."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=True, index=True)
    channel = Column(String(32), nullable=False)
    recipient = Column(String(512), nullable=False)
    subject = Column(String(512), default="")
    message = Column(Text, default="")
    status = Column(String(16), default="sent")
    error_message = Column(Text, default="")
    sent_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_notification_channel", "channel"),
        Index("idx_notification_status", "status"),
    )


class FeedbackModel(Base):
    """User feedback on RCA analyses."""

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_id = Column(Integer, ForeignKey("analysis.id"), nullable=False, index=True)
    was_helpful = Column(Boolean, default=False)
    correct_root_cause = Column(Boolean, default=False)
    correct_solution = Column(Boolean, default=False)
    comments = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SettingModel(Base):
    """Runtime settings configurable from the dashboard."""

    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeBaseModel(Base):
    """Knowledge base for RAG and historical incidents."""

    __tablename__ = "knowledge_base"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(512), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(128), default="manual")
    tags = Column(JSON, default=list)
    category = Column(String(64), default="general")
    embedding = Column(JSON, nullable=True)
    extra_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_kb_category", "category"),
        Index("idx_kb_source", "source"),
    )
