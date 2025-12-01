#!/usr/bin/env python3
"""
sample_plugin.py

Production-ready Python plugin for OmniFlow (TheSkiF4er/OmniFlow)
License: Apache-2.0

Features:
 - Communicates with host via newline-delimited JSON messages on stdin/stdout.
 - Uses only stdlib (json, asyncio) to avoid extra dependencies; optionally supports orjson if installed.
 - Structured logging to stderr (plain text or JSON via OMNIFLOW_LOG_JSON).
 - Graceful shutdown on SIGINT/SIGTERM or "shutdown" message.
 - Background heartbeat worker and safe timeouts for exec handlers.
 - Configurable via environment variables.
 - Enforces a maximum incoming message size to mitigate DoS.
 - Implements safe built-in actions: echo, reverse, compute (sum).

Runtime contract (newline-delimited JSON):
 Host -> Plugin:
   { "id": "<uuid>", "type": "exec|health|shutdown", "payload": {...} }

 Plugin -> Host responses (newline-delimited JSON):
   { "id": "<uuid>", "status": "ok|error", "code"?:int, "message"?:string, "body"?:object }

Environment variables:
 - OMNIFLOW_PLUGIN_MAX_LINE (bytes, default 131072)
 - OMNIFLOW_PLUGIN_HEARTBEAT (seconds, default 5)
 - OMNIFLOW_LOG_JSON (if set -> JSON logs)
 - OMNIFLOW_EXEC_TIMEOUT (seconds, default 10)
 - OMNIFLOW_PLUGIN_DEBUG (if set -> debug logs)

Usage:
  echo '{"id":"1","type":"health"}' | ./sample_plugin.py

Notes:
 - This file is designed to be production-ready but you should adapt timeouts,
   resource limits and allowed actions to your security policy.
 - For rigorous parsing or higher performance consider enabling orjson if available.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Metadata
PLUGIN_NAME = "OmniFlowPyRelease"
PLUGIN_VERSION = "1.0.0"

# Configuration from environment
MAX_LINE = int(os.getenv("OMNIFLOW_PLUGIN_MAX_LINE", "131072"))  # bytes
HEARTBEAT = int(os.getenv("OMNIFLOW_PLUGIN_HEARTBEAT", "5"))
LOG_JSON = bool(os.getenv("OMNIFLOW_LOG_JSON"))
EXEC_TIMEOUT = int(os.getenv("OMNIFLOW_EXEC_TIMEOUT", "10"))
DEBUG = bool(os.getenv("OMNIFLOW_PLUGIN_DEBUG"))

# Use orjson if available for speed; fall back to stdlib json
try:
    import orjson as _orjson  # type: ignore

    def loads(s: bytes) -> Any:
        return _orjson.loads(s)

    def dumps(obj: Any) -> bytes:
        return _orjson.dumps(obj)

    JSON_LIB = "orjson"
except Exception:
    _json = json

    def loads(s: bytes) -> Any:
        return _json.loads(s.decode("utf-8"))

    def dumps(obj: Any) -> bytes:
        return _json.dumps(obj, separators=(",", ":")).encode("utf-8")

    JSON_LIB = "json"

# Runtime control
RUNNING = True
SHUTDOWN_REQUESTED = False

# Async queues
LINE_QUEUE: asyncio.Queue[str] = asyncio.Queue()

# Logging helpers

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(level: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
    if LOG_JSON:
        rec = {"time": _now_iso(), "level": level, "plugin": PLUGIN_NAME, "message": message}
        if extra:
            rec["extra"] = extra
        sys.stderr.write(dumps(rec).decode("utf-8") + "\n")
    else:
        sys.stderr.write(f"{_now_iso()} [{level}] {PLUGIN_NAME}: {message}\n")
    sys.stderr.flush()


def info(msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
    log("INFO", msg, extra)


def warn(msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
    log("WARN", msg, extra)


def error_log(msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
    log("ERROR", msg, extra)

# Response helpers

def respond(obj: Dict[str, Any]) -> None:
    try:
        sys.stdout.write(dumps(obj).decode("utf-8") + "\n")
        sys.stdout.flush()
    except Exception as e:
        error_log(f"failed to serialize response: {e}")


def respond_ok(id: Optional[str], body: Optional[Dict[str, Any]] = None) -> None:
    r: Dict[str, Any] = {"status": "ok"}
    if id is not None:
        r["id"] = id
    if body is not None:
        r["body"] = body
    respond(r)


def respond_error(id: Optional[str], code: int, message: str) -> None:
    r: Dict[str, Any] = {"status": "error", "code": code, "message": message}
    if id is not None:
        r["id"] = id
    respond(r)


# Built-in action handlers
async def action_echo(payload: Any) -> Dict[str, Any]:
    message = ""
    if isinstance(payload, dict):
        m = payload.get("message")
        if isinstance(m, str):
            message = m
    return {"action": "echo", "message": message}


async def action_reverse(payload: Any) -> Dict[str, Any]:
    message = ""
    if isinstance(payload, dict):
        m = payload.get("message")
        if isinstance(m, str):
            message = m
    # Unicode-safe reversal
    rev = "".join(reversed(list(message)))
    return {"action": "reverse", "message": rev}


async def action_compute(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("missing or invalid payload")
    nums = payload.get("numbers")
    if not isinstance(nums, list):
        raise ValueError("missing or invalid 'numbers' array")
    total = 0.0
    for n in nums:
        if not isinstance(n, (int, float)):
            raise ValueError("numbers must be numeric")
        total += float(n)
    return {"action": "compute", "sum": total}


# Dispatcher for exec
async def handle_exec_message(id: Optional[str], payload: Any) -> None:
    if not isinstance(payload, dict):
        respond_error(id, 400, "missing or invalid payload")
        return
    action = payload.get("action")
    if not isinstance(action, str):
        respond_error(id, 400, "missing or invalid 'action'")
        return

    try:
        if action == "echo":
            result = await asyncio.wait_for(action_echo(payload), timeout=EXEC_TIMEOUT)
            respond_ok(id, result)
        elif action == "reverse":
            result = await asyncio.wait_for(action_reverse(payload), timeout=EXEC_TIMEOUT)
            respond_ok(id, result)
        elif action == "compute":
            result = await asyncio.wait_for(action_compute(payload), timeout=EXEC_TIMEOUT)
            respond_ok(id, result)
        else:
            respond_error(id, 422, "unsupported action")
    except asyncio.TimeoutError:
        respond_error(id, 408, "exec timeout")
    except ValueError as ve:
        respond_error(id, 400, str(ve))
    except Exception as e:
        error_log(f"exec handler unexpected error: {e}")
        respond_error(id, 500, "internal error")


# Health handler
async def handle_health_message(id: Optional[str]) -> None:
    respond_ok(id, {"status": "healthy", "version": PLUGIN_VERSION})


# Main line processor
async def process_line(line: str) -> None:
    if not line:
        return
    try:
        # Accept bytes-safe by encoding
        obj = loads(line.encode("utf-8"))
    except Exception:
        warn("invalid JSON message")
        respond_error(None, 400, "invalid JSON")
        return

    if not isinstance(obj, dict):
        respond_error(None, 400, "invalid JSON message shape")
        return

    id_val = obj.get("id")
    id_str = id_val if isinstance(id_val, str) else None
    t = obj.get("type")
    if not isinstance(t, str):
        respond_error(id_str, 400, "missing 'type'")
        return
    t = t.lower()

    payload = obj.get("payload")

    if t == "health":
        await handle_health_message(id_str)
    elif t == "exec":
        await handle_exec_message(id_str, payload)
    elif t in ("shutdown", "quit"):
        respond_ok(id_str, {"result": "shutting_down"})
        global SHUTDOWN_REQUESTED, RUNNING
        SHUTDOWN_REQUESTED = True
        RUNNING = False
    else:
        respond_error(id_str, 400, "unknown type")


# Background heartbeat
async def heartbeat_worker() -> None:
    info(f"background worker started (heartbeat={HEARTBEAT})")
    counter = 0
    while RUNNING:
        await asyncio.sleep(HEARTBEAT)
        if not RUNNING:
            break
        counter += 1
        info(f"heartbeat {counter}")
    info("background worker stopping")


# Reader: non-blocking reading of stdin lines with size limits
async def stdin_reader() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while RUNNING:
        try:
            # Read until newline but no more than MAX_LINE bytes
            line_bytes = await reader.readline()
            if not line_bytes:
                # EOF
                info("stdin closed (EOF)")
                global RUNNING
                RUNNING = False
                break

            if len(line_bytes) > MAX_LINE:
                warn("incoming message exceeds MAX_LINE, rejecting")
                respond_error(None, 413, "payload too large")
                # Drain the rest of this line if needed — readline already gives full line until \n
                continue

            # decode safely
            try:
                line = line_bytes.decode("utf-8").rstrip("\r\n")
            except Exception:
                warn("invalid encoding in input"); respond_error(None, 400, "invalid input encoding"); continue

            await LINE_QUEUE.put(line)
        except Exception as e:
            error_log(f"stdin_reader error: {e}")
            await asyncio.sleep(0.1)


# Main processor that consumes LINE_QUEUE
async def main_processor() -> None:
    while RUNNING or not LINE_QUEUE.empty():
        try:
            line = await asyncio.wait_for(LINE_QUEUE.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        try:
            await process_line(line)
        except Exception as e:
            error_log(f"processor unexpected error: {e}")


# Signal handlers to set RUNNING flag false
def _signal_handler(signame: str) -> None:
    info(f"received signal {signame}, initiating shutdown")
    global RUNNING, SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True
    RUNNING = False


def setup_signals() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    signals = (signal.SIGINT, signal.SIGTERM)
    for s in signals:
        try:
            signal.signal(s, lambda sig, frame, s=s: _signal_handler(s.name))
        except Exception:
            # In some environments (Windows older Python) signal handling may be restricted
            pass


# Entrypoint
async def main() -> None:
    info(f"starting plugin version={PLUGIN_VERSION} max_line={MAX_LINE} heartbeat={HEARTBEAT} exec_timeout={EXEC_TIMEOUT} json={JSON_LIB}")
    setup_signals()

    hb_task = asyncio.create_task(heartbeat_worker())
    reader_task = asyncio.create_task(stdin_reader())
    processor_task = asyncio.create_task(main_processor())

    # Wait until RUNNING becomes False
    while RUNNING:
        await asyncio.sleep(0.2)

    # allow remaining queue processing and graceful shutdown
    await asyncio.sleep(0.05)

    # cancel reader if still running to allow program to exit
    if not reader_task.done():
        reader_task.cancel()
        try:
            await reader_task
        except Exception:
            pass

    # wait processor to finish outstanding messages
    try:
        await asyncio.wait_for(processor_task, timeout=1.0)
    except asyncio.TimeoutError:
        warn("processor did not finish within timeout, forcing shutdown")

    # stop heartbeat
    if not hb_task.done():
        hb_task.cancel()
        try:
            await hb_task
        except Exception:
            pass

    info("plugin shutdown complete")


if __name__ == "__main__":
    # Run the main event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        info("keyboard interrupt, exiting")
        sys.exit(0)
