/*
 * sample_plugin.c
 *
 * Production-ready C plugin for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Overview
 * --------
 * This plugin is a release-quality template demonstrating:
 *  - robust JSON parsing with cJSON (recommended: https://github.com/DaveGamble/cJSON)
 *  - safe input length limits and validation
 *  - structured logging to stderr (JSON-lines style option)
 *  - graceful shutdown via signals and a "shutdown" message
 *  - background worker for maintenance tasks
 *  - configurable runtime via environment variables
 *  - minimal external dependencies and easy-to-follow build instructions
 *
 * Protocol (stdin/stdout JSON RPC - newline delimited)
 * ---------------------------------------------------
 * Host -> Plugin messages (newline terminated JSON):
 * {
 *   "id": "<uuid>",
 *   "type": "exec" | "health" | "shutdown",
 *   "payload": { ... }
 * }
 *
 * Plugin -> Host responses (newline terminated JSON):
 * {
 *   "id": "<uuid>",
 *   "status": "ok" | "error",
 *   "code": <int>,               // optional for errors
 *   "message": "...",          // optional
 *   "body": { ... }             // optional
 * }
 *
 * Security rationale
 * ------------------
 * - Enforces input size limits to reduce DoS risk.
 * - Uses cJSON which is small and auditable; replace with your organization's
 *   preferred parser if needed.
 * - No dynamic code execution. If external processes are needed, orchestrate
 *   them with a hardened launcher outside the plugin process.
 * - Avoids global mutable state where possible and uses pthreads safely.
 *
 * Build (recommended)
 * -------------------
 * 1) vendor/ or system-provide cJSON. Example with bundled single-file:
 *    - Place cJSON.c and cJSON.h in vendor/cjson/
 *    - gcc -std=c11 -O2 -Wall -Wextra -pthread -Ivendor/cjson -o sample_plugin sample_plugin.c vendor/cjson/cJSON.c
 *
 * 2) With a provided Makefile (recommended): `make` (Makefile available in repo root)
 *
 * Running
 * -------
 * - The plugin reads newline-delimited JSON from stdin and writes newline-delimited
 *   JSON to stdout. Run locally for testing:
 *     echo '{"id":"1","type":"health"}' | ./sample_plugin
 *
 * Configuration via environment variables:
 * - OMNIFLOW_PLUGIN_MAX_LINE=131072    # max bytes per incoming message (default 131072)
 * - OMNIFLOW_PLUGIN_HEARTBEAT=5        # heartbeat interval seconds
 * - OMNIFLOW_LOG_JSON=true             # if set, emit structured JSON logs to stderr
 *
 * Tests & CI
 * ----------
 * - Provide unit tests (CTest or shell-based) that validate message parsing, error
 *   handling, and graceful shutdown. Add GitHub Actions for build, static-analysis (clang-tidy), and SAST.
 *
 * Note: This file is intended to be used as a plugin template. Adjust timeouts,
 * resource limits and allowed actions according to your security policy.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <time.h>
#include <errno.h>
#include <unistd.h>

/* cJSON include - vendor/cjson/cJSON.h
 * Ensure cJSON.c is compiled and linked into the plugin binary.
 */
#include "cJSON.h"

/* ---------------- Configurable constants ---------------- */
#define DEFAULT_MAX_LINE (128 * 1024) /* 128 KiB */
#define DEFAULT_HEARTBEAT 5            /* seconds */
#define PLUGIN_NAME "OmniFlowCRelease"
#define PLUGIN_VERSION "1.0.0"

/* ---------------- Global state ---------------- */
static atomic_bool running = ATOMIC_VAR_INIT(true);
static atomic_bool shutdown_requested = ATOMIC_VAR_INIT(false);
static pthread_t bg_thread;

/* max incoming bytes per message (read from env) */
static size_t MAX_LINE = DEFAULT_MAX_LINE;
static int HEARTBEAT_SEC = DEFAULT_HEARTBEAT;
static bool LOG_JSON = false;

/* ---------------- Utilities ---------------- */
static void current_time_iso8601(char *buf, size_t len) {
    time_t t = time(NULL);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(buf, len, "%Y-%m-%dT%H:%M:%SZ", &tm);
}

static void log_raw(const char *level, const char *msg) {
    char tbuf[32]; current_time_iso8601(tbuf, sizeof(tbuf));
    if (LOG_JSON) {
        /* Structured JSON log */
        fprintf(stderr, "{\"time\":\"%s\",\"level\":\"%s\",\"plugin\":\"%s\",\"message\":%s}\n",
                tbuf, level, PLUGIN_NAME, cJSON_PrintUnformatted(cJSON_CreateString(msg)));
    } else {
        fprintf(stderr, "%s [%s] %s: %s\n", tbuf, level, PLUGIN_NAME, msg);
    }
    fflush(stderr);
}

