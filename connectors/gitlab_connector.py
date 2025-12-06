# OmniFlow/connectors/gitlab_connector.py
"""
OmniFlow — GitLab connector
============================

Production-ready, dependency-light Python connector for interacting with GitLab's REST API.
Designed for use inside OmniFlow plugins, automation scripts, and CI/CD helpers.

Features
- Sync and async clients (requests and aiohttp optional)
- Environment-driven configuration (GITLAB_API_TOKEN, GITLAB_BASE_URL)
- Support for Personal Access Token (Private-Token or Authorization: Bearer),
  and optional OAuth token usage.
- Pagination helpers (keyset & page-based), list iterators
- Retry with exponential backoff and jitter for transient errors (5xx, network)
- Rate-limit awareness (reads headers like RateLimit-Remaining / Retry-After)
- Convenience methods for common operations: get project, list pipelines, trigger pipeline, list merge requests, upload artifact, create release, create issue, download raw file
- Structured logging and optional metrics hook
- Clear exceptions hierarchy for consumer code to handle
- Type hints and small usage examples in docstrings

Notes
- This module avoids adding hard dependencies; it uses requests/aiohttp if available.
- For strongly-typed API bindings consider using python-gitlab, but this connector is intentionally lightweight.
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
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

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
    "GitLabError",
    "GitLabAuthError",
    "GitLabAPIError",
    "GitLabRateLimitError",
    "GitLabConnectorConfig",
    "GitLabClient",
    "AsyncGitLabClient",
]

logger = logging.getLogger("omniflow.connectors.gitlab")
logger.addHandler(logging.NullHandler())


# ---- Exceptions ----
class GitLabError(Exception):
    """Base class for GitLab connector errors."""


class GitLabAuthError(GitLabError):
    """Authentication / authorization error (401 / 403)."""


class GitLabAPIError(GitLabError):
    """Generic API error (non-2xx)."""

    def __init__(self, status_code: int, body: Any = None, message: Optional[str] = None):
        super().__init__(message or f"GitLab API error: {status_code}")
        self.status_code = status_code
        self.body = body


class GitLabRateLimitError(GitLabAPIError):
    """Raised when a rate limit is enforced (429 or Retry-After)."""


# ---- Config dataclass ----
@dataclass
class GitLabConnectorConfig:
    """
    Configuration for the GitLab connector.

    Environment variables (defaults):
      - GITLAB_API_TOKEN (required unless anonymous)
      - GITLAB_BASE_URL (default: https://gitlab.com/api/v4)
      - GITLAB_TIMEOUT (default: 30.0)
      - GITLAB_MAX_RETRIES (default: 3)
      - GITLAB_BACKOFF_FACTOR (default: 0.6)
      - GITLAB_TOKEN_AUTH_HEADER (optional, default picks Authorization: Bearer or Private-Token)
    """

    api_token: Optional[str] = None
    base_url: str = "https://gitlab.com/api/v4"
    timeout: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 0.6
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None
    # If set to "Private-Token", will send header "Private-Token: <token>", else uses "Authorization: Bearer <token>"
    token_header_style: Optional[str] = None

    @staticmethod
    def from_env(prefix: str = "GITLAB") -> "GitLabConnectorConfig":
        token = os.getenv(f"{prefix}_API_TOKEN") or os.getenv("GITLAB_API_TOKEN")
        base = os.getenv(f"{prefix}_BASE_URL") or os.getenv("GITLAB_BASE_URL") or "https://gitlab.com/api/v4"
        timeout = float(os.getenv(f"{prefix}_TIMEOUT", os.getenv("GITLAB_TIMEOUT", "30.0")))
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", os.getenv("GITLAB_MAX_RETRIES", "3")))
        backoff = float(os.getenv(f"{prefix}_BACKOFF_FACTOR", os.getenv("GITLAB_BACKOFF_FACTOR", "0.6")))
        token_header_style = os.getenv(f"{prefix}_TOKEN_AUTH_HEADER") or os.getenv("GITLAB_TOKEN_AUTH_HEADER")
        return GitLabConnectorConfig(
            api_token=token,
            base_url=base.rstrip("/"),
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff,
            token_header_style=token_header_style,
        )


# ---- Helpers: backoff, headers, metrics ----
def _compute_backoff(attempt: int, factor: float = 0.6, jitter: float = 0.2) -> float:
    """Exponential backoff in seconds with jitter."""
    base = factor * (2 ** attempt)
    jitter_amount = base * jitter * (random.random() * 2 - 1)
    return max(0.0, base + jitter_amount)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


def _build_auth_headers(cfg: GitLabConnectorConfig) -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    if cfg.api_token:
        if cfg.token_header_style and cfg.token_header_style.lower() == "private-token":
            headers["Private-Token"] = cfg.api_token
        else:
            # prefer Authorization: Bearer if token looks like JWT; allow override
            headers["Authorization"] = f"Bearer {cfg.api_token}"
    return headers


def _extract_rate_limit_info(resp_headers: Dict[str, Any]) -> Dict[str, Any]:
    # GitLab may include RateLimit headers or Retry-After for 429
    info: Dict[str, Any] = {}
    for k in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset", "Retry-After"):
        if k in resp_headers:
            info[k] = resp_headers.get(k)
    return info


# ---- Sync client ----
class GitLabClient:
    """
    Synchronous GitLab connector.

    Example:
        cfg = GitLabConnectorConfig.from_env()
        client = GitLabClient(cfg)
        project = client.get_project("namespace/project")
    """

    def __init__(self, config: GitLabConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if requests is None:
            logger.warning("`requests` not installed — GitLabClient will not function without it.")

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
    ) -> requests.Response:
        if requests is None:
            raise GitLabError("missing dependency `requests` for GitLabClient")

        url = f"{self.cfg.base_url}{path}"
        hdrs = dict(_build_auth_headers(self.cfg))
        if headers:
            hdrs.update(headers)

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.cfg.max_retries:
            attempt += 1
            start = time.time()
            try:
                resp = requests.request(method, url, params=params, json=json_body, headers=hdrs, timeout=self.cfg.timeout, stream=stream)
            except requests.RequestException as exc:
                last_exc = exc
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("GitLab request exception (attempt %d/%d): %s — retrying after %.2fs", attempt, self.cfg.max_retries + 1, exc, wait)
                self.metrics("request_error", {"attempt": attempt, "error": str(exc)})
                if attempt > self.cfg.max_retries:
                    break
                time.sleep(wait)
                continue

            latency = time.time() - start
            self.metrics("request_completed", {"method": method, "path": path, "status": resp.status_code, "latency": latency})
            rate_info = _extract_rate_limit_info(resp.headers)
            if rate_info:
                self.metrics("rate_info", rate_info)
                logger.debug("Rate info: %s", rate_info)

            if 200 <= resp.status_code < 300:
                return resp
            if resp.status_code in (401, 403):
                raise GitLabAuthError(f"authentication failed: {resp.status_code}")
            if resp.status_code == 429:
                # Respect Retry-After if present
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        ra = float(retry_after)
                        logger.warning("Rate limited by GitLab; sleeping %s seconds", ra)
                        time.sleep(ra)
                    except Exception:
                        pass
                if attempt > self.cfg.max_retries:
                    raise GitLabRateLimitError(resp.status_code, resp.text, "rate limited")
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                if attempt > self.cfg.max_retries:
                    raise GitLabAPIError(resp.status_code, resp.text)
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("Server error %d — retrying after %.2fs", resp.status_code, wait)
                time.sleep(wait)
                continue
            # Other 4xx considered permanent
            raise GitLabAPIError(resp.status_code, resp.text)

        raise GitLabError(f"request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    # ---- High-level helpers ----

    def _json(self, resp: requests.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return resp.text

    def get_project(self, project: str) -> Dict[str, Any]:
        """
        Get project details by path or id. `project` may be URL-encoded path like 'group%2Fname' or numeric id.
        """
        path = f"/projects/{requests.utils.requote_uri(project)}"
        resp = self._request("GET", path)
        return self._json(resp)

    def list_merge_requests(self, project: str, state: str = "opened", per_page: int = 100) -> Iterator[Dict[str, Any]]:
        """
        Iterate through merge requests for a project (paginated).
        """
        page = 1
        while True:
            params = {"state": state, "per_page": per_page, "page": page}
            resp = self._request("GET", f"/projects/{requests.utils.requote_uri(project)}/merge_requests", params=params)
            data = self._json(resp)
            if not data:
                break
            for item in data:
                yield item
            # GitLab pagination via X-Next-Page header
            next_page = resp.headers.get("X-Next-Page")
            if not next_page:
                break
            page = int(next_page)

    def list_pipelines(self, project: str, ref: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        page = 1
        while True:
            params = {"per_page": 100, "page": page}
            if ref:
                params["ref"] = ref
            resp = self._request("GET", f"/projects/{requests.utils.requote_uri(project)}/pipelines", params=params)
            data = self._json(resp)
            if not data:
                break
            for p in data:
                yield p
            next_page = resp.headers.get("X-Next-Page")
            if not next_page:
                break
            page = int(next_page)

    def trigger_pipeline(self, project: str, ref: str, variables: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Trigger a pipeline by ref (requires project to allow trigger by token or API).
        """
        path = f"/projects/{requests.utils.requote_uri(project)}/pipeline"
        body = {"ref": ref}
        if variables:
            body["variables"] = [{"key": k, "value": v} for k, v in variables.items()]
        resp = self._request("POST", path, json_body=body)
        return self._json(resp)

    def get_pipeline(self, project: str, pipeline_id: int) -> Dict[str, Any]:
        resp = self._request("GET", f"/projects/{requests.utils.requote_uri(project)}/pipelines/{pipeline_id}")
        return self._json(resp)

    def list_repository_files(self, project: str, ref: str = "main", path: str = "", per_page: int = 100) -> Iterator[Dict[str, Any]]:
        page = 1
        while True:
            params = {"per_page": per_page, "page": page, "ref": ref, "path": path}
            resp = self._request("GET", f"/projects/{requests.utils.requote_uri(project)}/repository/tree", params=params)
            data = self._json(resp)
            if not data:
                break
            for item in data:
                yield item
            next_page = resp.headers.get("X-Next-Page")
            if not next_page:
                break
            page = int(next_page)

    def get_raw_file(self, project: str, file_path: str, ref: str = "main") -> bytes:
        """
        Get raw file contents. Note: path must be URL-encoded.
        """
        encoded = requests.utils.requote_uri(file_path)
        resp = self._request("GET", f"/projects/{requests.utils.requote_uri(project)}/repository/files/{encoded}/raw", params={"ref": ref}, stream=True)
        # stream content to reduce memory usage if large
        chunks = []
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    def create_issue(self, project: str, title: str, description: str = "", labels: Optional[List[str]] = None) -> Dict[str, Any]:
        body = {"title": title, "description": description}
        if labels:
            body["labels"] = ",".join(labels)
        resp = self._request("POST", f"/projects/{requests.utils.requote_uri(project)}/issues", json_body=body)
        return self._json(resp)

    def create_release(self, project: str, tag_name: str, name: str, description: str = "") -> Dict[str, Any]:
        body = {"name": name, "tag_name": tag_name, "description": description}
        resp = self._request("POST", f"/projects/{requests.utils.requote_uri(project)}/releases", json_body=body)
        return self._json(resp)

    def upload_project_file(self, project: str, file_path: str, content: bytes) -> Dict[str, Any]:
        """
        Upload file to repository via the upload API (uploads are project-level and return URL).
        """
        if requests is None:
            raise GitLabError("missing dependency `requests` for upload_project_file")
        url = f"{self.cfg.base_url}/projects/{requests.utils.requote_uri(project)}/uploads"
        hdrs = dict(_build_auth_headers(self.cfg))
        files = {"file": (file_path, content)}
        resp = self._request("POST", url.replace(self.cfg.base_url, ""), headers=hdrs, json_body=None)  # fallback to generic _request
        # Note: simpler to call requests.post directly here but keep consistent retries; instead we do a direct call with retry above
        # If _request can't accept files we perform a direct requests.post with same headers and retry logic:
        attempt = 0
        last_exc = None
        while attempt <= self.cfg.max_retries:
            attempt += 1
            try:
                r = requests.post(url, headers=hdrs, files=files, timeout=self.cfg.timeout)
            except Exception as exc:
                last_exc = exc
                if attempt > self.cfg.max_retries:
                    raise GitLabError("upload failed") from exc
                time.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor))
                continue
            if 200 <= r.status_code < 300:
                try:
                    return r.json()
                except Exception:
                    return {"raw": r.text}
            if r.status_code in (401, 403):
                raise GitLabAuthError("authentication failed")
            if r.status_code == 429:
                if attempt > self.cfg.max_retries:
                    raise GitLabRateLimitError(r.status_code, r.text)
                time.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor))
                continue
            if 500 <= r.status_code < 600:
                if attempt > self.cfg.max_retries:
                    raise GitLabAPIError(r.status_code, r.text)
                time.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor))
                continue
            raise GitLabAPIError(r.status_code, r.text)
        raise GitLabError("upload failed after retries") from last_exc


