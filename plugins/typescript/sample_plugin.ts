/*
 * sample_plugin.ts
 *
 * Production-ready TypeScript plugin for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Overview
 * --------
 * - Communicates with host via newline-delimited JSON messages on stdin/stdout.
 * - Strong typing with TypeScript interfaces.
 * - Robust validation and size limiting to mitigate DoS.
 * - Structured logging (optional JSON) to stderr.
 * - Graceful shutdown on SIGINT/SIGTERM or receiving a "shutdown" message.
 * - Background heartbeat worker and safe exec timeouts using AbortController.
 * - Configurable via environment variables.
 * - Supports built-in safe actions: `echo`, `reverse`, `compute`.
 *
 * Build & Run
 * -----------
 * 1) Initialize project (recommended):
 *    npm init -y
 *    npm install --save-dev typescript @types/node
 *    npx tsc --init
 *    (ensure tsconfig.target >= "ES2020" and module "CommonJS")
 *
 * 2) Compile:
 *    npx tsc plugins/typescript/sample_plugin.ts --outDir dist --esModuleInterop
 *
 * 3) Run (example):
 *    echo '{"id":"1","type":"health"}' | node dist/plugins/typescript/sample_plugin.js
 *
 * Recommended package.json scripts:
 * {
 *   "scripts": {
 *     "build": "tsc",
 *     "start": "node dist/plugins/typescript/sample_plugin.js",
 *     "lint": "eslint . --ext .ts",
 *     "test": "jest"
 *   }
 * }
 *
 * Docker
 * ------
 * Use a small Node base image (node:20-alpine). Build the TypeScript to JS in CI or multi-stage Docker build.
 *
 * Security notes
 * --------------
 * - Enforce MAX_LINE to prevent memory exhaustion.
 * - No dynamic code execution (no eval/new Function).
 * - If external processes are required, run them in sandboxed containers and with strict allowlists.
 * - Validate all payload fields before use.
 */

import * as readline from 'readline';
import { stdin, stdout, stderr, env } from 'process';

// -------------------- Configuration --------------------
const PLUGIN_NAME = 'OmniFlowTSRelease';
const PLUGIN_VERSION = '1.0.0';

const MAX_LINE = (() => {
  const v = env.OMNIFLOW_PLUGIN_MAX_LINE;
  const n = v ? Number(v) : 128 * 1024; // 128 KiB
  return Number.isInteger(n) && n > 0 && n <= 10 * 1024 * 1024 ? n : 128 * 1024;
})();

const HEARTBEAT = (() => {
  const v = env.OMNIFLOW_PLUGIN_HEARTBEAT;
  const n = v ? Number(v) : 5;
  return Number.isInteger(n) && n > 0 && n <= 3600 ? n : 5;
})();

const LOG_JSON = !!env.OMNIFLOW_LOG_JSON;
const EXEC_TIMEOUT = (() => {
  const v = env.OMNIFLOW_EXEC_TIMEOUT;
  const n = v ? Number(v) : 10;
  return Number.isInteger(n) && n > 0 && n <= 3600 ? n : 10;
})();

// -------------------- Types --------------------
interface IncomingMessage {
  id?: string;
  type: string;
  payload?: unknown;
}

interface Response {
  id?: string;
  status: 'ok' | 'error';
  code?: number;
  message?: string;
  body?: unknown;
}

// -------------------- Logging --------------------
function nowIso(): string {
  return new Date().toISOString();
}

function log(level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG', message: string, extra?: unknown) {
  if (LOG_JSON) {
    const obj = { time: nowIso(), level, plugin: PLUGIN_NAME, message, extra };
    stderr.write(JSON.stringify(obj) + '\n');
  } else {
    stderr.write(`${nowIso()} [${level}] ${PLUGIN_NAME}: ${message}\n`);
  }
  stderr.flush?.();
}

const info = (m: string, e?: unknown) => log('INFO', m, e);
const warn = (m: string, e?: unknown) => log('WARN', m, e);
const errorLog = (m: string, e?: unknown) => log('ERROR', m, e);

// -------------------- Respond helpers --------------------
function respond(obj: Response) {
  try {
    stdout.write(JSON.stringify(obj) + '\n');
  } catch (e) {
    // fallback minimal error
    stdout.write(JSON.stringify({ status: 'error', message: 'serialization failed' }) + '\n');
  }
}

function respondOk(id?: string, body?: unknown) {
  respond({ id, status: 'ok', body });
}

function respondError(id: string | undefined, code: number, message: string) {
  respond({ id, status: 'error', code, message });
}

// -------------------- Heartbeat / background --------------------
let running = true;
let heartbeatTimer: NodeJS.Timeout | null = null;

function startHeartbeat() {
  info(`starting heartbeat (${HEARTBEAT}s)`);
  heartbeatTimer = setInterval(() => {
    info('heartbeat');
  }, HEARTBEAT * 1000);
}

function stopHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

