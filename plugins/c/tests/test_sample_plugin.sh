#!/usr/bin/env bash
#
# test_sample_plugin.sh
#
# Integration tests for OmniFlow C plugin (plugins/c/sample_plugin.c)
# - Builds plugin (prefer Makefile if present)
# - Runs plugin using FIFO for stdin, captures stdout/stderr
# - Sends JSON newline-delimited messages and validates responses with jq
#
# Requirements:
# - bash, mkfifo, jq, timeout (coreutils)
# - make & gcc (or clang) for build
#
# Usage:
#   cd <repo-root>
#   ./plugins/c/tests/test_sample_plugin.sh
#
set -euo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$REPO_ROOT/plugins/c"
TEST_DIR="$(mktemp -d)"
FIFO_IN="$TEST_DIR/plugin.stdin.fifo"
STDOUT_LOG="$TEST_DIR/plugin.stdout.log"
STDERR_LOG="$TEST_DIR/plugin.stderr.log"
BIN_PATH="$PLUGIN_DIR/sample_plugin"   # target binary path
MAKE_CMD="make -C \"$PLUGIN_DIR\""

# timeouts (seconds)
BUILD_TIMEOUT=120
TEST_TIMEOUT=10
RESP_POLL_INTERVAL=0.1
RESP_WAIT_TIMEOUT=5

# Ensure cleanup
cleanup() {
  local rc=$?
  set +e
  echo "=== Cleaning up ==="
  if [[ -n "${PLUGIN_PID:-}" ]]; then
    echo "Killing plugin pid $PLUGIN_PID"
    kill "$PLUGIN_PID" 2>/dev/null || true
    wait "$PLUGIN_PID" 2>/dev/null || true
  fi
  rm -rf "$TEST_DIR"
  exit $rc
}
trap cleanup EXIT INT TERM

echo "Test workspace: $TEST_DIR"

# Helper: fail with message
fail() {
  echo "FAIL: $*" >&2
  # print logs for debug
  echo "=== plugin stdout ==="
  sed -n '1,200p' "$STDOUT_LOG" || true
  echo "=== plugin stderr ==="
  sed -n '1,200p' "$STDERR_LOG" || true
  exit 1
}

# Check prerequisites
command -v jq >/dev/null 2>&1 || { echo "jq required but not found. Install jq."; exit 2; }
command -v timeout >/dev/null 2>&1 || { echo "timeout required but not found. Install coreutils."; exit 2; }

# 1) Build plugin
echo "Building plugin..."
if [[ -f "$PLUGIN_DIR/Makefile" ]]; then
  echo "Using Makefile..."
  timeout "${BUILD_TIMEOUT}" bash -lc "$MAKE_CMD" || fail "Make failed"
  # try to find built binary if not at expected path
  if [[ ! -x "$BIN_PATH" ]]; then
    found=$(find "$PLUGIN_DIR" -maxdepth 2 -type f -perm /111 -name 'sample_plugin*' -print -quit || true)
    if [[ -n "$found" ]]; then
      BIN_PATH="$found"
    fi
  fi
else
  echo "No Makefile found — attempting direct gcc build..."
  GCC_OUT="$PLUGIN_DIR/sample_plugin"
  timeout "${BUILD_TIMEOUT}" bash -lc "gcc -std=c11 -O2 -Wall -Wextra -pthread -I$PLUGIN_DIR/vendor/cjson -o '$GCC_OUT' '$PLUGIN_DIR/sample_plugin.c' '$PLUGIN_DIR/vendor/cjson/cJSON.c' 2>&1" || fail "gcc build failed"
  BIN_PATH="$GCC_OUT"
fi

[[ -x "$BIN_PATH" ]] || fail "Built binary not found or not executable at $BIN_PATH"
echo "Built binary: $BIN_PATH"

# 2) Prepare FIFO and logs
mkfifo "$FIFO_IN"
: > "$STDOUT_LOG"
: > "$STDERR_LOG"

