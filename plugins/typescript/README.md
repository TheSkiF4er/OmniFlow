# OmniFlow TypeScript Plugin — `plugins/typescript/README.md`

**Production-ready README** for the OmniFlow TypeScript plugin. Drop this file into `OmniFlow/plugins/typescript/`. It’s written for maintainers, plugin authors, CI engineers and operators and contains everything needed to build, test, containerize, and securely operate the TypeScript plugin runtime.

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
* [TypeScript / linting / formatting](#typescript--linting--formatting)
* [Docker & container usage](#docker--container-usage)
* [Configuration & environment variables](#configuration--environment-variables)
* [Security & hardening guidance](#security--hardening-guidance)
* [CI / Release recommendations](#ci--release-recommendations)
* [Troubleshooting](#troubleshooting)
* [Contributing](#contributing)
* [License & attribution](#license--attribution)

---

## Overview

This folder contains a production-grade TypeScript implementation for OmniFlow plugins. The plugin reads and writes NDJSON (newline-delimited JSON) on `stdin`/`stdout` and demonstrates robust protocol handling, Unicode-safety, bounded input parsing, graceful shutdown, and sensible defaults for containerized deployment.

The code here is intended as a reference implementation and a template you can adapt for your own TypeScript/Node.js OmniFlow plugins.

---

## Key features

* NDJSON framing helpers and strictly single-line JSON responses.
* Example actions: `health`, `exec` (with `echo`, `reverse`, `compute`), `shutdown`.
* TypeScript-first codebase with `tsc` build and typings.
* Jest test suite (unit + optional integration).
* ESLint + Prettier configuration for consistent style.
* Multi-stage Dockerfile producing a minimal non-root runtime image.
* CI-friendly scripts and publish-ready `package.json` config.

---

## Repository layout

```
plugins/typescript/
├── tests/                     # Jest integration/unit tests
    └── sample_plugin.spec.ts
├── package.json               # npm metadata & scripts
├── package-lock.json          # (recommended) lockfile for reproducible installs
├── tsconfig.json              # TypeScript config
├── .eslintrc.js               # ESLint config
├── .prettierrc                # Prettier config
├── Dockerfile                 # Multi-stage production Dockerfile
└── README.md                  # (this file)
```

---

## Requirements

* **Node.js:** `>=18` (Node 20 LTS recommended).
* **npm:** `>=9` (or use `pnpm`/`yarn` if you adapt scripts).
* **TypeScript toolchain:** installed via `devDependencies` (tsc, ts-node).
* **Optional:** Docker to build container images.

---

## Quick start (build & run)

Install dependencies and build:

```bash
cd plugins/typescript
npm ci           # preferred for CI/reproducible installs
npm run build    # compiles TypeScript into dist/
```

Run a one-line health probe against the compiled plugin:

```bash
echo '{"id":"hc-1","type":"health","payload":null}' | node dist/sample_plugin.js
```

If you prefer to run without building (dev mode):

```bash
# requires ts-node installed (devDependency)
npm run dev
# then send NDJSON to the running process as above
```

---

## NDJSON protocol summary

OmniFlow plugin protocol is intentionally simple and language-agnostic:

* **Framing:** NDJSON — exactly one JSON object per newline (no multi-line JSON).
* **Request envelope:** must include `id` (string) and `type` (string). Example:

  ```json
  {"id":"uuid-1","type":"exec","payload":{"action":"echo","args":{"message":"hello"}}}
  ```
* **Response envelope:** must echo the same `id` and include `status` (`ok` | `error` | `busy`), optional `code`, `message`, and `body`:

  ```json
  {"id":"uuid-1","status":"ok","code":0,"body":{"action":"echo","message":"hello"}}
  ```
* **Minimum behavior:** plugin must handle `health`, `exec`, and `shutdown` requests, enforce a configurable max-line byte guard before parsing, and not crash on malformed JSON.

For full protocol details and examples see `plugins/common/protocol.md` in the repository.

---

## Development workflow

Recommended local workflow:

```bash
# install deps
cd plugins/typescript
npm ci

# format and lint before commit
npm run format
npm run lint

# run tests
npm test

# build for release
npm run build
```

Use the provided npm scripts (`build`, `test`, `lint`, `format`, `start`) for consistent behavior across dev machines and CI.

---

## Testing

Unit + integration tests use Jest. Tests live under `plugins/typescript/tests/`.

Run tests:

```bash
cd plugins/typescript
npm test
```

Notes:

* Integration tests that spawn a plugin process will attempt to use `dist/sample_plugin.js` (built output) or run via `ts-node` in dev mode.
* Tests are designed with deterministic timeouts and clear failure output for CI.

---

## TypeScript / linting / formatting

We recommend these configs which are already included:

* **ESLint** with `@typescript-eslint` plugin for static analysis (`.eslintrc.js`).
* **Prettier** for formatting (`.prettierrc`).
* **tsconfig.json** tuned for library code with `declaration` and `sourceMap` enabled.

Run linters and formatters:

```bash
npm run lint        # fails on warnings (CI-friendly)
npm run lint:fix    # auto-fix where possible
npm run format
```

Keep `skipLibCheck: true` in `tsconfig.json` to reduce CI friction, but consider enabling stricter checks for core library code.

---

## Docker & container usage

A production-ready multi-stage `Dockerfile` is included at `plugins/typescript/Dockerfile`. It compiles TypeScript inside the builder stage and produces a small runtime image that:

* runs the compiled JS as a non-root user,
* includes a `HEALTHCHECK` that sends an NDJSON health probe, and
* includes SBOM-friendly labels.

Build image example:

```bash
# from repository root
docker build \
  --file plugins/typescript/Dockerfile \
  --build-arg VERSION=1.0.0 \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t omniflow/plugin-typescript:1.0.0 .
```

Run container (secure flags recommended):

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  omniflow/plugin-typescript:1.0.0
```

---

## Configuration & environment variables

Implement and honor the following environment variables in your plugin to make it manageable in production:

| Variable                   |  Default | Description                                               |
| -------------------------- | -------: | --------------------------------------------------------- |
| `OMNIFLOW_PLUGIN_MAX_LINE` | `131072` | Max bytes per NDJSON input line (guard against DoS)       |
| `OMNIFLOW_EXEC_TIMEOUT`    |     `10` | Per-request timeout (seconds) for long-running operations |
| `OMNIFLOW_LOG_JSON`        |  `false` | When `true`, emit structured JSON logs to `stderr`        |
| `PLUGIN_LOG_LEVEL`         |   `info` | Log level (`debug`, `info`, `warn`, `error`)              |

Expose additional env vars as needed for configuration, but avoid embedding secrets in environment or code—prefer secret mounts or dedicated secret stores.

---

## Security & hardening guidance

Follow these best practices for production releases:

* **Least privilege:** container runs as non-root (Dockerfile sets non-root user).
* **Read-only filesystem:** use `--read-only` and mount writable directories explicitly.
* **Drop capabilities:** run with `--cap-drop ALL` and `--security-opt no-new-privileges`.
* **Input bounds:** enforce `OMNIFLOW_PLUGIN_MAX_LINE` before JSON parsing.
* **Dependency hygiene:** pin dependencies (commit `package-lock.json`), run SCA scanners (e.g., `npm audit`, `trivy`) and include SBOMs in releases.
* **Secret handling:** do not store secrets in the repo; inject them at runtime via secure mechanisms.
* **Artifact signing:** sign release artifacts and container images (cosign, GPG).

---

## CI / Release recommendations

Example CI release pipeline:

1. `npm ci` (or `npm ci --prefer-offline`)
2. `npm run lint` && `npm test`
3. `npm run build`
4. Build Docker image and run containerized integration tests
5. Generate SBOM (Syft), create checksums and sign artifacts (cosign/GPG)
6. Publish image to registry and package to artifact storage

Cache `node_modules` and npm cache between CI runs to accelerate builds.

---

## Troubleshooting

* **No response from plugin:** ensure the process reads from `stdin` and flushes `stdout` one line per JSON object. Run:

  ```bash
  echo '{"id":"hc","type":"health","payload":null}' | node dist/sample_plugin.js
  ```

  Inspect `stderr` for startup errors.

* **Plugin crashes on malformed JSON:** parser must catch JSON parse errors and return a structured `error` response instead of exiting. Unit-test parsers to verify behavior.

* **Healthcheck failing in container:** run the health probe inside the container to capture output:

  ```bash
  docker exec -it <container> sh -c "printf '%s\n' '{\"id\":\"hc\",\"type\":\"health\",\"payload\":null}' | node /opt/omniflow/plugins/typescript/dist/sample_plugin.js"
  ```

* **Permission errors in mounted volumes:** ensure the non-root user in the container owns mounted paths or use group mappings.

---

## Contributing

Contributions are welcome. Suggested process:

1. Fork → branch → implement → add tests.
2. Run `npm run lint`, `npm test` and `npm run build` locally.
3. Open a PR with clear description and CI status.
4. Follow semantic versioning and keep `CHANGELOG.md` updated.

Use Conventional Commits (`feat:`, `fix:`, `chore:`) to help automation and changelog generation.

---

## License & attribution

This code and supporting files are licensed under **Apache License 2.0**. See `LICENSE` at the repository root for full text.

Maintained by **TheSkiF4er / OmniFlow**. For questions or issues open an issue in the upstream repository.