# ---- Async client ----
class AsyncGitLabClient:
    """
    Async GitLab connector using aiohttp.

    Example:
        cfg = GitLabConnectorConfig.from_env()
        client = AsyncGitLabClient(cfg)
        project = await client.get_project("group/project")
    """

    def __init__(self, config: GitLabConnectorConfig):
        self.cfg = config
        self.metrics = config.metrics_hook or _default_metrics_hook
        if aiohttp is None:
            logger.warning("`aiohttp` not installed — AsyncGitLabClient will not function without it.")

    async def _request_async(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
    ) -> Tuple[int, Dict[str, Any], bytes]:
        if aiohttp is None:
            raise GitLabError("missing dependency `aiohttp` for AsyncGitLabClient")

        url = f"{self.cfg.base_url}{path}"
        hdrs = dict(_build_auth_headers(self.cfg))
        if headers:
            hdrs.update(headers)

        attempt = 0
        last_exc: Optional[Exception] = None
        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout)
        while attempt <= self.cfg.max_retries:
            attempt += 1
            start = time.time()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(method, url, params=params, json=json_body, headers=hdrs) as resp:
                        status = resp.status
                        text = await resp.text()
                        latency = time.time() - start
                        self.metrics("request_completed", {"method": method, "path": path, "status": status, "latency": latency})
                        rate_info = _extract_rate_limit_info(resp.headers)
                        if rate_info:
                            self.metrics("rate_info", rate_info)
                        if 200 <= status < 300:
                            # Try to parse JSON else return raw bytes
                            try:
                                body = await resp.json()
                                return status, dict(resp.headers), json.dumps(body).encode("utf-8")
                            except Exception:
                                return status, dict(resp.headers), text.encode("utf-8")
                        if status in (401, 403):
                            raise GitLabAuthError(f"authentication failed: {status}")
                        if status == 429:
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    await asyncio.sleep(float(retry_after))
                                except Exception:
                                    pass
                            if attempt > self.cfg.max_retries:
                                raise GitLabRateLimitError(status, text)
                            await asyncio.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor))
                            continue
                        if 500 <= status < 600:
                            if attempt > self.cfg.max_retries:
                                raise GitLabAPIError(status, text)
                            await asyncio.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor))
                            continue
                        raise GitLabAPIError(status, text)
            except Exception as exc:
                last_exc = exc
                if attempt > self.cfg.max_retries:
                    raise GitLabError(f"request failed after retries: {exc}") from exc
                await asyncio.sleep(_compute_backoff(attempt - 1, self.cfg.backoff_factor))
                continue
        raise GitLabError(f"request failed after {self.cfg.max_retries} retries: {last_exc!s}")

    # Convenience wrappers similar to sync client but returning parsed JSON / bytes

    async def get_project(self, project: str) -> Any:
        status, headers, body_bytes = await self._request_async("GET", f"/projects/{project}")
        try:
            return json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return body_bytes

    async def list_merge_requests(self, project: str, state: str = "opened", per_page: int = 100) -> AsyncIterator[Dict[str, Any]]:
        page = 1
        while True:
            params = {"state": state, "per_page": per_page, "page": page}
            status, headers, body_bytes = await self._request_async("GET", f"/projects/{project}/merge_requests", params=params)
            try:
                data = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                data = []
            if not data:
                break
            for item in data:
                yield item
            next_page = headers.get("X-Next-Page")
            if not next_page:
                break
            page = int(next_page)

    async def trigger_pipeline(self, project: str, ref: str, variables: Optional[Dict[str, str]] = None) -> Any:
        body = {"ref": ref}
        if variables:
            body["variables"] = [{"key": k, "value": v} for k, v in variables.items()]
        status, headers, body_bytes = await self._request_async("POST", f"/projects/{project}/pipeline", json_body=body)
        try:
            return json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return body_bytes

    async def get_raw_file(self, project: str, file_path: str, ref: str = "main") -> bytes:
        status, headers, body_bytes = await self._request_async("GET", f"/projects/{project}/repository/files/{file_path}/raw", params={"ref": ref})
        return body_bytes

    # Other async methods can mirror the sync client as needed


