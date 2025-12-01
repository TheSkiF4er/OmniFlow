#!/usr/bin/env php
<?php
/**
 * sample_plugin.php
 *
 * Production-ready PHP plugin for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Overview:
 *  - CLI script that communicates with the host via newline-delimited JSON over stdin/stdout.
 *  - Robust input size limiting, structured logging to stderr (optional JSON), graceful shutdown,
 *    heartbeat background behavior, configurable env vars, and simple exec-timeout support where available.
 *  - Designed to be portable across typical PHP CLI environments. Uses only core PHP functions where possible.
 *
 * Runtime contract (newline-delimited JSON):
 *  Host -> Plugin:
 *    { "id": "<uuid>", "type": "exec|health|shutdown", "payload": {...} }
 *
 *  Plugin -> Host responses:
 *    { "id":"<uuid>", "status":"ok|error", "code"?:int, "message"?:string, "body"?:object }
 *
 * Environment variables (recommended):
 *  - OMNIFLOW_PLUGIN_MAX_LINE (default 131072)   # max bytes per incoming message
 *  - OMNIFLOW_PLUGIN_HEARTBEAT (default 5)       # heartbeat seconds
 *  - OMNIFLOW_LOG_JSON (default empty)           # if set, emit JSON logs to stderr
 *  - OMNIFLOW_EXEC_TIMEOUT (default 10)          # seconds timeout for exec handlers (best-effort)
 *
 * Build/Run:
 *  - Requires PHP 7.4+ (recommended 8.x) with CLI SAPI.
 *  - Run: echo '{"id":"1","type":"health"}' | ./sample_plugin.php
 *
 * Notes on timeouts:
 *  - If the pcntl extension is available, this script will attempt to use pcntl_alarm for exec timeouts
 *    and pcntl_async_signals for responsive shutdown. If pcntl is not available, exec timeout is best-effort
 *    and relies on the handler doing non-blocking work.
 */

// ---------------- Configuration ----------------
$MAX_LINE = getenv('OMNIFLOW_PLUGIN_MAX_LINE') !== false ? (int)getenv('OMNIFLOW_PLUGIN_MAX_LINE') : 131072;
$HEARTBEAT = getenv('OMNIFLOW_PLUGIN_HEARTBEAT') !== false ? (int)getenv('OMNIFLOW_PLUGIN_HEARTBEAT') : 5;
$LOG_JSON = getenv('OMNIFLOW_LOG_JSON') !== false;
$EXEC_TIMEOUT = getenv('OMNIFLOW_EXEC_TIMEOUT') !== false ? (int)getenv('OMNIFLOW_EXEC_TIMEOUT') : 10;

define('PLUGIN_NAME', 'OmniFlowPHPRelease');
define('PLUGIN_VERSION', '1.0.0');

// runtime control
$running = true; // when false, main loop exits
$shutdown_requested = false;

// track last heartbeat time (monotonic)
$lastHeartbeat = hrtime(true);

// Try to enable async signals (PHP 7.1+ with pcntl)
$pcntlAvailable = function_exists('pcntl_async_signals') && function_exists('pcntl_signal');
if ($pcntlAvailable) {
    pcntl_async_signals(true);
    pcntl_signal(SIGINT, function($sig) use (&$running, &$shutdown_requested) {
        stderr_log('WARN', "received SIGINT, initiating shutdown");
        $shutdown_requested = true;
        $running = false;
    });
    pcntl_signal(SIGTERM, function($sig) use (&$running, &$shutdown_requested) {
        stderr_log('WARN', "received SIGTERM, initiating shutdown");
        $shutdown_requested = true;
        $running = false;
    });
}

// If pcntl available, also use alarm for timeouts (best-effort)
$pcntlAlarmAvailable = function_exists('pcntl_alarm') && $pcntlAvailable;

// ---------------- Logging ----------------
function current_time_iso8601() {
    return gmdate('Y-m-d\TH:i:s\Z');
}

