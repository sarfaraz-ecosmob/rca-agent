"""
REST API routes for the AI RCA Agent.

Provides endpoints for:
- Container management and monitoring
- Alert and incident retrieval
- RCA analysis (manual and automated)
- Statistics and health checks
- Feedback collection
- Notification history
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.db.database import get_db
from src.db.models import (
    AlertModel,
    AnalysisModel,
    ContainerModel,
    FeedbackModel,
    NotificationModel,
)
from src.models.schemas import (
    AlertResponse,
    AlertStatus,
    AlertStatusUpdate,
    ContainerInfo,
    ContainerListResponse,
    ContainerState,
    CustomLogSource,
    DashboardStats,
    FeedbackCreate,
    HealthResponse,
    LogSourceCreate,
    NotificationResponse,
    RCARequest,
    RCAResponse,
    RCAResult,
    SettingsResponse,
    SettingsUpdate,
    Severity,
)

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/v1", tags=["RCA Agent API"])


def _to_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Strip timezone info from a datetime to avoid naive/aware comparison errors."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


# --- Container Endpoints ---


@router.get(
    "/containers",
    response_model=ContainerListResponse,
    summary="List all monitored containers",
)
async def list_containers(
    state: Optional[str] = None,
    name: Optional[str] = None,
    created_from: Optional[datetime] = Query(None, description="Filter: created after this time"),
    created_to: Optional[datetime] = Query(None, description="Filter: created before this time"),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a list of all monitored Docker containers.
    
    Supports filtering by state (running, stopped, etc.), name search,
    and a created-at timestamp range.
    """
    query = select(ContainerModel)

    if state:
        query = query.where(ContainerModel.state == state)
    if name:
        query = query.where(ContainerModel.name.ilike(f"%{name}%"))
    if created_from:
        query = query.where(ContainerModel.created_at >= _to_naive(created_from))
    if created_to:
        query = query.where(ContainerModel.created_at <= _to_naive(created_to))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Get paginated results
    query = query.order_by(ContainerModel.last_seen.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    containers = result.scalars().all()

    monitored = sum(1 for c in containers if c.is_monitored)

    return ContainerListResponse(
        containers=[
            ContainerInfo(
                container_id=c.container_id,
                name=c.name,
                image=c.image,
                state=ContainerState(c.state) if c.state else ContainerState.UNKNOWN,
                status=c.status,
                created_at=c.created_at,
                labels=c.labels or {},
                log_driver=c.log_driver or "json-file",
                hostname=c.hostname or "",
            )
            for c in containers
        ],
        total=total,
        monitored=monitored,
    )


@router.get(
    "/containers/{container_id}",
    response_model=ContainerInfo,
    summary="Get container details",
)
async def get_container(
    container_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific container."""
    result = await db.execute(
        select(ContainerModel).where(ContainerModel.container_id == container_id)
    )
    container = result.scalar_one_or_none()
    if not container:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Container {container_id} not found",
        )

    return ContainerInfo(
        container_id=container.container_id,
        name=container.name,
        image=container.image,
        state=ContainerState(container.state) if container.state else ContainerState.UNKNOWN,
        status=container.status,
        created_at=container.created_at,
        labels=container.labels or {},
        log_driver=container.log_driver or "json-file",
        hostname=container.hostname or "",
    )


# --- Alert Endpoints ---


@router.get(
    "/alerts",
    response_model=List[AlertResponse],
    summary="List alerts with filters",
)
async def list_alerts(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    container_id: Optional[str] = None,
    category: Optional[str] = None,
    from_timestamp: Optional[datetime] = Query(None, description="Filter: alerts after this time"),
    to_timestamp: Optional[datetime] = Query(None, description="Filter: alerts before this time"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Get alerts with optional filtering by severity, status, container,
    category, or a timestamp range.
    """
    query = select(AlertModel)

    if severity:
        query = query.where(AlertModel.severity == severity)
    if status:
        query = query.where(AlertModel.status == status)
    if container_id:
        query = query.where(AlertModel.container_id == container_id)
    if category:
        query = query.where(AlertModel.error_category == category)
    if from_timestamp:
        query = query.where(AlertModel.timestamp >= _to_naive(from_timestamp))
    if to_timestamp:
        query = query.where(AlertModel.timestamp <= _to_naive(to_timestamp))

    query = query.order_by(AlertModel.timestamp.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    alerts = result.scalars().all()

    return [
        AlertResponse(
            id=a.id,
            container_id=a.container_id,
            container_name=a.container_name,
            error_category=a.error_category,
            severity=a.severity,
            match_pattern=a.match_pattern,
            log_snippet=a.log_snippet[:500],
            timestamp=a.timestamp,
            status=AlertStatus(a.status) if a.status else AlertStatus.NEW,
            hostname=a.hostname or "",
            created_at=a.created_at,
        )
        for a in alerts
    ]


@router.get(
    "/alerts/{alert_id}",
    response_model=AlertResponse,
    summary="Get alert details",
)
async def get_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific alert."""
    result = await db.execute(select(AlertModel).where(AlertModel.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found",
        )

    return AlertResponse(
        id=alert.id,
        container_id=alert.container_id,
        container_name=alert.container_name,
        error_category=alert.error_category,
        severity=alert.severity,
        match_pattern=alert.match_pattern,
        log_snippet=alert.log_snippet[:500],
        timestamp=alert.timestamp,
        status=AlertStatus(alert.status) if alert.status else AlertStatus.NEW,
        hostname=alert.hostname or "",
        created_at=alert.created_at,
    )


@router.patch(
    "/alerts/{alert_id}/status",
    response_model=AlertResponse,
    summary="Update alert status",
)
async def update_alert_status(
    alert_id: int,
    body: AlertStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update the status of an alert (acknowledge, resolve, dismiss)."""
    result = await db.execute(select(AlertModel).where(AlertModel.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found",
        )

    alert.status = body.status.value
    await db.commit()
    await db.refresh(alert)

    return AlertResponse(
        id=alert.id,
        container_id=alert.container_id,
        container_name=alert.container_name,
        error_category=alert.error_category,
        severity=alert.severity,
        match_pattern=alert.match_pattern,
        log_snippet=alert.log_snippet[:500],
        timestamp=alert.timestamp,
        status=AlertStatus(alert.status) if alert.status else AlertStatus.NEW,
        hostname=alert.hostname or "",
        created_at=alert.created_at,
    )


@router.delete(
    "/alerts/bulk",
    summary="Delete alerts by IDs",
)
async def delete_alerts_bulk(
    alert_ids: List[int],
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple alerts by their IDs."""
    if not alert_ids:
        return {"deleted": 0}
    result = await db.execute(
        select(AlertModel).where(AlertModel.id.in_(alert_ids))
    )
    alerts = result.scalars().all()
    count = len(alerts)
    for alert in alerts:
        await db.delete(alert)
    await db.commit()
    return {"deleted": count}


@router.delete(
    "/alerts/older-than",
    summary="Delete alerts older than X days",
)
async def delete_alerts_older_than(
    days: int = Query(..., ge=1, description="Delete alerts older than this many days"),
    db: AsyncSession = Depends(get_db),
):
    """Delete all alerts older than the specified number of days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(AlertModel).where(AlertModel.timestamp < cutoff)
    )
    alerts = result.scalars().all()
    count = len(alerts)
    for alert in alerts:
        await db.delete(alert)
    await db.commit()
    return {"deleted": count, "cutoff_date": cutoff.isoformat()}


@router.delete(
    "/incidents/bulk",
    summary="Delete incidents by IDs",
)
async def delete_incidents_bulk(
    incident_ids: List[int],
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple incidents by their IDs."""
    if not incident_ids:
        return {"deleted": 0}
    result = await db.execute(
        select(AnalysisModel).where(AnalysisModel.id.in_(incident_ids))
    )
    incidents = result.scalars().all()
    count = len(incidents)
    for inc in incidents:
        await db.delete(inc)
    await db.commit()
    return {"deleted": count}


@router.delete(
    "/incidents/older-than",
    summary="Delete incidents older than X days",
)
async def delete_incidents_older_than(
    days: int = Query(..., ge=1, description="Delete incidents older than this many days"),
    db: AsyncSession = Depends(get_db),
):
    """Delete all incidents older than the specified number of days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(AnalysisModel).where(AnalysisModel.analyzed_at < cutoff)
    )
    incidents = result.scalars().all()
    count = len(incidents)
    for inc in incidents:
        await db.delete(inc)
    await db.commit()
    return {"deleted": count, "cutoff_date": cutoff.isoformat()}


# --- Incidents (Analysis) Endpoints ---


@router.get(
    "/incidents",
    response_model=List[RCAResponse],
    summary="List RCA incidents",
)
async def list_incidents(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    container_id: Optional[str] = None,
    from_timestamp: Optional[datetime] = Query(None, description="Filter: incidents after this time"),
    to_timestamp: Optional[datetime] = Query(None, description="Filter: incidents before this time"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get list of RCA analysis incidents."""
    query = select(AnalysisModel)

    if severity:
        query = query.where(AnalysisModel.severity == severity)
    if status:
        query = query.where(AnalysisModel.status == status)
    if container_id:
        query = query.where(AnalysisModel.container_id == container_id)
    if from_timestamp:
        query = query.where(AnalysisModel.analyzed_at >= _to_naive(from_timestamp))
    if to_timestamp:
        query = query.where(AnalysisModel.analyzed_at <= _to_naive(to_timestamp))

    query = query.order_by(AnalysisModel.analyzed_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    analyses = result.scalars().all()

    return [
        RCAResponse(
            analysis_id=a.id,
            container_name=a.container_name,
            error_type=a.error_type,
            severity=a.severity,
            root_cause=a.root_cause,
            explanation=a.explanation,
            solutions=a.solutions,
            confidence=a.confidence,
            possible_impact=a.possible_impact or "",
            references=a.references or [],
            analyzed_at=a.analyzed_at,
            ai_provider=a.ai_provider or "unknown",
            status=AlertStatus(a.status) if a.status else AlertStatus.NEW,
            markdown_report=_generate_markdown_report(a),
        )
        for a in analyses
    ]


@router.get(
    "/incidents/{analysis_id}",
    response_model=RCAResponse,
    summary="Get RCA incident details",
)
async def get_incident(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed RCA analysis result."""
    result = await db.execute(
        select(AnalysisModel).where(AnalysisModel.id == analysis_id)
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis {analysis_id} not found",
        )

    return RCAResponse(
        analysis_id=analysis.id,
        container_name=analysis.container_name,
        error_type=analysis.error_type,
        severity=analysis.severity,
        root_cause=analysis.root_cause,
        explanation=analysis.explanation,
        solutions=analysis.solutions,
        confidence=analysis.confidence,
        possible_impact=analysis.possible_impact or "",
        references=analysis.references or [],
        analyzed_at=analysis.analyzed_at,
        ai_provider=analysis.ai_provider or "unknown",
        status=AlertStatus(analysis.status) if analysis.status else AlertStatus.NEW,
        markdown_report=_generate_markdown_report(analysis),
    )


@router.patch(
    "/incidents/{analysis_id}/status",
    response_model=RCAResponse,
    summary="Update incident status",
)
async def update_incident_status(
    analysis_id: int,
    body: AlertStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge, resolve, or dismiss an incident (RCA analysis)."""
    result = await db.execute(
        select(AnalysisModel).where(AnalysisModel.id == analysis_id)
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis {analysis_id} not found",
        )

    analysis.status = body.status.value
    await db.commit()
    await db.refresh(analysis)

    return RCAResponse(
        analysis_id=analysis.id,
        container_name=analysis.container_name,
        error_type=analysis.error_type,
        severity=analysis.severity,
        root_cause=analysis.root_cause,
        explanation=analysis.explanation,
        solutions=analysis.solutions,
        confidence=analysis.confidence,
        possible_impact=analysis.possible_impact or "",
        references=analysis.references or [],
        analyzed_at=analysis.analyzed_at,
        ai_provider=analysis.ai_provider or "unknown",
        status=AlertStatus(analysis.status) if analysis.status else AlertStatus.NEW,
        markdown_report=_generate_markdown_report(analysis),
    )


@router.post(
    "/analyse",
    response_model=RCAResponse,
    summary="Trigger manual RCA analysis",
)
async def trigger_analysis(
    request: RCARequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually trigger RCA analysis for a container with a log snippet.
    
    This endpoint allows manual analysis independent of automated error detection.
    """
    # Get recent logs if not provided
    log_snippet = request.log_snippet
    if not log_snippet:
        log_snippet = f"No log snippet provided. Using error type: {request.error_type}"

    # Perform AI analysis using the runtime-configured analyzer (falls back to env)
    analyzer = _get_runtime_analyzer()
    result = await analyzer.analyze(
        container_name=request.container_id,
        container_id=request.container_id,
        log_snippet=log_snippet,
        error_type=request.error_type,
    )

    # Save to database (may fail if container_id doesn't exist in containers table)
    analysis = None
    try:
        analysis = AnalysisModel(
            container_id=request.container_id,
            container_name=request.container_id,
            error_type=result.get("error_type", "Unknown"),
            severity=result.get("severity", "medium"),
            root_cause=result.get("root_cause", {}),
            explanation=result.get("explanation", ""),
            solutions=result.get("solutions", {}),
            confidence=result.get("confidence", 0.0),
            log_snippet=log_snippet[:2000],
            possible_impact=result.get("possible_impact", ""),
            references=result.get("references", []),
            ai_provider=result.get("ai_provider", ""),
            analyzed_at=datetime.utcnow(),
        )
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
    except Exception as e:
        logger.warning(f"Could not persist manual analysis to DB (container may not exist): {e}")
        await db.rollback()
        analysis = None

    if analysis is not None:
        return RCAResponse(
        analysis_id=analysis.id,
        container_name=analysis.container_name,
        error_type=analysis.error_type,
        severity=analysis.severity,
        root_cause=analysis.root_cause,
        explanation=analysis.explanation,
        solutions=analysis.solutions,
        confidence=analysis.confidence,
        possible_impact=analysis.possible_impact or "",
        references=analysis.references or [],
        analyzed_at=analysis.analyzed_at,
        ai_provider=analysis.ai_provider or "",
        markdown_report=_generate_markdown_report(analysis),
    )

    # Analysis succeeded but couldn't be saved (e.g. container doesn't exist in DB)
    from datetime import datetime as _dt
    severity_val = result.get("severity", "medium")
    root_cause_val = result.get("root_cause", {})
    solutions_val = result.get("solutions", {})
    analyzed_at = result.get("analyzed_at", _dt.utcnow().isoformat())
    if isinstance(analyzed_at, str):
        analyzed_at = _dt.fromisoformat(analyzed_at) if analyzed_at else _dt.utcnow()
    return RCAResponse(
        analysis_id=0,
        container_name=request.container_id,
        error_type=result.get("error_type", "Unknown"),
        severity=severity_val,
        root_cause=root_cause_val,
        explanation=result.get("explanation", ""),
        solutions=solutions_val,
        confidence=result.get("confidence", 0.0),
        possible_impact=result.get("possible_impact", ""),
        references=result.get("references", []),
        analyzed_at=analyzed_at,
        ai_provider=result.get("ai_provider", ""),
    )


# --- Statistics Endpoints ---


@router.get(
    "/statistics",
    response_model=DashboardStats,
    summary="Get dashboard statistics",
)
async def get_statistics(db: AsyncSession = Depends(get_db)):
    """Get aggregated statistics for the dashboard."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Container stats
    container_result = await db.execute(select(ContainerModel))
    containers = container_result.scalars().all()
    total_containers = len(containers)
    running = sum(1 for c in containers if c.state == "running")
    stopped = sum(1 for c in containers if c.state in ("exited", "stopped"))

    # Alert stats
    alert_result = await db.execute(select(AlertModel))
    alerts = alert_result.scalars().all()
    total_alerts = len(alerts)
    critical = sum(1 for a in alerts if a.severity == Severity.CRITICAL.value)
    high = sum(1 for a in alerts if a.severity == Severity.HIGH.value)
    medium = sum(1 for a in alerts if a.severity == Severity.MEDIUM.value)
    low = sum(1 for a in alerts if a.severity in (Severity.LOW.value, Severity.INFO.value))
    alerts_today = sum(1 for a in alerts if a.created_at >= today_start)

    # Incident stats
    analysis_result = await db.execute(select(AnalysisModel))
    analyses = analysis_result.scalars().all()
    resolved = sum(1 for a in analyses if a.status == AlertStatus.RESOLVED.value)
    open_incidents = len(analyses)

    # Average confidence
    avg_confidence = 0.0
    if analyses:
        avg_confidence = sum(a.confidence for a in analyses) / len(analyses)

    # Error categories
    category_counts = {}
    for a in alerts:
        cat = a.error_category
        category_counts[cat] = category_counts.get(cat, 0) + 1

    return DashboardStats(
        total_containers=total_containers,
        running_containers=running,
        stopped_containers=stopped,
        total_alerts=total_alerts,
        critical_alerts=critical,
        high_alerts=high,
        medium_alerts=medium,
        low_alerts=low,
        resolved_incidents=resolved,
        open_incidents=open_incidents,
        alerts_today=alerts_today,
        avg_confidence=avg_confidence,
        top_error_categories=category_counts,
    )


# --- Settings Endpoints ---


def _mask(value: str) -> str:
    """Mask a secret value for display."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


@router.get(
    "/settings",
    response_model=SettingsResponse,
    summary="Get current runtime settings",
)
async def get_settings_config(db: AsyncSession = Depends(get_db)):
    """Return current runtime configuration (secrets masked)."""
    from src.config import runtime

    cfg = await runtime.get_all_settings()

    webhook = cfg.get(runtime.KEY_GOOGLE_CHAT, "") or ""
    api_key = cfg.get(runtime.KEY_AI_API_KEY, "") or ""
    provider = cfg.get(runtime.KEY_AI_PROVIDER, "ollama")
    model = cfg.get(runtime.KEY_AI_MODEL, "") or ""
    base_url = cfg.get(runtime.KEY_AI_BASE_URL, "") or ""

    # Whether the chosen provider is actually usable right now
    ai_configured = _ai_is_configured(provider, api_key)

    return SettingsResponse(
        ai={
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key_masked": _mask(api_key) if api_key else "",
            "api_key_set": bool(api_key),
        },
        notifications={
            "google_chat_webhook_url": _mask(webhook) if webhook else "",
            "google_chat_configured": bool(webhook),
        },
        custom_log_sources=[
            CustomLogSource(path=s["path"], name=s.get("name", ""))
            for s in (cfg.get(runtime.KEY_CUSTOM_LOG_FILES, []) or [])
        ],
        ai_configured=ai_configured or bool(_analyzer_is_configured()),
    )


@router.put(
    "/settings",
    response_model=SettingsResponse,
    summary="Update runtime settings",
)
async def update_settings_config(
    request: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update AI and/or notification settings at runtime."""
    from src.config import runtime

    updates: Dict[str, Any] = {}

    if request.ai:
        ai = request.ai
        if ai.provider:
            updates[runtime.KEY_AI_PROVIDER] = ai.provider
        updates[runtime.KEY_AI_MODEL] = ai.model
        updates[runtime.KEY_AI_BASE_URL] = ai.base_url
        if ai.api_key:
            updates[runtime.KEY_AI_API_KEY] = ai.api_key

    if request.notifications:
        webhook = (request.notifications.google_chat_webhook_url or "").strip()
        if webhook:
            updates[runtime.KEY_GOOGLE_CHAT] = webhook

    if updates:
        await runtime.update_settings(updates)
        await runtime.apply_runtime_settings()

    # Return fresh settings
    cfg = await runtime.get_all_settings()
    api_key = cfg.get(runtime.KEY_AI_API_KEY, "") or ""
    provider = cfg.get(runtime.KEY_AI_PROVIDER, "ollama")
    model = cfg.get(runtime.KEY_AI_MODEL, "") or ""
    base_url = cfg.get(runtime.KEY_AI_BASE_URL, "") or ""
    webhook = cfg.get(runtime.KEY_GOOGLE_CHAT, "") or ""

    return SettingsResponse(
        ai={
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key_masked": _mask(api_key) if api_key else "",
            "api_key_set": bool(api_key),
        },
        notifications={
            "google_chat_webhook_url": _mask(webhook) if webhook else "",
            "google_chat_configured": bool(webhook),
        },
        custom_log_sources=[
            CustomLogSource(path=s["path"], name=s.get("name", ""))
            for s in (cfg.get(runtime.KEY_CUSTOM_LOG_FILES, []) or [])
        ],
        ai_configured=_ai_is_configured(provider, api_key),
    )


@router.get(
    "/log-sources",
    response_model=List[CustomLogSource],
    summary="List custom log file sources",
)
async def list_log_sources(db: AsyncSession = Depends(get_db)):
    """List currently configured custom log file sources."""
    from src.config import runtime

    cfg = await runtime.get_all_settings()
    return [
        CustomLogSource(path=s["path"], name=s.get("name", ""))
        for s in (cfg.get(runtime.KEY_CUSTOM_LOG_FILES, []) or [])
    ]


@router.post(
    "/log-sources",
    response_model=CustomLogSource,
    status_code=status.HTTP_201_CREATED,
    summary="Add a custom log file source",
)
async def add_log_source(
    request: LogSourceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a custom application log file to monitor in real-time."""
    from src.config import runtime

    path = request.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="Log file path is required")

    cfg = await runtime.get_all_settings()
    sources = list(cfg.get(runtime.KEY_CUSTOM_LOG_FILES, []) or [])
    if any(str(s.get("path", "")).strip() == path for s in sources):
        raise HTTPException(status_code=400, detail="Log source already exists")

    name = request.name.strip() or path.rstrip("/").split("/")[-1]
    sources.append({"path": path, "name": name})
    await runtime.sync_custom_log_sources(sources)

    return CustomLogSource(path=path, name=name)


@router.delete(
    "/log-sources",
    status_code=status.HTTP_200_OK,
    summary="Remove a custom log file source",
)
async def delete_log_source(
    path: str = Query(..., description="Log file path to stop monitoring"),
    db: AsyncSession = Depends(get_db),
):
    """Stop monitoring and remove a custom log file source."""
    from src.config import runtime

    cfg = await runtime.get_all_settings()
    sources = list(cfg.get(runtime.KEY_CUSTOM_LOG_FILES, []) or [])
    remaining = [
        s for s in sources if str(s.get("path", "")).strip() != path.strip()
    ]
    await runtime.sync_custom_log_sources(remaining)

    return {"removed": True, "path": path}


def _ai_is_configured(provider: str, api_key: str) -> bool:
    """Whether the chosen AI provider is configured (local providers need no key)."""
    if provider in ("ollama", "lm_studio"):
        return True
    return bool(api_key)


def _analyzer_is_configured() -> bool:
    """Ask the live analyzer (accounts for env-configured keys too)."""
    try:
        from src.config import runtime

        analyzer = runtime.components.get("rca_analyzer")
        return bool(analyzer and analyzer.is_configured())
    except Exception:
        return False


def _get_runtime_analyzer():
    """Return the runtime-configured analyzer, falling back to a fresh one."""
    from src.analyzer.rca_analyzer import RCAAnalyzer

    try:
        from src.config import runtime

        analyzer = runtime.components.get("rca_analyzer")
        if analyzer is not None:
            return analyzer
    except Exception:
        pass
    return RCAAnalyzer()


# --- Feedback Endpoints ---


@router.post(
    "/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback on RCA analysis",
)
async def submit_feedback(
    feedback: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
):
    """Submit user feedback on the quality and accuracy of an RCA analysis."""
    # Verify analysis exists
    result = await db.execute(
        select(AnalysisModel).where(AnalysisModel.id == feedback.analysis_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis {feedback.analysis_id} not found",
        )

    feedback_entry = FeedbackModel(
        analysis_id=feedback.analysis_id,
        was_helpful=feedback.was_helpful,
        correct_root_cause=feedback.correct_root_cause,
        correct_solution=feedback.correct_solution,
        comments=feedback.comments,
    )
    db.add(feedback_entry)
    await db.commit()

    return {
        "success": True,
        "message": "Feedback submitted successfully",
        "analysis_id": feedback.analysis_id,
    }


# --- Health Endpoint ---


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check endpoint",
)
async def health_check(
    db: AsyncSession = Depends(get_db),
):
    """Health check endpoint for monitoring the RCA agent itself."""
    import time

    from src.db.database import check_db_connection

    start_time = time.time()

    # Check database
    db_connected = await check_db_connection()

    # Check AI provider (uses runtime-configured analyzer, falls back to env)
    analyzer = _get_runtime_analyzer()
    ai_configured = analyzer.is_configured()

    # Check notifications
    notifications = {
        "google_chat": bool(settings.GOOGLE_CHAT_WEBHOOK_URL),
        "email": bool(settings.SMTP_SERVER and settings.SMTP_USER),
    }

    # Count monitored containers
    container_result = await db.execute(
        select(func.count()).where(ContainerModel.is_monitored == True)  # noqa: E712
    )
    monitored_count = container_result.scalar() or 0

    uptime = time.time() - start_time

    return HealthResponse(
        status="healthy" if db_connected else "degraded",
        uptime_seconds=uptime,
        containers_monitored=monitored_count,
        db_connected=db_connected,
        ai_provider_configured=ai_configured,
        notifications_configured=notifications,
    )


# --- Notification Endpoints ---


@router.get(
    "/notifications",
    response_model=List[NotificationResponse],
    summary="List recent notifications",
)
async def list_notifications(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get recent notification history."""
    query = (
        select(NotificationModel)
        .order_by(NotificationModel.sent_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    notifications = result.scalars().all()

    return [
        NotificationResponse(
            success=n.status == "sent",
            channel=n.channel,
            message_id=str(n.id),
            error=n.error_message or "",
        )
        for n in notifications
    ]


# --- Knowledge Base Endpoints ---


@router.get(
    "/knowledge-base",
    summary="List knowledge base entries",
)
async def list_knowledge_base(
    category: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get knowledge base entries used for RAG analysis."""
    from src.db.models import KnowledgeBaseModel

    query = select(KnowledgeBaseModel)
    if category:
        query = query.where(KnowledgeBaseModel.category == category)

    query = query.order_by(KnowledgeBaseModel.updated_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    return [
        {
            "id": entry.id,
            "title": entry.title,
            "source": entry.source,
            "tags": entry.tags or [],
            "category": entry.category,
            "created_at": entry.created_at,
        }
        for entry in entries
    ]


# --- Helper Functions ---




def _generate_markdown_report(analysis: AnalysisModel) -> str:
    """Generate a markdown-formatted RCA report."""
    root_cause = analysis.root_cause or {}
    solutions = analysis.solutions or {}

    return f"""# Root Cause Analysis Report

## Container Information
- **Container:** {analysis.container_name}
- **Error Type:** {analysis.error_type}
- **Severity:** {analysis.severity.upper()}
- **Analyzed At:** {analysis.analyzed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}
- **AI Provider:** {analysis.ai_provider or 'N/A'}
- **Confidence:** {analysis.confidence * 100:.0f}%

## Root Cause
**Description:** {root_cause.get('description', 'N/A')}
**Component:** {root_cause.get('component', 'N/A')}
**Dependency:** {root_cause.get('dependency', 'N/A')}
**Probability:** {root_cause.get('probability', 0) * 100:.0f}%

## Explanation
{analysis.explanation}

## Possible Impact
{analysis.possible_impact or 'N/A'}

## Solutions

### Immediate Fix
{solutions.get('immediate_fix', 'N/A')}

### Permanent Fix
{solutions.get('permanent_fix', 'N/A')}

### Preventive Action
{solutions.get('preventive_action', 'N/A')}

### Best Practices
{solutions.get('best_practices', 'N/A')}

## References
{chr(10).join(f'- {ref}' for ref in (analysis.references or [])) or 'N/A'}

---
*Generated by AI RCA Agent*
"""
