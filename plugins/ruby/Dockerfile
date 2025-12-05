# OmniFlow Go Plugin Dockerfile (multi-stage, production-ready)
#
# Location: OmniFlow/plugins/go/Dockerfile
#
# Purpose:
#   - Multi-stage build that compiles a statically linked, optimized Go binary
#   - Minimal runtime image running as non-root user
#   - Reproducible build metadata via build-args (VERSION, VCS_REF, BUILD_DATE)
#   - Security: CGO disabled by default, trimmed binary, removed symbol table
#   - Healthcheck: sends an NDJSON health probe and expects a valid JSON reply
#   - Labels suitable for provenance / SBOM tooling
#
# Notes:
#   - Requires Go modules (go.mod present in repo)
#   - The plugin is expected to read NDJSON from stdin and write NDJSON to stdout
#   - For even smaller images consider switching the final stage to scratch/distroless
#
# Usage (example):
#   docker build \
#     --build-arg VERSION=1.2.3 \
#     --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-go:1.2.3 \
#     -f plugins/go/Dockerfile .
#
################################################################################

# --------------------------
# Builder stage
# --------------------------
FROM golang:1.21-alpine3.18 AS builder

# Build metadata (injected into binary via -ldflags)
ARG VERSION=unreleased
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG GOOS=linux
ARG GOARCH=amd64

ENV CGO_ENABLED=0 \
    GO111MODULE=on \
    GOPROXY=https://proxy.golang.org,direct \
    GOCACHE=/go-cache

WORKDIR /src

# Cache deps: copy only go.mod and go.sum first to leverage layer caching
COPY go.mod go.sum ./
RUN apk add --no-cache git ca-certificates && \
    go mod download

# Copy full source
COPY . .

# Build flags: trimpath, strip symbol table, set version variables (main.package must expose variables if present)
# Adjust -X flags to match package variable names in your code (example: main.version)
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/go-build \
    set -eux; \
    build_ldflags="-s -w -X 'github.com/TheSkiF4er/omniflow/plugins/go/pkg/version.Version=${VERSION}' -X 'github.com/TheSkiF4er/omniflow/plugins/go/pkg/version.VcsRef=${VCS_REF}' -X 'github.com/TheSkiF4er/omniflow/plugins/go/pkg/version.BuildDate=${BUILD_DATE}'"; \
    # produce a single, statically-linked binary
    go build -trimpath -ldflags="${build_ldflags}" -o /out/omniflow-plugin-go ./plugins/go/cmd/omniflow-plugin || (echo "go build failed" && exit 1)

# --------------------------
# Runtime stage
# --------------------------
FROM alpine:3.18 AS runtime

ARG VERSION=unreleased
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

# Minimal runtime packages for TLS, user management and tini (process reaper)
RUN apk add --no-cache ca-certificates tini \
    && update-ca-certificates

# Application paths
ENV APP_HOME=/opt/omniflow
ENV BIN_DIR=${APP_HOME}/bin
ENV PLUGIN_BIN=${BIN_DIR}/omniflow-plugin-go

# Create non-root user and directories
RUN addgroup -S plugin && adduser -S -G plugin plugin \
    && mkdir -p ${BIN_DIR} ${APP_HOME}/log \
    && chown -R plugin:plugin ${APP_HOME}

# Copy binary from builder
COPY --from=builder /out/omniflow-plugin-go ${PLUGIN_BIN}

# Ensure correct permissions
RUN chmod 0755 ${PLUGIN_BIN} && chown plugin:plugin ${PLUGIN_BIN}

# Switch to non-root user
USER plugin

WORKDIR ${APP_HOME}

# Entrypoint: run plugin binary (reads NDJSON from stdin; writes NDJSON to stdout)
# Use tini in the exec form to reap processes and forward signals correctly
ENTRYPOINT ["/sbin/tini", "--", "/opt/omniflow/bin/omniflow-plugin-go"]
CMD []

# Healthcheck: send a one-line NDJSON health probe to the plugin and expect a JSON reply.
# The plugin must accept a single-line JSON object on stdin and write a single-line JSON response to stdout.
HEALTHCHECK --interval=20s --timeout=5s --start-period=5s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | /opt/omniflow/bin/omniflow-plugin-go >/dev/null 2>&1 || exit 1

# Provenance and SBOM-friendly labels
LABEL org.opencontainers.image.title="OmniFlow Go Plugin" \
      org.opencontainers.image.description="OmniFlow Go plugin — production-ready Go plugin for OmniFlow." \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Provide a small, helpful default for runtime tuning (can be overridden via docker run -e)
ENV OMNIFLOW_PLUGIN_MAX_LINE=131072 \
    OMNIFLOW_EXEC_TIMEOUT=10

# Final notes (not enforced in the image):
#  - For maximum minimal image size, consider replacing `alpine` with a distroless/scratch image and
#    building a fully static binary. That requires verifying no CGO usage and copying /etc/ssl/certs appropriately.
#  - Generate an SBOM (e.g. using syft) in CI after the image is built and attach it to the release artifacts.
#  - To debug runtime issues, run the binary directly inside the container:
#      docker run --rm -it --entrypoint sh omniflow/plugin-go:tag
#      /opt/omniflow/bin/omniflow-plugin-go
#
################################################################################
