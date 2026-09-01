"""
Runtime settings store and component registry.

Allows the dashboard to read and update application configuration at runtime
(without restarting the agent):
- Google Chat webhook URL
- AI provider / API key / model / base URL
- Custom log file sources

Values are persisted in the `app_settings` table so they survive restarts,
and fall back to environment-based defaults when unset.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from config.settings import get_settings
from src.db.database import async_session_factory
from src.db.models import SettingModel

logger = logging.getLogger(__name__)
settings = get_settings()


# --- Setting keys ---
KEY_GOOGLE_CHAT = "google_chat_webhook_url"
KEY_AI_PROVIDER = "ai_provider"
KEY_AI_API_KEY = "ai_api_key"
KEY_AI_MODEL = "ai_model"
KEY_AI_BASE_URL = "ai_base_url"
KEY_CUSTOM_LOG_FILES = "custom_log_files"


# --- Component registry (populated by main.py at startup) ---
# Holds live component references so API routes can reconfigure them.
components: Dict[str, Any] = {
    "google_chat_notifier": None,
    "rca_analyzer": None,
    "file_monitor": None,
}


def env_defaults() -> Dict[str, Any]:
    """Environment-based defaults for each setting key."""
    ai_provider = settings.AI_PROVIDER.value
    model_by_provider = {
        "ollama": settings.OLLAMA_MODEL,
        "lm_studio": settings.LM_STUDIO_MODEL,
        "openai": settings.OPENAI_MODEL,
        "anthropic": settings.ANTHROPIC_MODEL,
        "google_gemini": settings.GEMINI_MODEL,
        "openrouter": settings.OPENROUTER_MODEL,
    }
    url_by_provider = {
        "ollama": settings.OLLAMA_URL,
        "lm_studio": settings.LM_STUDIO_URL,
    }
    return {
        KEY_GOOGLE_CHAT: settings.GOOGLE_CHAT_WEBHOOK_URL or "",
        KEY_AI_PROVIDER: ai_provider,
        KEY_AI_API_KEY: "",
        KEY_AI_MODEL: model_by_provider.get(ai_provider, ""),
        KEY_AI_BASE_URL: url_by_provider.get(ai_provider, ""),
        KEY_CUSTOM_LOG_FILES: settings.custom_log_sources,
    }


async def get_all_settings() -> Dict[str, Any]:
    """Return runtime settings merged over environment defaults (DB wins)."""
    result = env_defaults()
    try:
        async with async_session_factory() as session:
            rows = (await session.execute(select(SettingModel))).scalars().all()
            for row in rows:
                result[row.key] = row.value
    except Exception as e:
        logger.warning(f"Failed to load runtime settings: {e}")
    return result


async def get_setting(key: str, default: Any = None) -> Any:
    """Read a single runtime setting (DB value, else env default, else fallback)."""
    all_settings = await get_all_settings()
    return all_settings.get(key, default)


async def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Persist one or more setting values to the app_settings table."""
    async with async_session_factory() as session:
        try:
            for key, value in updates.items():
                if value is None:
                    continue
                result = await session.execute(
                    select(SettingModel).where(SettingModel.key == key)
                )
                row = result.scalar_one_or_none()
                if row:
                    row.value = value
                    row.updated_at = datetime.utcnow()
                else:
                    session.add(SettingModel(key=key, value=value))
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to update settings: {e}")
            raise
    return await get_all_settings()


async def delete_setting(key: str) -> None:
    """Remove a runtime setting so the env default applies again."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(SettingModel).where(SettingModel.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            await session.delete(row)
            await session.commit()


# --- Application of runtime settings to live components ---


async def apply_runtime_settings() -> Dict[str, Any]:
    """
    Push persisted runtime settings into the live components:
    Google Chat notifier, AI analyzer, and file log monitor.
    """
    cfg = await get_all_settings()

    notifier = components.get("google_chat_notifier")
    if notifier is not None:
        webhook = cfg.get(KEY_GOOGLE_CHAT, "")
        notifier.configure(webhook_url=webhook)

    analyzer = components.get("rca_analyzer")
    if analyzer is not None:
        analyzer.configure(
            provider=cfg.get(KEY_AI_PROVIDER, ""),
            api_key=cfg.get(KEY_AI_API_KEY, "") or None,
            model=cfg.get(KEY_AI_MODEL, "") or None,
            base_url=cfg.get(KEY_AI_BASE_URL, "") or None,
        )

    file_monitor = components.get("file_monitor")
    if file_monitor is not None:
        await _apply_log_sources(file_monitor, cfg.get(KEY_CUSTOM_LOG_FILES, []))

    return cfg


async def _apply_log_sources(file_monitor, sources: List[dict]) -> None:
    """Ensure the file monitor tails exactly the configured log sources."""
    if not isinstance(sources, list):
        return
    desired = {
        str(s.get("path", "")).strip(): s for s in sources if s.get("path")
    }
    try:
        active = file_monitor.get_active_sources() if hasattr(file_monitor, "get_active_sources") else {}
    except Exception:
        active = {}

    for path, source in desired.items():
        if path not in active:
            await file_monitor.add_source(
                path=path, name=source.get("name", "")
            )

    # Remove sources no longer configured
    for path in list(active.keys()):
        if path not in desired:
            source_id = active[path]
            await file_monitor.remove_source(source_id)


# --- Convenience helpers used by API routes ---


async def sync_custom_log_sources(sources: List[dict]) -> List[dict]:
    """Persist the custom log file list and reapply to the file monitor."""
    normalized = [
        {"path": str(s["path"]).strip(), "name": str(s.get("name", "")).strip()}
        for s in sources
        if s.get("path") and str(s["path"]).strip()
    ]
    await update_settings({KEY_CUSTOM_LOG_FILES: normalized})
    file_monitor = components.get("file_monitor")
    if file_monitor is not None:
        await _apply_log_sources(file_monitor, normalized)
    return normalized
