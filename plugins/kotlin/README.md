# OmniFlow Kotlin Plugin — `plugins/kotlin/README.md`

**Production-ready README** for the OmniFlow Kotlin plugin. Drop this file into `OmniFlow/plugins/kotlin/` for a release-quality developer and ops reference.

---

## Table of contents

* [Overview](#overview)
* [Features](#features)
* [Repository layout](#repository-layout)
* [Requirements & supported platforms](#requirements--supported-platforms)
* [Quick start (build & run)](#quick-start-build--run)
* [Development workflow](#development-workflow)
* [Testing & CI recommendations](#testing--ci-recommendations)
* [Docker & deployment](#docker--deployment)
* [Plugin protocol (NDJSON) summary](#plugin-protocol-ndjson-summary)
* [Configuration & environment variables](#configuration--environment-variables)
* [Security & hardening guidance](#security--hardening-guidance)
* [Releasing & artifacts](#releasing--artifacts)
* [Troubleshooting](#troubleshooting)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

This folder contains a production-grade Kotlin implementation for an OmniFlow plugin. The plugin is intended to:

* implement the OmniFlow NDJSON plugin protocol (one JSON object per line on `stdin` / `stdout`),
* provide high-performance handlers using Kotlin coroutines,
* be container-friendly and easily integrated into CI/CD pipelines,
* ship with tests, build scripts, a Dockerfile, and publishing hooks.

The provided code and build files are opinionated for reliability, reproducibility and security for enterprise usage.

---

## Features

* Kotlin (JVM) plugin scaffold using idiomatic coroutines and kotlinx.serialization.
* ShadowJar (fat JAR) support for standalone runtime.
* Gradle Kotlin DSL (`build.gradle.kts`) for robust builds and publishing.
* Tests (JUnit5 + Kotest) and static analysis (ktlint, detekt).
* Docker multi-stage build (production-ready, non-root runtime).
* Build metadata injection (`VERSION`, `VCS_REF`, `BUILD_DATE`).
* Packaging, signing and publication stubs for CI/CD.

---

## Repository layout

```
plugins/kotlin/
│
├── test/
│   ├── integration_test.sh  # End-to-end protocol integration test
│   └── plugin_test.go       # Unit tests for plugin logic
│
├── build.gradle.kts              # Gradle Kotlin DSL build
├── Dockerfile                    # Multi-stage Dockerfile for building + runtime
├── README.md                     # (this file)
└── sample_plugin.kts                       # main plugin source (example name)
```

---

## Requirements & supported platforms

* **JDK:** Java 17 (tested with Eclipse Temurin 17).
* **Gradle:** Gradle wrapper is included — use `./gradlew`.
* **OS:** Linux + macOS for build; Docker-based runtime for containers.
* **CI:** Ubuntu-latest runners recommended. Ensure Gradle cache and Docker available for optimal performance.

---

## Quick start (build & run)

### Build (local)

From repository root or `plugins/kotlin`:

```bash
# using included wrapper (recommended)
./gradlew :plugins:kotlin:clean :plugins:kotlin:shadowJar

# or from plugin folder
cd plugins/kotlin
./gradlew clean shadowJar
```

The fat JAR will be produced under:

```
plugins/kotlin/build/libs/omniflow-plugin-kotlin-<version>.jar
```

### Run locally (one-shot)

The plugin reads NDJSON from stdin and writes NDJSON to stdout:

```bash
java -jar build/libs/omniflow-plugin-kotlin-<version>.jar
```

Test using a one-line health probe:

```bash
echo '{"id":"hc-1","type":"health","payload":null}' | java -jar build/libs/omniflow-plugin-kotlin-<version>.jar
```

---

## Development workflow

### IDE

Open the project in IntelliJ IDEA (recommended). Gradle import will configure Kotlin tooling automatically.

### Linting & formatting

Run static checks and formatters:

```bash
./gradlew ktlintCheck detekt
./gradlew ktlintFormat
```

### Code style

* Use `ktlint` rules.
* Keep coroutine usage explicit (`suspend` functions, use structured concurrency).
* Use `kotlinx.serialization` for JSON parsing and strict schema validation.

---

## Testing & CI recommendations

### Unit tests

```bash
./gradlew test
```

* Use JUnit 5 + `kotlin.test` / Kotest for assertions.
* Keep tests deterministic and fast; avoid network I/O in unit tests.

### Integration tests

Integration tests should exercise:

* NDJSON framing and parsing,
* end-to-end behavior (start the JAR and pipe messages),
* large/oversized payload handling,
* timeouts and shutdown behavior.

Example (from repo root):

```bash
# example integration harness
./plugins/kotlin/test/integration_test.sh
```

(If you don't have such a script, add one that spawns the built JAR with pipes and validates responses using `jq`.)

### CI pipeline (recommended matrix)

* `build` (linux, jdk 17) — compile, lint, test.
* `asan/fuzz` (optional) — if integrating native libs.
* `package` — produce `shadowJar`, SBOM, checksums.
* `sign` — sign artifacts (GPG/cosign).
* `publish` — push to artifact registry (Maven Central / GitHub Packages).

Add caching for Gradle dependencies and Docker layers.

---

## Docker & deployment

A production-ready Dockerfile is included to build via Gradle (multi-stage) and produce a minimal runtime image that runs as non-root.

### Build image (example)

```bash
docker build \
  --build-arg VERSION=1.2.3 \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t omniflow/plugin-kotlin:1.2.3 \
  -f plugins/kotlin/Dockerfile .
```

### Run container

```bash
docker run --rm --name omniflow-kotlin \
  omniflow/plugin-kotlin:1.2.3
```

Container healthcheck sends a one-line NDJSON health probe to the JAR.

For production use, prefer:

* read-only root filesystem (`--read-only`),
* drop capabilities, `--security-opt no-new-privileges`,
* limit resources (`--memory`, `--cpus`) and use Kubernetes PodSecurity policies.

---

## Plugin protocol (NDJSON) summary

OmniFlow uses a very small protocol:

* **Framing:** newline-delimited JSON (NDJSON) — exactly one JSON object per line.
* **Request envelope:** e.g.

  ```json
  { "id": "uuid", "type": "exec", "payload": { "action":"analyze", "args": {...} } }
  ```
* **Response envelope:** must include the original `id` and a `status`:

  ```json
  { "id":"uuid", "status":"ok", "code":0, "body": {...} }
  ```
* **Required behaviors:**

  * Echo `id` in responses.
  * Emit exactly one response per request id.
  * Validate payloads and reject oversized lines (configurable limit).
  * Support at minimum `health`, `exec`, and `shutdown` request types.

Refer to `plugins/common/protocol.md` for full spec and examples.

---

## Configuration & environment variables

Recommend supporting these env vars:

| Variable                   |            Default | Purpose                                         |
| -------------------------- | -----------------: | ----------------------------------------------- |
| `OMNIFLOW_PLUGIN_MAX_LINE` |           `131072` | Max bytes per NDJSON line to defend against DoS |
| `OMNIFLOW_EXEC_TIMEOUT`    |               `10` | Per-request execution timeout (seconds)         |
| `OMNIFLOW_LOG_JSON`        |            `false` | If `true`, emit structured JSON logs to stderr  |
| `JVM_OPTS`                 | `-Xms64m -Xmx256m` | JVM tuning for container runtime                |

Document any additional plugin-specific envs in code and README.

---

## Security & hardening guidance

* **Least privilege:** run as a non-root user (Dockerfile already does).
* **Resource limits:** enforce CPU/memory limits in containers.
* **Input bounds:** enforce `OMNIFLOW_PLUGIN_MAX_LINE` before parsing.
* **Dependency hygiene:** pin dependencies, produce SBOM (Syft/CycloneDX) and run SCA (Trivy/Dependabot).
* **Memory safety:** while Kotlin/JVM is memory-safe, guard against unbounded allocations. Use coroutines and structured concurrency to bound concurrency.
* **Secrets:** do not store secrets in the repository. Use secret stores (Vault, Kubernetes Secrets).
* **Signing:** sign release artifacts (GPG or cosign) and publish checksums.

---

## Releasing & artifacts

A release for the plugin should include:

* Stripped fat-JAR (runtime artifact).
* Separate debug symbol artifact if needed (keep unstripped binary for debugging).
* SBOM (`spdx.json` or `cyclonedx.xml`).
* `sha256sum` and `sha512sum`.
* GPG / cosign signatures for artifacts.
* `release_metadata.json` with `version`, `vcs_ref`, `build_date`, and artifact list.
* `CHANGELOG.md` capturing notable changes.

Use the Gradle `maven-publish` + `signing` configuration in CI to produce signed artifacts and publish to your registry.

---

## Troubleshooting

* **Plugin not responding:** verify process is reading stdin and not waiting for interactive input. Check `stderr` for startup logs.
* **Healthcheck failing in container:** run the health probe manually inside container to reproduce.
* **Malformed JSON crashes:** run integration tests and static analyzers; ensure parser is defensive.
* **High memory usage:** lower concurrency, set JVM_OPTS memory limits, profile heap with async-profiler.

---

## Contributing

* Fork the repo and use feature branches.
* Run `./gradlew ktlintFormat` and `./gradlew test` before opening PR.
* Write unit and integration tests for new features.
* Keep `plugins/common/*` protocol files in sync when changing API.

---

## License & attribution

This module is licensed under **Apache License 2.0**. See the `LICENSE` file for full details.

Maintained by TheSkiF4er / OmniFlow. For questions or help, open an issue or contact `maintainers@cajeer.com`.