# 3) Start plugin (stdin from FIFO, stdout/stderr to logs)
# Use unbuffered output to keep real-time logs. The plugin should flush stdout/stderr itself.
bash -c "stdbuf -oL -eL '$BIN_PATH' < '$FIFO_IN' >> '$STDOUT_LOG' 2>> '$STDERR_LOG'" &
PLUGIN_PID=$!
sleep 0.15  # give it a moment to start

# Utility: send JSON message and return id
send_msg() {
  local id="$1"; shift
  local json="$1"; shift
  # write newline-terminated JSON into FIFO
  printf '%s\n' "$json" >> "$FIFO_IN"
}

# Utility: wait for response with given id and optional jq filter; returns JSON line or empty
wait_for_response() {
  local id="$1"; shift
  local jq_filter="${1:-.}"  # full object by default
  local out=""
  local waited=0
  while (( $(echo "$waited < $RESP_WAIT_TIMEOUT" | bc -l) )); do
    # read last lines from stdout log and try to find a JSON object with matching id
    # use grep to approximate JSON lines, then jq to filter
    if [[ -s "$STDOUT_LOG" ]]; then
      out=$(tac "$STDOUT_LOG" | grep -m1 -o '{.*}' || true)
      if [[ -n "$out" ]]; then
        # try to parse and check id
        echo "$out" | jq -e --arg ID "$id" "select(.id == \$ID)" >/dev/null 2>&1 && { echo "$out"; return 0; }
        # If not matching, search all lines
        match=$(jq -c --arg ID "$id" 'select(.id == $ID)' <(awk '/^\s*{/{p=1} p{print; if(/}[^}]*$/){p=0}}' "$STDOUT_LOG") 2>/dev/null | head -n1 || true)
        if [[ -n "$match" ]]; then
          echo "$match"
          return 0
        fi
      fi
    fi
    sleep "$RESP_POLL_INTERVAL"
    waited=$(awk "BEGIN {print $waited + $RESP_POLL_INTERVAL; exit}")
  done
  return 1
}

# For more robust matching, we'll append unique ids and verify via jq scanning of stdout.
# Helper: find response by id scanning entire stdout log (with jq), return first match
find_response_by_id() {
  local id="$1"
  # filter lines that are valid JSON objects and then use jq
  # Using awk to print candidate JSON objects line by line
  awk '{
      # naive: print lines that look like JSON objects (start with { and end with })
      if ($0 ~ /^\s*{.*}\s*$/) print $0
  }' "$STDOUT_LOG" | jq -c --arg ID "$id" 'select(.id == $ID)' 2>/dev/null | head -n1 || true
}

# Short wrapper to send and assert expected response content
test_message_expect() {
  local test_name="$1"; shift
  local id="$1"; shift
  local payload_json="$1"; shift
  local expect_jq="$1"; shift

  echo "== Test: $test_name (id=$id) =="
  send_msg "$id" "$payload_json"
  # Wait for response by repeatedly scanning stdout for JSON matching id
  local tries=0
  local max_tries=$((RESP_WAIT_TIMEOUT / RESP_POLL_INTERVAL))
  local resp=""
  while (( tries < max_tries )); do
    resp="$(find_response_by_id "$id")"
    if [[ -n "$resp" ]]; then
      break
    fi
    sleep "$RESP_POLL_INTERVAL"
    tries=$((tries+1))
  done

  if [[ -z "$resp" ]]; then
    echo "No response for id=$id within ${RESP_WAIT_TIMEOUT}s"
    echo "=== STDOUT ==="; sed -n '1,200p' "$STDOUT_LOG"
    echo "=== STDERR ==="; sed -n '1,200p' "$STDERR_LOG"
    fail "$test_name: no response"
  fi

  echo "Response: $resp"
  # validate with jq expression provided by caller
  if ! echo "$resp" | jq -e "$expect_jq" >/dev/null 2>&1; then
    echo "Response did not satisfy expected jq predicate: $expect_jq"
    echo "Full response: $resp"
    fail "$test_name: assertion failed"
  fi
  echo "OK: $test_name"
}

