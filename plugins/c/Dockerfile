# -----------------------------------------------------------------------------
# OmniFlow C Plugin Dockerfile (multi-stage)
# Purpose: Build a small, secure, reproducible container for the C plugin.
# Location: OmniFlow/plugins/c/Dockerfile
#
# Features:
#  - Multi-stage build (builder -> runtime)
#  - Build-time args: BUILD_TYPE, CC, ENABLE_ASAN, STRIP_BINARY
#  - Reproducible-ish flags and deterministic timestamps where possible
#  - Non-root runtime user, minimal runtime footprint
#  - Optional AddressSanitizer build variant for QA/debug images
#  - Healthcheck (basic) that runs plugin with --health or sends JSON health probe
#
# Usage examples:
#  Build release image (default):
#    docker build -t omniflow/plugin-c:v1.2.3 .
#
#  Build ASAN debug image for CI:
#    docker build --build-arg ENABLE_ASAN=1 --build-arg BUILD_TYPE=Debug -t omniflow/plugin-c:asan .
#
#  Build with specific compiler:
#    docker build --build-arg CC=clang -t omniflow/plugin-c:clang .
#
# Notes:
#  - The Dockerfile expects the C source (sample_plugin.c and vendor/cJSON) to be under the build context at ./plugins/c
#  - You may adapt the plugin binary name `sample_plugin` to whatever your build produces.
# -----------------------------------------------------------------------------

# ------------------------
# Builder stage
# ------------------------
FROM debian:stable-slim AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_TYPE=Release          # Release or Debug
ARG CC=gcc                      # gcc or clang
ARG ENABLE_ASAN=0               # 1 to enable AddressSanitizer (for QA images only)
ARG STRIP_BINARY=1              # 1 to strip debug symbols from final binary
ARG PLUGIN_NAME=sample_plugin
ARG PLUGIN_SRC_DIR=/src/plugins/c

# Install build dependencies
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      git \
      pkg-config \
      cmake \
      curl \
      ca-certificates \
      python3 \
      python3-pip \
      # optional tools for static analysis in CI:
      clang-format clang-tidy valgrind \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy source code (expecting build context root contains plugins/c)
# Keep copy minimal to enable Docker cache. Copy vendor and Makefile first if present.
COPY plugins/c/Makefile plugins/c/Makefile
COPY plugins/c/vendor plugins/c/vendor
COPY plugins/c/*.c plugins/c/*.h plugins/c/  # copy plugin source files
COPY plugins/c/*.[ch] plugins/c/  # any extra headers/sources
# NOTE: adjust above COPY lines to match real repo layout if different

# Expose build metadata (useful for reproducible builds)
ARG BUILD_DATE
ARG VCS_REF
ENV BUILD_DATE=${BUILD_DATE:-unknown} \
    VCS_REF=${VCS_REF:-unknown}

# Build step - prefer Makefile if present
# Makefile should honor BUILD_TYPE and CC and output binary into /build/out/<name>
RUN set -eux; \
    mkdir -p out; \
    if [ -f plugins/c/Makefile ]; then \
      echo "Using Makefile to build"; \
      # Pass build args via environment to Makefile; Makefile should respect CC/BUILD_TYPE/ENABLE_ASAN
      make -C plugins/c clean || true; \
      CC=${CC} BUILD_TYPE=${BUILD_TYPE} ENABLE_ASAN=${ENABLE_ASAN} make -C plugins/c all || (cat plugins/c/build.log || true; exit 1); \
      # find binary
      if [ -f plugins/c/${PLUGIN_NAME} ]; then cp plugins/c/${PLUGIN_NAME} out/; fi; \
      if [ -f plugins/c/build/${PLUGIN_NAME} ]; then cp plugins/c/build/${PLUGIN_NAME} out/; fi; \
    else \
      echo "No Makefile found - performing direct compile"; \
      SRC=$(ls plugins/c/*.c | tr '\n' ' '); \
      ${CC} -std=c11 -O2 -Wall -Wextra -fno-omit-frame-pointer -march=x86-64 ${ENABLE_ASAN:+-fsanitize=address -g} -D_FORTIFY_SOURCE=2 -fstack-protector-strong -o out/${PLUGIN_NAME} $SRC plugins/c/vendor/cJSON/cJSON.c || exit 1; \
    fi; \
    ls -la out

# Optionally strip binary to reduce size (we will conditionally strip in runtime stage)
# Keep out/ binary ownership to root for now

# ------------------------
# Runtime stage
# ------------------------
FROM debian:stable-slim AS runtime

ARG PLUGIN_NAME=sample_plugin
ARG STRIP_BINARY=1
ARG ENABLE_ASAN=0

# Create non-root user and group
ENV APP_USER=omniflow
ENV APP_GROUP=omniflow
ENV APP_HOME=/opt/omniflow
RUN groupadd --gid 1000 ${APP_GROUP} \
 && useradd --uid 1000 --gid ${APP_GROUP} --create-home --home-dir ${APP_HOME} --shell /usr/sbin/nologin ${APP_USER} \
 && mkdir -p ${APP_HOME}/bin \
 && chown ${APP_USER}:${APP_GROUP} ${APP_HOME}

# Install only runtime required packages
# If binary is fully static or uses libc present in base image, this will be minimal.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR ${APP_HOME}

# Copy built artifact(s) from builder
COPY --from=builder /build/out/${PLUGIN_NAME} /opt/omniflow/bin/${PLUGIN_NAME}

# Optional: copy debug symbols into separate directory (if builder produced them)
# COPY --from=builder /build/out/${PLUGIN_NAME}.debug /opt/omniflow/debug/ || true

# Make binary non-executable by root-only if desired, ensure ownership to non-root user
RUN chmod 0755 /opt/omniflow/bin/${PLUGIN_NAME} \
 && chown -R ${APP_USER}:${APP_GROUP} /opt/omniflow

# Optionally strip the binary to reduce image size; keep an unstripped debug artifact in builder if needed
# Use 'strip' only if STRIP_BINARY=1 and ASAN is disabled (ASAN builds require debug symbols)
RUN if [ "${STRIP_BINARY}" = "1" ] && [ "${ENABLE_ASAN}" = "0" ]; then \
      apt-get update && apt-get install -y --no-install-recommends binutils && \
      strip --strip-unneeded /opt/omniflow/bin/${PLUGIN_NAME} || true && \
      rm -rf /var/lib/apt/lists/*; \
    fi

# Drop privileges by default
USER ${APP_USER}

# Healthcheck: basic JSON probe — the plugin should accept a 'health' message on stdin and respond
# This probe runs the plugin with a short-lived process sending a health JSON and checks exit code.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD echo '{"id":"hc-1","type":"health"}' | /opt/omniflow/bin/${PLUGIN_NAME} >/dev/null 2>&1 || exit 1

# Default entrypoint: run plugin reading from STDIN/STDOUT
# Using exec form preserves signals
ENTRYPOINT ["/opt/omniflow/bin/sample_plugin"]
CMD []

# metadata labels
LABEL org.opencontainers.image.title="OmniFlow C plugin" \
      org.opencontainers.image.description="OmniFlow plugin (C) - small, secure plugin for OmniFlow" \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0 (project), MIT (vendor/cJSON)" \
      org.opencontainers.image.revision="${VCS_REF:-unknown}" \
      org.opencontainers.image.created="${BUILD_DATE:-unknown}"
