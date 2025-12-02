/*
 * json.hpp - small, self-contained JSON header (nlohmann-like surface)
 *
 * Purpose:
 *   Lightweight vendor header that provides a small `nlohmann::json`-like API
 *   sufficient for the OmniFlow C++ plugins' unit/integration tests and simple
 *   runtime needs.  It supports:
 *     - objects (string->value), arrays, strings, numbers (double), booleans, null
 *     - parsing from std::string: json::parse(...)
 *     - serializing to string: json::dump()
 *     - operator[] for objects and arrays
 *     - basic type queries: is_object(), is_array(), is_string(), is_number(), is_boolean(), is_null()
 *     - getters: get<T>()
 *
 *   This header is intentionally compact and auditable. It is NOT a drop-in full
 *   replacement for the upstream nlohmann/json.hpp (which has a very large feature set).
 *   For full functionality, replace this file with upstream's single-header release.
 *
 * License: MIT
 *
 * Copyright (c) 2025 TheSkiF4er / OmniFlow (this vendor copy)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 * Usage:
 *   #include "plugins/cpp/third_party/nlohmann/json.hpp"
 *   using nlohmann::json;
 *
 *   json j = json::parse(R"({"id":"1","type":"health"})");
 *   std::string id = j["id"].get<std::string>();
 *   std::string s = j.dump();
 *
 * Note: This header targets C++17 and above.
 */

#ifndef OMNIFLOW_THIRD_PARTY_NLOHMANN_JSON_HPP
#define OMNIFLOW_THIRD_PARTY_NLOHMANN_JSON_HPP

#include <string>
#include <vector>
#include <map>
#include <variant>
#include <memory>
#include <stdexcept>
#include <sstream>
#include <cctype>
#include <cmath>
#include <limits>
#include <type_traits>

namespace nlohmann {

class json {
public:
    // underlying variant type
    using object_t = std::map<std::string, json>;
    using array_t  = std::vector<json>;
    using string_t = std::string;
    using number_t = double;
    using boolean_t = bool;
    using null_t = std::nullptr_t;
    using value_t = std::variant<null_t, boolean_t, number_t, string_t, array_t, object_t>;

private:
    value_t m_value;

public:
    // Exceptions
    struct parse_error : public std::runtime_error { parse_error(const std::string& s):std::runtime_error(s){} };
    struct type_error  : public std::runtime_error { type_error(const std::string& s):std::runtime_error(s){} };

    // Constructors
    json() noexcept : m_value(nullptr) {}
    json(std::nullptr_t) noexcept : m_value(nullptr) {}
    json(boolean_t b) noexcept : m_value(b) {}
    json(int v) noexcept : m_value(static_cast<number_t>(v)) {}
    json(long v) noexcept : m_value(static_cast<number_t>(v)) {}
    json(number_t d) noexcept : m_value(d) {}
    json(const char* s) : m_value(string_t(s ? s : "")) {}
    json(const string_t& s) : m_value(s) {}
    json(string_t&& s) : m_value(std::move(s)) {}
    json(const array_t& a) : m_value(a) {}
    json(array_t&& a) : m_value(std::move(a)) {}
    json(const object_t& o) : m_value(o) {}
    json(object_t&& o) : m_value(std::move(o)) {}

    // Factory helpers
    static json object() { return json(object_t{}); }
    static json array()  { return json(array_t{}); }

    // parse from string (static)
    static json parse(const std::string& s) {
        size_t idx = 0;
        json result = parse_internal(s, idx);
        idx = skip_ws(s, idx);
        if (idx != s.size()) throw parse_error("Extra characters after JSON value");
        return result;
    }

    // dump to string (compact)
    std::string dump() const {
        std::ostringstream oss;
        dump_internal(oss);
        return oss.str();
    }

    // pretty print (indent)
    std::string dump(int indent) const {
        if (indent <= 0) return dump();
        std::ostringstream oss;
        dump_internal(oss, indent, 0);
        return oss.str();
    }

    // type queries
    bool is_null() const noexcept { return std::holds_alternative<null_t>(m_value); }
    bool is_boolean() const noexcept { return std::holds_alternative<boolean_t>(m_value); }
    bool is_number() const noexcept { return std::holds_alternative<number_t>(m_value); }
    bool is_string() const noexcept { return std::holds_alternative<string_t>(m_value); }
    bool is_array() const noexcept { return std::holds_alternative<array_t>(m_value); }
    bool is_object() const noexcept { return std::holds_alternative<object_t>(m_value); }

