# OmniFlow Python Plugin Dockerfile (multi-stage, production-ready)
#
# Location: OmniFlow/plugins/python/Dockerfile
#
# Purpose:
#   - Multi-stage build to install Python dependencies and produce a minimal runtime image
#   - Support both Poetry (pyproject.toml) and pip (requirements.txt) based projects
#   - Run plugin as a non-root user (least privilege)
#   - Provide reproducible build metadata via build-args (VERSION, VCS_REF, BUILD_DATE)
#   - Include a lightweight HEALTHCHECK that sends an NDJSON health probe to the plugin
#   - Small, secure runtime (Debian slim base), optionally install tini for proper signal handling
#
# Usage examples:
#   # Basic pip-based build (uses plugins/python/requirements.txt and plugins/python/sample_plugin.py)
#   docker build \
#     --file plugins/python/Dockerfile \
#     --build-arg VERSION=1.2.3 \
#     --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-python:1.2.3 \
#     .
#
#   # Poetry-based build (if pyproject.toml is present in plugins/python)
#   docker build --file plugins/python/Dockerfile --build-arg USE_POETRY=1 .
#
# Notes:
#   - The default plugin entrypoint is /opt/omniflow/plugins/python/sample_plugin.py.
#     Change COPY/ENTRYPOINT if your entrypoint differs.
#   - For a smaller runtime, swap the final stage base to a distroless image if you don't need a shell.
#
################################################################################

# ----------------------------
# Builder stage
# ----------------------------
FROM python:3.11-slim AS builder

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG VERSION=unreleased
ARG USE_POETRY=0
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Install build-time packages needed for many wheels and for poetry if used
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       python3-dev \
       gcc \
       git \
       curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency manifests first to leverage caching
# Support both Poetry (pyproject.toml) and pip (requirements.txt)
COPY plugins/python/pyproject.toml plugins/python/poetry.lock* ./ || true
COPY plugins/python/requirements.txt ./ || true

# If using Poetry, install poetry and export a requirements.txt for pip
RUN if [ "${USE_POETRY}" = "1" ] && [ -f "./pyproject.toml" ]; then \
      curl -sSL https://install.python-poetry.org | python - --version 1.8.1 && \
      export PATH="$HOME/.local/bin:$PATH" && \
      poetry --version && \
      # create a virtual env-less export with exact pinned dependencies (PEP 508)
      POETRY_VIRTUALENVS_CREATE=false poetry export --without-hashes -f requirements.txt -o /build/poetry-reqs.txt --dev; \
    fi

# Choose which requirements file to use
RUN if [ -f "./poetry-reqs.txt" ]; then cp /build/poetry-reqs.txt ./requirements.lock.txt; \
    elif [ -f "./requirements.txt" ]; then cp ./requirements.txt ./requirements.lock.txt; \
    else echo "# no requirements provided" > ./requirements.lock.txt; fi

# Install dependencies into a target directory (to copy into runtime image)
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    if [ -s ./requirements.lock.txt ]; then /opt/venv/bin/pip install --no-deps -r ./requirements.lock.txt; fi

# Copy plugin source into build context
COPY plugins/python/ ./src/

# Optionally run tests during build (toggle by build arg or CI)
ARG RUN_TESTS=0
RUN if [ "${RUN_TESTS}" = "1" ]; then \
      /opt/venv/bin/pip install pytest && \
      cd /build/src && /opt/venv/bin/pytest -q || (echo "Tests failed" && exit 1); \
    fi

# Strip unnecessary files from venv (reduce size)
RUN find /opt/venv -name '__pycache__' -type d -exec rm -rf {} + || true

# ----------------------------
# Runtime stage
# ----------------------------
FROM python:3.11-slim AS runtime

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG VERSION=unreleased

ENV APP_HOME=/opt/omniflow \
    PLUGIN_DIR=/opt/omniflow/plugins/python \
    VENV_PATH=/opt/venv \
    PYTHONPATH=/opt/omniflow/plugins/python

# Create non-root user & directories
RUN groupadd --gid 1000 omniflow || true \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/omniflow --shell /usr/sbin/nologin omniflow \
    && mkdir -p ${PLUGIN_DIR} ${APP_HOME}/log \
    && chown -R omniflow:omniflow ${APP_HOME}

# Copy virtualenv from builder
COPY --from=builder /opt/venv ${VENV_PATH}

# Copy plugin source
COPY --from=builder /build/src/ ${PLUGIN_DIR}/

# Ensure correct ownership/permissions
RUN chown -R omniflow:omniflow ${PLUGIN_DIR} ${VENV_PATH} \
    && chmod -R 0755 ${PLUGIN_DIR} || true

# Install tini (process reaper) for proper signal handling (optional)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

USER omniflow
WORKDIR ${APP_HOME}

# Entrypoint: run plugin script (reads NDJSON from stdin, writes NDJSON to stdout)
# Allow overriding ENTRYPOINT by upstream orchestrator if needed.
ENV PLUGIN_ENTRYPOINT=${PLUGIN_DIR}/sample_plugin.py
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python", "/opt/omniflow/plugins/python/sample_plugin.py"]
CMD []

# Lightweight healthcheck: send NDJSON health probe and expect a JSON response on stdout
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | /opt/venv/bin/python ${PLUGIN_ENTRYPOINT} >/dev/null 2>&1 || exit 1

# Image metadata labels (useful for SBOM / provenance)
LABEL org.opencontainers.image.title="OmniFlow Python Plugin" \
      org.opencontainers.image.description="OmniFlow Python plugin — NDJSON protocol plugin for OmniFlow." \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Recommendations:
# - Run container with: --read-only, --tmpfs /tmp:rw, --cap-drop ALL, --security-opt no-new-privileges
# - Mount logs or sockets as necessary with appropriate permissions
#
# Example runtime:
#   docker run --rm --read-only --tmpfs /tmp:rw --cap-drop ALL --security-opt no-new-privileges \
#     omniflow/plugin-python:1.2.3
#
################################################################################
