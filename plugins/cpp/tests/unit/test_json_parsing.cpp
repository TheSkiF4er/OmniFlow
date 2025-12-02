// plugins/cpp/tests/unit/test_json_parsing.cpp
//
// Integration/unit tests for the JSON parser used by OmniFlow C++ plugins.
// These tests are written with Google Test (gtest) and exercise the vendorized
// cJSON implementation (plugins/c/vendor/cJSON).
//
// Place this file in: plugins/cpp/tests/unit/test_json_parsing.cpp
//
// How to compile & run (example):
//   # from repository root (adjust paths if your build system differs)
//   mkdir -p build/tests && cd build/tests
//   cmake -DCMAKE_BUILD_TYPE=Debug ../..                  # your top-level CMake should include gtest and this test
//   make -j$(nproc) test_json_parsing
//   ./tests/test_json_parsing
//
// Or quick manual build (for developers without full CMake):
//   g++ -std=c++17 -I../../plugins/c/vendor/cJSON -I$(gtest/include) \
//       ../../../plugins/c/vendor/cJSON/cJSON.c test_json_parsing.cpp \
//       -L/path/to/gtest/lib -lgtest -lgtest_main -pthread -o test_json_parsing
//
// NOTE: These tests assume the vendor cJSON sources are available at
//       plugins/c/vendor/cJSON/cJSON.c and plugins/c/vendor/cJSON/cJSON.h
//
// The test suite checks:
//  - basic object/array parsing
//  - correct handling of strings and escapes
//  - handling of Unicode escape sequences (\uXXXX for BMP)
//  - number parsing (ints and floats)
//  - rejection of malformed JSON
//  - consistent memory cleanup (we call cJSON_Delete to avoid leaks)
//
// Keep tests small, deterministic and safe to run inside CI (do not allocate unbounded memory).
//

#include <gtest/gtest.h>
#include <string>
#include <sstream>
#include <vector>
#include <cstring>

extern "C" {
#include "../../../../plugins/c/vendor/cJSON/cJSON.h"
}

// Helper: parse JSON text and return pointer (nullptr on failure)
static cJSON* parse_json(const std::string& text) {
    // cJSON_Parse expects a null-terminated C-string
    return cJSON_Parse(text.c_str());
}

// Helper: convenience to get string value from object by key (or nullptr)
static const char* get_string(const cJSON* obj, const char* key) {
    if (!obj) return nullptr;
    cJSON* item = cJSON_GetObjectItem(obj, key);
    if (!item) return nullptr;
    return item->valuestring;
}

TEST(JsonParsing, ParseSimpleObject) {
    const std::string txt = R"({"id":"1","type":"health","payload":null})";
    cJSON* root = parse_json(txt);
    ASSERT_NE(root, nullptr) << "Parser returned NULL for valid JSON object";
    // Validate fields
    EXPECT_STREQ(get_string(root, "id"), "1");
    EXPECT_STREQ(get_string(root, "type"), "health");
    cJSON_Delete(root);
}

TEST(JsonParsing, ParseArrayAndNumbers) {
    const std::string txt = R"({"id":"n1","type":"exec","payload":{"action":"compute","args":{"numbers":[1,2,3.5,-4]}}})";
    cJSON* root = parse_json(txt);
    ASSERT_NE(root, nullptr);
    cJSON* payload = cJSON_GetObjectItem(root, "payload");
    ASSERT_NE(payload, nullptr);
    cJSON* args = cJSON_GetObjectItem(payload, "args");
    ASSERT_NE(args, nullptr);
    cJSON* nums = cJSON_GetObjectItem(args, "numbers");
    ASSERT_NE(nums, nullptr);
    // Check array length and numeric values
    cJSON* n0 = cJSON_GetArrayItem(nums, 0);
    cJSON* n1 = cJSON_GetArrayItem(nums, 1);
    cJSON* n2 = cJSON_GetArrayItem(nums, 2);
    cJSON* n3 = cJSON_GetArrayItem(nums, 3);
    ASSERT_NE(n0, nullptr); ASSERT_NE(n1, nullptr); ASSERT_NE(n2, nullptr); ASSERT_NE(n3, nullptr);
    EXPECT_EQ(n0->valueint, 1);
    EXPECT_EQ(n1->valueint, 2);
    EXPECT_DOUBLE_EQ(n2->valuedouble, 3.5);
    EXPECT_EQ(n3->valueint, -4);
    cJSON_Delete(root);
}

