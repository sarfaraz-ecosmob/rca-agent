# Minimal image for the RCA agent test error-generator.
# Produces realistic error logs so the agent's DockerMonitor ->
# ErrorDetector -> RCA (OpenRouter) -> Google Chat pipeline can be
# exercised end-to-end in a real container.

FROM python:3.12-alpine

LABEL maintainer="AI RCA Agent Team"
LABEL description="Test error generator container for AI RCA Agent E2E validation"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as a non-root user for consistency with the agent's security posture
RUN addgroup -S errgen && adduser -S errgen -G errgen

COPY generate_errors.py /app/generate_errors.py
WORKDIR /app

USER errgen

# Default: one finite burst then exit (driven by the E2E test script).
# Override the command for continuous mode: python generate_errors.py (with ERROR_MODE=loop)
CMD ["python", "generate_errors.py"]
