// The settings store, the C twin (moy_settings.h has the contract).
//
// The rows are an array of {one allocation holding key then text, lengths}
// that grows by doubling. Lookup is a linear scan: system.json has dozens of
// keys, and the scan touches the lengths first.

#include <string.h>

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
};

#define MAX_LEN 0x7fffffffu

// -- the scanner -------------------------------------------------------------

static const char *skip_ws(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) {
        p++;
    }
    return p;
}

static int hex_val(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

// The code unit of the \uXXXX escape at `p` (which points at the 'u'), or -1.
static int32_t u_escape(const char *p, const char *end) {
    if (end - p < 5) {
        return -1;
    }
    int32_t v = 0;
    for (int i = 1; i <= 4; i++) {
        int d = hex_val(p[i]);
        if (d < 0) {
            return -1;
        }
        v = v * 16 + d;
    }
    return v;
}

// A string starting at its opening quote: the pointer past the closing one, or
// NULL. With `key` the string is decoded into `out` (when not NULL) and its
// decoded length goes to `*n`; a lone surrogate is refused.
static const char *scan_string(const char *p, const char *end, int key,
                               char *out, size_t *n) {
    size_t w = 0;
    p++;
    for (;;) {
        if (p >= end) {
            return NULL;
        }
        unsigned char c = (unsigned char)*p;
        if (c == '"') {
            if (n != NULL) {
                *n = w;
            }
            return p + 1;
        }
        if (c < 0x20) {
            return NULL;
        }
        if (c != '\\') {
            if (out != NULL) {
                out[w] = (char)c;
            }
            w++;
            p++;
            continue;
        }
        if (++p >= end) {
            return NULL;
        }
        char e = *p;
        char lit = 0;
        switch (e) {
            case '"': lit = '"'; break;
            case '\\': lit = '\\'; break;
            case '/': lit = '/'; break;
            case 'b': lit = '\b'; break;
            case 'f': lit = '\f'; break;
            case 'n': lit = '\n'; break;
            case 'r': lit = '\r'; break;
            case 't': lit = '\t'; break;
            case 'u': break;
            default: return NULL;
        }
        if (e != 'u') {
            if (out != NULL) {
                out[w] = lit;
            }
            w++;
            p++;
            continue;
        }
        int32_t u = u_escape(p, end);
        if (u < 0) {
            return NULL;
        }
        p += 5;
        uint32_t cp = (uint32_t)u;
        if (key) {
            if (cp >= 0xdc00u && cp <= 0xdfffu) {
                return NULL;
            }
            if (cp >= 0xd800u && cp <= 0xdbffu) {
                int32_t lo;
                if (end - p < 2 || p[0] != '\\' || p[1] != 'u'
                    || (lo = u_escape(p + 1, end)) < 0
                    || lo < 0xdc00 || lo > 0xdfff) {
                    return NULL;
                }
                p += 6;
                cp = 0x10000u + ((cp - 0xd800u) << 10) + ((uint32_t)lo - 0xdc00u);
            }
            // UTF-8 for cp
            char b[4];
            size_t k;
            if (cp < 0x80u) {
                b[0] = (char)cp;
                k = 1;
            } else if (cp < 0x800u) {
                b[0] = (char)(0xc0u | (cp >> 6));
                b[1] = (char)(0x80u | (cp & 0x3fu));
                k = 2;
            } else if (cp < 0x10000u) {
                b[0] = (char)(0xe0u | (cp >> 12));
                b[1] = (char)(0x80u | ((cp >> 6) & 0x3fu));
                b[2] = (char)(0x80u | (cp & 0x3fu));
                k = 3;
            } else {
                b[0] = (char)(0xf0u | (cp >> 18));
                b[1] = (char)(0x80u | ((cp >> 12) & 0x3fu));
                b[2] = (char)(0x80u | ((cp >> 6) & 0x3fu));
                b[3] = (char)(0x80u | (cp & 0x3fu));
                k = 4;
            }
            if (out != NULL) {
                memcpy(out + w, b, k);
            }
            w += k;
        }
    }
}

static int is_digit(char c) {
    return c >= '0' && c <= '9';
}

static const char *scan_number(const char *p, const char *end) {
    if (p < end && *p == '-') {
        p++;
    }
    if (p >= end || !is_digit(*p)) {
        return NULL;
    }
    if (*p == '0') {
        p++;
    } else {
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    if (p < end && *p == '.') {
        p++;
        if (p >= end || !is_digit(*p)) {
            return NULL;
        }
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    if (p < end && (*p == 'e' || *p == 'E')) {
        p++;
        if (p < end && (*p == '+' || *p == '-')) {
            p++;
        }
        if (p >= end || !is_digit(*p)) {
            return NULL;
        }
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    return p;
}

static const char *literal(const char *p, const char *end, const char *word) {
    size_t n = strlen(word);
    if ((size_t)(end - p) < n || memcmp(p, word, n) != 0) {
        return NULL;
    }
    return p + n;
}

// One value at `p` (whitespace already skipped), `depth` containers deep: the
// pointer past it, or NULL.
static const char *scan_value(const char *p, const char *end, uint32_t depth) {
    if (p >= end) {
        return NULL;
    }
    switch (*p) {
        case '"':
            return scan_string(p, end, 0, NULL, NULL);
        case '{':
        case '[': {
            if (depth >= MOY_SETTINGS_DEPTH) {
                return NULL;
            }
            char close = *p == '{' ? '}' : ']';
            int obj = *p == '{';
            p = skip_ws(p + 1, end);
            if (p < end && *p == close) {
                return p + 1;
            }
            for (;;) {
                if (obj) {
                    if (p >= end || *p != '"') {
                        return NULL;
                    }
                    p = scan_string(p, end, 0, NULL, NULL);
                    if (p == NULL) {
                        return NULL;
                    }
                    p = skip_ws(p, end);
                    if (p >= end || *p != ':') {
                        return NULL;
                    }
                    p = skip_ws(p + 1, end);
                }
                p = scan_value(p, end, depth + 1u);
                if (p == NULL) {
                    return NULL;
                }
                p = skip_ws(p, end);
                if (p < end && *p == ',') {
                    p = skip_ws(p + 1, end);
                    continue;
                }
                if (p < end && *p == close) {
                    return p + 1;
                }
                return NULL;
            }
        }
        case 't':
            return literal(p, end, "true");
        case 'f':
            return literal(p, end, "false");
        case 'n':
            return literal(p, end, "null");
        case 'N':
            return literal(p, end, "NaN");
        case 'I':
            return literal(p, end, "Infinity");
        case '-':
            if (end - p >= 2 && p[1] == 'I') {
                return literal(p, end, "-Infinity");
            }
            return scan_number(p, end);
        default:
            return scan_number(p, end);
    }
}

int moy_settings_validate(const char *text, size_t len) {
    const char *end = text + len;
    const char *p = scan_value(skip_ws(text, end), end, 1u);
    return p != NULL && skip_ws(p, end) == end ? MOY_SETTINGS_OK
                                               : MOY_SETTINGS_BADJSON;
}

// The members of the object in `text`, in order, each as the spans of its key
// string (quote to quote) and of its value: `visit` for each, stopping at its
// first non-zero. BADJSON for anything that is not exactly one object.
typedef int (*visit_t)(void *ctx, const char *key, const char *key_end,
                       const char *val, const char *val_end);

static int walk_object(const char *text, size_t len, visit_t visit, void *ctx) {
    const char *end = text + len;
    const char *p = skip_ws(text, end);
    if (p >= end || *p != '{') {
        return MOY_SETTINGS_BADJSON;
    }
    p = skip_ws(p + 1, end);
    if (p < end && *p == '}') {
        return skip_ws(p + 1, end) == end ? MOY_SETTINGS_OK
                                          : MOY_SETTINGS_BADJSON;
    }
    for (;;) {
        if (p >= end || *p != '"') {
            return MOY_SETTINGS_BADJSON;
        }
        const char *k = p;
        size_t kn;
        p = scan_string(p, end, 1, NULL, &kn);
        if (p == NULL || kn == 0u) {
            return MOY_SETTINGS_BADJSON;
        }
        const char *k_end = p;
        p = skip_ws(p, end);
        if (p >= end || *p != ':') {
            return MOY_SETTINGS_BADJSON;
        }
        p = skip_ws(p + 1, end);
        const char *v = p;
        p = scan_value(p, end, 1u);
        if (p == NULL) {
            return MOY_SETTINGS_BADJSON;
        }
        int rc = visit(ctx, k, k_end, v, p);
        if (rc != 0) {
            return rc;
        }
        p = skip_ws(p, end);
        if (p < end && *p == ',') {
            p = skip_ws(p + 1, end);
            continue;
        }
        if (p < end && *p == '}') {
            return skip_ws(p + 1, end) == end ? MOY_SETTINGS_OK
                                              : MOY_SETTINGS_BADJSON;
        }
        return MOY_SETTINGS_BADJSON;
    }
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
    scan_string(key, key_end, 1, NULL, &kn);
    size_t jn = (size_t)(val_end - val);
    char *buf = make_buf(l->into, kn, jn);
    if (buf == NULL) {
        return MOY_SETTINGS_NOMEM;
    }
    scan_string(key, key_end, 1, buf, &kn);
    memcpy(buf + kn, val, jn);
    l->seen++;
    return put_row(l->into, buf, (uint32_t)kn, (uint32_t)jn);
}

int moy_settings_load(moy_settings_t *s, const char *text, size_t len,
                      uint32_t *rows) {
    moy_settings_t tmp = { s->mem, NULL, 0u, 0u };
    load_t l = { 0, &tmp, 0u };
    int rc = walk_object(text, len, load_row, &l);
    if (rc != MOY_SETTINGS_OK) {
        clear_rows(&tmp);
        return rc;
    }
    clear_rows(s);
    s->rows = tmp.rows;
    s->count = tmp.count;
    s->cap = tmp.cap;
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
    return put_row(s, buf, (uint32_t)key_len, (uint32_t)json_len);
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
    return 1;
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
