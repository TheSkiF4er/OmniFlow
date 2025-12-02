# OmniFlow C++ Plugin — `plugins/cpp/`

**Production-ready README** for the OmniFlow C++ plugin. This document explains the purpose, build & test instructions, runtime contract, security guidance, packaging and release best practices. Drop this `README.md` into `OmniFlow/plugins/cpp/` before releasing.

---

## Table of contents

* [Overview](#overview)
* [Repository layout](#repository-layout)
* [Requirements & supported platforms](#requirements--supported-platforms)
* [Build (recommended: CMake)](#build-recommended-cmake)
* [Makefile fallback (direct build)](#makefile-fallback-direct-build)
* [Docker image (builder → runtime)](#docker-image-builder--runtime)
* [Run & quick examples](#run--quick-examples)
* [Tests & CI recommendations](#tests--ci-recommendations)
* [Plugin protocol summary (NDJSON)](#plugin-protocol-summary-ndjson)
* [Configuration & environment variables](#configuration--environment-variables)
* [Security & hardening guidance](#security--hardening-guidance)
* [Packaging & release artifacts](#packaging--release-artifacts)
* [Troubleshooting](#troubleshooting)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

This directory contains the reference C++ plugin for OmniFlow. The plugin implements the newline-delimited JSON (NDJSON) plugin contract used by OmniFlow hosts: it receives single-line JSON requests on `stdin` and emits single-line JSON responses on `stdout`. It is designed to be:

* **Portable:** buildable with CMake or Makefile, runs on Linux containers and hosts.
* **Secure:** defaults favor least privilege; README recommends sandboxing, input bounds and ASAN in CI.
* **Tested:** unit and integration test harnesses included.
* **Reproducible:** supports build metadata (VCS_REF, BUILD_DATE) and Docker multi-stage build.

---

## Repository layout

```
plugins/cpp/
├── CMakeLists.txt            # CMake build configuration (recommended)
├── Makefile                  # Convenience build tasks (fallback)
├── Dockerfile                # Multi-stage builder → minimal runtime
├── README.md                 # (this file)
├── sample_plugin.cpp         # main plugin source (example name)
├── include/                  # public headers (if any)
├── third_party/
│   └── nlohmann/json.hpp     # minimal vendored JSON (or upstream)
├── tests/
│   ├── unit/                 # GoogleTest unit tests (C++)
│   └── integration/          # integration scripts (bash)
└── LICENSE
```

> Adjust filenames to match your codebase — the Makefile and Dockerfile try to detect common locations.

---

## Requirements & supported platforms

* **Build host:** Linux (Debian/Ubuntu recommended) or macOS for dev. CI: `ubuntu-latest`.
* **Tooling:** `cmake >= 3.16`, `gcc`/`g++` or `clang`, `make`, `git`, `pkg-config`.
* **Optional tools (CI/QA):** `clang-format`, `clang-tidy`, `AddressSanitizer` (compiler support), `valgrind`, `gtest`.
* **Runtime:** glibc-compatible Linux (for static, adapt base image). Distroless images are recommended for smallest surface.

---

## Build (recommended: CMake)

The project ships with a production-ready `CMakeLists.txt`. CMake is preferred because it supports tests, packaging and reproducible flags.

```bash
# from repo root
cd plugins/cpp
mkdir -p build && cd build

# Release build (default)
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -- -j$(nproc)

# Debug build
cmake .. -DCMAKE_BUILD_TYPE=Debug
cmake --build . -- -j$(nproc)

# Enable sanitizers for CI (do not use ASAN builds in production runtime)
cmake .. -DCMAKE_BUILD_TYPE=Debug -DENABLE_ASAN=ON
cmake --build . -- -j$(nproc)
```

Output binary location: `build/bin/` (or `build/<PLUGIN_NAME>` depending on CMake config). The provided `CMakeLists.txt` installs to `bin/` when `make install` is used.

---

## Makefile fallback (direct build)

If you prefer `make`:

```bash
cd plugins/cpp

# Release build (default)
make

# Debug
make BUILD_TYPE=Debug

# ASAN build for QA
make ENABLE_ASAN=1

# Install
make install PREFIX=/usr/local
```

This Makefile will prefer CMake when `CMakeLists.txt` present; otherwise it compiles sources directly.

---

## Docker image (builder → runtime)

A multi-stage Dockerfile builds with CMake in a builder stage and copies the binary into a minimal runtime image. Example build commands:

```bash
# Release image
docker build -t omniflow/plugin-cpp:1.2.3 -f plugins/cpp/Dockerfile .

# ASAN debug image for CI
docker build --build-arg ENABLE_ASAN=1 --build-arg BUILD_TYPE=Debug \
  -t omniflow/plugin-cpp:asan -f plugins/cpp/Dockerfile .
```

Recommended runtime image properties:

* Run as non-root user `omniflow`.
* Healthcheck: sends a health NDJSON probe to the binary and expects an OK response.
* Strip binary on production images (keep debug symbols separately in CI artifacts).

For smallest runtime, consider `gcr.io/distroless/cc` or similar, verifying glibc vs musl compatibility.

---

## Run & quick examples

Run locally (binary reads NDJSON from stdin):

```bash
# Interactive
./build/bin/omni_plugin_cpp

# One-shot message
echo '{"id":"1","type":"health","payload":null}' | ./build/bin/omni_plugin_cpp

# Send exec request example
echo '{"id":"x1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}' \
  | ./build/bin/omni_plugin_cpp
```

Logs: plugin should write structured or human logs to `stderr`; `stdout` is reserved for single-line JSON responses.

---

## Tests & CI recommendations

* **Unit tests:** use GoogleTest. The `CMakeLists.txt` fetches or uses installed gtest and wires `tests/unit/*.cpp`.
* **Integration tests:** shell harnesses in `tests/integration/*.sh` run the binary via FIFO and validate responses using `jq`.
* **Sanitizers:** run ASAN/UBSAN builds in CI and fail on findings.
* **Fuzzing:** add libFuzzer or AFL tests for parser robustness (malformed escapes, oversized lines).
* **SBOM & SCA:** generate SBOMs (e.g., `syft`/`cyclonedx`) and run SCA tools (e.g., `trivy`) against artifacts in CI.

Example GitHub Actions matrix suggestion:

* job: build-release → build & run integration tests
* job: build-asan → build with `ENABLE_ASAN=1` and run tests (fail on sanitizer output)
* job: package → produce tar.gz, SBOM, checksums, sign artifacts

---

## Plugin protocol summary (NDJSON)

Plugins implement the OmniFlow host contract:

* **Framing:** newline-delimited single-line JSON objects (one request per line, one response per line).
* **Host → Plugin fields:** `{ "id": string, "type": string, "timestamp": string?, "payload": object|null }`
* **Plugin → Host response:** `{ "id": string, "status": "ok"|"error"|"busy", "code": int?, "message": string?, "body": object|null, "meta": object? }`

Required behaviors:

* Echo the same `id` in the response.
* Emit exactly one response per request `id`.
* Validate/limit input size (`OMNIFLOW_PLUGIN_MAX_LINE`, default 131072 bytes).
* Support `health`, `exec`, `shutdown` request types at minimum.

See `plugins/common/plugin-api.md` and `plugins/common/protocol.md` for full spec and examples.

---

## Configuration & environment variables

The plugin respects the following environment variables (defaults recommended):

| Variable                    |  Default | Description                                            |
| --------------------------- | -------: | ------------------------------------------------------ |
| `OMNIFLOW_PLUGIN_MAX_LINE`  | `131072` | Max single-line request size in bytes                  |
| `OMNIFLOW_EXEC_TIMEOUT`     |     `10` | Execution timeout (seconds) for `exec` actions         |
| `OMNIFLOW_PLUGIN_HEARTBEAT` |      `5` | Interval (sec) for internal heartbeat (if implemented) |
| `OMNIFLOW_LOG_JSON`         |  `false` | If `true`, logs to `stderr` must be JSON lines         |
| `OMNIFLOW_PLUGIN_DEBUG`     |    unset | If set, enable verbose debugging                       |

Set these via container `environment:` or process env when launching.

---

## Security & hardening guidance

Follow these best practices for production releases:

* **Least privilege:** run plugin as non-root user, drop capabilities, set `no-new-privileges`.
* **Sandboxing:** default to no network access; allow network only when necessary and vetted.
* **Input bounds:** enforce `OMNIFLOW_PLUGIN_MAX_LINE` before parsing to prevent memory exhaustion DoS.
* **Dependency hygiene:** vendor or pin third-party libs; include SBOM in release.
* **Memory safety:** run ASan/UBSan and fuzz tests in CI.
* **Signing:** sign artifacts (GPG detached signatures or cosign) and publish checksums (`sha256sum`, `sha512sum`).
* **Secrets:** do not store secrets in repo; inject at runtime via secret managers or mounted files with strict permissions.

---

## Packaging & release artifacts

A release should include for each compiled target/platform:

* Binary artifact (stripped).
* Separate debug symbol file (unstripped) for debugging.
* `sha256sum.txt` and `sha512sum.txt` for artifacts.
* GPG detached signatures (or cosign attestations).
* SBOM (`spdx.json` or `cyclonedx.xml`).
* `release_metadata.json` containing: `version`, `vcs_ref`, `build_date`, `artifacts`.
* `README.md`, `LICENSE`, `SECURITY.md` (at repo root and/or plugin folder).

Use CI to produce platform artifacts and attach to GitHub Releases or artifact storage (S3/GCS).

---

## Troubleshooting

* **No response:** ensure plugin is running and not blocked reading stdin; check `stderr` logs.
* **Crashes on malformed input:** run ASAN locally to find memory errors or use the integration test harness.
* **Healthcheck failing in container:** run the same `echo '{...}' | /path/to/binary` inside container to reproduce.
* **Large-memory usage:** check for unbounded allocations; enforce input size limits and add guardrails.

---

## Contributing

* Fork the repo, create feature branches, and open PRs to the main repo.
* Add unit + integration tests for new behavior.
* Run ASAN/UBSan locally and ensure CI passes.
* When updating vendored third-party code, include upstream reference and reason in the PR.

---

## License & attribution

* **Plugin code:** Apache-2.0 (project default) — include full LICENSE in release artifacts.
* **Vendored third-party libraries:** preserve upstream license texts in `third_party/` directories (e.g., MIT for `nlohmann/json` or cJSON). Include these license files in the release bundle.
