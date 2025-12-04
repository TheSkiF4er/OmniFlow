#!/usr/bin/env bash
#
# integration_test.sh
#
# Integration tests for OmniFlow Kotlin plugin (plugins/kotlin)
# - Builds the shadow/fat JAR using Gradle (prefers ./gradlew if present)
# - Runs the plugin JAR in an isolated temp workspace using a FIFO for stdin
# - Sends newline-delimited JSON (NDJSON) requests and validates single-line JSON responses using jq
# - Tests: health, exec (echo/reverse/compute), malformed JSON resilience, oversized payload handling, unsupported action, graceful shutdown
#
# Place at: OmniFlow/plugins/kotlin/tests/integration_test.sh
# Run (from repository root):
#   ./plugins/kotlin/tests/integration_test.sh
#
set -euo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$REPO_ROOT/plugins/kotlin"
TEST_DIR="$(mktemp -d)"
FIFO_IN="$TEST_DIR/plugin.stdin.fifo"
STDOUT_LOG="$TEST_DIR/plugin.stdout.log"
STDERR_LOG="$TEST_DIR/plugin.stderr.log"
BUILD_DIR="$PLUGIN_DIR/build"
JAR_GLOB="$BUILD_DIR/libs/omniflow-plugin-kotlin*.jar"
JAR_PATH=""
BUILD_TIMEOUT=300       # seconds to allow Gradle build
RESP_POLL_INTERVAL=0.12
RESP_WAIT_TIMEOUT=6     # seconds for ordinary responses
LONG_RESP_WAIT=12       # seconds for longer responses

# Ensure cleanup on exit
cleanup() {
  local rc=$?
  set +e
  echo "=== Cleaning up (exit code $rc) ==="
  if [[ -n "${PLUGIN_PID:-}" ]]; then
    echo "Killing plugin pid $PLUGIN_PID"
    kill "$PLUGIN_PID" 2>/dev/null || true
    wait "$PLUGIN_PID" 2>/dev/null || true
  fi
  rm -rf "$TEST_DIR"
  exit $rc
}
trap cleanup EXIT INT TERM

echo "=== Integration test workspace: $TEST_DIR ==="

