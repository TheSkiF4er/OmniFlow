#!/usr/bin/env bash
#
# run_all_plugins.sh
#
# Purpose:
#   Launch a sandbox with example OmniFlow plugins (C, TypeScript/Node, Python, Go)
#   - Default: runs plugins as Docker containers
#   - Fallback: runs local binaries if Docker not available / --local is used
#   - Performs health checks, tails logs, and ensures cleanup on exit
#
# Location: OmniFlow/plugins/examples/run_all_plugins.sh
# Usage:
#   # Run containers (default)
#   ./run_all_plugins.sh
#
#   # Build images from plugin folders then run
#   ./run_all_plugins.sh --build-images
#
#   # Force local binary mode (no docker)
#   ./run_all_plugins.sh --local \
#        --c-bin /path/to/sample_plugin_c \
#        --ts-cmd "node /path/to/sample_plugin.js" \
#        --py-cmd "python3 /path/to/sample_plugin.py"
#
#   # Show help
#   ./run_all_plugins.sh --help
#
set -euo pipefail
IFS=$'\n\t'

### -------------------------
### Configuration (defaults)
### -------------------------
# Default Docker image names (override with env or CLI)
C_IMAGE_DEFAULT="omniflow/plugin-c:latest"
TS_IMAGE_DEFAULT="omniflow/plugin-ts:latest"
PY_IMAGE_DEFAULT="omniflow/plugin-py:latest"
GO_IMAGE_DEFAULT="omniflow/plugin-go:latest"

# Default container names
C_NAME="omni-c-sample"
TS_NAME="omni-ts-sample"
PY_NAME="omni-py-sample"
GO_NAME="omni-go-sample"

# Default local binary commands (if --local mode)
C_BIN_DEFAULT="./plugins/c/build/sample_plugin"    # adjust if your build output differs
TS_CMD_DEFAULT="node ./plugins/javascript/dist/sample_plugin.js"
PY_CMD_DEFAULT="python3 ./plugins/python/sample_plugin.py"
GO_BIN_DEFAULT="./plugins/go/build/sample_plugin_go"

# Health JSON probe
HC_JSON='{"id":"hc-1","type":"health","payload":null}'

# Timeouts
HEALTH_RETRIES=6            # number of times to check health before giving up
HEALTH_INTERVAL=2           # seconds between health checks
SHUTDOWN_WAIT=5             # seconds to wait after sending shutdown before force kill

# Log directory (when using local binaries)
LOG_DIR="./plugins/examples/logs"
MKDIR_P="mkdir -p"

# Flags (set later via CLI)
MODE="docker"               # or "local"
BUILD_IMAGES=0
KEEP_CONTAINERS=0           # if set, don't remove containers after run
QUIET=0

### -------------------------
### Helpers
### -------------------------
usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --local                       Run local binaries instead of Docker containers
  --c-image IMAGE               Override C plugin image (Docker mode)
  --ts-image IMAGE              Override TypeScript plugin image (Docker mode)
  --py-image IMAGE              Override Python plugin image (Docker mode)
  --go-image IMAGE              Override Go plugin image (Docker mode)
  --c-bin PATH                  Override C local binary (local mode)
  --ts-cmd "command"            Override TypeScript local command (local mode)
  --py-cmd "command"            Override Python local command (local mode)
  --go-bin PATH                 Override Go local binary (local mode)
  --build-images                Build Docker images from local plugin directories before running
  --keep                        Keep containers running after script exit (do not remove)
  --quiet                       Minimal output
  --help                        Show this help
EOF
  exit 1
}

log() {
  if [ "$QUIET" -eq 0 ]; then
    printf '%s\n' "$*"
  fi
}

