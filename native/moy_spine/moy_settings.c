// The settings store, the C twin (moy_settings.h has the contract).
//
// The rows are an array of {one allocation holding key then text, lengths}
// that grows by doubling. Lookup is a linear scan: system/settings has dozens of
// keys, and the scan touches the lengths first.

// The lock's clock and recursive mutex are POSIX's, which a strict C99
// build on Linux leaves out unless asked.
#if defined(__linux__) && !defined(_GNU_SOURCE) && !defined(_XOPEN_SOURCE)
#define _XOPEN_SOURCE 700
#endif

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
    uint32_t gen;           // changes since the store was made (moy_settings_gen)
};

#define MAX_LEN 0x7fffffffu

// -- the lock (moy_settings.h, THE SETTINGS LOCK) -----------------------------

#if defined(__has_include) && __has_include("esp_heap_caps.h")
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static StaticSemaphore_t s_mu_buf;
static SemaphoreHandle_t s_mu;

void moy_settings_lock(void) {
    if (s_mu == NULL) {
        portENTER_CRITICAL(&s_mux);
        if (s_mu == NULL) {
            s_mu = xSemaphoreCreateRecursiveMutexStatic(&s_mu_buf);
        }
        portEXIT_CRITICAL(&s_mux);
    }
    xSemaphoreTakeRecursive(s_mu, portMAX_DELAY);
}

void moy_settings_unlock(void) {
    xSemaphoreGiveRecursive(s_mu);
}
#elif !defined(__EMSCRIPTEN__) || defined(__EMSCRIPTEN_PTHREADS__)
#include <pthread.h>

static pthread_mutex_t s_mu;
static pthread_once_t s_once = PTHREAD_ONCE_INIT;

static void mu_init(void) {
    pthread_mutexattr_t at;
    pthread_mutexattr_init(&at);
    pthread_mutexattr_settype(&at, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&s_mu, &at);
    pthread_mutexattr_destroy(&at);
}

void moy_settings_lock(void) {
    pthread_once(&s_once, mu_init);
    pthread_mutex_lock(&s_mu);
}

void moy_settings_unlock(void) {
    pthread_mutex_unlock(&s_mu);
}
#else
void moy_settings_lock(void) {
}

void moy_settings_unlock(void) {
}
#endif

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

