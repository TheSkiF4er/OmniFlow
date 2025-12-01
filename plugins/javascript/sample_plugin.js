#!/usr/bin/env node
"use strict";
/*
 * sample_plugin.js
 *
 * Production-ready JavaScript (Node.js) plugin for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Overview:
 *  - Communicates with host via newline-delimited JSON messages on stdin/stdout.
 *  - Robust parsing/validation using built-in JSON and explicit type checks.
 *  - Structured logging to stderr (optionally JSON formatted via env).
 *  - Graceful shutdown on SIGINT/SIGTERM or a "shutdown" message.
 *  - Background worker for periodical maintenance (heartbeat, metrics flush).
 *  - Configurable via environment variables.
 *  - Enforces size limits for incoming messages to mitigate DoS.
 *
 * Runtime contract (example):
 *  Host -> Plugin messages (newline terminated JSON):
 *    { "id": "<uuid>", "type": "exec|health|shutdown", "payload": {...} }
 *
 *  Plugin -> Host responses (newline terminated JSON):
 *    { "id": "<uuid>", "status": "ok|error", "code": <int?>, "message": "...", "body": {...} }
 *
 * Environment variables (recommended):
 *  OMNIFLOW_PLUGIN_MAX_LINE=131072     # max bytes per incoming message (default 131072)
 *  OMNIFLOW_PLUGIN_HEARTBEAT=5         # heartbeat interval (seconds)
 *  OMNIFLOW_LOG_JSON=true              # if set, emit JSON logs to stderr
 *  OMNIFLOW_EXEC_TIMEOUT=10            # seconds timeout for exec handlers
 *
 * Build & Run:
 *  - Requires Node.js 18+ (for global AbortController and stable features).
 *  - Make executable: chmod +x sample_plugin.js
 *  - Run: echo '{"id":"1","type":"health"}' | ./sample_plugin.js
 *
 * Security notes:
 *  - No dynamic code execution. If external commands are required, run them in
 *    a hardened, sandboxed process outside this plugin.
 *  - Validate types Strictly. Limit input sizes and enforce timeouts.
 *  - If plugin needs to access network or secrets, ensure host enforces least privilege.
 */

const readline = require('readline');
const { stdin, stdout, stderr } = process;
const { setTimeout: delay } = require('timers/promises');

// Configuration from env
const MAX_LINE = (() => {
  const v = process.env.OMNIFLOW_PLUGIN_MAX_LINE;
  if (!v) return 128 * 1024; // 128 KiB
  const n = Number(v);
  return Number.isInteger(n) && n > 0 && n <= 10 * 1024 * 1024 ? n : 128 * 1024;
})();
const HEARTBEAT = (() => {
  const v = process.env.OMNIFLOW_PLUGIN_HEARTBEAT;
  if (!v) return 5;
  const n = Number(v);
  return Number.isInteger(n) && n > 0 && n <= 3600 ? n : 5;
})();
const LOG_JSON = !!process.env.OMNIFLOW_LOG_JSON;
const EXEC_TIMEOUT = (() => {
  const v = process.env.OMNIFLOW_EXEC_TIMEOUT;
  if (!v) return 10; // seconds
  const n = Number(v);
  return Number.isInteger(n) && n > 0 && n <= 3600 ? n : 10;
})();

const PLUGIN_NAME = 'OmniFlowNodeRelease';
const PLUGIN_VERSION = '1.0.0';

let running = true;

// Logging helpers
function log(level, message, extra) {
  const ts = new Date().toISOString();
  if (LOG_JSON) {
    const obj = { time: ts, level, plugin: PLUGIN_NAME, message };
    if (extra) obj.extra = extra;
    stderr.write(JSON.stringify(obj) + '\n');
  } else {
    stderr.write(`${ts} [${level}] ${PLUGIN_NAME}: ${message}\n`);
  }
}
function info(msg, extra) { log('INFO', msg, extra); }
function warn(msg, extra) { log('WARN', msg, extra); }
function errorLog(msg, extra) { log('ERROR', msg, extra); }

// Respond helpers: write to stdout newline-delimited JSON
function respond(obj) {
  try {
    stdout.write(JSON.stringify(obj) + '\n');
  } catch (err) {
    // If serialization fails, write a minimal error
    stdout.write(JSON.stringify({ status: 'error', message: 'serialization failed' }) + '\n');
  }
}

function respondOk(id, body = {}) {
  const r = { id, status: 'ok', body };
  respond(r);
}

function respondError(id, code, message) {
  const r = { id, status: 'error', code, message };
  respond(r);
}

// Graceful shutdown
async function shutdown() {
  if (!running) return;
  running = false;
  info('shutdown requested');
  // give small time for background tasks to finish
  await delay(100);
  info('exiting');
  // allow stdout to flush
  await delay(10);
  process.exit(0);
}

