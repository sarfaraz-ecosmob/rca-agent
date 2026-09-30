"""
AI-Powered Root Cause Analysis Engine.

Supports multiple AI providers:
- Ollama (local, default)
- LM Studio (local)
- OpenAI
- Azure OpenAI
- Anthropic Claude
- Google Gemini

Analyzes container logs, identifies root cause, and suggests solutions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from config.settings import AIProvider, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RCAAnalyzer:
    """
    Root Cause Analysis engine powered by AI.
    Supports multiple providers with a unified interface.
    """

    def __init__(self):
        self.provider = settings.AI_PROVIDER
        # Runtime-overridable configuration (from dashboard settings)
        self._runtime_provider: Optional[AIProvider] = None
        self._runtime_api_key: Optional[str] = None
        self._runtime_model: Optional[str] = None
        self._runtime_base_url: Optional[str] = None
        self._client = self._create_client()

    def configure(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """Reconfigure the AI provider at runtime (from dashboard settings).

        Passing None keeps the current value. An empty string clears it.
        """
        if provider:
            try:
                self._runtime_provider = AIProvider(provider)
            except ValueError:
                logger.warning(f"Unknown AI provider: {provider}")
        if api_key is not None:
            self._runtime_api_key = api_key or None
        if model is not None:
            self._runtime_model = model or None
        if base_url is not None:
            self._runtime_base_url = base_url or None
        # Rebuild the client with the updated config
        self.provider = self._runtime_provider or settings.AI_PROVIDER
        self._client = self._create_client()
        logger.info(f"AI analyzer reconfigured | provider={self.provider.value}")

    def _create_client(self):
        """Create the appropriate AI client based on configuration."""
        if self.provider == AIProvider.OLLAMA:
            return OllamaClient(
                base_url=self._runtime_base_url or settings.OLLAMA_URL,
                model=self._runtime_model or settings.OLLAMA_MODEL,
            )
        elif self.provider == AIProvider.LM_STUDIO:
            return LMStudioClient(
                base_url=self._runtime_base_url or settings.LM_STUDIO_URL,
                model=self._runtime_model or settings.LM_STUDIO_MODEL,
            )
        elif self.provider == AIProvider.OPENAI:
            return OpenAIClient(
                api_key=self._runtime_api_key or settings.OPENAI_API_KEY or "",
                model=self._runtime_model or settings.OPENAI_MODEL,
            )
        elif self.provider == AIProvider.ANTHROPIC:
            return AnthropicClient(
                api_key=self._runtime_api_key or settings.ANTHROPIC_API_KEY or "",
                model=self._runtime_model or settings.ANTHROPIC_MODEL,
            )
        elif self.provider == AIProvider.GEMINI:
            return GeminiClient(
                api_key=self._runtime_api_key or settings.GEMINI_API_KEY or "",
                model=self._runtime_model or settings.GEMINI_MODEL,
            )
        elif self.provider == AIProvider.OPENROUTER:
            return OpenRouterClient(
                api_key=self._runtime_api_key or settings.OPENROUTER_API_KEY or "",
                model=self._runtime_model or settings.OPENROUTER_MODEL,
            )
        else:
            logger.warning(f"Unknown AI provider: {self.provider}, falling back to Ollama")
            return OllamaClient(
                base_url=self._runtime_base_url or settings.OLLAMA_URL,
                model=self._runtime_model or settings.OLLAMA_MODEL,
            )

    async def analyze(
        self,
        container_name: str,
        container_id: str,
        log_snippet: str,
        error_type: str = "",
        severity: str = "medium",
    ) -> Dict[str, Any]:
        """
        Perform RCA analysis on a log snippet.
        
        Args:
            container_name: Name of the container
            container_id: Docker container ID
            log_snippet: Log lines surrounding the error
            error_type: Type of error detected
            severity: Error severity
            
        Returns:
            RCA analysis result with root cause, solutions, etc.
        """
        prompt = self._build_analysis_prompt(
            container_name=container_name,
            container_id=container_id,
            log_snippet=log_snippet,
            error_type=error_type,
            severity=severity,
        )

        try:
            response = await self._client.analyze(prompt)
            result = self._parse_response(response, container_id, container_name)
            logger.info(
                f"RCA completed | container={container_name} "
                f"confidence={result.get('confidence', 0):.2f} "
                f"provider={self.provider.value}"
            )
            return result
        except Exception as e:
            logger.error(f"RCA analysis failed for {container_name}: {e}")
            return self._fallback_analysis(
                container_id=container_id,
                container_name=container_name,
                error_type=error_type,
                log_snippet=log_snippet,
            )

    # Orchestration-tool guidance per deployment environment.
    # "forbidden" tooling must NEVER appear in suggested fixes; "preferred"
    # tooling is what the AI should reference in remediation steps.
    _ENVIRONMENT_GUIDANCE = {
        "docker-compose": {
            "preferred": "Docker Compose (docker compose ...), plain docker CLI (docker ps/logs/restart/exec), and the application's own config",
            "forbidden": "kubectl, helm, k9s, istioctl, or any other Kubernetes/Service-Mesh command",
        },
        "docker": {
            "preferred": "plain docker CLI (docker ps/logs/restart/exec/inspect) and the application's own config",
            "forbidden": "kubectl, helm, or any other Kubernetes/Service-Mesh command",
        },
        "kubernetes": {
            "preferred": "kubectl and helm as appropriate",
            "forbidden": "docker compose or docker-swarm specific commands",
        },
        "vm": {
            "preferred": "systemd (systemctl/journalctl), process supervision, and the application's own config",
            "forbidden": "kubectl, helm, docker compose, or any container-orchestration command",
        },
        "bare-metal": {
            "preferred": "systemd (systemctl/journalctl), init scripts, and the application's own config",
            "forbidden": "kubectl, helm, docker compose, or any container-orchestration command",
        },
        "other": {
            "preferred": "tooling that actually exists in this deployment",
            "forbidden": "orchestration commands that do not apply to this deployment",
        },
    }

    def _deployment_context(self) -> str:
        """Build the deployment-environment section of the analysis prompt.

        Keeps AI-suggested remediation realistic: only tools that actually
        exist in the configured environment may be suggested.
        """
        env_type = (settings.ENVIRONMENT_TYPE or "docker-compose").strip().lower()
        guidance = self._ENVIRONMENT_GUIDANCE.get(env_type, self._ENVIRONMENT_GUIDANCE["other"])
        return (
            "## Deployment Environment\n"
            f"- Environment type: {env_type}\n"
            f"- Available tooling for remediation: {guidance['preferred']}\n"
            f"- STRICTLY FORBIDDEN in every fix: {guidance['forbidden']}. "
            "Do not mention these commands even as alternatives. "
            "Only suggest commands that can actually be run in this environment.\n"
        )

    def _build_analysis_prompt(
        self,
        container_name: str,
        container_id: str,
        log_snippet: str,
        error_type: str,
        severity: str,
    ) -> str:
        """Build the analysis prompt for the AI model."""
        return f"""You are an expert Site Reliability Engineer (SRE) and DevOps root cause analysis AI. 