// -------------------- Action handlers --------------------
async function actionEcho(payload: unknown): Promise<unknown> {
  if (payload && typeof payload === 'object' && 'message' in payload) {
    // @ts-expect-error index
    const m = (payload as any).message;
    return { action: 'echo', message: typeof m === 'string' ? m : String(m) };
  }
  return { action: 'echo', message: '' };
}

async function actionReverse(payload: unknown): Promise<unknown> {
  if (payload && typeof payload === 'object' && 'message' in payload) {
    // @ts-expect-error index
    const m = (payload as any).message;
    const s = typeof m === 'string' ? m : String(m);
    // Unicode-safe reverse
    const rev = Array.from(s).reverse().join('');
    return { action: 'reverse', message: rev };
  }
  return { action: 'reverse', message: '' };
}

async function actionCompute(payload: unknown): Promise<unknown> {
  if (!payload || typeof payload !== 'object' || !('numbers' in payload)) {
    throw new Error("missing or invalid 'numbers' array");
  }
  // @ts-expect-error
  const arr = (payload as any).numbers;
  if (!Array.isArray(arr)) throw new Error("missing or invalid 'numbers' array");
  let sum = 0;
  for (const el of arr) {
    if (typeof el !== 'number') throw new Error('numbers must be numeric');
    sum += el;
  }
  return { action: 'compute', sum };
}

async function handleExec(id: string | undefined, payload: unknown) {
  // Use AbortController for timeout
  const ac = new AbortController();
  const signal = ac.signal;
  const timeout = setTimeout(() => ac.abort(), EXEC_TIMEOUT * 1000);

  try {
    if (signal.aborted) throw new Error('exec timeout');

    // dispatch action
    // @ts-expect-error payload type
    const action = payload && typeof payload === 'object' && 'action' in payload ? (payload as any).action : undefined;
    if (!action || typeof action !== 'string') {
      respondError(id, 400, "missing or invalid 'action'");
      return;
    }

    let result: unknown;
    if (action === 'echo') {
      result = await actionEcho(payload);
    } else if (action === 'reverse') {
      result = await actionReverse(payload);
    } else if (action === 'compute') {
      result = await actionCompute(payload);
    } else {
      respondError(id, 422, 'unsupported action');
      return;
    }

    if (signal.aborted) {
      respondError(id, 408, 'exec timeout');
      return;
    }

    respondOk(id, result);
  } catch (err: any) {
    if (err.name === 'AbortError' || err.message === 'exec timeout') {
      respondError(id, 408, 'exec timeout');
    } else if (err.message && err.message.startsWith('missing')) {
      respondError(id, 400, err.message);
    } else if (err.message && err.message.includes('numbers must be')) {
      respondError(id, 400, err.message);
    } else {
      errorLog('exec handler unexpected error: ' + (err && err.stack ? err.stack : String(err)));
      respondError(id, 500, 'internal error');
    }
  } finally {
    clearTimeout(timeout);
  }
}

// -------------------- Main loop --------------------

const rl = readline.createInterface({ input: stdin, crlfDelay: Infinity });

rl.on('line', async (line: string) => {
  if (!running) return;
  try {
    // enforce max length by bytes
    const bytes = Buffer.byteLength(line, 'utf8');
    if (bytes > MAX_LINE) {
      warn(`incoming message exceeds MAX_LINE (${bytes} bytes), rejecting`);
      respondError(undefined, 413, 'payload too large');
      return;
    }

    if (!line || !line.trim()) return;

    let msg: IncomingMessage;
    try {
      msg = JSON.parse(line) as IncomingMessage;
    } catch (e) {
      warn('invalid JSON message');
      respondError(undefined, 400, 'invalid JSON');
      return;
    }

    const id = msg.id;
    const type = (msg.type || '').toLowerCase();
    const payload = msg.payload;

    if (!type) {
      respondError(id, 400, "missing 'type'");
      return;
    }

    switch (type) {
      case 'health':
        respondOk(id, { status: 'healthy', version: PLUGIN_VERSION });
        break;
      case 'exec':
        // run exec but don't block other incoming messages; action handlers are async
        handleExec(id, payload).catch((err) => {
          errorLog('unhandled exec error: ' + String(err));
        });
        break;
      case 'shutdown':
      case 'quit':
        respondOk(id, { result: 'shutting_down' });
        running = false;
        // close readline after a short drain
        setImmediate(() => rl.close());
        break;
      default:
        respondError(id, 400, 'unknown type');
    }
  } catch (err) {
    errorLog('main loop error: ' + String(err));
  }
});

rl.on('close', () => {
  info('stdin closed (EOF)');
  running = false;
  stopHeartbeat();
});

process.on('SIGINT', () => {
  warn('SIGINT received, initiating shutdown');
  running = false;
  rl.close();
});
process.on('SIGTERM', () => {
  warn('SIGTERM received, initiating shutdown');
  running = false;
  rl.close();
});

// Start heartbeat and log plugin start
startHeartbeat();
info(`plugin started version=${PLUGIN_VERSION} max_line=${MAX_LINE} heartbeat=${HEARTBEAT} exec_timeout=${EXEC_TIMEOUT}`);

// Export nothing — plugin runs as process
export {};
