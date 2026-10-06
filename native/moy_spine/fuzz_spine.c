// The spine's API sequences, fuzzed against models, under the sanitizers.
//
// tools/moy_index_spike.py builds it (`--component spine sanitize`), and so does
// tests/test_moy_spine_twins.py. With clang's libFuzzer
// (-fsanitize=fuzzer,address,undefined) it is a LLVMFuzzerTestOneInput; with
// -DMOY_SPINE_FUZZ_MAIN it is a seeded random driver any compiler builds,
// `fuzz_spine SEED RUNS`. Either way it links one implementation of
// moy_htab.h, moy_route.h and moy_settings.h and defines the allocator itself.
//
// An input is a program: an op byte, then its operands. Each component has a
// model, the obvious version with arrays scanned linearly, and every answer the
// implementation gives is checked against it: a handle table of a random kind
// and size, the app registry, the back-stack, the return records, the leases
// and the settings rows. The allocator counts every byte, holds each release to
// the size it was allocated with, and refuses an allocation on a countdown the
// input sets, so the out-of-memory paths run too and must leave a component
// exactly as it was. Two ops feed the settings scanner raw and corrupted text:
// it must answer without reading outside the text, and a text it takes must
// dump to a file that loads to the same dump. (Its agreement with CPython's
// json is tests/test_moy_spine_twins.py's.)

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_htab.h"
#include "moy_route.h"
#include "moy_settings.h"

