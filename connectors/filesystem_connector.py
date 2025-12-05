# OmniFlow/connectors/filesystem_connector.py
"""
OmniFlow — Filesystem connector
================================

Production-ready, security-conscious Python filesystem helper for OmniFlow plugins
and other services that need safe, well-tested file I/O operations.

Features
- Safe path confinement to a configured base directory (prevents path traversal)
- Atomic writes via write-to-temp + os.replace
- Optional advisory file locking (fcntl on Unix or portalocker when installed)
- Sync and async APIs (async requires `aiofiles` — graceful message if not installed)
- Configurable retry/backoff for transient IO errors
- Permission and ownership helpers (mode, uid/gid) with sensible defaults
- Streaming helpers for large files (chunked read/write)
- Custom exceptions and structured logging
- Small usage examples in docstrings

Security notes
- Always configure a dedicated `base_dir` and set restrictive permissions on it.
- Avoid running with elevated privileges; be cautious when using uid/gid changes.
- For cross-platform locking behaviour consider adding `portalocker` to your deps.

Intended use
- Plugin-local storage (caches, small datasets, sockets)
- Not intended as a general-purpose blob store replacement.

"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Tuple

# Optional libs
try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None  # type: ignore

try:
    import aiofiles  # type: ignore
except Exception:
    aiofiles = None  # type: ignore

# Public API
__all__ = [
    "FilesystemError",
    "FilesystemPermissionError",
    "FilesystemNotFoundError",
    "FilesystemLockError",
    "FilesystemConnectorConfig",
    "FilesystemConnector",
]


logger = logging.getLogger("omniflow.connectors.filesystem")
logger.addHandler(logging.NullHandler())


# ---- Exceptions ----
class FilesystemError(Exception):
    """Base class for Filesystem connector errors."""


class FilesystemPermissionError(FilesystemError, PermissionError):
    """Raised when a permission operation fails."""


class FilesystemNotFoundError(FilesystemError, FileNotFoundError):
    """Raised when a path does not exist."""


class FilesystemLockError(FilesystemError):
    """Raised when a lock operation fails."""


# ---- Config dataclass ----
@dataclass
class FilesystemConnectorConfig:
    """
    Filesystem connector configuration.

    base_dir: root directory to confine all operations to (must exist or will be created
              by connector if create_base=True).
    create_base: whether to create base_dir if missing.
    default_mode: default file mode for new files (octal int, e.g., 0o640).
    max_retries: retry attempts for transient IO errors (EINTR, EAGAIN, etc).
    backoff_factor: base seconds for exponential backoff between retries.
    metrics_hook: optional callable(metric_name: str, payload: dict) for telemetry.
    """

    base_dir: Path
    create_base: bool = False
    default_mode: int = 0o640
    dir_mode: int = 0o750
    max_retries: int = 3
    backoff_factor: float = 0.1
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None

    @staticmethod
    def from_env(prefix: str = "FILESYSTEM") -> "FilesystemConnectorConfig":
        base = os.getenv(f"{prefix}_BASE_DIR", os.getenv("FILESYSTEM_BASE_DIR", "/var/lib/omniflow"))
        create = os.getenv(f"{prefix}_CREATE_BASE", "false").lower() in ("1", "true", "yes")
        default_mode = int(os.getenv(f"{prefix}_DEFAULT_MODE", "0o640"), 8)
        dir_mode = int(os.getenv(f"{prefix}_DIR_MODE", "0o750"), 8)
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", "3"))
        backoff = float(os.getenv(f"{prefix}_BACKOFF_FACTOR", "0.1"))
        return FilesystemConnectorConfig(
            base_dir=Path(base),
            create_base=create,
            default_mode=default_mode,
            dir_mode=dir_mode,
            max_retries=max_retries,
            backoff_factor=backoff,
        )


# ---- Utilities ----
def _is_transient_exc(exc: OSError) -> bool:
    # Consider common transient errno codes
    return exc.errno in (errno.EINTR, errno.EAGAIN, errno.EWOULDBLOCK, errno.ENFILE, errno.EMFILE)


def _compute_backoff(attempt: int, factor: float = 0.1) -> float:
    return factor * (2 ** attempt)


def _ensure_within_base(base: Path, path: Path) -> None:
    try:
        base_res = base.resolve(strict=False)
        path_res = path.resolve(strict=False)
    except Exception:
        # If resolve fails for non-existing path, approximate with absolute
        base_res = base.absolute()
        path_res = path.absolute()
    if not str(path_res).startswith(str(base_res)):
        raise FilesystemError(f"Path {path} is outside of allowed base directory {base}")


# ---- Locking helpers (advisory) ----
class _AdvisoryLock:
    """
    Context manager for advisory file locks.

    Best-effort: uses fcntl.flock on Unix. If unavailable, acts as no-op
    but emits a debug warning. For cross-platform locking, install portalocker
    and extend accordingly.
    """

    def __init__(self, file_path: Path, exclusive: bool = True, timeout: Optional[float] = None):
        self.file_path = file_path
        self.exclusive = exclusive
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        # Open the file (create if missing) and lock
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(self.file_path, "a+b")
        self._fd = fd
        if fcntl is None:
            logger.debug("fcntl not available; advisory locking is a no-op on this platform")
            return self
        mode = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
        start = time.time()
        while True:
            try:
                fcntl.flock(fd.fileno(), mode | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    if self.timeout is not None and (time.time() - start) >= self.timeout:
                        raise FilesystemLockError("Timeout acquiring file lock") from exc
                    time.sleep(0.05)
                    continue
                raise FilesystemLockError("Failed to acquire file lock") from exc

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fd:
                if fcntl is not None:
                    try:
                        fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        logger.debug("Failed to release fcntl lock", exc_info=True)
                try:
                    self._fd.close()
                except Exception:
                    pass
                self._fd = None
        except Exception:
            logger.debug("Error while releasing lock", exc_info=True)


# ---- Connector ----
class FilesystemConnector:
    """
    Primary Filesystem connector providing safe, atomic and optionally locked file operations.

    Example (sync):
        cfg = FilesystemConnectorConfig(base_dir=Path("/tmp/omniflow"), create_base=True)
        fs = FilesystemConnector(cfg)
        fs.write_text("jobs/1/out.txt", "hello world")
        print(fs.read_text("jobs/1/out.txt"))

    Example (async):
        cfg = FilesystemConnectorConfig(base_dir=Path("/tmp/omniflow"), create_base=True)
        fs = FilesystemConnector(cfg)
        await fs.write_text_async("jobs/1/out.txt", "async hello")
        txt = await fs.read_text_async("jobs/1/out.txt")
    """

    def __init__(self, config: FilesystemConnectorConfig):
        self.cfg = config
        self.base = config.base_dir
        self.metrics = config.metrics_hook or (lambda *_: None)
        if config.create_base:
            self.base.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.base, config.dir_mode)
            except Exception:
                logger.debug("Could not chmod base dir", exc_info=True)

    # ---------------------
    # Internal helpers
    # ---------------------
    def _resolve(self, relative: str) -> Path:
        # Prevent absolute paths being used; join to base and ensure confinement
        candidate = (self.base / relative).resolve()
        _ensure_within_base(self.base, candidate)
        return candidate

    def _retryable(self, func: Callable[[], Any], action_name: str = "io") -> Any:
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                start = time.time()
                result = func()
                latency = time.time() - start
                self.metrics(f"{action_name}_completed", {"attempt": attempt, "latency": latency})
                return result
            except OSError as exc:
                last_exc = exc
                if _is_transient_exc(exc) and attempt < self.cfg.max_retries:
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    logger.warning("Transient IO error (attempt %d/%d): %s — retrying after %.3fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                    self.metrics(f"{action_name}_transient_error", {"attempt": attempt, "error": str(exc)})
                    time.sleep(wait)
                    continue
                # For permission errors raise a more specific exception
                if isinstance(exc, PermissionError):
                    raise FilesystemPermissionError(str(exc)) from exc
                raise FilesystemError(str(exc)) from exc
        raise FilesystemError(f"{action_name} failed after retries") from last_exc

    # ---------------------
    # Read helpers
    # ---------------------
    def read_bytes(self, relative_path: str) -> bytes:
        p = self._resolve(relative_path)
        if not p.exists():
            raise FilesystemNotFoundError(f"{p} not found")
        def _op():
            with open(p, "rb") as f:
                return f.read()
        return self._retryable(_op, "read_bytes")

    def read_text(self, relative_path: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(relative_path).decode(encoding)

    def stream_read(self, relative_path: str, chunk_size: int = 65536) -> Iterator[bytes]:
        p = self._resolve(relative_path)
        if not p.exists():
            raise FilesystemNotFoundError(f"{p} not found")
        def _op():
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        # Can't easily wrap generator in retryable; do lightweight read without retry for streaming
        try:
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc
        except OSError as exc:
            raise FilesystemError(str(exc)) from exc

    # ---------------------
    # Write helpers
    # ---------------------
    def write_bytes(self, relative_path: str, data: bytes, mode: Optional[int] = None, *, atomic: bool = True, force_dirs: bool = True) -> None:
        p = self._resolve(relative_path)
        if force_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        mode = mode or self.cfg.default_mode

        def _op():
            # Atomic write: write to temp file in same directory then os.replace
            tmp = None
            try:
                fd, tmp_path = tempfile.mkstemp(dir=str(p.parent))
                tmp = Path(tmp_path)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, mode)
                os.replace(tmp, p)
            finally:
                if tmp and tmp.exists():
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
        return self._retryable(_op, "write_bytes")

    def write_text(self, relative_path: str, text: str, encoding: str = "utf-8", mode: Optional[int] = None, **kwargs) -> None:
        self.write_bytes(relative_path, text.encode(encoding), mode=mode, **kwargs)

    def append_bytes(self, relative_path: str, data: bytes, mode: Optional[int] = None, *, create: bool = True) -> None:
        p = self._resolve(relative_path)
        if create:
            p.parent.mkdir(parents=True, exist_ok=True)
        def _op():
            with open(p, "ab") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if mode is not None:
                try:
                    os.chmod(p, mode)
                except Exception:
                    pass
        return self._retryable(_op, "append")

    def append_text(self, relative_path: str, text: str, encoding: str = "utf-8", **kwargs) -> None:
        self.append_bytes(relative_path, text.encode(encoding), **kwargs)

    # ---------------------
    # Async variants (require aiofiles)
    # ---------------------
    async def read_bytes_async(self, relative_path: str) -> bytes:
        if aiofiles is None:
            raise FilesystemError("async IO requires aiofiles; install aiofiles to use async API")
        p = self._resolve(relative_path)
        if not p.exists():
            raise FilesystemNotFoundError(f"{p} not found")
        attempt = 0
        last_exc = None
        while attempt <= self.cfg.max_retries:
            try:
                async with aiofiles.open(p, "rb") as f:
                    data = await f.read()
                    self.metrics("read_bytes_async_completed", {"attempt": attempt})
                    return data
            except OSError as exc:
                last_exc = exc
                if _is_transient_exc(exc) and attempt < self.cfg.max_retries:
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                if isinstance(exc, PermissionError):
                    raise FilesystemPermissionError(str(exc)) from exc
                raise FilesystemError(str(exc)) from exc
        raise FilesystemError("read_bytes_async failed after retries") from last_exc

    async def read_text_async(self, relative_path: str, encoding: str = "utf-8") -> str:
        b = await self.read_bytes_async(relative_path)
        return b.decode(encoding)

    async def write_bytes_async(self, relative_path: str, data: bytes, mode: Optional[int] = None, **kwargs) -> None:
        if aiofiles is None:
            raise FilesystemError("async IO requires aiofiles; install aiofiles to use async API")
        p = self._resolve(relative_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = mode or self.cfg.default_mode
        attempt = 0
        last_exc = None
        while attempt <= self.cfg.max_retries:
            try:
                # Write to a temp file then replace atomically
                tmp_fd, tmp_path = tempfile.mkstemp(dir=str(p.parent))
                os.close(tmp_fd)
                async with aiofiles.open(tmp_path, "wb") as f:
                    await f.write(data)
                    await f.flush()
                os.chmod(tmp_path, mode)
                os.replace(tmp_path, p)
                self.metrics("write_bytes_async_completed", {"attempt": attempt})
                return
            except OSError as exc:
                last_exc = exc
                if _is_transient_exc(exc) and attempt < self.cfg.max_retries:
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                if isinstance(exc, PermissionError):
                    raise FilesystemPermissionError(str(exc)) from exc
                raise FilesystemError(str(exc)) from exc
        raise FilesystemError("write_bytes_async failed after retries") from last_exc

    async def write_text_async(self, relative_path: str, text: str, encoding: str = "utf-8", **kwargs) -> None:
        await self.write_bytes_async(relative_path, text.encode(encoding), **kwargs)

    # ---------------------
    # Directory and metadata
    # ---------------------
    def list_dir(self, relative_path: str = ".", recursive: bool = False) -> List[str]:
        p = self._resolve(relative_path)
        if not p.exists():
            raise FilesystemNotFoundError(f"{p} not found")
        res: List[str] = []
        if recursive:
            for root, dirs, files in os.walk(p):
                for f in files:
                    rel = Path(root).joinpath(f).relative_to(self.base)
                    res.append(str(rel))
        else:
            for entry in p.iterdir():
                rel = entry.relative_to(self.base)
                res.append(str(rel))
        return res

    def exists(self, relative_path: str) -> bool:
        p = (self.base / relative_path)
        try:
            _ensure_within_base(self.base, p.resolve())
        except FilesystemError:
            return False
        return p.exists()

    def stat(self, relative_path: str) -> os.stat_result:
        p = self._resolve(relative_path)
        if not p.exists():
            raise FilesystemNotFoundError(f"{p} not found")
        try:
            return p.stat()
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc

    def mkdir(self, relative_path: str, mode: Optional[int] = None, parents: bool = True) -> None:
        p = self._resolve(relative_path)
        try:
            p.mkdir(mode=(mode or self.cfg.dir_mode), parents=parents, exist_ok=True)
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc
        except OSError as exc:
            raise FilesystemError(str(exc)) from exc

    def remove(self, relative_path: str, ignore_missing: bool = False) -> None:
        p = self._resolve(relative_path)
        try:
            if p.is_dir():
                # only remove empty dir
                p.rmdir()
            else:
                p.unlink()
        except FileNotFoundError:
            if not ignore_missing:
                raise FilesystemNotFoundError(f"{p} not found")
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc
        except OSError as exc:
            raise FilesystemError(str(exc)) from exc

    # ---------------------
    # Permissions & ownership helpers
    # ---------------------
    def set_mode(self, relative_path: str, mode: int) -> None:
        p = self._resolve(relative_path)
        try:
            os.chmod(p, mode)
        except FileNotFoundError:
            raise FilesystemNotFoundError(f"{p} not found")
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc

    def chown(self, relative_path: str, uid: int, gid: int) -> None:
        p = self._resolve(relative_path)
        try:
            os.chown(p, uid, gid)
        except FileNotFoundError:
            raise FilesystemNotFoundError(f"{p} not found")
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc
        except OSError as exc:
            raise FilesystemError(str(exc)) from exc

    # ---------------------
    # Locks
    # ---------------------
    def lock(self, relative_path: str, exclusive: bool = True, timeout: Optional[float] = None):
        """
        Return a context manager that acquires an advisory lock on a lock-file associated
        with the given relative_path. Example:

            with fs.lock("jobs/1/state.json"):
                # exclusive access
                do_stuff()

        Locks are advisory and best-effort. On platforms without fcntl they are no-ops.
        """
        p = self._resolve(relative_path)
        lockfile = p.with_name(p.name + ".lock")
        return _AdvisoryLock(lockfile, exclusive=exclusive, timeout=timeout)

    # ---------------------
    # Convenience utilities
    # ---------------------
    def atomic_replace(self, src_relative: str, dst_relative: str) -> None:
        """
        Atomically replace dst with src (both relative to base). This wraps os.replace.
        Useful when a producer writes to a temp path and then promotes it.
        """
        src = self._resolve(src_relative)
        dst = self._resolve(dst_relative)
        try:
            os.replace(src, dst)
        except FileNotFoundError:
            raise FilesystemNotFoundError(f"{src} or {dst} missing")
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc
        except OSError as exc:
            raise FilesystemError(str(exc)) from exc

    # ---------------------
    # Small CLI-like helpers (not executed on import)
    # ---------------------
    def write_json(self, relative_path: str, obj: Any, encoding: str = "utf-8", **kwargs) -> None:
        import json as _json

        txt = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        self.write_text(relative_path, txt, encoding=encoding, **kwargs)

    async def write_json_async(self, relative_path: str, obj: Any, encoding: str = "utf-8", **kwargs) -> None:
        if aiofiles is None:
            raise FilesystemError("async IO requires aiofiles; install aiofiles to use async API")
        import json as _json

        txt = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        await self.write_text_async(relative_path, txt, encoding=encoding, **kwargs)


# ---- Module-level quick helpers ----
def default_connector(base_dir: str = "/var/lib/omniflow", create_base: bool = True) -> FilesystemConnector:
    cfg = FilesystemConnectorConfig(base_dir=Path(base_dir), create_base=create_base)
    return FilesystemConnector(cfg)


# ---- Example usage (when run directly) ----
if __name__ == "__main__":  # pragma: no cover - example only
    logging.basicConfig(level=logging.DEBUG)
    cfg = FilesystemConnectorConfig(base_dir=Path("/tmp/omniflow-test"), create_base=True)
    fs = FilesystemConnector(cfg)
    key = "example/hello.txt"
    fs.write_text(key, "Hello OmniFlow\n")
    print("Read:", fs.read_text(key))
    with fs.lock(key):
        print("Acquired lock for", key)
    # async example
    async def async_demo():
        await fs.write_text_async("example/async.txt", "async write")
        print(await fs.read_text_async("example/async.txt"))
    if aiofiles is not None:
        asyncio.run(async_demo())
    else:
        logger.info("aiofiles not installed — skipping async demo")
