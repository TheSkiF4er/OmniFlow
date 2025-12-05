# OmniFlow PHP Plugin — `plugins/php/README.md`

**Production-ready README** for the OmniFlow PHP plugin. Drop this file into `OmniFlow/plugins/php/` — it is written for maintainers, integrators and release engineers and contains everything needed to build, test, run and securely operate the PHP plugin.

---

## Table of contents

* [Overview](#overview)
* [Features](#features)
* [Repository layout](#repository-layout)
* [Requirements](#requirements)
* [Quick start (build & run)](#quick-start-build--run)
* [Usage examples (NDJSON protocol)](#usage-examples-ndjson-protocol)
* [Testing](#testing)
* [Docker (build & runtime)](#docker-build--runtime)
* [Configuration & environment variables](#configuration--environment-variables)
* [Security & hardening guidance](#security--hardening-guidance)
* [CI / Release recommendations](#ci--release-recommendations)
* [Troubleshooting](#troubleshooting)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

This folder contains the reference OmniFlow PHP plugin implementation and helper files. The plugin implements the OmniFlow NDJSON plugin protocol (one JSON object per line on `stdin`/`stdout`) and is intended to be:

* Small and dependency-light (PHP CLI script + `composer` dependencies)
* Secure-by-default (non-root runtime, recommended container constraints)
* Testable (unit + integration test examples)
* Easy to package and release (Composer metadata, Dockerfile, SBOM-friendly)

The example plugin demonstrates common actions (`health`, `exec` with `echo`, `reverse`, `compute`, and `shutdown`) and is production-hardened in structure and guidance.

---

## Features

* NDJSON framing (exactly one JSON object per newline)
* Clear request/response envelope (echo `id`, `status`, `code`, `body`)
* Minimal external dependencies (`symfony/process` optionally used)
* PHPUnit integration tests + test harness script examples
* Docker multi-stage build that produces a secure runtime image
* Guidance for SBOM, artifact signing and release metadata

---

## Repository layout

```
plugins/php/
│
├── test/
│   ├── integration_test.sh  # End-to-end protocol integration test
│   ├── phpunit.xml
│   └── test_plugin.php       # Unit tests for plugin logic
│
├── sample_plugin.php          # Example entrypoint script (NDJSON plugin)
├── composer.json              # Package metadata & dependencies
├── composer.lock              # (optional) locked deps
├── Dockerfile                 # Multi-stage build for production runtime
└── README.md                  # (this file)
```

---

## Requirements

* **PHP:** `>=8.1` (CLI SAPI recommended)
* **Composer:** required for dependency installation in builder step (`composer install`)
* **Optional tools (for tests/CI):** `phpunit`, `php-cs-fixer`, `phpstan`/`psalm`, `jq` (for shell-based verification)
* **Runtime:** any Linux machine/container with PHP CLI installed (Alpine, Debian-based images supported)

---

## Quick start (build & run)

### Install dependencies (development machine)

```bash
cd plugins/php
composer install --no-dev --prefer-dist --optimize-autoloader
# For development with dev dependencies:
composer install
```

### Run the plugin locally (stdin/stdout NDJSON)

```bash
# From repository root (plugin reads NDJSON from stdin and writes single-line JSON responses to stdout)
echo '{"id":"hc-1","type":"health","payload":null}' | php plugins/php/sample_plugin.php
```

Expect a single-line JSON response echoing the `id` and a status `ok` (or body.status = "healthy").

---

## Usage examples (NDJSON protocol)

**Host → Plugin (health probe)**

```json
{"id":"hc-1","type":"health","payload":null}
```

**Plugin → Host (health response)**

```json
{"id":"hc-1","status":"ok","code":0,"body":{"status":"healthy","version":"1.0.0"}}
```

**Exec example (echo)**

Host request:

```json
{"id":"exec-1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}
```

Plugin response:

```json
{"id":"exec-1","status":"ok","code":0,"body":{"action":"echo","message":"hello"}}
```

**Requirements**

* Plugin must echo the same `id` in response.
* Output must be exactly one JSON object per line (NDJSON).
* Enforce a reasonable maximum input line length (e.g. `131072` bytes) to mitigate DoS.

---

## Testing

### Unit & integration (PHPUnit)

Install dev dependencies (if not already):

```bash
composer install
```

Run PHPUnit tests (recommended from plugin folder):

```bash
./vendor/bin/phpunit --configuration tests/phpunit.xml
```

### Integration script

A shell-based integration test harness is provided in `tests/` (e.g., `tests/test_plugin.php` or `integration_test.sh`) — it:

* Builds/starts plugin process
* Sends NDJSON requests via a FIFO or direct stdin
* Verifies single-line JSON responses using `jq` or PHP JSON parsing

Run integration tests:

```bash
# Example (if using provided script)
bash plugins/php/tests/integration_test.sh
```

(Adjust paths/commands if your harness differs.)

---

## Docker (build & runtime)

A multi-stage Dockerfile is included at `plugins/php/Dockerfile`.

### Build image (recommended for CI)

```bash
docker build \
  --build-arg VERSION=1.0.0 \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t omniflow/plugin-php:1.0.0 \
  -f plugins/php/Dockerfile .
```

### Run container (secure defaults)

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  omniflow/plugin-php:1.0.0
```

The container runs the PHP plugin as a non-root user. It includes a `HEALTHCHECK` that sends a one-line NDJSON health probe to the plugin and expects a valid response.

---

## Configuration & environment variables

The plugin respects (or should be implemented to respect) the following environment variables:

| Variable                   |  Default | Description                                        |
| -------------------------- | -------: | -------------------------------------------------- |
| `OMNIFLOW_PLUGIN_MAX_LINE` | `131072` | Maximum allowed bytes per NDJSON line              |
| `OMNIFLOW_EXEC_TIMEOUT`    |     `10` | Per-request timeout (seconds) for heavy operations |
| `OMNIFLOW_LOG_JSON`        |  `false` | If `true`, logs to stderr as JSON lines            |
| `OMNIFLOW_PLUGIN_DEBUG`    |  `false` | Enables verbose debug logs if set                  |

Use container environment settings or process environment to configure behavior.

---

## Security & hardening guidance

Follow these best practices before releasing to production:

* **Least privilege:** run plugin as non-root (Dockerfile enforces non-root user).
* **Sandboxing:** default to network-disabled containers unless required. Use unix sockets for inter-plugin RPC where possible.
* **Drop capabilities & set `no-new-privileges`:** use `--cap-drop ALL` and `--security-opt no-new-privileges`.
* **Read-only root filesystem:** use `--read-only` and mount any writable directories as tmpfs or specific volumes only.
* **Input validation:** enforce `OMNIFLOW_PLUGIN_MAX_LINE` before parsing; limit field sizes and types.
* **Dependency hygiene:** pin Composer dependencies (commit `composer.lock`), generate and include SBOM during release (e.g., `syft`/`cyclonedx`).
* **Secrets:** never store secrets in repo; inject secrets at runtime via secret managers or mounted files with strict permissions.
* **Signing & checksums:** sign release artifacts (GPG or cosign) and publish checksums (`sha256sum`, `sha512sum`).

---

## CI / Release recommendations

A typical CI release pipeline should perform:

1. `composer install --no-dev --prefer-dist --optimize-autoloader`
2. `composer run-script lint` (if configured) and `composer run-script test`
3. Build Docker image and run integration tests against container
4. Produce artifacts: `composer.lock`, SBOM, checksums, and signatures
5. Publish to registry (Docker registry, package repository, artifact storage)

Include `governance` checks like SCA (`trivy`, `ossf-scanner`), license checks and automated security tests.

---

## Troubleshooting

* **No response from plugin**: ensure plugin is reading from stdin (not awaiting interactive input). Run simple probe:

  ```bash
  echo '{"id":"hc","type":"health","payload":null}' | php plugins/php/sample_plugin.php
  ```

  Check `stderr` for startup errors.

* **Plugin crashes on malformed JSON**: plugin must be defensive — handle parse errors without exiting. Run unit tests to reproduce.

* **Healthcheck failing in container**: run the health command manually inside the container to capture stdout/stderr:

  ```bash
  docker exec -it <container> sh -c "echo '{\"id\":\"hc-1\",\"type\":\"health\",\"payload\":null}' | php /opt/omniflow/plugins/php/sample_plugin.php"
  ```

* **Permissions errors**: verify container user has access to mounted volumes, socket files, and any model/data files.

---

## Contributing

Contributions are welcome. Please follow:

* Fork and open a pull request against `main`.
* Run `composer install`, `composer test`, and `composer lint` locally before submitting.
* Use descriptive commit messages and follow Conventional Commits if possible.
* Update `CHANGELOG.md` and `release_metadata.json` for public releases.

---

## License & attribution

This plugin and supporting files are licensed under **Apache License 2.0**. See `LICENSE` for full text.

Maintained by **TheSkiF4er / OmniFlow**. For questions or support, open an issue in the repository or contact `maintainers@cajeer.com`.
