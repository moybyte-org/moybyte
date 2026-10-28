// The settings store, the C twin (moy_settings.h has the contract).
//
// The rows are an array of {one allocation holding key then text, lengths}
// that grows by doubling. Lookup is a linear scan: system.json has dozens of
// keys, and the scan touches the lengths first.

#include <string.h>

#include "moy_json.h"
#include "moy_settings.h"

typedef struct {
    char *buf;              // key_len bytes of key, then json_len of text
    uint32_t key_len;
    uint32_t json_len;
} row_t;

struct moy_settings {
    const moy_htab_mem_t *mem;
    row_t *rows;
    uint32_t count;
    uint32_t cap;
    uint32_t dirty;         // changes since the store was last clean
    moy_settings_save_fn save;  // what writes the file, or NULL
    void *save_ctx;
};

#define MAX_LEN 0x7fffffffu

// The scanner is moy_json's: a row holds one value, a file one object.

int moy_settings_validate(const char *text, size_t len) {
    return moy_json_valid(text, len) == MOY_JSON_OK ? MOY_SETTINGS_OK
                                                    : MOY_SETTINGS_BADJSON;
}

// -- the rows ----------------------------------------------------------------

moy_settings_t *moy_settings_new(const moy_htab_mem_t *mem) {
    moy_settings_t *s = mem->alloc(sizeof(moy_settings_t));
    if (s != NULL) {
        s->mem = mem;
    }
    return s;
}

static void clear_rows(moy_settings_t *s) {
    for (uint32_t i = 0; i < s->count; i++) {
        s->mem->release(s->rows[i].buf,
                        (size_t)s->rows[i].key_len + s->rows[i].json_len);
    }
    if (s->rows != NULL) {
        s->mem->release(s->rows, (size_t)s->cap * sizeof(row_t));
    }
    s->rows = NULL;
    s->count = s->cap = 0u;
}

void moy_settings_saver(moy_settings_t *s, moy_settings_save_fn fn, void *ctx) {
    s->save = fn;
    s->save_ctx = ctx;
}

void *moy_settings_saver_ctx(const moy_settings_t *s) {
    return s->save_ctx;
}

int moy_settings_flush(moy_settings_t *s) {
    if (s->dirty == 0u) {
        return 1;
    }
    if (s->save == NULL) {
        return 0;
    }
    size_t n = moy_settings_dump(s, NULL, 0);
    char *text = s->mem->alloc(n + 1u);
    if (text == NULL) {
        return 0;
    }
    moy_settings_dump(s, text, n);
    int landed = s->save(s->save_ctx, text, n);
    s->mem->release(text, n + 1u);
    if (!landed) {
        return 0;
    }
    s->dirty = 0u;
    return 1;
}

void moy_settings_free(moy_settings_t *s) {
    if (s == NULL) {
        return;
    }
    clear_rows(s);
    s->mem->release(s, sizeof(moy_settings_t));
}

