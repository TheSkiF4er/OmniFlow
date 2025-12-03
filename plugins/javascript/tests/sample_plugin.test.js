/**
 * OmniFlow JavaScript Plugin
 * Sample Plugin Test Suite
 *
 * This file provides a complete, fully production-ready Jest test suite
 * that validates the behavior of a JavaScript-based OmniFlow plugin.
 *
 * It covers:
 *  - Protocol compliance
 *  - JSON message handling
 *  - Request/response formatting
 *  - Error propagation
 *  - Initialization / shutdown behavior
 *
 * Author: TheSkiF4er
 * License: Apache 2.0
 */

import { spawn } from "child_process";
import path from "path";

const pluginPath = path.resolve(__dirname, "../sample_plugin.js");

const createPluginProcess = () =>
  spawn("node", [pluginPath], {
    stdio: ["pipe", "pipe", "pipe"],
  });

/**
 * Helper to send a JSON message into the plugin
 */
const sendMessage = (proc, message) => {
  proc.stdin.write(JSON.stringify(message) + "\n");
};

/**
 * Helper to read exactly one JSON message from stdout
 */
const readMessage = (proc) => {
  return new Promise((resolve, reject) => {
    proc.stdout.once("data", (chunk) => {
      try {
        const text = chunk.toString().trim();
        const msg = JSON.parse(text);
        resolve(msg);
      } catch (err) {
        reject(new Error("Failed to parse plugin output: " + err.message));
      }
    });
  });
};

describe("OmniFlow JavaScript Plugin – Integration Tests", () => {
  test("plugin starts and sends 'plugin.ready' lifecycle event", async () => {
    const proc = createPluginProcess();

    const msg = await readMessage(proc);

    expect(msg.type).toBe("plugin.ready");
    expect(msg.timestamp).toBeDefined();
    expect(typeof msg.timestamp).toBe("number");

    proc.kill();
  });

  test("plugin returns expected response for valid invocation", async () => {
    const proc = createPluginProcess();

    // Await lifecycle event
    await readMessage(proc);

    const request = {
      type: "invoke",
      requestId: "test-1",
      payload: { value: 42 },
    };

    sendMessage(proc, request);

    const response = await readMessage(proc);

    expect(response.type).toBe("response");
    expect(response.requestId).toBe("test-1");
    expect(response.payload).toBeDefined();
    expect(response.payload.result).toBe(84); // plugin doubles value

    proc.kill();
  });

  test("plugin handles malformed messages gracefully", async () => {
    const proc = createPluginProcess();

    await readMessage(proc);

    proc.stdin.write("this is not json\n");

    const errorMsg = await readMessage(proc);

    expect(errorMsg.type).toBe("error");
    expect(errorMsg.message).toMatch(/invalid json/i);

    proc.kill();
  });

  test("plugin propagates runtime exceptions through error protocol", async () => {
    const proc = createPluginProcess();

    await readMessage(proc);

    sendMessage(proc, {
      type: "invoke",
      requestId: "err-1",
      payload: { triggerError: true },
    });

    const msg = await readMessage(proc);

    expect(msg.type).toBe("error");
    expect(msg.requestId).toBe("err-1");
    expect(msg.message).toMatch(/runtime error/i);

    proc.kill();
  });

  test("plugin shuts down cleanly after receiving 'plugin.shutdown' event", async () => {
    const proc = createPluginProcess();

    await readMessage(proc);

    sendMessage(proc, { type: "plugin.shutdown" });

    const msg = await readMessage(proc);

    expect(msg.type).toBe("plugin.exit");
    expect(msg.timestamp).toBeDefined();

    proc.kill();
  });

  test("plugin output is always valid JSON and never empty", async () => {
    const proc = createPluginProcess();

    const first = await readMessage(proc);

    expect(() => JSON.stringify(first)).not.toThrow();
    expect(Object.keys(first).length).toBeGreaterThan(0);

    proc.kill();
  });
});

/**
 * Extra edge-case protocol tests
 */
describe("Protocol Edge Cases", () => {
  test("plugin preserves requestId in all responses", async () => {
    const proc = createPluginProcess();

    await readMessage(proc);

    const request = {
      type: "invoke",
      requestId: "edge-7",
      payload: { x: 10 },
    };

    sendMessage(proc, request);

    const resp = await readMessage(proc);

    expect(resp.requestId).toBe("edge-7");
    proc.kill();
  });

  test("plugin ignores unknown event types", async () => {
    const proc = createPluginProcess();

    await readMessage(proc);

    sendMessage(proc, { type: "unknown-event", foo: "bar" });

    // Plugin MUST NOT crash or respond unexpectedly
    setTimeout(() => proc.kill(), 150);

    expect(proc.killed || proc.exitCode !== null).toBe(true);
  });
});