# ---- Module-level convenience factory ----
def default_client_from_env() -> GitLabClient:
    """Create a GitLabClient from environment variables (sync)."""
    cfg = GitLabConnectorConfig.from_env()
    if cfg.api_token is None:
        logger.warning("No GITLAB_API_TOKEN set; client will be unauthenticated for public resources.")
    return GitLabClient(cfg)


def default_async_client_from_env() -> AsyncGitLabClient:
    cfg = GitLabConnectorConfig.from_env()
    if cfg.api_token is None:
        logger.warning("No GITLAB_API_TOKEN set; client will be unauthenticated for public resources.")
    return AsyncGitLabClient(cfg)


# ---- Example usage (not executed on import) ----
if __name__ == "__main__":  # pragma: no cover - example
    logging.basicConfig(level=logging.INFO)
    cfg = GitLabConnectorConfig.from_env()
    client = GitLabClient(cfg)
    try:
        proj = client.get_project("gitlab-org/gitlab")
        print("Project:", proj.get("path_with_namespace") if isinstance(proj, dict) else str(proj)[:200])
        # list first 5 pipelines
        for i, p in enumerate(client.list_pipelines("gitlab-org/gitlab")):
            print("Pipeline:", p.get("id"), p.get("status"))
            if i >= 4:
                break
    except Exception as exc:
        logger.exception("Example failed: %s", exc)
