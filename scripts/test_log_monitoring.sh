#!/usr/bin/env bash
# =============================================================================
# test_log_monitoring.sh
#
# End-to-end test for custom log file monitoring (non-Docker applications).
#
# What it does:
#   1. Verifies the RCA agent API is reachable
#   2. Records the current alert count
#   3. Appends several distinct CRITICAL/HIGH error lines to logs/app.log
#      over a few seconds (to exercise real-time tailing)
#   4. Waits for detection -> RCA analysis -> notifications
#   5. Verifies new alerts via the REST API
#   6. Greps the agent container logs for the pipeline markers
#
# Usage:
#   ./scripts/test_log_monitoring.sh
#
# Env overrides:
#   API      - agent base URL (default http://localhost:8000)
#   LOG_FILE - log file to append to (default logs/app.log)
#   WAIT     - seconds to wait after appending (default 15)
# =============================================================================
set -euo pipefail

# Make the script location-independent (run from anywhere)
cd "$(dirname "$0")/.."

API="${API:-http://localhost:8000}"
LOG_FILE="${LOG_FILE:-logs/app.log}"
WAIT="${WAIT:-15}"
RUN_ID="$(date +%s)"
GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[0;33m'
NC=$'\033[0m'

pass() { echo "${GREEN}✔ $1${NC}"; }
fail() { echo "${RED}✘ $1${NC}"; }
warn() { echo "${YELLOW}! $1${NC}"; }

echo "==========================================================="
echo "  RCA Agent - Custom Log Monitoring End-to-End Test"
echo "  Run ID: ${RUN_ID}"
echo "==========================================================="
warn "NOTE: dedup is per error pattern for 300s. Re-running within 5 min"
warn "      may report 0 new alerts. Wait 5 min between runs."
echo

# --- 1. Health check -----------------------------------------------------
if curl -sf "${API}/api/v1/health" > /dev/null 2>&1; then
    pass "Agent API is reachable at ${API}"
else
    fail "Agent API not reachable at ${API}"
    echo
    echo "Start the stack first:"
    echo "    docker compose up -d --build"
    exit 1
fi

# --- Helper: count alerts created in THIS test window --------------------
# NOTE: the alerts API caps limit at 200 (limit: le=200); requesting more
# returns a 422 and a silent zero count. Filter on the test start time instead.
TEST_START="$(date -u '+%Y-%m-%dT%H:%M:%S')"

count_alerts() {
    curl -sf "${API}/api/v1/alerts?from_timestamp=${TEST_START}&limit=200" 2>/dev/null \
        | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))' 2>/dev/null \
        || echo "0"
}

ALERTS_BEFORE="$(count_alerts)"
echo
echo "Alerts before test: ${ALERTS_BEFORE}"

# --- 2. Append test error lines ------------------------------------------
echo
echo "Appending critical/high error lines to ${LOG_FILE} ..."

# Distinct patterns so each line triggers a separate alert (dedup is per pattern).
# NOTE: avoid bare words like "error"/"exception" so the intended pattern wins
# (patterns are matched in list order; `error`/`exception` come before `panic`/`fatal`).
TEST_LINES=(
    "FATAL: payment service crashed (test ${RUN_ID})"
    "ConnectionRefusedError: connection refused by postgres:5432 (test ${RUN_ID})"
    "panic: index out of range (test ${RUN_ID})"
    "OutOfMemoryError: Java heap space (test ${RUN_ID})"
    "ENOSPC: no space left on device (test ${RUN_ID})"
    "deadlock detected on table users (test ${RUN_ID})"
)

if [ ! -w "${LOG_FILE}" ]; then
    warn "Log file not writable: ${LOG_FILE} (creating it)"
    mkdir -p "$(dirname "${LOG_FILE}")"
    touch "${LOG_FILE}"
fi

for line in "${TEST_LINES[@]}"; do
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    printf '%s [CRITICAL] %s\n' "${ts}" "${line}" >> "${LOG_FILE}"
    echo "    + ${line}"
    sleep 2   # space appends so each is picked up as a separate poll
done

# --- 3. Wait for the pipeline --------------------------------------------
echo
echo "Waiting ${WAIT}s for detection -> RCA analysis -> notifications ..."
sleep "${WAIT}"

# --- 4. Verify via API ----------------------------------------------------
ALERTS_AFTER="$(count_alerts)"
NEW_ALERTS="${ALERTS_AFTER}"

echo
echo "New alerts since ${TEST_START}: ${ALERTS_AFTER}"

if [ "${NEW_ALERTS}" -ge 1 ]; then
    pass "Detected ${NEW_ALERTS} new alert(s)"
else
    fail "No new alerts detected"
    warn "Possible causes:"
    warn "  1. Agent not running with CUSTOM_LOG_FILES configured"
    warn "  2. logs/ not mounted into the container (check docker-compose.yml)"
    warn "  3. Lines already deduplicated (same patterns within 300s window)"
    warn "  4. Agent container needs rebuild (docker compose up -d --build)"
fi

echo
echo "Latest 6 alerts from API:"
curl -sf "${API}/api/v1/alerts?limit=6" 2>/dev/null \
    | python3 -c '
import sys, json
try:
    alerts = json.load(sys.stdin)
except Exception:
    alerts = []
for a in alerts:
    aid = a.get("id")
    sev = a.get("severity")
    pat = a.get("match_pattern")
    cname = a.get("container_name")
    print("    #%-5d [%-8s] %-28s %s" % (aid, sev, pat, cname))
' 2>/dev/null || echo "    (could not parse alerts response)"

# --- 5. Check agent logs ---------------------------------------------------
echo
echo "Agent container logs (last 2 minutes, pipeline markers):"
if docker compose ps rcagent > /dev/null 2>&1; then
    docker compose logs rcagent --since=2m 2>/dev/null \
        | grep -Ei "Error detected|queued for AI analysis|RCA analysis completed|alert sent" \
        | tail -20 \
        || echo "    (no matching log lines found)"
else
    warn "docker compose not available or rcagent container not running"
fi

echo
echo "==========================================================="
echo "  DONE - check your Google Chat / email for notification cards."
echo "  Full pipeline: log line -> ErrorDetector -> Alert -> RCA -> notifier"
echo "==========================================================="
