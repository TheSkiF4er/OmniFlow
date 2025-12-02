# OmniFlow Plugin Protocol — `plugins/common/protocol.md`

> Production-ready protocol specification for communication between the OmniFlow host (supervisor) and plugin processes.
> This document is the canonical, implementation-oriented reference describing framing, message shapes, lifecycle, error codes, timeouts, observability, and security constraints. Keep this file in `plugins/common/protocol.md` and update on any breaking change.

---

## Table of contents

* [Purpose & scope](#purpose--scope)
* [Design principles](#design-principles)
* [Transport & framing](#transport--framing)
* [Encoding & character set](#encoding--character-set)
* [Top-level message contract](#top-level-message-contract)

  * [Host → Plugin: Request structure](#host--plugin-request-structure)
  * [Plugin → Host: Response structure](#plugin--host-response-structure)
* [Standard request types & payloads](#standard-request-types--payloads)

  * [`health`](#health)
  * [`exec`](#exec)
  * [`shutdown`](#shutdown)
  * `meta`, `config`, and vendor extensions
* [Response semantics, status codes and error taxonomy](#response-semantics-status-codes-and-error-taxonomy)
* [Behavioral rules & lifecycle](#behavioral-rules--lifecycle)

  * [Startup & readiness](#startup--readiness)
  * [Request processing model (sync vs async)](#request-processing-model-sync-vs-async)
  * [Cancellation and timeouts](#cancellation-and-timeouts)
  * [Graceful shutdown & signals](#graceful-shutdown--signals)
* [Framing, buffering & backpressure](#framing-buffering--backpressure)
* [Logging, diagnostics & observability](#logging-diagnostics--observability)
* [Security & safe-by-default constraints](#security--safe-by-default-constraints)
* [Testing recommendations & fuzzing](#testing-recommendations--fuzzing)
* [Sample JSON schemas & examples](#sample-json-schemas--examples)
* [Checklist for implementers](#checklist-for-implementers)
* [Change log & versioning](#change-log--versioning)

---

## Purpose & scope

This protocol defines a stable, minimal and interoperable NDJSON (newline-delimited JSON) messaging contract used by OmniFlow to embed language-native plugins. It is deliberately small so it can be implemented easily in any language and run inside containers, processes or restricted sandboxes.

This document is implementation-focused — if you need high-level policy or integration examples, see `plugins/common/plugin-api.md` and example files in `plugins/common/examples/`.

---

## Design principles

1. **Single-line JSON messages** — one JSON object per line (no multi-line JSON). This simplifies framing and robust parsing across languages and stream boundaries.
2. **Idempotent correlation** — every request has an `id` that MUST be echoed in exactly one response.
3. **Deterministic error semantics** — consistent status codes and error categories so hosts can react programmatically.
4. **Security-first** — maximum length limits, no network by default, least privileges.
5. **Language-agnostic** — designed to be trivial to implement in C, Go, Python, Node, Java, etc.

---

## Transport & framing

* Transport: plugin process `stdin` (host → plugin) and plugin `stdout` (plugin → host). Optional additional channels (unix sockets, TCP) are allowed but not covered here.
* Framing: Each message is a single JSON object encoded in UTF-8, followed by a single newline character `\n`. Do **not** send binary data or multi-line JSON objects on stdout; logs belong on stderr.
* In production hosts often multiplex multiple plugins; each plugin gets its own process/pipe.

---

## Encoding & character set

* Use **UTF-8** for all text.
* Strings inside JSON must be valid Unicode (escaped as necessary). Plugins should support Unicode escapes `\uXXXX` at least for BMP characters. Prefer full Unicode handling when possible.

---

## Top-level message contract

### Host → Plugin: Request structure

All requests MUST be JSON objects with this minimal shape:

```json
{
  "id": "string",              // REQUIRED, opaque to plugin (recommend UUID)
  "type": "string",            // REQUIRED, e.g. "health", "exec", "shutdown", "meta"
  "timestamp": "string",       // OPTIONAL, ISO-8601 UTC (host may include)
  "payload": { ... } | null    // OPTIONAL, request-specific data
}
```

Rules:

* `id` must be unique for the request and used to correlate the response.
* `type` defines the semantics; unknown types should be rejected with an error response (see errors).
* `payload` may be omitted or `null` when not needed.

### Plugin → Host: Response structure

Every response MUST be a single JSON object with at least:

```json
{
  "id": "string",              // MUST match request id
  "status": "string",          // "ok" | "error" | "busy"
  "code": 0,                   // integer code (0 for success), optional but recommended
  "message": "string|null",    // human message, optional
  "body": { ... } | null,      // action-specific result
  "meta": { ... } | null       // optional diagnostic metadata (timings, memory)
}
```

Rules:

* Exactly one response MUST be emitted for each request `id`. Duplicate responses are a protocol violation.
* Responses must be emitted to stdout (not stderr). stderr is reserved for logs/diagnostics.
* `status` indicates outcome; `code` is machine-parseable and follows the taxonomy below.

---

## Standard request types & payloads

Implementations must at least support `health`, `exec`, and `shutdown`. Other types (`meta`, `config`, custom vendor types) are allowed but should be documented by plugin authors.

### `health`

**Request**

```json
{ "id":"hc-1", "type":"health", "payload": null }
```

**Response**

```json
{
  "id":"hc-1",
  "status":"ok",
  "code":0,
  "body":{
    "status":"healthy",
    "version":"1.2.3",
    "uptime_seconds": 42
  }
}
```

Semantics: fast liveness/readiness check. Plugins should answer quickly (within seconds).

### `exec`

**Request**

```json
{
  "id":"exec-1",
  "type":"exec",
  "payload":{
    "action":"string",        // e.g. "echo", "reverse", "compute"
    "args": { ... }           // action-specific args
  }
}
```

**Response (success)**

```json
{
  "id":"exec-1",
  "status":"ok",
  "code":0,
  "body": { "action":"echo", "message":"hello" }
}
```

Semantics: an arbitrary action. Hosts set an execution timeout (`OMNIFLOW_EXEC_TIMEOUT`, default 10s). Plugin must enforce/collaborate with cancellation.

### `shutdown`

**Request**

```json
{ "id":"shutdown-1", "type":"shutdown", "payload":null }
```

**Response**

```json
{ "id":"shutdown-1", "status":"ok", "code":0, "body": { "result":"shutting_down" } }
```

Semantics: plugin should finish/abort in-flight requests gracefully and exit with status 0 after acknowledging.

### `meta` / vendor extensions

* `meta` used to query plugin capabilities, configuration, or diagnostics. Shape is plugin-defined.
* Plugins must ignore unknown optional fields and should validate required fields.

---

## Response semantics, status codes and error taxonomy

`status` values:

* `ok` — operation succeeded. `code: 0` recommended.
* `error` — operation failed. `code` non-zero gives category.
* `busy` — plugin cannot process now; host may retry later.

Recommended `code` ranges (informational; choose exact numeric scheme in your plugin):

* `0` — success
* `1xx` — Input/Validation errors

  * `100` — malformed JSON
  * `101` — payload too large (exceeds `OMNIFLOW_PLUGIN_MAX_LINE`)
  * `102` — missing required field
* `2xx` — Action/Domain errors

  * `200` — unsupported action
  * `201` — invalid action args
* `3xx` — Runtime/resource errors

  * `300` — resource exhausted (memory/disk)
  * `301` — internal timeout
* `4xx` — Internal/unexpected failures

  * `400` — internal exception

Plugins SHOULD return a helpful `message` and may include `meta` with more diagnostics (`processing_time_ms`, `memory_bytes`, etc.).

Hosts MUST treat `error` statuses as recoverable/localized unless the plugin repeatedly returns `error` causing host operator action.

---

## Behavioral rules & lifecycle

### Startup & readiness

* Plugin MAY print a startup banner to `stderr` for humans (e.g., `OmniFlow C Plugin v1.2.3 starting (pid:123)`), but MUST NOT write JSON responses to stderr.
* No special handshake message required. Hosts typically send a `health` request after launch to confirm readiness.

### Request processing model (sync vs async)

* Sync model: plugin processes a request and writes a response before reading the next request.
* Async model: plugin starts processing and may handle multiple requests concurrently using worker threads/tasks — still MUST emit exactly one final response per request `id`.
* If a plugin accepts async model, it must associate responses with `id` and should not reorder responses in a way that confuses host expectations (host must match by `id`).

### Cancellation and timeouts

* Host sets `OMNIFLOW_EXEC_TIMEOUT` (env var). Plugin MUST:

  * enforce per-request timeouts (cooperative cancellation) where possible; or
  * early-check periodically for elapsed time and abort work
  * If unable to stop in time, host may kill the process.
* For long-running actions, plugin may respond `status: busy` and allow host to requeue or retry.

### Graceful shutdown & signals

* On `shutdown` request: plugin must respond (ack) and exit with code 0 after finishing/aborting in-flight work within `stop_timeout_seconds` (host-side config; default 5s).
* On `SIGTERM`/`SIGINT`, plugin should behave the same as on `shutdown` — try graceful exit, otherwise terminate.
* On `SIGKILL` host will force-terminate; plugin cannot handle this.

---

## Framing, buffering & backpressure

* Hosts must not send more than one very large request at once; they should await responses or use limited concurrency.
* Plugins must read from stdin with a line-based reader and enforce a maximum single-line size (`OMNIFLOW_PLUGIN_MAX_LINE`, default 131072). If an incoming line exceeds the limit, plugin must:

  * reject it with `status: "error", code: 101` OR
  * close the connection/exit if it's a policy violation.
* Plugin stdout should be line-buffered (flush after writing) to avoid host-side delays. Use `fflush(stdout)` or equivalent.

---

## Logging, diagnostics & observability

* **Stdout**: reserved for responses only (single-line JSON per request).
* **Stderr**: free-form logs; prefer structured JSON lines when `OMNIFLOW_LOG_JSON=true`. Example structured log line:

  ```json
  {"ts":"2025-12-02T00:00:00Z","level":"info","msg":"handling exec","id":"exec-1","action":"echo"}
  ```
* **Meta**: responses MAY include `meta` object with `processing_time_ms`, `heap_bytes`, `thread_count`.
* **Metrics**: plugins SHOULD emit operational metrics to stderr (JSON) or expose an optional metrics endpoint when allowed.
* **Tracing**: accept optional `payload.meta.trace` or `payload.meta.trace_id` to propagate trace context.

---

## Security & safe-by-default constraints

* **Max-line enforcement:** plugins MUST reject or safely handle input lines larger than `OMNIFLOW_PLUGIN_MAX_LINE` (env default `131072`).
* **No arbitrary network access by default:** host should sandbox plugins; plugins should not open network servers by default.
* **Least privilege:** run plugins as non-root, drop capabilities, apply seccomp/AppArmor profiles when possible.
* **Secrets:** never commit secrets into repo. Provide secrets securely via host secret manager or mounted files with restrictive permissions.
* **Dependencies:** vendor or pin dependencies. Provide SBOM files and sign release artifacts (cosign/PGP).
* **Memory safety:** for native languages, use sanitizers in CI (ASan/UBSan) and perform regular fuzzing on the parser.

---

## Testing recommendations & fuzzing

* Include an integration test harness that:

  * starts plugin binary,
  * writes NDJSON requests to stdin (or FIFO),
  * reads stdout and verifies JSON responses using `jq` or language-native JSON parsers,
  * asserts plugin survives malformed input, oversized lines and shutdowns.
* Use AddressSanitizer in CI and fail builds on violations.
* Add fuzz tests targeting the JSON parser (libFuzzer, AFL). Focus on malformed escape sequences, extremely long strings, and boundary numeric formats.

---

## Sample JSON schemas & examples

**Minimal request schema**

```json
{
  "type": "object",
  "required": ["id","type"],
  "properties": {
    "id": {"type":"string"},
    "type": {"type":"string"},
    "timestamp": {"type":"string","format":"date-time"},
    "payload": {}
  },
  "additionalProperties": false
}
```

**Minimal response schema**

```json
{
  "type": "object",
  "required": ["id","status"],
  "properties": {
    "id": {"type":"string"},
    "status": {"type":"string","enum":["ok","error","busy"]},
    "code": {"type":"integer"},
    "message": {"type":"string"},
    "body": {},
    "meta": {}
  },
  "additionalProperties": false
}
```

**Typical `exec` Example**
Host → Plugin:

```json
{"id":"exec-42","type":"exec","payload":{"action":"compute","args":{"numbers":[1,2,3.5]}}}
```

Plugin → Host:

```json
{"id":"exec-42","status":"ok","code":0,"body":{"action":"compute","sum":6.5},"meta":{"processing_time_ms":12}}
```

---

## Checklist for implementers

* [ ] Read and implement `stdin` line-based reader with `OMNIFLOW_PLUGIN_MAX_LINE` guard.
* [ ] Ensure `stdout` writes one JSON object per line and flushes output.
* [ ] Echo request `id` exactly once in a response for each request.
* [ ] Handle `health` and `shutdown` in a predictable manner.
* [ ] Implement `OMNIFLOW_EXEC_TIMEOUT` cooperative cancellation behavior.
* [ ] Emit logs to `stderr`; support `OMNIFLOW_LOG_JSON` for structured logs.
* [ ] Run ASan and fuzzing in CI before release.
* [ ] Produce SBOM and sign release artifacts per release policy.

---

## Change log & versioning

* This document is protocol **version 1.0**.
* Any breaking change (remove fields, change semantics of `id` or framing) MUST increment the major protocol version and be accompanied by migration documentation and compatibility tests.
* Minor/ additive changes (optional fields, additional `meta`) are backward-compatible.

---

## Where to put this in code

* Reference implementation examples live in language-specific plugin templates: `plugins/templates/language-*`.
* Integration tests available: `plugins/c/tests/test_sample_plugin.sh` (example harness).
* Release & packaging rules live under `plugins/cpp-packages/releases/`.
