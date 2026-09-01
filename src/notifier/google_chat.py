"""
Google Chat Notification for AI RCA Agent.

Sends rich, formatted alert notifications to Google Chat via webhooks.
Supports severity-based card colors, actionable messages, and markdown formatting.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from config.settings import get_settings
from src.models.schemas import NotificationChannel, NotificationMessage, Severity

logger = logging.getLogger(__name__)
settings = get_settings()


class GoogleChatNotifier:
    """
    Sends notifications to Google Chat using inbound webhooks.
    
    Features:
    - Rich card-based messages with severity colors
    - Formatted error details, root cause, and solutions        - Links to the API for more details
    - Handles webhook failures gracefully
    """

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.GOOGLE_CHAT_WEBHOOK_URL
        self._client = httpx.AsyncClient(timeout=15)

    def configure(self, webhook_url: Optional[str] = None) -> None:
        """Update the Google Chat webhook URL at runtime."""
        if webhook_url is not None:
            self.webhook_url = webhook_url
        if self.webhook_url:
            logger.info("Google Chat notifier reconfigured (webhook set)")

    async def send_alert(
        self,
        container_name: str,
        severity: Severity,
        error_type: str,
        root_cause: str,
        suggested_fix: str,
        confidence: float,
        log_snippet: str = "",
        hostname: str = "",
    ) -> Dict[str, Any]:
        """
        Send an alert notification to Google Chat.
        
        Returns:
            Dict with success status and message_id or error.
        """
        if not self.webhook_url:
            logger.warning("Google Chat webhook URL not configured")
            return {
                "success": False,
                "channel": NotificationChannel.GOOGLE_CHAT.value,
                "error": "Webhook URL not configured",
            }

        card = self._build_alert_card(
            container_name=container_name,
            severity=severity,
            error_type=error_type,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            confidence=confidence,
            log_snippet=log_snippet,
            hostname=hostname,
        )

        try:
            response = await self._client.post(
                self.webhook_url,
                json=card,
            )
            response.raise_for_status()
            logger.info(
                f"Google Chat alert sent | container={container_name} "
                f"severity={severity.value}"
            )
            return {
                "success": True,
                "channel": NotificationChannel.GOOGLE_CHAT.value,
                "message_id": response.headers.get("x-goog-message-id", ""),
            }
        except httpx.TimeoutException:
            logger.error("Google Chat webhook timeout")
            return {
                "success": False,
                "channel": NotificationChannel.GOOGLE_CHAT.value,
                "error": "Webhook timeout",
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"Google Chat webhook error: {e.response.status_code} {e.response.text}")
            return {
                "success": False,
                "channel": NotificationChannel.GOOGLE_CHAT.value,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.error(f"Google Chat notification failed: {e}")
            return {
                "success": False,
                "channel": NotificationChannel.GOOGLE_CHAT.value,
                "error": str(e),
            }

    def _build_alert_card(
        self,
        container_name: str,
        severity: Severity,
        error_type: str,
        root_cause: str,
        suggested_fix: str,
        confidence: float,
        log_snippet: str,
        hostname: str,
    ) -> Dict[str, Any]:
        """Build a Google Chat card message with severity-based formatting."""
        # Determine card color based on severity
        color = self._get_severity_color(severity)
        emoji = self._get_severity_emoji(severity)

        return {
            "cards": [
                {
                    "header": {
                        "title": f"{emoji} {severity.value.upper()} Error Detected",
                        "subtitle": f"Container: {container_name}",
                        "imageUrl": "",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "keyValue": {
                                        "topLabel": "Container",
                                        "content": container_name,
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Host",
                                        "content": hostname or "Unknown",
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Timestamp",
                                        "content": datetime.utcnow().strftime(
                                            "%Y-%m-%d %H:%M:%S UTC"
                                        ),
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Severity",
                                        "content": severity.value.upper(),
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Error Type",
                                        "content": error_type,
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Root Cause",
                                        "content": root_cause[:300],
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Suggested Fix",
                                        "content": suggested_fix[:500],
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "AI Confidence",
                                        "content": f"{confidence * 100:.0f}%",
                                    }
                                },
                            ]
                        },
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": (
                                            f"<b>Log Snippet:</b><br>"
                                            f"<pre style='font-size:10px;overflow:hidden;"
                                            f"text-overflow:ellipsis;max-width:500px;"
                                            f"white-space:pre-wrap;word-break:break-all;'>"
                                            f"{log_snippet[:500]}"
                                            f"</pre>"
                                        )
                                    }
                                }
                            ]
                        },

                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": (
                                            f"<i>Generated by AI RCA Agent | "
                                            f"Confidence: {confidence * 100:.0f}%</i>"
                                        )
                                    }
                                }
                            ]
                        },
                    ],
                }
            ]
        }

    def _get_severity_color(self, severity: Severity) -> str:
        """Get Google Chat card accent color for severity."""
        colors = {
            Severity.CRITICAL: "#FF0000",
            Severity.HIGH: "#FF6600",
            Severity.MEDIUM: "#FFCC00",
            Severity.LOW: "#00AA00",
            Severity.INFO: "#3366FF",
        }
        return colors.get(severity, "#666666")

    def _get_severity_emoji(self, severity: Severity) -> str:
        """Get emoji for severity level."""
        emojis = {
            Severity.CRITICAL: "🚨",
            Severity.HIGH: "⚠️",
            Severity.MEDIUM: "⚡",
            Severity.LOW: "ℹ️",
            Severity.INFO: "✅",
        }
        return emojis.get(severity, "❓")

    async def send_batch_alerts(self, alerts: list) -> list:
        """Send multiple alerts in batch."""
        results = []
        for alert in alerts:
            result = await self.send_alert(**alert)
            results.append(result)
        return results

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
