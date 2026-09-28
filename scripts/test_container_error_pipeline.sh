#!/usr/bin/env bash
# =============================================================================
# test_container_error_pipeline.sh
#
# End-to-end test for the FULL container pipeline:
#
#   error-generator container
#        -> DockerMonitor (log streaming via Docker socket)
#        -> ErrorDetector (pattern matching, alert creation)
#        -> RCA analysis  (OpenRouter API, AI_PROVIDER=openrouter)
#        -> Google Chat notification (webhook)
#
# What it does:
#   1. Verifies the RCA agent API is healthy and OpenRouter + Google Chat
#      are configured (via /api/v1/health)
#   2. Starts a temporary error-generator container (unique name per run,
#      so dedup windows never suppress the test)
#   3. Waits for detection -> OpenRouter RCA analysis -> Google Chat send
#   4. Verifies new alerts AND a new RCA incident via the REST API
#   5. Greps the agent container logs for the pipeline markers
#
# Usage:
#   ./scripts/test_container_error_pipeline.sh
#
# Env overrides:
#   API      - agent base URL (default http://localhost:8000)
#   WAIT     - seconds to wait for the AI pipeline (default 120; OpenRouter
#              free models can be slow)
#   PROFILE  - docker compose profile to use (default test)
# =============================================================================
set -euo pipefail

# Make the script location-independent (run from anywhere)
cd "$(dirname "$0")/.."

API="${API:-http://localhost:8000}"
WAIT="${WAIT:-120}"
PROFILE="${PROFILE:-test}"
RUN_ID="$(date +%s)"
GEN_NAME="rca-error-generator-${RUN_ID}"
GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[0;33m'
NC=$'\033[0m'

pass() { echo "${GREEN}✔ $1${NC}"; }
fail() { echo "${RED}✘ $1${NC}"; }
warn() { echo "${YELLOW}! $1${NC}"; }

cleanup() {
    # Remove the generator container if it is still around
    docker rm -f "$GEN_NAME" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==========================================================="
echo "  RCA Agent - Container Error Pipeline End-to-End Test"
echo "  Run ID: ${RUN_ID}"
echo "==========================================================="
echo "  Pipeline: generator -> DockerMonitor -> ErrorDetector"
echo "            -> OpenRouter RCA -> Google Chat"
echo "==========================================================="
echo

# --- 1. Health check -------------------------------------------------------
HEALTH_JSON="$(curl -sf "${API}/api/v1/health" 2>/dev/null || true)"
if [ -n "${HEALTH_JSON}" ]; then
    pass "Agent API is reachable at ${API}"
else
    fail "Agent API not reachable at ${API}"
    echo
    echo "Start the stack first:"
    echo "    docker compose up -d --build"
    exit 1
fi

# Precondition checks: OpenRouter + Google Chat must be configured
AI_OK="$(printf '%s' "${HEALTH_JSON}" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("ai_provider_configured", False))' 2>/dev/null || echo False)"
CHAT_OK="$(printf '%s' "${HEALTH_JSON}" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("notifications_configured", {}).get("google_chat", False))' 2>/dev/null || echo False)"

if [ "${AI_OK}" != "True" ]; then
    fail "AI provider (openrouter) is not configured - RCA would be skipped"
    echo "    Check OPENROUTER_API_KEY / AI_PROVIDER in .env and rebuild:"
    echo "    docker compose up -d --build rcagent"
    exit 1
fi
pass "AI provider configured (openrouter via OPENROUTER_API_KEY)"

if [ "${CHAT_OK}" != "True" ]; then
    warn "Google Chat webhook not configured - RCA will run but no Chat card will be sent"
    warn "Set GOOGLE_CHAT_WEBHOOK_URL in .env and rebuild the rcagent container"
fi

# --- Helpers: count alerts/incidents created in THIS test window -----------
# NOTE: the alerts API caps limit at 200 (limit: le=200), so requesting more
# returns a 422 and a silent zero count. We avoid pagination entirely by
# filtering on the test start time (from_timestamp) instead.
TEST_START="$(date -u '+%Y-%m-%dT%H:%M:%S')"

count_alerts() {
    curl -sf "${API}/api/v1/alerts?from_timestamp=${TEST_START}&limit=200" 2>/dev/null \
        | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))' 2>/dev/null \
        || echo "0"
}

count_incidents() {
    curl -sf "${API}/api/v1/incidents?from_timestamp=${TEST_START}&limit=200" 2>/dev/null \
        | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))' 2>/dev/null \
        || echo "0"
}

ALERTS_BEFORE="$(count_alerts)"
INCIDENTS_BEFORE="$(count_incidents)"
echo
echo "Alerts before test:    ${ALERTS_BEFORE}"
echo "Incidents before test: ${INCIDENTS_BEFORE}"

# --- 2. Start the error-generator container --------------------------------
echo
echo "Starting error-generator container '${GEN_NAME}' ..."
# One-shot burst: emits 12 distinct CRITICAL/HIGH error lines then exits.
# The unique container name makes the agent treat this as a brand-new
# container, so dedup (300s per pattern) can never suppress the run.
# --no-deps: never restart/recreate the running agent stack.
docker compose --profile "${PROFILE}" run --rm --no-deps --name "$GEN_NAME" error-generator
pass "Error burst generated (12 error lines: fatal, panic-class, OOM, conn refused, deadlock, ...)"

