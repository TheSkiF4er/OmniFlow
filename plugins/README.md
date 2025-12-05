# OmniFlow — `plugins/` README

**OmniFlow Plugins — `plugins/`**
Production-ready, release-quality root README for the `plugins/` workspace. Drop this file at `OmniFlow/plugins/` — it documents repository layout, the NDJSON plugin protocol, how to build/test/package language plugins, CI & release best practices, security and operations guidance, and contributor instructions for adding new plugins.

> Audience: plugin authors, integrators, release engineers, maintainers, and operators.

---

## Table of contents

* [What this folder is](#what-this-folder-is)
* [Supported plugin runtimes](#supported-plugin-runtimes)
* [Plugin protocol (NDJSON) — quick summary](#plugin-protocol-ndjson---quick-summary)
* [Repository layout (convention)](#repository-layout-convention)
* [How to add a new plugin](#how-to-add-a-new-plugin)
* [Common development commands](#common-development-commands)
* [Testing strategy (unit & integration)](#testing-strategy-unit--integration)
* [CI / Release checklist (recommended)](#ci--release-checklist-recommended)
* [Container image best practices](#container-image-best-practices)
* [Security & hardening guidance](#security--hardening-guidance)
* [SBOMs, signing & provenance](#sboms-signing--provenance)
* [Troubleshooting & diagnostics](#troubleshooting--diagnostics)
* [Contributing & governance](#contributing--governance)
* [Useful templates & helper files](#useful-templates--helper-files)
* [Contacts & support](#contacts--support)

---

## What this folder is

`plugins/` is the collection of language-specific OmniFlow plugin templates, examples, tests and Docker images that demonstrate how to implement a production-ready OmniFlow plugin for a particular language/runtime. Each plugin directory is intended to be self-contained and releaseable as:

* a small language package (where applicable), and
* a secure, reproducible container image that implements the OmniFlow NDJSON plugin protocol on `stdin`/`stdout`.

The goal: make it trivial for developers to create new plugins and for operators to run them securely in CI or production.

---

## Supported plugin runtimes

Each subfolder under `plugins/` is a first-class plugin scaffold. Typical directories included:

* `plugins/php/` — PHP plugin template (composer, Dockerfile, tests)
* `plugins/python/` — Python plugin template (poetry/requirements, Dockerfile, tests)
* `plugins/go/` — Go plugin template (go.mod, Makefile, Dockerfile, tests)
* `plugins/typescript/` — TypeScript/Node plugin template (tsconfig, package.json, Dockerfile, tests)
* `plugins/ruby/` — Ruby plugin template (Gemfile, Dockerfile, tests)
* `plugins/common/` — shared docs (protocol.md, plugin-api.md), templates and examples

If you add a new language, follow the conventions in [How to add a new plugin](#how-to-add-a-new-plugin).

---

## Plugin protocol (NDJSON) — quick summary

All plugins must implement the same minimal protocol so the OmniFlow host can interact with them reliably.

* **Transport:** NDJSON over `stdin` / `stdout` — exactly one JSON object per newline.
* **Request envelope:** must include `id` (string) and `type` (string). Common types: `health`, `exec`, `shutdown`. Example:

  ```json
  {"id":"uuid-1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}
  ```
* **Response envelope:** must echo `id` and include `status` (`ok` | `error` | `busy`), optional `code`, `message`, and `body`. Example:

  ```json
  {"id":"uuid-1","status":"ok","code":0,"body":{"action":"echo","message":"hello"}}
  ```
* **Operational rules:**

  * enforce a byte-length max-line guard before parsing (recommend default `131072` bytes) to mitigate DoS; make it configurable via environment var `OMNIFLOW_PLUGIN_MAX_LINE`.
  * never crash on malformed JSON — return an error response and continue running.
  * respond with at most one response per `id`.
  * implement a `health` request that returns `ok` and small metadata (version, uptime).
  * support `shutdown` to exit cleanly.

Full spec & examples: `plugins/common/protocol.md`.

---

## Repository layout (convention)

Each plugin should follow a similar layout:

```
plugins/<language>/
├── tests/             # unit + integration tests
├── sample_plugin.*            # small example entrypoint
├── Dockerfile                 # production-ready multi-stage image
├── README.md                  # language-specific README
├── build tooling: Makefile/package.json/go.mod/composer.json
└── .github/workflows/         # optional plugin-specific CI
```

`plugins/common/` contains shared documentation, example requests/responses, CI templates and cross-language helpers.

---

## How to add a new plugin

1. **Create folder:** `plugins/<language>/`.
2. **Include README.md** (follow other readmes).
3. **Provide sample plugin:** a small `sample_plugin` entrypoint that implements NDJSON protocol. Keep it minimal but production-minded (timeouts, logging to `stderr`, input guards).
4. **Add tests:** unit tests for protocol helpers and an integration harness that spawns the plugin and validates NDJSON responses.
5. **Add Dockerfile:** multi-stage, non-root runtime, healthcheck, SBOM-friendly labels.
6. **Add CI workflow:** Lint → Test → Build → Image build → Integration tests → SBOM → Publish. Use `plugins/.github/workflows/` templates.
7. **Document env vars and config** and include `LICENSE` and attribution.
8. **Open PR** against `main`; ensure CI passes.

Checklist to include in PR description: tests added, README updated, Docker build reproducible, SBOM generation in CI, artifact signing plan.

---

## Common development commands

Replace `<lang>` with appropriate plugin folder.

* Run unit tests:

  * Python: `cd plugins/python && pytest`
  * Go: `cd plugins/go && go test ./...`
  * TypeScript: `cd plugins/typescript && npm ci && npm test`
  * PHP: `cd plugins/php && composer install && ./vendor/bin/phpunit`
  * Ruby: `cd plugins/ruby && bundle install && bundle exec rspec`
* Run integration harness (example): `bash plugins/<lang>/tests/integration_test.sh`
* Build Docker image (example):

  ```bash
  docker build -f plugins/<lang>/Dockerfile -t omniflow/plugin-<lang>:1.0.0 --build-arg VERSION=1.0.0 .
  ```
* Run plugin locally (NDJSON probe):

  ```bash
  echo '{"id":"hc","type":"health","payload":null}' | <plugin-cmd>
  ```

---

## Testing strategy (unit & integration)

* **Unit tests:** deterministic, fast, no network, validate parser, builders, action handlers.
* **Integration tests:** spawn the plugin binary/process, exchange NDJSON lines, validate responses. Use timeouts and log capturing; tests must not be flaky.
* **Containerized integration tests:** run the image in CI and perform the same NDJSON exchanges inside the container to match production behavior.
* **CI:** ensure both unit and integration tests pass on every PR.

---

## CI / Release checklist (recommended)

A good CI pipeline should run:

1. Checkout + cache dependencies.
2. Lint + static analysis (language-specific).
3. Run unit tests with coverage.
4. Build plugin artifacts (binary / package / dist).
5. Build Docker image (multi-stage) and run containerized integration tests.
6. Produce SBOM (e.g., Syft), checksums, and sign artifacts (cosign / GPG).
7. Publish artifacts/images to registry only when signed and passing compliance checks.

Include gating for: license checks, SCA vulnerability thresholds, coverage thresholds (optional).

---

## Container image best practices

* Multi-stage build to keep runtime small.
* Run as non-root user.
* Install only runtime deps in final image.
* Use `HEALTHCHECK` that sends an NDJSON `health` probe to the plugin.
* Add labels for provenance: `org.opencontainers.image.*` including `version`, `revision`, `created`.
* Recommend runtime flags: `--read-only`, `--tmpfs /tmp:rw`, `--cap-drop ALL`, `--security-opt no-new-privileges`.
* Generate and attach an SBOM for each image.

---

## Security & hardening guidance

* Enforce a max-line limit (byte length) before parsing JSON to mitigate memory/CPU DoS.
* Validate input fields and types; never trust plugin callers.
* Use least privilege and drop Linux capabilities in containers.
* Use read-only rootfs and explicit writable mounts.
* Pin dependencies and commit lockfiles (where applicable).
* Run SCA (trivy, ossf-scanner) and scan images in CI.
* Rotate secrets and never embed them in the repo. Use secret managers.
* Sign release artifacts (GPG/cosign) and publish checksums.

If you discover a security issue, follow the procedure in `CONTRIBUTING.md` — do not open a public issue.

---

## SBOMs, signing & provenance

* Produce an SBOM (Syft, CycloneDX) for every release and attach it to the release artifacts.
* Sign images and artifacts with cosign/GPG; publish checksums (sha256, sha512).
* Store build metadata (`VERSION`, `VCS_REF`, `BUILD_DATE`) in image labels and in release metadata file (JSON).

---

## Troubleshooting & diagnostics

* If plugin doesn’t reply:

  * Ensure plugin reads `stdin` and flushes `stdout` per NDJSON line.
  * Re-run the health probe locally and inspect `stderr`.
* If plugin crashes on malformed JSON — fix parser to catch decode errors and continue.
* If healthcheck fails in container — exec into container, run the health probe manually and capture stdout/stderr.
* For permission issues — ensure non-root user owns mounted directories or use `chown` with init containers/volume mounts.

When opening an issue, include exact command used, logs (stderr/stdout), and a minimal repro.

---

## Contributing & governance

See `plugins/CONTRIBUTING.md` for contribution workflow, PR requirements, code of conduct, commit conventions and security disclosure. All PRs should include tests, follow style rules and be small & focused.

---

## Useful templates & helper files

You’ll find these helpful files under `plugins/` and `plugins/common/`:

* `plugins/common/protocol.md` — full protocol spec & examples
* `plugins/common/plugin-api.md` — common API and integration examples
* Language-specific README templates and Dockerfile examples
* CI workflow templates: `plugins/.github/workflows/*` (adapt per plugin)
* Test harness scripts: `plugins/*/tests/integration_test.sh` and language tests

---

## Contacts & support

* Repository maintainers: see `MAINTAINERS` or README root.
* Security contact: use the channel specified in `CONTRIBUTING.md` (private).
* For general issues: open a new issue in the repository (non-sensitive).
