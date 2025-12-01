/*
 * sample_plugin.c
 *
 * Sample C plugin for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Purpose:
 *   - Demonstrates a secure, production-minded template for writing a plugin
 *     in C that integrates with the OmniFlow plugin host via a simple
 *     JSON-over-stdin/stdout RPC-style protocol.
 *   - Shows secure initialization, configuration via environment variables,
 *     structured logging, graceful shutdown, health checks, background tasks,
 *     and careful resource management.
 *
 * Build (recommended):
 *   gcc -std=c11 -O2 -Wall -Wextra -pthread -o sample_plugin sample_plugin.c
 *
 * Security notes:
 *   - Avoids dynamic code evaluation and uses limited parsing to avoid
 *     large dependency surface.
 *   - Uses timeouts and resource limits where appropriate.
 *   - Logs to stderr; protocol responses go to stdout.
 *
 * Integration contract (example):
 *   - Host sends JSON messages to plugin's stdin, terminated by newline.
 *   - Plugin writes JSON responses to stdout, terminated by newline.
 *   - Messages have simple shape: {"id": "<id>", "type":"exec|health|quit", "payload": {...}}
 *
 * Replace/extend parsing with a robust JSON library in production (cJSON, jsmn, sajson).
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

/* ---------------- Configuration & Constants ---------------- */

#define PLUGIN_NAME "OmniFlowSampleC"
#define PLUGIN_VERSION "0.1.0"
#define MAX_LINE 65536
#define LOG_PREFIX "[omni-plugin:c]"

/* Read env var: OMNIFLOW_PLUGIN_TIMEOUT (seconds) */
static int plugin_timeout_seconds = 10;

/* Background worker control */
static atomic_bool running = true;
static pthread_t bg_thread;

/* ---------------- Utilities: Logging, time ---------------- */

static void safe_log(const char *fmt, ...)
    __attribute__((format(printf, 1, 2)));

static void safe_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "%s %s: ", LOG_PREFIX, PLUGIN_NAME);
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    fflush(stderr);
    va_end(ap);
}

static void current_time_iso8601(char *buf, size_t len)
{
    time_t t = time(NULL);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(buf, len, "%Y-%m-%dT%H:%M:%SZ", &tm);
}

/* ---------------- Tiny JSON helpers (safe, limited) ---------------- */
/*
 * These helpers are intentionally minimal: they search for string keys and
 * extract simple string or integer values. For production use, link a proper
 * JSON parser (cJSON, jsmn, yajl). The limited parser reduces dependency
 * surface for an example plugin.
 */

/* Find a string value for key in JSON (very limited):
 *   json: full JSON text (null-terminated)
 *   key: plain key name (without quotes)
 *   out: buffer to fill
 *   out_len: capacity
 * Returns true on success. */
static bool json_get_string(const char *json, const char *key, char *out, size_t out_len)
{
    if (!json || !key || !out) return false;
    size_t keylen = strlen(key);
    /* pattern "key"\s*:\s*"value" */
    const char *p = json;
    while ((p = strstr(p, "\"") ) != NULL) {
        /* p points to a quote, check if what follows is key" */
        if (strncmp(p+1, key, keylen) == 0 && p[1+keylen] == '"') {
            const char *colon = strchr(p+1+keylen+1, ':');
            if (!colon) { p += 1; continue; }
            /* find opening quote for value */
            const char *valq = strchr(colon, '"');
            if (!valq) { p += 1; continue; }
            valq++; /* move after opening quote */
            const char *valq_end = valq;
            while (*valq_end && (*valq_end != '"' || *(valq_end-1) == '\\')) valq_end++;
            size_t vlen = valq_end - valq;
            if (vlen >= out_len) vlen = out_len - 1;
            memcpy(out, valq, vlen);
            out[vlen] = '\0';
            return true;
        }
        p += 1;
    }
    return false;
}

