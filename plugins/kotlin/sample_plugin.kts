#!/usr/bin/env kotlin
/*
 * sample_plugin.kts
 *
 * Production-ready Kotlin plugin script for OmniFlow (TheSkiF4er/OmniFlow)
 * License: Apache-2.0
 *
 * Notes:
 *  - This file is provided as a Kotlin script (.kts) and is ready for release usage
 *    with minimal packaging (via kotlinc, kscript, or a small fat-jar produced by Gradle).
 *  - Uses kotlinx.coroutines for concurrency and kotlinx.serialization for JSON handling.
 *  - Communicates via newline-delimited JSON on stdin/stdout.
 *  - Includes robust validation, size limits, structured logging, graceful shutdown,
 *    configurable env vars, exec timeouts and a heartbeat background worker.
 *
 * Build / Run (recommended options):
 *  Option A (Gradle, preferred for production):
 *    - Create Gradle Kotlin JVM project with dependencies:
 *        implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.7.3")
 *        implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.0")
 *    - Build fat JAR and run with: java -jar build/libs/sample-plugin.jar
 *
 *  Option B (kscript for quick run):
 *    - Install kscript (https://github.com/holgerbrandl/kscript)
 *    - kscript sample_plugin.kts
 *
 *  Option C (kotlinc script runner):
 *    - kotlinc -script sample_plugin.kts --classpath <path-to-deps>
 *
 * Runtime contract (newline-delimited JSON):
 *  Host -> Plugin:
 *    { "id":"<uuid>", "type":"exec|health|shutdown", "payload": {...} }
 *
 *  Plugin -> Host:
 *    { "id":"<uuid>", "status":"ok|error", "code"?:int, "message"?:string, "body"?:object }
 *
 * Environment variables (defaults shown):
 *  OMNIFLOW_PLUGIN_MAX_LINE=131072   # max bytes per incoming message
 *  OMNIFLOW_PLUGIN_HEARTBEAT=5       # heartbeat seconds
 *  OMNIFLOW_LOG_JSON=false           # if true, emit JSON logs to stderr
 *  OMNIFLOW_EXEC_TIMEOUT=10         # exec handler timeout seconds
 *
 * Security:
 *  - Input size limiting to mitigate DoS.
 *  - No dynamic code execution.
 *  - Use host-enforced least privilege for secrets and network.
 */

@file:DependsOn("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.7.3")
@file:DependsOn("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.0")

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.serialization.*
import kotlinx.serialization.json.*
import java.io.BufferedReader
import java.io.InputStreamReader
import java.time.Instant
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.system.exitProcess

// --- Configuration ---
val PLUGIN_NAME = "OmniFlowKotlinRelease"
val PLUGIN_VERSION = "1.0.0"

val MAX_LINE: Int = System.getenv("OMNIFLOW_PLUGIN_MAX_LINE")?.toIntOrNull()?.takeIf { it in 1024..(10 * 1024 * 1024) } ?: 128 * 1024
val HEARTBEAT: Int = System.getenv("OMNIFLOW_PLUGIN_HEARTBEAT")?.toIntOrNull()?.takeIf { it in 1..3600 } ?: 5
val LOG_JSON: Boolean = System.getenv("OMNIFLOW_LOG_JSON")?.toBooleanStrictOrNull() ?: false
val EXEC_TIMEOUT: Long = System.getenv("OMNIFLOW_EXEC_TIMEOUT")?.toLongOrNull()?.takeIf { it in 1..3600 } ?: 10L

// --- Serialization models ---
@Serializable
data class IncomingMessage(val id: String? = null, val type: String, val payload: JsonElement? = null)

@Serializable
data class Response(val id: String? = null, val status: String, val code: Int? = null, val message: String? = null, val body: JsonElement? = null)

val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

// --- Logging ---
fun nowIso(): String = Instant.now().toString()
fun log(level: String, message: String, extra: JsonElement? = null) {
    if (LOG_JSON) {
        val obj = buildJsonObject {
            put("time", nowIso())
            put("level", level)
            put("plugin", PLUGIN_NAME)
            put("message", message)
            if (extra != null) put("extra", extra)
        }
        System.err.println(json.encodeToString(obj))
    } else {
        System.err.println("${nowIso()} [$level] $PLUGIN_NAME: $message")
    }
}
fun info(msg: String) = log("INFO", msg)
fun warn(msg: String) = log("WARN", msg)
fun errorLog(msg: String) = log("ERROR", msg)

// --- Runtime control ---
val running = AtomicBoolean(true)
val shutdownRequested = AtomicBoolean(false)

// Channel for inbound lines
val inbound = Channel<String>(capacity = Channel.UNLIMITED)

// --- Helpers ---
fun respond(resp: Response) {
    val s = json.encodeToString(resp)
    println(s)
    System.out.flush()
}

fun respondOk(id: String?, body: JsonElement? = null) { respond(Response(id = id, status = "ok", body = body)) }
fun respondError(id: String?, code: Int, message: String) { respond(Response(id = id, status = "error", code = code, message = message)) }

