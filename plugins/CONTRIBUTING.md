# OmniFlow Plugins — CONTRIBUTING.md

Thank you for contributing to **OmniFlow / plugins**!
This document explains how to contribute, what we expect from contributors, and the workflow we follow for issues, pull requests, tests, releases, and security reports. It’s written for maintainers, plugin authors and CI engineers — follow it to make reviews fast and predictable.

---

## Table of contents

* [Who can contribute](#who-can-contribute)
* [Code of conduct](#code-of-conduct)
* [Getting started (local setup)](#getting-started-local-setup)
* [Repository layout & scope](#repository-layout--scope)
* [Branching & workflow](#branching--workflow)
* [Issue guidelines](#issue-guidelines)
* [Pull request guidelines](#pull-request-guidelines)
* [Commit messages & changelog](#commit-messages--changelog)
* [Tests & CI expectations](#tests--ci-expectations)
* [Linting, formatting & static analysis](#linting-formatting--static-analysis)
* [Releases & publishing](#releases--publishing)
* [Security reporting & responsible disclosure](#security-reporting--responsible-disclosure)
* [Licensing & copyright](#licensing--copyright)
* [Developer tooling & helpers](#developer-tooling--helpers)
* [Getting help / contact](#getting-help--contact)

---

## Who can contribute

Everyone is welcome — individuals, organisations, and teams. We accept contributions in the form of:

* bug fixes and improvements
* new plugin templates or language runtimes following the OmniFlow NDJSON protocol
* tests, CI workflows and tooling improvements
* documentation and examples

Before large or disruptive work, open an issue to discuss the plan so maintainers can provide feedback early.

---

## Code of conduct

Be respectful. This repository follows the [Contributor Covenant code of conduct](https://www.contributor-covenant.org/) — treat all community members with respect and professionalism. Report violations by opening a confidential issue (if your platform supports it) or emailing the maintainers.

---

## Getting started (local setup)

1. Fork the repo and clone your fork:

   ```bash
   git clone https://github.com/TheSkiF4er/OmniFlow.git
   cd OmniFlow
   ```

2. Create a branch for your work:

   ```bash
   git checkout -b feat/<short-description>
   ```

3. Follow language-specific README files for per-plugin setup:

   * `plugins/php/README.md`
   * `plugins/python/README.md`
   * `plugins/go/README.md`
   * `plugins/typescript/README.md`
   * `plugins/ruby/README.md`

4. Run linters and tests locally before pushing (examples below).

Tips:

* Use a modern development environment (up-to-date Node / Python / Go / PHP / Ruby, and Docker).
* Install required tools listed in each plugin’s README (e.g., `composer`, `poetry`, `go`, `npm`, `bundle`).

---

## Repository layout & scope

This repository contains multiple plugin templates and shared plugin tooling under `plugins/`. Each plugin directory is intended to be a self-contained language runtime with:

```
plugins/<language>/
├── tests/                       # unit/integration tests
├── Dockerfile                   # production-ready multi-stage Dockerfile
├── README.md                    # language-specific instructions
└── packaging / CI manifests
```

Shared docs and protocol specs live under `plugins/common/`.

When adding a new plugin:

* Add a top-level `plugins/<language>/README.md` (follow existing templates).
* Provide a production-ready `Dockerfile`, `tests/`, a small example `sample_plugin` entrypoint and CI workflow.
* Include `LICENSE` and clearly attribute authorship.

---

## Branching & workflow

We use a simple, predictable branching model:

* `main` — stable, release-ready code (protected branch). All changes merged via PR.
* `develop` (optional) — integration branch for ongoing work (if used).
* Feature branches: `feat/<short>`, `fix/<short>`, `chore/<short>`, `docs/<short>`.

Pull requests should be opened against `main` (or `develop` if the project uses it). All PRs must:

* pass CI (lint + tests)
* include a descriptive title and summary
* reference the issue number when applicable

---

## Issue guidelines

When opening an issue, include:

* Clear, descriptive title.
* A short description of the problem or feature.
* Steps to reproduce (for bugs) or use cases (for features).
* Environment and versions (OS, language runtime versions, Docker image tags).
* Expected vs actual behavior.
* Attach logs / minimal repro code if possible.

Label your issue from the available set (bug, enhancement, discussion, question) or ask maintainers to help.

---

## Pull request guidelines

Make PRs easy to review:

* Keep PRs small and focused (one logical change per PR).
* Describe **what** you changed and **why** — include design decisions.
* Link to related issue(s) using `#<issue>` or `closes #<issue>`.
* Ensure tests are added/updated for new behaviour.
* Run `format`, `lint` and `test` locally — fix failures before opening PR.
* Use the repository’s code style and naming conventions.

PR checklist (maintainers may require this):

* [ ] Code builds and runs locally.
* [ ] Tests added or updated and passing.
* [ ] Linting and formatting applied.
* [ ] Documentation (README, examples) updated where applicable.
* [ ] Changelog entry (if large change).

---

## Commit messages & changelog

Use Conventional Commits for consistent change history:

```
feat(scope): short description
fix(scope): short description
chore: tooling or dependency changes
docs: documentation only changes
test: adding or correcting tests
refactor: code change that neither fixes bug nor adds feature
```

On release, maintainers will generate a `CHANGELOG.md` using conventional commit history (or you may include a suggested changelog entry in your PR).

---

## Tests & CI expectations

All contributions must include reasonable tests. The repository contains language-specific test harnesses:

* PHP: PHPUnit & shell integration scripts (`plugins/php/tests/`)
* Python: pytest (`plugins/python/tests/`)
* Go: `go test` and shell integration scripts (`plugins/go/tests/`)
* TypeScript: Jest (`plugins/typescript/tests/`)
* Ruby: RSpec & shell harnesses (`plugins/ruby/spec` / `plugins/ruby/tests/`)

CI will run the following steps (examples — see `.github/workflows`):

1. Install dependencies for the plugin under test.
2. Run linters and static analysis.
3. Run unit tests.
4. Build Docker image and run integration tests inside container (where relevant).
5. Produce artifacts: SBOM, coverage reports, checksums.

Local test commands (examples):

```bash
# PHP (from repo root)
cd plugins/php
composer install
./vendor/bin/phpunit

# Python
cd plugins/python
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest -q

# Go
cd plugins/go
go test ./...

# TypeScript / Node.js
cd plugins/typescript
npm ci
npm test

# Ruby
cd plugins/ruby
bundle install --path vendor/bundle
bundle exec rspec
```

If your change touches CI config, make sure to test your workflow locally or on a feature branch.

---

## Linting, formatting & static analysis

We enforce style and quality checks:

* Use the language-specific linters and formatters defined in each plugin (Prettier, ESLint, Black, RuboCop, PHP CS Fixer, golangci-lint, Psalm/PHPStan, MyPy/Pyright).
* Run fixers and checkers locally before opening a PR. Example:

```bash
# TypeScript
cd plugins/typescript
npm run lint
npm run format

# Python
cd plugins/python
black src tests
mypy src

# Go
golangci-lint run ./...

# PHP
composer run-script lint
```

CI will fail PRs with linter or test failures.

---

## Releases & publishing

Releases are managed by maintainers. Key points:

* Versioning: **Semantic Versioning** (MAJOR.MINOR.PATCH).
* Release artifacts: Docker image(s), language package artifacts (where relevant), SBOM, checksums, and signatures.
* Build metadata: inject `VERSION`, `VCS_REF` and `BUILD_DATE` during builds (Docker `--build-arg`).
* Sign artifacts: maintainers should sign release binaries and images (GPG / cosign) and attach checksums (`sha256`, `sha512`).
* Generate SBOM (e.g. Syft/CycloneDX) as part of CI and attach to release assets.

If you’re proposing a release process change, open an issue/PR detailing the new flow and CI changes required.

---

## Security reporting & responsible disclosure

If you discover a security vulnerability:

1. DO NOT open a public issue.
2. Email the maintainers at `security@cajeer.com` (replace with the actual email in `README`) with:

   * subject: `Security vulnerability — OmniFlow plugins`
   * description of the vulnerability and steps to reproduce
   * affected versions and suggested mitigations
3. If you cannot reach maintainers by email, open a confidential report at the platform’s private security issue channel (GitHub private security advisories) if available.

We follow coordinated disclosure: the maintainers will acknowledge receipt, investigate, and coordinate patches and advisories with the reporter before public disclosure.

---

## Licensing & copyright

This repository is licensed under **Apache License 2.0** (see `LICENSE` at repo root). By contributing, you agree that your contributions will be licensed under the project license.

If your employer requires a Contributor License Agreement (CLA), please mention it in your PR — maintainers will handle the process.

---

## Developer tooling & helpers

We provide helper scripts and templates under `plugins/common/`:

* `plugins/common/protocol.md` — protocol reference (NDJSON) used by all plugins.
* Example CI workflows under `plugins/.github/workflows/` to copy/adapt per plugin.
* Templates for `Dockerfile`, `README.md`, `tests/` for new language plugins.

Recommended local helpers:

* `pre-commit` and `husky` hooks (optional) to run linters before commits.
* Shell scripts in `scripts/` to run a plugin locally inside a minimal container for integration testing.

---

## Getting help / contact

* For questions or issues, open an issue in the repository (non-sensitive).
* For security reports, use `security@cajeer.com` (private).
* For maintainers and contribution guidance, mention `@TheSkiF4er` in PRs / issues where appropriate.
