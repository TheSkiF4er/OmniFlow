#!/usr/bin/env bash
#
# integration_test.sh
#
# Integration tests for OmniFlow Go plugin (plugins/go)
# - Builds the plugin with `go build`
# - Runs the plugin in an isolated temp workspace using a FIFO for stdin
# - Sends newline-delimited JSON requests and validates responses using jq
# - Tests: health, exec (echo/reverse/compute), invalid JSON, oversized payload, unsupported action, graceful shutdown
#
# Place at: plugins/go/test/integration_test.sh
# Run:
#   cd <repo-root>
#   ./plugins/go/test/integration_test.sh
#
set -euo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$REPO_ROOT/plugins/go"
TEST_DIR="$(mktemp -d)"
FIFO_IN="$TEST_DIR/plugin.stdin.fifo"
STDOUT_LOG="$TEST_DIR/plugin.stdout.log"
STDERR_LOG="$TEST_DIR/plugin.stderr.log"
BUILD_DIR="$PLUGIN_DIR/build"
BIN_PATH="$BUILD_DIR/omni_plugin_go"
BUILD_TIMEOUT=120
RESP_POLL_INTERVAL=0.12
RESP_WAIT_TIMEOUT=6      # seconds to wait for ordinary responses
LONG_RESP_WAIT=12        # seconds for heavier responses (if needed)

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

# Helper: fail with diagnostics
fail() {
  echo "FAIL: $*" >&2
  echo "=== plugin stdout (last 200 lines) ==="
  tail -n 200 "$STDOUT_LOG" || true
  echo "=== plugin stderr (last 200 lines) ==="
  tail -n 200 "$STDERR_LOG" || true
  exit 1
}

# Check prerequisites
command -v jq >/dev/null 2>&1 || { echo "jq is required but not installed. Install jq."; exit 2; }
command -v go >/dev/null 2>&1 || { echo "go (Go toolchain) is required but not installed. Install Go."; exit 2; }
command -v timeout >/dev/null 2>&1 || { echo "timeout (coreutils) required but not installed. Install coreutils."; exit 2; }

# 1) Build step (go build)
echo "=== Building Go plugin in $PLUGIN_DIR ==="
mkdir -p "$BUILD_DIR"
# try to build into BUILD_DIR
if ! (cd "$PLUGIN_DIR" && timeout "${BUILD_TIMEOUT}" go build -o "$BIN_PATH" ./... ); then
  fail "go build failed"
fi

[[ -x "$BIN_PATH" ]] || fail "Built binary not found or not executable at $BIN_PATH"
echo "Built binary: $BIN_PATH"

# 2) Prepare FIFO and logs
mkfifo "$FIFO_IN"
: > "$STDOUT_LOG"
: > "$STDERR_LOG"

# 3) Start plugin (stdin from FIFO, capture stdout/stderr)
# Use stdbuf to force line buffering; plugin should flush stdout for real-time interaction
bash -c "stdbuf -oL -eL '$BIN_PATH' < '$FIFO_IN' >> '$STDOUT_LOG' 2>> '$STDERR_LOG'" &
PLUGIN_PID=$!
sleep 0.15  # give plugin a moment to initialize

# Ensure plugin is still running initially
if ! kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin failed to start — printing stderr"
  sed -n '1,200p' "$STDERR_LOG" || true
  fail "Plugin process terminated immediately"
fi

# Utility: send JSON message (newline-terminated)
send_msg() {
  local json="$1"
  printf '%s\n' "$json" >> "$FIFO_IN"
}

# Utility: extract candidate JSON lines from stdout logfile and filter by jq for id
find_response_by_id() {
  local id="$1"
  # get last 2000 lines (avoid huge scan), then filter lines that look like JSON objects
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

# Helper to assert response by jq predicate
assert_response() {
  local test_name="$1"; shift
  local id="$1"; shift
  local payload="$1"; shift
  local jq_predicate="$1"; shift
  echo "=== Test: $test_name (id=$id) ==="
  send_msg "$payload"
  local resp
  if ! resp="$(wait_for_response "$id" "$RESP_WAIT_TIMEOUT")"; then
    echo "Timeout waiting for response to id=$id"
    fail "$test_name: no response within ${RESP_WAIT_TIMEOUT}s"
  fi
  echo "Response: $resp"
  if ! echo "$resp" | jq -e "$jq_predicate" >/dev/null 2>&1; then
    echo "Assertion failed for $test_name. Predicate: $jq_predicate"
    echo "Full response: $resp"
    fail "$test_name: assertion failed"
  fi
  echo "OK: $test_name"
}

# Give plugin a bit of time to warm up
sleep 0.2

# === Tests ===

# 1) health
assert_response "health" "go-health-1" '{"id":"go-health-1","type":"health","payload":null}' '.status == "ok" and (.body.status == "healthy" or (.body.status == "ok"))'

# 2) exec echo
assert_response "exec-echo" "go-echo-1" '{"id":"go-echo-1","type":"exec","payload":{"action":"echo","args":{"message":"hello go"}}}' '.status == "ok" and .body.action == "echo" and .body.message == "hello go"'

# 3) exec reverse (unicode test)
assert_response "exec-reverse" "go-rev-1" '{"id":"go-rev-1","type":"exec","payload":{"action":"reverse","args":{"message":"Привет"}}}' '.status == "ok" and .body.action == "reverse" and .body.message == "тевирП"'

# 4) exec compute (sum)
assert_response "exec-compute" "go-calc-1" '{"id":"go-calc-1","type":"exec","payload":{"action":"compute","args":{"numbers":[1,2,3,4.5]}}}' '.status == "ok" and .body.action == "compute" and (.body.sum == 10.5 or (.body.sum | tonumber) == 10.5)'

# 5) invalid JSON — plugin should not crash (we don't assert specific response, only liveness)
echo "=== Test: invalid-json ==="
printf '%s\n' 'not a json' >> "$FIFO_IN"
sleep 0.5
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin alive after invalid JSON (good)"
else
  fail "Plugin crashed on invalid JSON"
fi

# 6) oversized payload — ensure plugin survives and ideally responds with an error
echo "=== Test: oversized payload ==="
LARGE_LEN=$((200 * 1024))
# create a safe large JSON with repeated A's in message
LARGE_STR=$(head -c "$LARGE_LEN" < /dev/zero | tr '\0' 'A' | tr -d '\n')
# note: some shells may choke on extremely long variables; use printf to write the JSON directly
printf '{"id":"go-large-1","type":"exec","payload":{"action":"echo","args":{"message":"%s"}}}\n' "$LARGE_STR" >> "$FIFO_IN" || true
sleep 0.6
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin survived oversized payload (good)"
else
  fail "Plugin crashed on oversized payload"
fi

# 7) unsupported action -> expect status:error
assert_response "exec-unsupported" "go-unk-1" '{"id":"go-unk-1","type":"exec","payload":{"action":"does_not_exist"}}' '.status == "error" or (.status == "busy")'

# 8) graceful shutdown
echo "=== Test: shutdown ==="
send_msg '{"id":"go-shutdown-1","type":"shutdown","payload":null}'
# wait for ack (optional)
if resp="$(wait_for_response "go-shutdown-1" 3)"; then
  echo "Shutdown response: $resp"
else
  echo "No explicit shutdown response found in logs; continuing to check process exit"
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

echo "=== All tests passed for Go plugin ==="
exit 0
