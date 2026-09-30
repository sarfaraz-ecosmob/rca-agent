# AI RCA Agent — Complete Setup Guide

> **AI-Powered Root Cause Analysis Agent for Docker Containers**

This guide covers everything you need to deploy, configure, and operate the AI RCA Agent. It works **out of the box** with error detection and Google Chat notifications, and you can add AI-powered root cause analysis later by configuring an AI provider.

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Quick Start](#2-quick-start)
3. [How Container Monitoring Works](#3-how-container-monitoring-works)
4. [Architecture Overview](#4-architecture-overview)
5. [Deploying on a New Server](#5-deploying-on-a-new-server)
6. [Configuration Reference](#6-configuration-reference)
7. [AI Provider Setup](#7-ai-provider-setup)
8. [Notification Setup](#8-notification-setup)
9. [API Reference](#9-api-reference)
10. [Monitoring & Observability](#10-monitoring--observability)
11. [Security Best Practices](#11-security-best-practices)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. System Requirements

### Minimum Hardware

| Resource | Requirement | Notes |
|----------|-------------|-------|
| CPU | 2 cores | 4+ cores recommended for AI analysis |
| RAM | 2 GB | 8 GB+ if running local AI models (Ollama) |
| Disk | 10 GB free | More for log retention + AI models |
| Docker | 24.0+ | With Compose V2 plugin |
| OS | Linux (recommended) | macOS/Windows with Docker Desktop also work |

### Required Software

- **Docker Engine** 24.0+ (with Docker Compose V2)
- **Docker socket** accessible (`/var/run/docker.sock`)
- **Git** (to clone the repository)

### Optional Dependencies

| Dependency | Purpose | Notes |
|------------|---------|-------|
| **Ollama** | Local AI analysis (free) | No API costs, but needs CPU/GPU |
| **Google Chat webhook** | Chat notifications | Free, requires Google Workspace |
| **SMTP server** | Email notifications | Gmail App Password works |
| **OpenAI API key** | Cloud AI analysis | Fast, needs billing setup |
| **OpenRouter API key** | Cloud AI analysis | Access 200+ models, free tier models available |
| **Anthropic API key** | Cloud AI analysis | Fast, needs billing setup |
| **Google Gemini API key** | Cloud AI analysis | Free tier available |

### Ports Used

| Port | Service | Purpose |
|------|---------|---------|
| 8000 | RCA Agent API | REST API + WebSocket + built-in dashboard |

> PostgreSQL (5432) runs on the internal compose network only and is **not** published to the host.

---

## 2. Quick Start

### Step 1: Clone and enter the repository

```bash
git clone <repo-url> ai-rcagent
cd ai-rcagent
```

### Step 2: Find your Docker group GID

Linux requires the container to be in the `docker` group to access the Docker socket:

```bash
getent group docker | cut -d: -f3
# Example output: 998
```

Update the `group_add` value in `docker-compose.yml` with your GID:

```yaml
services:
  rcagent:
    group_add:
      - "998"   # ← Replace with your GID
```

> **macOS / Windows users:** Docker Desktop manages permissions automatically — you can skip this step.

### Step 3: Create a `.env` file (optional)

All settings have defaults, but create a `.env` next to `docker-compose.yml` for
your AI provider keys, notifications, and a production database password:

```bash
# Example
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
POSTGRES_PASSWORD=change-me
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=...
```

### Step 4: Start the stack

```bash
docker compose up --build -d
```

This builds and starts:

| Service | Purpose |
|---------|---------|
| `rcagent` | AI RCA Agent (API + WebSocket + built-in dashboard, port 8000) |
| `postgres` | PostgreSQL 16 database (internal only) |
| `error-generator` | Test-only error emitter (compose profile `test`, not started by default) |

First build takes 2–5 minutes.

### Step 5: Verify everything is running

```bash
# Check all containers are up
docker compose ps

# Test the health endpoint
curl http://localhost:8000/api/v1/health

# Watch the agent discover your containers
docker compose logs -f rcagent | grep -E "Started monitoring|Connected to Docker"
```

### Step 6: Trigger a test error and verify monitoring

```bash
# Start a test container that generates error logs
docker run -d --name test-error-app alpine:latest sh -c \
  'while true; do echo "FATAL: OutOfMemoryError: Java heap space - application crashed"; sleep 10; done'

# Within ~5 seconds the agent should pick it up automatically (see Section 3),
# then check alerts
curl http://localhost:8000/api/v1/alerts | python3 -m json.tool
```

Expected: You should see a `critical` alert for the `fatal` error pattern.

### Step 7: Open the dashboard

| Service | URL |
|---------|-----|
| **API Docs (Swagger)** | http://localhost:8000/docs |
| **Built-in Dashboard** | Served by the agent on port 8000 |

---

## 3. How Container Monitoring Works

This section explains what happens inside the agent when it monitors containers —
useful for understanding capacity planning and troubleshooting.

### The pipeline

```
Docker daemon (socket)
        │
   DockerMonitor                      ← src/monitor/docker_monitor.py
        │
  handle_log_line                     ← src/main.py
        │
 LogStreamManager                     ← src/monitor/log_stream.py (ring buffer, RCA context)
        │
  ErrorDetector                       ← src/detector/error_detector.py (80+ regex patterns)
        │
     ├─► PostgreSQL (alert saved) + WebSocket (/ws, live dashboard updates)
     │
     └─► AnalysisQueue (critical/high only) → RCAAnalyzer → AI provider
                │
                ├─► RCA incident saved to PostgreSQL
                ├─► Google Chat card + Email (SMTP)
                └─► WebSocket broadcast to dashboard
```

### 1. Connecting to Docker

- The agent connects via the Docker socket (`unix:///var/run/docker.sock`) or
  `DOCKER_HOST` (e.g. `tcp://remote-host:2375`) using **aiodocker**.
- The HTTP session is configured with **no total timeout** and an **unlimited
  connection pool**, so long-lived `follow=True` log streams are never killed by
  a client timeout and never exhaust the pool.

### 2. Container discovery — two mechanisms in parallel

| Mechanism | What it does |
|-----------|--------------|
| **Periodic scan** | Every `CONTAINER_SCAN_INTERVAL` seconds (default 5s) lists all containers on the host. Containers in `running`/`restarting` state that are not yet monitored are started; exited/removed ones are torn down. |
| **Docker events listener** | Subscribes to the Docker events stream. On `start`/`create`/`restart` events it triggers an **immediate** scan (no wait for the periodic interval); on `die`/`stop`/`destroy`/`kill` it stops monitoring that container right away. |

**Bottom line: when a new container is started on the host, the agent detects it
automatically within seconds — via the event listener or the periodic scan,
whichever fires first — and starts streaming its logs. No configuration, no
restart of the monitored container.** If the events stream drops, it reconnects
after a 5s backoff and the periodic scan acts as the safety net.

When a monitored container stops or is **recreated** (same name, new ID), its
stream is torn down cleanly, its DB record is marked `removed`, and the new
container is picked up automatically.

### 3. Log streaming — per-container asyncio task

Each monitored container gets its own asyncio task streaming every line to the
error detector. Two strategies, chosen by the container's logging driver:

| Logging driver | Strategy |
|----------------|----------|
| `json-file` (Docker default) | Real-time streaming: `follow=True, stdout=True, stderr=True, timestamps=True, tail=10` |
| `journald`, `syslog`, others | Polling fallback: fetch `tail=100` lines since the last timestamp every `LOG_POLL_INTERVAL` seconds (Docker's follow API is unreliable for these drivers) |

### 4. Lifecycle & resilience

- A stream that ends (container exited/crashed/removed) triggers a state check,
  the container-stop callback, and a clean teardown — leaked follow streams would
  otherwise exhaust the shared Docker connection pool for the whole process.
- Interrupted streams reconnect automatically after 2s; Docker 404/409
  (container gone / not running) are treated as normal lifecycle ends, not errors.
- Containers are persisted to PostgreSQL (upsert on discovery), so the container
  list survives agent restarts; old records for recreated containers are marked `removed`.

---

## 4. Architecture Overview

```
                          Docker Host
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   Container A           Container B             Container C
        │                      │                      │
        └──────────────────────┼──────────────────────┘
                               │
                         Docker Socket (read-only)
                               │
                      AI RCA Agent Container
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         PostgreSQL      AI Provider     Notifications
         (persistence)     (optional)          │
              │                          ┌─────┴─────┐
              └──────────────────────────┤Google Chat│
                                         │   Email   │
                                         └───────────┘
```

### Component Roles

| Component | Role | Why It's Needed |
|-----------|------|-----------------|
| **RCA Agent** (`rcagent`) | Core engine | Monitors containers via Docker socket, detects errors with regex patterns, runs AI analysis, sends notifications, serves API + dashboard |
| **PostgreSQL** | Database | Stores alerts, RCA analysis results, container metadata, runtime settings, user feedback permanently |
| **Error Generator** | Test tool | Compose profile `test` only — emits realistic errors to exercise the pipeline end-to-end |

### Error Detection Flow

```
Container Log → Docker Socket → DockerMonitor → LogStreamManager
                                                      │
                                                      ▼
                                              ErrorDetector
                                              (80+ regex patterns)
                                                      │
                                           ┌──────────┼──────────┐
                                           ▼          ▼          ▼
                                      PostgreSQL   WebSocket   Analysis Queue
                                      (save alert) (dashboard)  (if critical/high)
                                                                      │
                                                                      ▼
                                                                RCAAnalyzer
                                                                    │
                                                            ┌───────┼───────┐
                                                            ▼       ▼       ▼
                                                      Google Chat  Email  PostgreSQL
                                                     (notification)       (save RCA)
```

---

## 5. Deploying on a New Server

Container auto-discovery needs **zero configuration** — the checklist below is
about making sure the agent can reach Docker and your AI provider.

### Checklist

1. **Install Docker Engine + Compose plugin** on the new server.

2. **Fix the Docker group GID** (the #1 gotcha). `group_add` in
   `docker-compose.yml` must match the server's docker group GID:
   ```bash
   getent group docker | cut -d: -f3
   ```
   ```yaml
   group_add:
     - "998"   # ← e.g. 998 on the original server, 999 on many Ubuntu/Debian installs
   ```
   A mismatch shows up as `Permission denied` on `/var/run/docker.sock` and the
   agent cannot monitor anything.

3. **Create `.env`** with AI provider credentials and a production
   `POSTGRES_PASSWORD` (see [Configuration Reference](#6-configuration-reference)).

4. **Optional:** copy a customized `config/` directory (the settings defaults live
   in `config/settings.py`; compose mounts it read-only at `/app/config`).

5. **Start and verify:**
   ```bash
   docker compose up --build -d
   curl http://localhost:8000/api/v1/health
   curl http://localhost:8000/api/v1/containers   # should list ALL containers on this host
   ```

6. **Open port 8000** in the server's firewall if the dashboard/API must be
   reachable from outside.

### Things to know on the new server

- **The agent monitors ALL containers on that host.** It mounts the host's Docker
  socket, so any other workloads running there are picked up automatically — which
  is the design.
- **Monitoring a different host remotely:** instead of mounting a socket, set
  `DOCKER_HOST=tcp://<host>:2375` (the code falls back to a TCP connector
  automatically). The remote Docker daemon must expose its Engine API — protect it
  with TLS and network restrictions; never expose it publicly unauthenticated.
- **Postgres is internal-only** (no published port). Data persists in the
  `postgres_data` volume — back it up in production.
- **Ollama users:** run Ollama on the host machine. `docker-compose.yml` already
  maps `host.docker.internal` to the host gateway (`extra_hosts:
  host.docker.internal:host-gateway`), so the default `OLLAMA_URL` works on Linux too.
- **Non-Docker applications:** use `CUSTOM_LOG_FILES` with host log directories
  mounted under `/host-logs` (see [Section 10](#10-monitoring--observability)).

---

## 6. Configuration Reference

All configuration is done through **environment variables**. Set them:

1. **In a `.env` file** (recommended, next to `docker-compose.yml`):
   ```bash
   echo 'GOOGLE_CHAT_WEBHOOK_URL=https://...' >> .env
   docker compose up -d
   ```

2. **Inline** when running:
   ```bash
   GOOGLE_CHAT_WEBHOOK_URL="https://..." docker compose up -d
   ```

3. **Directly in `docker-compose.yml`** under `environment:`

> **Note:** Changes to environment variables require `docker compose up -d rcagent` to apply.

### AI Provider Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PROVIDER` | `ollama` | AI provider to use |
| `ENVIRONMENT_TYPE` | `docker-compose` | Deployment environment for the monitored workloads. Controls which remediation commands the AI suggests — e.g. with `docker-compose` it will never suggest `kubectl`/`helm`. Supported: `docker-compose`, `docker`, `kubernetes`, `vm`, `bare-metal`, `other` |

**Ollama (local, free):**
```bash
AI_PROVIDER=ollama
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=gemma:2b    # or llama3.2, mistral, etc.
```
> On Linux, `host.docker.internal` is resolved automatically via `extra_hosts` in docker-compose.yml

**OpenAI:**
```bash
AI_PROVIDER=openai
OPENAI_API_KEY=sk-proj-...your-key...
OPENAI_MODEL=gpt-4o-mini    # fast & affordable
```

**OpenRouter (free models available — no credit card):**
```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...your-key...
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free    # free tier
```
> OpenRouter offers 20+ free models. Browse all models at https://openrouter.ai/models

**Anthropic Claude:**
```bash
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...your-key...
ANTHROPIC_MODEL=claude-3-haiku-20240307
```

**Google Gemini:**
```bash
AI_PROVIDER=google_gemini
GEMINI_API_KEY=AIza...your-key...
GEMINI_MODEL=gemini-pro
```

**Azure OpenAI:**
```bash
AI_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_KEY=...your-key...
AZURE_OPENAI_DEPLOYMENT=gpt-4-deployment
```

### Notification Configuration

**Google Chat:**
```bash
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=...
```

**Email (SMTP):**
```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true              # false for SSL-on-connect (port 465)
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=rcagent@example.com
EMAIL_TO=devops@company.com
```

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `postgres` | PostgreSQL hostname (keep as-is for compose) |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `rcagent` | Database user |
| `POSTGRES_PASSWORD` | `rcagent_secret` | **Change this in production** |
| `POSTGRES_DB` | `rcagent` | Database name |

### Monitoring & Analysis Tuning

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `CONTAINER_SCAN_INTERVAL` | `5` | 1–300 (seconds) | How often containers are re-scanned for discovery/lifecycle changes |
| `SCAN_INTERVAL` | `5` | 1–300 (seconds) | Generic scan interval |
| `LOG_POLL_INTERVAL` | `1.0` | seconds | Log polling frequency for non-json-file logging drivers |
| `AI_ANALYSIS_INTERVAL` | `5` | seconds | Gap between queued AI RCA requests (rate-limit protection) |
| `AI_ANALYSIS_TIMEOUT` | `150` | seconds | Max duration of one AI analysis job |
| `MAX_LOG_LINES` | `200` | 50–5000 | Log lines captured before the error |
| `MAX_LOG_LINES_AFTER` | `50` | — | Log lines captured after the error |
| `RETENTION_DAYS` | `30` | 1–365 | Days to keep historical data |
| `DEDUPLICATION_WINDOW` | `300` | seconds | Suppress duplicate alerts within this window |
| `MAX_CONTAINERS` | `1000` | — | Upper bound on monitored containers |

### Logging

| Variable | Default | Options |
|----------|---------|---------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

---

## 7. AI Provider Setup

### Fallback Mode (No AI Provider)

The system works **fully without an AI provider** — it monitors containers, detects errors, saves alerts to the database, sends Google Chat notifications, and displays everything in the dashboard. The only thing missing is the AI-generated root cause analysis, which will show:

```
AI analysis unavailable. Manual investigation required.
Confidence: 0%
```

To run without AI:
```bash
# Just start the stack as-is — no AI provider needed
docker compose up --build -d
```

When you're ready to add AI, configure one of the options below and restart:
```bash
docker compose up -d rcagent
```

### Option A: Ollama (Local — Free)

Best for testing and development. Runs entirely on your machine.

**1. Install Ollama:**
```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS — download from https://ollama.com
```

**2. Pull a model:**
```bash
# Recommended for most laptops: small & fast (3B params, ~2GB)
ollama pull gemma:2b

# Better quality but needs more RAM (7B params, ~4GB)
ollama pull mistral

# Tiny model for low-end hardware (1.1B params, ~700MB)
ollama pull tinyllama:1.1b
```

**3. Verify Ollama is running:**
```bash
curl http://localhost:11434/api/tags
# Should return a JSON list of models
```

**4. Configure the RCA Agent:**
```bash
AI_PROVIDER=ollama OLLAMA_MODEL=gemma:2b docker compose up -d rcagent
```

**5. Test:**
```bash
# Trigger a test error
docker run -d --name test-alpine alpine sh -c 'echo "FATAL: Critical error"; sleep 3600'

# Wait for analysis (may take 30s–5min depending on hardware)
# Then check incidents
curl http://localhost:8000/api/v1/incidents
```

> **Performance note:** Local models are slow on laptops without GPUs. A single analysis can take 1–10+ minutes depending on your hardware. Use a small model (`gemma:2b` or `tinyllama:1.1b`) for faster results.

### Option B: OpenAI (Cloud — Fast, Needs API Key)

**1. Get an API key:**
- Go to https://platform.openai.com/api-keys
- Create a new secret key (starts with `sk-proj-`)
- Add billing at https://platform.openai.com/account/billing

**2. Configure:**
```bash
AI_PROVIDER=openai \
OPENAI_API_KEY=sk-proj-...your-key... \
OPENAI_MODEL=gpt-4o-mini \
docker compose up -d rcagent
```

### Option C: Anthropic Claude

**1. Get an API key:**
- Go to https://console.anthropic.com/
- Create an API key (starts with `sk-ant-`)

**2. Configure:**
```bash
AI_PROVIDER=anthropic \
ANTHROPIC_API_KEY=sk-ant-...your-key... \
docker compose up -d rcagent
```

### Option D: Google Gemini (Cloud — Free Tier Available)

**1. Get an API key:**
- Go to https://aistudio.google.com/apikey
- Click **"Create API Key"** (free tier available)

**2. Configure:**
```bash
AI_PROVIDER=google_gemini \
GEMINI_API_KEY=AIza...your-key... \
docker compose up -d rcagent
```

### Switching AI Providers Later

To switch providers anytime:

```bash
# 1. Configure the new provider via env vars
AI_PROVIDER=openai \
OPENAI_API_KEY=sk-proj-... \
docker compose up -d rcagent

# 2. Verify it's working
curl http://localhost:8000/api/v1/health
# Look for: "ai_provider_configured": true
```

No rebuild needed — just restart the container with new environment variables.

---

## 8. Notification Setup

### A. Google Chat Notifications ✅

The Google Chat notifier sends rich card messages when critical or high-severity errors are detected. **This has been tested and confirmed working.**

#### Setup

**1. Create a Google Chat webhook:**

- Open **Google Chat**
- Go to the target space → **Apps & integrations** → **Manage webhooks**
- Click **Add webhook**, name it `AI RCA Agent`
- Copy the webhook URL (looks like `https://chat.googleapis.com/v1/spaces/...`)

**2. Configure:**
```bash
GOOGLE_CHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/AAAAxxxx/messages?key=AIza...&token=..." \
  docker compose up -d rcagent
```

**3. Test:**
```bash
# Start a container that generates fatal errors
docker run -d --name test-error alpine sh -c \
  'while true; do echo "FATAL: Database connection pool exhausted"; sleep 5; done'

# Check logs for confirmation
docker compose logs rcagent | grep "Google Chat alert sent"
```

#### What the notification looks like

```
┌─────────────────────────────────────────────┐
│  🚨 CRITICAL Error Detected                 │
│  Container: test-error-app                  │
├─────────────────────────────────────────────┤
│  Container  │ test-error-app                │
│  Host       │ a1b2c3d4e5f6                 │
│  Timestamp  │ 2026-07-30 16:14:15 UTC       │
│  Severity   │ CRITICAL                      │
│  Error Type │ fatal                         │
│  Root Cause │ Database connection pool...    │
│  Suggested  │ Check container logs...        │
│  AI Confid. │ 95%                           │
├─────────────────────────────────────────────┤
│  [🔍 View in Dashboard]  [✅ Acknowledge]    │
└─────────────────────────────────────────────┘
```

### B. Email Notifications (SMTP)

The email notifier sends HTML-formatted alerts when errors are detected.

**1. Get SMTP credentials (Gmail example):**
- Enable **2-Step Verification** at https://myaccount.google.com/security
- Go to **App Passwords** → generate one for **Mail**
- Copy the 16-character password

**2. Configure:**
```bash
SMTP_SERVER=smtp.gmail.com \
SMTP_PORT=587 \
SMTP_USER=your-email@gmail.com \
SMTP_PASSWORD=your-app-password \
EMAIL_FROM=rcagent@yourdomain.com \
EMAIL_TO=devops@company.com \
docker compose up -d rcagent
```

### C. How Notifications Work

| Mechanism | Setting | Default | Effect |
|-----------|---------|---------|--------|
| **Severity filter** | Hardcoded | Critical/High only | Low/medium errors don't trigger notifications |
| **Deduplication** | `DEDUPLICATION_WINDOW` | 300s | Same error from same container won't repeat within 5 min |
| **Rate limiting** | Hardcoded | 10s between same-type alerts | Prevents alert storms |
| **Max alerts/min** | Hardcoded | 60/min | Global safety cap |
| **AI analysis queue** | `AI_ANALYSIS_INTERVAL` | 5s gap | RCA requests processed one at a time |

---

## 9. API Reference

The RCA Agent exposes a REST API at `/api/v1/` and a WebSocket at `/ws`.

### Key Endpoints

```bash
# Health check
curl http://localhost:8000/api/v1/health

# List all containers
curl http://localhost:8000/api/v1/containers

# List alerts (newest first)
curl http://localhost:8000/api/v1/alerts

# Filter alerts by severity
curl "http://localhost:8000/api/v1/alerts?severity=critical"

# Update alert status (acknowledge / resolve / dismiss)
curl -X PATCH "http://localhost:8000/api/v1/alerts/1/status?status=acknowledged"

# List RCA incidents
curl http://localhost:8000/api/v1/incidents

# Get specific incident with full markdown report
curl http://localhost:8000/api/v1/incidents/1

# Dashboard statistics
curl http://localhost:8000/api/v1/statistics

# Trigger manual RCA analysis
curl -X POST http://localhost:8000/api/v1/analyse \
  -H "Content-Type: application/json" \
  -d '{"container_id": "abc123", "log_snippet": "FATAL: OOM", "error_type": "fatal"}'

# Submit feedback on analysis quality
curl -X POST http://localhost:8000/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"analysis_id": 1, "was_helpful": true, "comments": "Good analysis"}'
```

### WebSocket

Connect to `ws://localhost:8000/ws` for real-time updates:

```javascript
const ws = new WebSocket("ws://localhost:8000/ws");

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  // msg.type: "alert" | "analysis" | "container_update" | "stats"
};
```

### Complete Endpoint Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| GET | `/api/v1/health` | System health check |
| GET | `/api/v1/containers` | List monitored containers |
| GET | `/api/v1/containers/{id}` | Container details |
| GET | `/api/v1/alerts` | List alerts |
| GET | `/api/v1/alerts/{id}` | Alert details |
| PATCH | `/api/v1/alerts/{id}/status` | Acknowledge/resolve/dismiss alert |
| GET | `/api/v1/incidents` | List RCA incidents |
| GET | `/api/v1/incidents/{id}` | Incident with full report |
| POST | `/api/v1/analyse` | Trigger manual analysis |
| GET | `/api/v1/statistics` | Dashboard statistics |
| POST | `/api/v1/feedback` | Submit feedback |
| GET | `/api/v1/notifications` | Notification history |
| GET | `/api/v1/knowledge-base` | Knowledge base entries |
| WS | `/ws` | Real-time WebSocket |
| GET | `/docs` | Interactive Swagger UI |

---

## 10. Monitoring & Observability

The built-in dashboard (served by the agent itself on port 8000) provides
real-time views via the REST API and WebSocket:

- Monitored containers and log file sources
- Alerts and incidents with full RCA reports
- Error categories and statistics
- Live updates over WebSocket (`/ws`)

### Monitoring Non-Docker Applications

The agent can monitor applications that run **outside** Docker by reading
custom log files in real-time. Configure the paths in your environment
(`.env` or `docker-compose.yml`):

```bash
# Comma-separated absolute paths to log files to tail
CUSTOM_LOG_FILES=/host-logs/myapp/app.log,/host-logs/myapp/error.log
# Optional display names (must match CUSTOM_LOG_FILES order)
CUSTOM_LOG_NAMES=myapp,myapp-errors
# Seconds between file reads while tailing
LOG_FILE_POLL_INTERVAL=0.5
# Existing lines seeded into the buffer on start (for RCA context)
LOG_FILE_TAIL_LINES=100
```

Each configured file is tailed (`tail -f` style), and every new line flows
through the same pipeline as container logs: error detection → alert
creation → AI RCA analysis → notifications (Google Chat / email) →
WebSocket broadcast to the dashboard.

> **Running the agent in Docker?** Mount the host log files into the
> `rcagent` container (read-only) and point `CUSTOM_LOG_FILES` at the
> mounted path. Compose already mounts `${CUSTOM_LOG_DIR:-./logs}:/host-logs:ro`,
> so the simplest setup is to put your log files in `./logs/` on the host and
> set e.g. `CUSTOM_LOG_FILES=/host-logs/app.log`.

Notes: existing lines are seeded into the buffer (no alerts) — only **new**
lines appended after startup trigger detection/notifications.

### Container Logs

```bash
# Follow all logs
docker compose logs -f rcagent

# Filter for container discovery
docker compose logs rcagent | grep -E "Started monitoring|Stopped monitoring"

# Filter for errors
docker compose logs rcagent | grep "Error detected"

# Filter for notifications
docker compose logs rcagent | grep "Google Chat alert sent"

# Filter for AI analysis
docker compose logs rcagent | grep "RCA analysis completed"
```

---

## 11. Security Best Practices

### Docker Socket

- Mounted **read-only** (`:ro`) — the agent can only read container metadata and logs via the Engine API
- Agent runs as **non-root user** (`rcagent`) with minimal capabilities
- Only `NET_BIND_SERVICE` capability added; `no-new-privileges` enabled
- `group_add` provides Docker group access (Linux only)

### Secrets Management

- **Never** hardcode API keys in `docker-compose.yml`
- Use a `.env` file (keep it out of git)
- For production, use **Docker Secrets** or HashiCorp Vault

### Network Security

- All services on isolated `rcagent_network` bridge
- Only port 8000 exposed to the host; PostgreSQL is internal-only
- CORS configurable via `CORS_ORIGINS`
- For remote Docker hosts (`DOCKER_HOST=tcp://...`), always use TLS and network restrictions

### Data Protection

- PostgreSQL data persisted in Docker volume
- Log retention configurable (`RETENTION_DAYS`)
- Log snippets truncated before sending to AI (8000 chars max)
- Log snippets in notifications truncated (500 chars max)

### Production Hardening Checklist

- [ ] Change the default `POSTGRES_PASSWORD`
- [ ] Put HTTPS in front of port 8000 (reverse proxy of your choice)
- [ ] Restrict CORS to your dashboard domain
- [ ] Set up log rotation (pre-configured: 10MB × 3 files)
- [ ] Configure volume backups (especially `postgres_data`)
- [ ] Monitor the health endpoint externally
- [ ] Verify the docker group GID matches (`getent group docker`)

---

## 12. Troubleshooting

### Container keeps restarting

```bash
# Check logs
docker compose logs rcagent

# Common issues:
# - "Permission denied" on Docker socket → Fix group_add GID (see below)
# - Database connection refused → Wait for PostgreSQL (healthcheck-gated startup)
```

### Docker socket permission denied

```bash
# Find your Docker group GID
getent group docker | cut -d: -f3
# Example: 998

# Update docker-compose.yml group_add, then restart
docker compose up -d rcagent
```

### Agent is not detecting new containers

```bash
# 1. Check the agent can talk to Docker
curl --unix-socket /var/run/docker.sock http://localhost/version   # from the host

# 2. Check agent logs for discovery activity
docker compose logs rcagent | grep -E "Connected to Docker|Started monitoring|Error scanning"

# 3. Check what the agent sees
curl http://localhost:8000/api/v1/containers
```

- If `Connected to Docker` never appears → socket permission problem (GID mismatch) or wrong `DOCKER_HOST`.
- If discovery errors repeat → verify the Docker daemon is healthy (`docker info` on the host).
- New containers are normally detected within ~5s (event listener) — the periodic
  scan is the fallback.

### AI analysis shows 0% confidence (fallback mode)

This is **normal** if no AI provider is configured. The system works without it:

```
# Check what's happening
docker compose logs rcagent | grep -i "RCA analysis failed"

# Verify current provider
docker inspect ai-rcagent | grep AI_PROVIDER

# To fix: configure an AI provider (see Section 7) and restart
AI_PROVIDER=openai OPENAI_API_KEY=sk-... docker compose up -d rcagent
```

### Ollama is too slow

On laptops without GPUs, local models can take 1–10+ minutes per analysis. Solutions:

```bash
# 1. Use a smaller model
ollama pull gemma:2b        # 3B params, ~2GB
ollama pull tinyllama:1.1b  # 1.1B params, ~700MB

# 2. Or switch to a cloud AI provider (much faster)
AI_PROVIDER=openai OPENAI_API_KEY=sk-... docker compose up -d rcagent
```

### Google Chat notification not sending

```bash
# 1. Verify webhook URL is set
docker inspect ai-rcagent | grep GOOGLE_CHAT_WEBHOOK_URL

# 2. Check for errors
docker compose logs rcagent | grep -i "google\|chat\|webhook"

# 3. Test the webhook URL manually
curl -X POST "https://chat.googleapis.com/...your-webhook..." \
  -H "Content-Type: application/json" \
  -d '{"text": "Test message"}'

# 4. Only critical/high severity triggers notifications
#    Test with: echo "FATAL: test" from a monitored container
```

### Custom log file is not being monitored

```bash
# Check the agent logs
docker compose logs rcagent | grep -i "file\|custom\|log"

# Common causes:
# 1. CUSTOM_LOG_FILES is empty or paths are incorrect (must be absolute)
# 2. The log file is not mounted into the container (see Section 10)
# 3. The agent process cannot read the file (permission denied)

# Verify from inside the container
docker compose exec rcagent ls -la /path/to/app.log
```

### Reset everything

```bash
# Stop and remove all containers, volumes, and networks
docker compose down -v

# Rebuild from scratch
docker compose up --build -d
```

---

## Quick Reference Card

```bash
# ─────────────────────────────────────────────
#  START & STOP
# ─────────────────────────────────────────────

# Start the full stack (build first time)
docker compose up --build -d

# Stop the stack
docker compose down

# Full reset (removes all data)
docker compose down -v

# ─────────────────────────────────────────────
#  RESTART A SINGLE SERVICE
# ─────────────────────────────────────────────

docker compose up -d rcagent

# ─────────────────────────────────────────────
#  LOGS
# ─────────────────────────────────────────────

docker compose logs -f rcagent
docker compose logs rcagent | grep -E "Started monitoring"
docker compose logs rcagent | grep "Error detected"
docker compose logs rcagent | grep "Google Chat alert sent"

# ─────────────────────────────────────────────
#  TEST THE SYSTEM
# ─────────────────────────────────────────────

# Health check
curl http://localhost:8000/api/v1/health

# Trigger test errors
docker run -d --name test-error alpine sh -c \
  'while true; do echo "FATAL: OOM - crashed"; sleep 5; done'

# Check alerts
curl http://localhost:8000/api/v1/alerts

# ─────────────────────────────────────────────
#  CONFIGURE NOTIFICATIONS (Google Chat)
# ─────────────────────────────────────────────

GOOGLE_CHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=..." \
docker compose up -d rcagent

# ─────────────────────────────────────────────
#  CONFIGURE AI PROVIDERS
# ─────────────────────────────────────────────

# Ollama (local)
AI_PROVIDER=ollama OLLAMA_MODEL=gemma:2b docker compose up -d rcagent

# OpenAI (cloud)
AI_PROVIDER=openai OPENAI_API_KEY="sk-proj-..." docker compose up -d rcagent

# OpenRouter (free models available)
AI_PROVIDER=openrouter OPENROUTER_API_KEY="sk-or-..." docker compose up -d rcagent

# Google Gemini (cloud, free tier)
AI_PROVIDER=google_gemini GEMINI_API_KEY="AIza..." docker compose up -d rcagent

# Anthropic Claude (cloud)
AI_PROVIDER=anthropic ANTHROPIC_API_KEY="sk-ant-..." docker compose up -d rcagent
```

---

*AI RCA Agent Setup Guide | Version 1.2.0*