# Wait a bit for plugin warm-up (background worker may log)
sleep 0.2

# === Tests ===

# 1) health
test_message_expect "health" "t-health-1" '{"id":"t-health-1","type":"health"}' '.status == "ok" and .body.status == "healthy"'

# 2) exec echo
test_message_expect "exec-echo" "t-echo-1" '{"id":"t-echo-1","type":"exec","payload":{"action":"echo","message":"hello world"}}' '.status == "ok" and .body.action == "echo" and .body.message == "hello world"'

# 3) exec reverse (unicode test)
test_message_expect "exec-reverse" "t-rev-1" '{"id":"t-rev-1","type":"exec","payload":{"action":"reverse","message":"Привет"}}' '.status == "ok" and .body.action == "reverse" and .body.message == "тевирП"'

# 4) exec compute (sum)
test_message_expect "exec-compute" "t-calc-1" '{"id":"t-calc-1","type":"exec","payload":{"action":"compute","numbers":[1,2,3,4.5]}}' '.status == "ok" and .body.action == "compute" and (.body.sum | (. == 10.5))'

# 5) invalid JSON -> plugin should reply error (or at least not crash)
echo "== Test: invalid-json =="
printf '%s\n' 'this is not json' >> "$FIFO_IN"
# Wait a moment to let plugin respond
sleep 0.5
# plugin may respond with status:error; we won't strict-check but ensure plugin still alive
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin still running after invalid JSON (good)"
else
  fail "Plugin crashed after invalid JSON"
fi

# 6) oversized payload -> expect plugin to respond with error or a sensible rejection
echo "== Test: oversized payload =="
# craft a large message > MAX_LINE (use 200k bytes)
LARGE_PAYLOAD_LEN=$((200 * 1024))
LARGE_STR=$(head -c "$LARGE_PAYLOAD_LEN" < /dev/zero | tr '\0' 'A' | tr -d '\n')
LARGE_JSON=$(printf '{"id":"t-large-1","type":"exec","payload":{"action":"echo","message":"%s"}}' "$LARGE_STR")
# send via FIFO - may block if too large, so use background writer with timeout
( printf '%s\n' "$LARGE_JSON" >> "$FIFO_IN" ) & pidwriter=$!
sleep 0.5
# If writer still running after 2s, kill to prevent blocking forever
if kill -0 "$pidwriter" 2>/dev/null; then
  kill "$pidwriter" 2>/dev/null || true
fi
# plugin should not crash; if it responds with error, it's acceptable
sleep 0.5
if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin survived oversized payload (good)"
else
  fail "Plugin crashed on oversized payload"
fi

# 7) exec unsupported action -> expect error
test_message_expect "exec-unsupported" "t-unk-1" '{"id":"t-unk-1","type":"exec","payload":{"action":"does_not_exist"}}' '.status == "error"'

# 8) graceful shutdown
echo "== Test: shutdown =="
send_msg "t-shutdown-1" '{"id":"t-shutdown-1","type":"shutdown"}'
# wait for shutdown ack
sleep 0.5
resp="$(find_response_by_id 't-shutdown-1' || true)"
if [[ -z "$resp" ]]; then
  echo "No explicit shutdown response found; check logs"
  sed -n '1,200p' "$STDOUT_LOG"
fi

# Wait for process to exit (allow up to 3s)
wait_secs=0
while kill -0 "$PLUGIN_PID" 2>/dev/null && [[ $wait_secs -lt 3 ]]; do
  sleep 0.2
  wait_secs=$((wait_secs+1))
done

if kill -0 "$PLUGIN_PID" 2>/dev/null; then
  echo "Plugin did not exit after shutdown request; killing"
  kill -9 "$PLUGIN_PID" 2>/dev/null || true
  fail "Plugin failed to exit on shutdown"
else
  echo "Plugin exited gracefully after shutdown (OK)"
fi

echo "All tests passed."

# success — cleanup will run via trap
exit 0
