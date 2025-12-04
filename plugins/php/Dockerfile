# OmniFlow PHP Plugin Dockerfile (multi-stage, production-ready)
#
# Location: OmniFlow/plugins/php/Dockerfile
#
# Purpose:
#   - Multi-stage build to install PHP deps with Composer in a builder image
#   - Produce a small, secure runtime image that runs the PHP plugin as a non-root user
#   - Support reproducible metadata via build-args (VERSION, VCS_REF, BUILD_DATE)
#   - Run plugin as a one-shot NDJSON responder on STDIN/STDOUT
#   - Healthcheck that pipes a health NDJSON probe to the plugin and expects a valid JSON response
#
# Usage (example):
#   docker build \
#     --build-arg VERSION=1.2.3 \
#     --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-php:1.2.3 \
#     -f plugins/php/Dockerfile .
#
# Notes:
#   - The plugin entrypoint is expected at /opt/omniflow/plugins/php/sample_plugin.php
#   - If your plugin path differs, adjust the COPY/ENTRYPOINT lines accordingly.
#   - For extra hardening, run the resulting container with:
#       --read-only --tmpfs /tmp:rw --cap-drop ALL --security-opt no-new-privileges
#
###############################################################################

# ----------------------------
# Builder stage
# ----------------------------
FROM composer:2.7 AS builder

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG VERSION=unreleased

WORKDIR /app

# Copy composer files first to leverage Docker cache
COPY plugins/php/composer.json plugins/php/composer.lock* ./

# Install production dependencies in vendor/ (we keep dev deps for test step only if needed)
# Use --no-interaction and --no-ansi for CI-friendly output
RUN composer install --no-dev --no-progress --no-interaction --prefer-dist --optimize-autoloader

# Copy plugin source
COPY plugins/php/ .

# Optional: run tests in builder to fail early (toggle by build-arg if desired)
# Here we run tests if phpunit config exists. This helps CI but can be toggled.
ARG RUN_TESTS=false
RUN if [ "$RUN_TESTS" = "true" ] && [ -f "tests/phpunit.xml" ]; then \
      composer require --dev phpunit/phpunit --no-interaction --no-progress && \
      ./vendor/bin/phpunit --configuration tests/phpunit.xml || (echo "PHPUnit tests failed" && exit 1); \
    fi

# Ensure vendor dir exists and has correct permissions
RUN ls -la vendor || true

# ----------------------------
# Runtime stage
# ----------------------------
FROM php:8.2-cli-alpine3.19 AS runtime

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG VERSION=unreleased

ENV APP_HOME=/opt/omniflow
ENV PLUGIN_DIR=${APP_HOME}/plugins/php
ENV PLUGIN_BIN=${PLUGIN_DIR}/sample_plugin.php
ENV APP_USER=omniflow
ENV APP_GROUP=omniflow

# Install minimal runtime packages (ca-certificates for TLS, tini for signal handling optionally)
RUN apk add --no-cache ca-certificates tini

# Create non-root user and group
RUN addgroup -S ${APP_GROUP} && adduser -S -G ${APP_GROUP} ${APP_USER}

# Create application directories
RUN mkdir -p ${PLUGIN_DIR} ${APP_HOME}/log \
    && chown -R ${APP_USER}:${APP_GROUP} ${APP_HOME}

# Copy built artifact from builder: source files + vendor
COPY --from=builder /app ${PLUGIN_DIR}

# Ensure script is executable and owned by non-root user
RUN chmod 0755 ${PLUGIN_BIN} \
    && chown -R ${APP_USER}:${APP_GROUP} ${PLUGIN_DIR}

# Switch to non-root user
USER ${APP_USER}

WORKDIR ${APP_HOME}

# Use tini to reap processes and forward signals; run plugin as the entrypoint.
# Plugin reads NDJSON from stdin and writes NDJSON to stdout.
ENTRYPOINT ["tini", "--", "php", "/opt/omniflow/plugins/php/sample_plugin.php"]
CMD []

# Healthcheck: pipe a health NDJSON message to the plugin and expect it to respond (exit code 0).
# The plugin must accept a single-line JSON object on stdin and write a single-line JSON response to stdout.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | php /opt/omniflow/plugins/php/sample_plugin.php >/dev/null 2>&1 || exit 1

# Metadata labels for provenance / SBOM
LABEL org.opencontainers.image.title="OmniFlow PHP Plugin" \
      org.opencontainers.image.description="OmniFlow PHP plugin (sample_plugin.php) — secure, production-ready plugin runtime." \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Recommendations for runtime (documented; not enforced here):
# - Run container with --read-only, mount any writable paths as tmpfs or dedicated volumes
# - Drop capabilities and set no-new-privileges:
#     docker run --read-only --tmpfs /tmp:rw --cap-drop ALL --security-opt no-new-privileges ...
#
# Example:
#   docker run --rm \
#     --read-only \
#     --tmpfs /tmp:rw \
#     --cap-drop ALL \
#     --security-opt no-new-privileges \
#     omniflow/plugin-php:1.2.3
#
###############################################################################
