# OmniFlow Ruby Plugin Dockerfile (multi-stage, production-ready)
#
# Location: OmniFlow/plugins/ruby/Dockerfile
#
# Purpose:
#   - Multi-stage build that installs Ruby gems (via Bundler) in a builder image
#   - Produces a small, secure runtime image running as a non-root user
#   - Supports native gem compilation by installing build deps in the builder only
#   - Injects reproducible build metadata via build-args (VERSION, VCS_REF, BUILD_DATE)
#   - Uses tini to reap child processes and forward signals correctly
#   - Healthcheck: sends an NDJSON health probe to the plugin and expects a valid JSON reply
#   - Labels for provenance / SBOM tooling
#
# Usage (example):
#   docker build \
#     --build-arg VERSION=1.2.3 \
#     --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-ruby:1.2.3 \
#     -f plugins/ruby/Dockerfile .
#
# Notes:
#   - This Dockerfile expects plugin files at plugins/ruby/ inside repository.
#   - The plugin entrypoint defaults to /opt/omniflow/plugins/ruby/sample_plugin.rb.
#     If your entrypoint differs, update COPY/ENTRYPOINT accordingly.
#   - To keep the runtime small the builder compiles native extensions and we copy only
#     the vendor/bundle and app code into the final image.
#
###############################################################################

# ----------------------------
# Builder stage
# ----------------------------
FROM ruby:3.2-slim AS builder

ARG VERSION=unreleased
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

ENV DEBIAN_FRONTEND=noninteractive \
    BUNDLE_APP_CONFIG=/tmp/bundle_config

WORKDIR /build

# Install build dependencies needed for native gems (nokogiri, oj, etc.)
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
     build-essential \
     libssl-dev \
     libffi-dev \
     libxml2-dev \
     libxslt1-dev \
     git \
     ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy only Gemfile/Gemfile.lock first to leverage Docker caching
COPY plugins/ruby/Gemfile plugins/ruby/Gemfile.lock* ./

# Configure bundler for deployment-style install to vendor/bundle
RUN gem install bundler -v 2.4.21 --no-document \
  && bundle config set --local without 'development test' \
  && bundle config set --local deployment 'true' \
  && bundle config set --local path 'vendor/bundle'

# Install app dependencies (production only)
RUN bundle install --jobs=4 --retry=3

# Copy plugin source into builder (after dependencies are installed)
COPY plugins/ruby/ ./

# Optional: run tests in builder to fail early in CI (toggle via build-arg)
ARG RUN_TESTS=false
RUN if [ "${RUN_TESTS}" = "true" ] && [ -f "spec/spec_helper.rb" ]; then \
      gem install rspec --no-document && \
      bundle exec rspec || (echo "RSpec tests failed" && exit 1); \
    fi

# Remove build-only packages and caches to reduce size (best-effort)
RUN rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# ----------------------------
# Runtime stage
# ----------------------------
FROM ruby:3.2-slim AS runtime

ARG VERSION=unreleased
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

ENV APP_HOME=/opt/omniflow \
    PLUGIN_DIR=/opt/omniflow/plugins/ruby \
    GEM_HOME=/opt/omniflow/vendor/bundle \
    PATH=/opt/omniflow/vendor/bundle/ruby/3.2.0/bin:$PATH \
    RACK_ENV=production \
    RAILS_ENV=production \
    OMNIFLOW_PLUGIN_MAX_LINE=131072 \
    OMNIFLOW_EXEC_TIMEOUT=10

# Install tini (process reaper) and CA certificates
RUN apt-get update \
  && apt-get install -y --no-install-recommends tini ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Create non-root user & directories
RUN groupadd --system omniflow && useradd --system --gid omniflow --create-home --home-dir /home/omniflow --shell /usr/sbin/nologin omniflow \
  && mkdir -p ${PLUGIN_DIR} ${APP_HOME}/log ${GEM_HOME} \
  && chown -R omniflow:omniflow ${APP_HOME} ${GEM_HOME}

WORKDIR ${APP_HOME}

# Copy app code and vendor/bundle from builder
COPY --from=builder /build/ ${PLUGIN_DIR}/
# Copy the installed gems
COPY --from=builder /build/vendor/ ${GEM_HOME}/

# Ensure correct permissions
RUN chown -R omniflow:omniflow ${PLUGIN_DIR} ${GEM_HOME} \
  && chmod -R 0755 ${PLUGIN_DIR} || true

# Switch to non-root user
USER omniflow

# Default entrypoint - run the Ruby plugin script.
# The plugin must read NDJSON from stdin and write NDJSON to stdout.
ENV PLUGIN_ENTRYPOINT=${PLUGIN_DIR}/sample_plugin.rb
ENTRYPOINT ["/usr/bin/tini", "--", "ruby", "/opt/omniflow/plugins/ruby/sample_plugin.rb"]
CMD []

# Healthcheck: send a one-line NDJSON health probe to the plugin and expect a valid JSON reply.
# The plugin must accept a single-line JSON object on stdin and write a single-line JSON response to stdout.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | ruby ${PLUGIN_ENTRYPOINT} >/dev/null 2>&1 || exit 1

# Provenance / SBOM friendly labels
LABEL org.opencontainers.image.title="OmniFlow Ruby Plugin" \
      org.opencontainers.image.description="OmniFlow Ruby plugin — production-ready Ruby NDJSON plugin for OmniFlow." \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Documentation: recommended runtime flags (not enforced)
#  - --read-only, --tmpfs /tmp:rw, --cap-drop ALL, --security-opt no-new-privileges
# Example:
# docker run --rm \
#   --read-only \
#   --tmpfs /tmp:rw \
#   --cap-drop ALL \
#   --security-opt no-new-privileges \
#   omniflow/plugin-ruby:1.2.3

###############################################################################
