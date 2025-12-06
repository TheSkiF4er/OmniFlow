# OmniFlow/connectors/openai_connector.py
"""
OmniFlow — OpenAI connector
============================

Production-ready, dependency-light Python connector for OpenAI-style HTTP APIs
(or compatible endpoints). Designed for use inside OmniFlow plugins and workers.

Features
- Sync and async clients (auto-uses `requests` and `aiohttp` when available).
- Optional integration with the official OpenAI Python SDK if installed.
- Environment-variable-driven configuration (OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL).
- Sensible defaults for timeouts, retries, and exponential backoff with jitter.
- Clear exceptions and small, well-documented surface for callers.
- Support for streaming responses (async & sync) where the API offers chunked SSE or chunked JSON.
- Structured logging and optional metrics hook.
- Minimal dependencies by default; good defaults for production.

Notes
- This connector assumes the target API accepts JSON POST and returns JSON (or streaming chunks).
  You may need to adapt payload keys depending on the exact OpenAI-compatible endpoint.
- For heavy usage prefer using the vendor SDK; this module provides a thin, testable wrapper.
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
from typing import Any, AsyncIterator, Callable, Dict, Generator, Iterator, List, Optional, Tuple

# Optional third-party libraries — `requests` and `aiohttp` are used when available.
try:
    import requests  # type: ignore
    from requests import Response as RequestsResponse  # type: ignore
except Exception:  # pragma: no cover - optional import
    requests = None
    RequestsResponse = None  # type: ignore

try:
    import aiohttp  # type: ignore
    from aiohttp import ClientResponse as AiohttpResponse  # type: ignore
except Exception:  # pragma: no cover - optional import
    aiohttp = None
    AiohttpResponse = None  # type: ignore

# Optionally support official openai package if installed
try:
    import openai  # type: ignore
    _HAS_OPENAI_SDK = True
except Exception:
    openai = None  # type: ignore
    _HAS_OPENAI_SDK = False

logger = logging.getLogger("omniflow.connectors.openai")
logger.addHandler(logging.NullHandler())

__all__ = [
    "OpenAIError",
    "OpenAIAuthError",
    "OpenAIRateLimitError",
    "OpenAIAPIError",
    "OpenAIConnectorConfig",
    "OpenAIClient",
    "AsyncOpenAIClient",
]


# ---- Exceptions ----
class OpenAIError(Exception):
    """Base class for OpenAI connector errors."""


class OpenAIAuthError(OpenAIError):
    """Authentication / authorization errors (401 / 403)."""


class OpenAIRateLimitError(OpenAIAPIError := type("OpenAIAPIError", (OpenAIError,), {})):  # keep backwards compat name
    """Rate limit error (429)."""


# Recreate proper OpenAIAPIError type if not already created above
if "OpenAIAPIError" not in globals():
    class OpenAIAPIError(OpenAIError):
        """Generic API error (non-2xx)."""


# ---- Config dataclass ----
@dataclass
class OpenAIConnectorConfig:
    """
    Configuration for OpenAI connector.

    Environment variables:
      - OPENAI_API_KEY (required unless SDK configured elsewhere)
      - OPENAI_BASE_URL (optional; default official API: https://api.openai.com/v1)
      - OPENAI_MODEL (default model to use)
      - OPENAI_TIMEOUT (seconds; default: 30)
      - OPENAI_MAX_RETRIES (default: 3)
      - OPENAI_BACKOFF_FACTOR (default: 0.6)
      - OPENAI_JITTER (default: 0.2)
    """

    api_key: Optional[str]
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"  # non-binding default; callers often override
    timeout: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 0.6
    jitter: float = 0.2
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None
    default_headers: Optional[Dict[str, str]] = None

    @staticmethod
    def from_env(prefix: str = "OPENAI") -> "OpenAIConnectorConfig":
        api_key = os.getenv(f"{prefix}_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv(f"{prefix}_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        model = os.getenv(f"{prefix}_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o"
        timeout = float(os.getenv(f"{prefix}_TIMEOUT", os.getenv("OPENAI_TIMEOUT", "30.0")))
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", os.getenv("OPENAI_MAX_RETRIES", "3")))
        backoff_factor = float(os.getenv(f"{prefix}_BACKOFF_FACTOR", os.getenv("OPENAI_BACKOFF_FACTOR", "0.6")))
        jitter = float(os.getenv(f"{prefix}_JITTER", os.getenv("OPENAI_JITTER", "0.2")))
        return OpenAIConnectorConfig(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            jitter=jitter,
        )


# ---- Helpers: backoff, metrics, headers ----
def _compute_backoff(attempt: int, factor: float = 0.6, jitter: float = 0.2) -> float:
    """
    Exponential backoff with jitter. attempt is 0-based.
    """
    base = factor * (2 ** attempt)
    jitter_amt = base * jitter * (random.random() * 2 - 1)
    return max(0.0, base + jitter_amt)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


def _build_auth_headers(cfg: OpenAIConnectorConfig) -> Dict[str, str]:
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.default_headers:
        hdrs.update(cfg.default_headers)
    if cfg.api_key:
        hdrs["Authorization"] = f"Bearer {cfg.api_key}"
    return hdrs


# ---- Response normalization helpers ----
def _extract_text_from_response(body: Any) -> str:
    """
    Try to extract readable text from common OpenAI-like response shapes.
    """
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        # Chat completions (OpenAI v1/chat/completions): choices[].message.content or choices[].text
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                # chat-style message content
                msg = first.get("message") or first.get("delta") or {}
                if isinstance(msg, dict):
                    content = msg.get("content") or msg.get("text")
                    if isinstance(content, str):
                        return content
                # legacy text
                for key in ("text", "completion", "message", "content"):
                    if key in first and isinstance(first[key], str):
                        return first[key]
            # fallback to stringifying first choice
            try:
                return json.dumps(first)
            except Exception:
                return str(first)
        # direct fields
        for k in ("text", "completion", "message", "content"):
            v = body.get(k)
            if isinstance(v, str):
                return v
        # last resort: pretty JSON
        try:
            return json.dumps(body)
        except Exception:
            return str(body)
    # fallback
    try:
        return json.dumps(body)
    except Exception:
        return str(body)


# ---- Sync client (requests) ----
class OpenAIClient:
    """
    Synchronous OpenAI connector using `requests`.

    Example:
        cfg = OpenAIConnectorConfig.from_env()
        client = OpenAIClient(cfg)
        resp = client.create_completion({"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]})
        print(resp["text"])
    """

    def __init__(self, cfg: OpenAIConnectorConfig):
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        if requests is None:
            logger.warning("`requests` not installed — OpenAIClient will not function without it.")
        # If openai SDK present and api_key set, configure it optionally so users can call directly.
        if _HAS_OPENAI_SDK and cfg.api_key:
            try:
                # Configure the SDK to use provided key & base_url if needed
                openai.api_key = cfg.api_key
                # Some SDK versions expose api_base
                if hasattr(openai, "api_base"):
                    openai.api_base = cfg.base_url
            except Exception:
                logger.debug("Failed to configure openai SDK from connector", exc_info=True)

    def _request(self, path: str, payload: Dict[str, Any], stream: bool = False) -> RequestsResponse:
        if requests is None:
            raise OpenAIError("`requests` is required for OpenAIClient but is not installed.")
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = _build_auth_headers(self.cfg)
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self.cfg.max_retries:
            start = time.time()
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.cfg.timeout, stream=stream)
            except requests.RequestException as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor, self.cfg.jitter)
                logger.warning("OpenAIClient request exception (attempt %d): %s — retrying after %.2fs", attempt + 1, exc, wait)
                self.metrics("request_exception", {"attempt": attempt + 1, "error": str(exc)})
                if attempt >= self.cfg.max_retries:
                    break
                time.sleep(wait)
                attempt += 1
                continue

            latency = time.time() - start
            self.metrics("request_completed", {"path": path, "status": resp.status_code, "latency": latency, "attempt": attempt + 1})

            if 200 <= resp.status_code < 300:
                return resp
            if resp.status_code in (401, 403):
                raise OpenAIAuthError(f"authentication failed: {resp.status_code} - {resp.text}")
            if resp.status_code == 429:
                # Rate limited
                retry_after = resp.headers.get("Retry-After")
                self.metrics("rate_limited", {"attempt": attempt + 1, "status": 429})
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                    except Exception:
                        pass
                if attempt >= self.cfg.max_retries:
                    raise OpenAIRateLimitError(f"rate limited: {resp.status_code}",)
                wait = _compute_backoff(attempt, self.cfg.backoff_factor, self.cfg.jitter)
                time.sleep(wait)
                attempt += 1
                continue
            if 500 <= resp.status_code < 600:
                # Server error — retry
                if attempt >= self.cfg.max_retries:
                    raise OpenAIAPIError(f"server error: {resp.status_code} - {resp.text}")
                wait = _compute_backoff(attempt, self.cfg.backoff_factor, self.cfg.jitter)
                logger.warning("Server error %d — retrying after %.2fs", resp.status_code, wait)
                time.sleep(wait)
                attempt += 1
                continue
            # Other 4xx errors considered permanent
            raise OpenAIAPIError(f"api error: {resp.status_code} - {resp.text}")

        raise OpenAIError(f"request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    def create_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a completion/chat call synchronously.

        Payload shape should conform to the API you target (e.g., chat/completions).
        Example:
            {"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}], "max_tokens": 100}
        """
        # Choose path: chat completions if messages present, else completions
        path = "chat/completions" if "messages" in payload else "completions"
        resp = self._request(path, payload, stream=False)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        text = _extract_text_from_response(body)
        return {"status_code": resp.status_code, "body": body, "text": text}

    def create_completion_stream(self, payload: Dict[str, Any]) -> Iterator[str]:
        """
        Synchronous streaming call. Yields text chunks as they arrive.

        Note: Streaming implementations vary by vendor. For OpenAI-compatible streaming
        that responds with SSE or chunked JSON, this helper will attempt to read lines
        from the response stream and yield text-bearing fragments. Callers should be
        prepared to assemble chunks into a full response.
        """
        path = "chat/completions" if "messages" in payload else "completions"
        resp = self._request(path, payload, stream=True)

        # Try chunked reading
        if resp is None:
            return
            yield  # keep as generator

        # There are different streaming formats: SSE or newline-delimited JSON.
        # We'll read iter_lines and attempt to parse JSON chunks where possible.
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                # Remove SSE prefix if present: "data: "
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if line == "[DONE]":
                    break
                # Try parse JSON
                try:
                    obj = json.loads(line)
                    text = _extract_text_from_response(obj)
                    yield text
                except Exception:
                    # Not JSON — yield raw line
                    yield line
        finally:
            try:
                resp.close()
            except Exception:
                pass


# ---- Async client (aiohttp) ----
class AsyncOpenAIClient:
    """
    Asynchronous OpenAI connector using `aiohttp`.

    Example:
        cfg = OpenAIConnectorConfig.from_env()
        client = AsyncOpenAIClient(cfg)
        resp = await client.create_completion({"model": "gpt-4o", "messages": [...]})
        print(resp["text"])
    """

    def __init__(self, cfg: OpenAIConnectorConfig):
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        if aiohttp is None:
            logger.warning("`aiohttp` not installed — AsyncOpenAIClient will not function without it.")
        # configure SDK if present
        if _HAS_OPENAI_SDK and cfg.api_key:
            try:
                openai.api_key = cfg.api_key
                if hasattr(openai, "api_base"):
                    openai.api_base = cfg.base_url
            except Exception:
                logger.debug("Failed to configure openai SDK from async connector", exc_info=True)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_or_new(self) -> aiohttp.ClientSession:
        if self._session is None:
            headers = _build_auth_headers(self.cfg)
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _request_async(self, path: str, payload: Dict[str, Any], stream: bool = False) -> Tuple[int, Any, Optional[AiohttpResponse]]:
        if aiohttp is None:
            raise OpenAIError("`aiohttp` is required for AsyncOpenAIClient but is not installed.")
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        session = await self._session_or_new()
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self.cfg.max_retries:
            start = time.time()
            try:
                resp = await session.post(url, json=payload, timeout=self.cfg.timeout, ssl=True)
                # If streaming requested, we will return response object for caller to stream manually
                if stream:
                    return resp.status, None, resp
                text = await resp.text()
            except Exception as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor, self.cfg.jitter)
                logger.warning("AsyncOpenAIClient request exception (attempt %d): %s — retrying after %.2fs", attempt + 1, exc, wait)
                self.metrics("request_exception", {"attempt": attempt + 1, "error": str(exc)})
                if attempt >= self.cfg.max_retries:
                    break
                await asyncio.sleep(wait)
                attempt += 1
                continue

            latency = time.time() - start
            self.metrics("request_completed", {"path": path, "status": resp.status, "latency": latency, "attempt": attempt + 1})

            if 200 <= resp.status < 300:
                try:
                    body = json.loads(text) if text else None
                except Exception:
                    body = {"raw": text}
                return resp.status, body, None
            if resp.status in (401, 403):
                raise OpenAIAuthError(f"authentication failed: {resp.status} - {text}")
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                self.metrics("rate_limited", {"attempt": attempt + 1, "status": 429})
                if retry_after:
                    try:
                        await asyncio.sleep(float(retry_after))
                    except Exception:
                        pass
                if attempt >= self.cfg.max_retries:
                    raise OpenAIRateLimitError(f"rate limited: {resp.status}")
                wait = _compute_backoff(attempt, self.cfg.backoff_factor, self.cfg.jitter)
                await asyncio.sleep(wait)
                attempt += 1
                continue
            if 500 <= resp.status < 600:
                if attempt >= self.cfg.max_retries:
                    # try to return body text for debugging
                    raise OpenAIAPIError(f"server error: {resp.status} - {text}")
                wait = _compute_backoff(attempt, self.cfg.backoff_factor, self.cfg.jitter)
                logger.warning("Server error %d — retrying after %.2fs", resp.status, wait)
                await asyncio.sleep(wait)
                attempt += 1
                continue
            # Other 4xx
            raise OpenAIAPIError(f"api error: {resp.status} - {text}")

        raise OpenAIError(f"async request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    async def create_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = "chat/completions" if "messages" in payload else "completions"
        status, body, _ = await self._request_async(path, payload, stream=False)
        text = _extract_text_from_response(body)
        return {"status_code": status, "body": body, "text": text}

    async def create_completion_stream(self, payload: Dict[str, Any], on_chunk: Optional[Callable[[str], None]] = None) -> AsyncIterator[str]:
        """
        Asynchronous streaming generator. Yields chunks of text as strings.

        The connector attempts to read chunked responses from the server (SSE or chunked JSON).
        `on_chunk` can be provided to receive each chunk as it arrives.
        """
        path = "chat/completions" if "messages" in payload else "completions"
        status, _, resp = await self._request_async(path, payload, stream=True)
        if resp is None:
            return
            yield  # maintain generator type

        # Aiohttp streaming approach: iterate over content chunks and attempt to parse JSON per line.
        try:
            async for raw in resp.content:
                if not raw:
                    continue
                try:
                    s = raw.decode("utf-8")
                except Exception:
                    s = raw.decode("utf-8", errors="ignore")
                # break into lines if server sends newline-delimited chunks
                for line in s.splitlines():
                    if not line:
                        continue
                    # strip SSE prefix "data: "
                    part = line.strip()
                    if part.startswith("data:"):
                        part = part[len("data:"):].strip()
                    if part == "[DONE]":
                        return
                    # try parse JSON
                    try:
                        obj = json.loads(part)
                        text = _extract_text_from_response(obj)
                    except Exception:
                        # not JSON, yield raw text
                        text = part
                    if on_chunk:
                        try:
                            on_chunk(text)
                        except Exception:
                            logger.exception("on_chunk handler raised")
                    yield text
        finally:
            try:
                await resp.release()
            except Exception:
                try:
                    resp.close()
                except Exception:
                    pass

    async def __aenter__(self):
        await self._session_or_new()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# ---- Small convenience factories ----
def default_openai_client_from_env() -> OpenAIClient:
    cfg = OpenAIConnectorConfig.from_env()
    if cfg.api_key is None and not _HAS_OPENAI_SDK:
        logger.warning("No OPENAI_API_KEY set and OpenAI SDK not present — calls may fail without credentials.")
    return OpenAIClient(cfg)


def default_async_openai_client_from_env() -> AsyncOpenAIClient:
    cfg = OpenAIConnectorConfig.from_env()
    if cfg.api_key is None and not _HAS_OPENAI_SDK:
        logger.warning("No OPENAI_API_KEY set and OpenAI SDK not present — calls may fail without credentials.")
    return AsyncOpenAIClient(cfg)


# ---- Example usage (not executed on import) ----
if __name__ == "__main__":  # pragma: no cover - examples only
    logging.basicConfig(level=logging.INFO)
    cfg = OpenAIConnectorConfig.from_env()
    print("Config:", cfg)
    client = OpenAIClient(cfg)
    try:
        sample = {"model": cfg.model, "messages": [{"role": "user", "content": "Say hello in three words."}], "max_tokens": 50}
        res = client.create_completion(sample)
        print("Sync text:", res.get("text"))
    except Exception as exc:
        logger.exception("Sync example failed: %s", exc)

    if aiohttp is not None:
        async def async_demo():
            ac = AsyncOpenAIClient(cfg)
            res = await ac.create_completion({"model": cfg.model, "messages": [{"role":"user","content":"Say hi"}]})
            print("Async text:", res.get("text"))
            # streaming example
            async for chunk in ac.create_completion_stream({"model": cfg.model, "messages": [{"role":"user","content":"Stream one word at a time"}], "stream": True}):
                print("chunk:", chunk)
            await ac.close()
        try:
            asyncio.run(async_demo())
        except Exception:
            logger.exception("Async example failed")
    else:
        logger.info("aiohttp not installed; skipping async examples")
