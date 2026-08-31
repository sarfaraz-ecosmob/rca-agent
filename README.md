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
               Docker Socket
                     |
            AI RCA Agent Container
                     |
         ┌──────────┼──────────┐
         |          |          |
    PostgreSQL    Redis    AI Provider
         |          |          |
         └──────────┼──────────┘
                    |
         ┌──────────┼──────────┐
         |          |          |
   Google Chat   Email    Dashboard
```

## Features

### Container Monitoring
- Auto-discovers new containers
- Real-time log streaming (json-file, journald, syslog)
- Supports stdout/stderr
- No container restart required
- Handles container lifecycle (start/stop/restart)

### Error Detection
- **Application Errors:** Exception, Error, Fatal, Panic, Traceback, Segfault, NPE, StackOverflow
- **Infrastructure Errors:** Connection refused, Timeout, DNS failure, Disk full, OOM, Permission denied
- **Database Errors:** MySQL, MariaDB, PostgreSQL, MongoDB, Redis, Elasticsearch
- **Container Errors:** CrashLoop, Restart, Exit code, Signal terminated, Health check failures
- **Web Server Errors:** Nginx, Apache, Node.js, Python, Java, Go, PHP

### AI-Powered RCA
- Multiple AI providers: Ollama, LM Studio, OpenAI, Azure OpenAI, Anthropic, Google Gemini
- Reads surrounding logs (200 lines before, 50 lines after)
- Identifies root cause, severity, probability, and impacted components
- Suggests immediate fix, permanent fix, preventive actions
- Generates structured RCA reports (JSON, Markdown)
- Confidence scoring

### Notifications
- **Google Chat:** Rich card messages with severity colors
- **Email:** HTML-formatted alerts with complete RCA details
- Deduplication and rate limiting to prevent alert storms

### Dashboard
- Real-time container monitoring
- Alert and incident management
- RCA report viewer with drill-down
- Dark mode support
- WebSocket-based live updates
- Search and filtering

## Technology Stack

| Component | Technology |
|-----------|------------|
| Backend | Python FastAPI |
| Monitoring | Docker SDK, aiodocker |
| Database | PostgreSQL (SQLAlchemy async) |
| Cache/Queue | Redis |
| AI | Ollama / OpenAI / Anthropic / Gemini |
| Frontend | React + TypeScript + MUI |
| WebSocket | FastAPI WebSocket |
| Dashboard | Real-time monitoring UI (built-in) |
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

# Copy and edit configuration
cp .env.example .env
# Edit .env with your settings (AI provider, notifications, etc.)
```

### 2. Start the Stack

```bash
docker-compose up -d
```

This starts:
- AI RCA Agent on port 8000
- PostgreSQL database
- Redis cache
- React Dashboard on port 3000
- Nginx reverse proxy on port 80

### 3. Verify Installation

```bash
# Check health
curl http://localhost:8000/api/v1/health

# List monitored containers
curl http://localhost:8000/api/v1/containers

# Open dashboard
open http://localhost:3000
```

## Configuration

Key environment variables (see `.env.example` for full list):

```bash
# AI Provider (ollama, openai, anthropic, google_gemini)
AI_PROVIDER=ollama
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3

# Notifications
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/...
SMTP_SERVER=smtp.gmail.com
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_TO=devops@company.com

# Monitoring
SCAN_INTERVAL=5
MAX_LOG_LINES=200
RETENTION_DAYS=30
```

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
| GET | `/api/v1/health` | Health check |
| WS | `/ws` | WebSocket for real-time updates |

## Error Detection Patterns

The agent detects errors across five categories with 80+ specific patterns:

- **Application** (19 patterns): exceptions, errors, panics, tracebacks
- **Infrastructure** (15 patterns): connection issues, timeouts, OOM, disk full
- **Database** (18 patterns): connection errors, deadlocks, replication issues
- **Container** (8 patterns): crash loops, OOM, health check failures
- **Web Server** (20+ patterns): HTTP errors, worker failures, language-specific errors

## Security

- Docker socket mounted read-only (`:ro`)
- Runs as non-root user
- All communication encrypted (HTTPS/TLS)
- API keys stored in environment variables / Docker secrets
- Rate limiting on all API endpoints
- CORS configurable

## Deployment Options

### Option 1: Docker Container (Recommended)
```bash
docker-compose up -d
```

### Option 2: Direct Installation
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env
python -m src.main
```

### Option 3: Kubernetes
Deploy using the provided Docker image with appropriate manifests.

## Monitoring

The React dashboard at http://localhost:3000 provides real-time views for:
- Monitored containers and log file sources
- Alerts and incidents (RCA reports)
- Error categories and statistics

## Monitoring Non-Docker Applications

The agent can also monitor applications that run **outside** Docker by
reading their log files in real-time. Configure the log file paths in `.env`:

```bash
CUSTOM_LOG_FILES=/var/log/myapp/app.log,/var/log/myapp/error.log
CUSTOM_LOG_NAMES=myapp,myapp-errors
```

Restart the agent; each configured file is tailed (`tail -f` style) and fed
through the same error detection, RCA, and notification pipeline as
container logs. When running the agent via Docker Compose, mount the host
log files into the container (see `docker-compose.yml` for an example).

## Development

```bash
# Backend
cd ai-rcagent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.main

# Frontend
cd dashboard
npm install
REACT_APP_API_URL=http://localhost:8000 npm start
```

## License

MIT License - see LICENSE file for details.