    // accessors (const)
    const object_t&   get_object() const { if (!is_object()) throw type_error("not an object"); return std::get<object_t>(m_value); }
    const array_t&    get_array()  const { if (!is_array())  throw type_error("not an array");  return std::get<array_t>(m_value); }
    const string_t&   get_string() const { if (!is_string()) throw type_error("not a string");  return std::get<string_t>(m_value); }
    number_t          get_number() const { if (!is_number()) throw type_error("not a number");  return std::get<number_t>(m_value); }
    boolean_t         get_boolean() const { if (!is_boolean()) throw type_error("not a boolean"); return std::get<boolean_t>(m_value); }

    // templated get<T>
    template<typename T>
    T get() const {
        if constexpr (std::is_same_v<T, std::string>) return get_string();
        else if constexpr (std::is_same_v<T, const char*>) return get_string().c_str();
        else if constexpr (std::is_same_v<T, number_t>) return get_number();
        else if constexpr (std::is_same_v<T, int>) return static_cast<int>(get_number());
        else if constexpr (std::is_same_v<T, bool>) return get_boolean();
        else if constexpr (std::is_same_v<T, array_t>) return get_array();
        else if constexpr (std::is_same_v<T, object_t>) return get_object();
        else static_assert(sizeof(T)==0, "unsupported get<T>() type");
    }

    // operator[] for object - creates key if not exists (like nlohmann)
    json& operator[](const std::string& key) {
        if (!is_object()) {
            // convert to object if null
            if (is_null()) m_value = object_t{};
            else throw type_error("not an object (operator[])");
        }
        auto& obj = std::get<object_t>(m_value);
        return obj[key]; // default constructs json (null) if key absent
    }

    const json& operator[](const std::string& key) const {
        const auto& obj = get_object();
        auto it = obj.find(key);
        if (it == obj.end()) throw type_error("key not found in object");
        return it->second;
    }

    // operator[] for arrays
    json& operator[](size_t idx) {
        if (!is_array()) throw type_error("not an array (operator[])");
        auto& arr = std::get<array_t>(m_value);
        if (idx >= arr.size()) throw type_error("array index out of range");
        return arr[idx];
    }

    const json& operator[](size_t idx) const {
        const auto& arr = get_array();
        if (idx >= arr.size()) throw type_error("array index out of range");
        return arr[idx];
    }

    size_t size() const noexcept {
        if (is_array()) return std::get<array_t>(m_value).size();
        if (is_object()) return std::get<object_t>(m_value).size();
        return 0;
    }

    // convenience: contains
    bool contains(const std::string& key) const noexcept {
        if (!is_object()) return false;
        const auto& obj = std::get<object_t>(m_value);
        return obj.find(key) != obj.end();
    }

    // push_back for arrays
    void push_back(const json& v) {
        if (!is_array()) {
            if (is_null()) m_value = array_t{};
            else throw type_error("not an array (push_back)");
        }
        std::get<array_t>(m_value).push_back(v);
    }

    // iterators for objects/arrays (simple)
    // For brevity we expose const accessors for underlying types
    const object_t& as_object() const { return get_object(); }
    const array_t& as_array() const { return get_array(); }

private:
    // ---------- Parsing implementation (compact recursive descent) ----------
    static size_t skip_ws(const std::string& s, size_t idx) {
        while (idx < s.size() && std::isspace(static_cast<unsigned char>(s[idx]))) ++idx;
        return idx;
    }

    static json parse_internal(const std::string& s, size_t& idx) {
        idx = skip_ws(s, idx);
        if (idx >= s.size()) throw parse_error("Unexpected end of input");
        char c = s[idx];
        if (c == '{') {
            return parse_object(s, idx);
        } else if (c == '[') {
            return parse_array(s, idx);
        } else if (c == '"') {
            return json(parse_string(s, idx));
        } else if (c == 'n') {
            if (s.compare(idx, 4, "null") == 0) { idx += 4; return json(nullptr); }
            throw parse_error("Invalid token (expected null)");
        } else if (c == 't') {
            if (s.compare(idx, 4, "true") == 0) { idx += 4; return json(true); }
            throw parse_error("Invalid token (expected true)");
        } else if (c == 'f') {
            if (s.compare(idx, 5, "false") == 0) { idx += 5; return json(false); }
            throw parse_error("Invalid token (expected false)");
        } else if ( (c == '-') || (c >= '0' && c <= '9') ) {
            return parse_number(s, idx);
        } else {
            throw parse_error(std::string("Unexpected character '") + c + "'");
        }
    }

