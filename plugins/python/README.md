# OmniFlow Python Plugin — `plugins/python/README.md`

**Production-ready README** for the OmniFlow Python plugin. Drop this file into `OmniFlow/plugins/python/` — it’s written for maintainers, plugin authors and release engineers and contains everything needed to build, test, run and securely operate the Python plugin runtime.

---

## Table of contents

* [Overview](#overview)
* [Key features](#key-features)
* [Repository layout](#repository-layout)
* [Requirements](#requirements)
* [Quick start (build & run)](#quick-start-build--run)
* [NDJSON protocol summary](#ndjson-protocol-summary)
* [Development workflow](#development-workflow)
* [Testing](#testing)
* [Docker & container usage](#docker--container-usage)
* [Configuration & environment variables](#configuration--environment-variables)
* [Security & hardening guidance](#security--hardening-guidance)
* [CI / Release recommendations](#ci--release-recommendations)
* [Troubleshooting](#troubleshooting)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

This folder contains a production-quality Python plugin template and runtime helpers for **OmniFlow**. The plugin implements the OmniFlow plugin protocol over NDJSON (newline-delimited JSON) on `stdin`/`stdout`, supports high-performance async handlers, and is packaged with tooling, tests and Docker configuration for CI/CD and secure deployment.

The code in this folder is intended to be a reference implementation and a drop-in template for new Python plugins.

---

## Key features

* NDJSON framing (one JSON object per newline)
* Robust request/response envelope helpers and size guards to mitigate DoS
* Example handlers: `health`, `exec` with `echo`/`reverse`/`compute`, `shutdown`
* Async-first implementation (aiohttp-ready) but works in pure-CLI mode
* Tests (pytest) and integration harnesses included
* Multi-stage Dockerfile with non-root runtime, healthcheck and SBOM-friendly labels
* `pyproject.toml` / Poetry and `requirements.txt` support for release workflows

---

## Repository layout

```
plugins/python/
│
├── test/
│   ├── test_actions.py
│   └── test_protocol.py
│
├── sample_plugin.py           # Example plugin entrypoint (reads NDJSON from stdin)
├── pyproject.toml             # Poetry / packaging metadata
├── poetry/                    # optional poetry workspace & lock (poetry.lock)
├── requirements.txt           # pinned runtime deps (or for pip installs)
├── Dockerfile                 # Production-ready multi-stage Dockerfile
└── README.md                  # (this file)
```

---

## Requirements

* **Python**: `3.8+` (3.10/3.11 recommended for production)
* **Recommended**: use an isolated venv (`python -m venv .venv`) or Poetry
* **Tools (dev)**: `poetry` or `pip`, `pytest`, `black`, `mypy` (for development and CI)

---

## Quick start (build & run)

### Using pip + requirements.txt

```bash
cd plugins/python
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# Run a one-line health probe:
echo '{"id":"hc-1","type":"health","payload":null}' | python sample_plugin.py
```

### Using Poetry (recommended for packaging)

```bash
cd plugins/python
poetry install
poetry run python sample_plugin.py  # or: poetry run omniflow-plugin (if entrypoint defined)
```

### Run tests

```bash
# inside venv or poetry shell
pytest -q
```

---

## NDJSON protocol summary

OmniFlow plugin protocol is intentionally simple and language-agnostic:

* **Framing:** NDJSON — one JSON object per newline (no multi-line JSON).
* **Request envelope:** must include `id` (string) and `type` (string). Common types: `health`, `exec`, `shutdown`. Example:

  ```json
  {"id":"uuid-1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}
  ```
* **Response envelope:** must echo `id` and include `status` (`ok` | `error` | `busy`), optional `code`, `message`, and `body`:

  ```json
  {"id":"uuid-1","status":"ok","code":0,"body":{"action":"echo","message":"hello"}}
  ```
* **Guarantees:** plugin should produce at most one response per request id, handle malformed JSON without crashing, and enforce a configurable max-line byte limit before parsing.

For full spec see `plugins/common/protocol.md` (in the repo).

---

## Development workflow

* Use a virtual environment or Poetry. Keep dev dependencies out of runtime containers.
* Follow the project's style: run `black`, `mypy`, `flake8` (or configured equivalents) locally.
* Write unit tests for protocol helpers and pure functions; write small integration tests that spawn the plugin process and interact via NDJSON for end-to-end validation.

Example developer commands:

```bash
# format, lint, type-check
black src tests
mypy src --ignore-missing-imports
flake8 src

# run unit tests
pytest -q
```

---

## Testing

The repository includes:

* Unit tests: test protocol parsing, response building, action handlers.
* Integration harness: `tests/integration_test.sh` (spawns plugin, uses FIFO, verifies NDJSON responses).
* Optional: smoke test that spawns `sample_plugin.py` and exchanges NDJSON lines.

Run tests locally:

```bash
# pip path
pytest -q tests/

# or using poetry
poetry run pytest -q
```

CI should run both unit and integration tests; containerized integration tests are recommended to match runtime environment.

---

## Docker & container usage

A production-ready multi-stage `Dockerfile` is provided.

### Build image (example):

```bash
docker build \
  --file plugins/python/Dockerfile \
  --build-arg VERSION=1.0.0 \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t omniflow/plugin-python:1.0.0 .
```

### Run container (secure defaults):

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  omniflow/plugin-python:1.0.0
```

The `Dockerfile` runs the plugin as a non-root user and provides a `HEALTHCHECK` that sends a one-line NDJSON health probe to the plugin.

---

## Configuration & environment variables

Standard environment variables (implement in your plugin code):

* `OMNIFLOW_PLUGIN_MAX_LINE` — `131072` (bytes). Max NDJSON line size (guard against DoS).
* `OMNIFLOW_EXEC_TIMEOUT` — per-request timeout in seconds.
* `OMNIFLOW_LOG_JSON` — `false` by default; when `true` emit structured JSON logs to `stderr`.
* `PLUGIN_LOG_LEVEL` — default `info`, supports `debug`, `warn`, `error`.

Document and honor these variables in your plugin to make operation predictable.

---

## Security & hardening guidance

* **Least privilege:** containers run as non-root user. Use `--read-only` and mount writable directories explicitly.
* **Input bounds:** enforce `OMNIFLOW_PLUGIN_MAX_LINE` — do byte-length check before parsing.
* **Dependency hygiene:** pin dependencies, produce a lockfile (`poetry.lock`) and an SBOM for each release; run SCA scanners (Trivy, GitHub Dependabot).
* **Secrets:** never store secrets in repo; use secret stores or runtime secret mounts.
* **Resource limits:** limit CPU/memory in orchestrator (Kubernetes resource requests/limits).
* **Signing:** sign artifacts (GPG or cosign) and publish checksums with releases.

---

## CI / Release recommendations

A recommended release CI pipeline:

1. Checkout → `poetry install` (or `pip install -r requirements.txt`)
2. `black --check`, `mypy`, `flake8`, `pytest` (unit + integration)
3. Build Docker image and run containerized integration tests
4. Generate SBOM (e.g., `syft`), checksums, and sign artifacts (GPG or cosign)
5. Publish image to registry and Python package to your artifact repository (if applicable)

Include caching for pip/Poetry and Docker layers for faster CI.

---

## Troubleshooting

* **No response to probe:** ensure plugin reads `stdin` rather than waiting for interactive input. Test locally with:

  ```bash
  echo '{"id":"hc","type":"health","payload":null}' | python sample_plugin.py
  ```
* **Plugin crashes on malformed JSON:** parser must catch `JSONDecodeError` and return an `error` response instead of exiting.
* **Healthcheck failing in container:** exec into container and run health probe manually to inspect `stdout`/`stderr`.
* **Permission errors:** confirm the non-root runtime user owns required runtime directories or adjust mounts.

---

## Contributing

Contributions welcome. Suggested workflow:

1. Fork → branch → implement → add tests.
2. Run `black`, `mypy`, `pytest` locally.
3. Open PR with clear description and changelog entry.
4. Maintainers will review CI results and request changes if needed.

Follow Conventional Commits (`feat:`, `fix:`, `chore:`) and maintain semantic versioning for releases.

---

## License & attribution

This module is licensed under **Apache License 2.0**. See the `LICENSE` file for full text.

Maintained by **TheSkiF4er / OmniFlow**. For questions or issues open an issue in the repository or contact the maintainers.
