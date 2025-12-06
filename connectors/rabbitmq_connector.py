# OmniFlow/connectors/rabbitmq_connector.py
"""
OmniFlow — RabbitMQ connector
==============================

Production-ready, dependency-aware Python connector for RabbitMQ (AMQP 0-9-1) brokers.
Provides both synchronous (pika) and asynchronous (aio_pika) clients with:

- Environment-driven configuration and sensible defaults.
- Connection management with automatic reconnect and exponential backoff.
- Simple Producer (publish) and Consumer (subscribe) abstractions.
- Confirmations (publisher confirms) support for at-least-once delivery.
- QoS (prefetch) control and manual/auto-ack strategies.
- Optional JSON serialization helpers and content-type handling.
- Structured logging and an optional metrics hook.
- Clear exception hierarchy and safe shutdown utilities.
- Small usage examples in docstrings.

Notes:
- This module does not strictly require `pika` or `aio_pika` at import time; it fails with
  descriptive errors when trying to use sync/async features without the corresponding libs.
- Use durable exchanges/queues in production and bind appropriately. Prefer TLS connections
  and least-privileged RabbitMQ users.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

# Optional imports
try:
    import pika  # type: ignore
    from pika.adapters.blocking_connection import BlockingConnection  # type: ignore
    from pika.exceptions import AMQPConnectionError as PikaConnectionError, ChannelClosedByBroker  # type: ignore
except Exception:
    pika = None
    BlockingConnection = None
    PikaConnectionError = Exception
    ChannelClosedByBroker = Exception

try:
    import aio_pika  # type: ignore
    from aio_pika import ExchangeType as AioExchangeType  # type: ignore
except Exception:
    aio_pika = None
    AioExchangeType = None

logger = logging.getLogger("omniflow.connectors.rabbitmq")
logger.addHandler(logging.NullHandler())

__all__ = [
    "RabbitMQError",
    "RabbitMQConnectionError",
    "RabbitMQPublishError",
    "RabbitMQConsumeError",
    "RabbitMQConfig",
    "SyncRabbitMQProducer",
    "SyncRabbitMQConsumer",
    "AsyncRabbitMQProducer",
    "AsyncRabbitMQConsumer",
    "default_rabbitmq_config_from_env",
]


# ---- Exceptions ----
class RabbitMQError(Exception):
    """Base exception type for RabbitMQ connector."""


class RabbitMQConnectionError(RabbitMQError):
    """Connection or channel-level errors."""


class RabbitMQPublishError(RabbitMQError):
    """Publishing failed after retries."""


class RabbitMQConsumeError(RabbitMQError):
    """Consumption handler errors."""


# ---- Config dataclass ----
@dataclass
class RabbitMQConfig:
    """
    RabbitMQ connection configuration.

    Environment variables supported (defaults shown in code):
      - RABBITMQ_URL (amqp://user:pass@host:5672/vhost) or parts below
      - RABBITMQ_HOST
      - RABBITMQ_PORT
      - RABBITMQ_VHOST
      - RABBITMQ_USERNAME
      - RABBITMQ_PASSWORD
      - RABBITMQ_HEARTBEAT (s)
      - RABBITMQ_CONNECTION_TIMEOUT (s)
      - RABBITMQ_PREFETCH (consumer prefetch count)
      - RABBITMQ_MAX_RETRIES (reconnect/publish attempts)
      - RABBITMQ_BACKOFF_FACTOR (base seconds for backoff)
      - RABBITMQ_CLIENT_PROPERTIES (JSON string for connection properties)
      - RABBITMQ_SSL (true/false) -- TLS usage (pika/aio_pika must be configured externally if needed)
    """

    url: Optional[str] = None
    host: str = "localhost"
    port: int = 5672
    vhost: str = "/"
    username: Optional[str] = None
    password: Optional[str] = None
    heartbeat: int = 60
    connection_timeout: int = 10
    prefetch_count: int = 50
    max_retries: int = 3
    backoff_factor: float = 0.5
    client_properties: Optional[Dict[str, Any]] = None
    ssl: bool = False
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None

    @staticmethod
    def from_env(prefix: str = "RABBITMQ") -> "RabbitMQConfig":
        url = os.getenv(f"{prefix}_URL") or os.getenv("RABBITMQ_URL")
        host = os.getenv(f"{prefix}_HOST") or os.getenv("RABBITMQ_HOST") or "localhost"
        port = int(os.getenv(f"{prefix}_PORT") or os.getenv("RABBITMQ_PORT") or "5672")
        vhost = os.getenv(f"{prefix}_VHOST") or os.getenv("RABBITMQ_VHOST") or "/"
        username = os.getenv(f"{prefix}_USERNAME") or os.getenv("RABBITMQ_USERNAME")
        password = os.getenv(f"{prefix}_PASSWORD") or os.getenv("RABBITMQ_PASSWORD")
        heartbeat = int(os.getenv(f"{prefix}_HEARTBEAT") or os.getenv("RABBITMQ_HEARTBEAT") or "60")
        conn_timeout = int(os.getenv(f"{prefix}_CONNECTION_TIMEOUT") or os.getenv("RABBITMQ_CONNECTION_TIMEOUT") or "10")
        prefetch = int(os.getenv(f"{prefix}_PREFETCH") or os.getenv("RABBITMQ_PREFETCH") or "50")
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES") or os.getenv("RABBITMQ_MAX_RETRIES") or "3")
        backoff = float(os.getenv(f"{prefix}_BACKOFF_FACTOR") or os.getenv("RABBITMQ_BACKOFF_FACTOR") or "0.5")
        ssl_flag = str(os.getenv(f"{prefix}_SSL") or os.getenv("RABBITMQ_SSL") or "false").lower() in ("1", "true", "yes")
        props_raw = os.getenv(f"{prefix}_CLIENT_PROPERTIES")
        props = None
        if props_raw:
            try:
                props = json.loads(props_raw)
            except Exception:
                props = None
        return RabbitMQConfig(
            url=url,
            host=host,
            port=port,
            vhost=vhost,
            username=username,
            password=password,
            heartbeat=heartbeat,
            connection_timeout=conn_timeout,
            prefetch_count=prefetch,
            max_retries=max_retries,
            backoff_factor=backoff,
            client_properties=props,
            ssl=ssl_flag,
        )


def default_rabbitmq_config_from_env(prefix: str = "RABBITMQ") -> RabbitMQConfig:
    return RabbitMQConfig.from_env(prefix=prefix)


# ---- Utilities ----
def _compute_backoff(attempt: int, factor: float = 0.5, jitter: float = 0.2) -> float:
    """Exponential backoff with jitter. attempt is 0-based."""
    base = factor * (2 ** attempt)
    jitter_amt = base * jitter * (random.random() * 2 - 1)
    return max(0.0, base + jitter_amt)


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


# ---- Sync Producer & Consumer using pika ----
class SyncRabbitMQProducer:
    """
    Synchronous RabbitMQ producer using pika.BlockingConnection.

    Basic usage:
        cfg = RabbitMQConfig.from_env()
        p = SyncRabbitMQProducer(cfg)
        p.publish(exchange="my-ex", routing_key="rk", body={"hello":"world"}, durable=True, properties={"content_type":"application/json"})
        p.close()
    """

    def __init__(self, cfg: RabbitMQConfig):
        if pika is None:
            raise RabbitMQConnectionError("pika is required for SyncRabbitMQProducer. Install pika.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self._conn: Optional[BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._ensure_connection()

    def _build_params(self) -> pika.ConnectionParameters:
        if self.cfg.url:
            return pika.URLParameters(self.cfg.url)
        creds = None
        if self.cfg.username:
            creds = pika.PlainCredentials(self.cfg.username, self.cfg.password or "")
        params = pika.ConnectionParameters(
            host=self.cfg.host,
            port=self.cfg.port,
            virtual_host=self.cfg.vhost,
            credentials=creds,
            heartbeat=self.cfg.heartbeat,
            blocked_connection_timeout=self.cfg.connection_timeout,
            client_properties=self.cfg.client_properties,
            # ssl options not explicitly set here; advanced SSL should use pika.URLParameters with amqps://
        )
        return params

    def _ensure_connection(self):
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                if self._conn and self._conn.is_open:
                    return
                params = self._build_params()
                self._conn = pika.BlockingConnection(params)
                self._channel = self._conn.channel()
                # enable publisher confirms for stronger guarantees
                try:
                    self._channel.confirm_delivery()
                except Exception:
                    logger.debug("confirm_delivery not available or failed; continuing without confirms")
                logger.info("Connected to RabbitMQ (sync) at %s:%s", self.cfg.host, self.cfg.port)
                return
            except (PikaConnectionError, socket.error) as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                logger.warning("Failed to connect to RabbitMQ (attempt %d/%d): %s — retrying after %.2fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                self.metrics("connect_retry", {"attempt": attempt + 1, "error": str(exc)})
                time.sleep(wait)
                continue
        raise RabbitMQConnectionError(f"Failed to connect to RabbitMQ after retries: {last_exc!s}")

    def publish(
        self,
        exchange: str,
        routing_key: str,
        body: Union[str, bytes, Dict[str, Any]],
        content_type: str = "application/json",
        durable: bool = True,
        mandatory: bool = False,
        properties: Optional[Dict[str, Any]] = None,
        exchange_type: str = "direct",
        declare_exchange: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Publish a message.

        - body may be bytes, str or dict (dict will be JSON-serialized).
        - properties is a dict of pika.BasicProperties-like fields (headers, message_id, etc).
        - declare_exchange: if True, declare the exchange before publishing.
        """
        if self._channel is None or self._conn is None or self._conn.is_closed:
            self._ensure_connection()
        props = pika.BasicProperties(content_type=content_type, delivery_mode=2 if durable else 1)
        if properties:
            # merge known fields
            if "headers" in properties:
                props.headers = properties["headers"]
            if "message_id" in properties:
                props.message_id = properties["message_id"]
            # other properties can be set by callers modifying BasicProperties directly if needed
        payload: Union[bytes, str]
        if isinstance(body, (dict, list)):
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        elif isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        else:
            payload = str(body).encode("utf-8")

        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                if declare_exchange:
                    # declare durable exchange to avoid accidental loss
                    self._channel.exchange_declare(exchange=exchange, exchange_type=exchange_type, durable=durable)
                # publish with mandatory flag to get returned messages if unroutable (if broker supports it)
                ok = self._channel.basic_publish(
                    exchange=exchange,
                    routing_key=routing_key,
                    body=payload,
                    properties=props,
                    mandatory=mandatory
                )
                # basic_publish returns True/False depending on confirms if enabled
                self.metrics("publish_success", {"exchange": exchange, "routing_key": routing_key})
                logger.debug("Published message exchange=%s routing_key=%s len=%d", exchange, routing_key, len(payload))
                return
            except (PikaConnectionError, ChannelClosedByBroker, socket.error) as exc:
                last_exc = exc
                # reconnect and retry
                logger.warning("Publish attempt %d failed: %s", attempt + 1, exc)
                self.metrics("publish_retry", {"attempt": attempt + 1, "error": str(exc)})
                # attempt to reopen connection
                try:
                    self._ensure_connection()
                except Exception:
                    pass
                if attempt < self.cfg.max_retries:
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    time.sleep(wait)
                    continue
                break
            except Exception as exc:
                last_exc = exc
                logger.exception("Unexpected error during publish")
                break
        raise RabbitMQPublishError(f"Failed to publish message after retries: {last_exc!s}")

    def close(self):
        try:
            if self._channel:
                try:
                    if self._channel.is_open:
                        self._channel.close()
                except Exception:
                    pass
            if self._conn:
                try:
                    if self._conn.is_open:
                        self._conn.close()
                except Exception:
                    pass
        except Exception:
            logger.exception("Error closing RabbitMQ connection")

    # Context manager
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class SyncRabbitMQConsumer:
    """
    Synchronous RabbitMQ consumer using pika.BlockingConnection.

    Usage:
        def handler(body, properties, envelope):
            print("Got", body)
            # return True to ack, False to nack/requeue

        cfg = RabbitMQConfig.from_env()
        c = SyncRabbitMQConsumer(cfg)
        c.consume(queue="my-queue", on_message=handler, auto_ack=False)
    """

    def __init__(self, cfg: RabbitMQConfig):
        if pika is None:
            raise RabbitMQConnectionError("pika is required for SyncRabbitMQConsumer. Install pika.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self._conn: Optional[BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._ensure_connection()

    def _ensure_connection(self):
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                if self._conn and self._conn.is_open:
                    return
                params = SyncRabbitMQProducer(self.cfg)._build_params()
                self._conn = pika.BlockingConnection(params)
                self._channel = self._conn.channel()
                # set QoS
                try:
                    self._channel.basic_qos(prefetch_count=self.cfg.prefetch_count)
                except Exception:
                    logger.debug("Failed to set prefetch; continuing")
                logger.info("Connected to RabbitMQ (sync consumer)")
                return
            except (PikaConnectionError, socket.error) as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                logger.warning("Consumer connect attempt %d/%d failed: %s — retrying after %.2fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                self.metrics("connect_retry", {"attempt": attempt + 1, "error": str(exc)})
                time.sleep(wait)
        raise RabbitMQConnectionError(f"Failed to connect consumer after retries: {last_exc!s}")

    def consume(
        self,
        queue: str,
        on_message: Callable[[bytes, pika.spec.BasicProperties, pika.spec.Basic.Deliver], bool],
        auto_ack: bool = False,
        consumer_tag: Optional[str] = None,
        durable: bool = True,
        requeue_on_error: bool = True,
    ) -> None:
        """
        Start consuming and block until interruption. on_message should return True to ack, False to nack.
        """
        if self._channel is None:
            self._ensure_connection()
        # declare queue optionally durable
        try:
            self._channel.queue_declare(queue=queue, durable=durable)
        except Exception:
            logger.debug("Queue declare failed or not necessary")

        def _callback(ch, method, properties, body):
            try:
                self.metrics("message_received", {"queue": queue, "delivery_tag": getattr(method, "delivery_tag", None)})
                ok = on_message(body, properties, method)
                if not auto_ack:
                    if ok:
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                        self.metrics("message_ack", {"queue": queue, "delivery_tag": method.delivery_tag})
                    else:
                        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=requeue_on_error)
                        self.metrics("message_nack", {"queue": queue, "delivery_tag": method.delivery_tag})
            except Exception as exc:
                logger.exception("Exception in on_message handler")
                self.metrics("consumer_handler_error", {"error": str(exc)})
                if not auto_ack:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=requeue_on_error)

        try:
            self._channel.basic_consume(queue=queue, on_message_callback=_callback, auto_ack=auto_ack, consumer_tag=consumer_tag)
            logger.info("Starting consumer loop on queue=%s", queue)
            # Blocking I/O loop
            self._channel.start_consuming()
        except KeyboardInterrupt:
            logger.info("Consumer interrupted by KeyboardInterrupt")
        except Exception as exc:
            logger.exception("Consumer loop error")
            raise RabbitMQConsumeError(str(exc))

    def stop(self):
        try:
            if self._channel:
                try:
                    self._channel.stop_consuming()
                except Exception:
                    pass
        except Exception:
            logger.exception("Error stopping consumer")

    def close(self):
        try:
            if self._channel:
                try:
                    if self._channel.is_open:
                        self._channel.close()
                except Exception:
                    pass
            if self._conn:
                try:
                    if self._conn.is_open:
                        self._conn.close()
                except Exception:
                    pass
        except Exception:
            logger.exception("Error closing consumer connection")


# ---- Async Producer & Consumer using aio_pika ----
class AsyncRabbitMQProducer:
    """
    Asynchronous RabbitMQ producer using aio_pika.

    Usage (async):
        cfg = RabbitMQConfig.from_env()
        p = AsyncRabbitMQProducer(cfg)
        await p.connect()
        await p.publish(exchange="ex", routing_key="rk", body={"msg":"hello"})
        await p.close()
    """

    def __init__(self, cfg: RabbitMQConfig):
        if aio_pika is None:
            raise RabbitMQConnectionError("aio_pika is required for AsyncRabbitMQProducer. Install aio-pika.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self._conn: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.RobustChannel] = None
        self._lock = asyncio.Lock()
        self._connected = False

    async def connect(self):
        async with self._lock:
            if self._connected:
                return
            last_exc = None
            for attempt in range(self.cfg.max_retries + 1):
                try:
                    if self.cfg.url:
                        self._conn = await aio_pika.connect_robust(self.cfg.url, timeout=self.cfg.connection_timeout)
                    else:
                        credentials = None
                        if self.cfg.username:
                            credentials = aio_pika.PlainCredentials(self.cfg.username, self.cfg.password or "")
                        self._conn = await aio_pika.connect_robust(
                            host=self.cfg.host,
                            port=self.cfg.port,
                            login=self.cfg.username,
                            password=self.cfg.password,
                            virtualhost=self.cfg.vhost,
                            timeout=self.cfg.connection_timeout,
                        )
                    self._channel = await self._conn.channel()
                    await self._channel.set_qos(prefetch_count=self.cfg.prefetch_count)
                    self._connected = True
                    logger.info("Connected to RabbitMQ (async)")
                    return
                except Exception as exc:
                    last_exc = exc
                    wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                    logger.warning("Async connect attempt %d/%d failed: %s — retrying after %.2fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                    self.metrics("connect_retry", {"attempt": attempt + 1, "error": str(exc)})
                    await asyncio.sleep(wait)
            raise RabbitMQConnectionError(f"Failed to connect async after retries: {last_exc!s}")

    async def publish(
        self,
        exchange: str,
        routing_key: str,
        body: Union[str, bytes, Dict[str, Any]],
        exchange_type: Union[str, aio_pika.ExchangeType] = aio_pika.ExchangeType.DIRECT,
        durable: bool = True,
        mandatory: bool = False,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._connected:
            await self.connect()
        if self._channel is None:
            raise RabbitMQConnectionError("Channel not available")
        # Serialize payload
        if isinstance(body, (dict, list)):
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        elif isinstance(body, str):
            payload = body.encode("utf-8")
            content_type = "text/plain"
        else:
            payload = body
            content_type = "application/octet-stream"
        # properties -> aio_pika.Message
        msg_props = {}
        if properties:
            msg_props.update(properties)
        # Publish with retries
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                exch = await self._channel.declare_exchange(name=exchange, type=exchange_type, durable=durable)
                message = aio_pika.Message(body=payload, content_type=content_type, delivery_mode=aio_pika.DeliveryMode.PERSISTENT if durable else aio_pika.DeliveryMode.NOT_PERSISTENT, headers=msg_props.get("headers"))
                await exch.publish(message, routing_key=routing_key, mandatory=mandatory)
                self.metrics("publish_success", {"exchange": exchange, "routing_key": routing_key})
                logger.debug("Async published exchange=%s routing_key=%s len=%d", exchange, routing_key, len(payload))
                return
            except Exception as exc:
                last_exc = exc
                self.metrics("publish_retry", {"attempt": attempt + 1, "error": str(exc)})
                logger.warning("Async publish attempt %d failed: %s", attempt + 1, exc)
                # attempt reconnect on connection errors
                try:
                    await self.connect()
                except Exception:
                    pass
                if attempt < self.cfg.max_retries:
                    await asyncio.sleep(_compute_backoff(attempt, self.cfg.backoff_factor))
                    continue
                break
        raise RabbitMQPublishError(f"Async publish failed after retries: {last_exc!s}")

    async def close(self):
        try:
            if self._channel:
                await self._channel.close()
            if self._conn:
                await self._conn.close()
        except Exception:
            logger.exception("Error closing async connection")
        finally:
            self._connected = False

    # async context manager
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


class AsyncRabbitMQConsumer:
    """
    Asynchronous RabbitMQ consumer using aio_pika.

    Usage (async):
        async def handler(body, message):
            print(body)
            await message.ack()

        cfg = RabbitMQConfig.from_env()
        c = AsyncRabbitMQConsumer(cfg)
        await c.consume(queue="my-queue", on_message=handler, durable=True)
    """

    def __init__(self, cfg: RabbitMQConfig):
        if aio_pika is None:
            raise RabbitMQConnectionError("aio_pika is required for AsyncRabbitMQConsumer. Install aio-pika.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self._conn: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.RobustChannel] = None
        self._connected = False
        self._task: Optional[asyncio.Task] = None

    async def connect(self):
        if self._connected:
            return
        last_exc = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                if self.cfg.url:
                    self._conn = await aio_pika.connect_robust(self.cfg.url, timeout=self.cfg.connection_timeout)
                else:
                    self._conn = await aio_pika.connect_robust(
                        host=self.cfg.host,
                        port=self.cfg.port,
                        login=self.cfg.username,
                        password=self.cfg.password,
                        virtualhost=self.cfg.vhost,
                        timeout=self.cfg.connection_timeout,
                    )
                self._channel = await self._conn.channel()
                await self._channel.set_qos(prefetch_count=self.cfg.prefetch_count)
                self._connected = True
                logger.info("Connected to RabbitMQ (async consumer)")
                return
            except Exception as exc:
                last_exc = exc
                wait = _compute_backoff(attempt, self.cfg.backoff_factor)
                logger.warning("Async consumer connect attempt %d/%d failed: %s — retrying after %.2fs", attempt + 1, self.cfg.max_retries + 1, exc, wait)
                self.metrics("connect_retry", {"attempt": attempt + 1, "error": str(exc)})
                await asyncio.sleep(wait)
        raise RabbitMQConnectionError(f"Async consumer failed to connect after retries: {last_exc!s}")

    async def consume(
        self,
        queue: str,
        on_message: Callable[[Any, aio_pika.IncomingMessage], asyncio.Future],
        durable: bool = True,
        auto_ack: bool = False,
        prefetch_count: Optional[int] = None,
    ):
        """
        Start consuming messages. on_message should be an async callable receiving (decoded_body, message).
        If auto_ack is False, the handler should ack/nack the message using message.ack() / message.nack().
        """
        await self.connect()
        assert self._channel is not None
        if prefetch_count is not None:
            await self._channel.set_qos(prefetch_count=prefetch_count)
        queue_obj = await self._channel.declare_queue(name=queue, durable=durable)

        async def _handler(message: aio_pika.IncomingMessage):
            async with message.process(ignore_processed=True):
                try:
                    body = message.body
                    content_type = message.content_type or ""
                    if content_type.startswith("application/json"):
                        try:
                            decoded = json.loads(body.decode("utf-8"))
                        except Exception:
                            decoded = body.decode("utf-8", errors="ignore")
                    elif content_type.startswith("text/"):
                        decoded = body.decode("utf-8", errors="ignore")
                    else:
                        decoded = body
                    self.metrics("message_received", {"queue": queue, "delivery_tag": message.delivery_tag})
                    # call user handler
                    res = await on_message(decoded, message)
                    # handler may choose to ack/nack; if it doesn't and auto_ack is False, message.process() context will ack automatically.
                    # The design here leaves ack control to the handler for flexibility.
                    return res
                except Exception as exc:
                    logger.exception("Exception in async on_message handler")
                    self.metrics("consumer_handler_error", {"error": str(exc)})
                    # which behavior? We let message.process() do default (ack). If user wants nack, call message.nack() in handler.
                    # For explicit control, handler should call message.nack() / message.ack() itself.
                    return None

        await queue_obj.consume(_handler, no_ack=auto_ack)
        logger.info("Async consumer started for queue=%s", queue)
        # The caller typically wants to keep running the event loop. This function returns and leaves consumption active.
        return queue_obj

    async def close(self):
        try:
            if self._channel:
                await self._channel.close()
            if self._conn:
                await self._conn.close()
        except Exception:
            logger.exception("Error closing async consumer")
        finally:
            self._connected = False


# ---- Example quick helpers / factories ----
def _example_sync_publish():
    cfg = RabbitMQConfig.from_env()
    p = SyncRabbitMQProducer(cfg)
    try:
        p.publish(exchange="omniflow-ex", routing_key="test", body={"hello": "world"}, declare_exchange=True, exchange_type="direct")
        print("Published sync")
    finally:
        p.close()


async def _example_async_publish():
    cfg = RabbitMQConfig.from_env()
    p = AsyncRabbitMQProducer(cfg)
    await p.connect()
    try:
        await p.publish(exchange="omniflow-ex", routing_key="test", body={"hello": "async"})
        print("Published async")
    finally:
        await p.close()


# ---- End of module ----
