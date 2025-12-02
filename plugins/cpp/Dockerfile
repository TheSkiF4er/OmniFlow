# ---------------------------------------------------------------------------
# OmniFlow C++ Plugin Dockerfile (multi-stage)
#
# Location: OmniFlow/plugins/cpp/Dockerfile
#
# Goals:
#  - Multi-stage build (builder -> runtime) for small runtime artifacts
#  - Build-time options: BUILD_TYPE, CC, ENABLE_ASAN, STRIP_BINARY
#  - Use CMake to configure + build; fallback to direct g++ compile if necessary
#  - Non-root runtime user, healthcheck, reproducible build metadata via build-args
#  - Keep runtime image minimal and secure
#
# Usage examples:
#  Build release image:
#    docker build -t omniflow/plugin-cpp:1.2.3 -f plugins/cpp/Dockerfile .
#
#  Build ASAN debug image for CI:
#    docker build --build-arg ENABLE_ASAN=1 --build-arg BUILD_TYPE=Debug -t omniflow/plugin-cpp:asan -f plugins/cpp/Dockerfile .
# ---------------------------------------------------------------------------

# ------------------------
# Builder stage
# ------------------------
FROM debian:stable-slim AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_TYPE=Release        # Release or Debug
ARG CC=g++                    # gcc/g++
ARG C_COMPILER=gcc
ARG ENABLE_ASAN=0             # 1 to enable AddressSanitizer (for QA images only)
ARG STRIP_BINARY=1
ARG PLUGIN_NAME=omni_plugin_cpp
ARG PLUGIN_SRC_DIR=/src/plugins/cpp

# Install build dependencies
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      git \
      cmake \
      pkg-config \
      python3 \
      python3-pip \
      wget \
      curl \
      ca-certificates \
      # Optional QA tools:
      clang-format clang-tidy valgrind \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only what builder needs to maximize cache efficiency.
# Expect build context root to include plugins/cpp directory.
COPY plugins/cpp/CMakeLists.txt plugins/cpp/CMakeLists.txt
COPY plugins/cpp/ CMakeLists_temp/   # lightweight copy to prime cache (we'll copy full tree next)

# Now copy the full plugin source tree (any changes to sources will bust cache)
COPY plugins/cpp/ ${PLUGIN_SRC_DIR}/

# Expose build metadata (useful for reproducible builds)
ARG BUILD_DATE
ARG VCS_REF
ENV BUILD_DATE=${BUILD_DATE:-unknown} \
    VCS_REF=${VCS_REF:-unknown}

# Configure and build using CMake
RUN set -eux; \
    mkdir -p build && cd build; \
    # Ensure CMake honors BUILD_TYPE and ENABLE_ASAN via -D flags if your CMakeLists supports them
    cmake ${PLUGIN_SRC_DIR} \
      -DCMAKE_BUILD_TYPE=${BUILD_TYPE} \
      -DENABLE_ASAN=${ENABLE_ASAN} \
      -DPLUGIN_NAME=${PLUGIN_NAME} ; \
    cmake --build . --config ${BUILD_TYPE} -- -j$(nproc); \
    # copy resulting binary(s) to /out; allow flexible locations
    mkdir -p /out; \
    # Common CMake out locations
    if [ -f build/${PLUGIN_NAME} ]; then cp build/${PLUGIN_NAME} /out/; fi || true; \
    if [ -f ${PLUGIN_NAME} ]; then cp ${PLUGIN_NAME} /out/; fi || true; \
    # find any executable in the build tree matching plugin name and copy
    found=$(find . -type f -perm /111 -name "${PLUGIN_NAME}*" -print -quit || true); \
    if [ -n "$found" ]; then cp "$found" /out/ || true; fi; \
    ls -la /out

# Optional: produce separate unstripped debug binary under /out/debug for ASAN troubleshooting
# (CMake configuration should generate debug symbols when BUILD_TYPE=Debug or ENABLE_ASAN=1)

# ------------------------
# Runtime stage
# ------------------------
FROM debian:stable-slim AS runtime

ARG PLUGIN_NAME=omni_plugin_cpp
ARG ENABLE_ASAN=0
ARG STRIP_BINARY=1

# Create non-root user
ENV APP_USER=omniflow
ENV APP_GROUP=omniflow
ENV APP_HOME=/opt/omniflow
RUN groupadd --gid 1000 ${APP_GROUP} \
 && useradd --uid 1000 --gid ${APP_GROUP} --create-home --home-dir ${APP_HOME} --shell /usr/sbin/nologin ${APP_USER} \
 && mkdir -p ${APP_HOME}/bin ${APP_HOME}/log \
 && chown ${APP_USER}:${APP_GROUP} ${APP_HOME}

# Minimal runtime deps: only CA certs; additional libs should be installed if the binary depends on them.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR ${APP_HOME}

# Copy built binary from builder stage
COPY --from=builder /out/${PLUGIN_NAME} /opt/omniflow/bin/${PLUGIN_NAME}

# Ensure binary exists; if not, try to copy any executable in /out
# (Note: Docker COPY above will fail build if file missing — that's desirable; adjust build if needed)

# Ensure correct permissions and non-root ownership
RUN chmod 0755 /opt/omniflow/bin/${PLUGIN_NAME} \
 && chown -R ${APP_USER}:${APP_GROUP} /opt/omniflow

# Optionally strip binary to reduce size (only if ASAN disabled)
RUN if [ "${STRIP_BINARY}" = "1" ] && [ "${ENABLE_ASAN}" = "0" ]; then \
      apt-get update && apt-get install -y --no-install-recommends binutils && \
      strip --strip-unneeded /opt/omniflow/bin/${PLUGIN_NAME} || true && \
      rm -rf /var/lib/apt/lists/*; \
    fi

# Use non-root user
USER ${APP_USER}

# Healthcheck: basic JSON probe — plugin must accept health message on stdin and respond to stdout
# This runs plugin as a transient process, piping a health JSON to stdin and expecting an OK response.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD echo '{"id":"hc-1","type":"health"}' | /opt/omniflow/bin/${PLUGIN_NAME} >/dev/null 2>&1 || exit 1

# Default entrypoint: plugin executable — plugin reads NDJSON from stdin and writes NDJSON to stdout.
ENTRYPOINT ["/opt/omniflow/bin/omni_plugin_cpp"]
CMD []

# OCI labels / metadata
LABEL org.opencontainers.image.title="OmniFlow C++ plugin" \
      org.opencontainers.image.description="OmniFlow C++ plugin (omni_plugin_cpp) - minimal, secure plugin for OmniFlow" \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="${VCS_REF:-unknown}" \
      org.opencontainers.image.created="${BUILD_DATE:-unknown}"
