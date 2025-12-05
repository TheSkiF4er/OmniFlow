# OmniFlow/connectors/gemini_connector.py
"""
OmniFlow — Gemini connector
============================

Production-ready, dependency-light Python connector for Gemini-style models/APIs
(Google Gemini-compatible HTTP endpoints or similar). Designed for use inside
OmniFlow plugins or other services that need a robust, testable wrapper.

Features
- Sync and async clients (auto-selects available HTTP library)
- Environment-variable-driven configuration (GEMINI_API_KEY, GEMINI_BASE_URL)
- Safe defaults: timeouts, retries with exponential backoff + jitter
- Structured logging and optional metrics hook
- Small, well-defined error hierarchy for callers to react to
- Response normalization for common shapes (text completion, chat messages)
- Optional streaming helper for async streaming endpoints (SSE / chunked JSON)
- Type hints, docstrings and example usage

Notes
- This connector avoids hard dependency on any vendor SDK. If you prefer vendor
  SDKs (google-cloud, etc.), consider wrapping them similarly or swapping the
  implementation where needed.
- The connector expects the target HTTP API to accept JSON POST requests and
  to return JSON responses; adapt payload keys (messages/prompt, model, etc.)
  to your specific Gemini-compatible endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

# Optional imports
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

try:
    import aiohttp  # type: ignore
except Exception:
    aiohttp = None  # type: ignore

# Public API
__all__ = [
    "GeminiError",
    "GeminiAPIError",
    "GeminiRateLimitError",
    "GeminiAuthError",
    "GeminiConnectorConfig",
    "GeminiClient",
    "AsyncGeminiClient",
]

logger = logging.getLogger("omniflow.connectors.gemini")
logger.addHandler(logging.NullHandler())


# ---- Exceptions ----
class GeminiError(Exception):
    """Base class for Gemini connector errors."""


class GeminiAPIError(GeminiError):
    """Generic API error (non-2xx)."""

    def __init__(self, status_code: int, body: Optional[Dict[str, Any]] = None, message: Optional[str] = None):
        super().__init__(message or f"Gemini API error: {status_code}")
        self.status_code = status_code
        self.body = body or {}


class GeminiRateLimitError(GeminiAPIError):
    """Raised on 429 responses or explicit rate-limit signals."""


class GeminiAuthError(GeminiAPIError):
    """Raised on 401/403 responses indicating invalid credentials."""


# ---- Config dataclass ----
@dataclass
class GeminiConnectorConfig:
    """
    Configuration for the Gemini connector.

    Use `GeminiConnectorConfig.from_env()` to pick values from environment variables.

    Environment variables read (defaults shown):
      - GEMINI_API_KEY (required)
      - GEMINI_BASE_URL (default: https://api.gemini.example/v1/generate)
      - GEMINI_MODEL (default: gemini-1)
      - GEMINI_TIMEOUT (default: 30.0)
      - GEMINI_MAX_RETRIES (default: 3)
      - GEMINI_BACKOFF_FACTOR (default: 0.6)
    """

    api_key: str
    base_url: str = "https://api.gemini.example/v1/generate"
    model: str = "gemini-1"
    timeout: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 0.6
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None
    default_headers: Optional[Dict[str, str]] = None

    @staticmethod
    def from_env(prefix: str = "GEMINI") -> "GeminiConnectorConfig":
        api_key = os.getenv(f"{prefix}_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise GeminiError("Missing Gemini API key. Set GEMINI_API_KEY in environment.")
        base_url = os.getenv(f"{prefix}_BASE_URL") or os.getenv("GEMINI_BASE_URL") or "https://api.gemini.example/v1/generate"
        model = os.getenv(f"{prefix}_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-1"
        timeout = float(os.getenv(f"{prefix}_TIMEOUT", os.getenv("GEMINI_TIMEOUT", "30.0")))
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", os.getenv("GEMINI_MAX_RETRIES", "3")))
        backoff_factor = float(os.getenv(f"{prefix}_BACKOFF_FACTOR", os.getenv("GEMINI_BACKOFF_FACTOR", "0.6")))
        return GeminiConnectorConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )


# ---- Helpers: backoff, jitter, metrics ----
def _compute_backoff(attempt: int, factor: float = 0.6, jitter: float = 0.1) -> float:
    """
    Exponential backoff with jitter.
    attempt: 0-based attempt index.
    """
    exp = factor * (2 ** attempt)
    # add +/- jitter relative to exp
    jitter_amount = exp * jitter * (random.random() * 2 - 1)
    return max(0.0, exp + jitter_amount)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


def _build_auth_header(api_key: str) -> Dict[str, str]:
    # Common pattern: Authorization: Bearer <key> ; some vendors use x-api-key
    return {"Authorization": f"Bearer {api_key}"}


# ---- Response normalization ----
def _normalize_response_body(body: Any) -> Dict[str, Any]:
    """
    Normalize candidate response shapes into a common dict:
    { "text": str, "choices": [...], "raw": <body> }
    """
    if body is None:
        return {"text": "", "choices": [], "raw": body}
    if isinstance(body, str):
        return {"text": body, "choices": [], "raw": body}
    if isinstance(body, dict):
        # Common shapes:
        #  - { "text": "..." }
        #  - { "output": [ { "content": "..." } ] }  (some Gemini-like shapes)
        #  - { "candidates": [ { "content": "..." } ] }
        text = ""
        choices: List[Dict[str, Any]] = []

        # direct
        if "text" in body and isinstance(body["text"], str):
            text = body["text"]
            choices = [{"text": text}]
        # t2-like candidates
        elif "candidates" in body and isinstance(body["candidates"], list) and body["candidates"]:
            for c in body["candidates"]:
                if isinstance(c, dict):
                    t = c.get("text") or c.get("content") or c.get("message") or ""
                    choices.append({"text": t, **{k: v for k, v in c.items() if k != "text"}})
            text = choices[0]["text"] if choices else ""
        # output list (e.g., [{ "content": "..." }])
        elif "output" in body and isinstance(body["output"], list) and body["output"]:
            for item in body["output"]:
                if isinstance(item, dict):
                    t = item.get("content") or item.get("text") or ""
                    choices.append({"text": t})
            text = choices[0]["text"] if choices else ""
        else:
            # try to find nested text in known keys
            for key in ("message", "completion", "result", "reply"):
                v = body.get(key) if isinstance(body, dict) else None
                if isinstance(v, str):
                    text = v
                    break
            if not text:
                # fallback: stringify
                try:
                    text = json.dumps(body)
                except Exception:
                    text = str(body)
        return {"text": text, "choices": choices, "raw": body}
    # fallback
    try:
        text = json.dumps(body)
    except Exception:
        text = str(body)
    return {"text": text, "choices": [], "raw": body}


# ---- Synchronous client ----
class GeminiClient:
    """
    Synchronous connector for Gemini-like HTTP APIs.

    Example:
        cfg = GeminiConnectorConfig.from_env()
        client = GeminiClient(cfg)
        resp = client.generate(prompt="Hello", max_tokens=100)
        print(resp["text"])
    """

    def __init__(self, config: GeminiConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if requests is None:
            logger.warning("`requests` not installed — GeminiClient will not work without it.")

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        headers.update(_build_auth_header(self.cfg.api_key))
        if self.cfg.default_headers:
            headers.update(self.cfg.default_headers)
        return headers

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if requests is None:
            raise GeminiError("missing dependency `requests` for synchronous GeminiClient")

        url = self.cfg.base_url
        headers = self._headers()
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self.cfg.max_retries:
            start = time.time()
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.cfg.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                logger.warning("GeminiClient request exception (attempt %d/%d): %s — retry after %.2fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                self.metrics("request_error", {"attempt": attempt + 1, "error": str(exc)})
                if attempt >= self.cfg.max_retries:
                    break
                time.sleep(wait)
                attempt += 1
                continue

            latency = time.time() - start
            status = resp.status_code
            try:
                body = resp.json()
            except Exception:
                body = {"raw_text": resp.text}

            self.metrics("request_completed", {"status": status, "latency": latency, "attempt": attempt + 1})
            logger.debug("GeminiClient response status=%d body=%s", status, json.dumps(body)[:1000])

            if 200 <= status < 300:
                return {"status": status, "body": body}
            if status in (401, 403):
                raise GeminiAuthError(status, body, "authentication failed")
            if status == 429:
                self.metrics("rate_limited", {"status": status, "attempt": attempt + 1})
                if attempt >= self.cfg.max_retries:
                    raise GeminiRateLimitError(status, body, "rate limited")
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                time.sleep(wait)
                attempt += 1
                continue
            if 500 <= status < 600:
                if attempt >= self.cfg.max_retries:
                    raise GeminiAPIError(status, body, "server error")
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                logger.warning("GeminiClient server error %d — retry after %.2fs", status, wait)
                time.sleep(wait)
                attempt += 1
                continue
            # other 4xx
            raise GeminiAPIError(status, body, f"API error {status}")

        raise GeminiError(f"request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    def generate(
        self,
        prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        High-level generate method. Supports single-text prompt or chat-style messages.

        Returns normalized dict: { "text": str, "choices": [...], "raw": <server body> }
        """
        payload: Dict[str, Any] = {"model": model or self.cfg.model, "max_tokens": max_tokens, "temperature": temperature}
        if messages is not None:
            payload["messages"] = messages
        else:
            payload["prompt"] = prompt or ""
        payload.update(kwargs)

        resp = self._request(payload)
        norm = _normalize_response_body(resp.get("body"))
        norm["status_code"] = resp.get("status")
        return norm

    def chat(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """Convenience alias for generate with chat-style messages."""
        return self.generate(messages=messages, **kwargs)


# ---- Asynchronous client ----
class AsyncGeminiClient:
    """
    Async connector for Gemini-like HTTP APIs using aiohttp.

    Example:
        cfg = GeminiConnectorConfig.from_env()
        client = AsyncGeminiClient(cfg)
        resp = await client.generate_async(prompt="Hello")
        print(resp["text"])
    """

    def __init__(self, config: GeminiConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if aiohttp is None:
            logger.warning("`aiohttp` not installed — AsyncGeminiClient will not function without it.")

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        headers.update(_build_auth_header(self.cfg.api_key))
        if self.cfg.default_headers:
            headers.update(self.cfg.default_headers)
        return headers

    async def _request_async(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if aiohttp is None:
            raise GeminiError("missing dependency `aiohttp` for AsyncGeminiClient")

        url = self.cfg.base_url
        headers = self._headers()
        attempt = 0
        last_exc: Optional[Exception] = None
        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout)

        while attempt <= self.cfg.max_retries:
            start = time.time()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        status = resp.status
                        try:
                            body = await resp.json()
                        except Exception:
                            text = await resp.text()
                            body = {"raw_text": text}

                        latency = time.time() - start
                        self.metrics("request_completed", {"status": status, "latency": latency, "attempt": attempt + 1})
                        logger.debug("AsyncGeminiClient response status=%d body=%s", status, json.dumps(body)[:1000])

                        if 200 <= status < 300:
                            return {"status": status, "body": body}
                        if status in (401, 403):
                            raise GeminiAuthError(status, body, "authentication failed")
                        if status == 429:
                            self.metrics("rate_limited", {"status": status, "attempt": attempt + 1})
                            if attempt >= self.cfg.max_retries:
                                raise GeminiRateLimitError(status, body, "rate limited")
                            wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                            await asyncio.sleep(wait)
                            attempt += 1
                            continue
                        if 500 <= status < 600:
                            if attempt >= self.cfg.max_retries:
                                raise GeminiAPIError(status, body, "server error")
                            wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                            logger.warning("AsyncGeminiClient server error %d — retry after %.2fs", status, wait)
                            await asyncio.sleep(wait)
                            attempt += 1
                            continue
                        raise GeminiAPIError(status, body, f"API error {status}")
            except Exception as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                logger.warning("AsyncGeminiClient exception (attempt %d/%d): %s — retry after %.2fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                self.metrics("request_error", {"attempt": attempt + 1, "error": str(exc)})
                if attempt >= self.cfg.max_retries:
                    break
                await asyncio.sleep(wait)
                attempt += 1
                continue

        raise GeminiError(f"async request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    async def generate_async(
        self,
        prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model or self.cfg.model, "max_tokens": max_tokens, "temperature": temperature}
        if messages is not None:
            payload["messages"] = messages
        else:
            payload["prompt"] = prompt or ""
        payload.update(kwargs)

        resp = await self._request_async(payload)
        norm = _normalize_response_body(resp.get("body"))
        norm["status_code"] = resp.get("status")
        return norm

    async def chat_async(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        return await self.generate_async(messages=messages, **kwargs)

    async def stream_chat_async(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Optional helper for streaming endpoints that return chunks (SSE, chunked JSON).
        This implementation tries to read chunked responses and call `on_chunk` for each
        textual chunk received. It falls back to full-body behavior if the server doesn't stream.

        Note: Streaming shapes differ between vendors — adapt parsing as needed.
        """
        if aiohttp is None:
            raise GeminiError("aiohttp required for streaming API")
        url = self.cfg.base_url
        headers = self._headers()
        payload = {"model": model or self.cfg.model, "messages": messages}
        payload.update(kwargs)
        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                status = resp.status
                if status >= 400:
                    try:
                        body = await resp.json()
                    except Exception:
                        body = {"raw": await resp.text()}
                    if status in (401, 403):
                        raise GeminiAuthError(status, body, "authentication failed")
                    if status == 429:
                        raise GeminiRateLimitError(status, body, "rate limited")
                    raise GeminiAPIError(status, body, f"API error {status}")

                # Stream handling: read text chunks
                aggregated = ""
                async for chunk, _ in resp.content.iter_chunks():
                    if not chunk:
                        continue
                    s = chunk.decode("utf-8", errors="ignore")
                    aggregated += s
                    if on_chunk:
                        try:
                            on_chunk(s)
                        except Exception:
                            logger.exception("on_chunk handler raised")
                # Try to parse final body
                try:
                    body = json.loads(aggregated) if aggregated.strip().startswith("{") else {"raw": aggregated}
                except Exception:
                    body = {"raw": aggregated}
                return _normalize_response_body(body)

# ---- Minimal CLI & usage helpers (not executed on import) ----
def _example_sync() -> None:  # pragma: no cover - example only
    cfg = GeminiConnectorConfig.from_env()
    client = GeminiClient(cfg)
    resp = client.generate(prompt="Write a friendly 3-word greeting.", max_tokens=50)
    print(">>>", resp["text"])


async def _example_async() -> None:  # pragma: no cover - example only
    cfg = GeminiConnectorConfig.from_env()
    client = AsyncGeminiClient(cfg)
    resp = await client.generate_async(prompt="Write a friendly 3-word greeting.", max_tokens=50)
    print(">>>", resp["text"])


if __name__ == "__main__":  # pragma: no cover - example only
    logging.basicConfig(level=logging.INFO)
    try:
        _example_sync()
    except Exception as exc:
        logger.exception("example sync failed: %s", exc)
    # run async example
    if aiohttp is not None:
        asyncio.run(_example_async())