#define CHECK(c) do { \
        if (!(c)) { \
            fprintf(stderr, "fuzz_spine: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            abort(); \
        } \
} while (0)

// -- the allocator, counted ------------------------------------------------------

static long live_bytes;
static int fail_in;         // refuse the allocation this many calls on; 0 off
static int failed;          // an allocation was refused during this op

static void *fz_alloc(size_t n) {
    if (fail_in > 0 && --fail_in == 0) {
        failed = 1;
        return NULL;
    }
    size_t *p = calloc(1, n + 2 * sizeof(size_t));
    if (p == NULL) {
        return NULL;
    }
    p[0] = n;
    live_bytes += (long)n;
    return p + 2;
}

static void fz_release(void *q, size_t n) {
    if (q == NULL) {
        return;
    }
    size_t *p = (size_t *)q - 2;
    CHECK(p[0] == n);
    live_bytes -= (long)n;
    free(p);
}

static const moy_htab_mem_t mem = { fz_alloc, fz_release };

// -- the program -------------------------------------------------------------------

typedef struct {
    const uint8_t *b;
    size_t n, i;
} input_t;

static uint8_t next(input_t *in) {
    return in->i < in->n ? in->b[in->i++] : 0u;
}

// -- the handle table ----------------------------------------------------------------

#define TMAX 256

static struct {
    moy_htab_t *t;
    uint8_t kind;
    uint32_t max, used, count;
    uint32_t gen[TMAX];
    uint8_t live[TMAX];
    uint32_t stale[8];
    uint32_t stale_at;
} tm;

static uint32_t tm_handle(uint32_t s) {
    return (tm.gen[s] << 12) | ((uint32_t)tm.kind << 8) | s;
}

static int tm_valid(uint32_t h) {
    uint32_t s = h & 0xffu;
    return h != 0 && ((h >> 8) & 15u) == tm.kind && s < tm.used && tm.live[s]
           && tm.gen[s] == (h >> 12);
}

static void tm_verify(void) {
    CHECK(moy_htab_count(tm.t) == tm.count);
    CHECK(moy_htab_slots(tm.t) == tm.used);
    for (uint32_t s = 0; s < tm.used; s++) {
        CHECK(moy_htab_at(tm.t, s) == (tm.live[s] ? tm_handle(s) : 0u));
        if (tm.live[s]) {
            void *row;
            CHECK(moy_htab_get(tm.t, tm_handle(s), &row) == MOY_HTAB_OK);
        }
    }
    CHECK(moy_htab_at(tm.t, tm.used) == 0u);
}

static void tm_new(input_t *in) {
    int keep = fail_in;
    fail_in = 0;
    moy_htab_free(tm.t);
    memset(&tm, 0, sizeof tm);
    tm.kind = (uint8_t)(1u + next(in) % 15u);
    tm.max = 1u + next(in) % 40u;
    if (next(in) % 8u == 0u) {
        tm.max = TMAX;
    }
    tm.t = moy_htab_new(&mem, tm.kind, tm.max, 24u);
    CHECK(tm.t != NULL);
    fail_in = keep;
}

static uint32_t tm_handle_of(input_t *in) {
    uint8_t mode = next(in) % 4u;
    if (mode < 2u && tm.count) {
        uint32_t k = next(in) % tm.count;
        for (uint32_t s = 0; s < tm.used; s++) {
            if (tm.live[s] && k-- == 0u) {
                return tm_handle(s);
            }
        }
    }
    if (mode == 2u) {
        return tm.stale[next(in) % 8u];
    }
    uint32_t h = 0;
    for (int k = 0; k < 4; k++) {
        h = (h << 8) | next(in);
    }
    return h;
}

static void tm_add(void) {
    uint32_t h = 0;
    void *row = NULL;
    failed = 0;
    int rc = moy_htab_add(tm.t, &h, &row);
    uint32_t s = tm.used;
    for (uint32_t i = 0; i < tm.used; i++) {
        if (!tm.live[i]) {
            s = i;
            break;
        }
    }
    if (s == tm.used && tm.used == tm.max) {
        CHECK(rc == MOY_HTAB_FULL);
        return;
    }
    if (rc == MOY_HTAB_NOMEM) {
        CHECK(failed);
        tm_verify();
        return;
    }
    CHECK(rc == MOY_HTAB_OK);
    if (s == tm.used) {
        tm.gen[s] = 1u;
        tm.used++;
    }
    tm.live[s] = 1u;
    tm.count++;
    CHECK(h == tm_handle(s) && h != 0u && h < (1u << 30));
    for (int i = 0; i < 24; i++) {
        CHECK(((uint8_t *)row)[i] == 0u);       // a taken row is zeroed
    }
    memset(row, 0xa5, 24);                       // and the table owns all of it
    void *again;
    CHECK(moy_htab_get(tm.t, h, &again) == MOY_HTAB_OK && again == row);
}

static void tm_release(uint32_t h) {
    int ok = tm_valid(h);
    CHECK(moy_htab_release(tm.t, h) == (ok ? MOY_HTAB_OK : MOY_HTAB_STALE));
    if (!ok) {
        return;
    }
    uint32_t s = h & 0xffu;
    tm.live[s] = 0u;
    tm.gen[s] = tm.gen[s] >= MOY_HTAB_GEN_MAX ? 1u : tm.gen[s] + 1u;
    tm.count--;
    tm.stale[tm.stale_at++ % 8u] = h;
    void *row;
    CHECK(moy_htab_get(tm.t, h, &row) == MOY_HTAB_STALE);
}

// Round one slot's generations all the way, on a one-slot table.
static void tm_wrap(input_t *in) {
    if (tm.count) {
        tm_release(tm_handle(0));
    }
    uint32_t reps = next(in) % 2u ? MOY_HTAB_GEN_MAX + 1u
                                  : (next(in) % 16u + 1u) * 64u;
    for (uint32_t r = 0; r < reps; r++) {
        uint32_t h = 0;
        void *row;
        failed = 0;
        int rc = moy_htab_add(tm.t, &h, &row);
        if (rc != MOY_HTAB_OK) {
            CHECK(rc == MOY_HTAB_FULL || (rc == MOY_HTAB_NOMEM && failed));
            return;
        }
        uint32_t s = h & 0xffu;
        if (s == tm.used) {
            tm.gen[s] = 1u;
            tm.used++;
        }
        tm.live[s] = 1u;
        tm.count++;
        CHECK(h == tm_handle(s));
        tm_release(h);
    }
}

// -- the app registry ------------------------------------------------------------------

#define APPS 64
#define TITLE_MAX 12

static struct {
    moy_apps_t *a;
    uint32_t n;
    char id[APPS][MOY_ID_MAX];
    uint8_t idlen[APPS];
    char title[APPS][TITLE_MAX];
    uint8_t tlen[APPS];
    uint8_t text[APPS], has_min[APPS];
    int32_t w[APPS], h[APPS];
} ap;

static uint32_t ap_handle(uint32_t s) {
    return (1u << 12) | ((uint32_t)MOY_KIND_APP << 8) | s;
}

static int ap_find(const char *id, size_t n) {
    for (uint32_t s = 0; s < ap.n; s++) {
        if (ap.idlen[s] == n && memcmp(ap.id[s], id, n) == 0) {
            return (int)s;
        }
    }
    return -1;
}

// An id from a pool of 100, sometimes one the registry cannot take.
static size_t id_of(input_t *in, char *out) {
    uint8_t m = next(in);
    if (m % 16u == 0u) {
        return 0;
    }
    if (m % 16u == 1u) {
        memset(out, 'x', 16);
        return 16;
    }
    return (size_t)snprintf(out, 17, "app%u", next(in) % 100u);
}

static void ap_verify(void) {
    CHECK(moy_apps_count(ap.a) == ap.n && moy_apps_slots(ap.a) == ap.n);
    for (uint32_t s = 0; s < ap.n; s++) {
        uint32_t h = ap_handle(s);
        CHECK(moy_apps_at(ap.a, s) == h && moy_apps_valid(ap.a, h));
        CHECK(moy_apps_find(ap.a, ap.id[s], ap.idlen[s]) == h);
        const moy_app_t *app;
        CHECK(moy_apps_get(ap.a, h, &app) == MOY_ROUTE_OK);
        CHECK(app->id.len == ap.idlen[s]
              && memcmp(app->id.s, ap.id[s], ap.idlen[s]) == 0);
        CHECK(app->title_len == ap.tlen[s]
              && memcmp(app->title, ap.title[s], ap.tlen[s]) == 0
              && app->title[app->title_len] == '\0');
        CHECK(app->text_mode == ap.text[s] && app->has_min == ap.has_min[s]);
        CHECK(app->min_w == ap.w[s] && app->min_h == ap.h[s]);
    }
    CHECK(moy_apps_at(ap.a, ap.n) == 0u);
}

static void ap_register(input_t *in) {
    char id[17], title[TITLE_MAX];
    size_t n = id_of(in, id);
    size_t tn = next(in) % (TITLE_MAX + 1u);
    for (size_t k = 0; k < tn; k++) {
        title[k] = (char)next(in);
    }
    int text = next(in) & 1, has_min = next(in) & 1;
    int32_t w = (int32_t)(next(in) * 7919u) - 100, h = (int32_t)next(in) - 100;
    uint32_t handle = 0;
    failed = 0;
    int rc = moy_apps_register(ap.a, id, n, title, tn, text, has_min, w, h,
                               &handle);
    if (n < 1u || n > MOY_ID_MAX) {
        CHECK(rc == MOY_ROUTE_BAD);
    } else if (ap_find(id, n) >= 0) {
        CHECK(rc == MOY_ROUTE_DUP);
    } else if (ap.n == APPS) {
        CHECK(rc == MOY_ROUTE_FULL);
    } else if (rc == MOY_ROUTE_NOMEM) {
        CHECK(failed);
    } else {
        CHECK(rc == MOY_ROUTE_OK);
        uint32_t s = ap.n++;
        memcpy(ap.id[s], id, n);
        ap.idlen[s] = (uint8_t)n;
        memcpy(ap.title[s], title, tn);
        ap.tlen[s] = (uint8_t)tn;
        ap.text[s] = text != 0;
        ap.has_min[s] = has_min != 0;
        ap.w[s] = has_min ? w : 0;
        ap.h[s] = has_min ? h : 0;
        CHECK(handle == ap_handle(s));
    }
    ap_verify();
}

// -- the back-stack ---------------------------------------------------------------------

#define DEPTH 32

static struct {
    moy_back_t *b;
    uint32_t depth;
    char k[DEPTH][MOY_ID_MAX + 1];
    uint8_t len[DEPTH];
} bk;

static void bk_reset(void) {
    bk.depth = 1;
    memcpy(bk.k[0], MOY_ROOT, sizeof MOY_ROOT - 1u);
    bk.len[0] = sizeof MOY_ROOT - 1u;
}

static const char *const KINDS[] = {
    "launcher", "menu", "desk", "desktop", "settings", "files", "artwork",
};

static size_t kind_of(input_t *in, char *out) {
    uint8_t m = next(in);
    if (m % 24u == 0u) {
        return 0;
    }
    if (m % 24u == 1u) {
        memset(out, 'k', 16);
        return 16;
    }
    if (m % 3u == 0u) {
        return (size_t)snprintf(out, 17, "k%u", next(in) % 40u);
    }
    const char *s = KINDS[next(in) % (sizeof KINDS / sizeof KINDS[0])];
    memcpy(out, s, strlen(s));
    return strlen(s);
}

static int bk_index(const char *k, size_t n) {
    for (uint32_t i = 0; i < bk.depth; i++) {
        if (bk.len[i] == n && memcmp(bk.k[i], k, n) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static void bk_verify(void) {
    CHECK(moy_back_depth(bk.b) == bk.depth);
    for (uint32_t i = 0; i < bk.depth; i++) {
        const moy_kind_t *k = moy_back_at(bk.b, i);
        CHECK(k != NULL && k->len == bk.len[i] && memcmp(k->s, bk.k[i], k->len) == 0);
    }
    CHECK(moy_back_at(bk.b, bk.depth) == NULL);
    CHECK(moy_back_top(bk.b) == moy_back_at(bk.b, bk.depth - 1u));
}

static void bk_goto(input_t *in) {
    char k[17];
    size_t n = kind_of(in, k);
    int answer = -1;
    int rc = moy_back_goto(bk.b, k, n, &answer);
    if (n < 1u || n > MOY_ID_MAX) {
        CHECK(rc == MOY_ROUTE_BAD);
    } else if (bk.len[bk.depth - 1u] == n && memcmp(bk.k[bk.depth - 1u], k, n) == 0) {
        CHECK(rc == MOY_ROUTE_OK && answer == MOY_GOTO_STAYED);
    } else if (bk_index(k, n) >= 0) {
        CHECK(rc == MOY_ROUTE_OK && answer == MOY_GOTO_RETURNED);
        bk.depth = (uint32_t)bk_index(k, n) + 1u;
    } else if (bk.depth == DEPTH) {
        CHECK(rc == MOY_ROUTE_FULL);
    } else {
        CHECK(rc == MOY_ROUTE_OK && answer == MOY_GOTO_PUSHED);
        memcpy(bk.k[bk.depth], k, n);
        bk.len[bk.depth++] = (uint8_t)n;
    }
    bk_verify();
}

static void bk_remove(input_t *in) {
    char k[17];
    size_t n = kind_of(in, k);
    int removed = -1;
    int rc = moy_back_remove(bk.b, k, n, &removed);
    if (n < 1u || n > MOY_ID_MAX) {
        CHECK(rc == MOY_ROUTE_BAD);
    } else {
        int at = bk_index(k, n);
        CHECK(rc == MOY_ROUTE_OK && removed == (at > 0));
        if (at > 0) {
            for (uint32_t i = (uint32_t)at; i + 1u < bk.depth; i++) {
                memcpy(bk.k[i], bk.k[i + 1u], sizeof bk.k[i]);
                bk.len[i] = bk.len[i + 1u];
            }
            bk.depth--;
        }
    }
    bk_verify();
}

// -- the return records -------------------------------------------------------------------

static struct {
    moy_returns_t *r;
    char caller[16], back[16];
    uint8_t clen, blen;     // 0: none
} rt;

static void rt_verify(void) {
    const moy_kind_t *c = moy_returns_caller(rt.r), *b = moy_returns_back(rt.r);
    CHECK((c != NULL) == (rt.clen != 0u) && (b != NULL) == (rt.blen != 0u));
    CHECK(c == NULL || (c->len == rt.clen && memcmp(c->s, rt.caller, rt.clen) == 0));
    CHECK(b == NULL || (b->len == rt.blen && memcmp(b->s, rt.back, rt.blen) == 0));
}

static void rt_op(input_t *in, uint8_t op) {
    char k[17];
    size_t n;
    moy_kind_t out;
    switch (op) {
        case 0: {                           // run
            if (next(in) % 4u == 0u) {
                CHECK(moy_returns_run(rt.r, NULL, 0) == MOY_ROUTE_OK);
                rt.clen = 0;
                break;
            }
            n = kind_of(in, k);
            int rc = moy_returns_run(rt.r, k, n);
            if (n < 1u || n > MOY_ID_MAX) {
                CHECK(rc == MOY_ROUTE_BAD);
            } else {
                CHECK(rc == MOY_ROUTE_OK);
                memcpy(rt.caller, k, n);
                rt.clen = (uint8_t)n;
            }
            break;
        }
        case 1: {                           // spend
            int had = moy_returns_spend(rt.r, &out);
            CHECK(had == (rt.clen != 0u));
            CHECK(!had || (out.len == rt.clen && memcmp(out.s, rt.caller, rt.clen) == 0));
            rt.clen = 0;
            break;
        }
        case 2: {                           // route
            int windowed = next(in) & 1;
            int want = MOY_ROUTE_HOME;
            if (windowed) {
                want = MOY_ROUTE_WINDOW;
            } else if (rt.clen == sizeof MOY_EDITOR - 1u
                       && memcmp(rt.caller, MOY_EDITOR, rt.clen) == 0) {
                want = MOY_ROUTE_EDITOR;
            } else if (rt.clen && ap_find(rt.caller, rt.clen) >= 0) {
                want = MOY_ROUTE_APP;
            }
            CHECK(moy_returns_route(rt.r, windowed) == want);
            break;
        }
        case 3: {                           // note
            n = next(in) % 2u ? kind_of(in, k)
                              : (size_t)snprintf(k, 17, "app%u", next(in) % 100u);
            int set = -1;
            int rc = moy_returns_note(rt.r, k, n, &set);
            if (n < 1u || n > MOY_ID_MAX) {
                CHECK(rc == MOY_ROUTE_BAD);
            } else {
                int reg = ap_find(k, n) >= 0;
                CHECK(rc == MOY_ROUTE_OK && set == reg);
                if (reg) {
                    memcpy(rt.back, k, n);
                    rt.blen = (uint8_t)n;
                }
            }
            break;
        }
        default: {                          // take_back
            int had = moy_returns_take_back(rt.r, &out);
            CHECK(had == (rt.blen != 0u));
            CHECK(!had || (out.len == rt.blen && memcmp(out.s, rt.back, rt.blen) == 0));
            rt.blen = 0;
            break;
        }
    }
    rt_verify();
}

// -- the leases -------------------------------------------------------------------------------

static struct {
    moy_leases_t *l;
    uint32_t mask;
} ls;

static const char *const TAGS[] = {
    "web", "update", "settings", "cart", "link", "carts", "dev",
    "", "wasm", "webx", "we", "carts ", "Dev",
};

static void ls_op(input_t *in) {
    int hold = next(in) & 1;
    const char *tag = TAGS[next(in) % (sizeof TAGS / sizeof TAGS[0])];
    size_t n = strlen(tag);
    int idx = -1;
    for (int i = 0; i < (int)MOY_LEASE_TAGS; i++) {
        if (strlen(moy_lease_tag((uint32_t)i)) == n
            && memcmp(moy_lease_tag((uint32_t)i), tag, n) == 0) {
            idx = i;
        }
    }
    uint32_t mask = 0xdeadu;
    int rc = hold ? moy_leases_hold(ls.l, tag, n, &mask)
                  : moy_leases_release(ls.l, tag, n, &mask);
    if (idx < 0) {
        CHECK(rc == MOY_ROUTE_BAD && mask == 0xdeadu);
    } else {
        ls.mask = hold ? ls.mask | (1u << idx) : ls.mask & ~(1u << idx);
        CHECK(rc == MOY_ROUTE_OK && mask == ls.mask);
    }
    CHECK(moy_leases_mask(ls.l) == ls.mask);
    CHECK(moy_lease_tag(MOY_LEASE_TAGS) == NULL);
}

// -- the settings rows -------------------------------------------------------------------------

#define ROWS 24
#define KEY_MAX 16
#define VAL_MAX 80

static struct {
    moy_settings_t *s;
    uint32_t n;
    char key[ROWS][KEY_MAX];
    uint8_t klen[ROWS];
    char val[ROWS][VAL_MAX];
    uint8_t vlen[ROWS];
} st;

// The keys: ASCII, the characters a JSON key escapes, and multi-byte UTF-8.
static const char *const KEYS[] = {
    "a", "b", "theme", "fs", "app_guard", "q\"q", "b\\s", "n\nl", "t\tb", "\x01x",
    "caf\xc3\xa9", "\xf0\x9f\x98\x80", "k\x7f", "wallpaper_guard", "x y",
};
static const size_t KEY_LEN[] = { 1, 1, 5, 2, 9, 3, 3, 3, 3, 2, 5, 4, 2, 15, 3 };
#define NKEYS ((int)(sizeof KEYS / sizeof KEYS[0]))

// Valid values, then invalid ones.
static const char *const GOOD[] = {
    "1", "-0", "0.5e+3", "1E-2", "true", "false", "null", "NaN", "-Infinity",
    "\"s\"", "\"\\u00e9\\n\\\"\"", "[]", "{}", "[1, 2, {\"a\": null}]", " 7 ",
    "{\"k\": [ ]}", "\n[\t1 ]\r", "\"\\ud83d\\ude00\"", "\"\\ud800\"",
    "{\"a\": {\"b\": [true, false]}}", "Infinity", "\"\xc3\xa9\"",
};
static const char *const BAD[] = {
    "", " ", "01", "1.", "[1,]", "{\"a\" 1}", "'x'", "\"\\x\"", "tru", "[", "1 2",
    "\"a\nb\"", "+1", ".5", "1e", "{,}", "[1 2]", "nul", "-", "\"\\u12G4\"",
    "{\"a\":}", "[}", "-NaN", "\"\\u12\"", "\"abc", "NaN1", "--1", "0x10",
};
#define NGOOD ((int)(sizeof GOOD / sizeof GOOD[0]))
#define NBAD ((int)(sizeof BAD / sizeof BAD[0]))

static int st_find(const char *k, size_t n) {
    for (uint32_t i = 0; i < st.n; i++) {
        if (st.klen[i] == n && memcmp(st.key[i], k, n) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static void st_put(const char *k, size_t kn, const char *v, size_t vn) {
    int at = st_find(k, kn);
    if (at < 0) {
        CHECK(st.n < ROWS);
        at = (int)st.n++;
        memcpy(st.key[at], k, kn);
        st.klen[at] = (uint8_t)kn;
    }
    CHECK(vn <= VAL_MAX);
    memcpy(st.val[at], v, vn);
    st.vlen[at] = (uint8_t)vn;
}

// The model's file: the same rows, the same escapes, written the obvious way.
static size_t st_text(char *out, size_t cap) {
    size_t w = 0;
#define PUT(p, n) do { CHECK(w + (n) <= cap); memcpy(out + w, (p), (n)); w += (n); } while (0)
    PUT("{", 1);
    for (uint32_t i = 0; i < st.n; i++) {
        if (i) {
            PUT(", ", 2);
        }
        PUT("\"", 1);
        for (uint8_t j = 0; j < st.klen[i]; j++) {
            unsigned char c = (unsigned char)st.key[i][j];
            char esc[8];
            size_t en = 0;
            if (c == '"' || c == '\\') {
                esc[0] = '\\';
                esc[1] = (char)c;
                en = 2;
            } else if (c == '\b' || c == '\f' || c == '\n' || c == '\r' || c == '\t') {
                esc[0] = '\\';
                esc[1] = c == '\b' ? 'b' : c == '\f' ? 'f' : c == '\n' ? 'n'
                         : c == '\r' ? 'r' : 't';
                en = 2;
            } else if (c < 0x20) {
                en = (size_t)snprintf(esc, sizeof esc, "\\u%04x", c);
            } else {
                esc[0] = (char)c;
                en = 1;
            }
            PUT(esc, en);
        }
        PUT("\": ", 3);
        PUT(st.val[i], st.vlen[i]);
    }
    PUT("}", 1);
#undef PUT
    return w;
}

static char dump_buf[ROWS * (KEY_MAX * 6 + VAL_MAX + 8) + 8];

static void st_verify(void) {
    CHECK(moy_settings_count(st.s) == st.n);
    for (uint32_t i = 0; i < st.n; i++) {
        const char *k, *j;
        size_t kn, jn;
        CHECK(moy_settings_at(st.s, i, &k, &kn, &j, &jn));
        CHECK(kn == st.klen[i] && memcmp(k, st.key[i], kn) == 0);
        CHECK(jn == st.vlen[i] && memcmp(j, st.val[i], jn) == 0);
        const char *g;
        size_t gn;
        CHECK(moy_settings_get(st.s, st.key[i], st.klen[i], &g, &gn));
        CHECK(g == j && gn == jn);
    }
    const char *k, *j;
    size_t kn, jn;
    CHECK(!moy_settings_at(st.s, st.n, &k, &kn, &j, &jn));
    char want[sizeof dump_buf];
    size_t wn = st_text(want, sizeof want);
    size_t dn = moy_settings_dump(st.s, NULL, 0);
    CHECK(dn == wn && dn < sizeof dump_buf);
    CHECK(moy_settings_dump(st.s, dump_buf, sizeof dump_buf) == dn);
    CHECK(memcmp(dump_buf, want, dn) == 0);
    char tiny[3];
    CHECK(moy_settings_dump(st.s, tiny, sizeof tiny) == dn);    // truncates, says so
    CHECK(memcmp(tiny, want, dn < sizeof tiny ? dn : sizeof tiny) == 0);
}

static void st_key(input_t *in, const char **k, size_t *n) {
    int i = next(in) % (NKEYS + 1);
    if (i == NKEYS) {
        *k = "";
        *n = 0;
        return;
    }
    *k = KEYS[i];
    *n = KEY_LEN[i];
}

// A value and whether it is one a row may hold: a pool entry, or a nest.
static size_t st_value(input_t *in, char *out, int *valid) {
    uint8_t m = next(in) % 8u;
    if (m == 0u) {
        size_t d = next(in) % 40u;
        memset(out, '[', d);
        memset(out + d, ']', d);
        *valid = d >= 1u && d <= MOY_SETTINGS_DEPTH - 1u;
        return 2u * d;
    }
    if (m < 3u) {
        const char *s = BAD[next(in) % NBAD];
        *valid = 0;
        memcpy(out, s, strlen(s));
        return strlen(s);
    }
    const char *s = GOOD[next(in) % NGOOD];
    *valid = 1;
    memcpy(out, s, strlen(s));
    return strlen(s);
}

static void st_set(input_t *in) {
    const char *k;
    size_t kn;
    char v[VAL_MAX + 1];
    int valid;
    st_key(in, &k, &kn);
    size_t vn = st_value(in, v, &valid);
    if (st.n == ROWS && st_find(k, kn) < 0) {
        return;                                 // the model's room, not the store's
    }
    failed = 0;
    int rc = moy_settings_set(st.s, k, kn, v, vn);
    if (kn == 0u) {
        CHECK(rc == MOY_SETTINGS_BADKEY);
    } else if (!valid) {
        CHECK(rc == MOY_SETTINGS_BADJSON);
    } else if (rc == MOY_SETTINGS_NOMEM) {
        CHECK(failed);
    } else {
        CHECK(rc == MOY_SETTINGS_OK);
        st_put(k, kn, v, vn);
    }
    st_verify();
}

static void st_get_delete(input_t *in) {
    const char *k;
    size_t kn;
    st_key(in, &k, &kn);
    int at = kn ? st_find(k, kn) : -1;
    if (next(in) & 1) {
        const char *g;
        size_t gn;
        CHECK(moy_settings_get(st.s, k, kn, &g, &gn) == (at >= 0));
        return;
    }
    CHECK(moy_settings_delete(st.s, k, kn) == (at >= 0));
    if (at >= 0) {
        for (uint32_t i = (uint32_t)at; i + 1u < st.n; i++) {
            memcpy(st.key[i], st.key[i + 1u], KEY_MAX);
            st.klen[i] = st.klen[i + 1u];
            memcpy(st.val[i], st.val[i + 1u], VAL_MAX);
            st.vlen[i] = st.vlen[i + 1u];
        }
        st.n--;
    }
    st_verify();
}

// Write `n` JSON-escaped bytes of `k` into out, the way another writer might:
// raw where it can, \u escapes for the control characters and some letters.
static size_t json_key(input_t *in, const char *k, size_t n, char *out) {
    size_t w = 0;
    out[w++] = '"';
    for (size_t j = 0; j < n; j++) {
        unsigned char c = (unsigned char)k[j];
        if (c == '"' || c == '\\') {
            out[w++] = '\\';
            out[w++] = (char)c;
        } else if (c < 0x20 || (c < 0x80 && (next(in) % 5u) == 0u)) {
            w += (size_t)snprintf(out + w, 8, "\\u%04X", c);
        } else {
            out[w++] = (char)c;
        }
    }
    out[w++] = '"';
    return w;
}

// Load a file the model builds from a few members, repeats included.
static void st_load(input_t *in) {
    static char text[sizeof dump_buf];
    size_t w = 0;
    uint32_t members = next(in) % 7u;
    struct { char k[KEY_MAX]; size_t kn; char v[VAL_MAX]; size_t vn; } m[7];
    uint32_t nm = 0;
    text[w++] = '{';
    for (uint32_t i = 0; i < members; i++) {
        const char *k;
        size_t kn;
        char v[VAL_MAX + 1];
        int valid;
        st_key(in, &k, &kn);
        if (kn == 0u) {
            continue;
        }
        size_t vn = st_value(in, v, &valid);
        if (!valid) {
            vn = strlen(GOOD[0]);
            memcpy(v, GOOD[0], vn);
        }
        if (nm) {
            text[w++] = ',';
            text[w++] = ' ';
        }
        w += json_key(in, k, kn, text + w);
        memcpy(text + w, ":", 1);
        w += 1;
        if (next(in) & 1) {
            text[w++] = ' ';
        }
        memcpy(text + w, v, vn);
        w += vn;
        memcpy(m[nm].k, k, kn);
        m[nm].kn = kn;
        memcpy(m[nm].v, v, vn);
        m[nm].vn = vn;
        nm++;
    }
    text[w++] = '}';
    // a value keeps its surrounding spaces only inside the object's own
    // whitespace: the model trims what the scanner trims
    for (uint32_t i = 0; i < nm; i++) {
        size_t a = 0, b = m[i].vn;
        while (a < b && strchr(" \t\n\r", m[i].v[a])) {
            a++;
        }
        while (b > a && strchr(" \t\n\r", m[i].v[b - 1u])) {
            b--;
        }
        memmove(m[i].v, m[i].v + a, b - a);
        m[i].vn = b - a;
    }
    failed = 0;
    uint32_t rows = 0;
    int rc = moy_settings_load(st.s, text, w, &rows);
    if (rc == MOY_SETTINGS_NOMEM) {
        CHECK(failed);
        st_verify();
        return;
    }
    CHECK(rc == MOY_SETTINGS_OK);
    st.n = 0;
    for (uint32_t i = 0; i < nm; i++) {
        st_put(m[i].k, m[i].kn, m[i].v, m[i].vn);
    }
    CHECK(rows == st.n);
    st_verify();
}

// Text the scanner reads untrusted: raw bytes, or the model's file with a byte
// changed. Any answer is allowed, but a refusal changes nothing and an
// acceptance is a file that dumps and loads to itself.
static void st_scan(input_t *in) {
    static char text[sizeof dump_buf];
    size_t n;
    if (next(in) & 1) {
        n = next(in) % 48u;
        for (size_t i = 0; i < n; i++) {
            uint8_t b = next(in);
            static const char P[] = "{}[]\",:\\ntfu0123-.eE+ \t\"abc\xc3\xa9";
            text[i] = (b & 0x80u) ? (char)next(in) : P[b % (sizeof P - 1u)];
        }
    } else {
        n = st_text(text, sizeof text);
        uint8_t how = next(in) % 4u;
        size_t at = n ? next(in) % n : 0;
        if (how == 0u && n) {
            text[at] = (char)next(in);
        } else if (how == 1u && n) {
            memmove(text + at, text + at + 1, n - at - 1u);
            n--;
        } else if (how == 2u) {
            n = at;                              // a truncation
        }
    }
    int keep = fail_in;
    fail_in = 0;
    moy_settings_t *other = moy_settings_new(&mem);
    CHECK(other != NULL);
    fail_in = keep;
    int vrc = moy_settings_validate(text, n);
    CHECK(vrc == MOY_SETTINGS_OK || vrc == MOY_SETTINGS_BADJSON);
    uint32_t rows = 0;
    failed = 0;
    int rc = moy_settings_load(other, text, n, &rows);
    int armed = failed;
    fail_in = 0;
    if (rc == MOY_SETTINGS_OK) {
        CHECK(moy_settings_count(other) == rows);
        size_t dn = moy_settings_dump(other, NULL, 0);
        char *dump = malloc(dn + 1u);
        CHECK(dump != NULL);
        moy_settings_dump(other, dump, dn);
        moy_settings_t *again = moy_settings_new(&mem);
        CHECK(again != NULL);
        uint32_t rows2 = 0;
        CHECK(moy_settings_load(again, dump, dn, &rows2) == MOY_SETTINGS_OK);
        CHECK(rows2 == rows);
        size_t d2 = moy_settings_dump(again, NULL, 0);
        char *dump2 = malloc(d2 + 1u);
        CHECK(dump2 != NULL);
        moy_settings_dump(again, dump2, d2);
        CHECK(d2 == dn && memcmp(dump, dump2, dn) == 0);
        for (uint32_t i = 0; i < rows; i++) {
            const char *k, *j;
            size_t kn, jn;
            CHECK(moy_settings_at(other, i, &k, &kn, &j, &jn));
            CHECK(kn >= 1u);
            CHECK(moy_settings_validate(j, jn) == MOY_SETTINGS_OK);
        }
        free(dump);
        free(dump2);
        moy_settings_free(again);
    } else {
        CHECK(rc == MOY_SETTINGS_BADJSON || (rc == MOY_SETTINGS_NOMEM && armed));
        CHECK(moy_settings_count(other) == 0u);
    }
    moy_settings_free(other);
}

// -- all of it -----------------------------------------------------------------------------------

static void make_all(void) {
    ap.a = moy_apps_new(&mem);
    bk.b = moy_back_new(&mem);
    ls.l = moy_leases_new(&mem);
    st.s = moy_settings_new(&mem);
    CHECK(ap.a && bk.b && ls.l && st.s);
    rt.r = moy_returns_new(&mem, ap.a);
    CHECK(rt.r != NULL);
    ap.n = 0;
    bk_reset();
    rt.clen = rt.blen = 0;
    ls.mask = 0;
    st.n = 0;
}

static void free_all(void) {
    moy_returns_free(rt.r);
    moy_apps_free(ap.a);
    moy_back_free(bk.b);
    moy_leases_free(ls.l);
    moy_settings_free(st.s);
    moy_htab_free(tm.t);
    tm.t = NULL;
}

static void verify_all(void) {
    tm_verify();
    ap_verify();
    bk_verify();
    rt_verify();
    st_verify();
    CHECK(moy_leases_mask(ls.l) == ls.mask);
}

static void run(const uint8_t *data, size_t size) {
    fail_in = 0;
    live_bytes = 0;
    memset(&tm, 0, sizeof tm);
    input_t in = { data, size, 0 };
    make_all();
    tm_new(&in);
    unsigned ops = 0;
    while (in.i < in.n) {
        uint8_t op = next(&in) % 26u;
        switch (op) {
            case 0: case 1:
                tm_add();
                break;
            case 2: {
                uint32_t h = tm_handle_of(&in);
                void *row;
                CHECK(moy_htab_get(tm.t, h, &row) == (tm_valid(h) ? MOY_HTAB_OK : MOY_HTAB_STALE));
                break;
            }
            case 3:
                tm_release(tm_handle_of(&in));
                break;
            case 4:
                if (tm.max == 1u) {
                    tm_wrap(&in);
                }
                break;
            case 5:
                tm_new(&in);
                break;
            case 6: case 7:
                ap_register(&in);
                break;
            case 8: {
                char id[17];
                size_t n = id_of(&in, id);
                int at = n >= 1u && n <= MOY_ID_MAX ? ap_find(id, n) : -1;
                CHECK(moy_apps_find(ap.a, id, n) == (at >= 0 ? ap_handle((uint32_t)at) : 0u));
                break;
            }
            case 9: {
                uint32_t h = next(&in) % 3u == 0u ? (uint32_t)next(&in) * 977u
                             : ap_handle(next(&in) % (APPS + 3u));
                if (next(&in) & 1) {
                    h += (uint32_t)(next(&in) % 3u) << 12;
                }
                int ok = h != 0u && ((h >> 8) & 15u) == MOY_KIND_APP
                         && (h & 0xffu) < ap.n && (h >> 12) == 1u;
                const moy_app_t *app;
                CHECK(moy_apps_get(ap.a, h, &app) == (ok ? MOY_ROUTE_OK : MOY_ROUTE_STALE));
                CHECK(!!moy_apps_valid(ap.a, h) == ok);
                break;
            }
            case 10: case 11: case 12:
                bk_goto(&in);
                break;
            case 13:
                bk_remove(&in);
                break;
            case 14: {
                char k[17];
                size_t n = kind_of(&in, k);
                CHECK(moy_back_index(bk.b, k, n) == bk_index(k, n));
                break;
            }
            case 15: case 16:
                rt_op(&in, next(&in) % 5u);
                break;
            case 17:
                ls_op(&in);
                break;
            case 18: case 19:
                st_set(&in);
                break;
            case 20:
                st_get_delete(&in);
                break;
            case 21:
                st_load(&in);
                break;
            case 22:
                st_scan(&in);
                break;
            case 24:                            // towards a full registry
                for (unsigned k = 0; k < 70u; k++) {
                    uint8_t b[] = { 2, (uint8_t)k, 0, 1, 1, 9, 9 };
                    input_t one = { b, sizeof b, 0 };
                    ap_register(&one);
                }
                break;
            case 25:                            // and a full back-stack
                for (unsigned k = 0; k < 40u; k++) {
                    uint8_t b[] = { 3, (uint8_t)k };
                    input_t one = { b, sizeof b, 0 };
                    bk_goto(&one);
                }
                break;
            default:
                if (next(&in) % 3u == 0u) {     // a new countdown, or a new life
                    fail_in = next(&in) % 8u;
                } else {
                    fail_in = next(&in) % 5u == 0u ? 1 : 0;
                }
                break;
        }
        if ((++ops & 15u) == 0u) {
            verify_all();
        }
    }
    fail_in = 0;
    verify_all();
    free_all();
    CHECK(live_bytes == 0);
}

#ifdef MOY_SPINE_FUZZ_MAIN
static uint32_t rng;

static uint32_t xorshift(void) {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

int main(int argc, char **argv) {
    uint32_t seed = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 1u;
    unsigned long runs = argc > 2 ? strtoul(argv[2], NULL, 0) : 1000ul;
    static uint8_t buf[4096];
    rng = seed ? seed : 1u;
    for (unsigned long r = 0; r < runs; r++) {
        size_t n = xorshift() % sizeof buf;
        for (size_t k = 0; k < n; k++) {
            uint32_t x = xorshift();
            // Mostly small operands, so ids repeat and handles land.
            buf[k] = (uint8_t)((x & 0x300u) ? (x % 7u) : (x >> 24));
        }
        run(buf, n);
    }
    printf("fuzz_spine: %lu programs, seed %u, ok\n", runs, seed);
    return 0;
}
#else
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    run(data, size);
    return 0;
}
#endif