// --- Background worker ---
fun CoroutineScope.startHeartbeat() = launch {
    info("background worker started; heartbeat=${HEARTBEAT}")
    var counter = 0
    while (isActive && running.get()) {
        delay(HEARTBEAT * 1000L)
        if (!running.get()) break
        counter++
        info("heartbeat $counter")
    }
    info("background worker stopping")
}

// --- Exec handlers ---
suspend fun handleExec(id: String?, payload: JsonElement?) {
    if (payload == null || payload !is JsonObject) { respondError(id, 400, "missing or invalid payload"); return }
    val action = payload["action"]?.jsonPrimitive?.contentOrNull
    if (action == null) { respondError(id, 400, "missing action"); return }

    when (action) {
        "echo" -> {
            val msg = payload["message"]?.jsonPrimitive?.contentOrNull ?: ""
            val body = buildJsonObject { put("action","echo"); put("message", msg) }
            respondOk(id, body)
        }
        "reverse" -> {
            val msg = payload["message"]?.jsonPrimitive?.contentOrNull ?: ""
            val rev = msg.toCharArray().reversed().joinToString(separator = "")
            val body = buildJsonObject { put("action","reverse"); put("message", rev) }
            respondOk(id, body)
        }
        "compute" -> {
            val arr = payload["numbers"]
            if (arr == null || arr !is JsonArray) { respondError(id, 400, "missing or invalid numbers array"); return }
            var sum = 0.0
            for (el in arr) {
                val n = el.jsonPrimitive.doubleOrNull
                if (n == null) { respondError(id, 400, "numbers must be numeric"); return }
                sum += n
            }
            val body = buildJsonObject { put("action","compute"); put("sum", JsonPrimitive(sum)) }
            respondOk(id, body)
        }
        else -> respondError(id, 422, "unsupported action")
    }
}

// --- Line reader coroutine ---
fun CoroutineScope.startLineReader() = launch(Dispatchers.IO) {
    val reader = BufferedReader(InputStreamReader(System.`in`))
    while (running.get()) {
        val line = try { reader.readLine() } catch (e: Exception) { null }
        if (line == null) { // EOF
            info("stdin closed (EOF)")
            running.set(false)
            break
        }
        val bytes = line.toByteArray(Charsets.UTF_8).size
        if (bytes > MAX_LINE) {
            warn("incoming message too large ($bytes bytes), rejecting")
            respondError(null, 413, "payload too large")
            continue
        }
        inbound.send(line)
    }
}

// --- Main processing loop ---
fun CoroutineScope.startProcessor() = launch {
    for (line in inbound) {
        if (!running.get()) break
        val parsed = try { json.parseToJsonElement(line) } catch (e: Exception) { null }
        if (parsed == null || parsed !is JsonObject) {
            warn("invalid JSON message")
            respondError(null, 400, "invalid JSON")
            continue
        }
        val msg = try { json.decodeFromJsonElement<IncomingMessage>(parsed) } catch (e: Exception) { null }
        if (msg == null) { respondError(null, 400, "invalid message shape"); continue }
        val id = msg.id
        val type = msg.type.lowercase()
        when (type) {
            "health" -> handleHealth(id)
            "exec" -> {
                // enforce exec timeout
                try {
                    withTimeout(EXEC_TIMEOUT * 1000L) { handleExec(id, msg.payload) }
                } catch (e: TimeoutCancellationException) {
                    respondError(id, 408, "exec timeout")
                }
            }
            "shutdown", "quit" -> {
                respondOk(id, JsonPrimitive("shutting_down"))
                shutdownRequested.set(true)
                running.set(false)
                break
            }
            else -> respondError(id, 400, "unknown type")
        }
    }
}

fun handleHealth(id: String?) { respondOk(id, buildJsonObject { put("status","healthy"); put("version", PLUGIN_VERSION) }) }

// --- Signal handling ---
fun setupSignalHandlers() {
    val thread = Thread {
        try {
            val signals = arrayOf("INT", "TERM")
            val sig = sun.misc.Signal.handle(sun.misc.Signal("INT")) { }
            // Note: using sun.misc.Signal is platform dependent. For production prefer JVM signal libraries or
            // rely on external process supervisors to send shutdown messages.
        } catch (t: Throwable) {
            // ignore; best-effort
        }
    }
    thread.isDaemon = true
    thread.start()
}

// --- Entry point ---
runBlocking {
    info("plugin starting version=$PLUGIN_VERSION maxLine=$MAX_LINE heartbeat=$HEARTBEAT execTimeout=$EXEC_TIMEOUT")
    // Start reader and background worker
    val readerJob = startLineReader()
    val heartbeatJob = startHeartbeat()
    val processorJob = startProcessor()

    // Simple signal handling: rely on host to send shutdown message; but listen for JVM shutdown hook
    Runtime.getRuntime().addShutdownHook(Thread {
        warn("JVM shutdown hook triggered")
        shutdownRequested.set(true)
        running.set(false)
    })

    // Wait for processor to finish
    processorJob.join()

    // cleanup
    inbound.close()
    readerJob.cancelAndJoin()
    heartbeatJob.cancelAndJoin()

    info("plugin shutdown complete")
    // Small sleep to flush IO
    delay(50)
    exitProcess(0)
}