err() { printf 'ERROR: %s\n' "$*" >&2; }

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# Wait for given container or local command to respond to health probe
wait_for_health_docker() {
  local container="$1"
  local tries=0
  while [ $tries -lt $HEALTH_RETRIES ]; do
    # call the plugin binary INSIDE the container piping the health JSON
    if docker exec -i "$container" /bin/sh -c "cat >/dev/null <<'HC'\n$HC_JSON\nHC\n/opt/omniflow/bin/sample_plugin" >/dev/null 2>&1; then
      log "✔ $container healthy"
      return 0
    fi
    tries=$((tries+1))
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

wait_for_health_local() {
  local name="$1"
  local cmd="$2"
  local logf="$3"
  local tries=0
  while [ $tries -lt $HEALTH_RETRIES ]; do
    if echo "$HC_JSON" | eval "$cmd" >/dev/null 2>&1; then
      log "✔ $name healthy"
      return 0
    fi
    tries=$((tries+1))
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

# Graceful shutdown handler
cleanup() {
  local code=$?
  set +e
  log ""
  log "Shutting down (trap invoked, exit code: $code)..."

  if [ "$MODE" = "docker" ]; then
    if [ "$KEEP_CONTAINERS" -eq 0 ]; then
      log "Stopping containers..."
      docker stop -t "$SHUTDOWN_WAIT" "$C_NAME" "$TS_NAME" "$PY_NAME" "$GO_NAME" >/dev/null 2>&1 || true
      log "Removing containers..."
      docker rm -f "$C_NAME" "$TS_NAME" "$PY_NAME" "$GO_NAME" >/dev/null 2>&1 || true
    else
      log "Keeping containers running as requested (--keep)."
    fi
  else
    # local mode: try to find PIDs from tmp files and kill them
    for f in "$TEST_DIR"/*-pid 2>/dev/null || true; do
      [ -f "$f" ] || continue
      pid=$(cat "$f" 2>/dev/null || true)
      if [ -n "$pid" ]; then
        log "Killing pid $pid (from $f)..."
        kill "$pid" 2>/dev/null || true
      fi
    done
  fi

  log "Cleanup complete."
  exit $code
}

trap cleanup EXIT INT TERM

### -------------------------
### CLI parse
### -------------------------
# default overrides
C_IMAGE="$C_IMAGE_DEFAULT"
TS_IMAGE="$TS_IMAGE_DEFAULT"
PY_IMAGE="$PY_IMAGE_DEFAULT"
GO_IMAGE="$GO_IMAGE_DEFAULT"

C_BIN="$C_BIN_DEFAULT"
TS_CMD="$TS_CMD_DEFAULT"
PY_CMD="$PY_CMD_DEFAULT"
GO_BIN="$GO_BIN_DEFAULT"

# Temporary workspace for local mode pids
TEST_DIR="$(mktemp -d)"
# Note: TEST_DIR will be removed by cleanup via trap

while [ $# -gt 0 ]; do
  case "$1" in
    --local) MODE="local"; shift ;;
    --build-images) BUILD_IMAGES=1; shift ;;
    --c-image) C_IMAGE="$2"; shift 2 ;;
    --ts-image) TS_IMAGE="$2"; shift 2 ;;
    --py-image) PY_IMAGE="$2"; shift 2 ;;
    --go-image) GO_IMAGE="$2"; shift 2 ;;
    --c-bin) C_BIN="$2"; shift 2 ;;
    --ts-cmd) TS_CMD="$2"; shift 2 ;;
    --py-cmd) PY_CMD="$2"; shift 2 ;;
    --go-bin) GO_BIN="$2"; shift 2 ;;
    --keep) KEEP_CONTAINERS=1; shift ;;
    --quiet) QUIET=1; shift ;;
    --help|-h) usage ;;
    *) err "Unknown arg: $1"; usage ;;
  esac
done

### -------------------------
### Preconditions
### -------------------------
if [ "$MODE" = "docker" ]; then
  if ! command_exists docker ; then
    err "docker not found. Either install docker or run with --local."
    exit 2
  fi
fi

if [ "$MODE" = "local" ]; then
  $MKDIR_P "$LOG_DIR"
fi

### -------------------------
### Optional: build images
### -------------------------
if [ "$BUILD_IMAGES" -eq 1 ] && [ "$MODE" = "docker" ]; then
  log "Building plugin images (this may take a while)..."
  # Build images from plugin folders if Dockerfiles exist. Best-effort; continue on failure.
  for plugin_dir in "plugins/c" "plugins/javascript" "plugins/python" "plugins/go"; do
    if [ -f "$plugin_dir/Dockerfile" ]; then
      name=""
      case "$plugin_dir" in
        plugins/c) name="$C_IMAGE" ;;
        plugins/javascript) name="$TS_IMAGE" ;;
        plugins/python) name="$PY_IMAGE" ;;
        plugins/go) name="$GO_IMAGE" ;;
      esac
      log "Building $name from $plugin_dir..."
      if docker build -t "$name" -f "$plugin_dir/Dockerfile" "$plugin_dir"; then
        log "Built $name"
      else
        err "Failed to build $name from $plugin_dir (continuing)..."
      fi
    else
      log "No Dockerfile in $plugin_dir; skipping build for that plugin"
    fi
  done
fi

### -------------------------
### Run plugins (Docker mode)
### -------------------------
if [ "$MODE" = "docker" ]; then
  log "Launching plugins as Docker containers (secure defaults)..."

  # Common docker run options
  read -r -a COMMON_OPTS <<< "--restart on-failure:3 --read-only --tmpfs /tmp:rw --tmpfs /run:rw --cap-drop ALL --security-opt no-new-privileges"

  # C plugin
  docker rm -f "$C_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$C_NAME" \
    -e OMNIFLOW_PLUGIN_MAX_LINE=131072 -e OMNIFLOW_EXEC_TIMEOUT=10 -e OMNIFLOW_LOG_JSON=false \
    "${COMMON_OPTS[@]}" \
    "$C_IMAGE" >/dev/null
  log "Started $C_NAME (image: $C_IMAGE)"

  # TypeScript plugin
  docker rm -f "$TS_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$TS_NAME" \
    -e OMNIFLOW_PLUGIN_MAX_LINE=131072 -e OMNIFLOW_EXEC_TIMEOUT=8 -e OMNIFLOW_LOG_JSON=true \
    "${COMMON_OPTS[@]}" \
    "$TS_IMAGE" >/dev/null
  log "Started $TS_NAME (image: $TS_IMAGE)"

  # Python plugin
  docker rm -f "$PY_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$PY_NAME" \
    -e OMNIFLOW_PLUGIN_MAX_LINE=131072 -e OMNIFLOW_EXEC_TIMEOUT=10 -e OMNIFLOW_LOG_JSON=false \
    "${COMMON_OPTS[@]}" \
    "$PY_IMAGE" >/dev/null
  log "Started $PY_NAME (image: $PY_IMAGE)"

  # Go plugin
  docker rm -f "$GO_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$GO_NAME" \
    -e OMNIFLOW_PLUGIN_MAX_LINE=131072 -e OMNIFLOW_EXEC_TIMEOUT=8 -e OMNIFLOW_LOG_JSON=false \
    "${COMMON_OPTS[@]}" \
    "$GO_IMAGE" >/dev/null
  log "Started $GO_NAME (image: $GO_IMAGE)"

  log "Waiting for plugins to become healthy (up to $((HEALTH_RETRIES*HEALTH_INTERVAL))s each)..."
  ALL_OK=1
  for c in "$C_NAME" "$TS_NAME" "$PY_NAME" "$GO_NAME"; do
    if ! wait_for_health_docker "$c"; then
      err "Health check failed for $c"
      ALL_OK=0
    fi
  done

  if [ "$ALL_OK" -ne 1 ]; then
    err "One or more plugins failed health check. Inspect logs with: docker logs <container>"
    # continue to tail logs so user can debug
  else
    log "All plugins healthy."
  fi

  # Tail container logs until user interrupts
  log ""
  log "Tailing container logs. Press Ctrl-C to stop and cleanup."
  log "To inspect logs individually: docker logs -f $C_NAME"
  # Use docker-compose-like aggregated tail if available (docker logs -f --since ...), else tail each in background
  docker logs -f "$C_NAME" &
  PID_LOG_C=$!
  docker logs -f "$TS_NAME" &
  PID_LOG_TS=$!
  docker logs -f "$PY_NAME" &
  PID_LOG_PY=$!
  docker logs -f "$GO_NAME" &
  PID_LOG_GO=$!

  # Wait for any log follower to exit (user pressed Ctrl-C will trigger trap)
  wait

else
### -------------------------
### Run plugins (Local binary mode)
### -------------------------
  log "Launching plugins as local processes (logging to $LOG_DIR)..."
  $MKDIR_P "$LOG_DIR"

  # Function to start a background process and record pid
  start_local() {
    local name="$1"; shift
    local cmd="$*"
    local out="$LOG_DIR/${name}.out.log"
    local errf="$LOG_DIR/${name}.err.log"
    log "Starting $name -> '$cmd' (stdout -> $out, stderr -> $errf)"
    # Use stdbuf to line-buffer output for interactive logs
    # Start process in background in its own process group
    (stdbuf -oL -eL sh -c "$cmd") >"$out" 2>"$errf" &
    local pid=$!
    echo "$pid" > "$TEST_DIR/${name}-pid"
    log "$name pid=$pid"
  }

  start_local "c-plugin" "$C_BIN"
  start_local "ts-plugin" "$TS_CMD"
  start_local "py-plugin" "$PY_CMD"
  start_local "go-plugin" "$GO_BIN"

  # Wait for health for each local
  ALL_OK=1
  sleep 0.3
  if ! wait_for_health_local "c-plugin" "$C_BIN" "$LOG_DIR/c-plugin.out.log"; then err "c-plugin health failed"; ALL_OK=0; fi
  if ! wait_for_health_local "ts-plugin" "$TS_CMD" "$LOG_DIR/ts-plugin.out.log"; then err "ts-plugin health failed"; ALL_OK=0; fi
  if ! wait_for_health_local "py-plugin" "$PY_CMD" "$LOG_DIR/py-plugin.out.log"; then err "py-plugin health failed"; ALL_OK=0; fi
  if ! wait_for_health_local "go-plugin" "$GO_BIN" "$LOG_DIR/go-plugin.out.log"; then err "go-plugin health failed"; ALL_OK=0; fi

  if [ "$ALL_OK" -ne 1 ]; then
    err "One or more local plugins failed health check. Check logs under $LOG_DIR"
  else
    log "All local plugins healthy."
  fi

  log ""
  log "Tailing local plugin logs. Press Ctrl-C to stop and cleanup."
  tail -F "$LOG_DIR"/*.out.log &
  wait
fi

# End of script. cleanup() will be triggered by trap on EXIT.