    static json parse_object(const std::string& s, size_t& idx) {
        // assumes s[idx] == '{'
        ++idx; // skip '{'
        idx = skip_ws(s, idx);
        object_t obj;
        if (idx < s.size() && s[idx] == '}') { ++idx; return json(std::move(obj)); }
        while (true) {
            idx = skip_ws(s, idx);
            if (idx >= s.size() || s[idx] != '"') throw parse_error("Expected string for object key");
            std::string key = parse_string(s, idx);
            idx = skip_ws(s, idx);
            if (idx >= s.size() || s[idx] != ':') throw parse_error("Expected ':' after object key");
            ++idx;
            json val = parse_internal(s, idx);
            obj.emplace(std::move(key), std::move(val));
            idx = skip_ws(s, idx);
            if (idx >= s.size()) throw parse_error("Unterminated object");
            if (s[idx] == ',') { ++idx; continue; }
            else if (s[idx] == '}') { ++idx; break; }
            else throw parse_error("Expected ',' or '}' in object");
        }
        return json(std::move(obj));
    }

    static json parse_array(const std::string& s, size_t& idx) {
        // assumes s[idx] == '['
        ++idx; // skip '['
        idx = skip_ws(s, idx);
        array_t arr;
        if (idx < s.size() && s[idx] == ']') { ++idx; return json(std::move(arr)); }
        while (true) {
            json v = parse_internal(s, idx);
            arr.push_back(std::move(v));
            idx = skip_ws(s, idx);
            if (idx >= s.size()) throw parse_error("Unterminated array");
            if (s[idx] == ',') { ++idx; continue; }
            else if (s[idx] == ']') { ++idx; break; }
            else throw parse_error("Expected ',' or ']' in array");
        }
        return json(std::move(arr));
    }

    static std::string parse_string(const std::string& s, size_t& idx) {
        // assumes s[idx] == '"'
        ++idx;
        std::string out;
        while (idx < s.size()) {
            char c = s[idx++];
            if (c == '"') return out;
            if (c == '\\') {
                if (idx >= s.size()) throw parse_error("Invalid escape sequence");
                char esc = s[idx++];
                switch (esc) {
                    case '"': out.push_back('"'); break;
                    case '\\': out.push_back('\\'); break;
                    case '/': out.push_back('/'); break;
                    case 'b': out.push_back('\b'); break;
                    case 'f': out.push_back('\f'); break;
                    case 'n': out.push_back('\n'); break;
                    case 'r': out.push_back('\r'); break;
                    case 't': out.push_back('\t'); break;
                    case 'u': {
                        // parse \uXXXX (basic BMP only)
                        if (idx + 4 > s.size()) throw parse_error("Invalid unicode escape");
                        unsigned int code = 0;
                        for (int i = 0; i < 4; ++i) {
                            char h = s[idx++];
                            code <<= 4;
                            if (h >= '0' && h <= '9') code |= (h - '0');
                            else if (h >= 'A' && h <= 'F') code |= (10 + h - 'A');
                            else if (h >= 'a' && h <= 'f') code |= (10 + h - 'a');
                            else throw parse_error("Invalid hex in unicode escape");
                        }
                        // encode UTF-8 for BMP
                        if (code <= 0x7F) out.push_back(static_cast<char>(code));
                        else if (code <= 0x7FF) {
                            out.push_back(static_cast<char>(0xC0 | ((code >> 6) & 0x1F)));
                            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
                        } else {
                            out.push_back(static_cast<char>(0xE0 | ((code >> 12) & 0x0F)));
                            out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
                            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
                        }
                    } break;
                    default:
                        throw parse_error("Invalid escape character");
                }
            } else {
                out.push_back(c);
            }
        }
        throw parse_error("Unterminated string");
    }

    static json parse_number(const std::string& s, size_t& idx) {
        size_t start = idx;
        if (s[idx] == '-') ++idx;
        bool has_digits = false;
        while (idx < s.size() && s[idx] >= '0' && s[idx] <= '9') { ++idx; has_digits=true; }
        if (!has_digits) throw parse_error("Invalid number");
        if (idx < s.size() && s[idx] == '.') {
            ++idx;
            if (idx >= s.size() || !(s[idx] >= '0' && s[idx] <= '9')) throw parse_error("Invalid number fraction");
            while (idx < s.size() && s[idx] >= '0' && s[idx] <= '9') ++idx;
        }
        if (idx < s.size() && (s[idx] == 'e' || s[idx] == 'E')) {
            ++idx;
            if (idx < s.size() && (s[idx] == '+' || s[idx] == '-')) ++idx;
            if (idx >= s.size() || !(s[idx] >= '0' && s[idx] <= '9')) throw parse_error("Invalid number exponent");
            while (idx < s.size() && s[idx] >= '0' && s[idx] <= '9') ++idx;
        }
        std::string token = s.substr(start, idx - start);
        // Convert to double safely
        char* endptr = nullptr;
        errno = 0;
        double val = std::strtod(token.c_str(), &endptr);
        if (endptr != token.c_str() + token.size() || errno == ERANGE) throw parse_error("Number conversion error");
        return json(val);
    }

