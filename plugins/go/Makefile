# Makefile — OmniFlow Go plugin (plugins/go)
#
# Production-ready Makefile for building, testing, packaging and releasing the
# OmniFlow Go plugin. Designed to work well in CI and locally.
#
# Features:
#  - Go modules aware (go mod)
#  - Build with reproducible metadata (VERSION, VCS_REF, BUILD_DATE)
#  - Supports cross-compilation (GOOS / GOARCH)
#  - Race, coverage and static analysis targets
#  - Docker multi-stage build integration (uses plugins/go/Dockerfile)
#  - Install/uninstall, dist (tar.gz), clean, format, lint, vet and tidy
#
# Place at: OmniFlow/plugins/go/Makefile
#
# Usage examples:
#   make                # build (release)
#   make build DEBUG=1  # build debug (no -ldflags -s -w)
#   make race           # build + run tests with race detector
#   make test           # run go test
#   make docker-build VERSION=1.2.3
#   make dist VERSION=1.2.3
#
# Notes for CI:
#  - CI should set VERSION, VCS_REF and BUILD_DATE build args when creating artifacts.
#  - Run `make mod-tidy && make test` as baseline checks.
#  - Run `make lint` using golangci-lint in CI (install first).
#
###############################################################################

# ------------- Configurable variables -------------
PLUGIN_NAME ?= omni_plugin_go
PKG         ?= ./...                    # package to build; adjust if needed
OUTDIR      ?= $(CURDIR)/build/out
BINDIR      ?= $(OUTDIR)
BUILD_DIR   ?= $(CURDIR)/build
DISTDIR     ?= $(CURDIR)/dist

# Repro metadata (override in CI)
VERSION     ?= unreleased
VCS_REF     ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
BUILD_DATE  ?= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

# Go toolchain flags
GO          ?= go
GOFLAGS     ?= -mod=readonly
CGO_ENABLED ?= 0
LDFLAGS     ?= -s -w
DEBUG_LDFLAGS ?=
# Embed build metadata into binary via -X flags; main package must expose variables:
#   var buildVersion string
#   var buildVCS string
#   var buildDate string
LDFLAGS += -X 'main.buildVersion=$(VERSION)' -X 'main.buildVCS=$(VCS_REF)' -X 'main.buildDate=$(BUILD_DATE)'

# Cross-compilation defaults
GOOS        ?= $(shell go env GOOS)
GOARCH      ?= $(shell go env GOARCH)

# Test flags
TESTPKG     ?= ./...
TESTFLAGS   ?= -v

# Tools (optionally installed)
GOLANGCI_LINT ?= golangci-lint
GOCOVTOOL   ?= go tool cover

# -----------------------------------------------------------------------------
# Helper: detect if running inside CI (simple heuristic)
CI ?= $(if $(CI),1,0)

# Default goal
.DEFAULT_GOAL := all

.PHONY: all build clean dist docker-build docker-push test race coverage install uninstall \
        fmt vet lint mod-tidy mod-vendor help run cross

# ------------- Primary targets -------------

all: build

# Build (release by default). Use DEBUG=1 to skip -s -w (keep symbols).
build: mod-tidy
	@echo "=> Building plugin: $(PLUGIN_NAME) (GOOS=$(GOOS) GOARCH=$(GOARCH))"
	$(MKDIR_P) $(BINDIR)
	# Respect DEBUG flag
ifeq ($(DEBUG),1)
	$(eval LDFLAGS_FINAL := $(DEBUG_LDFLAGS))
else
	$(eval LDFLAGS_FINAL := $(LDFLAGS))
endif
	@env CGO_ENABLED=$(CGO_ENABLED) GOOS=$(GOOS) GOARCH=$(GOARCH) $(GO) build $(GOFLAGS) -trimpath -ldflags "$(LDFLAGS_FINAL)" -o "$(BINDIR)/$(PLUGIN_NAME)" $(PKG)
	@echo "Built: $(BINDIR)/$(PLUGIN_NAME)"

# Run plugin (reads NDJSON from stdin). Useful for local testing.
run: build
	@echo "=> Running plugin (local): $(BINDIR)/$(PLUGIN_NAME)"
	@$(BINDIR)/$(PLUGIN_NAME)

# Build and run tests
test: mod-tidy
	@echo "=> Running tests"
	$(GO) test $(GOFLAGS) $(TESTFLAGS) $(TESTPKG)

# Run tests with race detector
race: mod-tidy
	@echo "=> Running tests with race detector"
	@env CGO_ENABLED=1 $(GO) test $(GOFLAGS) -race $(TESTFLAGS) $(TESTPKG)

# Generate coverage profile and optionally open report
coverage: mod-tidy
	@echo "=> Running tests (coverage)"
	$(MKDIR_P) $(BUILD_DIR)
	@$(GO) test $(GOFLAGS) -covermode=atomic -coverprofile=$(BUILD_DIR)/coverage.out $(TESTPKG)
	@$(GOCOVTOOL) -func=$(BUILD_DIR)/coverage.out | tee $(BUILD_DIR)/coverage.txt
	@echo "Coverage report: $(BUILD_DIR)/coverage.txt"
	@if command -v $(GOCOVTOOL) >/dev/null 2>&1; then echo "To view HTML: $(GOCOVTOOL) -html=$(BUILD_DIR)/coverage.out -o $(BUILD_DIR)/coverage.html"; fi

