# Multi-stage Dockerfile for AI RCA Agent.
#
# Stage 1: Build dependencies
# Stage 2: Runtime (slim, secure, minimal)
# Uses a non-root user for security.

# Stage 1: Python dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies (system-wide)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Stage 2: Runtime
FROM python:3.11-slim AS runtime

# Metadata
LABEL maintainer="AI RCA Agent Team"
LABEL description="AI-Powered Root Cause Analysis Agent for Docker Containers"
LABEL version="1.0.0"

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    DOCKER_HOST=unix:///var/run/docker.sock \
    TZ=UTC

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user
RUN groupadd -r rcagent && useradd -r -g rcagent -d /app -s /sbin/nologin rcagent

# Copy Python packages from builder (system-wide location)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy application code
COPY config/ config/
COPY src/ src/

# Create directory for runtime data
RUN mkdir -p /app/data && chown -R rcagent:rcagent /app

# Switch to non-root user
USER rcagent

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Expose API port
EXPOSE 8000

# Run the application
CMD ["python", "-m", "src.main"]
