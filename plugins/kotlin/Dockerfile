# OmniFlow Kotlin Plugin Dockerfile (multi-stage)
#
# Location: OmniFlow/plugins/kotlin/Dockerfile
#
# Purpose:
#   - Multi-stage build using Gradle to produce a single executable/fat JAR (ShadowJar)
#   - Small, secure runtime image that runs as a non-root user
#   - Reproducible build metadata via build-args (VERSION, VCS_REF, BUILD_DATE)
#   - Optional build caches for faster CI builds (requires BuildKit)
#   - Lightweight healthcheck that probes the plugin with an NDJSON health message
#
# Usage (example):
#   docker build \
#     --build-arg VERSION=1.2.3 \
#     --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t omniflow/plugin-kotlin:1.2.3 \
#     -f plugins/kotlin/Dockerfile .
#
# Notes:
#   - This Dockerfile prefers to use the Gradle wrapper if present (./gradlew).
#   - For reproducible CI builds set VERSION, VCS_REF and BUILD_DATE in the pipeline.
#   - The built JAR is expected at build/libs/*.jar (ShadowJar configuration).
#   - Healthcheck runs the JAR as a one-shot subprocess and pipes a small NDJSON health probe.
#
# BuildKit caching hint (optional, for faster CI builds):
#   docker buildx build --progress=plain --tag ... --file ... .
# ------------------------------------------------------------------------------

# ---------------------------
# Builder stage (Gradle)
# ---------------------------
FROM gradle:8.6-jdk17 AS builder

ARG VERSION=unreleased
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG GRADLE_USER_HOME=/home/gradle/.gradle

# Use non-root gradle user provided by the official Gradle image
WORKDIR /home/gradle/project

# Copy Gradle wrapper and settings first for cache efficiency if present
# (this copy is safe if wrapper files don't exist; build will still work using system gradle)
COPY gradlew gradlew
COPY gradle/ gradle/

# Copy project files
COPY . .

# Ensure wrapper is executable if present
RUN if [ -f "./gradlew" ]; then chmod +x ./gradlew; fi

# Build with caches where possible. This uses BuildKit cache mounts when available.
# The --no-daemon flag avoids background daemons in CI; adjust as necessary.
RUN --mount=type=cache,target=${GRADLE_USER_HOME} \
    set -eux; \
    # Expose build metadata to Gradle via system properties/gradle properties
    export VERSION_ARG="${VERSION}"; \
    export VCS_REF_ARG="${VCS_REF}"; \
    export BUILD_DATE_ARG="${BUILD_DATE}"; \
    # Prefer wrapper if present
    if [ -x "./gradlew" ]; then \
      ./gradlew --no-daemon clean shadowJar -Pversion="${VERSION_ARG}" -PbuildDate="${BUILD_DATE_ARG}" -PvcsRef="${VCS_REF_ARG}"; \
    else \
      gradle --no-daemon clean shadowJar -Pversion="${VERSION_ARG}" -PbuildDate="${BUILD_DATE_ARG}" -PvcsRef="${VCS_REF_ARG}"; \
    fi; \
    ls -al build/libs || true

# Locate the fat JAR produced by the Shadow plugin. Use a deterministic copy name.
RUN set -eux; \
    outdir=/out; mkdir -p ${outdir}; \
    jarfile=$(ls build/libs/*.jar | head -n1) || jarfile=""; \
    if [ -n "$jarfile" ] && [ -f "$jarfile" ]; then cp "$jarfile" ${outdir}/omniflow-plugin-kotlin.jar; else echo "ERROR: build artifact not found"; exit 1; fi; \
    ls -lh ${outdir}/omniflow-plugin-kotlin.jar

# ---------------------------
# Runtime stage
# ---------------------------
FROM eclipse-temurin:17-jre-alpine AS runtime
# Note: eclipse-temurin:17-jre-alpine is compact and suitable for most deployments.
# If you require glibc-specific libs, switch to debian-based slim images.

ARG VERSION=unreleased
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG JVM_OPTS="-Xms64m -Xmx256m"

ENV APP_USER=omniflow \
    APP_GROUP=omniflow \
    APP_HOME=/opt/omniflow \
    BINDIR=/opt/omniflow/bin \
    LOGDIR=/var/log/omniflow

# Create non-root user and directories
RUN addgroup -S ${APP_GROUP} && adduser -S -G ${APP_GROUP} ${APP_USER} \
 && mkdir -p ${BINDIR} ${LOGDIR} ${APP_HOME} \
 && chown -R ${APP_USER}:${APP_GROUP} ${APP_HOME} ${LOGDIR}

WORKDIR ${APP_HOME}

# Copy built JAR from builder
COPY --from=builder /out/omniflow-plugin-kotlin.jar ${BINDIR}/omniflow-plugin-kotlin.jar

# Ensure correct permissions
RUN chown ${APP_USER}:${APP_GROUP} ${BINDIR}/omniflow-plugin-kotlin.jar \
 && chmod 0755 ${BINDIR}/omniflow-plugin-kotlin.jar

# Switch to non-root user
USER ${APP_USER}

# Entrypoint: run the plugin JAR (plugin reads NDJSON from stdin and writes NDJSON to stdout).
# Expose an environment variable to override JVM options at runtime (useful for tuning).
ENV JVM_OPTS="${JVM_OPTS}"

ENTRYPOINT ["sh","-c","exec java $JVM_OPTS -jar /opt/omniflow/bin/omniflow-plugin-kotlin.jar"]
CMD []

# Healthcheck: send a one-line NDJSON health probe to the plugin and expect it to exit 0/produce output.
# The plugin must accept a one-shot JSON line on stdin and write a single-line JSON response to stdout.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD printf '%s\n' '{"id":"hc-1","type":"health","payload":null}' | java $JVM_OPTS -jar /opt/omniflow/bin/omniflow-plugin-kotlin.jar >/dev/null 2>&1 || exit 1

# Labels / metadata for provenance and SBOM tooling
LABEL org.opencontainers.image.title="OmniFlow Kotlin Plugin" \
      org.opencontainers.image.description="OmniFlow Kotlin plugin (omniflow-plugin-kotlin) — secure, production-ready plugin for OmniFlow." \
      org.opencontainers.image.vendor="TheSkiF4er / OmniFlow" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Final notes:
# - For smaller runtime images: consider using a distroless or "slim" JRE base. If using distroless,
#   ensure the JAR is fully self-contained (no native libs) and you have a debugging plan (e.g., sidecar).
# - To enable remote debugging or attach a profiler in CI, set JVM_OPTS via docker run -e JVM_OPTS="...".
