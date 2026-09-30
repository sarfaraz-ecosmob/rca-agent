# AI RCA Agent - Root Cause Analysis for Docker Containers

Enterprise-grade AI-powered Root Cause Analysis (RCA) Agent that continuously monitors Docker container logs, detects errors in real time, analyzes root causes using AI, and automatically notifies DevOps teams.

## Architecture

```
                Docker Host
                     |
     ┌───────────────┼────────────────┐
     │               │                │
Container A     Container B      Container C
     │               │                │
     └───────────────┼────────────────┘
                     |
        Docker Socket (read-only mount)
                     |
            AI RCA Agent Container
                     |
         ┌──────────┼──────────┐
         |          |          |
    PostgreSQL   AI Provider   Notifications
    (persistence)  (optional)      |
         |                   ┌─────┴─────┐
         └───────────────────┤ Google Chat│
                             │   Email    │
                             └───────────┘
```

The compose stack consists of **three services**:

| Service | Purpose |
|---------|---------|
| `rcagent` | The AI RCA Agent (FastAPI, port 8000) — monitors Docker, detects errors, runs RCA, notifies |
| `postgres` | PostgreSQL 16 database (internal only, not published) — stores containers, alerts, incidents, settings |
| `error-generator` | Test-only container (compose profile `test`) that emits realistic errors to exercise the pipeline |

## How It Works

### Container Log Monitoring Pipeline

```
Docker daemon (socket)
        │
   DockerMonitor                     ← src/monitor/docker_monitor.py
        │
  handle_log_line                    ← src/main.py
        │
 LogStreamManager                    ← src/monitor/log_stream.py (ring buffer, RCA context)
        │
  ErrorDetector                      ← src/detector/error_detector.py (80+ regex patterns)
        │
     ├─► PostgreSQL (alert saved) + WebSocket (/ws, dashboard live updates)
     │
     └─► AnalysisQueue (critical/high only) → RCAAnalyzer → AI provider
                │
                ├─► RCA incident saved to PostgreSQL
                ├─► Google Chat card + Email (SMTP)
                └─► WebSocket broadcast to dashboard
```

### 1. Connecting to Docker

- `DockerMonitor` connects via the Docker socket (`unix:///var/run/docker.sock`, or
  `DOCKER_HOST` for remote hosts such as `tcp://remote-host:2375`) using **aiodocker**.
- A custom `aiohttp` session is built with **no total timeout** and an **unlimited
  connection pool** (`limit=0`), so long-lived `follow=True` log streams are never killed
  by aiohttp's default 5-minute timeout, and leaked streams never exhaust the pool.

### 2. Container Discovery — two mechanisms in parallel

| Mechanism | What it does |
|-----------|--------------|
| **Periodic scan** (`_scan_loop`) | Every `CONTAINER_SCAN_INTERVAL` seconds (default 5s) lists all containers. Containers in `running`/`restarting` state that are not yet monitored are started; exited/removed ones are torn down. |
| **Docker events listener** (`_event_listener`) | Subscribes to the Docker events stream. On `start`/`create`/`restart` it triggers an immediate scan (no waiting for the periodic interval); on `die`/`stop`/`destroy`/`kill` it stops monitoring that container right away. |

**So yes — when any new container is started on the host, the agent detects it
automatically (event + scan, whichever fires first) and starts streaming its logs
within seconds. No configuration or restart of the monitored container is required.**

When a monitored container stops or is recreated (same name, new ID), its log stream is
torn down cleanly, the DB record is marked `removed`, and the new container is picked up.

### 3. Log Streaming — per-container asyncio task

Each monitored container gets its own asyncio task that streams logs to the callback
`handle_log_line`. Two strategies, chosen by the container's logging driver:

- **`json-file` driver** (default) — real-time streaming:
  `container.log(follow=True, stdout=True, stderr=True, timestamps=True, tail=10)`
- **Other drivers (`journald`, `syslog`, ...)** — polling fallback:
  `container.log(since=<unix ts>, tail=100)` every `LOG_POLL_INTERVAL` seconds,
  because `follow` is not reliable for those drivers

Both stdout and stderr are captured.

### 4. Lifecycle & Resilience

- If a stream ends (container exited, crashed, removed), the task re-checks container
  state, fires the container-stop callback, and tears down cleanly — leaked follow
  streams would otherwise exhaust the shared Docker connection pool for the whole process.
- Interrupted streams reconnect automatically after 2 seconds; Docker 404/409 responses
  (container gone/stopped) are treated as normal lifecycle ends.