# --- 3. Wait for the AI pipeline --------------------------------------------
echo
echo "Waiting up to ${WAIT}s for: detection -> OpenRouter RCA -> Google Chat ..."
DETECTED=0
i=0
while [ "$i" -lt "$WAIT" ]; do
    sleep 5
    i=$((i + 5))
    ALERTS_NOW="$(count_alerts)"
    INCIDENTS_NOW="$(count_incidents)"
    NEW_ALERTS=$(( ALERTS_NOW - ALERTS_BEFORE ))
    NEW_INCIDENTS=$(( INCIDENTS_NOW - INCIDENTS_BEFORE ))
    if [ "${NEW_ALERTS}" -ge 1 ] && [ "${NEW_INCIDENTS}" -ge 1 ]; then
        DETECTED=1
        break
    fi
    printf '.'
done
echo

ALERTS_AFTER="$(count_alerts)"
INCIDENTS_AFTER="$(count_incidents)"
NEW_ALERTS=$(( ALERTS_AFTER - ALERTS_BEFORE ))
NEW_INCIDENTS=$(( INCIDENTS_AFTER - INCIDENTS_BEFORE ))

echo
echo "New alerts since ${TEST_START}:    ${ALERTS_AFTER}"
echo "New incidents since ${TEST_START}: ${INCIDENTS_AFTER}"

PIPELINE_OK=1

if [ "${ALERTS_AFTER}" -ge 1 ]; then
    pass "ErrorDetector created ${ALERTS_AFTER} new alert(s)"
else
    PIPELINE_OK=0
    fail "No new alerts detected"
fi

if [ "${INCIDENTS_AFTER}" -ge 1 ]; then
    pass "RCA incident(s) created: ${INCIDENTS_AFTER}"
else
    PIPELINE_OK=0
    fail "No new RCA incidents created"
fi

# --- 4. Show the RCA report (verifies OpenRouter was actually used) --------
echo
echo "Latest RCA incident (from OpenRouter):"
curl -sf "${API}/api/v1/incidents?from_timestamp=${TEST_START}&limit=1" 2>/dev/null | python3 -c '
import sys, json
try:
    incidents = json.load(sys.stdin)
except Exception:
    incidents = []
if not incidents:
    print("    (none)")
    sys.exit(0)
r = incidents[0]
rc = r.get("root_cause") or {}
sol = r.get("solutions") or {}
print("    Incident #%s | provider=%s | confidence=%s" % (
    r.get("analysis_id"), r.get("ai_provider"), r.get("confidence")))
print("    Container : %s" % r.get("container_name"))
print("    Error     : %s [%s]" % (r.get("error_type"), r.get("severity")))
print("    Root cause: %s" % str(rc.get("description", ""))[:220])
print("    Fix       : %s" % str(sol.get("immediate_fix", ""))[:220])
' 2>/dev/null || echo "    (could not parse incidents response)"

# --- 5. Check agent logs for pipeline markers -------------------------------
echo
echo "Agent container logs (last 5 minutes, pipeline markers):"
AGENT_MARKERS="$(docker compose logs rcagent --since=5m 2>/dev/null \
    | grep -Ei "Error detected|Alert queued for AI analysis|Starting RCA analysis|RCA analysis completed|RCA completed|Google Chat alert sent" \
    | tail -15 || true)"
if [ -n "${AGENT_MARKERS}" ]; then
    echo "${AGENT_MARKERS}" | sed 's/^/    /'
else
    warn "(no pipeline markers found in agent logs)"
fi

CHAT_SENT="$(docker compose logs rcagent --since=5m 2>/dev/null \
    | grep -Ec "Google Chat alert sent" || true)"
if [ "${CHAT_SENT}" -ge 1 ]; then
    pass "Google Chat notification sent (${CHAT_SENT} card(s))"
else
    if [ "${CHAT_OK}" = "True" ]; then
        PIPELINE_OK=0
        fail "Google Chat notification NOT sent (webhook configured but no 'alert sent' marker)"
    else
        warn "Google Chat skipped (webhook not configured)"
    fi
fi

# --- 6. Result ----------------------------------------------------------------
echo
echo "==========================================================="
if [ "${PIPELINE_OK}" -eq 1 ]; then
    pass "E2E PIPELINE TEST PASSED"
    echo "  generator -> DockerMonitor -> ErrorDetector -> OpenRouter"
    echo "  RCA -> Google Chat: all stages verified."
    echo "  Check your Google Chat space for the RCA card."
else
    fail "E2E PIPELINE TEST FAILED"
    echo "  Troubleshooting:"
    echo "    1. docker compose logs rcagent --since=10m | grep -Ei 'rca|openrouter|chat'"
    echo "    2. Verify .env: AI_PROVIDER=openrouter, OPENROUTER_API_KEY set,"
    echo "       GOOGLE_CHAT_WEBHOOK_URL set"
    echo "    3. Rebuild after .env changes: docker compose up -d --build rcagent"
    echo "    4. Free OpenRouter models can be rate-limited; retry in a minute"
fi
echo "==========================================================="

exit $(( 1 - PIPELINE_OK ))
