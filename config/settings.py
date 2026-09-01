"""
Configuration Management for AI RCA Agent.

Uses pydantic-settings to load from environment variables and .env files.
Supports multiple AI providers, notification channels, and database backends.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIProvider(str, Enum):
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GEMINI = "google_gemini"
    OPENROUTER = "openrouter"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Logging ---
    LOG_LEVEL: LogLevel = LogLevel.INFO
    SCAN_INTERVAL: int = 5

    # --- AI Provider ---
    AI_PROVIDER: AIProvider = AIProvider.OLLAMA

    # Ollama
    OLLAMA_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "llama3"

    # LM Studio
    LM_STUDIO_URL: str = "http://host.docker.internal:1234/v1"
    LM_STUDIO_MODEL: str = "local-model"

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4"

    # OpenRouter (OpenAI-compatible API — 20+ free models available)
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_KEY: Optional[str] = None
    AZURE_OPENAI_DEPLOYMENT: Optional[str] = None
    AZURE_OPENAI_API_VERSION: str = "2024-02-15-preview"

    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-haiku-20240307"

    # Google Gemini
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-pro"

    # --- Notifications ---
    GOOGLE_CHAT_WEBHOOK_URL: Optional[str] = None

    # SMTP
    SMTP_SERVER: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USE_TLS: bool = True
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAIL_FROM: str = "rcagent@example.com"
    EMAIL_TO: str = "devops@example.com"

    # --- Database ---
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "rcagent"
    POSTGRES_PASSWORD: str = "rcagent_secret"
    POSTGRES_DB: str = "rcagent"

    # --- Analysis ---
    MAX_LOG_LINES: int = 200
    MAX_LOG_LINES_AFTER: int = 50
    RETENTION_DAYS: int = 30
    BATCH_ANALYSIS_INTERVAL: int = 60
    DEDUPLICATION_WINDOW: int = 300

    # --- API ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: str = "*"

    # --- Monitoring ---
    CONTAINER_SCAN_INTERVAL: int = 5
    LOG_POLL_INTERVAL: float = 1.0
    MAX_CONTAINERS: int = 1000

    # --- Custom Log File Monitoring (non-Docker applications) ---
    # Comma-separated absolute paths to log files to tail in real-time.
    # Set these to monitor applications that run outside Docker containers.
    CUSTOM_LOG_FILES: str = ""
    # Optional comma-separated display names matching CUSTOM_LOG_FILES order.
    # If omitted, the file basename is used.
    CUSTOM_LOG_NAMES: str = ""
    # Seconds between file reads while polling for new log lines.
    LOG_FILE_POLL_INTERVAL: float = 0.5
    # Number of existing lines seeded into the buffer when monitoring starts.
    LOG_FILE_TAIL_LINES: int = 100

    @property
    def custom_log_sources(self) -> list:
        """
        Parse CUSTOM_LOG_FILES / CUSTOM_LOG_NAMES into a list of sources.

        Returns a list of dicts: [{"path": ..., "name": ...}, ...]
        """
        paths = [p.strip() for p in self.CUSTOM_LOG_FILES.split(",") if p.strip()]
        names = [n.strip() for n in self.CUSTOM_LOG_NAMES.split(",") if n.strip()]
        return [
            {
                "path": path,
                "name": names[i] if i < len(names) and names[i] else os.path.basename(path),
            }
            for i, path in enumerate(paths)
        ]

    @property
    def database_url(self) -> str:
        """Get async database URL."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_sync_url(self) -> str:
        """Get synchronous database URL (for Alembic)."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @field_validator("SCAN_INTERVAL", "CONTAINER_SCAN_INTERVAL")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 300:
            return 300
        return v

    @field_validator("MAX_LOG_LINES")
    @classmethod
    def validate_max_log_lines(cls, v: int) -> int:
        if v < 50:
            return 50
        if v > 5000:
            return 5000
        return v

    @field_validator("RETENTION_DAYS")
    @classmethod
    def validate_retention(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 365:
            return 365
        return v


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
