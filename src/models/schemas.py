"""
Pydantic schemas for the AI RCA Agent.

Defines data models for alerts, incidents, analysis results, and notifications.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --- Enums ---


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ErrorCategory(str, Enum):
    APPLICATION = "application"
    INFRASTRUCTURE = "infrastructure"
    DATABASE = "database"
    CONTAINER = "container"
    WEB_SERVER = "web_server"
    UNKNOWN = "unknown"


class ContainerState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"
    EXITED = "exited"
    RESTARTING = "restarting"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class AlertStatus(str, Enum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class NotificationChannel(str, Enum):
    GOOGLE_CHAT = "google_chat"
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"


# --- Container Schemas ---


class ContainerInfo(BaseModel):
    """Information about a monitored Docker container."""

    container_id: str = Field(..., description="Docker container ID")
    name: str = Field(..., description="Container name")
    image: str = Field(..., description="Container image")
    state: ContainerState = Field(..., description="Current container state")
    status: str = Field("", description="Container status string")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    labels: Dict[str, str] = Field(default_factory=dict)
    log_driver: str = Field("json-file", description="Logging driver")
    hostname: str = Field("", description="Hostname")


class ContainerListResponse(BaseModel):
    """Response for container listing."""

    containers: List[ContainerInfo]
    total: int = 0
    monitored: int = 0


# --- Alert / Error Schemas ---


class ErrorMatch(BaseModel):
    """Details of a matched error pattern."""

    pattern: str = Field(..., description="Matched pattern name")
    category: ErrorCategory = Field(..., description="Error category")
    line: str = Field(..., description="The log line that matched")
    line_number: int = Field(0, description="Line number in log stream")
    severity: Severity = Field(Severity.MEDIUM, description="Inferred severity")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AlertCreate(BaseModel):
    """Schema for creating a new alert."""

    container_id: str
    container_name: str
    error_category: ErrorCategory
    severity: Severity
    match_pattern: str
    log_snippet: str
    raw_log_line: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    hostname: str = ""


class AlertStatusUpdate(BaseModel):
    """Request body for updating alert/incident status."""

    status: AlertStatus


class AlertResponse(BaseModel):
    """Response schema for an alert."""

    id: int
    container_id: str
    container_name: str
    error_category: ErrorCategory
    severity: Severity
    match_pattern: str
    log_snippet: str
    timestamp: datetime
    status: AlertStatus
    hostname: str
    created_at: datetime
    analysis_id: Optional[int] = None

    model_config = {"from_attributes": True}


# --- RCA Analysis Schemas ---


class RCARequest(BaseModel):
    """Request for manual RCA analysis."""

    container_id: str = Field(..., description="Container ID to analyze")
    log_snippet: str = Field(..., description="Error log snippet")
    error_type: str = Field("", description="Type of error detected")


class RootCause(BaseModel):
    """Identified root cause details."""

    description: str = Field(..., description="Root cause description")
    component: str = Field("", description="Affected component")
    dependency: str = Field("", description="Affected dependency")
    probability: float = Field(0.0, ge=0.0, le=1.0)


class Solution(BaseModel):
    """Suggested solutions."""

    immediate_fix: str = Field("", description="Immediate remediation steps")
    permanent_fix: str = Field("", description="Long-term resolution")
    preventive_action: str = Field("", description="Preventive measures")
    best_practices: str = Field("", description="Best practice recommendations")


class RCAResult(BaseModel):
    """Complete RCA analysis result."""

    id: int = 0
    container_id: str
    container_name: str = ""
    error_type: str
    severity: Severity
    root_cause: RootCause
    explanation: str = Field(..., description="Why the issue happened")
    solutions: Solution
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    log_snippet: str = ""
    possible_impact: str = ""
    references: List[str] = Field(default_factory=list)
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)
    ai_provider: str = ""

    model_config = {"from_attributes": True}


class RCAResponse(BaseModel):
    """API response for RCA analysis."""

    analysis_id: int
    container_name: str
    error_type: str
    severity: Severity
    root_cause: RootCause
    explanation: str
    solutions: Solution
    confidence: float
    possible_impact: str
    references: List[str]
    analyzed_at: datetime
    ai_provider: str
    status: AlertStatus = AlertStatus.NEW
    markdown_report: str = ""


# --- Notification Schemas ---


class NotificationMessage(BaseModel):
    """Notification message structure."""

    container_name: str
    hostname: str
    timestamp: datetime
    severity: Severity
    root_cause: str
    suggested_fix: str
    log_snippet: str
    confidence: float
    channel: NotificationChannel


class NotificationResponse(BaseModel):
    """Response for a sent notification."""

    success: bool
    channel: NotificationChannel
    message_id: str = ""
    error: str = ""


# --- Dashboard / Statistics Schemas ---


class DashboardStats(BaseModel):
    """Dashboard statistics."""

    total_containers: int = 0
    running_containers: int = 0
    stopped_containers: int = 0
    total_alerts: int = 0
    critical_alerts: int = 0
    high_alerts: int = 0
    medium_alerts: int = 0
    low_alerts: int = 0
    resolved_incidents: int = 0
    open_incidents: int = 0
    alerts_today: int = 0
    avg_confidence: float = 0.0
    top_error_categories: Dict[str, int] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    version: str = "1.0.0"
    uptime_seconds: float = 0.0
    containers_monitored: int = 0
    db_connected: bool = False
    ai_provider_configured: bool = False
    notifications_configured: Dict[str, bool] = Field(default_factory=dict)


class FeedbackCreate(BaseModel):
    """User feedback on RCA analysis."""

    analysis_id: int
    was_helpful: bool
    correct_root_cause: bool = False
    correct_solution: bool = False
    comments: str = ""


# --- Settings Schemas ---


class CustomLogSource(BaseModel):
    """A custom application log file to monitor (non-Docker apps)."""

    path: str = Field(..., description="Absolute path to the log file")
    name: str = Field("", description="Display name (defaults to file basename)")


class LogSourceCreate(BaseModel):
    """Request to add a new custom log file source."""

    path: str = Field(..., description="Absolute path to the log file")
    name: str = Field("", description="Display name (defaults to file basename)")


class AISettings(BaseModel):
    """AI provider configuration."""

    provider: str = Field("ollama", description="AI provider: ollama, lm_studio, openai, openrouter, anthropic, google_gemini")
    api_key: str = Field("", description="API key (blank when using a local provider)")
    model: str = Field("", description="Model name")
    base_url: str = Field("", description="Base URL for local providers (Ollama/LM Studio)")


class NotificationSettings(BaseModel):
    """Notification channel configuration."""

    google_chat_webhook_url: str = Field("", description="Google Chat inbound webhook URL")


class SettingsUpdate(BaseModel):
    """Runtime settings update payload."""

    ai: Optional[AISettings] = None
    notifications: Optional[NotificationSettings] = None


class SettingsResponse(BaseModel):
    """Current runtime settings (API keys masked)."""

    ai: Dict[str, Any] = Field(default_factory=dict)
    notifications: Dict[str, Any] = Field(default_factory=dict)
    custom_log_sources: List[CustomLogSource] = Field(default_factory=list)
    ai_configured: bool = False


# --- WebSocket Messages ---


class WSMessage(BaseModel):
    """WebSocket message structure."""

    type: str = Field(..., description="Message type: alert, analysis, container_update, stats")
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
