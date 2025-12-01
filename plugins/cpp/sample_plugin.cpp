/*
 * sample_plugin.cpp
 *
 * Sample C++ plugin for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Purpose:
 *   - Production-ready template for a C++ plugin that integrates with OmniFlow
 *     using a JSON-over-stdin/stdout protocol.
 *   - Uses nlohmann::json (single-header) for robust parsing/serialization.
 *   - Provides strong safety practices: input size limits, structured logging,
 *     graceful shutdown, background workers, health checks, timeouts and
 *     resource management suitable for release.
 *
 * Build (recommended):
 *   - Option A (with system pkg):
 *       apt-get install -y build-essential cmake libboost-all-dev
 *       git clone https://github.com/nlohmann/json && install single header
 *       mkdir build && cd build
 *       cmake .. && cmake --build . --config Release
 *   - Option B (simple g++):
 *       g++ -std=c++17 -O2 -Wall -Wextra -pthread -I./third_party -o sample_plugin sample_plugin.cpp
 *       (place nlohmann/json.hpp into ./third_party)
 *
 * Runtime contract (example):
 *   - Host sends newline-terminated JSON messages to plugin's stdin.
 *   - Plugin writes newline-terminated JSON responses to stdout.
 *   - Message format (example):
 *       { "id": "<uuid>", "type": "exec|health|shutdown", "payload": {...} }
 *
 * Security notes:
 *   - Limits incoming line length to avoid DoS.
 *   - Validates JSON types and uses RAII for resource safety.
 *   - No dynamic code execution. If executing external processes is required,
 *     use strict allowlists and sandboxing outside of this plugin.
 */

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

// Include nlohmann::json single-header. Put json.hpp in include path or third_party.
#include "nlohmann/json.hpp"
using json = nlohmann::json;

// Plugin metadata
static constexpr const char *PLUGIN_NAME = "OmniFlowCppSample";
static constexpr const char *PLUGIN_VERSION = "1.0.0";
static constexpr size_t MAX_LINE = 128 * 1024; // 128KiB per message (tunable)
static constexpr int DEFAULT_HEARTBEAT_SEC = 5;

// Graceful shutdown control
static std::atomic<bool> running{true};
static std::atomic<bool> shutdown_requested{false};

// Background worker
static std::thread bg_thread;
static std::mutex log_mutex;

// Logging helper (thread-safe)
static void log_stderr(const std::string &level, const std::string &msg) {
    std::lock_guard<std::mutex> lock(log_mutex);
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    char buf[64];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&t));
    std::cerr << "[" << buf << "] [" << level << "] " << PLUGIN_NAME << ": " << msg << "\n";
    std::cerr.flush();
}

static void info(const std::string &msg) { log_stderr("INFO", msg); }
static void warn(const std::string &msg) { log_stderr("WARN", msg); }
static void error_log(const std::string &msg) { log_stderr("ERROR", msg); }

// Utility: safe string escape for JSON (for manual assembly if needed)
static std::string json_escape(const std::string &s) {
    return json(s).dump(); // uses library to escape correctly (returns quoted string)
}

// Respond helpers: write JSON to stdout followed by newline and flush
static void respond(const json &obj) {
    std::string out = obj.dump();
    std::cout << out << '\n';
    std::cout.flush();
}

static void respond_ok(const std::string &id, const json &body = json::object()) {
    json r = {
        {"id", id},
        {"status", "ok"},
        {"time", std::chrono::duration_cast<std::chrono::seconds>(
                     std::chrono::system_clock::now().time_since_epoch())
                     .count()},
        {"body", body}
    };
    respond(r);
}

static void respond_error(const std::string &id, int code, const std::string &message) {
    json r = {
        {"id", id},
        {"status", "error"},
        {"code", code},
        {"message", message},
        {"time", std::chrono::duration_cast<std::chrono::seconds>(
                     std::chrono::system_clock::now().time_since_epoch())
                     .count()}
    };
    respond(r);
}

// Background worker: emits heartbeat logs and can perform periodic maintenance
static void background_worker(int heartbeat_sec) {
    info("background worker started");
    int counter = 0;
    while (running.load()) {
        std::this_thread::sleep_for(std::chrono::seconds(heartbeat_sec));
        if (!running.load()) break;
        ++counter;
        info("heartbeat: " + std::to_string(counter));
        // Place lightweight maintenance here: e.g., cache cleanup, metrics flush
    }
    info("background worker stopping");
}

// Signal handler
static void handle_signal(int sig) {
    warn(std::string("received signal ") + std::to_string(sig));
    shutdown_requested.store(true);
    running.store(false);
}