- If the Docker events stream drops, it reconnects after a 5-second backoff; the periodic
  scan acts as the safety net for anything missed meanwhile.
- Containers are persisted to PostgreSQL (upsert), so the dashboard lists containers
  across agent restarts.

## Features

### Container Monitoring
- Auto-discovers new containers (Docker events + 5s periodic scan)
- Real-time log streaming (json-file, journald, syslog)
- Supports stdout/stderr
- No container restart required
- Handles container lifecycle (start/stop/restart/recreate)

### Error Detection
- **Application Errors:** Exception, Error, Fatal, Panic, Traceback, Segfault, NPE, StackOverflow
- **Infrastructure Errors:** Connection refused, Timeout, DNS failure, Disk full, OOM, Permission denied
- **Database Errors:** MySQL, MariaDB, PostgreSQL, MongoDB, Redis, Elasticsearch
- **Container Errors:** CrashLoop, Restart, Exit code, Signal terminated, Health check failures
- **Web Server Errors:** Nginx, Apache, Node.js, Python, Java, Go, PHP

### AI-Powered RCA
- Multiple AI providers: Ollama, LM Studio, OpenAI, **OpenRouter** (free models), Azure OpenAI, Anthropic, Google Gemini
- Reads surrounding logs (200 lines before, 50 lines after)
- Identifies root cause, severity, probability, and impacted components
- Suggests immediate fix, permanent fix, preventive actions
- **Environment-aware remediation:** set `ENVIRONMENT_TYPE=docker-compose|docker|kubernetes|vm|bare-metal` and the AI only suggests commands that exist in your deployment (no `kubectl` in Docker environments, etc.)
- Generates structured RCA reports (JSON, Markdown)
- Confidence scoring
- Serial analysis queue: AI requests are processed one at a time with a configurable gap
  (`AI_ANALYSIS_INTERVAL`), so bursts never hit provider rate limits

### Notifications
- **Google Chat:** rich card with key facts, log snippet, and the full RCA — analysis, possible impact, remediation plan (immediate/permanent/prevention/best practices), references, and the AI provider + confidence
- **Email:** the same complete RCA as styled HTML (plus a plain-text fallback) via SMTP
- Both channels fire for critical/high severity alerts, in parallel
- Deduplication and rate limiting to prevent alert storms

### Dashboard
- Built-in web dashboard served by the FastAPI app (same port 8000)
- Real-time container monitoring
- Alert and incident management
- RCA report viewer with drill-down
- WebSocket-based live updates (`/ws`)
- Search and filtering

## Technology Stack

| Component | Technology |
|-----------|------------|
| Backend | Python FastAPI (uvicorn) |
| Monitoring | aiodocker (Docker Engine API via socket) |
| Database | PostgreSQL 16 (SQLAlchemy async) |
| AI | Ollama / OpenRouter / OpenAI / Anthropic / Gemini / Azure OpenAI / LM Studio |
| WebSocket | FastAPI WebSocket |
| Dashboard | Built-in real-time monitoring UI (REST API + WebSocket) |
| Containerization | Docker Compose |

## Quick Start

### Prerequisites
- Docker and Docker Compose installed
- At least one running Docker container to monitor
- (Optional) AI provider: Ollama running locally or API keys

### 1. Clone and Configure

```bash
git clone <repo-url> ai-rcagent
cd ai-rcagent

# Create a .env file next to docker-compose.yml with your settings
# (AI provider, notifications — see Configuration section below)
```

> All settings have sane defaults; the stack runs out of the box with error
> detection + PostgreSQL, and you can add an AI provider / notifications later.

### 2. Check the Docker group GID (Linux)

The agent accesses the Docker socket through a supplementary group. Check the GID of
the `docker` group on **this** server and make sure `group_add` in
`docker-compose.yml` matches (many servers use `998` or `999`):

```bash
getent group docker | cut -d: -f3
```

```yaml
services:
  rcagent:
    group_add:
      - "998"   # ← must match your server's docker group GID
```

> **macOS / Windows (Docker Desktop):** permissions are managed automatically — skip this.

### 3. Start the Stack

```bash
docker compose up --build -d
```

This starts:
- AI RCA Agent on port 8000 (API + WebSocket + built-in dashboard)
- PostgreSQL 16 database (internal only)

### 4. Verify Installation

```bash
# Check health
curl http://localhost:8000/api/v1/health

# List monitored containers (should include every container on this host)
curl http://localhost:8000/api/v1/containers

# Watch the agent discover containers in its logs
docker compose logs -f rcagent | grep -E "Started monitoring|monitoring started"

# Open the API docs / dashboard
open http://localhost:8000/docs
```

