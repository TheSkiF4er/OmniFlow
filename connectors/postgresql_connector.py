# OmniFlow/connectors/postgresql_connector.py
"""
OmniFlow — PostgreSQL connector
================================

Production-ready, dependency-conscious Python connector for PostgreSQL databases.
Provides both synchronous (psycopg2) and asynchronous (asyncpg) clients with:

- Environment-driven configuration and safe defaults.
- Connection pooling and automatic reconnection.
- Transaction context managers and convenience helpers for common patterns.
- Parameterized query helpers (prevents SQL injection when used correctly).
- Retry logic for transient errors with exponential backoff + jitter.
- Optional lightweight migrations helper (applies *.sql files from a directory).
- Healthchecks and simple metrics hook integration points.
- Clear exceptions and type hints.
- Minimal runtime dependencies: psycopg2 / asyncpg are optional — informative errors if missing.

Security notes
- Prefer connection strings from secure secret stores (not checked into repo).
- Use least-privileged DB users for plugins; avoid superuser credentials.
- Keep migrations and SQL templates reviewed and signed if needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

# Optional imports
try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
    from psycopg2.pool import ThreadedConnectionPool as PsycoThreadedPool  # type: ignore
except Exception:
    psycopg2 = None
    psycopg2 = None  # for linters

try:
    import asyncpg  # type: ignore
except Exception:
    asyncpg = None

logger = logging.getLogger("omniflow.connectors.postgresql")
logger.addHandler(logging.NullHandler())

__all__ = [
    "PostgresError",
    "PostgresConnectionError",
    "PostgresQueryError",
    "PostgresConfig",
    "SyncPostgresClient",
    "AsyncPostgresClient",
    "default_sync_client_from_env",
    "default_async_client_from_env",
]


# ---- Exceptions ----
class PostgresError(Exception):
    """Base exception for PostgreSQL connector errors."""


class PostgresConnectionError(PostgresError):
    """Connection / pool related errors."""


class PostgresQueryError(PostgresError):
    """Errors raised from query execution (non-transient)."""


# ---- Config dataclass ----
@dataclass
class PostgresConfig:
    """
    PostgreSQL connection configuration.

    Common environment variables:
      - PG_DSN or POSTGRES_DSN (DSN/connection string). If not provided,
        PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD are used.
      - PG_HOST (default: localhost)
      - PG_PORT (default: 5432)
      - PG_DB (default: postgres)
      - PG_USER (default: postgres)
      - PG_PASSWORD (no default — prefer secrets)
      - PG_MIN_POOL (default: 1)
      - PG_MAX_POOL (default: 10)
      - PG_CONNECT_TIMEOUT (seconds, default: 10)
      - PG_MAX_RETRIES (retry transient errors, default: 3)
      - PG_BACKOFF_FACTOR (exponential backoff base seconds, default: 0.2)
    """

    dsn: Optional[str] = None
    host: str = "localhost"
    port: int = 5432
    database: str = "postgres"
    user: Optional[str] = None
    password: Optional[str] = None
    min_pool: int = 1
    max_pool: int = 10
    connect_timeout: float = 10.0
    max_retries: int = 3
    backoff_factor: float = 0.2
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None

    @staticmethod
    def from_env(prefix: str = "PG") -> "PostgresConfig":
        dsn = os.getenv("PG_DSN") or os.getenv("POSTGRES_DSN")
        host = os.getenv("PG_HOST", os.getenv("POSTGRES_HOST", "localhost"))
        port = int(os.getenv("PG_PORT", os.getenv("POSTGRES_PORT", "5432")))
        database = os.getenv("PG_DB", os.getenv("POSTGRES_DB", "postgres"))
        user = os.getenv("PG_USER", os.getenv("POSTGRES_USER"))
        password = os.getenv("PG_PASSWORD", os.getenv("POSTGRES_PASSWORD"))
        min_pool = int(os.getenv("PG_MIN_POOL", "1"))
        max_pool = int(os.getenv("PG_MAX_POOL", "10"))
        connect_timeout = float(os.getenv("PG_CONNECT_TIMEOUT", "10.0"))
        max_retries = int(os.getenv("PG_MAX_RETRIES", "3"))
        backoff_factor = float(os.getenv("PG_BACKOFF_FACTOR", "0.2"))
        return PostgresConfig(
            dsn=dsn,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            min_pool=min_pool,
            max_pool=max_pool,
            connect_timeout=connect_timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )


# ---- Utilities ----
def _compute_backoff(attempt: int, factor: float = 0.2, jitter: float = 0.1) -> float:
    """
    Exponential backoff with jitter.
    attempt: 0-based attempt index.
    """
    base = factor * (2 ** attempt)
    jitter_amount = base * jitter * (random.random() * 2 - 1)
    return max(0.0, base + jitter_amount)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


# ---- Sync client (psycopg2) ----
class SyncPostgresClient:
    """
    Thread-safe synchronous PostgreSQL client using psycopg2 connection pool.

    Usage:
        cfg = PostgresConfig.from_env()
        client = SyncPostgresClient(cfg)
        with client.conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                print(cur.fetchone())
        client.close()
    """

    def __init__(self, cfg: PostgresConfig):
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        if psycopg2 is None:
            raise PostgresConnectionError("psycopg2 not installed; install psycopg2-binary or psycopg2 to use SyncPostgresClient")
        # Build dsn if not provided
        if cfg.dsn:
            self._dsn = cfg.dsn
        else:
            # safe: only include user/password if present
            user = f"user={cfg.user}" if cfg.user else ""
            password = f" password={cfg.password}" if cfg.password else ""
            self._dsn = f"host={cfg.host} port={cfg.port} dbname={cfg.database} {user}{password} connect_timeout={int(cfg.connect_timeout)}"
        try:
            self._pool = PsycoThreadedPool(minconn=cfg.min_pool, maxconn=cfg.max_pool, dsn=self._dsn)
        except Exception as exc:
            logger.exception("Failed to create psycopg2 pool")
            raise PostgresConnectionError(f"failed to create pool: {exc}") from exc

    @contextlib.contextmanager
    def conn(self):
        """
        Context manager yielding a psycopg2 connection from the pool.
        Usage:
            with client.conn() as conn:
                with conn.cursor() as cur:
                    ...
        The connection is returned to the pool on exit. Exceptions inside the block are not suppressed.
        """
        conn = None
        try:
            conn = self._pool.getconn()
            # psycopg2 by default uses autocommit = False; caller can manage transactions.
            yield conn
        except Exception as exc:
            self.metrics("sync_conn_error", {"error": str(exc)})
            raise
        finally:
            if conn is not None:
                try:
                    # reset connection state before returning; psycopg2 has reset_session
                    conn.rollback()
                except Exception:
                    # if rollback fails, attempt to close and replace
                    try:
                        conn.close()
                    except Exception:
                        pass
                try:
                    self._pool.putconn(conn)
                except Exception:
                    # pool may be closed or conn invalid
                    logger.debug("putconn failed; closing conn", exc_info=True)

    def execute(
        self,
        query: str,
        params: Optional[Tuple[Any, ...]] = None,
        fetch: str = "none",
        timeout: Optional[int] = None,
    ) -> Any:
        """
        Convenience execute with retries.

        fetch: "none" (default) -> return None
               "one" -> return cursor.fetchone()
               "all" -> return cursor.fetchall()
               "value" -> return single scalar (first column of first row)
        """
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.cfg.max_retries:
            try:
                with self.conn() as conn:
                    # Optionally set statement_timeout for this session if provided
                    if timeout is not None:
                        with conn.cursor() as cur:
                            cur.execute("SET LOCAL statement_timeout = %s", (int(timeout * 1000),))
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(query, params)
                        if fetch == "one":
                            return cur.fetchone()
                        if fetch == "all":
                            return cur.fetchall()
                        if fetch == "value":
                            row = cur.fetchone()
                            if not row:
                                return None
                            # row is dict-like; return first column
                            return list(row.values())[0]
                        return None
            except psycopg2.OperationalError as exc:
                last_exc = exc
                # transient? reconnect/retry
                attempt += 1
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("OperationalError executing query, attempt %d/%d: %s — retrying after %.2fs", attempt, self.cfg.max_retries, exc, wait)
                self.metrics("sync_query_retry", {"attempt": attempt, "error": str(exc)})
                time.sleep(wait)
                continue
            except psycopg2.Error as exc:
                # Non-transient DB-level error (e.g., syntax, constraint)
                logger.exception("Postgres query error: %s", exc)
                self.metrics("sync_query_error", {"error": str(exc)})
                raise PostgresQueryError(str(exc)) from exc
            except Exception as exc:
                last_exc = exc
                logger.exception("Unexpected error executing query")
                raise PostgresError(str(exc)) from exc
        raise PostgresConnectionError(f"query failed after retries: {last_exc!s}")

    @contextlib.contextmanager
    def transaction(self, isolation_level: Optional[str] = None):
        """
        Transaction context manager.

        Example:
            with client.transaction():
                client.execute("INSERT ...")
                client.execute("UPDATE ...")
        """
        with self.conn() as conn:
            try:
                if isolation_level is not None:
                    # psycopg2 isolation level mapping: READ COMMITTED, REPEATABLE READ, SERIALIZABLE
                    conn.set_isolation_level(getattr(psycopg2.extensions, isolation_level))
                yield conn
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    logger.exception("Rollback failed")
                raise

    def healthcheck(self) -> bool:
        """Simple healthcheck that executes a lightweight query."""
        try:
            res = self.execute("SELECT 1 as ok", fetch="value")
            return res == 1
        except Exception:
            return False

    def close(self):
        """Close the connection pool and all pooled connections."""
        try:
            self._pool.closeall()
        except Exception:
            logger.exception("Error closing pool")

    # Lightweight migrations runner: executes SQL files in lexical order and records applied files.
    def apply_migrations(self, migrations_dir: str, table_name: str = "omniflow_schema_migrations") -> List[str]:
        """
        Apply .sql files from migrations_dir. Records applied filenames in a table.
        Returns list of applied filenames in this run.

        NOTE: This is intentionally simple. For complex migrations use a dedicated tool.
        """
        applied: List[str] = []
        # ensure table exists
        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id serial PRIMARY KEY,
                filename text UNIQUE NOT NULL,
                applied_at timestamptz DEFAULT now()
            );
            """
        )
        files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".sql")])
        for fname in files:
            path = os.path.join(migrations_dir, fname)
            # skip if already applied
            existing = self.execute(f"SELECT 1 FROM {table_name} WHERE filename = %s", (fname,), fetch="value")
            if existing:
                logger.debug("Migration %s already applied; skipping", fname)
                continue
            sql = open(path, "r", encoding="utf-8").read()
            try:
                # Execute file within transaction
                with self.transaction():
                    with self.conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql)
                        with conn.cursor() as cur2:
                            cur2.execute(f"INSERT INTO {table_name} (filename) VALUES (%s)", (fname,))
                applied.append(fname)
                logger.info("Applied migration %s", fname)
            except Exception:
                logger.exception("Failed to apply migration %s", fname)
                raise
        return applied


