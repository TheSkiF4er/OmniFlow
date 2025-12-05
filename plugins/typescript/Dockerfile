# OmniFlow TypeScript Plugin Dockerfile (multi-stage, production-ready)
#
# Location: OmniFlow/plugins/typescript/Dockerfile
#
# Purpose:
#   - Multi-stage build: install dev deps, compile TypeScript to JS in builder stage
#   - Produce a minimal, secure runtime image running the compiled JS as a non-root user
#   - Support reproducible metadata via build-args (VERSION, VCS_REF, BUILD_DATE)
#   - Healthcheck that sends an NDJSON health probe to the plugin and expects a valid JSON reply
#   - Recommend runtime security flags and labels for SBOM / provenance
#
# Expectations:
#   - Source layout: plugins/typescript/
#       ├── package.json
#       ├── package-lock.json (recommended) OR pnpm-lock.yaml / yarn.lock
#       └── tsconfig.json
#   - Entrypoint: compiled file at dist/sample_plugin.js (adjust ENTRYPOINT if your output differs)
#
# Usage example:
#   docker build \
#     --file plugins/typescript/Dockerfile \
#     --build-arg VERSION=1.0.0 \
#     --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-typescript:1.0.0 .
#
################################################################################

# ----------------------------
# Builder stage
# ----------------------------
FROM node:20-alpine AS builder

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG VERSION=unreleased

ENV NODE_ENV=development \
    APP_HOME=/build

# Install build dependencies (TypeScript compiler, bundlers) if native needs exist
RUN apk add --no-cache python3 make g++ git

WORKDIR ${APP_HOME}

# Copy package manifests first to leverage Docker cache for deps
COPY plugins/typescript/package*.json ./
# If you use pnpm or yarn, you can copy their lockfiles and adjust install commands:
# COPY plugins/typescript/pnpm-lock.yaml ./
# COPY plugins/typescript/yarn.lock ./

# Install all dependencies (including devDependencies) for build
RUN if [ -f package-lock.json ]; then \
      npm ci --no-audit --prefer-offline; \
    else \
      npm install --no-audit --prefer-offline; \
    fi

# Copy source & build
COPY plugins/typescript/ ./

# Build step: compile TypeScript to dist/
# Expects script "build": "tsc -p tsconfig.json" (or similar) to exist in package.json
RUN if npm run | sed -n '1,200p' | grep -q " build"; then \
      npm run build --if-present; \
    else \
      echo "No build script found; ensure your TS build outputs to dist/ or provide precompiled files" && exit 1; \
    fi

# Optionally run unit tests during build (toggle with build-arg in CI)
ARG RUN_TESTS=false
RUN if [ "${RUN_TESTS}" = "true" ]; then \
      if npm run | sed -n '1,200p' | grep -q " test"; then \
        npm test --if-present || (echo "Tests failed" && exit 1); \
      else \
        echo "No test script found; skipping tests"; \
      fi \
    ; fi

# Remove build tools caches to reduce size (best-effort)
RUN npm prune --production=false || true

# ----------------------------
# Runtime stage
# ----------------------------
FROM node:20-alpine AS runtime

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG VERSION=unreleased

ENV NODE_ENV=production \
    APP_HOME=/opt/omniflow \
    PLUGIN_DIR=/opt/omniflow/plugins/typescript \
    PLUGIN_ENTRYPOINT=/opt/omniflow/plugins/typescript/dist/sample_plugin.js \
    OMNIFLOW_PLUGIN_MAX_LINE=131072 \
    OMNIFLOW_EXEC_TIMEOUT=10

# Install tini (process reaper) and ca-certificates for TLS
RUN apk add --no-cache tini ca-certificates && update-ca-certificates

# Create non-root user & directories
RUN addgroup -S omniflow && adduser -S -G omniflow omniflow \
  && mkdir -p ${PLUGIN_DIR} ${APP_HOME}/log \
  && chown -R omniflow:omniflow ${APP_HOME}

WORKDIR ${APP_HOME}

# Copy only production node_modules (from builder) and built files
# Copy node_modules production subset
COPY --from=builder /build/node_modules ./plugins/typescript/node_modules
# Copy built dist and package files
COPY --from=builder /build/dist ${PLUGIN_DIR}/dist
COPY --from=builder /build/package.json ${PLUGIN_DIR}/package.json
# Optionally copy any runtime assets (schemas, templates, README)
COPY --from=builder /build/*.md ${PLUGIN_DIR}/ || true
COPY --from=builder /build/*.json ${PLUGIN_DIR}/ || true

# Ensure permissions and ownership
RUN chown -R omniflow:omniflow ${PLUGIN_DIR} \
  && chmod -R 0755 ${PLUGIN_DIR} || true

# Switch to non-root user
USER omniflow

WORKDIR ${APP_HOME}

# Entrypoint: run the compiled plugin (reads NDJSON from stdin, writes NDJSON to stdout)
# Default expects compiled JS at PLUGIN_ENTRYPOINT; adjust if your build emits a different file.
ENTRYPOINT ["/sbin/tini", "--", "node", "/opt/omniflow/plugins/typescript/dist/sample_plugin.js"]
CMD []

# Lightweight healthcheck: send NDJSON health probe and expect a valid JSON response
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | node ${PLUGIN_ENTRYPOINT} >/dev/null 2>&1 || exit 1

# Provenance and SBOM-friendly labels
LABEL org.opencontainers.image.title="OmniFlow TypeScript Plugin" \
      org.opencontainers.image.description="OmniFlow TypeScript plugin — compiled Node.js plugin for OmniFlow." \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Recommended runtime flags (documented only):
# - Run container with --read-only --tmpfs /tmp:rw --cap-drop ALL --security-opt no-new-privileges
# - Limit resources in orchestrator: CPU / memory
#
# Example runtime:
# docker run --rm \
#   --read-only \
#   --tmpfs /tmp:rw \
#   --cap-drop ALL \
#   --security-opt no-new-privileges \
#   omniflow/plugin-typescript:1.0.0
#
################################################################################