## Testing the Pipeline (Error Generator Container)

The stack ships with a test error-generator container (compose profile `test`)
that produces realistic CRITICAL/HIGH errors so the full pipeline can be
exercised end-to-end:

```
error-generator -> DockerMonitor -> ErrorDetector -> RCA (AI provider)
                -> Google Chat card
```

One-command E2E test (builds the stack if needed, generates errors, waits for
the AI RCA and verifies alerts + incident + Google Chat send):

```bash
./scripts/test_container_error_pipeline.sh
```

Or drive the generator manually:

```bash
# One error burst (12 distinct errors), then the container exits
docker compose --profile test run --rm error-generator

# Continuous bursts every 5 minutes (stress/demo mode)
ERROR_MODE=loop BURST_INTERVAL_SECONDS=300 docker compose --profile test up error-generator
```

Tunables (env): `ERROR_INTERVAL_SECONDS` (gap between lines, default 1.5),
`ERROR_BURSTS` (bursts per run, default 1), `BURST_INTERVAL_SECONDS` (loop mode).

Notes:
- Each run creates a uniquely-named container so alert deduplication (300s per
  pattern) never suppresses a fresh test.
- Free-tier AI models can be slow/rate-limited; the script waits up to 120s
  by default (`WAIT=180 ./scripts/test_container_error_pipeline.sh` to extend).
- Verify results: `curl http://localhost:8000/api/v1/incidents?limit=1` (check
  `ai_provider`) and your Google Chat space for the RCA card.

## Configuration

Key environment variables (set them in a `.env` file next to `docker-compose.yml`):

```bash
# AI Provider (ollama, lm_studio, openai, openrouter, anthropic, google_gemini, azure_openai)
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
# Ollama (local alternative)
# OLLAMA_URL=http://host.docker.internal:11434
# OLLAMA_MODEL=gemma:2b

# Deployment environment — controls which remediation commands the AI suggests
# (docker-compose | docker | kubernetes | vm | bare-metal | other)
ENVIRONMENT_TYPE=docker-compose

# Notifications
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/...

# Email (SMTP) — enabled when SMTP_SERVER + SMTP_USER are set
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true              # false for SSL-on-connect (port 465)
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-16-char-app-password   # Gmail: Account > Security > App passwords
EMAIL_FROM=your-email@gmail.com
EMAIL_TO=devops@company.com    # comma-separated for multiple recipients

# Database (defaults match docker-compose.yml; change POSTGRES_PASSWORD in production)
POSTGRES_PASSWORD=rcagent_secret

# Monitoring (defaults are fine)
SCAN_INTERVAL=5                # generic scan interval (seconds)
CONTAINER_SCAN_INTERVAL=5      # container discovery scan interval (seconds)
LOG_POLL_INTERVAL=1.0          # polling for non-json-file logging drivers (seconds)
MAX_LOG_LINES=200              # log context captured before the error
RETENTION_DAYS=30
DEDUPLICATION_WINDOW=300       # suppress duplicate alerts per pattern (seconds)

# Remote Docker host (optional — only if NOT using the local socket)
# DOCKER_HOST=tcp://remote-host:2375

# Custom log file monitoring (non-Docker applications)
# CUSTOM_LOG_FILES=/host-logs/app.log
# CUSTOM_LOG_NAMES=myapp
```

> **Gmail users:** use an App Password (Google Account → Security → 2-Step
> Verification → App passwords), not your login password. Any provider with an
> SMTP interface (Brevo, SendGrid, Mailgun, ...) works the same way.

## Deploying on a New Server

The setup is essentially **copy-and-run**; container auto-discovery needs **zero
configuration**. Checklist:

1. **Install Docker Engine + Compose plugin** on the new server.

2. **Fix the Docker group GID** — `group_add: ["998"]` in `docker-compose.yml` must
   match the server's docker group GID:
   ```bash
   getent group docker | cut -d: -f3
   ```
   A mismatch is the #1 cause of `Permission denied` on the Docker socket.