Your task is to analyze the following container logs and provide a comprehensive RCA.

## Context
- Container Name: {container_name}
- Container ID: {container_id}
- Error Type: {error_type or "Unknown"}
- Severity: {severity}

{self._deployment_context()}
## Log Snippet (last 200 lines before error + 50 lines after)
```
{log_snippet[:8000]}
```

## Analysis Requirements

Please analyze and provide a JSON response with the following exact structure:

```json
{{
    "root_cause": {{
        "description": "Clear, concise description of the root cause",
        "component": "The specific component that failed",
        "dependency": "The dependency that caused the issue (if applicable)",
        "probability": 0.95
    }},
    "severity": "critical|high|medium|low",
    "error_type": "The specific error type identified",
    "explanation": "Detailed explanation of why the issue happened, including relevant technical details",
    "possible_impact": "Description of the potential impact on the system/users",
    "solutions": {{
        "immediate_fix": "Step-by-step immediate remediation actions to resolve the issue right now",
        "permanent_fix": "Long-term solution to prevent this issue permanently",
        "preventive_action": "Measures to prevent similar issues in the future",
        "best_practices": "Recommended best practices relevant to this issue"
    }},
    "confidence": 0.90,
    "references": [
        "Reference 1: Brief description",
        "Reference 2: Brief description"
    ]
}}
```

