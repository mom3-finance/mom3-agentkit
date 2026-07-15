"""OpenAI-compatible LLM client for the mom3 agentkit.

Wraps the configured chat-completions endpoint (default: https://api.aiand.com/v1)
using the official `openai` SDK, authenticated via the AGENT_APIKEY env var.

Graceful degradation: when AGENT_APIKEY is absent (or the SDK is unavailable) the
client reports unavailable and callers fall back to heuristic responses instead of
raising, so the API never 500s on a missing key.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

# Defaults — overridable via env so the same code targets any OpenAI-compatible proxy.
_DEFAULT_BASE_URL = "https://api.aiand.com/v1"
_DEFAULT_MODEL = "zai-org/glm-5.2"


class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint."""

    def __init__(self) -> None:
        self.api_key = os.getenv("AGENT_APIKEY", "").strip()
        self.base_url = os.getenv("AGENT_API_BASE", _DEFAULT_BASE_URL).strip()
        self.model = os.getenv("AGENT_MODEL", _DEFAULT_MODEL).strip()
        self._client: Any = None  # lazy: only constructed if a key exists

        if not self.api_key:
            logger.warning(
                "AGENT_APIKEY not set — LLM features will run in heuristic fallback mode."
            )
            return

        try:
            from openai import OpenAI  # imported lazily so a missing dep doesn't crash startup
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.error(f"openai SDK unavailable, LLM disabled: {exc}")
            self.api_key = ""
            return

        try:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            logger.info(f"LLM client ready (base_url={self.base_url}, model={self.model})")
        except Exception as exc:
            logger.error(f"Failed to initialize LLM client: {exc}")
            self.api_key = ""
            self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key and self._client is not None)

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 900,
        timeout: float = 30.0,
    ) -> str:
        """Return the assistant reply text, or "" if unavailable/failed.

        Callers should treat an empty string as "no LLM answer — use fallback".
        """
        if not self.available:
            return ""

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            content = response.choices[0].message.content
            return (content or "").strip()
        except Exception as exc:
            logger.error(f"LLM chat failed: {exc}")
            return ""


# Singleton ---------------------------------------------------------------------
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
