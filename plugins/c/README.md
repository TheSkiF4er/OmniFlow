# OmniFlow C Plugin — `plugins/c/`

**Production-ready `README.md` for `OmniFlow/plugins/c`** — ready for release (English).

This README documents how to build, test, run, package and securely operate the C plugin included in `OmniFlow/plugins/c`. It contains recommended CI steps, security guidance, runtime contract and troubleshooting tips.

---

## Table of contents

* [Overview](#overview)
* [Contents of this directory](#contents-of-this-directory)
* [Requirements & supported platforms](#requirements--supported-platforms)
* [Build (local)](#build-local)
* [Build variants & flags](#build-variants--flags)
* [Docker image (builder + runtime)](#docker-image-builder--runtime)
* [Run & usage examples](#run--usage-examples)
* [Plugin protocol (stdin/stdout JSON contract)](#plugin-protocol-stdinstdout-json-contract)
* [Configuration (environment variables)](#configuration-environment-variables)
* [Testing & CI recommendations](#testing--ci-recommendations)
* [Security & hardening](#security--hardening)
* [Packaging & releases](#packaging--releases)
* [Vendorized dependencies](#vendorized-dependencies)
* [Troubleshooting & debugging](#troubleshooting--debugging)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

This directory contains a production-ready native C plugin for OmniFlow. The plugin implements a small, safe JSON-over-stdin/stdout protocol for communicating with the OmniFlow host. The implementation is designed for reliability, portability and security.

Key qualities:

* Minimal dependencies (only `vendor/cJSON` included)
* Strict input size enforcement and robust parsing
* Graceful shutdown and signal handling
* Optional AddressSanitizer support for CI/QA
* Docker multi-stage build for reproducible artifacts

---

## Contents of this directory

```
plugins/c/
├── sample_plugin.c             # Plugin source (entrypoint)
├── Makefile                    # Build, test, package (production-ready)
├── Dockerfile                  # Multi-stage builder + runtime
├── README.md                   # (this file)
├── vendor/
│   └── cJSON/
│       ├── cJSON.c
│       ├── cJSON.h
│       └── README.md
└── tests/
    └── test_sample_plugin.sh   # Integration test harness
```

> If your repository layout differs, adapt the paths in Makefile and Dockerfile accordingly.

---

## Requirements & supported platforms

* Build host: Linux (Ubuntu/Debian recommended) or macOS for local dev. CI runners supported: `ubuntu-latest`, `macos-latest`, `windows-latest` (MSVC).
* Tooling (for build & CI): `gcc` or `clang`, `make`, `cmake` (if used), `tar`, `gzip`, `jq` (tests), optional: `clang-format`, `clang-tidy`, `valgrind`.
* Runtime: Standard Linux distribution (glibc-compatible). For minimal images, use distroless or alpine (verify musl vs glibc compatibility).

---

## Build (local)

Default release build (recommended):

```bash
cd plugins/c
make
# result: build/sample_plugin (or build/<BIN> depending on Makefile variables)
```

Debug build:

```bash
make BUILD_TYPE=Debug
```

AddressSanitizer build (for CI/QA):

```bash
make ENABLE_ASAN=1 BUILD_TYPE=Debug
```

Install to system prefix:

```bash
make install PREFIX=/usr/local
# or use DESTDIR for packaging:
make install PREFIX=/usr/local DESTDIR=/tmp/package-root
```

Create a distributable tarball:

```bash
make dist VERSION=v1.2.3
# outputs: omniflow-plugin-c-v1.2.3.tar.gz
```

### Notes

* Variables can be overridden on the command line or environment: `CC`, `CFLAGS`, `BUILD_TYPE`, `ENABLE_ASAN`, `BIN`, `OUTDIR`, `PREFIX`.
* The Makefile uses `vendor/cJSON` by default — do not remove it unless replacing with another JSON library.

---

## Build variants & flags

* **Release**: `-O2 -march=native -fstack-protector-strong -D_FORTIFY_SOURCE=2` (strip optional).
* **Debug**: `-g -O0 -DDEBUG` for easier debugging.
* **ASAN**: `-fsanitize=address,undefined` (use in CI only — not for production runtime).
* CI should perform both Release and ASAN builds:

  * Release → functional/integration tests
  * ASAN → memory error detection

---

## Docker image (builder + runtime)

A multi-stage `Dockerfile` is provided to build the plugin and produce a small runtime image. Example build (release):

```bash
# from repo root
docker build -t omniflow/plugin-c:1.2.3 -f plugins/c/Dockerfile plugins/c
```

Build an ASAN debug image for QA:

```bash
docker build --build-arg ENABLE_ASAN=1 --build-arg BUILD_TYPE=Debug -t omniflow/plugin-c:asan -f plugins/c/Dockerfile plugins/c
```

Runtime image:

* Non-root user `omniflow`
* Healthcheck that performs a `{"id":"hc","type":"health"}` probe
* `ENTRYPOINT` runs the plugin binary reading from `stdin`

**Tip:** for reproducible builds pass:

```bash
--build-arg BUILD_DATE="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
--build-arg VCS_REF=$(git rev-parse --short HEAD)
```

---

## Run & usage examples

The plugin uses newline-delimited JSON messages on `stdin` and writes newline-delimited JSON responses to `stdout`.

Simple direct run:

```bash
cd plugins/c
./build/sample_plugin
# type messages, e.g. {"id":"1","type":"health"}<ENTER>
```

Send one-off message:

```bash
echo '{"id":"1","type":"health"}' | ./build/sample_plugin
```

Run in Docker (interactive):

```bash
docker run --rm -i omniflow/plugin-c:1.2.3 <<'JSON'
{"id":"1","type":"health"}
JSON
```

---

## Plugin protocol (stdin/stdout JSON contract)

The plugin follows a simple host -> plugin and plugin -> host contract using newline-delimited JSON:

**Host → Plugin (requests)**

```json
{ "id": "uuid-or-string", "type": "health|exec|shutdown", "payload": { ... } }
```

**Plugin → Host (responses)**

```json
{ "id": "same-id", "status": "ok|error", "code": <int?>, "message": "optional", "body": { ... } }
```

Common `exec` actions implemented:

* `echo` — returns same message
* `reverse` — returns reversed Unicode-safe string
* `compute` — expects `{"numbers":[...]} ` and returns sum `{ "sum": <number> }`

`health` → plugin responds with `status: ok` and `body: { "status": "healthy", "version": "x.y.z" }`.

`shutdown` → plugin responds `{ "result": "shutting_down" }` and exits gracefully.

---

## Configuration (environment variables)

Plugin respects environment variables to control runtime behaviour:

| Variable                    |  Default | Purpose                                                     |
| --------------------------- | -------: | ----------------------------------------------------------- |
| `OMNIFLOW_PLUGIN_MAX_LINE`  | `131072` | Max length in bytes of an incoming message (DoS protection) |
| `OMNIFLOW_PLUGIN_HEARTBEAT` |      `5` | Background heartbeat interval (seconds)                     |
| `OMNIFLOW_LOG_JSON`         |    unset | If set, logs to stderr as JSON objects                      |
| `OMNIFLOW_EXEC_TIMEOUT`     |     `10` | Execution timeout (seconds) for `exec` actions              |
| `OMNIFLOW_PLUGIN_DEBUG`     |    unset | Enable debug logs if set                                    |

Set these variables in the host environment or container for tuning.

---

## Testing & CI recommendations

* **Unit & integration tests**:

  * Use `plugins/c/tests/test_sample_plugin.sh` for integration. It:

    * builds plugin
    * runs plugin with FIFO stdin capture
    * sends messages (health/exec/reverse/compute/invalid/oversize/shutdown)
    * checks JSON responses using `jq`
* **Static analysis & formatting**:

  * `clang-format` for style; `clang-tidy` for checks.
* **Sanitizers**:

  * Run ASAN/UBSAN builds in CI and fail on findings.
* **Fuzzing**:

  * Add small fuzzers (libFuzzer/afl) to exercise parsing of malformed/huge JSON.
* **CI workflow**:

  * Matrix build for `Release` + `ASAN`
  * Run tests under both
  * Generate SBOM (e.g. `syft`) and SCA (e.g. `trivy`) for packaged artifacts
  * Sign artifacts in release pipeline (cosign/PGP)

Example CI steps (summary):

1. Checkout
2. Build (Release)
3. Run tests (integration)
4. Build (ASAN) → run tests → fail on errors
5. Package artifacts → generate checksums → sign → publish

---

## Security & hardening

* **Input length enforcement**: Always enforce `OMNIFLOW_PLUGIN_MAX_LINE` before parsing. The plugin itself enforces this limit; hosts should also limit size at transport layer.
* **No dynamic code execution**: The plugin does not use `system()`/`popen()` for processing payloads. If you add such code, sandbox it.
* **Capabilities**: Run plugins with least privilege. In containers, drop capabilities and use read-only filesystem mounts where possible.
* **Signing & reproducibility**: Sign all release artifacts (PGP or cosign). Record `VCS_REF`, build environment and SBOM in the release metadata.
* **Memory safety**: Use ASAN in CI and perform valgrind runs periodically.
* **Dependency handling**: `vendor/cJSON` is included and audited; update from upstream occasionally and run SCA scans.

---

## Packaging & releases

* Use `make dist` to create a tarball with metadata (`release_metadata.txt`).
* In CI release pipeline:

  * aggregate build artifacts for all target platforms
  * produce `sha256sum.txt` and `sha512sum.txt`
  * sign the checksum files with PGP and/or cosign (keyless OIDC recommended)
  * publish to GitHub Releases and/or artifact storage (S3/GCS)
  * update `manifests/index.json` in the `plugins/cpp-packages/releases/` or similar repo path

---

## Vendorized dependencies

* `vendor/cJSON/` — compact, hardened subset of cJSON (MIT). See `vendor/cJSON/README.md` for usage, license and examples.
* When updating vendor code, preserve attribution and include license file in any distributed package.

---

## Troubleshooting & debugging

* **No response from plugin**: ensure plugin is running and not blocked by a large synchronous write — try small `echo '{"id":"x","type":"health"}' | ./sample_plugin`.
* **Plugin crashes under malformed input**: run under ASAN or valgrind to see memory errors; check `stderr` logs (structured logs recommended).
* **Timeouts on `exec`**: tune `OMNIFLOW_EXEC_TIMEOUT`. For long-running tasks, consider moving work to an external worker process/sandbox.
* **Docker healthcheck failing**: healthcheck sends a health message on stdin and expects an OK response; adapt healthcheck if plugin interface changed.

---

## Contributing

* Follow repository contributing guidelines (see top-level `CONTRIBUTING.md`).
* Tests are required for any behavior change. If adding new `exec` actions, add unit + integration tests and docs.
* For changes to vendorized code (`vendor/cJSON`), include upstream reference and reason for change, and run full CI (including ASAN) before merging.

---

## License & attribution

* **Plugin code:** Apache-2.0 (project default) — include repository LICENSE.
* **Vendor cJSON:** MIT — included inside `vendor/cJSON/cJSON.c` and `vendor/cJSON/README.md`. Any distribution must include the MIT license and attribution.

---

## Contact & support

For questions, issues or PRs, open them against the main repository: **TheSkiF4er/OmniFlow** and add label `area:plugins/c` for faster triage.

---

### Quick references

* Build: `make`
* ASAN build: `make ENABLE_ASAN=1`
* Test: `make test` (runs `tests/test_sample_plugin.sh` if present)
* Docker build: `docker build -t omniflow/plugin-c:tag -f plugins/c/Dockerfile plugins/c`
* Release tarball: `make dist VERSION=vX.Y.Z`
