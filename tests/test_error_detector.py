"""Regression tests for the error detector's best-match pattern selection.

Covers the bug where the generic ``error`` pattern (application/medium)
shadowed the specific ``mysql_connection`` pattern (database/critical),
which caused MySQL connection failures to be misclassified and therefore
never trigger critical/high notifications.
"""

from datetime import datetime

import pytest

from src.detector.error_detector import ErrorDetector


def _entry(line: str, container_id: str = "test-container") -> dict:
    """Build a minimal log entry dict for the detector."""
    return {
        "message": line,
        "container_id": container_id,
        "container_name": "python-app",
        "timestamp": datetime.utcnow(),
        "stream": "stdout",
    }


@pytest.mark.asyncio
async def test_mysql_connection_error_classified_as_database_critical():
    """A MySQL connection error must match ``mysql_connection`` (critical),
    not the generic ``error`` pattern (medium)."""
    line = (
        "MySQL Connection Error: 2003 (HY000): "
        "Can't connect to MySQL server on 'localhost:3306' (111)"
    )
    alert = await ErrorDetector().detect(_entry(line))
    assert alert is not None
    assert alert.match_pattern == "mysql_connection"
    assert alert.error_category.value == "database"
    assert alert.severity.value == "critical"


@pytest.mark.asyncio
async def test_mysql_connection_error_with_timestamp_prefix():
    """The real-world log line (with timestamp/[ERROR] prefix) must be
    classified the same way as the bare error message."""
    line = (
        "2026-07-31 10:33:10,528 [ERROR] MySQL Connection Error: 2003 (HY000): "
        "Can't connect to MySQL server on 'localhost:3306' (111)"
    )
    alert = await ErrorDetector().detect(_entry(line))
    assert alert is not None
    assert alert.match_pattern == "mysql_connection"
    assert alert.error_category.value == "database"
    assert alert.severity.value == "critical"


@pytest.mark.asyncio
async def test_generic_error_still_detected_as_medium():
    """Lines that only contain a generic error keyword are unaffected."""
    alert = await ErrorDetector().detect(_entry("something went error in the app"))
    assert alert is not None
    assert alert.match_pattern == "error"
    assert alert.error_category.value == "application"
    assert alert.severity.value == "medium"


@pytest.mark.asyncio
async def test_specific_medium_pattern_beats_generic_medium():
    """Specificity breaks severity ties: mysql_query_error beats generic
    ``error`` even though both are medium severity."""
    alert = await ErrorDetector().detect(
        _entry("SQL syntax error: ER_PARSE_ERROR near 'SELECT'")
    )
    assert alert is not None
    assert alert.match_pattern == "mysql_query_error"
    assert alert.severity.value == "medium"


@pytest.mark.asyncio
async def test_severity_prevents_downgrade_by_longer_generic_pattern():
    """A critical keyword (fatal) is not downgraded to a medium severity
    pattern even when the medium regex happens to be longer."""
    alert = await ErrorDetector().detect(
        _entry("FATAL: timeout waiting for connection")
    )
    assert alert is not None
    assert alert.match_pattern == "fatal"
    assert alert.severity.value == "critical"


@pytest.mark.asyncio
async def test_no_match_returns_none():
    assert await ErrorDetector().detect(_entry("all systems operational")) is None