static void log_info(const char *msg) { log_raw("INFO", msg); }
static void log_warn(const char *msg) { log_raw("WARN", msg); }
static void log_err(const char *msg)  { log_raw("ERROR", msg); }

/* Respond helpers */
static void respond_json(cJSON *obj) {
    char *s = cJSON_PrintUnformatted(obj);
    if (s) {
        printf("%s\n", s);
        fflush(stdout);
        free(s);
    } else {
        /* Fallback minimal error */
        printf("{\"status\":\"error\",\"message\":\"serialization failed\"}\n");
        fflush(stdout);
    }
}

static void respond_ok(const char *id, cJSON *body) {
    cJSON *root = cJSON_CreateObject();
    if (id) cJSON_AddStringToObject(root, "id", id);
    cJSON_AddStringToObject(root, "status", "ok");
    if (body) cJSON_AddItemToObject(root, "body", body);
    respond_json(root);
    cJSON_Delete(root);
}

static void respond_error(const char *id, int code, const char *message) {
    cJSON *root = cJSON_CreateObject();
    if (id) cJSON_AddStringToObject(root, "id", id);
    cJSON_AddStringToObject(root, "status", "error");
    cJSON_AddNumberToObject(root, "code", code);
    if (message) cJSON_AddStringToObject(root, "message", message);
    respond_json(root);
    cJSON_Delete(root);
}

/* ---------------- Background worker ---------------- */
static void *background_worker(void *arg) {
    (void)arg;
    char buf[128];
    snprintf(buf, sizeof(buf), "background worker started (heartbeat=%d)", HEARTBEAT_SEC);
    log_info(buf);
    int counter = 0;
    while (atomic_load(&running)) {
        sleep(HEARTBEAT_SEC);
        if (!atomic_load(&running)) break;
        counter++;
        snprintf(buf, sizeof(buf), "heartbeat %d", counter);
        log_info(buf);
        /* Place periodic maintenance here: metrics flush, temp cleanup, etc. */
    }
    log_info("background worker stopping");
    return NULL;
}

/* ---------------- Signal handling ---------------- */
static void handle_signal(int sig) {
    (void)sig;
    log_warn("signal received, initiating shutdown");
    atomic_store(&shutdown_requested, true);
    atomic_store(&running, false);
}

/* ---------------- Message handlers ---------------- */
static void handle_health(const char *id) {
    cJSON *body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "status", "healthy");
    cJSON_AddStringToObject(body, "version", PLUGIN_VERSION);
    respond_ok(id, body);
}

static void handle_exec(const char *id, cJSON *payload) {
    /* Example supported actions: echo, reverse, compute(sum)
     * Validate payload carefully. */
    if (!payload) { respond_error(id, 400, "missing payload"); return; }
    cJSON *action = cJSON_GetObjectItemCaseSensitive(payload, "action");
    if (!cJSON_IsString(action)) { respond_error(id, 400, "missing or invalid 'action'"); return; }

    if (strcmp(action->valuestring, "echo") == 0) {
        cJSON *msg = cJSON_GetObjectItemCaseSensitive(payload, "message");
        const char *m = cJSON_IsString(msg) ? msg->valuestring : "";
        cJSON *body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "action", "echo");
        cJSON_AddStringToObject(body, "message", m);
        respond_ok(id, body);
        return;
    }
    else if (strcmp(action->valuestring, "reverse") == 0) {
        cJSON *msg = cJSON_GetObjectItemCaseSensitive(payload, "message");
        const char *m = cJSON_IsString(msg) ? msg->valuestring : "";
        size_t n = strlen(m);
        char *rev = malloc(n + 1);
        if (!rev) { respond_error(id, 500, "memory allocation failed"); return; }
        for (size_t i = 0; i < n; ++i) rev[i] = m[n - 1 - i];
        rev[n] = '\0';
        cJSON *body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "action", "reverse");
        cJSON_AddStringToObject(body, "message", rev);
        free(rev);
        respond_ok(id, body);
        return;
    }
    else if (strcmp(action->valuestring, "compute") == 0) {
        cJSON *arr = cJSON_GetObjectItemCaseSensitive(payload, "numbers");
        if (!cJSON_IsArray(arr)) { respond_error(id, 400, "missing or invalid 'numbers' array"); return; }
        long long sum = 0;
        cJSON *elem = NULL;
        cJSON_ArrayForEach(elem, arr) {
            if (!cJSON_IsNumber(elem)) { respond_error(id, 400, "numbers must be numeric"); return; }
            sum += (long long) elem->valuedouble;
        }
        cJSON *body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "action", "compute");
        cJSON_AddNumberToObject(body, "sum", sum);
        respond_ok(id, body);
        return;
    }

    respond_error(id, 422, "unsupported action");
}