# ---- Async client (asyncpg) ----
class AsyncPostgresClient:
    """
    Asynchronous PostgreSQL client using asyncpg connection pool.

    Usage (async):
        cfg = PostgresConfig.from_env()
        client = AsyncPostgresClient(cfg)
        await client.start()
        val = await client.fetchval("SELECT 1")
        await client.close()
    """

    def __init__(self, cfg: PostgresConfig):
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        if asyncpg is None:
            raise PostgresConnectionError("asyncpg not installed; install asyncpg to use AsyncPostgresClient")
        self._pool: Optional[asyncpg.pool.Pool] = None

    async def start(self):
        """Start the asyncpg pool."""
        if self._pool is not None:
            return
        dsn = self.cfg.dsn
        if not dsn:
            # build dsn
            dsn = f"postgresql://{self.cfg.user or ''}:{self.cfg.password or ''}@{self.cfg.host}:{self.cfg.port}/{self.cfg.database}"
        try:
            self._pool = await asyncpg.create_pool(
                dsn,
                min_size=self.cfg.min_pool,
                max_size=self.cfg.max_pool,
                timeout=self.cfg.connect_timeout,
            )
        except Exception as exc:
            logger.exception("Failed to create asyncpg pool")
            raise PostgresConnectionError(f"failed to create async pool: {exc}") from exc

    async def close(self):
        """Close the pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    @contextlib.asynccontextmanager
    async def connection(self):
        """Async context manager yielding a connection from the pool."""
        if self._pool is None:
            await self.start()
        assert self._pool is not None
        conn = await self._pool.acquire()
        try:
            yield conn
        finally:
            await self._pool.release(conn)

    async def fetchval(self, query: str, *args, timeout: Optional[float] = None) -> Any:
        """
        Fetch a single value (first column of the first row).
        """
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.cfg.max_retries:
            try:
                if self._pool is None:
                    await self.start()
                assert self._pool is not None
                async with self._pool.acquire() as conn:
                    return await conn.fetchval(query, *args, timeout=timeout)
            except (asyncpg.exceptions.PostgresConnectionError, ConnectionError) as exc:
                last_exc = exc
                attempt += 1
                wait = _compute_backoff(attempt - 1, self.cfg.backoff_factor)
                logger.warning("Async fetchval connection error attempt %d/%d: %s — retrying after %.2fs", attempt, self.cfg.max_retries, exc, wait)
                self.metrics("async_query_retry", {"attempt": attempt, "error": str(exc)})
                await asyncio.sleep(wait)
                continue
            except asyncpg.PostgresError as exc:
                logger.exception("Async Postgres query error")
                raise PostgresQueryError(str(exc)) from exc
            except Exception as exc:
                logger.exception("Unexpected async error")
                raise PostgresError(str(exc)) from exc
        raise PostgresConnectionError(f"async fetchval failed after retries: {last_exc!s}")

    async def fetch(self, query: str, *args, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        try:
            if self._pool is None:
                await self.start()
            assert self._pool is not None
            async with self._pool.acquire() as conn:
                records = await conn.fetch(query, *args, timeout=timeout)
                # Convert Record objects to dicts
                return [dict(r) for r in records]
        except asyncpg.PostgresError as exc:
            logger.exception("Async fetch error")
            raise PostgresQueryError(str(exc)) from exc

    async def execute(self, query: str, *args, timeout: Optional[float] = None) -> str:
        """Execute a statement (INSERT/UPDATE/DELETE). Returns status string."""
        try:
            if self._pool is None:
                await self.start()
            assert self._pool is not None
            async with self._pool.acquire() as conn:
                return await conn.execute(query, *args, timeout=timeout)
        except asyncpg.PostgresError as exc:
            logger.exception("Async execute error")
            raise PostgresQueryError(str(exc)) from exc

    @contextlib.asynccontextmanager
    async def transaction(self):
        """
        Async transaction context manager.

        Usage:
            async with client.transaction():
                await client.execute(...)
        """
        if self._pool is None:
            await self.start()
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                yield conn
                await tr.commit()
            except Exception:
                await tr.rollback()
                raise

    async def healthcheck(self) -> bool:
        """Lightweight health check."""
        try:
            val = await self.fetchval("SELECT 1")
            return val == 1
        except Exception:
            return False

    async def apply_migrations(self, migrations_dir: str, table_name: str = "omniflow_schema_migrations") -> List[str]:
        """
        Apply SQL migrations (async). See SyncPostgresClient.apply_migrations for notes.
        """
        applied: List[str] = []
        await self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id serial PRIMARY KEY,
                filename text UNIQUE NOT NULL,
                applied_at timestamptz DEFAULT now()
            );
            """
        )
        files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".sql")])
        for fname in files:
            path = os.path.join(migrations_dir, fname)
            existing = await self.fetchval(f"SELECT 1 FROM {table_name} WHERE filename = $1", fname)
            if existing:
                logger.debug("Async migration %s already applied; skipping", fname)
                continue
            sql = open(path, "r", encoding="utf-8").read()
            try:
                async with self.transaction():
                    async with self.connection() as conn:
                        await conn.execute(sql)
                        await conn.execute(f"INSERT INTO {table_name} (filename) VALUES ($1)", fname)
                applied.append(fname)
                logger.info("Applied async migration %s", fname)
            except Exception:
                logger.exception("Failed to apply async migration %s", fname)
                raise
        return applied


