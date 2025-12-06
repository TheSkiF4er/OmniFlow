# OmniFlow/connectors/redis_connector.py
"""
OmniFlow — Redis connector
===========================

Production-ready, dependency-aware Python connector for Redis used across OmniFlow.
Supports both synchronous and asynchronous usage, connection pooling, safe retries,
pub/sub helpers, simple distributed locks, caching helpers, and common convenience APIs.

Design goals
- Small, testable surface with clear exceptions
- Works with `redis` (redis-py) modern versions (sync + asyncio) when available.
- Graceful behaviour & informative errors if optional dependencies are missing.
- Safe defaults for timeouts, retries, and backoff
- Helpful telemetry hooks (metrics_hook) to integrate with instrumentation

Features
- Http-like config via environment variables (REDIS_URL, REDIS_HOST, REDIS_DB, etc.)
- Sync client wrapper (uses redis.Redis)
- Async client wrapper (uses redis.asyncio.Redis if available)
- Connection pooling, timeouts, and retry policy
- Common commands: get/set, incr, list ops, sorted set helpers
- Pub/Sub helpers (sync & async) with simple dispatch helpers
- Lightweight distributed lock (context manager) with safe expiry (uses SET NX)
- Atomic Lua script execution helper
- TTL, cache-get-or-set helper
- Convenience factory `default_redis_from_env()` and `default_async_redis_from_env()`
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

# Try to import redis-py (sync) and its asyncio submodule (modern redis-py >=4.x).
# If not available, we still export the wrappers but will raise when used.
try:
    import redis  # type: ignore
    try:
        import redis.asyncio as aioredis  # type: ignore
    except Exception:
        aioredis = None  # type: ignore
except Exception:
    redis = None  # type: ignore
    aioredis = None  # type: ignore

# Fallback for typing clarity
RedisSyncClient = Any
RedisAsyncClient = Any

logger = logging.getLogger("omniflow.connectors.redis")
logger.addHandler(logging.NullHandler())

__all__ = [
    "RedisError",
    "RedisConnectionError",
    "RedisCommandError",
    "RedisConfig",
    "RedisConnector",
    "AsyncRedisConnector",
    "default_redis_from_env",
    "default_async_redis_from_env",
]


# ---- Exceptions ----
class RedisError(Exception):
    """Base exception for Redis connector errors."""


class RedisConnectionError(RedisError):
    """Raised when a connection to Redis cannot be established."""


class RedisCommandError(RedisError):
    """Raised when a Redis command fails in a non-transient way."""


# ---- Config dataclass ----
@dataclass
class RedisConfig:
    """
    Redis connector configuration.

    Environment variables commonly respected:
      - REDIS_URL (e.g., redis://:password@host:6379/0)
      - REDIS_HOST (default "localhost")
      - REDIS_PORT (default 6379)
      - REDIS_DB (default 0)
      - REDIS_PASSWORD
      - REDIS_SOCKET_TIMEOUT (seconds, default 2.0)
      - REDIS_MAX_RETRIES (default 3)
      - REDIS_BACKOFF_FACTOR (default 0.1)
      - REDIS_DECODE_RESPONSES (default True)
    """

    url: Optional[str] = None
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    socket_timeout: float = 2.0
    max_retries: int = 3
    backoff_factor: float = 0.1
    decode_responses: bool = True
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None

    @staticmethod
    def from_env(prefix: str = "REDIS") -> "RedisConfig":
        url = os.getenv(f"{prefix}_URL") or os.getenv("REDIS_URL")
        host = os.getenv(f"{prefix}_HOST") or os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv(f"{prefix}_PORT") or os.getenv("REDIS_PORT", "6379"))
        db = int(os.getenv(f"{prefix}_DB") or os.getenv("REDIS_DB", "0"))
        password = os.getenv(f"{prefix}_PASSWORD") or os.getenv("REDIS_PASSWORD")
        socket_timeout = float(os.getenv(f"{prefix}_SOCKET_TIMEOUT") or os.getenv("REDIS_SOCKET_TIMEOUT", "2.0"))
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES") or os.getenv("REDIS_MAX_RETRIES", "3"))
        backoff_factor = float(os.getenv(f"{prefix}_BACKOFF_FACTOR") or os.getenv("REDIS_BACKOFF_FACTOR", "0.1"))
        decode = str(os.getenv(f"{prefix}_DECODE_RESPONSES") or os.getenv("REDIS_DECODE_RESPONSES", "true")).lower() not in ("0", "false", "no")
        return RedisConfig(
            url=url,
            host=host,
            port=port,
            db=db,
            password=password,
            socket_timeout=socket_timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            decode_responses=decode,
        )


# ---- Helpers: backoff, metrics ----
def _compute_backoff(attempt: int, factor: float = 0.1, jitter: float = 0.1) -> float:
    """Exponential backoff with jitter (attempt is 0-based)."""
    base = factor * (2 ** attempt)
    jitter_amount = base * jitter * (random.random() * 2 - 1)
    return max(0.0, base + jitter_amount)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


# ---- Sync connector ----
class RedisConnector:
    """
    Synchronous Redis connector wrapper.

    Example:
        cfg = RedisConfig.from_env()
        client = RedisConnector(cfg)
        client.set("k", {"x": 1}, ex=60)  # JSON automatically used if object provided
        print(client.get("k"))
    """

    def __init__(self, cfg: RedisConfig):
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        if redis is None:
            raise RedisConnectionError("`redis` package is required for RedisConnector. Install redis (redis-py).")
        # Build connection parameters
        conn_kwargs = {
            "host": cfg.host,
            "port": cfg.port,
            "db": cfg.db,
            "password": cfg.password,
            "socket_timeout": cfg.socket_timeout,
            "decode_responses": cfg.decode_responses,
        }
        # If URL provided prefer it
        if cfg.url:
            self._client: RedisSyncClient = redis.from_url(cfg.url, socket_timeout=cfg.socket_timeout, decode_responses=cfg.decode_responses)
        else:
            self._client = redis.Redis(**conn_kwargs)
        # test connection lazily or eagerly
        try:
            self._client.ping()
        except Exception as exc:
            raise RedisConnectionError(f"failed to connect to redis: {exc}") from exc

    # ---------- Low-level helpers ----------
    def _retryable(self, func: Callable[[], Any], action: str = "redis") -> Any:
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                start = time.time()
                result = func()
                latency = time.time() - start
                self.metrics(f"{action}_completed", {"attempt": attempt, "latency": latency})
                return result
            except redis.exceptions.RedisError as exc:
                last_exc = exc
                if attempt < self.cfg.max_retries:
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    self.metrics(f"{action}_retry", {"attempt": attempt + 1, "error": str(exc)})
                    time.sleep(wait)
                    continue
                raise RedisCommandError(str(exc)) from exc
        raise RedisCommandError(f"{action} failed after retries: {last_exc!s}")

    # ---------- Convenience commands ----------
    def get(self, key: str) -> Any:
        """Get key. If stored value is JSON string, return parsed object."""
        def _op():
            val = self._client.get(key)
            try:
                if isinstance(val, str):
                    return json.loads(val)
            except Exception:
                pass
            return val
        return self._retryable(_op, "get")

    def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False) -> bool:
        """Set key. If value is not bytes/str, it is JSON serialized."""
        def _op():
            v = value
            if not isinstance(value, (bytes, bytearray, str, int, float, bool)):
                v = json.dumps(value, ensure_ascii=False)
            return self._client.set(key, v, ex=ex, nx=nx)
        return self._retryable(_op, "set")

    def delete(self, key: Union[str, List[str]]) -> int:
        """Delete one or many keys. Returns number of deleted keys."""
        def _op():
            return self._client.delete(key)
        return self._retryable(_op, "delete")

    def incr(self, key: str, amount: int = 1) -> int:
        def _op():
            return self._client.incr(key, amount)
        return self._retryable(_op, "incr")

    def expire(self, key: str, seconds: int) -> bool:
        def _op():
            return self._client.expire(key, seconds)
        return self._retryable(_op, "expire")

    def ttl(self, key: str) -> int:
        def _op():
            return self._client.ttl(key)
        return self._retryable(_op, "ttl")

    def exists(self, key: str) -> bool:
        def _op():
            return bool(self._client.exists(key))
        return self._retryable(_op, "exists")

    def get_or_set(self, key: str, factory: Callable[[], Any], ex: Optional[int] = None) -> Any:
        """
        Get a key, or compute and set it atomically if missing.
        Uses a simple GET then SETNX fallback; for strict atomicity use Lua script.
        """
        val = self.get(key)
        if val is not None:
            return val
        # compute
        new_val = factory()
        # attempt to set (not strictly atomic with compute step but acceptable for cache)
        ok = self.set(key, new_val, ex=ex, nx=True)
        if ok:
            return new_val
        return self.get(key)

    # ---------- List / Queue helpers ----------
    def lpush(self, key: str, *values: Any) -> int:
        def _op():
            return self._client.lpush(key, *values)
        return self._retryable(_op, "lpush")

    def rpop(self, key: str) -> Any:
        def _op():
            val = self._client.rpop(key)
            try:
                if isinstance(val, str):
                    return json.loads(val)
            except Exception:
                pass
            return val
        return self._retryable(_op, "rpop")

    # ---------- Sorted set helpers ----------
    def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        def _op():
            return self._client.zadd(key, mapping)
        return self._retryable(_op, "zadd")

    def zrange(self, key: str, start: int = 0, end: int = -1, withscores: bool = False) -> List[Any]:
        def _op():
            return self._client.zrange(key, start, end, withscores=withscores)
        return self._retryable(_op, "zrange")

    # ---------- Lua / atomic helpers ----------
    def eval_script(self, script: str, keys: List[str] = [], args: List[Any] = []) -> Any:
        def _op():
            return self._client.eval(script, len(keys), *(keys + args))
        return self._retryable(_op, "eval")

    # ---------- Pub/Sub ----------
    def publish(self, channel: str, message: Any) -> int:
        def _op():
            payload = message if isinstance(message, (str, bytes)) else json.dumps(message, ensure_ascii=False)
            return self._client.publish(channel, payload)
        return self._retryable(_op, "publish")

    def subscribe(self, channels: Union[str, List[str]]) -> Iterator[Tuple[str, Any]]:
        """
        Subscribe to channels (blocking). Yields (channel, message).
        Use responsibly — this will block the executing thread.
        """
        def _op():
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            if isinstance(channels, str):
                pubsub.subscribe(channels)
            else:
                pubsub.subscribe(*channels)
            return pubsub

        pubsub = self._retryable(_op, "subscribe")
        try:
            for item in pubsub.listen():
                # item: {'type':'message', 'pattern':None, 'channel':'ch', 'data':b'...'}
                if item is None:
                    continue
                if item.get("type") != "message":
                    continue
                ch = item.get("channel")
                data = item.get("data")
                try:
                    if isinstance(data, (bytes, bytearray)):
                        text = data.decode("utf-8", errors="ignore")
                        try:
                            parsed = json.loads(text)
                            yield ch, parsed
                        except Exception:
                            yield ch, text
                    else:
                        yield ch, data
                except Exception:
                    yield ch, data
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    # ---------- Simple distributed lock (context manager) ----------
    class _LockCtx:
        def __init__(self, parent: "RedisConnector", name: str, ttl: int, blocking: bool, blocking_timeout: Optional[float]):
            self.parent = parent
            self.name = name
            self.ttl = ttl
            self.blocking = blocking
            self.blocking_timeout = blocking_timeout
            self._token = f"omniflow-lock-{random.random():.16f}"

        def acquire(self) -> bool:
            start = time.time()
            while True:
                # SET key value NX PX ttl
                ok = self.parent._client.set(self.name, self._token, nx=True, px=int(self.ttl * 1000))
                if ok:
                    return True
                if not self.blocking:
                    return False
                if self.blocking_timeout is not None and (time.time() - start) >= self.blocking_timeout:
                    return False
                time.sleep(0.05)

        def release(self) -> None:
            # Unlock safely using Lua to delete only if token matches
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            try:
                self.parent._client.eval(script, 1, self.name, self._token)
            except Exception:
                logger.exception("Failed to release lock %s", self.name)

        def __enter__(self):
            ok = self.acquire()
            if not ok:
                raise RedisError(f"Failed to acquire lock {self.name}")
            return self

        def __exit__(self, exc_type, exc, tb):
            self.release()

    def lock(self, name: str, ttl: int = 10, blocking: bool = True, blocking_timeout: Optional[float] = None):
        """
        Return a context manager implementing a simple distributed lock.

        Example:
            with client.lock("jobs:1", ttl=30):
                # critical section
        """
        return RedisConnector._LockCtx(self, name, ttl, blocking, blocking_timeout)

    # ---------- Connection lifecycle ----------
    def close(self):
        try:
            if hasattr(self._client, "close"):
                self._client.close()
        except Exception:
            logger.exception("Error closing redis client")


# ---- Async connector ----
class AsyncRedisConnector:
    """
    Asynchronous Redis connector wrapper using redis.asyncio (or aioredis if you prefer).
    API largely mirrors RedisConnector but with awaitable methods.
    """

    def __init__(self, cfg: RedisConfig):
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        if aioredis is None:
            # Try modern redis.python asyncio entrypoint if available
            if redis is not None:
                try:
                    import redis.asyncio as aiomod  # type: ignore
                    globals()["aioredis"] = aiomod
                except Exception:
                    aioredis = None  # type: ignore
            if aioredis is None:
                raise RedisConnectionError("`redis.asyncio` or compatible async redis client is required for AsyncRedisConnector.")
        # Build client
        if cfg.url:
            self._client: RedisAsyncClient = aioredis.from_url(cfg.url, socket_timeout=cfg.socket_timeout, decode_responses=cfg.decode_responses)
        else:
            self._client = aioredis.Redis(
                host=cfg.host,
                port=cfg.port,
                db=cfg.db,
                password=cfg.password,
                socket_timeout=cfg.socket_timeout,
                decode_responses=cfg.decode_responses,
            )

    # ---------- Low-level helpers ----------
    async def _retryable(self, func: Callable[[], Any], action: str = "redis") -> Any:
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                start = time.time()
                res = await func()
                latency = time.time() - start
                self.metrics(f"{action}_completed", {"attempt": attempt, "latency": latency})
                return res
            except (aioredis.exceptions.RedisError if aioredis is not None else Exception) as exc:
                last_exc = exc
                if attempt < self.cfg.max_retries:
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    self.metrics(f"{action}_retry", {"attempt": attempt + 1, "error": str(exc)})
                    await asyncio.sleep(wait)
                    continue
                raise RedisCommandError(str(exc)) from exc
        raise RedisCommandError(f"{action} failed after retries: {last_exc!s}")

    # ---------- Async convenience commands ----------
    async def get(self, key: str) -> Any:
        async def _op():
            val = await self._client.get(key)
            try:
                if isinstance(val, str):
                    return json.loads(val)
            except Exception:
                pass
            return val
        return await self._retryable(_op, "get_async")

    async def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False) -> bool:
        async def _op():
            v = value
            if not isinstance(value, (bytes, bytearray, str, int, float, bool)):
                v = json.dumps(value, ensure_ascii=False)
            return await self._client.set(key, v, ex=ex, nx=nx)
        return await self._retryable(_op, "set_async")

    async def delete(self, key: Union[str, List[str]]) -> int:
        async def _op():
            return await self._client.delete(key)
        return await self._retryable(_op, "delete_async")

    async def incr(self, key: str, amount: int = 1) -> int:
        async def _op():
            return await self._client.incr(key, amount)
        return await self._retryable(_op, "incr_async")

    async def expire(self, key: str, seconds: int) -> bool:
        async def _op():
            return await self._client.expire(key, seconds)
        return await self._retryable(_op, "expire_async")

    async def ttl(self, key: str) -> int:
        async def _op():
            return await self._client.ttl(key)
        return await self._retryable(_op, "ttl_async")

    async def exists(self, key: str) -> bool:
        async def _op():
            return bool(await self._client.exists(key))
        return await self._retryable(_op, "exists_async")

    async def get_or_set(self, key: str, factory: Callable[[], Any], ex: Optional[int] = None) -> Any:
        val = await self.get(key)
        if val is not None:
            return val
        new_val = await asyncio.get_event_loop().run_in_executor(None, factory)
        ok = await self.set(key, new_val, ex=ex, nx=True)
        if ok:
            return new_val
        return await self.get(key)

    # ---------- Async list/queue ----------
    async def lpush(self, key: str, *values: Any) -> int:
        async def _op():
            return await self._client.lpush(key, *values)
        return await self._retryable(_op, "lpush_async")

    async def rpop(self, key: str) -> Any:
        async def _op():
            val = await self._client.rpop(key)
            try:
                if isinstance(val, str):
                    return json.loads(val)
            except Exception:
                pass
            return val
        return await self._retryable(_op, "rpop_async")

    # ---------- Async sorted set ----------
    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        async def _op():
            return await self._client.zadd(key, mapping)
        return await self._retryable(_op, "zadd_async")

    async def zrange(self, key: str, start: int = 0, end: int = -1, withscores: bool = False) -> List[Any]:
        async def _op():
            return await self._client.zrange(key, start, end, withscores=withscores)
        return await self._retryable(_op, "zrange_async")

    # ---------- Async Lua eval ----------
    async def eval_script(self, script: str, keys: List[str] = [], args: List[Any] = []) -> Any:
        async def _op():
            return await self._client.eval(script, len(keys), *(keys + args))
        return await self._retryable(_op, "eval_async")

    # ---------- Async pub/sub ----------
    async def publish(self, channel: str, message: Any) -> int:
        async def _op():
            payload = message if isinstance(message, (str, bytes)) else json.dumps(message, ensure_ascii=False)
            return await self._client.publish(channel, payload)
        return await self._retryable(_op, "publish_async")

    async def subscribe(self, channels: Union[str, List[str]]):
        """
        Async subscribe helper returning an async iterator over (channel, message).
        Example:
            async for ch, msg in client.subscribe("ch"):
                ...
        """
        # Using redis-py's PubSub (asyncio) API
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        if isinstance(channels, str):
            await pubsub.subscribe(channels)
        else:
            await pubsub.subscribe(*channels)

        async def _aiter():
            try:
                while True:
                    item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)  # poll interval
                    if not item:
                        await asyncio.sleep(0.01)
                        continue
                    # item may be dict: {"type":"message", "pattern":None, "channel":"ch", "data":"..."}
                    if item.get("type") != "message":
                        continue
                    ch = item.get("channel")
                    data = item.get("data")
                    if isinstance(data, (bytes, bytearray)):
                        try:
                            text = data.decode("utf-8")
                            parsed = json.loads(text)
                            yield ch, parsed
                        except Exception:
                            yield ch, data.decode("utf-8", errors="ignore")
                    else:
                        yield ch, data
            finally:
                try:
                    await pubsub.close()
                except Exception:
                    pass

        return _aiter()

    # ---------- Async lock ----------
    class _AsyncLockCtx:
        def __init__(self, parent: "AsyncRedisConnector", name: str, ttl: int, blocking: bool, blocking_timeout: Optional[float]):
            self.parent = parent
            self.name = name
            self.ttl = ttl
            self.blocking = blocking
            self.blocking_timeout = blocking_timeout
            self._token = f"omniflow-lock-{random.random():.16f}"

        async def acquire(self) -> bool:
            start = time.time()
            while True:
                ok = await self.parent._client.set(self.name, self._token, nx=True, px=int(self.ttl * 1000))
                if ok:
                    return True
                if not self.blocking:
                    return False
                if self.blocking_timeout is not None and (time.time() - start) >= self.blocking_timeout:
                    return False
                await asyncio.sleep(0.05)

        async def release(self) -> None:
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            try:
                await self.parent._client.eval(script, 1, self.name, self._token)
            except Exception:
                logger.exception("Failed to release async lock %s", self.name)

        async def __aenter__(self):
            ok = await self.acquire()
            if not ok:
                raise RedisError(f"Failed to acquire async lock {self.name}")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            await self.release()

    def lock(self, name: str, ttl: int = 10, blocking: bool = True, blocking_timeout: Optional[float] = None):
        """
        Return an async context manager for a lock:
            async with async_client.lock("jobs:1", ttl=30):
                ...
        """
        return AsyncRedisConnector._AsyncLockCtx(self, name, ttl, blocking, blocking_timeout)

    # ---------- Lifecycle ----------
    async def close(self):
        try:
            if hasattr(self._client, "close"):
                await self._client.close()
        except Exception:
            logger.exception("Error closing async redis client")


# ---- Factories ----
def default_redis_from_env(prefix: str = "REDIS") -> RedisConnector:
    cfg = RedisConfig.from_env(prefix=prefix)
    return RedisConnector(cfg)


def default_async_redis_from_env(prefix: str = "REDIS") -> AsyncRedisConnector:
    cfg = RedisConfig.from_env(prefix=prefix)
    return AsyncRedisConnector(cfg)


# ---- Example usage (not executed on import) ----
if __name__ == "__main__":  # pragma: no cover - demo only
    logging.basicConfig(level=logging.DEBUG)
    cfg = RedisConfig.from_env()
    try:
        c = RedisConnector(cfg)
        print("PING:", c._client.ping())
        c.set("omniflow:test", {"hello": "world"}, ex=10)
        print("GET:", c.get("omniflow:test"))
        with c.lock("omniflow:lock:test", ttl=5):
            print("Acquired sync lock")
        c.publish("omniflow:ch", {"event": "demo"})
    except Exception:
        logger.exception("Sync demo failed")

    if aioredis is not None:
        async def async_demo():
            ac = AsyncRedisConnector(cfg)
            print("Async PING:", await ac._client.ping())
            await ac.set("omniflow:async:test", {"hello": "async"}, ex=10)
            print("Async GET:", await ac.get("omniflow:async:test"))
            async with ac.lock("omniflow:lock:async", ttl=5):
                print("Acquired async lock")
            await ac.publish("omniflow:ch", {"event": "async-demo"})
            await ac.close()
        try:
            asyncio.run(async_demo())
        except Exception:
            logger.exception("Async demo failed")
    else:
        logger.info("Async redis not available; skipping async demo")
