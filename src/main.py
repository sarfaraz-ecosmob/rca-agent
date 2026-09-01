"""
AI RCA Agent - Main Application Entry Point.

Orchestrates:
- Docker container monitoring and log streaming
- Real-time error detection
- AI-powered root cause analysis
- Multi-channel notifications (Google Chat, Email)
- REST API and WebSocket for the dashboard
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

import structlog
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from config.settings import get_settings
from src.models.schemas import Severity

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
settings = get_settings()

# Application start time
START_TIME = time.time()

# Global component references
docker_monitor = None
log_stream_manager = None
error_detector = None
rca_analyzer = None
google_chat_notifier = None
email_notifier = None
analysis_queue = None
file_monitor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    global docker_monitor, log_stream_manager, error_detector
    global rca_analyzer, google_chat_notifier, email_notifier, analysis_queue
    global file_monitor

    logger.info("Starting AI RCA Agent...", provider=settings.AI_PROVIDER.value)

    # Initialize components
    from src.monitor.docker_monitor import DockerMonitor
    from src.monitor.file_monitor import FileLogMonitor
    from src.monitor.log_stream import LogStreamManager
    from src.detector.error_detector import ErrorDetector
    from src.analyzer.rca_analyzer import RCAAnalyzer
    from src.notifier.google_chat import GoogleChatNotifier
    from src.notifier.email_notifier import EmailNotifier

    log_stream_manager = LogStreamManager()
    error_detector = ErrorDetector(dedup_window_seconds=settings.DEDUPLICATION_WINDOW)
    rca_analyzer = RCAAnalyzer()
    google_chat_notifier = GoogleChatNotifier()
    email_notifier = EmailNotifier()
    docker_monitor = DockerMonitor()
    file_monitor = FileLogMonitor()

    # Wire up callbacks
    log_stream_manager.set_error_detector(error_detector.detect)

    error_detector.set_alert_callback(handle_alert)

    # Initialize database FIRST (tables must exist before monitoring starts)
    try:
        from src.db.database import init_db

        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.warning("Database initialization failed", error=str(e))

    # Register components for runtime reconfiguration (dashboard Settings page)
    try:
        from src.config import runtime

        runtime.components["google_chat_notifier"] = google_chat_notifier
        runtime.components["rca_analyzer"] = rca_analyzer
        runtime.components["file_monitor"] = file_monitor
    except Exception as e:
        logger.warning("Failed to register runtime components", error=str(e))

    # Start Docker container monitoring (works only when Docker is available)
    try:
        await docker_monitor.connect()
        await docker_monitor.start_monitoring(
            on_log_callback=handle_log_line,
            on_container_stop_callback=handle_container_stop,
        )
        logger.info("Container monitoring started successfully")
    except Exception as e:
        logger.error(
            "Failed to start container monitoring. "
            "If your application runs outside Docker, configure CUSTOM_LOG_FILES instead.",
            error=str(e),
        )

    # Start custom log file monitoring (non-Docker applications)
    try:
        await file_monitor.start_monitoring(on_log_callback=handle_log_line)
        logger.info("Custom log file monitoring started successfully")
    except Exception as e:
        logger.error("Failed to start custom log file monitoring", error=str(e))

    # Apply runtime settings AFTER monitors are started so the file monitor
    # callback is wired up before runtime log sources are added.
    try:
        from src.config import runtime

        await runtime.apply_runtime_settings()
        logger.info("Runtime settings applied")
    except Exception as e:
        logger.warning("Failed to apply runtime settings", error=str(e))

    # Start background analysis worker
    analysis_queue = asyncio.Queue()
    analysis_worker_task = asyncio.create_task(process_analysis_queue())

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down AI RCA Agent...")
    analysis_worker_task.cancel()
    try:
        await analysis_worker_task
    except asyncio.CancelledError:
        pass

    if docker_monitor:
        await docker_monitor.stop_monitoring()
        await docker_monitor.disconnect()

    if file_monitor:
        await file_monitor.stop_monitoring()

    if google_chat_notifier:
        await google_chat_notifier.close()

    from src.db.database import close_db

    await close_db()
    logger.info("AI RCA Agent shutdown complete.")


# Create FastAPI application
app = FastAPI(
    title="AI RCA Agent",
    description="AI-Powered Root Cause Analysis Agent for Docker Containers",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routes
from src.api.routes import router as api_router

app.include_router(api_router)




@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    from src.api.websocket import websocket_endpoint as ws_handler

    await ws_handler(websocket)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "AI RCA Agent",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "api": "/api/v1",
        "health": "/api/v1/health",
        "websocket": "/ws",
    }


# --- Core Event Handlers ---


async def handle_log_line(
    container_id: str,
    container_name: str,
    log_line: Any,
    detect: bool = True,
) -> None:
    """Handle a log line from a container or log file source."""
    global log_stream_manager

    if log_stream_manager:
        await log_stream_manager.process_log_line(
            container_id=container_id,
            container_name=container_name,
            log_line=log_line,
            run_detection=detect,
        )


async def handle_container_stop(container_id: str, container_name: str) -> None:
    """Handle a container stopping."""
    global log_stream_manager

    logger.info(f"Container stopped: {container_name} ({container_id})")

    if log_stream_manager:
        log_stream_manager.remove_container(container_id)

    # Broadcast to WebSocket
    try:
        from src.api.websocket import broadcast_container_update

        await broadcast_container_update(
            {
                "container_id": container_id,
                "container_name": container_name,
                "state": "stopped",
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
    except Exception as e:
        logger.warning("WebSocket broadcast failed", error=str(e))


async def handle_alert(alert: Any) -> None:
    """Handle a detected error alert."""
    global rca_analyzer, google_chat_notifier, email_notifier, analysis_queue

    try:
        from src.db.database import async_session_factory
        from src.db.models import AlertModel

        # Save alert to database
        async with async_session_factory() as session:
            alert_db = AlertModel(
                container_id=alert.container_id,
                container_name=alert.container_name,
                error_category=alert.error_category.value
                if hasattr(alert.error_category, "value")
                else str(alert.error_category),
                severity=alert.severity.value
                if hasattr(alert.severity, "value")
                else str(alert.severity),
                match_pattern=alert.match_pattern,
                log_snippet=alert.log_snippet[:2000] if alert.log_snippet else "",
                raw_log_line=alert.raw_log_line[:2000] if alert.raw_log_line else "",
                timestamp=alert.timestamp,
                hostname=alert.hostname or "",
            )
            session.add(alert_db)
            await session.commit()
            alert_id = alert_db.id

        # Broadcast alert via WebSocket
        try:
            from src.api.websocket import broadcast_alert

            await broadcast_alert(
                {
                    "id": alert_id,
                    "container_id": alert.container_id,
                    "container_name": alert.container_name,
                    "error_category": alert.error_category.value
                    if hasattr(alert.error_category, "value")
                    else str(alert.error_category),
                    "severity": alert.severity.value
                    if hasattr(alert.severity, "value")
                    else str(alert.severity),
                    "match_pattern": alert.match_pattern,
                    "log_snippet": alert.log_snippet[:500] if alert.log_snippet else "",
                    "timestamp": alert.timestamp.isoformat(),
                }
            )
        except Exception as e:
            logger.warning("WebSocket broadcast failed", error=str(e))

        # Queue for AI analysis
        if analysis_queue and alert.severity.value in ("critical", "high"):
            await analysis_queue.put(
                {
                    "alert_id": alert_id,
                    "container_id": alert.container_id,
                    "container_name": alert.container_name,
                    "error_type": alert.match_pattern,
                    "severity": alert.severity.value,
                    "log_snippet": alert.log_snippet,
                }
            )
            logger.info(
                "Alert queued for AI analysis",
                alert_id=alert_id,
                container=alert.container_name,
            )

    except Exception as e:
        logger.error("Error handling alert", error=str(e))


async def process_analysis_queue() -> None:
    """Background worker that processes the analysis queue."""
    global rca_analyzer, google_chat_notifier, email_notifier, log_stream_manager

    logger.info("Analysis queue worker started")

    while True:
        try:
            task = await analysis_queue.get()

            container_id = task["container_id"]
            container_name = task["container_name"]
            error_type = task["error_type"]
            severity = task["severity"]
            alert_id = task["alert_id"]

            logger.info(
                "Starting RCA analysis",
                container=container_name,
                error_type=error_type,
                alert_id=alert_id,
            )

            # Get log snippet from buffer
            log_snippet = ""
            if log_stream_manager:
                log_snippet = log_stream_manager.get_log_snippet(
                    container_id=container_id,
                    before_lines=settings.MAX_LOG_LINES,
                    after_lines=settings.MAX_LOG_LINES_AFTER,
                )

            # If no AI provider/API key is configured, skip the AI analysis
            # but still send Google Chat notifications with the alert details.
            ai_available = False
            try:
                ai_available = bool(rca_analyzer and rca_analyzer.is_configured())
            except Exception as e:
                logger.warning("AI availability check failed", error=str(e))

            if not ai_available:
                logger.info(
                    "AI provider not configured - skipping AI analysis, "
                    "sending notification directly",
                    container=container_name,
                    error_type=error_type,
                    severity=severity,
                )
                analysis_result = {
                    "container_id": container_id,
                    "container_name": container_name,
                    "error_type": error_type or "Unknown",
                    "severity": severity,
                    "root_cause": {
                        "description": "AI analysis skipped - no AI provider/API key configured.",
                        "component": "",
                        "dependency": "",
                        "probability": 0.0,
                    },
                    "explanation": (
                        "AI root cause analysis was skipped because no AI provider "
                        "or API key is configured. Configure AI integration in the "
                        "dashboard Settings page to enable automated analysis."
                    ),
                    "solutions": {
                        "immediate_fix": "Review the log snippet manually. Configure an AI provider in Settings > AI Integration.",
                        "permanent_fix": "Add an API key or point to a local model (Ollama/LM Studio) in Settings.",
                        "preventive_action": "Set up AI integration to get automated root cause analysis.",
                        "best_practices": "Keep at least one AI provider configured for automated RCA.",
                    },
                    "confidence": 0.0,
                    "possible_impact": "Impact assessment unavailable without AI analysis.",
                    "references": [],
                    "analyzed_at": datetime.utcnow().isoformat(),
                    "ai_provider": "skipped",
                }
            else:
                # Perform AI analysis
                analysis_result = await rca_analyzer.analyze(
                    container_name=container_name,
                    container_id=container_id,
                    log_snippet=log_snippet,
                    error_type=error_type,
                    severity=severity,
                )

            # Save analysis to database
            try:
                from src.db.database import async_session_factory
                from src.db.models import AnalysisModel

                async with async_session_factory() as session:
                    analysis_db = AnalysisModel(
                        container_id=container_id,
                        container_name=container_name,
                        alert_id=alert_id,
                        error_type=analysis_result.get("error_type", error_type),
                        severity=analysis_result.get("severity", severity),
                        root_cause=analysis_result.get("root_cause", {}),
                        explanation=analysis_result.get("explanation", ""),
                        solutions=analysis_result.get("solutions", {}),
                        confidence=analysis_result.get("confidence", 0.0),
                        log_snippet=log_snippet[:2000],
                        possible_impact=analysis_result.get("possible_impact", ""),
                        references=analysis_result.get("references", []),
                        ai_provider=analysis_result.get("ai_provider", ""),
                    )
                    session.add(analysis_db)
                    await session.commit()
                    analysis_id = analysis_db.id

                # Broadcast analysis result via WebSocket
                try:
                    from src.api.websocket import broadcast_analysis

                    await broadcast_analysis(
                        {
                            "analysis_id": analysis_id,
                            "container_name": container_name,
                            "error_type": analysis_result.get("error_type", error_type),
                            "severity": analysis_result.get("severity", severity),
                            "root_cause": analysis_result.get("root_cause", {}),
                            "explanation": analysis_result.get("explanation", "")[:500],
                            "confidence": analysis_result.get("confidence", 0.0),
                        }
                    )
                except Exception as e:
                    logger.warning("WebSocket broadcast failed", error=str(e))

                # Send notifications for critical/high severity
                if severity in ("critical", "high"):
                    notifier_tasks = []

                    # Normalize severity to enum (it's a string from the task dict)
                    severity_enum = Severity(severity)

                    # Google Chat notification
                    if google_chat_notifier:
                        chat_task = asyncio.create_task(
                            google_chat_notifier.send_alert(
                                container_name=container_name,
                                severity=severity_enum,
                                error_type=analysis_result.get("error_type", error_type),
                                root_cause=analysis_result.get("root_cause", {}).get(
                                    "description", ""
                                ),
                                suggested_fix=analysis_result.get("solutions", {}).get(
                                    "immediate_fix", ""
                                ),
                                confidence=analysis_result.get("confidence", 0.0),
                                log_snippet=log_snippet[:500],
                                hostname=container_id[:12],
                            )
                        )
                        notifier_tasks.append(chat_task)

                    # Email notification
                    if email_notifier:
                        email_task = asyncio.create_task(
                            email_notifier.send_alert(
                                container_name=container_name,
                                severity=severity_enum,
                                error_type=analysis_result.get("error_type", error_type),
                                root_cause=analysis_result.get("root_cause", {}).get(
                                    "description", ""
                                ),
                                suggested_fix=analysis_result.get("solutions", {}).get(
                                    "immediate_fix", ""
                                ),
                                confidence=analysis_result.get("confidence", 0.0),
                                log_snippet=log_snippet[:500],
                                hostname=container_id[:12],
                            )
                        )
                        notifier_tasks.append(email_task)

                    # Wait for notifications to complete
                    if notifier_tasks:
                        results = await asyncio.gather(*notifier_tasks, return_exceptions=True)
                        for i, result in enumerate(results):
                            if isinstance(result, Exception):
                                logger.error(
                                    "Notification failed",
                                    channel="google_chat" if i == 0 else "email",
                                    error=str(result),
                                )

                logger.info(
                    "RCA analysis completed",
                    container=container_name,
                    error_type=error_type,
                    confidence=analysis_result.get("confidence", 0.0),
                    alert_id=alert_id,
                    analysis_id=analysis_id,
                )

            except Exception as e:
                logger.error(
                    "Failed to save analysis result",
                    container=container_name,
                    error=str(e),
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Analysis queue processing error", error=str(e))
            await asyncio.sleep(1)


def main() -> None:
    """Entry point for running the RCA agent."""
    import uvicorn

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.value),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(
        "Starting AI RCA Agent server",
        host=settings.API_HOST,
        port=settings.API_PORT,
        ai_provider=settings.AI_PROVIDER.value,
    )

    # Run the FastAPI app with uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.value.lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
