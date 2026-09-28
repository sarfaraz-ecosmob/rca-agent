#!/usr/bin/env python3
"""
Test Error Generator for the AI RCA Agent.

Emits realistic CRITICAL/HIGH error lines to stdout. When run as a Docker
container, the agent's DockerMonitor streams these lines from the Docker
logging driver, the ErrorDetector matches them against its patterns, an
RCA analysis is triggered via OpenRouter, and the result is sent to
Google Chat.

Modes:
  burst   (default) print a finite set of error lines, spaced by
          ERROR_INTERVAL_SECONDS, then exit. Used by the E2E test script
          so every run produces fresh, non-deduplicated alerts.
  loop    print error bursts forever (one burst every BURST_INTERVAL_SECONDS).
          Useful for a long-running demo/stress container.

Design notes:
- Every line matches a distinct CRITICAL or HIGH pattern from
  src/detector/error_patterns.py, so each line yields its own alert
  (dedup is per container+pattern for DEDUPLICATION_WINDOW seconds).
- Lines carry a unique run tag (container name + run id) so repeat runs
  are never suppressed as duplicates and are easy to spot in the report.
- Occasional INFO/normal lines are interleaved to mimic real log output.
"""

from __future__ import annotations

import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

MODE = os.environ.get("ERROR_MODE", "burst").strip().lower()          # burst | loop
INTERVAL = float(os.environ.get("ERROR_INTERVAL_SECONDS", "1.5"))       # gap between lines in a burst
BURST_INTERVAL = float(os.environ.get("BURST_INTERVAL_SECONDS", "300")) # gap between bursts in loop mode
BURSTS = int(os.environ.get("ERROR_BURSTS", "1"))                       # bursts per run (burst mode)

CONTAINER_NAME = os.environ.get("CONTAINER_NAME", os.uname().nodename)
RUN_ID = uuid.uuid4().hex[:8]
TAG = f"rca-test-{RUN_ID}"


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def emit(line: str, stream: str = "stdout") -> None:
    """Print a line to the chosen stream and flush immediately."""
    out = sys.stderr if stream == "stderr" else sys.stdout
    print(line, file=out, flush=True)


def error_lines() -> list[str]:
    """Error lines for one burst. Each matches a different agent pattern."""
    return [
        # CRITICAL: unhandled_exception pattern
        f"Unhandled exception in payment-worker: ConnectionRefusedError: connection refused by postgres:5432 [{TAG}]",
        # CRITICAL: postgresql_connection (longer match beats connection_refused)
        f"postgres error: could not connect to server: Connection refused (attempt 3/3) [{TAG}]",
        # CRITICAL: fatal
        f"FATAL: payment service crashed, worker exiting [{TAG}]",
        # CRITICAL: out_of_memory
        f"java.lang.OutOfMemoryError: Java heap space while processing batch 42 [{TAG}]",
        # CRITICAL: segmentation_fault
        f"Segmentation fault (core dumped) in image-resizer pid 1337 [{TAG}]",
        # CRITICAL: abort
        f"ABORTING: too many corrupted chunks in stream, giving up [{TAG}]",
        # HIGH: traceback
        f"Traceback (most recent call last): File 'app.py', line 99, in handle_request [{TAG}]",
        # HIGH: connection_reset
        f"Connection reset by peer while uploading artifact to s3 bucket [{TAG}]",
        # HIGH: dns_failure
        f"DNS resolution failed: NameOrServiceNotKnown for api.internal.svc.cluster.local [{TAG}]",
        # HIGH: mysql_deadlock
        f"MySQLDeadlockFound: deadlock detected on table orders, transaction rolled back [{TAG}]",
        # HIGH: crash_loop
        f"Back-off restarting failed container, crash loop detected (restart #7) [{TAG}]",
        # HIGH: health_check_failure
        f"Health check failed: liveness probe timeout after 3 retries, marking unhealthy [{TAG}]",
    ]


def normal_lines() -> list[str]:
    return [
        f"INFO: heartbeat ok, queue depth=0",
        f"INFO: cache warmed up in 120ms",
        f"INFO: scheduled job 'cleanup' finished successfully",
    ]


def run_burst() -> None:
    print(f"=== error-generator burst start | run={RUN_ID} | container={CONTAINER_NAME} | mode={MODE} ===", flush=True)

    for line in error_lines():
        # Route a couple of lines to stderr like a real app would
        stream = "stderr" if "Unhandled exception" in line or "Traceback" in line else "stdout"
        emit(f"{ts()} {line}", stream=stream)
        time.sleep(INTERVAL)
        # Interleave a normal line occasionally for realism
        if random.random() < 0.3:
            emit(f"{ts()} {random.choice(normal_lines())}")

    print(f"=== error-generator burst done   | run={RUN_ID} | 12 error lines emitted ===", flush=True)


def main() -> None:
    if MODE == "loop":
        print(f"error-generator: loop mode, one burst every {BURST_INTERVAL}s (run={RUN_ID})", flush=True)
        while True:
            run_burst()
            time.sleep(BURST_INTERVAL)
    else:
        for _ in range(max(1, BURSTS)):
            run_burst()
            if BURSTS > 1:
                time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