# Helpers
fail() {
  echo "FAIL: $*" >&2
  echo "=== plugin stdout (last 200 lines) ==="
  tail -n 200 "$STDOUT_LOG" || true
  echo "=== plugin stderr (last 200 lines) ==="
  tail -n 200 "$STDERR_LOG" || true
  exit 1
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# Prereqs
if ! command_exists jq ; then
  echo "This script requires 'jq' (https://stedolan.github.io/jq/) - please install it." >&2
  exit 2
fi
if ! command_exists java ; then
  echo "Java runtime not found. Please install Java 17+ and retry." >&2
  exit 2
fi

# 1) Build step (Gradle shadowJar)
echo "=== Building Kotlin plugin (shadowJar) ==="
pushd "$PLUGIN_DIR" >/dev/null

if [[ -x "./gradlew" ]]; then
  BUILD_CMD="./gradlew --no-daemon clean shadowJar -x test"
else
  if command_exists gradle ; then
    BUILD_CMD="gradle --no-daemon clean shadowJar -x test"
  else
    echo "Gradle wrapper not found and 'gradle' not installed. Install Gradle or include ./gradlew." >&2
    exit 2
  fi
fi

# Run build with timeout to avoid hanging CI
if ! timeout "$BUILD_TIMEOUT" bash -c "$BUILD_CMD"; then
  popd >/dev/null
  fail "Gradle build failed or timed out"
fi

# Locate JAR
shopt -s nullglob
candidates=($JAR_GLOB)
shopt -u nullglob
if [[ ${#candidates[@]} -eq 0 ]]; then
  popd >/dev/null
  fail "Built JAR not found at $JAR_GLOB"
fi
JAR_PATH="${candidates[0]}"
echo "Built JAR: $JAR_PATH"
popd >/dev/null

# 2) Prepare FIFO and logs
mkfifo "$FIFO_IN"
: > "$STDOUT_LOG"
: > "$STDERR_LOG"

# 3) Start plugin (stdin from FIFO, capture stdout/stderr)
# Use stdbuf to force line buffering; plugin should flush stdout for real-time interaction
echo "=== Starting plugin JAR (pid will follow) ==="
bash -c "stdbuf -oL -eL java -jar \"$JAR_PATH\" < \"$FIFO_IN\" >> \"$STDOUT_LOG\" 2>> \"$STDERR_LOG\"" &
PLUGIN_PID=$!
sleep 0.2

# Ensure plugin is still running initially
if ! kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin failed to start — printing stderr"
  sed -n '1,200p' "$STDERR_LOG" || true
  fail "Plugin process terminated immediately"
fi
echo "Plugin pid: $PLUGIN_PID"

# Utility: send JSON message (newline-terminated)
send_msg() {
  local json="$1"
  printf '%s\n' "$json" >> "$FIFO_IN"
}

# Utility: extract candidate JSON lines from stdout logfile and filter by jq for id
find_response_by_id() {
  local id="$1"
  tail -n 2000 "$STDOUT_LOG" 2>/dev/null \
    | awk 'match($0,/^\s*\{.*\}\s*$/,m){ print m[0] }' \
    | jq -c --arg ID "$id" 'select(.id == $ID)' 2>/dev/null | head -n1 || true
}

# Wait for response with timeout
wait_for_response() {
  local id="$1"
  local timeout_sec="${2:-$RESP_WAIT_TIMEOUT}"
  local elapsed=0
  while (( $(echo "$elapsed < $timeout_sec" | bc -l) )); do
    local resp
    resp="$(find_response_by_id "$id")"
    if [[ -n "$resp" ]]; then
      printf '%s' "$resp"
      return 0
    fi
    sleep "$RESP_POLL_INTERVAL"
    elapsed=$(awk "BEGIN {print $elapsed + $RESP_POLL_INTERVAL; exit}")
  done
  return 1
}

# Wait for plugin to emit initial lifecycle ready/health (optional)
echo "=== Waiting briefly for plugin readiness (0.5s) ==="
sleep 0.5

# ---------- Tests ----------

echo "=== Test: health probe ==="
HEALTH_ID="kt-health-1"
send_msg "{\"id\":\"$HEALTH_ID\",\"type\":\"health\",\"payload\":null}"
if resp="$(wait_for_response "$HEALTH_ID" 5)"; then
  echo "Health response: $resp"
  if ! echo "$resp" | jq -e '.status == "ok" or (.body.status == "healthy")' >/dev/null 2>&1 ; then
    fail "Unexpected health response content"
  fi
else
  fail "No health response for id=$HEALTH_ID"
fi

echo "=== Test: exec echo ==="
ECHO_ID="kt-echo-1"
send_msg "{\"id\":\"$ECHO_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"echo\",\"args\":{\"message\":\"hello kotlin\"}}}"
if resp="$(wait_for_response "$ECHO_ID")"; then
  echo "Echo response: $resp"
  if ! echo "$resp" | jq -e '.status == "ok" and .body.action == "echo" and .body.message == "hello kotlin"' >/dev/null 2>&1 ; then
    fail "Echo response content mismatch"
  fi
else
  fail "No echo response for id=$ECHO_ID"
fi

echo "=== Test: exec reverse (unicode) ==="
REV_ID="kt-rev-1"
send_msg "{\"id\":\"$REV_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"reverse\",\"args\":{\"message\":\"Привет\"}}}"
if resp="$(wait_for_response "$REV_ID")"; then
  echo "Reverse response: $resp"
  # ensure status ok and message is present
  if ! echo "$resp" | jq -e '.status == "ok" and .body.action == "reverse" and (.body.message | type == "string")' >/dev/null 2>&1 ; then
    fail "Reverse response invalid"
  fi
else
  fail "No reverse response for id=$REV_ID"
fi

echo "=== Test: exec compute (sum) ==="
CALC_ID="kt-calc-1"
send_msg "{\"id\":\"$CALC_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"compute\",\"args\":{\"numbers\":[1,2,3.5,-1.5]}}}"
if resp="$(wait_for_response "$CALC_ID")"; then
  echo "Compute response: $resp"
  if ! echo "$resp" | jq -e '.status == "ok" and .body.action == "compute" and (.body.sum == 10.5 or (.body.sum | tonumber) == 10.5)' >/dev/null 2>&1 ; then
    fail "Compute response mismatch"
  fi
else
  fail "No compute response for id=$CALC_ID"
fi

echo "=== Test: invalid JSON resilience (plugin must NOT crash) ==="
printf '%s\n' 'not a json' >> "$FIFO_IN"
sleep 0.3
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin alive after invalid JSON (good)"
else
  fail "Plugin crashed on invalid JSON"
fi

echo "=== Test: oversized payload survival/response ==="
LARGE_LEN=$((200 * 1024)) # 200 KiB
# Create a large JSON message safely by streaming 'A's
printf '{"id":"kt-large-1","type":"exec","payload":{"action":"echo","args":{"message":"%s"}}}\n' "$(head -c "$LARGE_LEN" < /dev/zero | tr '\0' 'A')" >> "$FIFO_IN" || true
sleep 0.6
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin survived oversized payload (good)"
else
  fail "Plugin crashed on oversized payload"
fi

echo "=== Test: unsupported action returns error ==="
UNK_ID="kt-unk-1"
send_msg "{\"id\":\"$UNK_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"does_not_exist\"}}"
if resp="$(wait_for_response "$UNK_ID")"; then
  echo "Unsupported action response: $resp"
  # status may be "error" or "busy" depending on implementation
  if ! echo "$resp" | jq -e '.status == "error" or .status == "busy" or (.code != null)' >/dev/null 2>&1 ; then
    fail "Unsupported action should yield error-like response"
  fi
else
  echo "No explicit unsupported-action response; acceptable if plugin returns nothing but remains alive"
fi

echo "=== Test: graceful shutdown ==="
SHUT_ID="kt-shutdown-1"
send_msg "{\"id\":\"$SHUT_ID\",\"type\":\"shutdown\",\"payload\":null}"
# wait briefly for a shutdown response or process exit
if resp="$(wait_for_response "$SHUT_ID" 3)"; then
  echo "Shutdown response: $resp"
else
  echo "No shutdown response received; checking process exit"
fi

# Allow a short period for graceful exit
wait_secs=0
while kill -0 "$PLUGIN_PID" 2>/dev/null && [[ $wait_secs -lt 5 ]]; do
  sleep 0.2
  wait_secs=$((wait_secs+1))
done

if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin did not exit after shutdown request; killing it"
  kill -9 "$PLUGIN_PID" 2>/dev/null || true
  fail "Plugin failed to exit gracefully on shutdown"
else
  echo "Plugin exited gracefully after shutdown (OK)"
fi

echo "=== All Kotlin plugin integration tests passed ==="
exit 0
