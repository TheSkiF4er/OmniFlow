# OmniFlow/connectors/anthropic_connector.py
"""
OmniFlow — Anthropic connector
================================

Production-ready, dependency-light Python connector for Anthropic-style completions
(Claude-family or compatible HTTP APIs). Designed for use inside OmniFlow plugins
or other services that need a robust, testable wrapper around the Anthropic API.

Features
- Sync and async clients (auto-selects available HTTP library)
- Environment-variable-driven configuration (ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL)
- Safe default timeouts and configurable retry/backoff for transient errors
- Structured logging and simple metrics hooks (callable hooks)
- Lightweight error hierarchy for clear handling in calling code
- Type hints and small usage examples in docstrings

NOTES
- This connector purposely avoids hard dependency on any Anthropic SDK so it can be
  used in minimal environments. If `anthropic` SDK is installed, you can still use that
  by adapting this connector or swapping implementations.
- The connector assumes the HTTP API accepts a POST JSON body and returns JSON with
  a completion text. Depending on your target Anthropic endpoint, you may need to adapt
  the request shape (e.g., `messages` vs `prompt` vs `input`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

# Try to import requests & aiohttp optionally; handle gracefully if not present.
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - optional import
    requests = None  # type: ignore

try:
    import aiohttp  # type: ignore
except Exception:  # pragma: no cover - optional import
    aiohttp = None  # type: ignore

# Public objects
__all__ = [
    "AnthropicError",
    "AnthropicAPIError",
    "AnthropicRateLimitError",
    "AnthropicAuthError",
    "AnthropicConnectorConfig",
    "AnthropicClient",
    "AsyncAnthropicClient",
]

logger = logging.getLogger("omniflow.connectors.anthropic")
logger.addHandler(logging.NullHandler())


# ---- Exceptions ----
class AnthropicError(Exception):
    """Base class for Anthropic connector errors."""


class AnthropicAPIError(AnthropicError):
    """Generic API error (non-2xx)."""

    def __init__(self, status_code: int, body: Optional[Dict[str, Any]] = None, message: Optional[str] = None):
        super().__init__(message or f"Anthropic API error: {status_code}")
        self.status_code = status_code
        self.body = body or {}


class AnthropicRateLimitError(AnthropicAPIError):
    """Raised on 429 responses or explicit rate-limit signals."""


class AnthropicAuthError(AnthropicAPIError):
    """Raised on 401/403 responses indicating invalid credentials."""


# ---- Config dataclass ----
@dataclass
class AnthropicConnectorConfig:
    """
    Configuration for the Anthropic connector.

    Common usage: `AnthropicConnectorConfig.from_env()` to pick values from environment.
    """

    api_key: str
    base_url: str = "https://api.anthropic.com/v1/complete"  # sensible default; adapt if needed
    model: str = "claude-v1"  # non-binding default; callers may override
    timeout: float = 30.0  # seconds
    max_retries: int = 3
    backoff_factor: float = 0.6  # seconds, multiplied exponentially
    # Hooks: optional callables that receive metrics/event info
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None

    @staticmethod
    def from_env(prefix: str = "ANTHROPIC") -> "AnthropicConnectorConfig":
        """
        Create config by reading environment variables.

        - ANTHROPIC_API_KEY (required)
        - ANTHROPIC_BASE_URL (optional)
        - ANTHROPIC_MODEL (optional)
        - ANTHROPIC_TIMEOUT (optional)
        - ANTHROPIC_MAX_RETRIES (optional)
        - ANTHROPIC_BACKOFF_FACTOR (optional)
        """
        api_key = os.getenv(f"{prefix}_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise AnthropicError("Missing Anthropic API key. Set ANTHROPIC_API_KEY in environment.")
        base_url = os.getenv(f"{prefix}_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com/v1/complete"
        model = os.getenv(f"{prefix}_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-v1"
        timeout = float(os.getenv(f"{prefix}_TIMEOUT", os.getenv("ANTHROPIC_TIMEOUT", "30.0")))
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", os.getenv("ANTHROPIC_MAX_RETRIES", "3")))
        backoff_factor = float(os.getenv(f"{prefix}_BACKOFF_FACTOR", os.getenv("ANTHROPIC_BACKOFF_FACTOR", "0.6")))
        return AnthropicConnectorConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )


# ---- Helper utilities ----
def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    """Default no-op metrics hook used if none provided."""
    logger.debug("metrics_hook(%s): %s", event, payload)


def _compute_backoff(attempt: int, factor: float = 0.6, jitter: float = 0.1) -> float:
    """
    Simple exponential backoff with jitter.
    attempt: 0-based attempt index.
    """
    base = factor * (2 ** attempt)
    # small jitter
    return base * (1.0 + (jitter * (0.5 - (time.time() % 1))))


# ---- Sync client ----
class AnthropicClient:
    """
    Synchronous connector for Anthropic HTTP-style APIs.

    Example:
        cfg = AnthropicConnectorConfig.from_env()
        client = AnthropicClient(cfg)
        resp = client.complete(prompt="Hello", max_tokens=200)
        print(resp.text)
    """

    def __init__(self, config: AnthropicConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if requests is None:
            logger.warning("`requests` not installed — AnthropicClient will not function without it.")

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.cfg.api_key,  # many Anthropic APIs use 'x-api-key' or Authorization; adapt if needed.
        }

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if requests is None:
            raise AnthropicError("missing dependency `requests` for synchronous AnthropicClient")

        url = self.cfg.base_url
        headers = self._build_headers()
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self.cfg.max_retries:
            attempt += 1
            start = time.time()
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.cfg.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("AnthropicClient request exception (attempt %d/%d): %s — retrying after %.2fs", attempt, self.cfg.max_retries, exc, wait)
                self.metrics("request_error", {"attempt": attempt, "error": str(exc)})
                if attempt > self.cfg.max_retries:
                    break
                time.sleep(wait)
                continue

            latency = time.time() - start
            status = resp.status_code
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text}

            self.metrics("request_completed", {"status": status, "latency": latency, "attempt": attempt})
            logger.debug("AnthropicClient response (status=%d) body=%s", status, json.dumps(body)[:1000])

            if 200 <= status < 300:
                return body
            if status in (401, 403):
                raise AnthropicAuthError(status, body, "authentication failed")
            if status == 429:
                # rate limited — maybe wait and retry
                self.metrics("rate_limited", {"status": status, "attempt": attempt})
                if attempt > self.cfg.max_retries:
                    raise AnthropicRateLimitError(status, body, "rate limited")
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                time.sleep(wait)
                continue
            if 500 <= status < 600:
                # server error — retry up to max_retries
                if attempt > self.cfg.max_retries:
                    raise AnthropicAPIError(status, body, "server error")
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("AnthropicClient server error %d — retrying after %.2fs", status, wait)
                time.sleep(wait)
                continue
            # Other 4xx errors are considered permanent for this request
            raise AnthropicAPIError(status, body, f"Anthropic API error {status}")

        # Exhausted retries
        raise AnthropicError(f"request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    def complete(
        self,
        prompt: Optional[str] = None,
        messages: Optional[Any] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        stop: Optional[Any] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        High-level completion method (sync).

        - prompt: older-style textual prompt (string)
        - messages: chat-like messages if your target API supports it
        - model: override default model from config
        - returns: dict with at least {"text": <completion string>, "raw": <full API response>}
        """
        model_to_use = model or self.cfg.model
        payload = {
            "model": model_to_use,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # adapt depending on provided inputs — many Anthropic endpoints expect `prompt` or `messages`
        if messages is not None:
            payload["messages"] = messages
        else:
            payload["prompt"] = prompt or ""

        if stop is not None:
            payload["stop"] = stop

        payload.update(kwargs)

        body = self._request(payload)

        # Normalize: attempt to extract a textual completion in common response shapes.
        text = None
        if isinstance(body, dict):
            # Typical shapes: {"completion": "..."} or {"choices":[{"text": "..."}]} etc.
            text = body.get("completion") or body.get("text")
            if text is None and "choices" in body and isinstance(body["choices"], list) and len(body["choices"]) > 0:
                first = body["choices"][0]
                if isinstance(first, dict):
                    text = first.get("text") or first.get("completion") or first.get("message") or None
        if text is None:
            # Fallback: stringify body
            text = json.dumps(body)

        return {"text": text, "raw": body}


# ---- Async client ----
class AsyncAnthropicClient:
    """
    Asynchronous Anthropic connector using aiohttp.

    Example:
        cfg = AnthropicConnectorConfig.from_env()
        async_client = AsyncAnthropicClient(cfg)
        resp = await async_client.complete_async(prompt="Hello")
        print(resp["text"])
    """

    def __init__(self, config: AnthropicConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if aiohttp is None:  # pragma: no cover - optional import
            logger.warning("`aiohttp` not installed — AsyncAnthropicClient will not function without it.")

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.cfg.api_key,
        }

    async def _request_async(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if aiohttp is None:
            raise AnthropicError("missing dependency `aiohttp` for AsyncAnthropicClient")

        url = self.cfg.base_url
        headers = self._build_headers()
        attempt = 0
        last_exc: Optional[Exception] = None

        # Use a single ClientSession for the request; caller may adapt for reuse
        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout)
        while attempt <= self.cfg.max_retries:
            attempt += 1
            start = time.time()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        status = resp.status
                        try:
                            body = await resp.json()
                        except Exception:
                            text = await resp.text()
                            body = {"raw": text}

                        latency = time.time() - start
                        self.metrics("request_completed", {"status": status, "latency": latency, "attempt": attempt})
                        logger.debug("AsyncAnthropicClient response (status=%d) body=%s", status, json.dumps(body)[:1000])

                        if 200 <= status < 300:
                            return body
                        if status in (401, 403):
                            raise AnthropicAuthError(status, body, "authentication failed")
                        if status == 429:
                            self.metrics("rate_limited", {"status": status, "attempt": attempt})
                            if attempt > self.cfg.max_retries:
                                raise AnthropicRateLimitError(status, body, "rate limited")
                            wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                            await asyncio.sleep(wait)
                            continue
                        if 500 <= status < 600:
                            if attempt > self.cfg.max_retries:
                                raise AnthropicAPIError(status, body, "server error")
                            wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                            logger.warning("AsyncAnthropicClient server error %d — retrying after %.2fs", status, wait)
                            await asyncio.sleep(wait)
                            continue
                        raise AnthropicAPIError(status, body, f"Anthropic API error {status}")
            except Exception as exc:  # network or session error
                last_exc = exc
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("AsyncAnthropicClient request exception (attempt %d/%d): %s — retrying after %.2fs", attempt, self.cfg.max_retries, exc, wait)
                self.metrics("request_error", {"attempt": attempt, "error": str(exc)})
                if attempt > self.cfg.max_retries:
                    break
                await asyncio.sleep(wait)
                continue

        raise AnthropicError(f"async request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    async def complete_async(
        self,
        prompt: Optional[str] = None,
        messages: Optional[Any] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        stop: Optional[Any] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Async completion method.

        Returns dict: {"text": <completion>, "raw": <API response>}
        """
        model_to_use = model or self.cfg.model
        payload = {
            "model": model_to_use,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if messages is not None:
            payload["messages"] = messages
        else:
            payload["prompt"] = prompt or ""

        if stop is not None:
            payload["stop"] = stop
        payload.update(kwargs)

        body = await self._request_async(payload)

        text = None
        if isinstance(body, dict):
            text = body.get("completion") or body.get("text")
            if text is None and "choices" in body and isinstance(body["choices"], list) and len(body["choices"]) > 0:
                first = body["choices"][0]
                if isinstance(first, dict):
                    text = first.get("text") or first.get("completion") or first.get("message") or None
        if text is None:
            text = json.dumps(body)
        return {"text": text, "raw": body}


# ---- Minimal CLI & usage helpers (not executed on import) ----
def _example_usage_sync() -> None:  # pragma: no cover - example only
    cfg = AnthropicConnectorConfig.from_env()
    client = AnthropicClient(cfg)
    resp = client.complete(prompt="Say hello in 3 words.", max_tokens=50)
    print(resp["text"])


async def _example_usage_async() -> None:  # pragma: no cover - example only
    cfg = AnthropicConnectorConfig.from_env()
    client = AsyncAnthropicClient(cfg)
    resp = await client.complete_async(prompt="Say hello in 3 words.", max_tokens=50)
    print(resp["text"])


if __name__ == "__main__":  # pragma: no cover - local example run
    # Simple CLI example: run synchronous client if invoked directly
    try:
        _example_usage_sync()
    except Exception as exc:
        logger.exception("example run failed: %s", exc)