// Background heartbeat worker
let heartbeatHandle = null;
function startHeartbeat() {
  info(`starting heartbeat (${HEARTBEAT}s)`, { version: PLUGIN_VERSION });
  heartbeatHandle = setInterval(() => {
    info('heartbeat');
  }, HEARTBEAT * 1000);
}
function stopHeartbeat() {
  if (heartbeatHandle) clearInterval(heartbeatHandle);
}

// Safe JSON parsing wrapper
function safeParseJson(line) {
  try {
    return JSON.parse(line);
  } catch (err) {
    return null;
  }
}

// Input buffer length enforcement
function enforceMaxLine(line) {
  // Node strings are UTF-16, but we enforce by bytes approximation using Buffer.byteLength
  const bytes = Buffer.byteLength(line, 'utf8');
  return bytes <= MAX_LINE;
}

// Exec handler - run tasks with timeout and safe handling
async function execHandler(id, payload) {
  // Validate payload is object
  if (!payload || typeof payload !== 'object') {
    respondError(id, 400, "invalid or missing payload");
    return;
  }
  const { action } = payload;
  if (typeof action !== 'string') {
    respondError(id, 400, "missing or invalid 'action'");
    return;
  }

  // Support a few safe built-in actions: echo, reverse, compute
  if (action === 'echo') {
    const message = typeof payload.message === 'string' ? payload.message : '';
    // Return promptly
    respondOk(id, { action: 'echo', message });
    return;
  }

  if (action === 'reverse') {
    const message = typeof payload.message === 'string' ? payload.message : '';
    // Reverse Unicode-safe
    const rev = Array.from(message).reverse().join('');
    respondOk(id, { action: 'reverse', message: rev });
    return;
  }

  if (action === 'compute') {
    // compute sum of numbers array
    const arr = payload.numbers;
    if (!Array.isArray(arr)) { respondError(id, 400, "missing or invalid 'numbers' array"); return; }
    // validate elements
    let sum = 0;
    for (const v of arr) {
      if (typeof v !== 'number') { respondError(id, 400, 'numbers must be numeric'); return; }
      sum += v;
    }
    respondOk(id, { action: 'compute', sum });
    return;
  }

  // Unknown action
  respondError(id, 422, 'unsupported action');
}

// Main message processing
async function processLine(line) {
  if (!enforceMaxLine(line)) {
    warn('incoming message exceeds MAX_LINE, rejecting');
    respondError('', 413, 'payload too large');
    return;
  }
  const msg = safeParseJson(line);
  if (!msg || typeof msg !== 'object') {
    warn('invalid JSON message');
    respondError('', 400, 'invalid JSON');
    return;
  }

  const id = typeof msg.id === 'string' ? msg.id : '';
  const type = typeof msg.type === 'string' ? msg.type.toLowerCase() : null;
  const payload = msg.payload;

  if (!type) { respondError(id, 400, "missing 'type'"); return; }

  if (type === 'health') {
    respondOk(id, { status: 'healthy', version: PLUGIN_VERSION });
    return;
  }

  if (type === 'exec') {
    // Run exec with timeout via Promise.race
    let timedOut = false;
    const timeoutMs = EXEC_TIMEOUT * 1000;
    const execPromise = (async () => {
      try {
        await execHandler(id, payload);
      } catch (err) {
        errorLog('exec handler failed: ' + String(err));
        respondError(id, 500, 'internal error in exec handler');
      }
    })();
    const timer = delay(timeoutMs).then(() => { timedOut = true; });
    await Promise.race([execPromise, timer]);
    if (timedOut) respondError(id, 408, 'exec timeout');
    return;
  }

  if (type === 'shutdown' || type === 'quit') {
    respondOk(id, { result: 'shutting_down' });
    // start shutdown sequence
    await shutdown();
    return;
  }

  respondError(id, 400, 'unknown type');
}

// Setup readline interface
const rl = readline.createInterface({ input: stdin, crlfDelay: Infinity });
rl.on('line', async (line) => {
  if (!running) return;
  // Process but do not block event loop for long
  processLine(line).catch(err => {
    errorLog('unhandled error processing line: ' + String(err));
  });
});
rl.on('close', async () => {
  info('stdin closed (EOF)');
  // allow graceful shutdown
  await shutdown();
});

// Signal handling
process.on('SIGINT', async () => {
  warn('SIGINT received');
  await shutdown();
});
process.on('SIGTERM', async () => {
  warn('SIGTERM received');
  await shutdown();
});

// Start
(async () => {
  info(`plugin starting version=${PLUGIN_VERSION} maxLine=${MAX_LINE} heartbeat=${HEARTBEAT} execTimeout=${EXEC_TIMEOUT}`);
  startHeartbeat();
})();