function stderr_log($level, $message, $extra = null) {
    global $LOG_JSON;
    $time = current_time_iso8601();
    if ($LOG_JSON) {
        $obj = ['time' => $time, 'level' => $level, 'plugin' => PLUGIN_NAME, 'message' => $message];
        if ($extra !== null) $obj['extra'] = $extra;
        fwrite(STDERR, json_encode($obj, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "\n");
    } else {
        fwrite(STDERR, "$time [$level] " . PLUGIN_NAME . ": $message\n");
    }
}

function info_log($msg, $extra = null) { stderr_log('INFO', $msg, $extra); }
function warn_log($msg, $extra = null) { stderr_log('WARN', $msg, $extra); }
function error_log_msg($msg, $extra = null) { stderr_log('ERROR', $msg, $extra); }

// ---------------- Respond helpers ----------------
function respond($data) {
    // Ensure newline terminated, single-line JSON
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "\n";
    flush();
}

function respond_ok($id, $body = null) {
    $r = ['status' => 'ok'];
    if ($id !== null) $r['id'] = $id;
    if ($body !== null) $r['body'] = $body;
    respond($r);
}

function respond_error($id, $code, $message) {
    $r = ['status' => 'error', 'code' => $code, 'message' => $message];
    if ($id !== null) $r['id'] = $id;
    respond($r);
}

// ---------------- Utility: safe read line with limit ----------------
function safe_read_line($maxLine) {
    // Use stream_get_line to limit bytes read (includes delimiter)
    $line = @stream_get_line(STDIN, $maxLine + 1, "\n");
    if ($line === false) {
        // EOF or error
        if (feof(STDIN)) return null;
        return false;
    }
    // stream_get_line removes the delimiter; we have the line content
    $bytes = strlen($line);
    if ($bytes > $maxLine) {
        return ['error' => 'too_large'];
    }
    return $line;
}

// ---------------- Exec timeout helpers (best-effort) ----------------
$alarmTriggered = false;
function alarm_handler($sig) {
    global $alarmTriggered;
    $alarmTriggered = true;
}
if ($pcntlAlarmAvailable) {
    pcntl_signal(SIGALRM, 'alarm_handler');
}

// ---------------- Action handlers ----------------
function handle_health($id) {
    $body = ['status' => 'healthy', 'version' => PLUGIN_VERSION];
    respond_ok($id, $body);
}

function handle_exec($id, $payload) {
    global $EXEC_TIMEOUT, $pcntlAlarmAvailable, $alarmTriggered;
    // payload expected as array/object
    if (!is_array($payload) && !is_object($payload)) {
        respond_error($id, 400, "missing or invalid payload");
        return;
    }
    // ensure it's associative
    $action = null;
    if (is_array($payload) && array_key_exists('action', $payload)) $action = $payload['action'];
    elseif (is_object($payload) && property_exists($payload, 'action')) $action = $payload->action;

    if (!is_string($action)) { respond_error($id, 400, "missing or invalid 'action'"); return; }

    // Best-effort timeout using pcntl_alarm
    $alarmTriggered = false;
    if ($pcntlAlarmAvailable) {
        pcntl_alarm($EXEC_TIMEOUT);
    }

    // Implement safe, quick built-in actions: echo, reverse, compute
    try {
        if ($action === 'echo') {
            $message = '';
            if (is_array($payload) && isset($payload['message'])) $message = (string)$payload['message'];
            if (is_object($payload) && property_exists($payload, 'message')) $message = (string)$payload->message;
            $body = ['action' => 'echo', 'message' => $message];
            respond_ok($id, $body);
        } elseif ($action === 'reverse') {
            $message = '';
            if (is_array($payload) && isset($payload['message'])) $message = (string)$payload['message'];
            if (is_object($payload) && property_exists($payload, 'message')) $message = (string)$payload->message;
            // reverse multibyte-safe
            $rev = implode('', array_reverse(preg_split('//u', $message, -1, PREG_SPLIT_NO_EMPTY)));
            $body = ['action' => 'reverse', 'message' => $rev];
            respond_ok($id, $body);
        } elseif ($action === 'compute') {
            // sum numbers
            $numbers = null;
            if (is_array($payload) && isset($payload['numbers'])) $numbers = $payload['numbers'];
            if (is_object($payload) && property_exists($payload, 'numbers')) $numbers = $payload->numbers;
            if (!is_array($numbers)) { respond_error($id, 400, "missing or invalid 'numbers' array"); return; }
            $sum = 0.0;
            foreach ($numbers as $n) {
                if (!is_numeric($n)) { respond_error($id, 400, "numbers must be numeric"); return; }
                $sum += (float)$n;
            }
            $body = ['action' => 'compute', 'sum' => $sum];
            respond_ok($id, $body);
        } else {
            respond_error($id, 422, 'unsupported action');
        }
    } catch (Throwable $e) {
        // If alarm triggered, send timeout
        if ($alarmTriggered) {
            respond_error($id, 408, 'exec timeout');
        } else {
            respond_error($id, 500, 'internal error');
            error_log_msg('exec handler exception: ' . $e->getMessage());
        }
    } finally {
        if ($pcntlAlarmAvailable) pcntl_alarm(0); // cancel alarm
    }
}

// ---------------- Main loop ----------------
info_log("starting plugin version=" . PLUGIN_VERSION . " max_line={$MAX_LINE} heartbeat={$HEARTBEAT}");

while ($running) {
    // heartbeat management (non-blocking): log heartbeat every HEARTBEAT seconds
    $now = hrtime(true);
    $elapsedSec = ($now - $lastHeartbeat) / 1e9;
    if ($elapsedSec >= $HEARTBEAT) {
        $lastHeartbeat = $now;
        info_log("heartbeat");
    }

    // Read line with limit; stream_get_line will block until delimiter or EOF
    $res = safe_read_line($MAX_LINE);
    if ($res === null) {
        // EOF
        info_log("stdin closed (EOF), exiting");
        break;
    }
    if ($res === false) {
        warn_log("error reading stdin");
        // small sleep to avoid tight loop on I/O error
        usleep(100000);
        continue;
    }
    if (is_array($res) && isset($res['error']) && $res['error'] === 'too_large') {
        warn_log("incoming message too large, rejecting");
        respond_error(null, 413, 'payload too large');
        // drain remainder of line (already handled by stream_get_line limit)
        continue;
    }

    $line = $res;
    $trim = trim($line);
    if ($trim === '') continue;

    // Decode JSON
    $msg = json_decode($line, true);
    if ($msg === null || !is_array($msg)) {
        warn_log('invalid JSON message');
        respond_error(null, 400, 'invalid JSON');
        continue;
    }

    $id = array_key_exists('id', $msg) ? $msg['id'] : null;
    $type = array_key_exists('type', $msg) ? strtolower((string)$msg['type']) : null;
    $payload = array_key_exists('payload', $msg) ? $msg['payload'] : null;

    if ($type === null) { respond_error($id, 400, "missing 'type'"); continue; }

    if ($type === 'health') {
        handle_health($id);
        continue;
    }
    if ($type === 'exec') {
        handle_exec($id, $payload);
        continue;
    }
    if ($type === 'shutdown' || $type === 'quit') {
        respond_ok($id, ['result' => 'shutting_down']);
        $shutdown_requested = true;
        $running = false;
        break;
    }

    respond_error($id, 400, 'unknown type');
}

// Graceful shutdown cleanup
info_log('plugin shutdown complete');
exit(0);

?>