    // ---------- Serialization ----------
    void dump_internal(std::ostream& os) const {
        if (is_null()) { os << "null"; return; }
        if (is_boolean()) { os << (get_boolean() ? "true" : "false"); return; }
        if (is_number()) {
            double v = get_number();
            if (std::isfinite(v)) {
                // choose formatting similar to common JSON libs
                std::ostringstream tmp;
                tmp.precision(std::numeric_limits<double>::digits10);
                tmp << v;
                os << tmp.str();
            } else {
                os << "null"; // non-finite numbers -> null
            }
            return;
        }
        if (is_string()) {
            os << '"';
            const auto& str = get_string();
            for (unsigned char ch : str) {
                switch (ch) {
                    case '"': os << "\\\""; break;
                    case '\\': os << "\\\\"; break;
                    case '\b': os << "\\b"; break;
                    case '\f': os << "\\f"; break;
                    case '\n': os << "\\n"; break;
                    case '\r': os << "\\r"; break;
                    case '\t': os << "\\t"; break;
                    default:
                        if (ch < 0x20) {
                            // control -> \u00XX
                            char buf[7];
                            std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                            os << buf;
                        } else os << ch;
                }
            }
            os << '"';
            return;
        }
        if (is_array()) {
            os << '[';
            const auto& arr = get_array();
            for (size_t i = 0; i < arr.size(); ++i) {
                if (i) os << ',';
                arr[i].dump_internal(os);
            }
            os << ']';
            return;
        }
        if (is_object()) {
            os << '{';
            const auto& obj = get_object();
            bool first = true;
            for (const auto& kv : obj) {
                if (!first) os << ',';
                first = false;
                // key
                os << '"';
                for (unsigned char ch : kv.first) {
                    switch (ch) {
                        case '"': os << "\\\""; break;
                        case '\\': os << "\\\\"; break;
                        case '\b': os << "\\b"; break;
                        case '\f': os << "\\f"; break;
                        case '\n': os << "\\n"; break;
                        case '\r': os << "\\r"; break;
                        case '\t': os << "\\t"; break;
                        default:
                            if (ch < 0x20) {
                                char buf[7];
                                std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                                os << buf;
                            } else os << ch;
                    }
                }
                os << "\":";
                kv.second.dump_internal(os);
            }
            os << '}';
            return;
        }
    }

    void dump_internal(std::ostream& os, int indent, int level) const {
        // Pretty-print with indent spaces per level
        if (is_object()) {
            const auto& obj = get_object();
            if (obj.empty()) { os << "{}"; return; }
            os << "{\n";
            bool first = true;
            for (const auto& kv : obj) {
                if (!first) os << ",\n";
                first = false;
                os << std::string((level+1)*indent, ' ');
                os << '"' << escape_string(kv.first) << "\": ";
                kv.second.dump_internal(os, indent, level+1);
            }
            os << '\n' << std::string(level*indent, ' ') << '}';
            return;
        } else if (is_array()) {
            const auto& arr = get_array();
            if (arr.empty()) { os << "[]"; return; }
            os << "[\n";
            for (size_t i = 0; i < arr.size(); ++i) {
                if (i) os << ",\n";
                os << std::string((level+1)*indent, ' ');
                arr[i].dump_internal(os, indent, level+1);
            }
            os << '\n' << std::string(level*indent, ' ') << ']';
            return;
        } else {
            dump_internal(os);
        }
    }

    static std::string escape_string(const std::string& s) {
        std::string out;
        out.reserve(s.size());
        for (unsigned char ch : s) {
            switch (ch) {
                case '"': out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\b': out += "\\b"; break;
                case '\f': out += "\\f"; break;
                case '\n': out += "\\n"; break;
                case '\r': out += "\\r"; break;
                case '\t': out += "\\t"; break;
                default:
                    if (ch < 0x20) {
                        char buf[7];
                        std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                        out += buf;
                    } else out.push_back((char)ch);
            }
        }
        return out;
    }
};

} // namespace nlohmann

#endif // OMNIFLOW_THIRD_PARTY_NLOHMANN_JSON_HPP
