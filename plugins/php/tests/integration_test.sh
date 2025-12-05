#!/usr/bin/env bash
#
# integration_test.sh
#
# Integration tests for OmniFlow PHP plugin (plugins/php)
# - Installs PHP deps (if needed)
# - Starts the plugin (php CLI script) with stdin from a FIFO and captures stdout/stderr
# - Sends newline-delimited JSON (NDJSON) requests and validates single-line JSON responses using jq
# - Tests: health, exec (echo/reverse/compute), malformed JSON resilience, oversized payload handling,
#          unsupported action, graceful shutdown
#
# Place at: plugins/php/tests/integration_test.sh
# Run:
#   cd <repo-root>
#   ./plugins/php/tests/integration_test.sh
#
# Requirements:
#   - bash, jq, php (>=8.1), composer (optional if you need to install deps)
#
set -euo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$REPO_ROOT/plugins/php"
TEST_DIR="$(mktemp -d)"
FIFO_IN="$TEST_DIR/plugin.stdin.fifo"
STDOUT_LOG="$TEST_DIR/plugin.stdout.log"
STDERR_LOG="$TEST_DIR/plugin.stderr.log"
BUILD_TIMEOUT=120       # seconds for composer install (if run)
RESP_POLL_INTERVAL=0.12
RESP_WAIT_TIMEOUT=6     # seconds for ordinary responses

# Config overrides (env-friendly)
: "${PLUGIN_CMD:=php ${PLUGIN_DIR}/sample_plugin.php}"   # default launcher; override with OMNIFLOW_PHP_PLUGIN_CMD
: "${RUN_COMPOSER_INSTALL:=false}"                      # set to "true" to run composer install in plugin dir
: "${COMPOSER_FLAGS:="--no-dev --prefer-dist --optimize-autoloader"}"