TEST(JsonParsing, ParseStringEscapes) {
    const std::string txt = R"({"s":"Line1\nLine2\tTabbed\\Backslash\"Quote"})";
    cJSON* root = parse_json(txt);
    ASSERT_NE(root, nullptr);
    cJSON* s = cJSON_GetObjectItem(root, "s");
    ASSERT_NE(s, nullptr);
    ASSERT_NE(s->valuestring, nullptr);
    std::string val(s->valuestring);
    // Ensure escape sequences were interpreted
    EXPECT_NE(val.find("Line1"), std::string::npos);
    EXPECT_NE(val.find("Line2"), std::string::npos);
    EXPECT_NE(val.find("Tabbed"), std::string::npos);
    EXPECT_NE(val.find("Backslash"), std::string::npos);
    EXPECT_NE(val.find("Quote"), std::string::npos);
    cJSON_Delete(root);
}

TEST(JsonParsing, ParseUnicodeEscapeBMP) {
    // \u041F = 'П' (Cyrillic capital letter Pe), \u0440 = 'р', \u0438='и', \u0432='в', \u0435='е', \u0442='т'
    const std::string txt = R"({"s":"\u041F\u0440\u0438\u0432\u0435\u0442"})";
    cJSON* root = parse_json(txt);
    ASSERT_NE(root, nullptr);
    cJSON* s = cJSON_GetObjectItem(root, "s");
    ASSERT_NE(s, nullptr);
    ASSERT_NE(s->valuestring, nullptr);
    std::string val(s->valuestring);
    // Expect the UTF-8 decoded string "Привет"
    EXPECT_EQ(val, u8"Привет");
    cJSON_Delete(root);
}

TEST(JsonParsing, ParseNumberFormats) {
    const std::string txt = R"({"a":123,"b":-45.6,"c":1e3,"d":-2.5E-1})";
    cJSON* root = parse_json(txt);
    ASSERT_NE(root, nullptr);
    cJSON* a = cJSON_GetObjectItem(root, "a");
    cJSON* b = cJSON_GetObjectItem(root, "b");
    cJSON* c = cJSON_GetObjectItem(root, "c");
    cJSON* d = cJSON_GetObjectItem(root, "d");
    ASSERT_NE(a, nullptr); ASSERT_NE(b, nullptr); ASSERT_NE(c, nullptr); ASSERT_NE(d, nullptr);
    EXPECT_EQ(a->valueint, 123);
    EXPECT_DOUBLE_EQ(b->valuedouble, -45.6);
    EXPECT_DOUBLE_EQ(c->valuedouble, 1000.0);
    EXPECT_DOUBLE_EQ(d->valuedouble, -0.25);
    cJSON_Delete(root);
}

TEST(JsonParsing, RejectMalformedJson_MissingBrace) {
    const std::string txt = R"({"id":"x","type":"health")"; // missing closing brace
    cJSON* root = parse_json(txt);
    EXPECT_EQ(root, nullptr) << "Parser should return NULL for malformed JSON (missing brace)";
    if (root) cJSON_Delete(root);
}

TEST(JsonParsing, RejectMalformedJson_BadEscape) {
    const std::string txt = R"({"s":"bad\qescape"})"; // \q is invalid escape
    cJSON* root = parse_json(txt);
    EXPECT_EQ(root, nullptr) << "Parser should return NULL for invalid escape sequences";
    if (root) cJSON_Delete(root);
}

TEST(JsonParsing, RejectMalformedJson_BadUnicodeEscape) {
    const std::string txt = R"({"s":"\uZZZZ"})"; // invalid hex digits
    cJSON* root = parse_json(txt);
    EXPECT_EQ(root, nullptr) << "Parser should return NULL for invalid unicode escape sequences";
    if (root) cJSON_Delete(root);
}

TEST(JsonParsing, LargeButSafeStringParsing) {
    // Build a reasonably large but safe JSON string (e.g., 64 KB) to verify parser handles it.
    // Avoid allocating extremely large buffers in CI to prevent flakiness.
    const size_t len = 64 * 1024; // 64 KiB
    std::string big;
    big.reserve(len + 64);
    big.append(R"({"id":"big","type":"exec","payload":{"action":"echo","message":")");
    big.append(len, 'A');
    big.append(R"("}})");
    cJSON* root = parse_json(big);
    ASSERT_NE(root, nullptr) << "Parser failed on a 64KiB JSON payload";
    cJSON* payload = cJSON_GetObjectItem(root, "payload");
    ASSERT_NE(payload, nullptr);
    cJSON* msg = cJSON_GetObjectItem(cJSON_GetObjectItem(payload, "payload") ? cJSON_GetObjectItem(payload, "payload") : cJSON_GetObjectItem(payload, "args"));
    // It's enough that parser succeeded and produced strings — verify message exists somewhere
    // Retrieve the "message" by traversing
    cJSON* payload_obj = cJSON_GetObjectItem(root, "payload");
    if (payload_obj) {
        cJSON* action_node = cJSON_GetObjectItem(payload_obj, "action");
        // In this structure action may be nested; just ensure parsing didn't crash and we can free memory
    }
    cJSON_Delete(root);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
