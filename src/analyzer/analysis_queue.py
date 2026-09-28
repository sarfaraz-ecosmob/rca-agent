"""
Serial Analysis Queue for AI RCA Agent.

All AI RCA requests go through a single FIFO queue processed by one worker
coroutine, so concurrent triggers (automated alert bursts, manual /analyse
API calls, dashboard requests) never hit the AI provider in parallel.

Why: hosted AI providers (OpenRouter free tier in particular) enforce strict
rate limits. Parallel analysis calls from an error burst return HTTP 429 and
degrade report quality (fallback boilerplate with confidence 0.0). A serial
queue with a small gap between calls keeps every request under the limit.

Design:
- asyncio.Queue (FIFO) + one worker task started in the application lifespan.
- Producers await `enqueue(...)` which returns the full RCA result — callers
  keep their existing signatures and just await the queue.
- Each job is wrapped in its own asyncio.Task with a timeout so one slow/hung
  AI call cannot stall the whole queue beyond AI_ANALYSIS_TIMEOUT seconds.
- A configurable gap (AI_ANALYSIS_INTERVAL seconds) is enforced AFTER each
  AI call so requests are spaced out, one by one.
- Bounded queue with maxsize: bursts beyond capacity fail fast instead of
  growing unbounded (caller's fallback analysis still produces a report).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class QueueFullError(RuntimeError):
    """Raised when the analysis queue is at capacity."""


class AnalysisQueue:
    """
    Serial FIFO queue for AI RCA analysis jobs.

    Usage (in application lifespan):
        queue = AnalysisQueue(interval_seconds=5)
        await queue.start()
        ...
        await queue.stop()

    Usage (producers):
        result = await queue.enqueue(analyzer.analyze, container_name=...,
                                     container_id=..., log_snippet=..., ...)
    """

    def __init__(
        self,
        interval_seconds: float = 5.0,
        job_timeout_seconds: float = 150.0,
        maxsize: int = 50,
    ):
        self._interval = max(0.0, float(interval_seconds))
        self._job_timeout = max(1.0, float(job_timeout_seconds))
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "processed": 0,
            "failed": 0,
            "timed_out": 0,
            "rejected_full": 0,
            "total_wait_time": 0.0,  # time jobs spent waiting in the queue
        }

    async def start(self) -> None:
        """Start the single worker coroutine."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info(
            f"Analysis queue started | interval={self._interval}s "
            f"timeout={self._job_timeout}s"
        )

    async def stop(self) -> None:
        """Stop the worker. Queued jobs are drained/cancelled on shutdown."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("Analysis queue stopped.")

    async def enqueue(
        self,
        analyze_func: Callable[..., Awaitable[Dict[str, Any]]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Queue an analysis job and await its result.

        Args:
            analyze_func: The coroutine function to execute (e.g.
                RCAAnalyzer.analyze). Called with **kwargs.
            **kwargs: Arguments passed through to analyze_func.

        Returns:
            The RCA analysis result dict from analyze_func.

        Raises:
            QueueFullError: If the queue is at capacity (caller should fall
                back to a direct/fallback analysis path).
        """
        loop = asyncio.get_running_loop()
        queued_at = loop.time()
        future: asyncio.Future = loop.create_future()
        job = {
            "analyze_func": analyze_func,
            "kwargs": kwargs,
            "future": future,
            "queued_at": queued_at,
        }
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._stats["rejected_full"] += 1
            logger.warning(
                "Analysis queue full - rejecting job (caller should use fallback)"
            )
            raise QueueFullError("Analysis queue is full")

        logger.info(
            "Analysis job queued | position=%s", self._queue.qsize()
        )
        return await future

    async def _worker(self) -> None:
        """Process queue jobs strictly one at a time, spacing AI calls out."""
        logger.info("Analysis queue worker started")
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break

            # Future may already be cancelled (caller gave up); skip it.
            if job["future"].done():
                self._queue.task_done()
                continue

            wait_time = loop.time() - job["queued_at"]
            self._stats["total_wait_time"] += wait_time

            job_task = asyncio.create_task(
                job["analyze_func"](**job["kwargs"])
            )
            try:
                result = await asyncio.wait_for(job_task, timeout=self._job_timeout)
                self._stats["processed"] += 1
                if not job["future"].done():
                    job["future"].set_result(result)
            except asyncio.TimeoutError:
                job_task.cancel()
                self._stats["timed_out"] += 1
                logger.error(
                    "Analysis job timed out after %ss | elapsed_queue_wait=%.1fs",
                    self._job_timeout,
                    wait_time,
                )
                if not job["future"].done():
                    job["future"].set_exception(
                        TimeoutError(
                            f"AI analysis timed out after {self._job_timeout}s"
                        )
                    )
            except asyncio.CancelledError:
                job_task.cancel()
                if not job["future"].done():
                    job["future"].cancel()
                self._queue.task_done()
                raise
            except Exception as e:
                self._stats["failed"] += 1
                logger.error("Analysis job failed: %s", e)
                if not job["future"].done():
                    job["future"].set_exception(e)
            finally:
                self._queue.task_done()

            # Gap AFTER each job (including failures) so AI calls stay spaced
            # out and rate limits are respected. Skipped on shutdown.
            if self._interval > 0 and self._running and not self._queue.empty():
                await asyncio.sleep(self._interval)

        logger.info("Analysis queue worker exited")

    @property
    def depth(self) -> int:
        """Number of jobs currently waiting in the queue."""
        return self._queue.qsize()

    def get_stats(self) -> Dict[str, Any]:
        """Queue statistics for monitoring/health endpoints."""
        avg_wait = (
            self._stats["total_wait_time"] / self._stats["processed"]
            if self._stats["processed"]
            else 0.0
        )
        return {
            **self._stats,
            "depth": self.depth,
            "running": self._running,
            "interval_seconds": self._interval,
            "avg_wait_seconds": round(avg_wait, 2),
        }
