# plugins/python/tests/test_actions.py
"""
Production-ready pytest suite for OmniFlow Python plugin actions and protocol utilities.

This test file validates:
 - NDJSON single-line parsing and size guards
 - Action handlers: echo, reverse (unicode-safe), compute (sum)
 - Robust handling of malformed JSON and oversized payloads
 - (Optional) lightweight integration smoke test against a local `sample_plugin.py`
   if present at plugins/python/sample_plugin.py

How to run:
    cd <repo-root>/plugins/python
    pytest -q tests/test_actions.py

Notes:
 - Tests will skip integration checks if the sample plugin script is not present.
 - The unit tests will attempt to import the plugin's public API modules; if those
   modules are not available in PYTHONPATH the tests will be skipped with helpful messages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Attempt to import the project's protocol and action implementations.
# If they are not installed / importable, skip unit tests that require them.
protocol = pytest.importorskip(
    "omniflow_plugin.protocol",
    reason="omniflow_plugin.protocol not importable — ensure your plugin package is on PYTHONPATH",
)
actions = pytest.importorskip(
    "omniflow_plugin.actions",
    reason="omniflow_plugin.actions not importable — ensure your plugin package is on PYTHONPATH",
)


# ----------------------
# Unit tests: protocol parsing
# ----------------------
def test_parse_ndjson_line_valid():
    line = '{"id":"r1","type":"health","payload":null}\n'
    req = protocol.parse_ndjson_line(line, max_line=131072)
    assert req["id"] == "r1"
    assert req["type"] == "health"
    assert req.get("payload") is None


def test_parse_ndjson_line_missing_fields():
    line = '{"type":"health"}\n'
    with pytest.raises((ValueError, KeyError)):
        protocol.parse_ndjson_line(line, max_line=8192)


def test_parse_ndjson_line_oversize():
    # Construct a JSON line that exceeds a small max_line
    long_msg = "A" * 2048
    line = json.dumps({"id": "x", "type": "exec", "payload": {"action": "echo", "args": {"message": long_msg}}}) + "\n"
    with pytest.raises(ValueError):
        protocol.parse_ndjson_line(line, max_line=1024)


# ----------------------
# Unit tests: actions
# ----------------------
def test_action_echo():
    args = {"message": "hello"}
    out = actions.action_echo(args)
    assert isinstance(out, dict)
    assert out.get("action") == "echo"
    assert out.get("message") == "hello"


def test_action_reverse_unicode():
    orig = "Привет, 世界! 👋"
    out = actions.action_reverse({"message": orig})
    assert isinstance(out, dict)
    assert out.get("action") == "reverse"
    rev = out.get("message")
    assert isinstance(rev, str) and rev != ""
    # reversing twice should return the original (works for proper rune-based reversal)
    double = actions.action_reverse({"message": rev})["message"]
    # Some implementations reverse by runes/Unicode codepoints, so double reversal should match original
    assert double == orig


def test_action_compute_sum():
    args = {"numbers": [1, 2, 3.5, -1.5]}
    out = actions.action_compute(args)
    assert isinstance(out, dict)
    assert out.get("action") == "compute"
    # Accept ints/floats; normalize to float for comparison
    assert pytest.approx(float(out.get("sum", 0.0)), rel=1e-12) == 10.5


# ----------------------
# Robustness tests
# ----------------------
def test_malformed_json_does_not_crash_parser():
    bad = "{ this is not valid json }\n"
    with pytest.raises((json.JSONDecodeError, ValueError)):
        # parser should raise, but should not crash the process (i.e., it raises controllably)
        protocol.parse_ndjson_line(bad, max_line=4096)


def test_actions_handle_missing_args_gracefully():
    # Calling actions with None / missing fields should not raise unexpected exceptions
    for fn in (actions.action_echo, actions.action_reverse, actions.action_compute):
        try:
            result = fn(None)
        except Exception as exc:
            pytest.fail(f"{fn.__name__} raised unexpected exception with None: {exc}")
        else:
            assert isinstance(result, dict)


def test_large_payload_handling_survives():
    # Build a large message (~200 KiB) and ensure action_echo can accept it in-memory.
    large = "A" * (200 * 1024)
    res = actions.action_echo({"message": large})
    assert res["message"] == large


# ----------------------
# Optional: Lightweight integration test with sample_plugin.py
# ----------------------
SAMPLE_PLUGIN_PATH = Path(__file__).resolve().parents[1] / "sample_plugin.py"


@pytest.mark.skipif(
    not SAMPLE_PLUGIN_PATH.exists(), reason="sample_plugin.py not found; skipping integration smoke tests"
)
def test_integration_sample_plugin_health_and_exec(tmp_path):
    """
    Spawn plugins/python/sample_plugin.py as a subprocess, send NDJSON messages and assert responses.

    This smoke test is intentionally small and uses timeouts to avoid flakiness.
    """
    plugin_cmd = [sys.executable, str(SAMPLE_PLUGIN_PATH)]
    proc = subprocess.Popen(
        plugin_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered
    )

    try:
        assert proc.stdin is not None and proc.stdout is not None

        def send(msg: dict):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

        def recv(timeout: float = 3.0) -> dict:
            deadline = time.time() + timeout
            line = ""
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line:
                    try:
                        return json.loads(line.strip())
                    except json.JSONDecodeError as exc:
                        pytest.fail(f"Plugin emitted invalid JSON line: {line!r} ({exc})")
                time.sleep(0.01)
            pytest.fail("Timeout waiting for plugin response")

        # Health probe
        hid = "py-int-health-1"
        send({"id": hid, "type": "health", "payload": None})
        resp = recv(3.0)
        assert resp.get("id") == hid
        assert resp.get("status") in ("ok", "healthy") or (resp.get("body", {}).get("status") == "healthy")

        # Exec echo
        eid = "py-int-echo-1"
        send({"id": eid, "type": "exec", "payload": {"action": "echo", "args": {"message": "hello"}}})
        resp = recv(3.0)
        assert resp.get("id") == eid
        assert resp.get("status") == "ok"
        assert resp.get("body", {}).get("action") == "echo"
        assert resp.get("body", {}).get("message") == "hello"

        # Shutdown
        sid = "py-int-shutdown-1"
        send({"id": sid, "type": "shutdown", "payload": None})
        # plugin may or may not emit a shutdown response; wait briefly and ensure process exits
        time.sleep(0.5)
        proc.poll()
    finally:
        # ensure termination
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()


# EOF
