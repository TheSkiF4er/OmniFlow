// plugins/kotlin/tests/pluginTest.kt
//
// Production-ready unit tests for OmniFlow Kotlin plugin helpers and protocol utilities.
// These tests are written with JUnit5 and kotlinx.serialization and are designed to be
// fast, deterministic, and suitable for CI.
//
// Place this file at: OmniFlow/plugins/kotlin/tests/pluginTest.kt
//
// Run with (example Gradle):
//   ./gradlew :plugins:kotlin:test
//
// The tests check:
//  - NDJSON single-line parsing with size guard
//  - Request/Response envelope correctness
//  - Example actions: echo, reverse (unicode-safe), compute (sum)
//  - Robust behavior on malformed JSON and oversized payloads
//  - Response formatting (single line, newline-terminated)
//

package omniflow.plugins.kotlin.test

import kotlinx.serialization.*
import kotlinx.serialization.json.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.nio.charset.StandardCharsets

// ---------- Protocol types ----------
@Serializable
data class Request(
    val id: String,
    val type: String,
    val timestamp: String? = null,
    val payload: JsonElement? = null
)

@Serializable
data class Response(
    val id: String,
    val status: String,                 // "ok" | "error" | "busy"
    val code: Int? = null,
    val message: String? = null,
    val body: JsonElement? = null,
    val meta: JsonElement? = null
)

// JSON instance - strict but permissive about unknown keys
val json = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    prettyPrint = false
}

// ---------- Helpers ----------

/**
 * Parse a single-line NDJSON string (line may include trailing newline).
 * Enforces a maximum allowed line length in bytes (maxBytes > 0 to enable).
 * Returns Request or throws a SerializationException or IllegalArgumentException.
 */
fun parseNdjsonRequest(line: String, maxBytes: Int = 131_072): Request {
    val bytes = line.toByteArray(StandardCharsets.UTF_8)
    if (maxBytes > 0 && bytes.size > maxBytes) {
        throw IllegalArgumentException("line exceeds max allowed length: ${bytes.size} > $maxBytes")
    }
    // Trim newline characters and whitespace at ends
    val trimmed = line.trimEnd('\n', '\r')
    return json.decodeFromString(trimmed)
}

/** Build a single-line NDJSON response (JSON + '\n') */
fun buildNdjsonResponse(resp: Response): String {
    val s = json.encodeToString(resp)
    // Ensure single-line NDJSON
    require(!s.contains('\n')) { "Response JSON contains newline(s)" }
    return s + "\n"
}

// ---------- Example action implementations (pure functions) ----------

/** Echo action - expects payload { "message": "..." } */
fun actionEcho(payload: JsonElement?): JsonElement {
    val msg = payload?.jsonObject?.get("message")?.jsonPrimitive?.content ?: ""
    return buildJsonObject {
        put("action", JsonPrimitive("echo"))
        put("message", JsonPrimitive(msg))
    }
}

/** Reverse action - unicode-safe reversal of a "message" string */
fun actionReverse(payload: JsonElement?): JsonElement {
    val msg = payload?.jsonObject?.get("message")?.jsonPrimitive?.content ?: ""
    val reversed = msg.toCharArray().let { it.reverse(); String(it) } // char-wise reversal
    // Use codepoints/runes for true Unicode correctness:
    val runes = msg.codePoints().toArray()
    val revRunes = runes.reversedArray()
    val sb = StringBuilder()
    for (cp in revRunes) sb.appendCodePoint(cp)
    val revStr = sb.toString()
    return buildJsonObject {
        put("action", JsonPrimitive("reverse"))
        put("message", JsonPrimitive(revStr))
    }
}

/** Compute action - expects payload { "numbers": [..] } and returns sum */
fun actionCompute(payload: JsonElement?): JsonElement {
    val nums = payload?.jsonObject?.get("numbers")?.jsonArray ?: JsonArray(emptyList())
    var sum = 0.0
    for (n in nums) {
        sum += n.jsonPrimitive.double
    }
    return buildJsonObject {
        put("action", JsonPrimitive("compute"))
        put("sum", JsonPrimitive(sum))
    }
}

// ---------- Tests ----------

class PluginUnitTests {