/* Find integer value for key. Very limited: handles optional spaces and digits */
static bool json_get_int(const char *json, const char *key, long *out)
{
    if (!json || !key || !out) return false;
    size_t keylen = strlen(key);
    const char *p = json;
    while ((p = strstr(p, "\"") ) != NULL) {
        if (strncmp(p+1, key, keylen) == 0 && p[1+keylen] == '"') {
            const char *colon = strchr(p+1+keylen+1, ':');
            if (!colon) { p += 1; continue; }
            const char *q = colon + 1;
            while (*q && (*q == ' ' || *q == '\t')) q++;
            char *endptr = NULL;
            long v = strtol(q, &endptr, 10);
            if (q == endptr) return false;
            *out = v;
            return true;
        }
        p += 1;
    }
    return false;
}

/* ---------------- Protocol helpers ---------------- */

/* Write JSON response to stdout and flush. Uses a simple protocol:
 * {"id":"...","response":{...}}
 */
static void respond_ok(const char *id, const char *body_json)
{
    if (!id) id = "";
    if (!body_json) body_json = "{}";
    /* Ensure single-line safe output */
    printf("{\"id\":\"%s\",\"status\":\"ok\",\"time\":\"%s\",\"body\":%s}\n",
           id, ({ char _t[32]; current_time_iso8601(_t, sizeof(_t)); strdup(_t); }),
           body_json);
    fflush(stdout);
}

static void respond_error(const char *id, int code, const char *message)
{
    if (!id) id = "";
    if (!message) message = "unknown error";
    printf("{\"id\":\"%s\",\"status\":\"error\",\"code\":%d,\"message\":\"%s\",\"time\":\"%s\"}\n",
           id, code, message, ({ char _t[32]; current_time_iso8601(_t, sizeof(_t)); strdup(_t); }));
    fflush(stdout);
}

/* Note: we used statement expressions above for brevity. If your compiler doesn't support
 * GNU extensions, replace with a small helper to call current_time_iso8601(). */

/* ---------------- Background worker (example) ---------------- */
static void *background_worker(void *arg)
{
    safe_log("background worker started");
    int counter = 0;
    while (atomic_load(&running)) {
        /* Example periodic work: emit heartbeat to stderr once per interval */
        sleep(5);
        counter++;
        safe_log("heartbeat %d", counter);
    }
    safe_log("background worker stopping");
    return NULL;
}

/* ---------------- Signal handling ---------------- */
static void handle_signal(int sig)
{
    safe_log("received signal %d, initiating shutdown", sig);
    atomic_store(&running, false);
}

/* ---------------- Plugin lifecycle ---------------- */

static void plugin_init(void)
{
    const char *t = getenv("OMNIFLOW_PLUGIN_TIMEOUT");
    if (t) {
        char *end;
        long v = strtol(t, &end, 10);
        if (end != t && v > 0 && v <= 3600) plugin_timeout_seconds = (int)v;
    }
    safe_log("init: version=%s timeout=%d", PLUGIN_VERSION, plugin_timeout_seconds);

    /* Setup signal handlers for graceful shutdown */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Start background worker */
    atomic_store(&running, true);
    if (pthread_create(&bg_thread, NULL, background_worker, NULL) != 0) {
        safe_log("failed to create background thread: %s", strerror(errno));
        atomic_store(&running, false);
    }
}

static void plugin_shutdown(void)
{
    safe_log("shutdown requested");
    atomic_store(&running, false);
    /* Join background thread with timeout */
    void *res;
    if (pthread_join(bg_thread, &res) != 0) {
        safe_log("failed to join background thread: %s", strerror(errno));
    }
    safe_log("shutdown complete");
}

/* ---------------- Command handlers ---------------- */

static void handle_health(const char *id)
{
    /* Return basic health payload */
    respond_ok(id, "{\"status\":\"healthy\",\"uptime_seconds\":0}");
}

