# OmniFlow Ruby Plugin — `plugins/ruby/README.md`

**Production-ready README** for the OmniFlow Ruby plugin. Drop this file into `OmniFlow/plugins/ruby/`. It’s written for maintainers, plugin authors, CI engineers and operators and contains everything needed to build, test, containerize and securely operate the Ruby plugin runtime.

---

## Table of contents

* [Overview](#overview)
* [Features](#features)
* [Repository layout](#repository-layout)
* [Requirements](#requirements)
* [Quick start — build & run locally](#quick-start--build--run-locally)
* [NDJSON protocol (summary)](#ndjson-protocol-summary)
* [Development workflow](#development-workflow)
* [Testing](#testing)
* [Docker & deployment](#docker--deployment)
* [Configuration & environment variables](#configuration--environment-variables)
* [Logging, metrics & health](#logging-metrics--health)
* [Security & hardening guidance](#security--hardening-guidance)
* [CI / Release recommendations](#ci--release-recommendations)
* [Troubleshooting](#troubleshooting)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

The Ruby plugin implements the OmniFlow NDJSON plugin protocol (one JSON object per newline on `stdin`/`stdout`) and provides a production-ready scaffold for writing Ruby-based plugins for OmniFlow. The folder includes example entrypoints, tests, Dockerfile, and guidance to produce small, secure runtime images suitable for CI/CD and production deployment.

---

## Features

* NDJSON framing helpers and robust request/response envelope
* Example actions: `health`, `exec` (`echo`, `reverse`, `compute`), `shutdown`
* Unit & integration test examples (RSpec) and integration harness patterns
* Multi-stage Dockerfile producing a secure, non-root runtime image
* Recommendations for SBOM, signing, and release artifacts

---

## Repository layout

```
plugins/ruby/
│
├── test/
│   └── test_plugin.rb
│
├── sample_plugin.rb           # example plugin entrypoint
├── Gemfile                    # runtime and dev dependencies
├── Gemfile.lock               # (should be committed for reproducible installs)
├── Dockerfile                 # multi-stage production Dockerfile
└── README.md                  # (this file)
```

---

## Requirements

* **Ruby**: `>= 3.1` (3.2+ recommended)
* **Bundler**: `bundle` (for dependency installation)
* **Tools (dev)**: `rspec`, `rubocop`, `bundler-audit` (optional)
* **Container tooling**: Docker (for image builds and containerized integration tests)

---

## Quick start — build & run locally

### Install dependencies

From repository root or `plugins/ruby`:

```bash
cd plugins/ruby
bundle install --path vendor/bundle
```

### Run a one-line health probe

```bash
echo '{"id":"hc-1","type":"health","payload":null}' | ruby sample_plugin.rb
```

Expected: a single-line JSON response echoing the `id` with status `ok` (or body.status = `"healthy"`).

---

## NDJSON protocol summary

OmniFlow uses a small, language-agnostic protocol for plugin communication:

* **Framing:** NDJSON — one JSON object per newline (no multi-line JSON).
* **Request envelope:** Must include `id` (string) and `type` (string). Example:

  ```json
  {"id":"uuid-1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}
  ```
* **Response envelope:** Must echo `id` and include `status` (`ok` | `error` | `busy`), optional `code`, `message`, and `body`:

  ```json
  {"id":"uuid-1","status":"ok","code":0,"body":{"action":"echo","message":"hello"}}
  ```
* **Minimum request types:** `health`, `exec`, `shutdown`
* **Rules:** enforce a max-line byte limit before parsing (e.g., `131072` bytes), never crash on malformed JSON (return an error response), emit exactly one response per request id.

See `plugins/common/protocol.md` for the full spec and examples.

---

## Development workflow

* Use Bundler for dependency management: `bundle install`.
* Use `rubocop` for linting and formatting.
* Keep dev dependencies (`rspec`, `rubocop`, `bundler-audit`) in `Gemfile` under `:development, :test` groups.
* Use `rake` or simple shell scripts to encapsulate common tasks (build/test/lint).

Recommended commands:

```bash
# install dev deps
bundle install

# run style checks
bundle exec rubocop

# run tests
bundle exec rspec

# security audit
bundle exec bundler-audit check --update
```

---

## Testing

### Unit tests

Focus on testing:

* NDJSON parser & builders (single-line framing)
* Action handlers: echo, reverse, compute
* Robustness: malformed JSON, oversized input limits, shutdown semantics

### Integration tests (shell harness)

A sample shell-based integration test harness is provided in `tests/`. It:

* starts the plugin as a subprocess,
* sends NDJSON requests via FIFO or redirected stdin,
* validates single-line JSON responses using `jq` or Ruby JSON parsing.

Run the integration script (example):

```bash
bash tests/integration_test.sh
```

(Adjust the script and permissions as needed.)

---

## Docker & deployment

A production-ready multi-stage `Dockerfile` is included at `plugins/ruby/Dockerfile`. Key characteristics:

* Builds gems in a builder image (with native build deps), copies only required runtime files into the final image.
* Runs the plugin as a non-root user.
* Includes a `HEALTHCHECK` that sends a one-line NDJSON health probe.
* Adds provenance labels for SBOM and release automation.

### Build image (example)

```bash
docker build \
  --build-arg VERSION=1.0.0 \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t omniflow/plugin-ruby:1.0.0 \
  -f plugins/ruby/Dockerfile .
```

### Run container (recommended secure flags)

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  omniflow/plugin-ruby:1.0.0
```

---

## Configuration & environment variables

Implement and honor these environment variables in the plugin to make operation predictable:

* `OMNIFLOW_PLUGIN_MAX_LINE` — default `131072` — max bytes per NDJSON line (guard against DoS)
* `OMNIFLOW_EXEC_TIMEOUT` — default `10` — per-request timeout (seconds)
* `OMNIFLOW_LOG_JSON` — `false` by default — when `true`, emit structured JSON logs to `stderr`
* `PLUGIN_LOG_LEVEL` — default `info` — log level (debug/info/warn/error)

Document additional plugin-specific env vars in `README` or code.

---

## Logging, metrics & health

* Log to `stderr` by default. Provide structured JSON logs when `OMNIFLOW_LOG_JSON=true` for easier ingestion into log systems.
* Provide a `health` handler that returns version and uptime in the response body for the orchestrator and Docker `HEALTHCHECK`.
* Optionally expose a metrics endpoint (HTTP) if you need Prometheus; secure it properly (authentication, network policies).

---

## Security & hardening guidance

Follow these best practices before releasing to production:

* **Least privilege:** build images to run as non-root (Dockerfile does) and require no unnecessary capabilities.
* **Read-only root:** run containers with `--read-only` and mount only required writable paths.
* **Input bounds:** enforce `OMNIFLOW_PLUGIN_MAX_LINE` before parsing; fail fast on oversized lines.
* **Dependency hygiene:** commit `Gemfile.lock`, run `bundler-audit`, and produce SBOMs during CI.
* **Secrets:** never store secrets in repo — use secret managers or mounted files with strict permissions.
* **Signing & checksums:** sign release artifacts (GPG/cosign) and publish checksums (`sha256`, `sha512`).

---

## CI / Release recommendations

Suggested CI pipeline:

1. `bundle install --deployment` (with cache for `vendor/cache`)
2. `bundle exec rubocop` and `bundle exec rspec`
3. Build Docker image and run containerized integration tests
4. Generate SBOM (e.g., `syft`), checksums and sign artifacts (cosign/GPG)
5. Publish to registry (Docker), and attach release metadata (version, vcs_ref, build_date)

Cache `vendor/cache` and Docker layers to speed up repeated CI runs.

---

## Troubleshooting

* **No response to a probe:** ensure plugin is reading `stdin` and not waiting for interactive input; run the probe locally and inspect `stderr`.
* **Crashes on malformed JSON:** parser must catch parse errors and return an error response instead of exiting. Use unit tests to reproduce and fix.
* **Healthcheck failing in container:** run the health probe inside the container to see stdout/stderr and root cause the failure.
* **Permission errors:** check that the non-root runtime user owns necessary runtime directories and mounted volumes.

---

## Contributing

Contributions welcome. Suggested workflow:

1. Fork → branch → implement → add tests.
2. Run `bundle exec rubocop` and `bundle exec rspec` locally.
3. Open a pull request with testing notes and CI status.
4. Follow Conventional Commits and update `CHANGELOG.md` for releases.

---

## License & attribution

This folder and its supporting files are licensed under **Apache License 2.0**. See the `LICENSE` file in the repository root for full text.

Maintained by **TheSkiF4er / OmniFlow**. For questions or issues open an issue in the repository or contact the maintainers.
