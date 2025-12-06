# OmniFlow/connectors/http_connector.py
"""
OmniFlow — HTTP connector
==========================

Production-ready, dependency-light HTTP connector used throughout OmniFlow for
talking to REST/JSON services. Provides both synchronous (requests) and
asynchronous (aiohttp) clients with sensible defaults: timeouts, retries with
exponential backoff + jitter, optional auth header helpers, streaming helpers,
file upload/download, and simple pagination helpers.

Design goals
- Small surface area and clear exceptions for callers
- Reuse HTTP sessions for connection pooling
- Respect environment for proxies / SSL verification / extra headers
- Optional metrics hook for integration with telemetry systems
- Graceful handling of transient errors and status-based retries

Notes
- This module intentionally avoids hard-coding vendor-specific auth schemes.
  Pass `auth_token` and `auth_header` or custom headers as needed.
- `requests` and `aiohttp` are optional. If missing, the corresponding client
  will raise an informative error at runtime.
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
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Generator,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
)

# Optional dependencies
try:
    import requests  # type: ignore
    from requests import Session as RequestsSession  # type: ignore
    from requests.exceptions import RequestException  # type: ignore
except Exception:  # pragma: no cover - optional import
    requests = None
    RequestsSession = None
    RequestException = Exception  # fallback type

try:
    import aiohttp  # type: ignore
except Exception:  # pragma: no cover - optional import
    aiohttp = None

logger = logging.getLogger("omniflow.connectors.http")
logger.addHandler(logging.NullHandler())

__all__ = [
    "HttpError",
    "HttpRequestError",
    "HttpAuthError",
    "HttpRateLimitError",
    "HttpConnectorConfig",
    "HttpClient",
    "AsyncHttpClient",
    "default_http_client_from_env",
    "default_async_http_client_from_env",
]


# ---- Exceptions ----
class HttpError(Exception):
    """Base class for HTTP connector errors."""


class HttpRequestError(HttpError):
    """Network / request failure (after retries)."""


class HttpAuthError(HttpError):
    """Authentication failure (401/403)."""


class HttpRateLimitError(HttpError):
    """Rate limit encountered (429 / Retry-After)."""


# ---- Config dataclass ----
@dataclass
class HttpConnectorConfig:
    """
    HTTP connector configuration.

    - base_url: optional base URL; if provided it will be joined with request paths.
    - timeout: per-request total timeout (seconds).
    - max_retries: number of retry attempts for transient failures (0 = no retry).
    - backoff_factor: base seconds used for exponential backoff calculation.
    - jitter: jitter fraction applied to backoff to avoid thundering herd.
    - headers: default headers to include on each request.
    - auth_token: optional bearer-like token; set auth_header to change header name.
    - auth_header: header name to use for auth_token (default: "Authorization").
    - verify_ssl: whether to verify TLS certs (default: True).
    - proxies: optional dict of proxies to pass to requests; aiohttp will use env by default.
    - metrics_hook: optional callable(event: str, payload: dict) for telemetry.
    """

    base_url: Optional[str] = None
    timeout: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 0.6
    jitter: float = 0.2
    headers: Optional[Dict[str, str]] = None
    auth_token: Optional[str] = None
    auth_header: str = "Authorization"
    verify_ssl: bool = True
    proxies: Optional[Dict[str, str]] = None
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None

    @staticmethod
    def from_env(prefix: str = "HTTP") -> "HttpConnectorConfig":
        base_url = os.getenv(f"{prefix}_BASE_URL") or os.getenv("HTTP_BASE_URL")
        timeout = float(os.getenv(f"{prefix}_TIMEOUT", os.getenv("HTTP_TIMEOUT", "30.0")))
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", os.getenv("HTTP_MAX_RETRIES", "3")))
        backoff_factor = float(os.getenv(f"{prefix}_BACKOFF_FACTOR", os.getenv("HTTP_BACKOFF_FACTOR", "0.6")))
        jitter = float(os.getenv(f"{prefix}_JITTER", os.getenv("HTTP_JITTER", "0.2")))
        auth_token = os.getenv(f"{prefix}_AUTH_TOKEN") or os.getenv("HTTP_AUTH_TOKEN")
        auth_header = os.getenv(f"{prefix}_AUTH_HEADER") or os.getenv("HTTP_AUTH_HEADER") or "Authorization"
        verify_ssl = os.getenv(f"{prefix}_VERIFY_SSL", os.getenv("HTTP_VERIFY_SSL", "true")).lower() not in ("0", "false", "no")
        # proxies can be set via standard env vars; optionally supply a JSON in HTTP_PROXIES
        proxies_raw = os.getenv(f"{prefix}_PROXIES") or os.getenv("HTTP_PROXIES")
        proxies = None
        if proxies_raw:
            try:
                proxies = json.loads(proxies_raw)
            except Exception:
                proxies = None
        headers_raw = os.getenv(f"{prefix}_HEADERS")
        headers = None
        if headers_raw:
            try:
                headers = json.loads(headers_raw)
            except Exception:
                headers = None
        return HttpConnectorConfig(
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            jitter=jitter,
            headers=headers,
            auth_token=auth_token,
            auth_header=auth_header,
            verify_ssl=verify_ssl,
            proxies=proxies,
        )


# ---- Utilities ----
def _compute_backoff(attempt: int, factor: float = 0.6, jitter: float = 0.2) -> float:
    """
    Exponential backoff with jitter.
    attempt is 0-based attempt index.
    """
    base = factor * (2 ** attempt)
    jitter_amt = base * jitter * (random.random() * 2 - 1)
    return max(0.0, base + jitter_amt)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


def _join_url(base: Optional[str], path: str) -> str:
    if not base:
        return path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


# ---- Sync HTTP client (requests) ----
class HttpClient:
    """
    Synchronous HTTP client using `requests` Session.

    Example:
        cfg = HttpConnectorConfig.from_env()
        client = HttpClient(cfg)
        resp = client.get("/health")
        print(resp.status_code, resp.json())
    """

    def __init__(self, config: HttpConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if RequestsSession is None:
            raise RuntimeError("`requests` package is required for HttpClient but not installed.")
        self.session: RequestsSession = requests.Session()
        # Apply default headers
        if config.headers:
            self.session.headers.update(config.headers)
        if config.auth_token:
            self.session.headers.setdefault(config.auth_header, f"Bearer {config.auth_token}")
        # Respect proxies if provided; else rely on requests env vars
        if config.proxies:
            self.session.proxies.update(config.proxies)
        # Allow SSL verification toggle
        self._verify = bool(config.verify_ssl)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
        stream: bool = False,
        timeout: Optional[float] = None,
    ) -> requests.Response:
        url = _join_url(self.cfg.base_url, path)
        hdrs = {}
        if headers:
            hdrs.update(headers)
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= self.cfg.max_retries:
            attempt += 1
            start = time.time()
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=data,
                    headers=hdrs or None,
                    timeout=timeout or self.cfg.timeout,
                    stream=stream,
                    verify=self._verify,
                )
            except RequestException as exc:
                last_exc = exc
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter)
                logger.warning("HttpClient request exception (attempt %d/%d): %s — retrying after %.2fs", attempt, self.cfg.max_retries + 1, exc, wait)
                self.metrics("request_exception", {"attempt": attempt, "error": str(exc)})
                if attempt > self.cfg.max_retries:
                    break
                time.sleep(wait)
                continue

            latency = time.time() - start
            self.metrics("request_completed", {"method": method, "path": path, "status": resp.status_code, "latency": latency})
            # Rate limit handling
            if resp.status_code in (401, 403):
                raise HttpAuthError(f"authentication failed: {resp.status_code}")
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                self.metrics("rate_limited", {"status": 429, "attempt": attempt})
                if retry_after:
                    try:
                        wait = float(retry_after)
                        logger.warning("HttpClient rate-limited, respecting Retry-After: %.2fs", wait)
                        time.sleep(wait)
                    except Exception:
                        pass
                if attempt > self.cfg.max_retries:
                    raise HttpRateLimitError("rate limited")
                # backoff and retry
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                # server error -> maybe retry
                if attempt > self.cfg.max_retries:
                    raise HttpRequestError(f"server error: {resp.status_code}")
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter)
                logger.warning("HttpClient server error %d — retrying after %.2fs", resp.status_code, wait)
                time.sleep(wait)
                continue
            # success or other 4xx
            return resp

        raise HttpRequestError(f"request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    # Convenience methods
    def get(self, path: str, **kwargs) -> requests.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self._request("DELETE", path, **kwargs)

    def json(self, method: str, path: str, **kwargs) -> Any:
        """
        Make a request and attempt to parse JSON; fall back to text if parse fails.
        """
        resp = self._request(method, path, **kwargs)
        try:
            return resp.json()
        except Exception:
            return resp.text

    def stream_download(self, path: str, dest_path: str, chunk_size: int = 8192) -> None:
        """
        Stream download to a file path atomically.
        """
        resp = self._request("GET", path, stream=True)
        tmp = dest_path + ".tmp"
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, dest_path)

    def upload_file(self, path: str, file_field: str, file_path: str, extra_fields: Optional[Dict[str, Any]] = None) -> Any:
        """
        Upload a file using multipart/form-data. Returns parsed JSON or raw text.
        """
        if requests is None:
            raise RuntimeError("requests required for upload_file")
        url = _join_url(self.cfg.base_url, path)
        files = {file_field: open(file_path, "rb")}
        data = extra_fields or {}
        attempt = 0
        last_exc = None
        while attempt <= self.cfg.max_retries:
            attempt += 1
            try:
                resp = self.session.post(url, files=files, data=data, timeout=self.cfg.timeout, verify=self._verify)
            except RequestException as exc:
                last_exc = exc
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter)
                if attempt > self.cfg.max_retries:
                    break
                time.sleep(wait)
                continue
            if 200 <= resp.status_code < 300:
                try:
                    return resp.json()
                except Exception:
                    return resp.text
            if resp.status_code in (401, 403):
                raise HttpAuthError("authentication failed during upload")
            if resp.status_code == 429:
                if attempt > self.cfg.max_retries:
                    raise HttpRateLimitError("rate limited during upload")
                time.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter))
                continue
            if 500 <= resp.status_code < 600:
                if attempt > self.cfg.max_retries:
                    raise HttpRequestError(f"upload server error: {resp.status_code}")
                time.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter))
                continue
            raise HttpRequestError(f"upload failed: {resp.status_code} {resp.text}")
        raise HttpRequestError(f"upload failed after retries: {last_exc!s}")

    def paginate(self, path: str, params: Optional[Dict[str, Any]] = None, page_key: str = "page", per_page_key: str = "per_page", per_page: int = 100) -> Generator[Any, None, None]:
        """
        Simple pager for APIs using numeric page/per_page params and returning JSON arrays.
        Yields items one by one.
        """
        page = 1
        while True:
            p = dict(params or {})
            p[page_key] = page
            p[per_page_key] = per_page
            resp = self.json("GET", path, params=p)
            items = resp if isinstance(resp, list) else resp.get("items") if isinstance(resp, dict) else []
            if not items:
                break
            for it in items:
                yield it
            page += 1


# ---- Async HTTP client (aiohttp) ----
class AsyncHttpClient:
    """
    Asynchronous HTTP client using aiohttp.

    Example:
        cfg = HttpConnectorConfig.from_env()
        client = AsyncHttpClient(cfg)
        resp = await client.json("GET", "/health")
    """

    def __init__(self, config: HttpConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if aiohttp is None:
            raise RuntimeError("`aiohttp` package is required for AsyncHttpClient but not installed.")
        # aiohttp session: use default connector that honors env proxies by default
        trace_configs = []  # placeholder for tracing if desired
        headers = config.headers or {}
        if config.auth_token:
            headers.setdefault(config.auth_header, f"Bearer {config.auth_token}")
        self._session = aiohttp.ClientSession(headers=headers)

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[int, Mapping[str, str], Any]:
        url = _join_url(self.cfg.base_url, path)
        attempt = 0
        last_exc: Optional[Exception] = None
        timeout_val = aiohttp.ClientTimeout(total=(timeout or self.cfg.timeout))

        while attempt <= self.cfg.max_retries:
            attempt += 1
            start = time.time()
            try:
                async with self._session.request(method, url, params=params, json=json_body, data=data, headers=headers, timeout=timeout_val, ssl=self.cfg.verify_ssl) as resp:
                    status = resp.status
                    text = await resp.text()
                    latency = time.time() - start
                    self.metrics("request_completed", {"method": method, "path": path, "status": status, "latency": latency})
                    if status in (401, 403):
                        raise HttpAuthError(f"authentication failed: {status}")
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        self.metrics("rate_limited", {"status": 429, "attempt": attempt})
                        if retry_after:
                            try:
                                await asyncio.sleep(float(retry_after))
                            except Exception:
                                pass
                        if attempt > self.cfg.max_retries:
                            raise HttpRateLimitError("rate limited")
                        await asyncio.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter))
                        continue
                    if 500 <= status < 600:
                        if attempt > self.cfg.max_retries:
                            raise HttpRequestError(f"server error: {status}")
                        await asyncio.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter))
                        continue
                    # Try to parse JSON, fallback to text
                    try:
                        body = json.loads(text) if text else None
                    except Exception:
                        body = text
                    return status, dict(resp.headers), body
            except Exception as exc:
                last_exc = exc
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor, self.cfg.jitter)
                self.metrics("request_exception", {"attempt": attempt, "error": str(exc)})
                logger.warning("AsyncHttpClient request exception (attempt %d/%d): %s — retrying after %.2fs", attempt, self.cfg.max_retries + 1, exc, wait)
                if attempt > self.cfg.max_retries:
                    break
                await asyncio.sleep(wait)
                continue

        raise HttpRequestError(f"async request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    # Conveniences
    async def get(self, path: str, **kwargs) -> Tuple[int, Mapping[str, str], Any]:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> Tuple[int, Mapping[str, str], Any]:
        return await self._request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs) -> Tuple[int, Mapping[str, str], Any]:
        return await self._request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> Tuple[int, Mapping[str, str], Any]:
        return await self._request("DELETE", path, **kwargs)

    async def json(self, method: str, path: str, **kwargs) -> Any:
        status, headers, body = await self._request(method, path, **kwargs)
        return body

    async def stream_download(self, path: str, dest_path: str, chunk_size: int = 8192) -> None:
        """
        Stream download into file asynchronously. Writes to a temp file and atomically replaces.
        """
        url = _join_url(self.cfg.base_url, path)
        tmp = dest_path + ".tmp"
        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout)
        async with aiohttp.ClientSession(headers=self.cfg.headers) as session:
            async with session.get(url, timeout=timeout, ssl=self.cfg.verify_ssl) as resp:
                if resp.status >= 400:
                    raise HttpRequestError(f"download failed: {resp.status}")
                with open(tmp, "wb") as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        if chunk:
                            f.write(chunk)
        os.replace(tmp, dest_path)

    async def upload_file(self, path: str, file_field: str, file_path: str, extra_fields: Optional[Dict[str, Any]] = None) -> Any:
        """
        Upload file via multipart/form-data asynchronously.
        """
        url = _join_url(self.cfg.base_url, path)
        data = aiohttp.FormData()
        for k, v in (extra_fields or {}).items():
            data.add_field(k, str(v))
        data.add_field(file_field, open(file_path, "rb"))
        status, headers, body = await self._request("POST", path, data=data)
        return body

    async def paginate(self, path: str, params: Optional[Dict[str, Any]] = None, page_key: str = "page", per_page_key: str = "per_page", per_page: int = 100) -> AsyncIterator[Any]:
        """
        Async pager for page/per_page style APIs. Yields items.
        """
        page = 1
        while True:
            p = dict(params or {})
            p[page_key] = page
            p[per_page_key] = per_page
            _, _, body = await self._request("GET", path, params=p)
            items = body if isinstance(body, list) else (body.get("items") if isinstance(body, dict) else [])
            if not items:
                break
            for it in items:
                yield it
            page += 1

    async def close(self) -> None:
        await self._session.close()


# ---- Factories ----
def default_http_client_from_env(prefix: str = "HTTP") -> HttpClient:
    cfg = HttpConnectorConfig.from_env(prefix=prefix)
    return HttpClient(cfg)


def default_async_http_client_from_env(prefix: str = "HTTP") -> AsyncHttpClient:
    cfg = HttpConnectorConfig.from_env(prefix=prefix)
    return AsyncHttpClient(cfg)


# ---- Example usage (not executed on import) ----
if __name__ == "__main__":  # pragma: no cover - example only
    logging.basicConfig(level=logging.DEBUG)
    cfg = HttpConnectorConfig.from_env()
    try:
        client = HttpClient(cfg)
        print("GET / =>", client.json("GET", "/"))
    except Exception as exc:
        logger.exception("sync example failed: %s", exc)

    if aiohttp is not None:
        async def async_demo():
            ac = AsyncHttpClient(cfg)
            body = await ac.json("GET", "/")
            print("async GET =>", body)
            await ac.close()
        try:
            asyncio.run(async_demo())
        except Exception:
            logger.exception("async example failed")
    else:
        logger.info("aiohttp not installed; skipping async demo")