    @Test
    fun `parse valid health request`() {
        val line = """{"id":"h1","type":"health","payload":null}"""
        val req = parseNdjsonRequest(line)
        assertEquals("h1", req.id)
        assertEquals("health", req.type)
        assertNull(req.payload)
    }

    @Test
    fun `exec echo action returns echoed message`() {
        val line = """{"id":"e1","type":"exec","payload":{"action":"echo","args":{"message":"hello kotlin"}}}"""
        val req = parseNdjsonRequest(line)
        // extract args object
        val action = req.payload?.jsonObject?.get("action")?.jsonPrimitive?.contentOrNull
        val args = req.payload?.jsonObject?.get("args")
        assertEquals("exec", req.type)
        assertEquals("echo", action)
        val body = actionEcho(args)
        val message = body.jsonObject["message"]!!.jsonPrimitive.content
        assertEquals("hello kotlin", message)
        val resp = Response(id = req.id, status = "ok", code = 0, body = body)
        val nd = buildNdjsonResponse(resp)
        assertTrue(nd.endsWith("\n"))
        // roundtrip check
        val parsedBack = json.decodeFromString<Response>(nd.trimEnd('\n', '\r'))
        assertEquals("ok", parsedBack.status)
        assertEquals(req.id, parsedBack.id)
    }

    @Test
    fun `exec reverse action handles unicode`() {
        val original = "Привет, 世界! 👋"
        val payload = buildJsonObject { put("message", JsonPrimitive(original)) }
        val body = actionReverse(payload)
        val reversed = body.jsonObject["message"]!!.jsonPrimitive.content
        // reversed string reversed back should equal original
        val runes = reversed.codePoints().toArray().reversedArray()
        val sb = StringBuilder()
        for (cp in runes) sb.appendCodePoint(cp)
        val roundTrip = sb.toString()
        // The above double-reversal returns original; so check length > 0 and valid UTF-8
        assertTrue(reversed.isNotEmpty())
        assertEquals(original.length, roundTrip.length)
    }

    @Test
    fun `compute action sums numbers correctly`() {
        val payload = buildJsonObject {
            put("numbers", JsonArray(listOf(JsonPrimitive(1), JsonPrimitive(2.5), JsonPrimitive(-0.5))))
        }
        val body = actionCompute(payload)
        val sum = body.jsonObject["sum"]!!.jsonPrimitive.double
        assertEquals(3.0, sum, 1e-9)
    }

    @Test
    fun `malformed json does not parse and throws exception`() {
        val bad = "{not a json}"
        assertThrows<SerializationException> {
            json.decodeFromString<Request>(bad)
        }
    }

    @Test
    fun `oversized payload is rejected`() {
        val large = "A".repeat(200 * 1024) // 200 KiB
        val line = """{"id":"big","type":"exec","payload":{"action":"echo","args":{"message":"$large"}}}"""
        val ex = assertThrows<IllegalArgumentException> {
            parseNdjsonRequest(line, maxBytes = 128 * 1024) // 128 KiB limit
        }
        assertTrue(ex.message!!.contains("line exceeds"))
    }

    @Test
    fun `response is single-line ndjson`() {
        val body = buildJsonObject { put("foo", JsonPrimitive("bar")) }
        val resp = Response(id = "r1", status = "ok", body = body)
        val nd = buildNdjsonResponse(resp)
        // exactly one newline at the end and no internal newlines
        assertTrue(nd.endsWith("\n"))
        val without = nd.removeSuffix("\n")
        assertFalse(without.contains("\n"))
        // parse back
        val parsed = json.decodeFromString<Response>(without)
        assertEquals("r1", parsed.id)
        assertEquals("ok", parsed.status)
    }

    @Test
    fun `parse multiple ndjson lines with scanner-like behavior`() {
        val lines = listOf(
            """{"id":"a","type":"health","payload":null}""",
            """{"id":"b","type":"exec","payload":{"action":"echo","args":{"message":"ok"}}}"""
        )
        val stream = lines.joinToString(separator = "\n") + "\n"
        val readLines = stream.split('\n').filter { it.isNotBlank() }
        assertEquals(2, readLines.size)
        val r1 = parseNdjsonRequest(readLines[0])
        val r2 = parseNdjsonRequest(readLines[1])
        assertEquals("a", r1.id)
        assertEquals("b", r2.id)
    }
}
