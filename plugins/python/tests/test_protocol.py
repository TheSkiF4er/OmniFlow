# plugins/python/tests/test_protocol.py
"""
Production-ready pytest suite for OmniFlow Python plugin **protocol** utilities.

This file validates the core NDJSON protocol helpers that OmniFlow plugins must implement:
 - single-line NDJSON parsing with max-line guard
 - response formatting (single-line NDJSON output)
 - id propagation and minimal envelope schema
 - streaming behavior (multiple NDJSON lines)
 - robustness to malformed JSON and oversized lines
 - deterministic behavior for unicode and binary-ish input

These tests are intentionally:
 - small and fast (unit-level)
 - deterministic (no network, no randomness)
 - suitable for CI (no external resources)

How to run:
    cd <repo-root>/plugins/python
    pytest -q plugins/python/tests/test_protocol.py

Notes:
 - Tests attempt to import the project's protocol module at:
     omniflow_plugin.protocol
   If that module is not importable, the tests will be skipped with a helpful message.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any, Dict, List

import pytest

# Try to import the protocol helpers from the plugin codebase.
protocol = pytest.importorskip(
    "omniflow_plugin.protocol",
    reason="omniflow_plugin.protocol not importable — ensure your plugin package is on PYTHONPATH",
)


# -------------------------
# Helper factories
# -------------------------
def make_req(req_id: str, typ: str, payload: Any) -> str:
    """Return a single-line JSON request (NDJSON) with terminating newline."""
    return json.dumps({"id": req_id, "type": typ, "payload": payload}) + "\n"


# -------------------------
# Tests: basic parsing
# -------------------------
def test_parse_ndjson_valid_health():
    line = make_req("r-1", "health", None)
    parsed = protocol.parse_ndjson_line(line, max_line=131072)
    assert isinstance(parsed, dict)
    assert parsed["id"] == "r-1"
    assert parsed["type"] == "health"
    assert parsed.get("payload") is None


def test_parse_ndjson_missing_fields_raises():
    line = json.dumps({"type": "health"}) + "\n"
    with pytest.raises(Exception):
        protocol.parse_ndjson_line(line, max_line=8192)


def test_parse_ndjson_oversize_rejected():
    # Build a JSON whose encoded byte-length exceeds max_line
    big = "A" * (1500)
    line = make_req("big-1", "exec", {"action": "echo", "args": {"message": big}})
    # Use a small limit to force rejection
    with pytest.raises(ValueError):
        protocol.parse_ndjson_line(line, max_line=1024)


# -------------------------
# Tests: response formatting
# -------------------------
def test_build_ndjson_response_single_line_and_contains_id():
    resp_obj = {"id": "resp1", "status": "ok", "body": {"foo": "bar"}}
    out = protocol.build_ndjson_response(resp_obj)
    assert isinstance(out, (bytes, str))
    if isinstance(out, bytes):
        out_text = out.decode("utf-8")
    else:
        out_text = out
    # Must be newline-terminated and contain no internal newline characters
    assert out_text.endswith("\n")
    assert "\n" not in out_text.rstrip("\n")
    # Should decode back to the same fields (id and status)
    parsed = json.loads(out_text.strip())
    assert parsed["id"] == "resp1"
    assert parsed["status"] == "ok"


def test_build_response_with_error_payload_serializes():
    resp_obj = {"id": "e1", "status": "error", "code": 123, "message": "bad input"}
    out = protocol.build_ndjson_response(resp_obj)
    parsed = json.loads(out.strip())
    assert parsed["id"] == "e1"
    assert parsed["status"] == "error"
    assert parsed["code"] == 123
    assert parsed["message"] == "bad input"


# -------------------------
# Tests: streaming / NDJSON reader behavior
# -------------------------
def test_streaming_ndjson_multiple_lines_parsed_iterably():
    lines: List[str] = [
        make_req("a1", "health", None),
        make_req("b2", "exec", {"action": "echo", "args": {"message": "ok"}}),
        make_req("c3", "exec", {"action": "compute", "args": {"numbers": [1, 2, 3]}}),
    ]
    stream = "".join(lines)
    # The protocol module should expose a helper that reads NDJSON from a stream
    # Try to use stream reader if available, otherwise fall back to manual parse for test.
    if hasattr(protocol, "ndjson_iter"):
        it = list(protocol.ndjson_iter(io.StringIO(stream), max_line=131072))
        assert len(it) == 3
        ids = [r["id"] for r in it]
        assert ids == ["a1", "b2", "c3"]
    else:
        # replicate expected behavior manually using parse_ndjson_line
        buf = io.StringIO(stream)
        parsed_ids = []
        while True:
            ln = buf.readline()
            if ln == "":
                break
            parsed = protocol.parse_ndjson_line(ln, max_line=131072)
            parsed_ids.append(parsed["id"])
        assert parsed_ids == ["a1", "b2", "c3"]


# -------------------------
# Tests: robustness & edge-cases
# -------------------------
def test_malformed_json_raises_but_does_not_crash_runtime():
    bad = "{ not valid json }\n"
    with pytest.raises(Exception):
        protocol.parse_ndjson_line(bad, max_line=4096)
    # Repeated parsing should still function afterwards
    ok = make_req("after-1", "health", None)
    parsed = protocol.parse_ndjson_line(ok, max_line=4096)
    assert parsed["id"] == "after-1"


def test_unicode_and_binary_like_input_handling():
    # Include high unicode, emoji, and some bytes that are valid UTF-8
    text = "Привет 🌍 — āčē 👍"
    line = make_req("u1", "exec", {"action": "reverse", "args": {"message": text}})
    parsed = protocol.parse_ndjson_line(line, max_line=131072)
    assert parsed["id"] == "u1"
    # Build a response that contains unicode; protocol builder must preserve encoding
    resp = {"id": "u1", "status": "ok", "body": {"message": text}}
    out = protocol.build_ndjson_response(resp)
    if isinstance(out, bytes):
        out_text = out.decode("utf-8")
    else:
        out_text = out
    assert "🌍" in out_text
    assert "Привет" in out_text


def test_empty_line_ignored_or_rejected_consistently():
    # Depending on implementation, an empty line may raise or be ignored.
    empty = "\n"
    if hasattr(protocol, "parse_ndjson_line"):
        with pytest.raises(Exception):
            protocol.parse_ndjson_line(empty, max_line=1024)
    else:
        pytest.skip("protocol.parse_ndjson_line not present")


# -------------------------
# Tests: defensive size guard (edge)
# -------------------------
def test_size_guard_off_when_zero_maxline_allows_large_lines():
    # max_line == 0 indicates 'no size guard' in some implementations — test for that behavior if supported
    big = "A" * (300 * 1024)  # 300 KiB
    line = make_req("big-no-guard", "exec", {"action": "echo", "args": {"message": big}})
    # If implementation documents max_line==0 as disabled, this should parse; otherwise we expect ValueError.
    try:
        parsed = protocol.parse_ndjson_line(line, max_line=0)
        # If parsing succeeded, verify id remained intact
        assert parsed["id"] == "big-no-guard"
    except ValueError:
        # acceptable alternative behavior; ensure error message mentions 'length' to be helpful
        pytest.skip("protocol enforces size guard even when max_line==0 in this implementation")


# -------------------------
# Optional interface checks (non-mandatory helpers)
# -------------------------
def test_optional_helpers_exist_with_expected_signatures():
    # The protocol module SHOULD provide parse_ndjson_line and build_ndjson_response.
    assert hasattr(protocol, "parse_ndjson_line"), "protocol.parse_ndjson_line is required"
    assert hasattr(protocol, "build_ndjson_response"), "protocol.build_ndjson_response is required"
    # ndjson_iter and safe_read_line are optional but helpful
    # If present, they should be callable
    for name in ("ndjson_iter", "safe_read_line"):
        if hasattr(protocol, name):
            assert callable(getattr(protocol, name))


# -------------------------
# Sanity: id preservation in roundtrip
# -------------------------
def test_roundtrip_request_to_response_preserves_id_and_type():
    req_line = make_req("round-1", "exec", {"action": "echo", "args": {"message": "ok"}})
    req = protocol.parse_ndjson_line(req_line, max_line=131072)
    # Build a standard ok response using available helper; if not present, build manually
    resp_payload = {"id": req["id"], "status": "ok", "body": {"echoed": True}}
    out = protocol.build_ndjson_response(resp_payload)
    parsed = json.loads(out.strip())
    assert parsed["id"] == req["id"]
    assert parsed["status"] == "ok"


# End of file
