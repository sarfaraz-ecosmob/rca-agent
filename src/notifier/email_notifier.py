"""
Email Notification for AI RCA Agent.

Sends HTML-formatted email alerts via SMTP with severity-based formatting,
RCA details, and actionable recommendations.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from config.settings import get_settings
from src.models.schemas import NotificationChannel, Severity

logger = logging.getLogger(__name__)
settings = get_settings()


class EmailNotifier:
    """
    Sends email notifications via SMTP.
    
    Supports:
    - HTML-formatted email templates
    - TLS/SSL encryption
    - Multiple recipients
    - Severity-based styling
    - Attachments (log snippets, RCA reports)
    """

    def __init__(
        self,
        smtp_server: Optional[str] = None,
        smtp_port: Optional[int] = None,
        use_tls: bool = True,
        username: Optional[str] = None,
        password: Optional[str] = None,
        from_addr: Optional[str] = None,
        to_addrs: Optional[List[str]] = None,
    ):
        self.smtp_server = smtp_server or settings.SMTP_SERVER
        self.smtp_port = smtp_port or settings.SMTP_PORT
        self.use_tls = use_tls or settings.SMTP_USE_TLS
        self.username = username or settings.SMTP_USER
        self.password = password or settings.SMTP_PASSWORD
        self.from_addr = from_addr or settings.EMAIL_FROM
        self.to_addrs = to_addrs or [settings.EMAIL_TO]

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
        Send an alert notification via email.
        
        Returns:
            Dict with success status and message_id or error.
        """
        if not self.smtp_server or not self.username:
            logger.warning("SMTP server not configured")
            return {
                "success": False,
                "channel": NotificationChannel.EMAIL.value,
                "error": "SMTP not configured",
            }

        subject = self._build_subject(severity, container_name, error_type)
        html_body = self._build_html_body(
            container_name=container_name,
            severity=severity,
            error_type=error_type,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            confidence=confidence,
            log_snippet=log_snippet,
            hostname=hostname,
        )
        text_body = self._build_text_body(
            container_name=container_name,
            severity=severity,
            error_type=error_type,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            confidence=confidence,
            hostname=hostname,
        )

        try:
            # Build message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            # Send via SMTP
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)

            if self.username and self.password:
                server.login(self.username, self.password)

            server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            server.quit()

            logger.info(
                f"Email alert sent | container={container_name} "
                f"severity={severity.value} recipients={len(self.to_addrs)}"
            )
            return {
                "success": True,
                "channel": NotificationChannel.EMAIL.value,
                "message_id": msg["Message-ID"] or "",
            }

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed")
            return {
                "success": False,
                "channel": NotificationChannel.EMAIL.value,
                "error": "SMTP authentication failed",
            }
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return {
                "success": False,
                "channel": NotificationChannel.EMAIL.value,
                "error": f"SMTP error: {str(e)[:200]}",
            }
        except Exception as e:
            logger.error(f"Email notification failed: {e}")
            return {
                "success": False,
                "channel": NotificationChannel.EMAIL.value,
                "error": str(e)[:200],
            }

    def _build_subject(self, severity: Severity, container_name: str, error_type: str) -> str:
        """Build email subject line."""
        prefix = {
            Severity.CRITICAL: "[CRITICAL]",
            Severity.HIGH: "[HIGH]",
            Severity.MEDIUM: "[MEDIUM]",
            Severity.LOW: "[LOW]",
            Severity.INFO: "[INFO]",
        }.get(severity, "[ALERT]")
        return f"{prefix} RCA Alert - {container_name} - {error_type[:80]}"

    def _build_html_body(
        self,
        container_name: str,
        severity: Severity,
        error_type: str,
        root_cause: str,
        suggested_fix: str,
        confidence: float,
        log_snippet: str,
        hostname: str,
    ) -> str:
        """Build HTML email body with rich formatting."""
        color = {
            Severity.CRITICAL: "#dc3545",
            Severity.HIGH: "#fd7e14",
            Severity.MEDIUM: "#ffc107",
            Severity.LOW: "#28a745",
            Severity.INFO: "#17a2b8",
        }.get(severity, "#6c757d")

        return f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa; }}
        .container {{ max-width: 600px; margin: 20px auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .header {{ background-color: {color}; color: white; padding: 20px; }}
        .header h1 {{ margin: 0; font-size: 20px; }}
        .header p {{ margin: 5px 0 0; opacity: 0.9; }}
        .content {{ padding: 20px; }}
        .field {{ margin-bottom: 15px; }}
        .field-label {{ font-size: 12px; color: #666; text-transform: uppercase; margin-bottom: 3px; }}
        .field-value {{ font-size: 14px; color: #333; }}
        .log-snippet {{ background: #f5f5f5; padding: 10px; border-radius: 4px; font-family: 'Courier New', monospace; font-size: 12px; white-space: pre-wrap; overflow-x: auto; max-height: 200px; }}
        .severity-badge {{ display: inline-block; padding: 3px 8px; border-radius: 12px; color: white; font-size: 12px; font-weight: bold; background-color: {color}; }}
        .footer {{ background: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #666; }}
        .button {{ display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; margin-top: 10px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚨 {severity.value.upper()} Error Detected</h1>
            <p>Container: {container_name}</p>
        </div>
        <div class="content">
            <div class="field">
                <div class="field-label">Container</div>
                <div class="field-value"><strong>{container_name}</strong></div>
            </div>
            <div class="field">
                <div class="field-label">Host</div>
                <div class="field-value">{hostname or 'Unknown'}</div>
            </div>
            <div class="field">
                <div class="field-label">Error Type</div>
                <div class="field-value">{error_type}</div>
            </div>
            <div class="field">
                <div class="field-label">Severity</div>
                <div class="field-value"><span class="severity-badge">{severity.value.upper()}</span></div>
            </div>
            <div class="field">
                <div class="field-label">Root Cause</div>
                <div class="field-value">{root_cause}</div>
            </div>
            <div class="field">
                <div class="field-label">Suggested Fix</div>
                <div class="field-value">{suggested_fix}</div>
            </div>
            <div class="field">
                <div class="field-label">AI Confidence</div>
                <div class="field-value">{confidence * 100:.0f}%</div>
            </div>
            <div class="field">
                <div class="field-label">Log Snippet</div>
                <div class="log-snippet">{log_snippet[:1000]}</div>
            </div>

        </div>
        <div class="footer">
            Generated by <strong>AI RCA Agent</strong> | Confidence: {confidence * 100:.0f}%
        </div>
    </div>
</body>
</html>"""

    def _build_text_body(
        self,
        container_name: str,
        severity: Severity,
        error_type: str,
        root_cause: str,
        suggested_fix: str,
        confidence: float,
        hostname: str,
    ) -> str:
        """Build plain text email body."""
        return f"""
=== {severity.value.upper()} ERROR DETECTED ===

Container: {container_name}
Host: {hostname or 'Unknown'}
Error Type: {error_type}
Severity: {severity.value.upper()}

--- Root Cause ---
{root_cause}

--- Suggested Fix ---
{suggested_fix}

--- AI Confidence ---
{confidence * 100:.0f}%

---
Generated by AI RCA Agent
"""

    def is_configured(self) -> bool:
        """Check if email notifier is properly configured."""
        return bool(self.smtp_server and self.username)
