# cJSON (vendorized) — OmniFlow/plugins/c/vendor/cJSON

**Summary:** a lightweight, reliable, vendor-scoped version of the *cJSON* library (subset), prepared specifically for the **OmniFlow** project.
Files are located in `plugins/c/vendor/cJSON/` and include `cJSON.c`, `cJSON.h`, and this `README.md`.

This package provides a secure JSON parser and serializer in the cJSON style, adapted for embedding into OmniFlow C plugins.

---

## Directory Structure

```
plugins/c/vendor/cJSON/
├── cJSON.c         # Core parsing/printing implementation
├── cJSON.h         # Public header (API)
└── README.md       # This file
```

---

## License & Attribution

* The library is based on the original **cJSON** project (author: Dave Gamble) and is distributed under the **MIT License**.
* Several modifications and simplifications were applied (subset API, memory hooks, security hardening) for integration into OmniFlow.
* Original copyright notices and attribution are preserved inside `cJSON.c`.

> **Important:** If you distribute binaries/packages containing this source code, include a copy of the MIT license and an attribution to the original project.

---

## Features of This Vendor Version

* **Minimalistic:** Only a safe and validated subset of functionality is included — just enough for JSON-over-stdin/stdout communication between OmniFlow and plugins. No unnecessary dependencies.
* **Memory hooks:** `cJSON_InitHooks` allows overriding `malloc/free` (for memory tracking or custom allocators).
* **Strict error handling:** Parsing functions return `NULL` on any malformed input. Escape/unicode handling is intentionally conservative (`\uXXXX`, BMP only).
* **Security-focused:** Comments inside `cJSON.c` describe recommendations such as input-size limits, ASan usage, and safe parsing patterns.

---

## API Overview (see `cJSON.h`)

Main public functions:

* `void cJSON_InitHooks(cJSON_Hooks* hooks);` — install/reset custom alloc/free handlers.
* `cJSON* cJSON_Parse(const char* json);` — parse a JSON string into a tree.
* `cJSON* cJSON_ParseWithOpts(const char* json, const char** return_parse_end, int require_null_terminated);`
* `char* cJSON_PrintUnformatted(const cJSON* item);` — serialize object (caller frees with `cJSON_free`).
* `char* cJSON_Print(const cJSON* item);` — same but formatted.
* `void cJSON_Delete(cJSON* item);` — delete the entire cJSON tree.
* `cJSON* cJSON_GetObjectItem(const cJSON* object, const char* name);`
* `cJSON* cJSON_GetArrayItem(const cJSON* array, int index);`
* Constructors & mutators:
  `cJSON_CreateString`, `cJSON_CreateNumber`, `cJSON_CreateObject`,
  `cJSON_CreateArray`, `cJSON_AddItemToObject`, `cJSON_AddItemToArray`.

Utility functions:
`cJSON_strdup`, `cJSON_malloc`, `cJSON_free`.

---

## Usage Examples

### Example 1 — Parsing a message

```c
#include "cJSON.h"

const char* txt = "{\"id\":\"1\", \"type\":\"health\"}";
cJSON* root = cJSON_Parse(txt);
if (!root) {
    // handle error
}

cJSON* type = cJSON_GetObjectItem(root, "type");
if (type && (type->type & cJSON_String)) {
    printf("type = %s\n", type->valuestring);
}

cJSON_Delete(root);
```

### Example 2 — Creating a response (serialization)

```c
cJSON* resp = cJSON_CreateObject();
cJSON_AddItemToObject(resp, "id", cJSON_CreateString("1"));
cJSON_AddItemToObject(resp, "status", cJSON_CreateString("ok"));

char* out = cJSON_PrintUnformatted(resp);
printf("%s\n", out); // send to stdout

cJSON_free(out);
cJSON_Delete(resp);
```

---

## Build & Testing

Typically, the global `plugins/c/Makefile` is configured to include this vendor directory automatically.

Manual compile example:

```bash
gcc -std=c11 -O2 -Wall -Wextra -Iplugins/c/vendor/cJSON -o plugins/c/sample_plugin \
    plugins/c/sample_plugin.c plugins/c/vendor/cJSON/cJSON.c
```

Recommended CI setup:

* Build with `-O2 -Wall -Wextra`
* Run tests under **AddressSanitizer** and/or **Valgrind**:

  ```bash
  gcc -fsanitize=address -g -O1 ...
  ```
* Run integration tests:
  `plugins/c/tests/test_sample_plugin.sh`

---

## Security Notes & Limitations

* **Input size limits:** Before calling `cJSON_Parse`, ensure JSON input is size-bounded (OmniFlow plugins enforce this). Prevents memory-exhaustion attacks.
* **Unicode:** Supports BMP (`\uXXXX`). Surrogate pairs (>U+FFFF) are not handled in this lightweight vendor build.
* **Strict failure mode:** Parser returns `NULL` instead of attempting recovery — safer for untrusted inputs.
* **Allocator control:** Use `cJSON_InitHooks` to plug a safe/custom allocator.

---

## Testing & CI

* Include `plugins/c/vendor/cJSON` in all unit and integration tests.
* Run ASan/UBSan in CI pipelines.
* Add malformed/big/fuzzed JSON test cases.

---

## Updating / Syncing With Upstream

To pull updates from the official cJSON project:

1. Download an upstream release.
2. Copy only the needed files (`cJSON.c`, `cJSON.h`) and re-apply OmniFlow-specific adjustments.
3. Update the attribution comment and include a note in your commit message and README.

---

## Support & Contributions

If you want to enhance performance, add full Unicode support, or implement streaming parsing — create an issue or PR in:
**TheSkiF4er/OmniFlow**, label: `area:plugins/c`.