# ------------- Formatting & static checks -------------

fmt:
	@echo "=> gofmt -s -w"
	@find . -name '*.go' -not -path "./vendor/*" -print0 | xargs -0 gofmt -s -w

vet:
	@echo "=> go vet ./..."
	$(GO) vet ./...

lint:
	@echo "=> running golangci-lint (if installed)"
	@if command -v $(GOLANGCI_LINT) >/dev/null 2>&1; then \
	  $(GOLANGCI_LINT) run --timeout 5m; \
	else \
	  echo "golangci-lint not found; install it or run 'go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest'"; \
	fi

# ------------- Modules & dependencies -------------

mod-tidy:
	@echo "=> go mod tidy"
	@$(GO) mod tidy

mod-verify:
	@echo "=> go mod verify"
	@$(GO) mod verify

# Optionally vendor dependencies (if you want to check them in)
mod-vendor:
	@echo "=> go mod vendor"
	@$(GO) mod vendor

# ------------- Docker & packaging -------------

# Build Docker image using Dockerfile in this directory
docker-build:
	@echo "=> docker build (using plugins/go/Dockerfile)"
ifndef VERSION
	$(error VERSION is required. Example: make docker-build VERSION=1.2.3)
endif
	docker build --build-arg VERSION=$(VERSION) --build-arg VCS_REF=$(VCS_REF) --build-arg BUILD_DATE=$(BUILD_DATE) -t omniflow/$(PLUGIN_NAME):$(VERSION) -f Dockerfile .

# Optionally push to registry (requires logged-in docker)
docker-push:
ifndef VERSION
	$(error VERSION is required. Example: make docker-push VERSION=1.2.3)
endif
	@echo "=> docker push omniflow/$(PLUGIN_NAME):$(VERSION)"
	docker push omniflow/$(PLUGIN_NAME):$(VERSION)

# Create release tarball (dist)
dist: build
	@echo "=> Packaging release tarball"
	$(MKDIR_P) $(DISTDIR)
	VER=$(VERSION); if [ -z "$$VER" ] || [ "$$VER" = "unreleased" ]; then VER=$(VCS_REF); fi; \
	DST=$(DISTDIR)/omniflow-$(PLUGIN_NAME)-$$VER-$(GOOS)-$(GOARCH).tar; \
	cp -v "$(BINDIR)/$(PLUGIN_NAME)" $(DISTDIR)/ || true; \
	echo "version: $$VER" > $(DISTDIR)/release_metadata.txt; \
	echo "vcs_ref: $(VCS_REF)" >> $(DISTDIR)/release_metadata.txt; \
	echo "build_date: $(BUILD_DATE)" >> $(DISTDIR)/release_metadata.txt; \
	cd $(DISTDIR) && tar cf $$DST . && gzip -n $$DST && echo "Created: $$DST.gz"

# ------------- Install / Uninstall -------------

install: build
	@echo "=> Installing $(PLUGIN_NAME) to $(DESTDIR)/usr/local/bin"
	install -d $(DESTDIR)/usr/local/bin
	install -m 0755 "$(BINDIR)/$(PLUGIN_NAME)" $(DESTDIR)/usr/local/bin/$(PLUGIN_NAME)

uninstall:
	@echo "=> Removing $(DESTDIR)/usr/local/bin/$(PLUGIN_NAME)"
	-rm -f $(DESTDIR)/usr/local/bin/$(PLUGIN_NAME) || true

# ------------- Clean -------------

clean:
	@echo "=> Cleaning build artifacts"
	-@rm -rf $(BUILD_DIR) $(OUTDIR) $(DISTDIR)
	-@rm -f $(PLUGIN_NAME) || true

# ------------- Utilities -------------

# portable mkdir -p
MKDIR_P ?= mkdir -p

# Help text
help:
	@cat <<EOF
OmniFlow Go plugin — Makefile targets:

  make                # build (default)
  make build DEBUG=1  # build debug (keep symbols)
  make run            # run plugin binary
  make test           # run unit tests
  make race           # run tests with race detector
  make coverage       # run tests and produce coverage report
  make fmt            # run gofmt
  make vet            # run go vet
  make lint           # run golangci-lint (if installed)
  make mod-tidy       # run go mod tidy
  make mod-verify     # run go mod verify
  make mod-vendor     # vendor dependencies (optional)
  make docker-build VERSION=1.2.3  # build docker image
  make docker-push VERSION=1.2.3   # push docker image
  make dist VERSION=1.2.3          # create release tarball in dist/
  make install       # install into (DESTDIR)/usr/local/bin
  make uninstall
  make clean
  make help

Environment variables:
  VERSION     - version string to embed; recommended in CI
  VCS_REF     - short git commit hash (auto-detected if not set)
  BUILD_DATE  - UTC build timestamp (auto-detected if not set)
  GOOS, GOARCH - target OS/ARCH for cross-compilation

Recommended CI steps:
  - Set VERSION, VCS_REF, BUILD_DATE
  - make mod-tidy
  - make test
  - make lint
  - make dist
  - Build and sign artifacts; publish to releases

EOF
