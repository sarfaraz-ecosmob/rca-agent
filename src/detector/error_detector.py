"""
Error Detection Engine for AI RCA Agent.

Processes log lines through all defined error patterns,
deduplicates alerts, and triggers RCA analysis.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.detector.error_patterns import ALL_PATTERNS, ErrorPattern
from src.models.schemas import (
    AlertCreate,
    ErrorCategory,
    ErrorMatch,
    Severity,
)

logger = logging.getLogger(__name__)

# Higher value = higher priority. Used to pick the best match when a log line
# matches multiple patterns.
_SEVERITY_WEIGHT = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


def _pattern_score(pattern: ErrorPattern) -> Tuple[int, int]:
    """Score a matched pattern for best-match selection.

    Severity dominates so critical/high errors are never downgraded by a
    generic low-severity match; a longer (more specific) regex breaks ties
    so generic patterns like ``error`` cannot shadow specific ones like
    ``mysql_connection`` that appear later in the pattern list.
    """
    return (_SEVERITY_WEIGHT.get(pattern.severity, 0), len(pattern.pattern))


class ErrorDetector:
    """
    Real-time error detection engine.
    
    Features:
    - Multi-pattern matching across all error categories
    - Severity inference based on matched patterns
    - Deduplication within configurable time windows
    - Rate limiting to prevent alert storms
    - Pattern match statistics

    When a log line matches several patterns, the best match wins: higher
    severity first, then the more specific (longer) pattern. This prevents
    generic patterns (e.g. ``error``) from shadowing specific ones (e.g.
    ``mysql_connection``). Note this is intentional: a generic critical
    pattern (e.g. ``fatal``) still outranks a specific medium pattern
    (e.g. ``mysql_query_error``) so real criticals are never downgraded.
    """

    def __init__(self, dedup_window_seconds: int = 300):
        # Pre-sort by best-match score (severity first, then specificity) so
        # the first pattern to match a line is always the best match. This
        # lets generic patterns (e.g. "error") not shadow specific ones
        # (e.g. "mysql_connection") while still short-circuiting on a match.
        # Python's sort is stable, so equal-scored patterns keep the original
        # ALL_PATTERNS order for deterministic tie-breaking.
        self._patterns = sorted(ALL_PATTERNS, key=_pattern_score, reverse=True)
        self._dedup_window = timedelta(seconds=dedup_window_seconds)
        self._recent_matches: Dict[str, Dict[str, datetime]] = defaultdict(dict)
        self._on_alert_callback: Optional[Callable] = None
        self._stats = {
            "total_matches": 0,
            "deduplicated": 0,
            "alerts_triggered": 0,
            "matches_by_category": defaultdict(int),
            "matches_by_severity": defaultdict(int),
        }
        self._match_counter: Dict[str, int] = defaultdict(int)
        self._rate_limit: Dict[str, datetime] = {}
        self._rate_limit_window = timedelta(seconds=10)
        self._max_alerts_per_minute = 60

    def set_alert_callback(self, callback: Callable) -> None:
        """Set callback for when an alert is triggered."""
        self._on_alert_callback = callback

    async def detect(self, log_entry: Dict[str, Any]) -> Optional[AlertCreate]:
        """
        Detect errors in a log line.
        
        Args:
            log_entry: Parsed log entry from LogStreamManager

        Returns:
            AlertCreate if error detected, None otherwise

        When several patterns match the same line, the best match wins:
        higher severity first, then the more specific (longer) pattern, so
        generic patterns like ``error`` can't shadow specific ones like
        ``mysql_connection``.
        """
        message = log_entry.get("message", "")
        container_id = log_entry.get("container_id", "")
        container_name = log_entry.get("container_name", "")
        timestamp = log_entry.get("timestamp", datetime.utcnow())
        stream = log_entry.get("stream", "stdout")

        if not message:
            return None

        # Patterns are pre-sorted by best-match score (severity first, then
        # specificity), so the first pattern that matches is the best match
        # and we can short-circuit immediately.
        for pattern in self._patterns:
            match = pattern.match(message)
            if match:
                return await self._handle_match(
                    pattern=pattern,
                    log_entry=log_entry,
                    match=match,
                )

        return None

    async def _handle_match(
        self,
        pattern: ErrorPattern,
        log_entry: Dict[str, Any],
        match,
    ) -> Optional[AlertCreate]:
        """Handle a pattern match with deduplication and rate limiting."""
        container_id = log_entry.get("container_id", "")
        pattern_name = pattern.name
        message = log_entry.get("message", "")

        # Update stats
        self._stats["total_matches"] += 1
        self._stats["matches_by_category"][pattern.category.value] += 1
        self._stats["matches_by_severity"][pattern.severity.value] += 1
        self._match_counter[pattern_name] += 1

        # Rate limiting
        rate_key = f"{container_id}:{pattern_name}"
        now = datetime.utcnow()
        if rate_key in self._rate_limit:
            if now - self._rate_limit[rate_key] < self._rate_limit_window:
                logger.debug(f"Rate limited: {rate_key}")
                return None
        self._rate_limit[rate_key] = now

        # Global rate limit
        if self._stats["alerts_triggered"] > self._max_alerts_per_minute:
            logger.warning("Global rate limit reached for alerts")
            return None

        # Deduplication check
        dedup_key = f"{container_id}:{pattern_name}"
        if await self._is_deduplicated(dedup_key, now):
            self._stats["deduplicated"] += 1
            return None

        # Create alert
        self._stats["alerts_triggered"] += 1
        self._recent_matches[container_id][pattern_name] = now

        alert = AlertCreate(
            container_id=container_id,
            container_name=log_entry.get("container_name", ""),
            error_category=pattern.category,
            severity=pattern.severity,
            match_pattern=pattern_name,
            log_snippet=message[:2000],  # Truncate to reasonable length
            raw_log_line=message,
            timestamp=log_entry.get("timestamp", datetime.utcnow()),
            hostname=log_entry.get("hostname", ""),
        )

        logger.info(
            f"Error detected | container={alert.container_name} "
            f"pattern={pattern_name} severity={pattern.severity.value} "
            f"category={pattern.category.value}"
        )

        # Trigger callback
        if self._on_alert_callback:
            await self._on_alert_callback(alert)

        return alert

    async def _is_deduplicated(
        self, dedup_key: str, now: datetime
    ) -> bool:
        """Check if this error is a duplicate within the dedup window."""
        for container_id, patterns in self._recent_matches.items():
            if f"{container_id}:" in dedup_key:
                pattern_name = dedup_key.split(":")[1]
                last_seen = patterns.get(pattern_name)
                if last_seen and (now - last_seen) < self._dedup_window:
                    return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get detection statistics."""
        return dict(self._stats)

    def get_top_errors(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Get most frequently matched error patterns."""
        sorted_errors = sorted(
            self._match_counter.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_errors[:limit]

    def get_matches_by_category(self) -> Dict[str, int]:
        """Get match counts by category."""
        return dict(self._stats["matches_by_category"])

    def get_matches_by_severity(self) -> Dict[str, int]:
        """Get match counts by severity."""
        return dict(self._stats["matches_by_severity"])

    def reset_stats(self) -> None:
        """Reset all statistics."""
        self._stats = {
            "total_matches": 0,
            "deduplicated": 0,
            "alerts_triggered": 0,
            "matches_by_category": defaultdict(int),
            "matches_by_severity": defaultdict(int),
        }
        self._match_counter.clear()
        self._recent_matches.clear()
        self._rate_limit.clear()
