"""
File Log Monitor for AI RCA Agent.

Tails configured log files on the host filesystem in real-time
(similar to `tail -f`) for applications that are NOT running inside
Docker containers. Log lines are fed into the same LogStreamManager
pipeline used for container logs, so error detection, RCA analysis,
and notifications work identically for file-based sources.

Configuration (via environment variables):
- CUSTOM_LOG_FILES: comma-separated absolute paths to log files
- CUSTOM_LOG_NAMES: optional comma-separated display names
- LOG_FILE_POLL_INTERVAL: seconds between file reads (default 0.5)
- LOG_FILE_TAIL_LINES: lines seeded into the buffer on start (default 100)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

from config.settings import get_settings
from src.db.database import async_session_factory
from src.db.models import ContainerModel

logger = logging.getLogger(__name__)
settings = get_settings()


class FileLogMonitor:
    """
    Monitors log files on the host filesystem in real-time.

    Features:
    - Tails configured log files (CUSTOM_LOG_FILES)
    - Detects file rotation and in-place truncation
    - Seeds the log buffer with the last N lines for RCA context
    - Registers each log source in the containers table
    - Feeds lines into the shared log stream pipeline
    """

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._on_log_callback: Optional[Callable] = None
        self._source_paths: Dict[str, str] = {}  # source_id -> path

    @staticmethod
    def source_id(path: str) -> str:
        """Generate a stable source ID for a log file path."""
        digest = hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:12]
        return f"file-{digest}"

    @property
    def monitored_count(self) -> int:
        """Number of log files currently being monitored."""
        return len(self._tasks)

    async def start_monitoring(self, on_log_callback: Optional[Callable] = None) -> None:
        """
        Start monitoring all configured log files.

        Args:
            on_log_callback: Async callback with signature
                (source_id, source_name, log_line, detect)
        """
        self._running = True
        self._on_log_callback = on_log_callback

        sources = settings.custom_log_sources
        if not sources:
            logger.info("No custom log files configured; file monitoring disabled.")
            return

        for source in sources:
            source_id = self.source_id(source["path"])
            await self._persist_source(source_id, source)
            task = asyncio.create_task(self._tail_file(source_id, source))
            self._tasks[source_id] = task
            self._source_paths[source_id] = source["path"]
            logger.info(
                f"Started monitoring log file: {source['path']} ({source_id})"
            )
        # Include runtime-configured sources from the DB (added via dashboard)
        try:
            from src.config.runtime import get_setting, KEY_CUSTOM_LOG_FILES

            extra = await get_setting(KEY_CUSTOM_LOG_FILES, [])
            for source in extra if isinstance(extra, list) else []:
                path = str(source.get("path", "")).strip()
                if not path:
                    continue
                source_id = self.source_id(path)
                if source_id in self._tasks:
                    continue
                src = {"path": path, "name": str(source.get("name", "")).strip() or os.path.basename(path)}
                await self._persist_source(source_id, src)
                task = asyncio.create_task(self._tail_file(source_id, src))
                self._tasks[source_id] = task
                self._source_paths[source_id] = path
                logger.info(f"Started runtime log file source: {path} ({source_id})")
        except Exception as e:
            logger.warning(f"Failed to load runtime log sources: {e}")

    async def stop_monitoring(self) -> None:
        """Stop all file monitoring tasks."""
        self._running = False
        for source_id, task in list(self._tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        logger.info("Log file monitoring stopped.")

    def get_active_sources(self) -> Dict[str, str]:
        """Return {path: source_id} for currently monitored log files."""
        return {path: sid for sid, path in self._source_paths.items()}

    async def add_source(self, path: str, name: str = "") -> dict:
        """Start monitoring a new log file at runtime."""
        path = path.strip()
        if not path:
            raise ValueError("Log file path cannot be empty")
        source_id = self.source_id(path)
        if source_id in self._tasks:
            return {"source_id": source_id, "path": path, "already_monitored": True}

        source = {"path": path, "name": name.strip() or os.path.basename(path)}
        if not self._running:
            self._running = True
        await self._persist_source(source_id, source)
        task = asyncio.create_task(self._tail_file(source_id, source))
        self._tasks[source_id] = task
        self._source_paths[source_id] = path
        logger.info(f"Added log file source at runtime: {path} ({source_id})")
        return {"source_id": source_id, "path": path, "already_monitored": False}

    async def remove_source(self, source_id: str) -> bool:
        """Stop monitoring a log file at runtime."""
        task = self._tasks.pop(source_id, None)
        self._source_paths.pop(source_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            logger.info(f"Removed log file source: {source_id}")

        # Mark the source as no longer monitored in the DB
        async with async_session_factory() as session:
            try:
                from sqlalchemy import select

                result = await session.execute(
                    select(ContainerModel).where(
                        ContainerModel.container_id == source_id
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.is_monitored = False
                    existing.state = "removed"
                    await session.commit()
            except Exception as e:
                logger.warning(f"Error updating removed log source: {e}")
        return True

    async def _tail_file(self, source_id: str, source: Dict[str, str]) -> None:
        """Continuously read new lines from a log file."""
        path = source["path"]
        name = source["name"]
        position = 0
        pending = ""  # holds a partial line (no trailing newline) across polls
        last_inode: Optional[int] = None
        first_run = True

        while self._running:
            try:
                if not os.path.exists(path):
                    logger.debug(f"Log file not found, waiting: {path}")
                    await asyncio.sleep(settings.LOG_FILE_POLL_INTERVAL)
                    continue

                stat = os.stat(path)
                inode = stat.st_ino
                size = stat.st_size

                # Detect rotation (new inode) or in-place truncation
                if last_inode is not None and (inode != last_inode or size < position):
                    logger.info(f"Log file rotated/truncated, restarting: {path}")
                    position = 0
                    pending = ""
                last_inode = inode

                if first_run:
                    # Seed buffer with the last N lines for RCA context
                    position = self._tail_start_position(path, settings.LOG_FILE_TAIL_LINES)
                    data = self._read_bytes(path, position, size)
                    seed_lines = self._split_lines(data)
                    # A trailing partial line (no newline) is kept pending and
                    # only dispatched once it completes with a newline.
                    for line in seed_lines[:-1] if seed_lines else []:
                        if self._on_log_callback:
                            await self._on_log_callback(
                                source_id, name, line, detect=False
                            )
                    # Only the last element is a partial line if no trailing newline;
                    # a trailing empty artifact from a final newline is skipped.
                    if seed_lines and not data.endswith(b"\n"):
                        pending = seed_lines[-1]
                    elif seed_lines and seed_lines[-1]:
                        if self._on_log_callback:
                            await self._on_log_callback(
                                source_id, name, seed_lines[-1], detect=False
                            )
                    position = size
                    first_run = False

                if size > position:
                    data = self._read_bytes(path, position, size)
                    lines = self._split_lines(data)
                    # Prepend the held partial line to the first chunk
                    if pending:
                        if lines:
                            lines[0] = pending + lines[0]
                        else:
                            lines = [pending]
                        pending = ""
                    # Dispatch all complete lines; keep a trailing partial line
                    for line in lines[:-1]:
                        if self._on_log_callback:
                            await self._on_log_callback(
                                source_id, name, line, detect=True
                            )
                    if lines and not data.endswith(b"\n"):
                        pending = lines[-1]
                    elif lines and lines[-1]:
                        if self._on_log_callback:
                            await self._on_log_callback(
                                source_id, name, lines[-1], detect=True
                            )
                    position = size

                await asyncio.sleep(settings.LOG_FILE_POLL_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error tailing log file {path}: {e}")
                await asyncio.sleep(settings.LOG_FILE_POLL_INTERVAL)

    @staticmethod
    def _read_bytes(path: str, start: int, end: int) -> bytes:
        """Read raw bytes from byte range [start, end) of a file."""
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                return fh.read(end - start)
        except Exception as e:
            logger.warning(f"Error reading log file {path}: {e}")
            return b""

    @staticmethod
    def _split_lines(data: bytes) -> List[str]:
        """Split raw bytes into decoded lines on newline boundaries."""
        if not data:
            return []
        text = data.decode("utf-8", errors="replace")
        return text.split("\n")

    @staticmethod
    def _tail_start_position(path: str, lines: int) -> int:
        """
        Find the byte offset that starts the last `lines` lines of a file.

        Only ever scans a bounded window (the last 1MB) so that large log
        files are not fully read into memory on startup.
        """
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size == 0:
                    return 0

                chunk_size = min(size, 1024 * 1024)
                fh.seek(size - chunk_size)
                data = fh.read(chunk_size)

                newline_positions = [i for i, b in enumerate(data) if b == 0x0A]
                if len(newline_positions) <= lines:
                    # Window has too few lines; seed from the window start so we
                    # never read an entire huge file. Whole-file read only for
                    # files small enough to fit in the window.
                    return 0 if chunk_size == size else size - chunk_size

                target = newline_positions[-lines - 1] + 1
                return size - chunk_size + target
        except Exception:
            return 0

    async def _persist_source(self, source_id: str, source: Dict[str, str]) -> None:
        """
        Register the log file source in the containers table.

        Required so alerts/analyses referencing this source satisfy the
        foreign key constraint and so the source shows up on the dashboard.
        """
        async with async_session_factory() as session:
            try:
                from sqlalchemy import select

                result = await session.execute(
                    select(ContainerModel).where(
                        ContainerModel.container_id == source_id
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.name = source["name"]
                    existing.state = "running"
                    existing.status = "monitoring log file"
                    existing.is_monitored = True
                    existing.last_seen = datetime.utcnow()
                else:
                    session.add(
                        ContainerModel(
                            container_id=source_id,
                            name=source["name"],
                            image="file-log",
                            state="running",
                            status="monitoring log file",
                            log_driver="file",
                            labels={"source": "file", "path": source["path"]},
                            hostname=os.uname().nodename,
                        )
                    )
                await session.commit()
            except Exception as e:
                logger.warning(f"Error persisting log source {source['path']}: {e}")
                await session.rollback()