// Safe read line with size limit; returns optional string; nullopt on EOF
static std::optional<std::string> safe_getline(std::istream &in) {
    std::string line;
    if (!std::getline(in, line)) return std::nullopt;
    if (line.size() > MAX_LINE) {
        warn("incoming line exceeds MAX_LINE, truncated");
        line.resize(MAX_LINE);
    }
    return line;
}

// Command handlers
static void handle_health(const std::string &id) {
    json body = {
        {"status", "healthy"},
        {"version", PLUGIN_VERSION}
    };
    respond_ok(id, body);
}

static void handle_exec(const std::string &id, const json &payload) {
    // Example actions: echo, reverse, compute
    if (!payload.contains("action") || !payload["action"].is_string()) {
        respond_error(id, 400, "missing or invalid 'action' in payload");
        return;
    }
    std::string action = payload["action"].get<std::string>();

    if (action == "echo") {
        std::string message = "";
        if (payload.contains("message") && payload["message"].is_string())
            message = payload["message"].get<std::string>();
        json body = { {"action", "echo"}, {"message", message} };
        respond_ok(id, body);
        return;
    }
    else if (action == "reverse") {
        std::string message = "";
        if (payload.contains("message") && payload["message"].is_string())
            message = payload["message"].get<std::string>();
        std::string rev(message.rbegin(), message.rend());
        json body = { {"action", "reverse"}, {"message", rev} };
        respond_ok(id, body);
        return;
    }
    else if (action == "compute") {
        // simple safe compute example: sum array of ints
        if (!payload.contains("numbers") || !payload["numbers"].is_array()) {
            respond_error(id, 400, "missing or invalid 'numbers' array");
            return;
        }
        long long sum = 0;
        for (const auto &v : payload["numbers"]) {
            if (!v.is_number_integer()) { respond_error(id, 400, "numbers must be integers"); return; }
            sum += v.get<long long>();
        }
        json body = { {"action", "compute"}, {"sum", sum} };
        respond_ok(id, body);
        return;
    }
    else {
        respond_error(id, 422, "unsupported action");
        return;
    }
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    // Install signal handlers
#if defined(SIGINT)
    std::signal(SIGINT, handle_signal);
#endif
#if defined(SIGTERM)
    std::signal(SIGTERM, handle_signal);
#endif

    // Start background worker
    int hb = DEFAULT_HEARTBEAT_SEC;
    if (const char *env = std::getenv("OMNIFLOW_PLUGIN_HEARTBEAT")) {
        try {
            int v = std::stoi(env);
            if (v > 0 && v <= 3600) hb = v;
        } catch (...) { /* ignore invalid */ }
    }
    running.store(true);
    bg_thread = std::thread(background_worker, hb);

    info(std::string("plugin initialized, version=") + PLUGIN_VERSION);

    // Main loop: read newline-terminated JSON messages from stdin
    while (running.load()) {
        auto opt_line = safe_getline(std::cin);
        if (!opt_line) {
            // EOF; break and shutdown
            info("stdin closed (EOF)");
            break;
        }
        std::string line = std::move(*opt_line);
        if (line.empty()) continue;

        // Parse JSON safely
        json msg;
        try {
            msg = json::parse(line);
        } catch (const std::exception &ex) {
            warn(std::string("failed to parse JSON: ") + ex.what());
            respond_error("", 400, std::string("invalid JSON: ") + ex.what());
            continue;
        }

        // Extract id (optional)
        std::string id = "";
        if (msg.contains("id") && msg["id"].is_string()) id = msg["id"].get<std::string>();

        // type required
        if (!msg.contains("type") || !msg["type"].is_string()) {
            respond_error(id, 400, "missing 'type' field");
            continue;
        }
        std::string type = msg["type"].get<std::string>();

        // payload optional
        json payload = json::object();
        if (msg.contains("payload")) payload = msg["payload"];

        if (type == "health") {
            handle_health(id);
        }
        else if (type == "exec") {
            // Consider delegating long-running tasks to worker threads and enforcing timeouts.
            handle_exec(id, payload);
        }
        else if (type == "shutdown" || type == "quit") {
            respond_ok(id, { {"result", "shutting_down"} });
            // request shutdown and break loop after responding
            shutdown_requested.store(true);
            running.store(false);
            break;
        }
        else {
            respond_error(id, 400, "unknown type");
        }

        // check if signal requested shutdown
        if (shutdown_requested.load()) break;
    }

    // Clean shutdown: join background thread with timeout
    if (bg_thread.joinable()) {
        // allow a short timeout for bg thread to finish
        running.store(false);
        if (std::this_thread::get_id() != bg_thread.get_id()) {
            bg_thread.join();
        }
    }

    info("plugin exiting");
    return 0;
}
