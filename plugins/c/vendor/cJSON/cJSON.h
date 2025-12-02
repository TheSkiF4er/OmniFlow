/* cJSON.h - public header for vendorized cJSON used by OmniFlow plugins
 *
 * Lightweight JSON parsing and printing library (vendorized subset).
 * License: MIT (see cJSON.c for full notice and modifications)
 *
 * This header pairs with cJSON.c provided in the vendor directory. It exposes a
 * small, safe subset of the original cJSON API, adapted for use inside the
 * OmniFlow project. The implementation uses allocation hooks which callers may
 * override to enforce memory limits.
 */

#ifndef OMNIFLOW_CJSON_H
#define OMNIFLOW_CJSON_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stddef.h>

/* Visibility/ABI helpers */
#ifndef CJSON_PUBLIC
  #if defined(_WIN32) && defined(CJSON_BUILD_DLL)
    #define CJSON_PUBLIC __declspec(dllexport)
  #elif defined(_WIN32) && defined(CJSON_USE_DLL)
    #define CJSON_PUBLIC __declspec(dllimport)
  #elif defined(__GNUC__) && __GNUC__ >= 4
    #define CJSON_PUBLIC __attribute__((visibility("default")))
  #else
    #define CJSON_PUBLIC
  #endif
#endif

/* Type flags (low byte) */
#define cJSON_False  (1 << 0)
#define cJSON_True   (1 << 1)
#define cJSON_NULL   (1 << 2)
#define cJSON_Number (1 << 3)
#define cJSON_String (1 << 4)
#define cJSON_Array  (1 << 5)
#define cJSON_Object (1 << 6)

typedef struct cJSON {
    struct cJSON *next;
    struct cJSON *prev;
    struct cJSON *child;

    int type;               /* cJSON_False | cJSON_True | cJSON_NULL | cJSON_Number | cJSON_String | cJSON_Array | cJSON_Object */

    char *valuestring;      /* for strings */
    int valueint;           /* for integers */
    double valuedouble;     /* for doubles */

    char *string;           /* the item's name (if this item is the child of, or is in, an object) */
} cJSON;

/* Allocation hooks to allow host to control memory (optional). */
typedef struct cJSON_Hooks {
    void *(*malloc_fn)(size_t sz);
    void (*free_fn)(void *ptr);
} cJSON_Hooks;

/* Initialize optional memory hooks. Pass NULL to reset to defaults. */
CJSON_PUBLIC void cJSON_InitHooks(cJSON_Hooks* hooks);

/* Parse JSON text to a cJSON structure. Returns NULL on parse error. */
CJSON_PUBLIC cJSON* cJSON_Parse(const char* value);

/* Variant: parse with options (simple wrapper in this vendorized impl). */
CJSON_PUBLIC cJSON* cJSON_ParseWithOpts(const char* value, const char** return_parse_end, int require_null_terminated);

/* Render a cJSON entity to text (allocated string). Caller must free with cJSON_free or use provided cJSON_Delete on created nodes - but free() is used for strings produced. */
CJSON_PUBLIC char* cJSON_Print(const cJSON* item);
CJSON_PUBLIC char* cJSON_PrintUnformatted(const cJSON* item);

/* Delete a cJSON structure (recursive). Uses cJSON_free internally. */
CJSON_PUBLIC void cJSON_Delete(cJSON* item);

/* Getters */
CJSON_PUBLIC cJSON* cJSON_GetObjectItem(const cJSON* object, const char* name);
CJSON_PUBLIC cJSON* cJSON_GetArrayItem(const cJSON* array, int index);

/* Creation helpers */
CJSON_PUBLIC cJSON* cJSON_CreateString(const char* s);
CJSON_PUBLIC cJSON* cJSON_CreateNumber(double num);
CJSON_PUBLIC cJSON* cJSON_CreateObject(void);
CJSON_PUBLIC cJSON* cJSON_CreateArray(void);

/* Add item to object / array (takes ownership of item and copies the key for objects) */
CJSON_PUBLIC void cJSON_AddItemToObject(cJSON* object, const char* string, cJSON* item);
CJSON_PUBLIC void cJSON_AddItemToArray(cJSON* array, cJSON* item);

/* Convenience: duplicates a string using the library allocator (may be useful for extensions) */
CJSON_PUBLIC char* cJSON_strdup(const char* str);

/* Internal allocators (exposed for advanced users): implementations may map to malloc/free or to hooks set via cJSON_InitHooks. Use with care. */
CJSON_PUBLIC void* cJSON_malloc(size_t size);
CJSON_PUBLIC void cJSON_free(void* ptr);

#ifdef __cplusplus
}
#endif

#endif /* OMNIFLOW_CJSON_H */
