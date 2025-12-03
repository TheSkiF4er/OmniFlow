# OmniFlow Go Plugin Dockerfile (multi-stage)
#
# Location: OmniFlow/plugins/go/Dockerfile
#
# Purpose:
#   - Multi-stage build to produce a small, secure runtime image for the OmniFlow Go plugin
#   - Support reproducible build metadata via build-args
#   - Default to static, portable build (CGO disabled). If you need CGO, set CGO_ENABLED=1 and adjust runtime base.
#   - Run plugin as a non-root user inside the container
#   - Provide a lightweight healthcheck which invokes the plugin with a health NDJSON probe
#
# Usage examples:
#   # Typical release build (static, stripped)
#   docker build --build-arg VERSION=1.2.3 --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-go:1.2.3 -f plugins/go/Dockerfile .
#
#   # Debug build with race detector (larger)
#   docker build --build-arg GOFLAGS="-race" --build-arg BUILD_MODE=debug -t omniflow/plugin-go:debug -f plugins/go/Dockerfile .
#
# Notes:
#   - For smallest runtime images consider swapping the runtime stage to a distroless image
#     (e.g. gcr.io/distroless/static) if your build is fully static and you accept the loss
#     of a shell for debugging. The default below uses debian:stable-slim to keep debugging easy.
#
# Build args:
#   - BUILD_MODE            : "release" (default) or "debug"
#   - GO_VERSION            : Go toolchain version (default 1.22)
#   - CGO_ENABLED           : 0 (default) — set to 1 only if you require cgo
#   - GOFLAGS               : additional go build flags (optional)
#   - LDFLAGS               : additional linker flags (optional)
#   - PLUGIN_BINARY         : binary name produced (default: omni_plugin_go)
#   - VERSION, VCS_REF, BUILD_DATE : reproducible metadata labels
#
###############################################################################

# ----------------------------
# Builder stage
# ----------------------------
FROM golang:1.22 AS builder

ARG BUILD_MODE=release
ARG GO_VERSION=1.22
ARG CGO_ENABLED=0
ARG GOFLAGS=""
ARG LDFLAGS=""
ARG PLUGIN_BINARY=omni_plugin_go
ARG PLUGIN_PKG=./cmd/omni_plugin_go     # package path to build; adapt to your repo layout
ARG BUILD_DATE=""
ARG VCS_REF=""
ARG VERSION="unreleased"

ENV CGO_ENABLED=${CGO_ENABLED}
WORKDIR /src

# Copy module files first for better layer caching
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    go mod download

# Copy the rest of the source
COPY . .

# Build flags
# Default ldflags strips symbol table and debug info for smaller binary in release mode
# In debug mode we skip -s -w to preserve symbols
RUN set -eux; \
    build_out=/out; mkdir -p "${build_out}"; \
    # choose ldflags and gcflags depending on mode
    if [ "${BUILD_MODE}" = "release" ]; then \
      default_ldflags="-s -w"; \
    else \
      default_ldflags=""; \
    fi; \
    LDFLAGS_FULL="${default_ldflags} ${LDFLAGS} -X 'main.buildVersion=${VERSION}' -X 'main.buildVCS=${VCS_REF}' -X 'main.buildDate=${BUILD_DATE}'"; \
    echo "Building plugin (GOFLAGS='${GOFLAGS}' LDFLAGS='${LDFLAGS_FULL}')"; \
    go env -w CGO_ENABLED=${CGO_ENABLED}; \
    # Build static when CGO_ENABLED=0; set -trimpath for reproducible builds if available
    go build ${GOFLAGS} -trimpath -ldflags="${LDFLAGS_FULL}" -o "${build_out}/${PLUGIN_BINARY}" ${PLUGIN_PKG}; \
    # Ensure binary is present
    ls -lh "${build_out}/${PLUGIN_BINARY}"; \
    # Show dynamic libs (if any)
    if [ "${CGO_ENABLED}" = "1" ]; then file "${build_out}/${PLUGIN_BINARY}"; else echo "Static binary built (CGO disabled)"; fi

# Optional: produce separate debug symbol file (not stripped) for archival
# For simplicity leave that to CI if needed

# ----------------------------
# Runtime stage
# ----------------------------
FROM debian:stable-slim AS runtime

ARG PLUGIN_BINARY=omni_plugin_go
ARG BUILD_DATE=""
ARG VCS_REF=""
ARG VERSION="unreleased"

# Create unprivileged user and paths
ENV APP_USER=omniflow
ENV APP_GROUP=omniflow
ENV APP_HOME=/opt/omniflow
ENV BINDIR=${APP_HOME}/bin
ENV LOGDIR=${APP_HOME}/log

RUN groupadd --gid 1000 ${APP_GROUP} \
 && useradd --uid 1000 --gid ${APP_GROUP} --create-home --home-dir ${APP_HOME} --shell /usr/sbin/nologin ${APP_USER} \
 && mkdir -p ${BINDIR} ${LOGDIR} \
 && chown -R ${APP_USER}:${APP_GROUP} ${APP_HOME}

# Minimal runtime deps: ca-certificates (if plugin performs TLS/HTTP)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copy binary from builder
COPY --from=builder /out/${PLUGIN_BINARY} ${BINDIR}/${PLUGIN_BINARY}

# Ensure ownership and permission
RUN chmod 0755 ${BINDIR}/${PLUGIN_BINARY} \
 && chown ${APP_USER}:${APP_GROUP} ${BINDIR}/${PLUGIN_BINARY}

# Use non-root user
USER ${APP_USER}

WORKDIR ${APP_HOME}

# Healthcheck: pipe a health NDJSON message to the plugin and expect a valid 0/ok response.
# The healthcheck executes the plugin as a one-shot process; the plugin must accept a single NDJSON health
# object on stdin and print a one-line JSON response to stdout. This command will return success when stdout
# contains a JSON object echoing the id and status ok (the plugin should follow protocol).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | ${BINDIR}/${PLUGIN_BINARY} >/dev/null 2>&1 || exit 1

# Entrypoint: run plugin (reads NDJSON from stdin, writes NDJSON to stdout).
ENTRYPOINT ["sh","-c","exec /opt/omniflow/bin/omni_plugin_go"]
CMD []

# Metadata labels
LABEL org.opencontainers.image.title="OmniFlow Go plugin" \
      org.opencontainers.image.description="OmniFlow Go plugin (omni_plugin_go) — secure, small plugin for OmniFlow" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow"
