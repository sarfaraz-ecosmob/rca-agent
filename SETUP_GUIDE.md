# AI RCA Agent — Complete Setup Guide

> **AI-Powered Root Cause Analysis Agent for Docker Containers**

This guide covers everything you need to deploy, configure, and operate the AI RCA Agent. It works **out of the box** with error detection and Google Chat notifications, and you can add AI-powered root cause analysis later by configuring an AI provider.

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Quick Start](#2-quick-start)
3. [Architecture Overview](#3-architecture-overview)
4. [Configuration Reference](#4-configuration-reference)
5. [AI Provider Setup](#5-ai-provider-setup)
6. [Notification Setup](#6-notification-setup)
7. [API Reference](#7-api-reference)
8. [Monitoring & Observability](#8-monitoring--observability)
9. [Security Best Practices](#9-security-best-practices)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. System Requirements

### Minimum Hardware

| Resource | Requirement | Notes |
|----------|-------------|-------|
| CPU | 2 cores | 4+ cores recommended for AI analysis |
| RAM | 4 GB | 8 GB+ if running local AI models (Ollama) |
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
| **OpenRouter API key** | Cloud AI analysis | Access 200+ models, pay-per-use |
| **Anthropic API key** | Cloud AI analysis | Fast, needs billing setup |
| **Google Gemini API key** | Cloud AI analysis | Free tier available |

### Ports Used

| Port | Service | Purpose |
|------|---------|---------|
| 8000 | RCA Agent API | REST API + WebSocket |
| 3000 | Dashboard | React frontend |
| 80 | Nginx | Reverse proxy |
| 443 | Nginx | HTTPS (if configured) |
| 5432 | PostgreSQL | Database |
| 6379 | Redis | Cache + message queue |

---

## 2. Quick Start

### Step 1: Clone and enter the repository

```bash
git clone <repo-url> ai-rcagent
cd ai-rcagent
```

### Step 2: Find your Docker group GID

Linux requires the `rcagent` user to be in the `docker` group to access the Docker socket:

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

### Step 3: Start the stack

```bash
docker compose up --build -d
```

This builds and starts all 7 services. First build takes 2–5 minutes.

### Step 4: Verify everything is running

```bash
# Check all containers are up
docker compose ps

# Test the health endpoint
curl http://localhost:8000/api/v1/health

# Expected output:
# {"status":"healthy","db_connected":true,"redis_connected":true,...}
```

### Step 5: Open the dashboard

| Service | URL | Login |
|---------|-----|-------|
| **Dashboard** | http://localhost:3000 | — |
| **API Docs** | http://localhost:8000/docs | — |

### Step 6: Trigger a test error and verify monitoring

```bash
# Start a test container that generates error logs
docker run -d --name test-error-app alpine:latest sh -c \
  'while true; do echo "FATAL: OutOfMemoryError: Java heap space - application crashed"; sleep 10; done'

# Wait 15 seconds, then check alerts
curl http://localhost:8000/api/v1/alerts | python3 -m json.tool
```

Expected: You should see a `critical` alert for the `fatal` error pattern.

---

## 3. Architecture Overview

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
         PostgreSQL          Redis         AI Provider
         (persistence)    (cache/queue)    (optional)
              │                │                │
              └────────────────┼────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
        Google Chat          Email         Dashboard/API
       (notifications)  (notifications)    (web UI)
```

### Component Roles

| Component | Role | Why It's Needed |
|-----------|------|-----------------|
| **RCA Agent** | Core engine | Monitors containers via Docker socket, detects errors with regex patterns, runs AI analysis, sends notifications |
| **PostgreSQL** | Database | Stores alerts, RCA analysis results, container metadata, user feedback permanently |
| **Redis** | Cache/Queue | In-memory buffer for log streaming, analysis job queue, deduplication cache |
| **Dashboard** | UI | React frontend with real-time WebSocket updates for containers, alerts, incidents |
| **Nginx** | Reverse proxy | Routes traffic, rate-limits APIs (30 req/s), serves static files, handles SSL termination |

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

## 4. Configuration Reference

All configuration is done through **environment variables**. Set them:

1. **Inline** when running:
   ```bash
   GOOGLE_CHAT_WEBHOOK_URL="https://..." docker compose up -d
   ```

2. **In a `.env` file** (recommended):
   ```bash
   echo 'GOOGLE_CHAT_WEBHOOK_URL=https://...' >> .env
   docker compose up -d
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
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free    # 1M context, free
```
> OpenRouter offers 20+ free models. The Nemotron 3 Ultra has a 1M token context window — ideal for long container logs. Other free options: `google/gemma-4-31b-it:free`, `nvidia/nemotron-3-super-120b-a12b:free`. Browse all models at https://openrouter.ai/models

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
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=rcagent@example.com
EMAIL_TO=devops@company.com
```

### Database & Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `postgres` | PostgreSQL hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `rcagent` | Database user |
| `POSTGRES_PASSWORD` | `rcagent_secret` | Database password |
| `POSTGRES_DB` | `rcagent` | Database name |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |

### Analysis Tuning

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `MAX_LOG_LINES` | `200` | 50–5000 | Log lines to capture before the error |
| `MAX_LOG_LINES_AFTER` | `50` | — | Log lines to capture after the error |
| `RETENTION_DAYS` | `30` | 1–365 | Days to keep historical data |
| `DEDUPLICATION_WINDOW` | `300` | seconds | Suppress duplicate alerts within this window |
| `SCAN_INTERVAL` | `5` | seconds | How often to scan for new containers |
| `LOG_POLL_INTERVAL` | `1.0` | seconds | Log polling frequency |

### Logging

| Variable | Default | Options |
|----------|---------|---------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

---

## 5. AI Provider Setup

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
- Click **"Create API Key"** (free tier: 60 requests/minute)

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

## 6. Notification Setup

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

---

## 7. API Reference

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

## 8. Monitoring & Observability

The React dashboard at http://localhost:3000 provides all monitoring views
in real-time via the REST API and WebSocket:

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
CUSTOM_LOG_FILES=/var/log/myapp/app.log,/var/log/myapp/error.log
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
> mounted path, e.g. `- /var/log/myapp:/host-logs/myapp:ro` with
> `CUSTOM_LOG_FILES=/host-logs/myapp/app.log`.

### Container Logs

```bash
# Follow all logs
docker compose logs -f rcagent

# Filter for errors
docker compose logs rcagent | grep "Error detected"

# Filter for notifications
docker compose logs rcagent | grep "Google Chat alert sent"

# Filter for AI analysis
docker compose logs rcagent | grep "RCA analysis completed"
```

---

## 9. Security Best Practices

### Docker Socket

- Mounted **read-only** (`:ro`) — can only read container logs, not modify containers
- Agent runs as **non-root user** (`rcagent`) with minimal capabilities
- Only `NET_BIND_SERVICE` capability added
- `group_add` provides Docker group access (Linux only)

### Secrets Management

- **Never** hardcode API keys in `docker-compose.yml`
- Use a `.env` file (already in `.gitignore`)
- For production, use **Docker Secrets** or HashiCorp Vault

### Network Security

- All services on isolated `rcagent_network` bridge
- Only necessary ports exposed to host
- Nginx rate limits API to 30 requests/second
- CORS configurable via `CORS_ORIGINS`

### Data Protection

- PostgreSQL data persisted in Docker volume
- Log retention configurable (`RETENTION_DAYS`)
- Log snippets truncated before sending to AI (8000 chars max)
- Log snippets in notifications truncated (500 chars max)

### Production Hardening Checklist

- [ ] Enable HTTPS in Nginx (uncomment SSL block, add certificates)
- [ ] Change the default `POSTGRES_PASSWORD`
- [ ] Restrict CORS to your dashboard domain
- [ ] Set up log rotation (pre-configured: 10MB × 3 files)
- [ ] Configure volume backups (especially PostgreSQL)
- [ ] Monitor the health endpoint externally
- [ ] Remove the `version` line from `docker-compose.yml` (obsolete in Docker Compose V2)

---

## 10. Troubleshooting

### Container keeps restarting

```bash
# Check logs
docker compose logs rcagent

# Common issues:
# - "Permission denied" on Docker socket → Fix group_add GID
# - "No module named 'structlog'" → Rebuild (pip install issue)
# - Database connection refused → Wait for PostgreSQL (30s startup)

# Full rebuild
docker compose down -v && docker compose up --build -d
```

### Docker socket permission denied

```bash
# Find your Docker group GID
getent group docker | cut -d: -f3
# Example: 998

# Update docker-compose.yml group_add, then restart
docker compose up -d rcagent
```

### AI analysis shows 0% confidence (fallback mode)

This is **normal** if no AI provider is configured. The system works without it:

```
# Check what's happening
docker compose logs rcagent | grep -i "RCA analysis failed"

# Verify current provider
docker inspect ai-rcagent | grep AI_PROVIDER

# To fix: configure an AI provider (see Section 5) and restart
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
# 2. The log file is not mounted into the container (see Section 8)
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

# OpenRouter (200+ models)
AI_PROVIDER=openrouter OPENROUTER_API_KEY="sk-or-..." docker compose up -d rcagent

# Google Gemini (cloud, free tier)
AI_PROVIDER=google_gemini GEMINI_API_KEY="AIza..." docker compose up -d rcagent

# Anthropic Claude (cloud)
AI_PROVIDER=anthropic ANTHROPIC_API_KEY="sk-ant-..." docker compose up -d rcagent
```

---

*Generated by AI RCA Agent Setup Guide | Version 1.1.0*