static int find_row(const moy_settings_t *s, const char *key, size_t n) {
    for (uint32_t i = 0; i < s->count; i++) {
        if (s->rows[i].key_len == n && memcmp(s->rows[i].buf, key, n) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static int reserve_row(moy_settings_t *s) {
    if (s->count < s->cap) {
        return MOY_SETTINGS_OK;
    }
    uint32_t cap = s->cap ? s->cap * 2u : 8u;
    row_t *rows = s->mem->alloc((size_t)cap * sizeof(row_t));
    if (rows == NULL) {
        return MOY_SETTINGS_NOMEM;
    }
    if (s->count) {
        memcpy(rows, s->rows, (size_t)s->count * sizeof(row_t));
    }
    if (s->rows != NULL) {
        s->mem->release(s->rows, (size_t)s->cap * sizeof(row_t));
    }
    s->rows = rows;
    s->cap = cap;
    return MOY_SETTINGS_OK;
}

// Take `buf` (key_len + json_len bytes, made by the caller) as the row of its
// key: in place when the key has one, last otherwise.
static int put_row(moy_settings_t *s, char *buf, uint32_t key_len,
                   uint32_t json_len) {
    int at = find_row(s, buf, key_len);
    if (at < 0) {
        if (reserve_row(s) != MOY_SETTINGS_OK) {
            s->mem->release(buf, (size_t)key_len + json_len);
            return MOY_SETTINGS_NOMEM;
        }
        at = (int)s->count++;
    } else {
        s->mem->release(s->rows[at].buf,
                        (size_t)s->rows[at].key_len + s->rows[at].json_len);
    }
    s->rows[at].buf = buf;
    s->rows[at].key_len = key_len;
    s->rows[at].json_len = json_len;
    return MOY_SETTINGS_OK;
}

static char *make_buf(const moy_settings_t *s, size_t key_len,
                      size_t json_len) {
    if (key_len > MAX_LEN || json_len > MAX_LEN
        || key_len + json_len > MAX_LEN) {
        return NULL;
    }
    return s->mem->alloc(key_len + json_len ? key_len + json_len : 1u);
}

typedef struct {
    int rc;
    moy_settings_t *into;
    uint32_t seen;
} load_t;

static int load_row(void *ctx, const char *key, const char *key_end,
                    const char *val, const char *val_end) {
    load_t *l = ctx;
    size_t kn;
    if (moy_json_string(key, key_end, 1, NULL, &kn) == NULL || kn == 0u) {
        return MOY_SETTINGS_BADJSON;    // a lone surrogate, or an empty key
    }
    size_t jn = (size_t)(val_end - val);
    char *buf = make_buf(l->into, kn, jn);
    if (buf == NULL) {
        return MOY_SETTINGS_NOMEM;
    }
    moy_json_string(key, key_end, 1, buf, &kn);
    memcpy(buf + kn, val, jn);
    l->seen++;
    return put_row(l->into, buf, (uint32_t)kn, (uint32_t)jn);
}

int moy_settings_load(moy_settings_t *s, const char *text, size_t len,
                      uint32_t *rows) {
    moy_settings_t tmp = { s->mem, NULL, 0u, 0u, 0u, NULL, NULL };
    load_t l = { 0, &tmp, 0u };
    int rc = moy_json_object(text, len, load_row, &l);
    if (rc != MOY_SETTINGS_OK) {
        clear_rows(&tmp);
        return rc;
    }
    clear_rows(s);
    s->rows = tmp.rows;
    s->count = tmp.count;
    s->cap = tmp.cap;
    s->dirty = 0u;
    *rows = s->count;
    return MOY_SETTINGS_OK;
}

int moy_settings_get(const moy_settings_t *s, const char *key, size_t key_len,
                     const char **json, size_t *json_len) {
    int at = find_row(s, key, key_len);
    if (at < 0) {
        return 0;
    }
    *json = s->rows[at].buf + s->rows[at].key_len;
    *json_len = s->rows[at].json_len;
    return 1;
}

int moy_settings_set(moy_settings_t *s, const char *key, size_t key_len,
                     const char *json, size_t json_len) {
    if (key_len == 0u) {
        return MOY_SETTINGS_BADKEY;
    }
    if (moy_settings_validate(json, json_len) != MOY_SETTINGS_OK) {
        return MOY_SETTINGS_BADJSON;
    }
    char *buf = make_buf(s, key_len, json_len);
    if (buf == NULL) {
        return MOY_SETTINGS_NOMEM;
    }
    memcpy(buf, key, key_len);
    memcpy(buf + key_len, json, json_len);
    int rc = put_row(s, buf, (uint32_t)key_len, (uint32_t)json_len);
    if (rc == MOY_SETTINGS_OK) {
        s->dirty++;
    }
    return rc;
}

int moy_settings_delete(moy_settings_t *s, const char *key, size_t key_len) {
    int at = find_row(s, key, key_len);
    if (at < 0) {
        return 0;
    }
    s->mem->release(s->rows[at].buf,
                    (size_t)s->rows[at].key_len + s->rows[at].json_len);
    memmove(&s->rows[at], &s->rows[at + 1],
            (size_t)(s->count - (uint32_t)at - 1u) * sizeof(row_t));
    s->count--;
    memset(&s->rows[s->count], 0, sizeof(row_t));
    s->dirty++;
    return 1;
}

uint32_t moy_settings_dirty(const moy_settings_t *s) {
    return s->dirty;
}

void moy_settings_clean(moy_settings_t *s) {
    s->dirty = 0u;
}

uint32_t moy_settings_count(const moy_settings_t *s) {
    return s->count;
}

int moy_settings_at(const moy_settings_t *s, uint32_t i, const char **key,
                    size_t *key_len, const char **json, size_t *json_len) {
    if (i >= s->count) {
        return 0;
    }
    *key = s->rows[i].buf;
    *key_len = s->rows[i].key_len;
    *json = s->rows[i].buf + s->rows[i].key_len;
    *json_len = s->rows[i].json_len;
    return 1;
}

// -- the file ----------------------------------------------------------------

typedef struct {
    char *out;
    size_t cap, n;
} sink_t;

static void put(sink_t *k, const char *p, size_t n) {
    if (k->n < k->cap) {
        size_t room = k->cap - k->n;
        memcpy(k->out + k->n, p, n < room ? n : room);
    }
    k->n += n;
}

static void put_key(sink_t *k, const char *p, size_t n) {
    static const char hex[] = "0123456789abcdef";
    put(k, "\"", 1);
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        const char *esc = NULL;
        switch (c) {
            case '"': esc = "\\\""; break;
            case '\\': esc = "\\\\"; break;
            case '\b': esc = "\\b"; break;
            case '\f': esc = "\\f"; break;
            case '\n': esc = "\\n"; break;
            case '\r': esc = "\\r"; break;
            case '\t': esc = "\\t"; break;
            default: break;
        }
        if (esc != NULL) {
            put(k, esc, 2);
        } else if (c < 0x20u) {
            char u[6] = { '\\', 'u', '0', '0', hex[c >> 4], hex[c & 15u] };
            put(k, u, 6);
        } else {
            put(k, p + i, 1);
        }
    }
    put(k, "\"", 1);
}

size_t moy_settings_dump(const moy_settings_t *s, char *out, size_t cap) {
    sink_t k = { out, cap, 0u };
    put(&k, "{", 1);
    for (uint32_t i = 0; i < s->count; i++) {
        if (i) {
            put(&k, ", ", 2);
        }
        put_key(&k, s->rows[i].buf, s->rows[i].key_len);
        put(&k, ": ", 2);
        put(&k, s->rows[i].buf + s->rows[i].key_len, s->rows[i].json_len);
    }
    put(&k, "}", 1);
    return k.n;
}