# Cleanup
cleanup() {
  local rc=$?
  set +e
  echo ""
  echo "=== Cleaning up (exit code: $rc) ==="
  if [[ -n "${PLUGIN_PID:-}" ]]; then
    echo "Killing plugin PID ${PLUGIN_PID}..."
    kill "${PLUGIN_PID}" 2>/dev/null || true
    wait "${PLUGIN_PID}" 2>/dev/null || true
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

command_exists() { command -v "$1" >/dev/null 2>&1; }

# Preconditions
if ! command_exists php ; then
  echo "php not found. Please install PHP (>=8.1)." >&2
  exit 2
fi

if ! command_exists jq ; then
  echo "jq not found. Please install jq." >&2
  exit 2
fi

# Optional: install composer deps if requested
if [[ "$RUN_COMPOSER_INSTALL" == "true" ]]; then
  if ! command_exists composer ; then
    echo "composer not found but RUN_COMPOSER_INSTALL=true; please install composer." >&2
    exit 2
  fi
  echo "=== Running composer install in $PLUGIN_DIR ==="
  pushd "$PLUGIN_DIR" >/dev/null
  if ! timeout "$BUILD_TIMEOUT" composer install $COMPOSER_FLAGS; then
    popd >/dev/null
    fail "composer install failed or timed out"
  fi
  popd >/dev/null
fi

# Prepare FIFO and logs
mkfifo "$FIFO_IN"
: > "$STDOUT_LOG"
: > "$STDERR_LOG"

# Start plugin (stdin from FIFO), capture stdout/stderr
echo "=== Starting plugin: ${PLUGIN_CMD} ==="
# Use stdbuf to encourage line-buffering of stdout/stderr
bash -c "stdbuf -oL -eL ${PLUGIN_CMD} < \"$FIFO_IN\" >> \"$STDOUT_LOG\" 2>> \"$STDERR_LOG\"" &
PLUGIN_PID=$!
sleep 0.12

# Check process alive
if ! kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin failed to start — printing stderr:"
  sed -n '1,200p' "$STDERR_LOG" || true
  fail "Plugin process terminated immediately"
fi
echo "Plugin PID: $PLUGIN_PID"

# Utility: send JSON message (newline-terminated)
send_msg() {
  local json="$1"
  printf '%s\n' "$json" >> "$FIFO_IN"
}

# Utility: extract JSON lines from stdout and filter by id using jq
find_response_by_id() {
  local id="$1"
  tail -n 2000 "$STDOUT_LOG" 2>/dev/null \
    | awk 'match($0,/^\s*\{.*\}\s*$/,m){ print m[0] }' \
    | jq -c --arg ID "$id" 'select(.id == $ID)' 2>/dev/null | head -n1 || true
}

# Wait for response by id with timeout
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

# Short startup wait (some plugins emit a ready event)
sleep 0.25

# ---------- Tests ----------

echo "=== Test: health probe ==="
HEALTH_ID="php-health-1"
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
ECHO_ID="php-echo-1"
send_msg "{\"id\":\"$ECHO_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"echo\",\"args\":{\"message\":\"hello php\"}}}"
if resp="$(wait_for_response "$ECHO_ID")"; then
  echo "Echo response: $resp"
  if ! echo "$resp" | jq -e '.status == "ok" and .body.action == "echo" and .body.message == "hello php"' >/dev/null 2>&1 ; then
    fail "Echo response content mismatch"
  fi
else
  fail "No echo response for id=$ECHO_ID"
fi

echo "=== Test: exec reverse (unicode) ==="
REV_ID="php-rev-1"
send_msg "{\"id\":\"$REV_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"reverse\",\"args\":{\"message\":\"Привет\"}}}"
if resp="$(wait_for_response "$REV_ID")"; then
  echo "Reverse response: $resp"
  if ! echo "$resp" | jq -e '.status == "ok" and .body.action == "reverse" and (.body.message | type == "string")' >/dev/null 2>&1 ; then
    fail "Reverse response invalid"
  fi
else
  fail "No reverse response for id=$REV_ID"
fi

echo "=== Test: exec compute (sum) ==="
CALC_ID="php-calc-1"
send_msg "{\"id\":\"$CALC_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"compute\",\"args\":{\"numbers\":[1,2,3.5,-1.5]}}}"
if resp="$(wait_for_response "$CALC_ID")"; then
  echo "Compute response: $resp"
  if ! echo "$resp" | jq -e '.status == "ok" and .body.action == "compute" and (.body.sum == 10.5 or (.body.sum | tonumber) == 10.5)' >/dev/null 2>&1 ; then
    fail "Compute response mismatch"
  fi
else
  fail "No compute response for id=$CALC_ID"
fi

echo "=== Test: malformed JSON resilience (plugin must NOT crash) ==="
printf '%s\n' 'not a json' >> "$FIFO_IN"
sleep 0.3
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin alive after malformed JSON (good)"
else
  fail "Plugin crashed on malformed JSON"
fi

echo "=== Test: oversized payload survival/response ==="
LARGE_LEN=$((200 * 1024)) # 200 KiB
# Generate large string in a safe streaming manner
LARGE_STR="$(head -c "$LARGE_LEN" < /dev/zero | tr '\0' 'A' | tr -d '\n')"
printf '{"id":"php-large-1","type":"exec","payload":{"action":"echo","args":{"message":"%s"}}}\n' "$LARGE_STR" >> "$FIFO_IN" || true
sleep 0.6
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin survived oversized payload (good)"
else
  fail "Plugin crashed on oversized payload"
fi

echo "=== Test: unsupported action returns error or stays alive ==="
UNK_ID="php-unk-1"
send_msg "{\"id\":\"$UNK_ID\",\"type\":\"exec\",\"payload\":{\"action\":\"does_not_exist\"}}"
if resp="$(wait_for_response "$UNK_ID" 1.5)"; then
  echo "Unsupported action response: $resp"
  if ! echo "$resp" | jq -e '.status == "error" or .status == "busy" or (.code != null)' >/dev/null 2>&1 ; then
    fail "Unsupported action should yield error-like response"
  fi
else
  echo "No explicit unsupported-action response; allowed as long as plugin remains alive"
fi

echo "=== Test: graceful shutdown ==="
SHUT_ID="php-shutdown-1"
send_msg "{\"id\":\"$SHUT_ID\",\"type\":\"shutdown\",\"payload\":null}"
# wait for shutdown response or process exit
if resp="$(wait_for_response "$SHUT_ID" 3)"; then
  echo "Shutdown response: $resp"
else
  echo "No shutdown response received; checking process exit"
fi

# Allow short time for graceful exit
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

echo "=== All PHP plugin integration tests passed ==="
exit 0