static int flush(moy_settings_t *s) {
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

int moy_settings_flush(moy_settings_t *s) {
    moy_settings_lock();
    int clean = flush(s);
    moy_settings_unlock();
    return clean;
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

static int settings_load(moy_settings_t *s, const char *text, size_t len,
                      uint32_t *rows) {
    moy_settings_t tmp = { s->mem, NULL, 0u, 0u, 0u, NULL, NULL, 0u };
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
    s->gen++;
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

static int settings_set(moy_settings_t *s, const char *key, size_t key_len,
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
        s->gen++;
    }
    return rc;
}

static int settings_delete(moy_settings_t *s, const char *key, size_t key_len) {
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
    s->gen++;
    return 1;
}

uint32_t moy_settings_gen(const moy_settings_t *s) {
    return s->gen;
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

// -- the settings file ---------------------------------------------------------

static int is_blank(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f';
}

int moy_settings_key_ok(const char *key, size_t len) {
    if (len == 0u || key[0] == '#') {
        return 0;
    }
    for (size_t i = 0; i < len; i++) {
        char c = key[i];
        if (c == ' ' || c == '\t' || c == '=' || c == '\r' || c == '\n') {
            return 0;
        }
    }
    return 1;
}

static void trim(const char **p, const char **e) {
    while (*p < *e && is_blank(**p)) {
        (*p)++;
    }
    while (*e > *p && is_blank((*e)[-1])) {
        (*e)--;
    }
}

static int settings_load_file(moy_settings_t *s, const char *text, size_t len,
                           uint32_t *rows, uint32_t *passed) {
    moy_settings_t tmp = { s->mem, NULL, 0u, 0u, 0u, NULL, NULL, 0u };
    uint32_t bad = 0u;
    const char *at = text, *end = text + len;
    while (at < end) {
        const char *nl = memchr(at, '\n', (size_t)(end - at));
        const char *le = nl != NULL ? nl : end;
        const char *lp = at;
        at = nl != NULL ? nl + 1 : end;
        trim(&lp, &le);
        if (lp == le || *lp == '#') {
            continue;
        }
        const char *eq = memchr(lp, '=', (size_t)(le - lp));
        if (eq == NULL) {
            bad++;
            continue;
        }
        const char *kp = lp, *ke = eq, *vp = eq + 1, *ve = le;
        trim(&kp, &ke);
        trim(&vp, &ve);
        size_t kn = (size_t)(ke - kp), jn = (size_t)(ve - vp);
        if (!moy_settings_key_ok(kp, kn)
            || moy_settings_validate(vp, jn) != MOY_SETTINGS_OK) {
            bad++;
            continue;
        }
        char *buf = make_buf(&tmp, kn, jn);
        if (buf == NULL || (memcpy(buf, kp, kn), memcpy(buf + kn, vp, jn),
                            put_row(&tmp, buf, (uint32_t)kn, (uint32_t)jn))
                               != MOY_SETTINGS_OK) {
            clear_rows(&tmp);
            return MOY_SETTINGS_NOMEM;
        }
    }
    clear_rows(s);
    s->rows = tmp.rows;
    s->count = tmp.count;
    s->cap = tmp.cap;
    s->dirty = 0u;
    s->gen++;
    *rows = s->count;
    if (passed != NULL) {
        *passed = bad;
    }
    return MOY_SETTINGS_OK;
}

// a's key before b's, by bytes, a shorter key first where one is the other's start
static int key_before(const row_t *a, const row_t *b) {
    size_t n = a->key_len < b->key_len ? a->key_len : b->key_len;
    int c = memcmp(a->buf, b->buf, n);
    return c < 0 || (c == 0 && a->key_len < b->key_len);
}

size_t moy_settings_file(const moy_settings_t *s, char *out, size_t cap) {
    sink_t k = { out, cap, 0u };
    // The rows in key order with no index to allocate: each pass writes the
    // least key after the last one written (a settings file holds dozens).
    const row_t *last = NULL;
    for (;;) {
        const row_t *next = NULL;
        for (uint32_t i = 0; i < s->count; i++) {
            const row_t *r = &s->rows[i];
            if ((last == NULL || key_before(last, r)) && (next == NULL || key_before(r, next))) {
                next = r;
            }
        }
        if (next == NULL) {
            return k.n;
        }
        last = next;
        if (moy_settings_key_ok(next->buf, next->key_len)) {
            put(&k, next->buf, next->key_len);
            put(&k, " = ", 3);
            const char *v = next->buf + next->key_len;
            for (uint32_t i = 0; i < next->json_len; i++) {
                put(&k, (v[i] == '\n' || v[i] == '\r') ? " " : v + i, 1);
            }
            put(&k, "\n", 1);
        }
    }
}

// -- every write under the settings lock --------------------------------------

int moy_settings_load(moy_settings_t *s, const char *text, size_t len, uint32_t *rows) {
    moy_settings_lock();
    int r = settings_load(s, text, len, rows);
    moy_settings_unlock();
    return r;
}

int moy_settings_set(moy_settings_t *s, const char *key, size_t key_len, const char *json, size_t json_len) {
    moy_settings_lock();
    int r = settings_set(s, key, key_len, json, json_len);
    moy_settings_unlock();
    return r;
}

int moy_settings_delete(moy_settings_t *s, const char *key, size_t key_len) {
    moy_settings_lock();
    int r = settings_delete(s, key, key_len);
    moy_settings_unlock();
    return r;
}

int moy_settings_load_file(moy_settings_t *s, const char *text, size_t len, uint32_t *rows, uint32_t *passed) {
    moy_settings_lock();
    int r = settings_load_file(s, text, len, rows, passed);
    moy_settings_unlock();
    return r;
}
