# OmniFlow/connectors/kafka_connector.py
"""
OmniFlow — Kafka connector
==========================

Production-ready, dependency-light Python connector for Apache Kafka compatible brokers.
Designed for use inside OmniFlow plugins, workers, and automation that require robust,
testable, and secure Kafka interactions.

Features
- Synchronous Producer/Consumer using `confluent_kafka` when available.
- Asynchronous Producer/Consumer using `aiokafka` when available.
- Environment-driven configuration and sensible defaults.
- Safe defaults: timeouts, retries with exponential backoff, idempotent producer support (where available).
- Simple message serialization helpers (JSON) and optional key/value codecs hook.
- Graceful shutdown helpers and context managers for resource safety.
- Structured logging and optional metrics hook.
- Clear exceptions hierarchy for caller logic.
- Helpful examples in docstrings.

Notes
- This connector intentionally avoids requiring Kafka client libraries at import time.
  It will raise clear errors if you attempt to use a sync or async client without
  the corresponding optional dependency installed.
- For high-throughput production deployments prefer `confluent_kafka` for sync usage
  and `aiokafka` for asyncio usage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union

# Optional imports for sync and async Kafka clients
try:
    import confluent_kafka  # type: ignore
    from confluent_kafka import Producer as ConfluentProducer  # type: ignore
    from confluent_kafka import Consumer as ConfluentConsumer  # type: ignore
    from confluent_kafka import KafkaException as ConfluentKafkaException  # type: ignore
except Exception:
    confluent_kafka = None
    ConfluentProducer = None  # type: ignore
    ConfluentConsumer = None  # type: ignore
    ConfluentKafkaException = Exception  # type: ignore

try:
    import aiokafka  # type: ignore
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer  # type: ignore
    from aiokafka.errors import KafkaError as AiokafkaError  # type: ignore
except Exception:
    aiokafka = None
    AIOKafkaProducer = None  # type: ignore
    AIOKafkaConsumer = None  # type: ignore
    AiokafkaError = Exception  # type: ignore

logger = logging.getLogger("omniflow.connectors.kafka")
logger.addHandler(logging.NullHandler())

__all__ = [
    "KafkaError",
    "KafkaConfig",
    "default_kafka_config_from_env",
    "SyncKafkaProducer",
    "SyncKafkaConsumer",
    "AsyncKafkaProducer",
    "AsyncKafkaConsumer",
]


# ---- Exceptions ----
class KafkaError(Exception):
    """Base class for Kafka connector errors."""


# ---- Config dataclass ----
@dataclass
class KafkaConfig:
    """
    Kafka connector configuration.

    Fields:
      - bootstrap_servers: comma-separated "host:port" list or list[str]
      - security_protocol: protocol (PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL)
      - sasl_mechanism, sasl_plain_username, sasl_plain_password: for SASL auth when used
      - client_id: optional client identifier
      - acks: producer acks (all, 1, 0)
      - retries: number of attempts for transient send errors
      - retry_backoff: base backoff seconds between retries
      - request_timeout: request timeout in seconds
      - max_request_size: optional max request size in bytes (client-level)
      - metrics_hook: optional callable(event: str, payload: dict) for telemetry
    """

    bootstrap_servers: Union[str, List[str]]
    security_protocol: Optional[str] = None
    sasl_mechanism: Optional[str] = None
    sasl_plain_username: Optional[str] = None
    sasl_plain_password: Optional[str] = None
    client_id: Optional[str] = None
    acks: Union[str, int] = "all"
    retries: int = 3
    retry_backoff: float = 0.5
    request_timeout: float = 30.0
    max_request_size: Optional[int] = None
    metrics_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None
    # Additional config passthrough for confluent_kafka/aiokafka clients
    extra: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_env(prefix: str = "KAFKA") -> "KafkaConfig":
        bs = os.getenv(f"{prefix}_BOOTSTRAP_SERVERS") or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        # Accept JSON array or comma-separated
        if bs.startswith("[") or bs.startswith('"'):
            try:
                parsed = json.loads(bs)
                bs_val = parsed
            except Exception:
                bs_val = bs
        else:
            # keep as comma-separated string acceptable to clients
            bs_val = bs
        cfg = KafkaConfig(
            bootstrap_servers=bs_val,
            security_protocol=os.getenv(f"{prefix}_SECURITY_PROTOCOL") or os.getenv("KAFKA_SECURITY_PROTOCOL"),
            sasl_mechanism=os.getenv(f"{prefix}_SASL_MECHANISM") or os.getenv("KAFKA_SASL_MECHANISM"),
            sasl_plain_username=os.getenv(f"{prefix}_SASL_USERNAME") or os.getenv("KAFKA_SASL_USERNAME"),
            sasl_plain_password=os.getenv(f"{prefix}_SASL_PASSWORD") or os.getenv("KAFKA_SASL_PASSWORD"),
            client_id=os.getenv(f"{prefix}_CLIENT_ID") or os.getenv("KAFKA_CLIENT_ID"),
            acks=os.getenv(f"{prefix}_ACKS", os.getenv("KAFKA_ACKS", "all")),
            retries=int(os.getenv(f"{prefix}_RETRIES", os.getenv("KAFKA_RETRIES", "3"))),
            retry_backoff=float(os.getenv(f"{prefix}_RETRY_BACKOFF", os.getenv("KAFKA_RETRY_BACKOFF", "0.5"))),
            request_timeout=float(os.getenv(f"{prefix}_REQUEST_TIMEOUT", os.getenv("KAFKA_REQUEST_TIMEOUT", "30.0"))),
            max_request_size=int(os.getenv(f"{prefix}_MAX_REQUEST_SIZE", os.getenv("KAFKA_MAX_REQUEST_SIZE", "0"))) or None,
            metrics_hook=None,
        )
        return cfg


def default_kafka_config_from_env(prefix: str = "KAFKA") -> KafkaConfig:
    return KafkaConfig.from_env(prefix=prefix)


# ---- Utility helpers ----
def _compute_backoff(attempt: int, base: float = 0.5, jitter: float = 0.2) -> float:
    """
    Exponential backoff with jitter. attempt is 0-based.
    """
    base_wait = base * (2 ** attempt)
    jitter_amt = base_wait * jitter * (random.random() * 2 - 1)
    return max(0.0, base_wait + jitter_amt)


def _to_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _default_metrics_hook(event: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - trivial
    logger.debug("metrics_hook(%s): %s", event, payload)


# ---- Synchronous Producer (confluent_kafka) ----
class SyncKafkaProducer:
    """
    Synchronous Kafka producer wrapper using `confluent_kafka.Producer`.

    Usage example:

        cfg = KafkaConfig.from_env()
        p = SyncKafkaProducer(cfg)
        p.produce("my-topic", key="k", value={"hello":"world"})
        p.flush()  # wait for outstanding messages
        p.close()
    """

    def __init__(
        self,
        cfg: KafkaConfig,
        key_serializer: Optional[Callable[[Any], bytes]] = None,
        value_serializer: Optional[Callable[[Any], bytes]] = None,
    ):
        if ConfluentProducer is None:
            raise KafkaError("confluent_kafka is required for SyncKafkaProducer. Install confluent-kafka-python.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self._key_serializer = key_serializer or (lambda k: k.encode("utf-8") if isinstance(k, str) else _to_json_bytes(k) if k is not None else None)
        self._value_serializer = value_serializer or (lambda v: _to_json_bytes(v) if v is not None else None)
        # Build confluent config
        conf = {
            "bootstrap.servers": cfg.bootstrap_servers if isinstance(cfg.bootstrap_servers, str) else ",".join(cfg.bootstrap_servers),
            "client.id": cfg.client_id or "omniflow-sync-producer",
            "enable.idempotence": True,  # safer delivery semantics if broker supports it
            "acks": str(cfg.acks),
            "message.send.max.retries": cfg.retries,
            "retry.backoff.ms": int(cfg.retry_backoff * 1000),
            "request.timeout.ms": int(cfg.request_timeout * 1000),
        }
        if cfg.max_request_size:
            conf["message.max.bytes"] = cfg.max_request_size
        # Security settings passthrough
        if cfg.security_protocol:
            conf["security.protocol"] = cfg.security_protocol
        if cfg.sasl_mechanism:
            conf["sasl.mechanisms"] = cfg.sasl_mechanism
        if cfg.sasl_plain_username:
            conf["sasl.username"] = cfg.sasl_plain_username
        if cfg.sasl_plain_password:
            conf["sasl.password"] = cfg.sasl_plain_password
        conf.update(cfg.extra or {})
        self._producer = ConfluentProducer(conf)
        # Delivery callback bookkeeping
        self._pending = 0

    def _on_delivery(self, err, msg):
        topic = msg.topic() if msg else None
        partition = msg.partition() if msg else None
        offset = msg.offset() if msg else None
        if err:
            logger.error("Delivery failed for topic=%s partition=%s: %s", topic, partition, err)
            self.metrics("produce_failed", {"topic": topic, "partition": partition, "error": str(err)})
        else:
            self.metrics("produce_success", {"topic": topic, "partition": partition, "offset": offset})
        # decrement pending counter
        try:
            self._pending -= 1
        except Exception:
            self._pending = max(0, self._pending - 1)

    def produce(self, topic: str, key: Any = None, value: Any = None, partition: Optional[int] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> None:
        """
        Produce a single message (fire-and-forget with delivery callback).
        For blocking/guaranteed delivery call `flush()` after produces.
        """
        k = None if key is None else self._key_serializer(key)
        v = None if value is None else self._value_serializer(value)
        # Confluent expects headers list of tuples or dict
        hdrs = None
        if headers:
            hdrs = [(k, str(v)) for k, v in headers.items()]
        attempts = 0
        last_exc = None
        while attempts <= self.cfg.retries:
            try:
                # increment pending
                self._pending += 1
                self._producer.produce(topic=topic, value=v, key=k, partition=partition if partition is not None else -1, headers=hdrs, callback=self._on_delivery)
                # Poll to trigger delivery callbacks
                self._producer.poll(0)
                self.metrics("produce_attempt", {"topic": topic})
                return
            except ConfluentKafkaException as exc:
                last_exc = exc
                wait = _compute_backoff(attempts, self.cfg.retry_backoff)
                logger.warning("Produce attempt %d failed: %s — retrying after %.2fs", attempts + 1, exc, wait)
                self.metrics("produce_retry", {"attempt": attempts + 1, "error": str(exc)})
                attempts += 1
                time.sleep(wait)
                continue
        raise KafkaError(f"produce failed after {self.cfg.retries} retries: {last_exc!s}")

    def flush(self, timeout: Optional[float] = None) -> None:
        """Block until all pending messages have been delivered or timeout."""
        t = timeout if timeout is not None else self.cfg.request_timeout
        self._producer.flush(int(t * 1000))

    def close(self) -> None:
        """Close producer (flushes pending messages)."""
        try:
            self.flush()
        except Exception:
            logger.exception("Error flushing producer on close")
        # confluent producer has no explicit close beyond flush

    # Context manager support
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ---- Synchronous Consumer (confluent_kafka) ----
class SyncKafkaConsumer:
    """
    Synchronous Kafka consumer wrapper using `confluent_kafka.Consumer`.

    Usage example:

        cfg = KafkaConfig.from_env()
        c = SyncKafkaConsumer(cfg, group_id="omniflow-group", topics=["my-topic"])
        for msg in c:
            process(msg)  # msg is confluent_kafka.Message
        c.close()
    """

    def __init__(
        self,
        cfg: KafkaConfig,
        group_id: str,
        topics: Optional[List[str]] = None,
        auto_offset_reset: str = "earliest",
        enable_auto_commit: bool = False,
        key_deserializer: Optional[Callable[[bytes], Any]] = None,
        value_deserializer: Optional[Callable[[bytes], Any]] = None,
    ):
        if ConfluentConsumer is None:
            raise KafkaError("confluent_kafka is required for SyncKafkaConsumer. Install confluent-kafka-python.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self._key_deserializer = key_deserializer or (lambda b: b.decode("utf-8") if b is not None else None)
        self._value_deserializer = value_deserializer or (lambda b: json.loads(b.decode("utf-8")) if b is not None else None)
        conf = {
            "bootstrap.servers": cfg.bootstrap_servers if isinstance(cfg.bootstrap_servers, str) else ",".join(cfg.bootstrap_servers),
            "group.id": group_id,
            "client.id": cfg.client_id or f"omniflow-sync-consumer-{group_id}",
            "auto.offset.reset": auto_offset_reset,
            "enable.auto.commit": enable_auto_commit,
            "session.timeout.ms": int(cfg.request_timeout * 1000),
        }
        if cfg.security_protocol:
            conf["security.protocol"] = cfg.security_protocol
        if cfg.sasl_mechanism:
            conf["sasl.mechanisms"] = cfg.sasl_mechanism
        if cfg.sasl_plain_username:
            conf["sasl.username"] = cfg.sasl_plain_username
        if cfg.sasl_plain_password:
            conf["sasl.password"] = cfg.sasl_plain_password
        conf.update(cfg.extra or {})
        self._consumer = ConfluentConsumer(conf)
        if topics:
            self._consumer.subscribe(topics)
        self._running = True

    def __iter__(self) -> Generator[Any, None, None]:
        """Iterator yielding deserialized messages as dicts: {key, value, topic, partition, offset, timestamp, raw}"""
        while self._running:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                # handle error
                logger.error("Consumer error: %s", msg.error())
                self.metrics("consumer_error", {"error": str(msg.error())})
                continue
            try:
                key = self._key_deserializer(msg.key())
                value = self._value_deserializer(msg.value())
            except Exception as exc:
                logger.exception("Failed to deserialize message: %s", exc)
                self.metrics("consumer_deserialize_error", {"error": str(exc)})
                # Skip or yield raw message depending on design choice; yield raw for inspection
                yield {"key": None, "value": None, "topic": msg.topic(), "partition": msg.partition(), "offset": msg.offset(), "timestamp": msg.timestamp(), "raw": msg}
                continue
            yield {
                "key": key,
                "value": value,
                "topic": msg.topic(),
                "partition": msg.partition(),
                "offset": msg.offset(),
                "timestamp": msg.timestamp(),
                "raw": msg,
            }

    def commit(self, msg=None, asynchronous: bool = False):
        """Commit offsets; if msg is provided commit that message's offset."""
        try:
            self._consumer.commit(message=msg, asynchronous=asynchronous)
        except Exception as exc:
            logger.exception("Commit failed: %s", exc)

    def close(self):
        """Unsubscribe and close the consumer."""
        try:
            self._running = False
            self._consumer.close()
        except Exception:
            logger.exception("Error closing consumer")

    # Context manager
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ---- Async Producer (aiokafka) ----
class AsyncKafkaProducer:
    """
    Async Kafka producer wrapper using `aiokafka.AIOKafkaProducer`.

    Usage example (async):

        cfg = KafkaConfig.from_env()
        p = AsyncKafkaProducer(cfg)
        await p.start()
        await p.send("topic", key="k", value={"hello":"world"})
        await p.stop()
    """

    def __init__(
        self,
        cfg: KafkaConfig,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        key_serializer: Optional[Callable[[Any], bytes]] = None,
        value_serializer: Optional[Callable[[Any], bytes]] = None,
    ):
        if AIOKafkaProducer is None:
            raise KafkaError("aiokafka is required for AsyncKafkaProducer. Install aiokafka.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self.loop = loop or asyncio.get_event_loop()
        self._key_serializer = key_serializer or (lambda k: k.encode("utf-8") if isinstance(k, str) else _to_json_bytes(k) if k is not None else None)
        self._value_serializer = value_serializer or (lambda v: _to_json_bytes(v) if v is not None else None)
        bootstrap = cfg.bootstrap_servers if isinstance(cfg.bootstrap_servers, (str, list)) else str(cfg.bootstrap_servers)
        # aiokafka expects string or list
        kms = None
        if cfg.sasl_mechanism:
            kms = cfg.sasl_mechanism
        # Build aiokafka producer config
        prod_kwargs = {
            "loop": self.loop,
            "bootstrap_servers": bootstrap,
            "client_id": cfg.client_id or "omniflow-async-producer",
            "request_timeout_ms": int(cfg.request_timeout * 1000),
            "max_block_ms": int(cfg.request_timeout * 1000),
            **(cfg.extra or {}),
        }
        # SASL / security options
        if cfg.security_protocol:
            prod_kwargs["security_protocol"] = cfg.security_protocol
        if cfg.sasl_mechanism:
            prod_kwargs["sasl_mechanism"] = cfg.sasl_mechanism
        if cfg.sasl_plain_username:
            prod_kwargs["sasl_plain_username"] = cfg.sasl_plain_username
        if cfg.sasl_plain_password:
            prod_kwargs["sasl_plain_password"] = cfg.sasl_plain_password
        self._producer = AIOKafkaProducer(**prod_kwargs)
        self._started = False

    async def start(self):
        if not self._started:
            await self._producer.start()
            self._started = True

    async def stop(self):
        if self._started:
            try:
                await self._producer.stop()
            finally:
                self._started = False

    async def send(self, topic: str, key: Any = None, value: Any = None, partition: Optional[int] = None, headers: Optional[Dict[str, str]] = None) -> None:
        k = None if key is None else self._key_serializer(key)
        v = None if value is None else self._value_serializer(value)
        hdrs = None
        if headers:
            # aiokafka expects list of tuples or bytes
            hdrs = [(hk, str(hv).encode("utf-8")) for hk, hv in headers.items()]
        attempts = 0
        last_exc = None
        while attempts <= self.cfg.retries:
            try:
                await self._producer.send_and_wait(topic, value=v, key=k, partition=partition, headers=hdrs)
                self.metrics("async_produce_success", {"topic": topic})
                return
            except AiokafkaError as exc:
                last_exc = exc
                wait = _compute_backoff(attempts, self.cfg.retry_backoff)
                logger.warning("Async produce attempt %d failed: %s — retrying after %.2fs", attempts + 1, exc, wait)
                self.metrics("async_produce_retry", {"attempt": attempts + 1, "error": str(exc)})
                attempts += 1
                await asyncio.sleep(wait)
                continue
        raise KafkaError(f"async produce failed after {self.cfg.retries} retries: {last_exc!s}")

    # Context manager helpers (async)
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()


# ---- Async Consumer (aiokafka) ----
class AsyncKafkaConsumer:
    """
    Async Kafka consumer wrapper using `aiokafka.AIOKafkaConsumer`.

    Usage example (async):

        cfg = KafkaConfig.from_env()
        c = AsyncKafkaConsumer(cfg, group_id="g", topics=["t"])
        await c.start()
        async for record in c:
            process(record)
        await c.stop()
    """

    def __init__(
        self,
        cfg: KafkaConfig,
        group_id: str,
        topics: Optional[List[str]] = None,
        auto_offset_reset: str = "earliest",
        enable_auto_commit: bool = False,
        key_deserializer: Optional[Callable[[bytes], Any]] = None,
        value_deserializer: Optional[Callable[[bytes], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        if AIOKafkaConsumer is None:
            raise KafkaError("aiokafka is required for AsyncKafkaConsumer. Install aiokafka.")
        self.cfg = cfg
        self.metrics = cfg.metrics_hook or _default_metrics_hook
        self.loop = loop or asyncio.get_event_loop()
        self._key_deserializer = key_deserializer or (lambda b: b.decode("utf-8") if b is not None else None)
        self._value_deserializer = value_deserializer or (lambda b: json.loads(b.decode("utf-8")) if b is not None else None)
        bootstrap = cfg.bootstrap_servers
        cons_kwargs = {
            "loop": self.loop,
            "bootstrap_servers": bootstrap,
            "group_id": group_id,
            "client_id": cfg.client_id or f"omniflow-async-consumer-{group_id}",
            "auto_offset_reset": auto_offset_reset,
            "enable_auto_commit": enable_auto_commit,
            "request_timeout_ms": int(cfg.request_timeout * 1000),
            **(cfg.extra or {}),
        }
        if cfg.security_protocol:
            cons_kwargs["security_protocol"] = cfg.security_protocol
        if cfg.sasl_mechanism:
            cons_kwargs["sasl_mechanism"] = cfg.sasl_mechanism
        if cfg.sasl_plain_username:
            cons_kwargs["sasl_plain_username"] = cfg.sasl_plain_username
        if cfg.sasl_plain_password:
            cons_kwargs["sasl_plain_password"] = cfg.sasl_plain_password
        self._consumer = AIOKafkaConsumer(**cons_kwargs)
        self._topics = topics or []
        self._started = False
        self._running = False

    async def start(self):
        if not self._started:
            await self._consumer.start()
            if self._topics:
                await self._consumer.subscribe(self._topics)
            self._started = True
            self._running = True

    async def stop(self):
        if self._started:
            self._running = False
            try:
                await self._consumer.stop()
            finally:
                self._started = False

    def __aiter__(self):
        if not self._started:
            raise KafkaError("consumer not started; call await consumer.start() before iterating")
        return self

    async def __anext__(self):
        if not self._running:
            raise StopAsyncIteration
        try:
            msg = await self._consumer.getone()
        except Exception as exc:
            logger.exception("Error fetching message: %s", exc)
            raise
        try:
            key = self._key_deserializer(msg.key)
            value = self._value_deserializer(msg.value)
        except Exception as exc:
            logger.exception("Failed to deserialize message: %s", exc)
            self.metrics("async_consumer_deserialize_error", {"error": str(exc)})
            return {"key": None, "value": None, "topic": msg.topic, "partition": msg.partition, "offset": msg.offset, "timestamp": msg.timestamp, "raw": msg}
        return {"key": key, "value": value, "topic": msg.topic, "partition": msg.partition, "offset": msg.offset, "timestamp": msg.timestamp, "raw": msg}

    async def commit(self):
        try:
            await self._consumer.commit()
        except Exception as exc:
            logger.exception("Async commit failed: %s", exc)

    # Async context manager support
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()


# ---- Convenience factories / helpers ----
def build_producer_from_env(prefix: str = "KAFKA", async_client: bool = False, **kwargs):
    """
    Convenience: build a Sync or Async producer from environment configuration.
    """
    cfg = default_kafka_config_from_env(prefix)
    if async_client:
        return AsyncKafkaProducer(cfg, **kwargs)
    return SyncKafkaProducer(cfg, **kwargs)


def build_consumer_from_env(prefix: str = "KAFKA", async_client: bool = False, **kwargs):
    """
    Convenience: build a Sync or Async consumer from environment configuration.
    """
    cfg = default_kafka_config_from_env(prefix)
    if async_client:
        return AsyncKafkaConsumer(cfg, **kwargs)
    return SyncKafkaConsumer(cfg, **kwargs)


# ---- Example usage (not executed on import) ----
if __name__ == "__main__":  # pragma: no cover - example only
    logging.basicConfig(level=logging.DEBUG)
    # Example sync producer
    try:
        cfg = KafkaConfig.from_env()
        if ConfluentProducer is not None:
            p = SyncKafkaProducer(cfg)
            p.produce("omniflow-test", key="hello", value={"msg": "hello world"})
            p.flush()
            p.close()
        else:
            logger.info("confluent_kafka not installed; skipping sync producer example")
    except Exception:
        logger.exception("sync producer example failed")

    # Example async producer
    async def async_demo():
        if AIOKafkaProducer is None:
            logger.info("aiokafka not installed; skipping async examples")
            return
        cfg = KafkaConfig.from_env()
        async with AsyncKafkaProducer(cfg) as ap:
            await ap.send("omniflow-test", key="async", value={"msg": "hello async"})
    try:
        asyncio.run(async_demo())
    except Exception:
        logger.exception("async demo failed")
