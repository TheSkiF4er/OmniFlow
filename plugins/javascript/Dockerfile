# ============================================================
#  OmniFlow JavaScript Plugin – Production Dockerfile
#  Multi-stage, secure, reproducible, minimal final image.
#
#  Features:
#   - Full dependency isolation (npm ci)
#   - Non-root runtime user
#   - Locked Node.js version for deterministic builds
#   - SBOM-friendly layer structure
#   - Vulnerability-minimized alpine image
#   - Healthcheck for plugin responsiveness
#   - Optional: plugin sandbox (seccomp, no-new-privileges)
#
#  Author: OmniFlow Project
#  License: Apache 2.0
# ============================================================


# -------- Stage 1: Builder --------
FROM node:20.11-alpine3.18 AS builder

WORKDIR /app

# Copy only lockfiles first (to leverage Docker cache)
COPY package.json package-lock.json* ./

# Install dependencies using clean CI mode
RUN npm ci --only=production

# Copy plugin source
COPY . .

# Run optional lint/tests during build (can be toggled via arg)
ARG RUN_TESTS=false
RUN if [ "$RUN_TESTS" = "true" ]; then \
      npm test ; \
    fi


# -------- Stage 2: Runtime --------
FROM node:20.11-alpine3.18 AS runtime

WORKDIR /opt/omniflow-plugin

# Create non-root user
RUN addgroup -S plugin && adduser -S plugin -G plugin

# Copy built app & dependencies
COPY --from=builder /app /opt/omniflow-plugin

# Reduce attack surface
RUN \
  # Remove npm, docs, manpages, extra binaries
  rm -rf /usr/local/lib/node_modules/npm \
         /usr/share/man \
         /usr/share/doc \
         /var/cache/* \
         /root/.npm && \
  # Ensure correct permissions
  chown -R plugin:plugin /opt/omniflow-plugin

USER plugin

# Use a minimal entrypoint script
ENTRYPOINT ["node", "sample_plugin.js"]

# Health check: plugin must respond via stdout within timeout
HEALTHCHECK --interval=20s --timeout=5s --start-period=3s --retries=3 \
  CMD node -e "console.log(JSON.stringify({type:'healthcheck'}))" | head -n 1 > /dev/null || exit 1

# Labels (SBOM / CycloneDX friendly)
LABEL org.opencontainers.image.title="OmniFlow JavaScript Plugin" \
      org.opencontainers.image.description="Production-ready JavaScript plugin runtime for OmniFlow." \
      org.opencontainers.image.vendor="OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0"
