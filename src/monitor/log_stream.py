"""
Log Stream Manager for AI RCA Agent.

Handles buffering, preprocessing, and dispatching of container log lines
to the error detection engine.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


class LogBuffer:
    """
    Circular buffer for container logs.
    Maintains a rolling window of log lines per container.
    """

    def __init__(self, max_lines: int = 500):
        self._buffers: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_lines)
        )
        self._max_lines = max_lines

    def append(self, container_id: str, log_entry: Dict[str, Any]) -> None:
        """Add a log entry to the container's buffer."""
        self._buffers[container_id].append(log_entry)

    def get_slice(
        self,
        container_id: str,
        before_lines: int = 200,
        after_lines: int = 50,
        from_index: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get a slice of logs around an error.
        
        Args:
            container_id: Container to get logs for
            before_lines: Number of lines before the error
            after_lines: Number of lines after the error
            from_index: Specific index to center around. If None, uses most recent.
        """
        buffer = self._buffers.get(container_id, deque())
        if not buffer:
            return []

        logs = list(buffer)
        if from_index is not None and from_index < len(logs):
            start = max(0, from_index - before_lines)
            end = min(len(logs), from_index + after_lines)
        else:
            # Return most recent logs
            start = max(0, len(logs) - before_lines)
            end = len(logs)

        return logs[start:end]

    def get_recent(self, container_id: str, count: int = 100) -> List[Dict[str, Any]]:
        """Get most recent logs for a container."""
        buffer = self._buffers.get(container_id, deque())
        return list(buffer)[-count:]

    def clear(self, container_id: Optional[str] = None) -> None:
        """Clear buffer for a specific container or all containers."""
        if container_id:
            self._buffers.pop(container_id, None)
        else:
            self._buffers.clear()

    @property
    def container_count(self) -> int:
        return len(self._buffers)

    def get_all_container_ids(self) -> List[str]:
        return list(self._buffers.keys())


class LogStreamManager:
    """
    Manages log streaming, buffering, and dispatching for all containers.
    Acts as the central hub between Docker monitor and error detector.
    """

    def __init__(self):
        self.buffer = LogBuffer(max_lines=1000)
        self._error_detector_callback: Optional[Callable] = None
        self._log_processors: List[Callable] = []
        self._stats = {
            "total_lines_processed": 0,
            "total_errors_detected": 0,
            "lines_per_container": defaultdict(int),
        }

    def set_error_detector(self, callback: Callable) -> None:
        """Set the callback for error detection."""
        self._error_detector_callback = callback

    def add_processor(self, processor: Callable) -> None:
        """Add a log preprocessor function."""
        self._log_processors.append(processor)

    async def process_log_line(
        self,
        container_id: str,
        container_name: str,
        log_line: Any,
        run_detection: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Process a single log line from a source (container or log file).
        
        1. Parse the log line
        2. Run preprocessors
        3. Buffer the log
        4. Send to error detector (unless run_detection is False)
        
        Args:
            run_detection: Set to False to buffer the line for context
                without triggering error detection (e.g. historical seed lines).
        
        Returns parsed log entry or None if parsing failed.
        """
        try:
            # Parse the log line
            parsed = self._parse_log_line(log_line, container_id, container_name)
            if not parsed:
                return None

            # Run preprocessors
            for processor in self._log_processors:
                try:
                    parsed = processor(parsed)
                    if parsed is None:
                        return None
                except Exception as e:
                    logger.debug(f"Log processor error: {e}")

            # Buffer the log
            self.buffer.append(container_id, parsed)

            # Update stats
            self._stats["total_lines_processed"] += 1
            self._stats["lines_per_container"][container_id] += 1

            # Send to error detector
            if self._error_detector_callback and run_detection:
                await self._error_detector_callback(parsed)

            return parsed

        except Exception as e:
            logger.warning(f"Error processing log line for {container_name}: {e}")
            return None

    def _parse_log_line(
        self,
        log_line: Any,
        container_id: str,
        container_name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Parse a raw Docker log line into a structured entry.
        
        Docker log formats:
        - json-file: Returns dict/list with 'log', 'stream', 'time'
        - Raw string: Simple text log
        """
        if not log_line:
            return None

        timestamp = datetime.utcnow()
        message = ""
        stream = "stdout"

        if isinstance(log_line, dict):
            # json-file driver format
            message = log_line.get("log", log_line.get("message", ""))
            stream = log_line.get("stream", log_line.get("channel", "stdout"))
            # Parse timestamp if present
            if "time" in log_line or "timestamp" in log_line:
                try:
                    ts = log_line.get("time", log_line.get("timestamp", ""))
                    if ts:
                        timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
        elif isinstance(log_line, bytes):
            message = log_line.decode("utf-8", errors="replace").strip()
        elif isinstance(log_line, str):
            message = log_line.strip()
        elif isinstance(log_line, list):
            # Some Docker SDK versions return list of lines
            message = "\n".join(str(l) for l in log_line).strip()
        else:
            message = str(log_line).strip()

        if not message:
            return None

        # Detect stream from stderr indicator
        if "[stderr]" in message or stream == "stderr":
            stream = "stderr"

        return {
            "container_id": container_id,
            "container_name": container_name,
            "message": message,
            "stream": stream,
            "timestamp": timestamp,
            "raw": log_line,
        }

    def get_log_snippet(
        self,
        container_id: str,
        error_index: Optional[int] = None,
        before_lines: int = 200,
        after_lines: int = 50,
    ) -> str:
        """
        Get a formatted log snippet for RCA analysis.
        
        Args:
            container_id: Container to get logs for
            error_index: Index of the error line
            before_lines: Lines before error to include
            after_lines: Lines after error to include
            
        Returns:
            Formatted log snippet as string
        """
        logs = self.buffer.get_slice(
            container_id=container_id,
            before_lines=before_lines,
            after_lines=after_lines,
            from_index=error_index,
        )

        if not logs:
            return ""

        lines = []
        for log in logs:
            ts = log.get("timestamp", "")
            msg = log.get("message", "")
            stream = log.get("stream", "")
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            lines.append(f"[{ts}] [{stream}] {msg}")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get processing statistics."""
        return {
            **self._stats,
            "buffered_containers": self.buffer.container_count,
        }

    def reset_container_stats(self, container_id: str) -> None:
        """Reset stats for a container."""
        self._stats["lines_per_container"][container_id] = 0

    def remove_container(self, container_id: str) -> None:
        """Remove container data."""
        self.buffer.clear(container_id)
        self._stats["lines_per_container"].pop(container_id, None)