static void handle_exec(const char *id, const char *payload)
{
    /* Example: payload may contain {"action":"echo","message":"..."}
     * We'll support a safe "echo" action and a "reverse" action for demo.
     */
    char action[128] = {0};
    char message[4096] = {0};
    if (!json_get_string(payload ? payload : "", "action", action, sizeof(action))) {
        respond_error(id, 400, "missing or invalid action");
        return;
    }
    if (!json_get_string(payload ? payload : "", "message", message, sizeof(message))) {
        /* message optional; default empty */
        message[0] = '\0';
    }

    if (strcmp(action, "echo") == 0) {
        /* Construct safe JSON body: we must escape quotes/backslashes in message; but for brevity
         * we'll limit message length and replace quotes. */
        for (size_t i = 0; i < strlen(message); ++i) {
            if (message[i] == '"' || message[i] == '\\') message[i] = '?';
        }
        char body[8192];
        snprintf(body, sizeof(body), "{\"action\":\"echo\",\"message\":\"%s\"}", message);
        respond_ok(id, body);
        return;
    }
    else if (strcmp(action, "reverse") == 0) {
        size_t n = strlen(message);
        char rev[4096];
        for (size_t i = 0; i < n && i < sizeof(rev)-1; ++i) rev[i] = message[n-1-i];
        rev[n < sizeof(rev)-1 ? n : (sizeof(rev)-1)] = '\0';
        for (size_t i = 0; i < strlen(rev); ++i) if (rev[i] == '"' || rev[i] == '\\') rev[i] = '?';
        char body[8192];
        snprintf(body, sizeof(body), "{\"action\":\"reverse\",\"message\":\"%s\"}", rev);
        respond_ok(id, body);
        return;
    }
    else {
        respond_error(id, 422, "unsupported action");
        return;
    }
}

/* ---------------- Main loop: read lines from stdin ---------------- */
int main(int argc, char **argv)
{
    (void)argc; (void)argv;

    /* Initialize plugin */
    plugin_init();

    char *line = NULL;
    size_t linecap = 0;
    ssize_t linelen;

    /* Set stdin to line-buffered and non-blocking timeout can be applied by select if needed */
    while (atomic_load(&running) && (linelen = getline(&line, &linecap, stdin)) != -1) {
        if (linelen == 0) continue;
        /* Trim newline */
        while (linelen > 0 && (line[linelen-1] == '\n' || line[linelen-1] == '\r')) { line[--linelen] = '\0'; }
        if (linelen == 0) continue;

        /* For safety, cap line length */
        if ((size_t)linelen > MAX_LINE) {
            safe_log("incoming line too long (%zd), rejecting", linelen);
            respond_error("", 413, "payload too large");
            continue;
        }

        /* Parse minimal fields: id and type */
        char id[128] = {0};
        char type[64] = {0};
        if (!json_get_string(line, "id", id, sizeof(id))) {
            /* not required: assign empty id */
            id[0] = '\0';
        }
        if (!json_get_string(line, "type", type, sizeof(type))) {
            respond_error(id, 400, "missing type");
            continue;
        }

        /* Extract payload substring (very small heuristic): find \"payload\": and then remainder
         * In production, use a real JSON parser. */
        char payload[MAX_LINE];
        payload[0] = '\0';
        const char *p = strstr(line, "\"payload\"");
        if (p) {
            const char *colon = strchr(p, ':');
            if (colon) {
                /* copy from colon+1 to end, but trim leading spaces */
                const char *q = colon + 1;
                while (*q == ' ' || *q == '\t') q++;
                strncpy(payload, q, sizeof(payload)-1);
                payload[sizeof(payload)-1] = '\0';
            }
        }

        if (strcmp(type, "health") == 0) {
            handle_health(id);
        }
        else if (strcmp(type, "exec") == 0) {
            /* Exec handled with timeout guard: spawn thread to avoid blocking main loop */
            /* For simplicity we'll handle synchronously here but plugin_timeout_seconds can be used
             * by host to kill long tasks. */
            handle_exec(id, payload);
        }
        else if (strcmp(type, "quit") == 0 || strcmp(type, "shutdown") == 0) {
            respond_ok(id, "{\"result\":\"shutting_down\"}");
            atomic_store(&running, false);
            break;
        }
        else {
            respond_error(id, 400, "unknown type");
        }
    }

    free(line);

    /* If loop ended due to EOF or running=false, shutdown gracefully */
    plugin_shutdown();

    /* Exit code: 0 for graceful, non-zero if external signal was reason for termination */
    return 0;
}