3. **Create `.env`** with your AI provider credentials and a production
   `POSTGRES_PASSWORD` (see [Configuration](#configuration)).

4. **Optional:** copy a customized `config/` directory (settings defaults live in
   `config/settings.py` and are mounted read-only into the container).

5. **Start and verify:**
   ```bash
   docker compose up --build -d
   curl http://localhost:8000/api/v1/health
   curl http://localhost:8000/api/v1/containers   # should list all containers on this host
   ```

6. **Open port 8000** in the server's firewall if the dashboard/API must be reachable
   from outside.

Things to know about the new server:

- The agent mounts the **host's** Docker socket, so it monitors **all containers
  running on that server** — any other workloads there are picked up automatically.
- **Monitoring a different host remotely:** instead of mounting a socket, set
  `DOCKER_HOST=tcp://<host>:2375` (the code falls back to a TCP connector). The remote
  Docker daemon must expose its API, ideally protected (TLS / network restrictions) —
  never expose it publicly unauthenticated.
- **Postgres is internal-only** (no published port); data persists in the
  `postgres_data` volume. Back up that volume in production.
- **Non-Docker apps:** use `CUSTOM_LOG_FILES` with the host log dir mounted at
  `/host-logs` (see [Monitoring Non-Docker Applications](#monitoring-non-docker-applications)).
- **Ollama users:** run Ollama on the host; `docker-compose.yml` already maps
  `host.docker.internal` to the host gateway (`extra_hosts: host-gateway`).

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/containers` | List monitored containers |
| GET | `/api/v1/containers/{id}` | Get container details |
| GET | `/api/v1/alerts` | List alerts (with filters) |
| PATCH | `/api/v1/alerts/{id}/status` | Update alert status |
| GET | `/api/v1/incidents` | List RCA incidents |
| POST | `/api/v1/analyse` | Trigger manual analysis |
| GET | `/api/v1/statistics` | Dashboard statistics |
| POST | `/api/v1/feedback` | Submit feedback on RCA |
| GET | `/api/v1/notifications` | Notification history |
| GET | `/api/v1/knowledge-base` | Knowledge base entries |
| GET | `/api/v1/health` | Health check |
| WS | `/ws` | WebSocket for real-time updates |
| GET | `/docs` | Interactive Swagger UI |

## Error Detection Patterns

The agent detects errors across five categories with 80+ specific patterns:

- **Application** (19 patterns): exceptions, errors, panics, tracebacks
- **Infrastructure** (15 patterns): connection issues, timeouts, OOM, disk full
- **Database** (18 patterns): connection errors, deadlocks, replication issues
- **Container** (8 patterns): crash loops, OOM, health check failures
- **Web Server** (20+ patterns): HTTP errors, worker failures, language-specific errors

## Security

- Docker socket mounted read-only (`:ro`)
- Runs as non-root user (`rcagent`), capabilities dropped except `NET_BIND_SERVICE`
- `no-new-privileges` enabled
- API keys stored in environment variables / Docker secrets
- Postgres not exposed to the host (internal network only)
- Rate limiting / deduplication on alerts to prevent storms
- CORS configurable (`CORS_ORIGINS`)

## Deployment Options

### Option 1: Docker Compose (Recommended)
```bash
docker compose up --build -d
```

### Option 2: Direct Installation
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Export the environment variables from the Configuration section
python -m src.main
```

> Direct installation requires a reachable Docker socket and a PostgreSQL
> server — set `POSTGRES_HOST` etc. accordingly.

### Option 3: Kubernetes
Deploy using the provided Docker image with appropriate manifests (mount the
node's Docker/containerd socket or set `DOCKER_HOST` to a reachable Engine API).

## Monitoring

The built-in dashboard (served by the agent itself on port 8000) provides real-time views for:
- Monitored containers and log file sources
- Alerts and incidents (RCA reports)
- Error categories and statistics
- Live updates over WebSocket (`/ws`)

## Monitoring Non-Docker Applications

The agent can also monitor applications that run **outside** Docker by
reading their log files in real-time. Configure the log file paths in `.env`:

```bash
CUSTOM_LOG_FILES=/host-logs/myapp/app.log,/host-logs/myapp/error.log
CUSTOM_LOG_NAMES=myapp,myapp-errors
```

Each configured file is tailed (`tail -f` style) and fed through the same error
detection, RCA, and notification pipeline as container logs. When running the
agent via Docker Compose, mount the host log directory into the container:

```yaml
volumes:
  - /var/log/myapp:/host-logs/myapp:ro
```

(Compose already mounts `${CUSTOM_LOG_DIR:-./logs}:/host-logs:ro` — point
`CUSTOM_LOG_FILES` at paths under `/host-logs`.)

Notes: existing lines are seeded into the buffer (no alerts); only **new** lines
appended after startup trigger detection/notifications.

## Development

```bash
# Backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.main

# Tests
pytest
```

## License

MIT License - see LICENSE file for details.