Focus on accuracy and actionable insights. Be specific about commands, configuration changes, and monitoring checks."""

    def _parse_response(
        self, response: str, container_id: str, container_name: str
    ) -> Dict[str, Any]:
        """Parse the AI response into structured RCA result."""
        try:
            json_str = self._extract_json(response)
            data = json.loads(json_str)

            return {
                "container_id": container_id,
                "container_name": container_name,
                "error_type": data.get("error_type", "Unknown"),
                "severity": data.get("severity", "medium"),
                "root_cause": {
                    "description": data.get("root_cause", {}).get("description", "Unknown"),
                    "component": data.get("root_cause", {}).get("component", ""),
                    "dependency": data.get("root_cause", {}).get("dependency", ""),
                    "probability": float(data.get("root_cause", {}).get("probability", 0.0)),
                },
                "explanation": data.get("explanation", "No explanation provided"),
                "solutions": {
                    "immediate_fix": data.get("solutions", {}).get("immediate_fix", ""),
                    "permanent_fix": data.get("solutions", {}).get("permanent_fix", ""),
                    "preventive_action": data.get("solutions", {}).get("preventive_action", ""),
                    "best_practices": data.get("solutions", {}).get("best_practices", ""),
                },
                "confidence": float(data.get("confidence", 0.0)),
                "possible_impact": data.get("possible_impact", ""),
                "references": data.get("references", []),
                "analyzed_at": datetime.utcnow().isoformat(),
                "ai_provider": self.provider.value,
            }
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse AI response as JSON: {e}")
            return self._extract_structured_text(response, container_id, container_name)

    def _extract_json(self, response: str) -> str:
        """Extract a JSON object from an AI response that may contain markdown, nested code blocks, etc."""
        text = response.strip()

        # Strip markdown code fences wrapping the entire response
        if text.startswith("```json"):
            text = text[len("```json"):]
        elif text.startswith("```"):
            text = text[len("```"):]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Fast path: try parsing directly
        try:
            json.loads(text)
            return text
        except (json.JSONDecodeError, ValueError):
            pass

        # Braces-counting: find the outermost {...} block
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start != -1:
                    candidate = text[start : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except (json.JSONDecodeError, ValueError):
                        continue

        # Last resort: try parsing the whole cleaned text
        return text

    def _extract_structured_text(
        self, response: str, container_id: str, container_name: str
    ) -> Dict[str, Any]:
        """Fallback: extract structured info from non-JSON response."""
        lines = response.split("\n")
        return {
            "container_id": container_id,
            "container_name": container_name,
            "error_type": "Unknown",
            "severity": "medium",
            "root_cause": {
                "description": response[:500],
                "component": "",
                "dependency": "",
                "probability": 0.5,
            },
            "explanation": response,
            "solutions": {
                "immediate_fix": "See analysis details",
                "permanent_fix": "See analysis details",
                "preventive_action": "See analysis details",
                "best_practices": "See analysis details",
            },
            "confidence": 0.5,
            "possible_impact": "",
            "references": [],
            "analyzed_at": datetime.utcnow().isoformat(),
            "ai_provider": self.provider.value,
        }

    def _fallback_analysis(
        self,
        container_id: str,
        container_name: str,
        error_type: str,
        log_snippet: str,
    ) -> Dict[str, Any]:
        """Generate a basic analysis when AI is unavailable."""
        return {
            "container_id": container_id,
            "container_name": container_name,
            "error_type": error_type or "Unknown",
            "severity": "medium",
            "root_cause": {
                "description": "AI analysis unavailable. Manual investigation required.",
                "component": "",
                "dependency": "",
                "probability": 0.0,
            },
            "explanation": (
                "The AI analysis engine is currently unavailable. "
                "Please check the AI provider configuration and ensure the service is running."
            ),
            "solutions": {
                "immediate_fix": "1. Check container logs manually\n"
                                f"2. Verify AI provider ({self.provider.value}) is running\n"
                                "3. Review the error pattern in the logs",
                "permanent_fix": (
                    "1. Configure the AI provider correctly\n"
                    "2. Ensure network connectivity to the AI service\n"
                    "3. Set up monitoring for the AI analysis service"
                ),
                "preventive_action": (
                    "1. Implement AI provider health checks\n"
                    "2. Configure fallback AI providers\n"
                    "3. Set up alerting for AI service availability"
                ),
                "best_practices": (
                    "1. Always have at least one local AI provider as fallback\n"
                    "2. Monitor AI service health and latency\n"
                    "3. Cache analysis results for known error patterns"
                ),
            },
            "confidence": 0.0,
            "possible_impact": "Impact assessment unavailable without AI analysis.",
            "references": [],
            "analyzed_at": datetime.utcnow().isoformat(),
            "ai_provider": self.provider.value,
        }

    @property
    def provider_name(self) -> str:
        """Get the current AI provider name."""
        return self.provider.value

    def is_configured(self) -> bool:
        """Check if the AI provider is properly configured."""
        return self._client.is_configured()


# --- AI Provider Implementations ---


class BaseAIClient:
    """Base class for AI provider clients."""

    # HTTP statuses worth retrying: rate limits and transient server errors.
    RETRYABLE_STATUS = (429, 500, 502, 503, 504)

    def __init__(self):
        self.timeout = 60
        self.max_retries = 3
        self.retry_base_delay = 5.0  # seconds; doubled on each retry

    async def analyze(self, prompt: str) -> str:
        """Send analysis prompt to AI and return response."""
        raise NotImplementedError

    def is_configured(self) -> bool:
        """Check if this client is properly configured."""
        raise NotImplementedError

    async def _post_with_retry(
        self,
        client: "httpx.AsyncClient",
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        label: str = "AI",
    ) -> "httpx.Response":
        """POST with exponential backoff on 429/5xx (e.g. free-tier rate limits).

        Waits retry_base_delay * 2^attempt between attempts (5s, 10s, 20s by
        default), then raises the last error if still failing.
        """
        last_exc: Optional[httpx.HTTPStatusError] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in self.RETRYABLE_STATUS and attempt < self.max_retries:
                    delay = self.retry_base_delay * (2**attempt)
                    logger.warning(
                        f"{label} request got HTTP {status} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}), "
                        f"retrying in {delay:.0f}s"
                    )
                    await asyncio.sleep(delay)
                    last_exc = e
                else:
                    raise
        raise last_exc  # pragma: no cover - defensive


class OllamaClient(BaseAIClient):
    """Client for local Ollama models."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3"):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = 10  # Quick timeout; falls back gracefully if Ollama unavailable

    async def analyze(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "top_p": 0.9,                            "num_predict": 2048,  # Limit tokens for faster generation
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")

    def is_configured(self) -> bool:
        return bool(self.base_url)


class LMStudioClient(BaseAIClient):
    """Client for LM Studio local models."""

    def __init__(self, base_url: str = "http://localhost:1234/v1", model: str = "local-model"):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def analyze(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are an expert SRE and DevOps root cause analysis AI.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    def is_configured(self) -> bool:
        return bool(self.base_url)


class OpenAIClient(BaseAIClient):
    """Client for OpenAI API."""

    def __init__(self, api_key: str = "", model: str = "gpt-4"):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.timeout = 120

    async def analyze(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are an expert SRE and DevOps root cause analysis AI. Always respond with valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)


class OpenRouterClient(BaseAIClient):
    """Client for OpenRouter API (OpenAI-compatible)."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str = "", model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.timeout = 120

    async def analyze(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ai-rca-agent",
            "X-Title": "AI RCA Agent",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert SRE and DevOps root cause analysis AI. Always respond with valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 2000,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._post_with_retry(
                client,
                f"{self.BASE_URL}/chat/completions",
                headers=headers,
                payload=payload,
                label="OpenRouter",
            )
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {}) or {}
            content = message.get("content") or ""
            if not content.strip():
                # Reasoning models (e.g. nemotron via OpenRouter) may spend
                # their completion budget thinking and return empty `content`
                # while the actual text lands in `reasoning`/
                # `reasoning_content`. Fall back so the RCA is not lost.
                content = (
                    message.get("reasoning")
                    or message.get("reasoning_content")
                    or ""
                )
            return content

    def is_configured(self) -> bool:
        return bool(self.api_key)


class AnthropicClient(BaseAIClient):
    """Client for Anthropic Claude API."""

    def __init__(self, api_key: str = "", model: str = "claude-3-haiku-20240307"):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.timeout = 120

    async def analyze(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 2000,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"You are an expert SRE root cause analysis AI. "
                                      f"Respond with valid JSON only.\n\n{prompt}",
                        }
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("content", [{}])[0].get("text", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)


class GeminiClient(BaseAIClient):
    """Client for Google Gemini API."""

    def __init__(self, api_key: str = "", model: str = "gemini-pro"):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.timeout = 120

    async def analyze(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent?key={self.api_key}",
                json={
                    "contents": [
                        {
                            "parts": [
                                {
                                    "text": f"You are an expert SRE root cause analysis AI. "
                                           f"Respond with valid JSON only.\n\n{prompt}"
                                }
                            ]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 2000,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return ""

    def is_configured(self) -> bool:
        return bool(self.api_key)