/* ---------------- Main loop ---------------- */
int main(int argc, char **argv) {
    (void)argc; (void)argv;

    /* Read configuration from env */
    const char *mx = getenv("OMNIFLOW_PLUGIN_MAX_LINE");
    if (mx) {
        char *end = NULL; long v = strtol(mx, &end, 10);
        if (end != mx && v > 0 && v <= 10 * 1024 * 1024) MAX_LINE = (size_t)v;
    }
    const char *hb = getenv("OMNIFLOW_PLUGIN_HEARTBEAT");
    if (hb) {
        char *end = NULL; long v = strtol(hb, &end, 10);
        if (end != hb && v > 0 && v <= 3600) HEARTBEAT_SEC = (int)v;
    }
    const char *lj = getenv("OMNIFLOW_LOG_JSON");
    if (lj && strlen(lj) > 0) LOG_JSON = true;

    char buf[128];
    snprintf(buf, sizeof(buf), "starting plugin version=%s max_line=%zu heartbeat=%d json_logs=%d", PLUGIN_VERSION, MAX_LINE, HEARTBEAT_SEC, LOG_JSON);
    log_info(buf);

    /* Install signal handlers */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Start background worker */
    if (pthread_create(&bg_thread, NULL, background_worker, NULL) != 0) {
        log_err("failed to create background thread");
        return 1;
    }

    /* Main read loop - read newline-terminated JSON messages */
    char *linebuf = malloc(MAX_LINE + 1);
    if (!linebuf) { log_err("failed to allocate input buffer"); return 1; }

    while (atomic_load(&running)) {
        if (!fgets(linebuf, (int)MAX_LINE + 1, stdin)) {
            if (feof(stdin)) {
                log_info("stdin closed (EOF), exiting");
                break;
            }
            if (ferror(stdin)) {
                log_warn("error reading stdin"); clearerr(stdin); continue;
            }
        }
        size_t len = strnlen(linebuf, MAX_LINE + 1);
        if (len == 0) continue;
        /* If line length == MAX_LINE and last char isn't '\n', the message may be truncated */
        if (len == MAX_LINE && linebuf[len-1] != '\n') {
            log_warn("incoming message truncated to MAX_LINE");
            /* drain remaining characters on this line to avoid corrupting next read */
            int ch;
            while ((ch = getchar()) != EOF && ch != '\n') { /* discard */ }
        }

        /* Trim newline */
        if (linebuf[len-1] == '\n') { linebuf[len-1] = '\0'; len--; }
        if (len == 0) continue;

        /* Parse JSON using cJSON */
        cJSON *msg = cJSON_Parse(linebuf);
        if (!msg) {
            log_warn("failed to parse JSON message");
            respond_error(NULL, 400, "invalid JSON");
            continue;
        }

        cJSON *id = cJSON_GetObjectItemCaseSensitive(msg, "id");
        const char *idstr = NULL;
        if (cJSON_IsString(id)) idstr = id->valuestring;

        cJSON *type = cJSON_GetObjectItemCaseSensitive(msg, "type");
        if (!cJSON_IsString(type)) {
            respond_error(idstr, 400, "missing or invalid 'type'");
            cJSON_Delete(msg);
            continue;
        }

        cJSON *payload = cJSON_GetObjectItemCaseSensitive(msg, "payload");
        if (strcmp(type->valuestring, "health") == 0) {
            handle_health(idstr);
        }
        else if (strcmp(type->valuestring, "exec") == 0) {
            handle_exec(idstr, payload);
        }
        else if (strcmp(type->valuestring, "shutdown") == 0 || strcmp(type->valuestring, "quit") == 0) {
            respond_ok(idstr, cJSON_CreateString("shutting_down"));
            atomic_store(&shutdown_requested, true);
            atomic_store(&running, false);
            cJSON_Delete(msg);
            break;
        }
        else {
            respond_error(idstr, 400, "unknown type");
        }

        cJSON_Delete(msg);

        if (atomic_load(&shutdown_requested)) break;
    }

    /* Graceful shutdown */
    atomic_store(&running, false);
    if (pthread_join(bg_thread, NULL) != 0) {
        log_warn("failed to join background thread");
    }
    free(linebuf);

    log_info("plugin shutdown complete");
    return 0;
}