# ---- Factories ----
def default_sync_client_from_env(prefix: str = "PG") -> SyncPostgresClient:
    cfg = PostgresConfig.from_env(prefix=prefix)
    return SyncPostgresClient(cfg)


def default_async_client_from_env(prefix: str = "PG") -> AsyncPostgresClient:
    cfg = PostgresConfig.from_env(prefix=prefix)
    return AsyncPostgresClient(cfg)


# ---- Example usage (not executed on import) ----
if __name__ == "__main__":  # pragma: no cover - demo only
    logging.basicConfig(level=logging.DEBUG)
    cfg = PostgresConfig.from_env()
    try:
        sync_client = SyncPostgresClient(cfg)
        print("Sync health:", sync_client.healthcheck())
        # run a simple query
        print("Version:", sync_client.execute("SELECT version() as v", fetch="value"))
        sync_client.close()
    except Exception:
        logger.exception("Sync demo failed")

    if asyncpg is not None:
        async def async_demo():
            async_client = AsyncPostgresClient(cfg)
            await async_client.start()
            print("Async health:", await async_client.healthcheck())
            val = await async_client.fetchval("SELECT 1")
            print("Async select 1:", val)
            await async_client.close()
        try:
            asyncio.run(async_demo())
        except Exception:
            logger.exception("Async demo failed")
    else:
        logger.info("asyncpg not installed; skipping async demo")
