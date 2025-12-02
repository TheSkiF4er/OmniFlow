# OmniFlow Plugin API — `plugins/common/plugin-api.md`

> Production-ready specification for plugin authors and integrators.
> Defines the unary newline-delimited JSON protocol used between the OmniFlow host and plugins, lifecycle and runtime expectations, security constraints, logs, testing guidance, and release requirements.

---

## Table of contents

* [Purpose](#purpose)
* [Design goals](#design-goals)
* [High-level contract](#high-level-contract)
* [Transport and encoding](#transport-and-encoding)
* [Message schema (host → plugin)](#message-schema-host--plugin)

  * [Common fields](#common-fields)
  * [Request types](#request-types)

    * [Health request](#health-request)
    * [Exec request](#exec-request)
    * [Shutdown request](#shutdown-request)
    * [Meta / Custom requests](#meta--custom-requests)
* [Message schema (plugin → host)](#message-schema-plugin--host)

  * [Common response fields](#common-response-fields)
  * [Standard statuses and codes](#standard-statuses-and-codes)
* [Examples](#examples)

  * [Health request / response](#health-request--response)
  * [Exec (echo) request / response](#exec-echo-request--response)
  * [Exec (compute) request / response](#exec-compute-request--response)
  * [Shutdown request / response](#shutdown-request--response)
* [Behavioral requirements](#behavioral-requirements)

  * [Startup and handshake](#startup-and-handshake)
  * [Synchronous vs asynchronous handling](#synchronous-vs-asynchronous-handling)
  * [Timeouts and cancellation](#timeouts-and-cancellation)
  * [Resource usage and quotas](#resource-usage-and-quotas)
  * [Error handling and validation](#error-handling-and-validation)
  * [Graceful shutdown](#graceful-shutdown)
* [Security requirements](#security-requirements)

  * [Input size and DoS protections](#input-size-and-dos-protections)
  * [Sandboxing and least privilege](#sandboxing-and-least-privilege)
  * [Secrets handling](#secrets-handling)
  * [Dependency and supply-chain hygiene](#dependency-and-supply-chain-hygiene)
* [Logging & observability](#logging--observability)

  * [Structured logs](#structured-logs)
  * [Metrics](#metrics)
  * [Tracing](#tracing)
* [Testing & CI guidance](#testing--ci-guidance)
* [Release & packaging requirements](#release--packaging-requirements)
* [Versioning and backward compatibility](#versioning-and-backward-compatibility)
* [Appendices](#appendices)

  * [JSON Schema (draft-like) — requests](#json-schema-draft-like---requests)
  * [JSON Schema (draft-like) — responses](#json-schema-draft-like---responses)
  * [Quick checklist for plugin authors](#quick-checklist-for-plugin-authors)

---

## Purpose

This document specifies the JSON-over-stdin/stdout protocol and operational contract between the OmniFlow host and third-party plugins. It exists to ensure interoperability, safety, observability and repeatable release practices across language ecosystems.

---

## Design goals

* **Simplicity:** newline-delimited JSON (NDJSON) messages; easy to implement in any language.
* **Robustness:** clear error semantics, strict input validation and bounded resource usage.
* **Observability:** structured logs and metadata to enable debugging and monitoring.
* **Security:** defaults encourage sandboxing, least privilege, signed releases and SBOMs.
* **Portability:** language-agnostic, platform-agnostic.

---

## High-level contract

* The host launches a plugin process (or container) and communicates via plugin `stdin` (host → plugin) and `stdout` (plugin → host).
* Each message is a single *JSON object* encoded in UTF-8 and terminated by a newline (`\n`). Partial or multi-line JSON objects are not permitted — each JSON object must appear on a single line for reliable parsing by the host.
* Plugins must not write arbitrary binary to `stdout` — only newline-delimited JSON responses and optional human-friendly logs to `stderr`. (See Logging & observability.)
* Each request includes an `id` that MUST be echoed in the corresponding response.

---

## Transport and encoding

* Encoding: UTF-8.
* Framing: newline (`\n`) delimited JSON objects (one JSON object per line).
* Max recommended single-line size (host default): **131072 bytes (128 KiB)**. Hosts MAY enforce this limit (configurable via `OMNIFLOW_PLUGIN_MAX_LINE`). Plugins MUST validate / reject inputs exceeding their local limits rather than attempting to parse them.

---

## Message schema (host → plugin)

### Common fields

All host → plugin messages MUST include these top-level fields:

* `id` (string) — unique identifier for correlating request/response. Recommended: UUIDv4 or short unique string. REQUIRED.
* `type` (string) — request type. One of: `health`, `exec`, `shutdown`, `meta`, or custom extension types. REQUIRED.
* `timestamp` (string, optional) — ISO 8601 UTC timestamp when host sent the message. RECOMMENDED.
* `payload` (object | null, optional) — request-specific payload. Use `null` when no payload.

### Request types

#### Health request

* `type: "health"`
* `payload: null` or optional object with probes. Host expects a quick response about liveness & basic readiness.

#### Exec request

* `type: "exec"`
* `payload` object MUST include:

  * `action` (string) — name of the action to perform (e.g., `echo`, `reverse`, `compute`). REQUIRED.
  * `args` (object, optional) — action-specific parameters.
* Exec requests may be long-running; the host will enforce `OMNIFLOW_EXEC_TIMEOUT` (default 10s), and the plugin MUST stop work early if it detects a timeout or cancellation (see Timeouts and cancellation).

#### Shutdown request

* `type: "shutdown"`
* `payload: null`
* Plugin MUST acknowledge and exit gracefully as soon as possible (see Graceful shutdown).

#### Meta / Custom requests

* `type: "meta"` or other custom string, `payload` may carry configuration or other requests. Hosts and plugins SHOULD document and version any custom types.

---

## Message schema (plugin → host)

### Common response fields

All plugin → host response objects MUST include:

* `id` (string) — must match the originating request `id`. REQUIRED.
* `status` (string) — one of: `ok`, `error`, `busy`. REQUIRED.
* `code` (integer, optional) — numeric result code. `0` indicates success. Non-zero indicates error category. RECOMMENDED for machine parsing.
* `message` (string, optional) — human readable summary.
* `body` (object | array | null, optional) — result payload; schema depends on action.

The response MUST be a single JSON object on one line, terminated by `\n`.

### Standard statuses and codes

* `status: "ok"` — operation completed successfully. `code: 0` recommended.
* `status: "error"` — operation failed. Provide `code` and `message` describing the failure. `code` namespace:

  * `1xx` — request validation / parsing errors (e.g., malformed JSON, missing fields).
  * `2xx` — action errors (e.g., unsupported action).
  * `3xx` — runtime errors (e.g., resource exhaustion).
  * `4xx` — internal plugin errors / unexpected exceptions.
* `status: "busy"` — plugin cannot process this request right now (e.g., overloaded). Host may retry later.

Plugins MAY include `meta` inside `body` with additional info: `processing_time_ms`, `memory_used_bytes` etc.

---

## Examples

### Health request / response

Host → Plugin:

```json
{"id":"hc-1","type":"health","timestamp":"2025-12-02T00:00:00Z","payload":null}
```

Plugin → Host:

```json
{"id":"hc-1","status":"ok","code":0,"body":{"status":"healthy","version":"1.2.3","uptime_seconds":42}}
```

### Exec (echo) request / response

Host → Plugin:

```json
{"id":"exec-1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}
```

Plugin → Host:

```json
{"id":"exec-1","status":"ok","code":0,"body":{"action":"echo","message":"hello"}}
```

### Exec (compute) request / response

Host → Plugin:

```json
{"id":"exec-2","type":"exec","payload":{"action":"compute","args":{"numbers":[1,2,3.5]}}}
```

Plugin → Host:

```json
{"id":"exec-2","status":"ok","code":0,"body":{"action":"compute","sum":6.5}}
```

### Shutdown request / response

Host → Plugin:

```json
{"id":"shutdown-1","type":"shutdown","payload":null}
```

Plugin → Host:

```json
{"id":"shutdown-1","status":"ok","code":0,"body":{"result":"shutting_down"}}
```

---

## Behavioral requirements

### Startup and handshake

* Plugins SHOULD write a short startup banner to `stderr` for human debugging (not to `stdout`). Example: `OmniFlow C Plugin v1.2.3 starting (pid: 42)`.
* No special handshake message is required. The first host message (often `health`) acts as an implicit readiness probe.

### Synchronous vs asynchronous handling

* Plugins MAY process requests synchronously (respond before reading the next request) or concurrently (spawn workers, stream partial progress to logs, then respond).
* The plugin MUST ensure a single well-formed response object is emitted for each request `id`. Duplicate responses or missing responses are both considered failures.

### Timeouts and cancellation

* Host enforces an exec timeout via environment variable `OMNIFLOW_EXEC_TIMEOUT` (default `10` seconds).
* Plugin MUST stop processing quickly when it detects timeouts or when it receives a `shutdown` request. If a plugin cannot enforce a hard timeout internally, the host will forcibly terminate it.

### Resource usage and quotas

* Host provides guidance; plugins MUST behave within provided resource limits: memory, CPU, file handles. Typical recommended defaults:

  * `OMNIFLOW_PLUGIN_MAX_LINE`: 131072 bytes (message size limit)
  * `max memory`: 256 MiB (recommended in container limits)
* Plugins MUST avoid spawning unbounded child processes or creating network listeners unless explicitly allowed and documented.

### Error handling and validation

* Plugins MUST validate incoming JSON and respond with `status: error` and `code` `1xx` for malformed requests. They MUST NOT crash on malformed input.
* For unknown action values, respond with `status: error`, `code: 200` (example) and `message: "unsupported action: <name>"`.

### Graceful shutdown

* On `shutdown` request, plugin MUST:

  1. Stop accepting new work.
  2. Finish or abort in-flight requests deterministically within `stop_timeout_seconds` (host config; default 5s).
  3. Write an acknowledgement response for the `shutdown` request.
  4. Exit with code `0`. If graceful exit fails, host may terminate the process.

---

## Security requirements

### Input size and DoS protections

* Plugins MUST enforce an input-line maximum length (`OMNIFLOW_PLUGIN_MAX_LINE`) and reject messages that exceed it with `status: error`, `code: 101` (`payload too large`).
* Validate types & shape of payloads; do not call into unsafe string functions without bounds checking.

### Sandboxing and least privilege

* Plugins SHOULD run with least privilege: non-root user inside containers, dropped capabilities, seccomp/AppArmor profiles.
* Network access SHOULD be disabled by default unless explicitly required and approved. If network access is required, ensure egress allowlists and TLS validation.

### Secrets handling

* Do not store sensitive secrets in the plugin repository. Secrets required at runtime MUST be provided via host secret management (e.g., environment variables from secret managers, mounted files with proper permissions). Avoid logging secrets.

### Dependency and supply-chain hygiene

* Vendor or pin third-party dependencies; provide SBOM (SPDX/CycloneDX) with releases.
* Sign release artifacts (PGP and/or cosign) and publish checksums.

---

## Logging & observability

### Structured logs

* Plugins MUST write diagnostic logs to `stderr`. Two recommended modes:

  * **Human mode (default)**: concise text
  * **Structured JSON mode**: when `OMNIFLOW_LOG_JSON=true` — each log line must be a single JSON object with fields:

    * `ts` (ISO-8601 UTC), `level` (`debug|info|warn|error`), `msg`, `plugin_id`, optional `meta` object.
* Avoid writing JSON to `stdout` — `stdout` is reserved for responses.

### Metrics

* Plugins SHOULD expose basic metrics via logs or an optional endpoint (only when permitted). Metrics to consider:

  * `requests_total`, `requests_failed`, `processing_time_ms`, `memory_bytes`, `uptime_seconds`.

### Tracing

* If distributed tracing is used, accept an optional trace context inside `payload.meta.trace` and propagate to downstream calls. Alternatively, emit `trace_id` in response `body.meta` for correlation.

---

## Testing & CI guidance

* Unit tests for all action handlers.
* Integration tests using the host emulator (`plugins/c/tests/test_sample_plugin.sh` pattern) that:

  * start plugin process, send NDJSON, assert responses using `jq`.
  * check behavior under invalid JSON, oversized input, unsupported actions, and graceful shutdown.
* Run AddressSanitizer (ASan) + UndefinedBehaviorSanitizer (UBSan) builds in CI and fail on violations.
* Generate SBOM and run SCA (Trivy/Snyk) on packaged artifacts.
* Fuzz JSON parsing (libFuzzer / AFL) to exercise malformed inputs.

---

## Release & packaging requirements

When publishing a plugin release (per `plugins/cpp-packages/releases` conventions):

* Provide:

  * Platform artifacts (tar.gz / zip), stripped and debug-symbols separated.
  * `sha256sum.txt` and `sha512sum.txt`.
  * GPG detached signatures (or cosign attestations).
  * SBOMs (`spdx.json`, `cyclonedx.xml`).
  * `release_metadata.json` with `version`, `commit`, `published_at`, `artifacts` paths.
  * `provenance.json` (builder image digest, CI job id, build flags).
* Include `README.md`, `SECURITY.md` and usage examples for the release.

---

## Versioning and backward compatibility

* Plugin API messages are intentionally small and stable. When adding fields:

  * New optional fields are allowed (backward-compatible).
  * Removing or changing semantics of existing fields is a breaking change and MUST increment plugin/host compatibility major version and be documented in release notes.
* Hosts should tolerate unknown fields; plugins should tolerate unknown `payload` fields (ignore extras).

---

## Appendices

### JSON Schema (draft-like) — requests

```json
{
  "$id": "https://omniflow.example/schema/plugin-request.json",
  "type": "object",
  "required": ["id", "type"],
  "properties": {
    "id": {"type": "string"},
    "type": {"type": "string"},
    "timestamp": {"type": "string", "format": "date-time"},
    "payload": {}
  },
  "additionalProperties": false
}
```

(Extend with `payload` schemas per `type` in language-specific docs.)

### JSON Schema (draft-like) — responses

```json
{
  "$id": "https://omniflow.example/schema/plugin-response.json",
  "type": "object",
  "required": ["id", "status"],
  "properties": {
    "id": {"type": "string"},
    "status": {"type": "string", "enum": ["ok","error","busy"]},
    "code": {"type": "integer"},
    "message": {"type": "string"},
    "body": {}
  },
  "additionalProperties": false
}
```

### Quick checklist for plugin authors

* [ ] Validate each incoming JSON and enforce `OMNIFLOW_PLUGIN_MAX_LINE`.
* [ ] Always echo `id` field in response.
* [ ] Return one (and only one) JSON response per request `id`.
* [ ] Write diagnostics to `stderr` only; keep `stdout` for responses.
* [ ] Support `health` and `shutdown` request types.
* [ ] Respect `OMNIFLOW_EXEC_TIMEOUT` and implement cancellation.
* [ ] Provide `README.md`, `SECURITY.md`, SBOM and signed release artifacts.
* [ ] Run ASan/UBSan/Valgrind in CI and fix issues before release.
* [ ] Do not log secrets. Use secret managers for runtime secrets.

---

## Contact & governance

If you propose a change to this API or encounter interoperability issues, open an issue or PR in the OmniFlow repository and tag it `area:plugins` or `spec:plugin-api`. Major changes must include migration guidance and compatibility tests.
