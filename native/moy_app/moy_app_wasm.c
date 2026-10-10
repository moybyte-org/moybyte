// The app ABI's wasm import adapter (moy_app_wasm.h has the contract).

#if defined(__has_include)
#if __has_include("esp_timer.h")
#define MOY_APP_WASM_ESP 1
#endif
#endif
#if !defined(MOY_APP_WASM_ESP) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200112L
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_app_wasm.h"

#if defined(MOY_APP_WASM_ESP)
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static uint32_t now_us(void) {
    return (uint32_t)esp_timer_get_time();
}
static void yield(void) {
    vTaskDelay(1);
}
#else
#include <sched.h>
#include <time.h>
static uint32_t now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)((uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u);
}
static void yield(void) {
    sched_yield();
}
#endif

// The engine's hop to the VM's task (native/moy_wasm/moy_wasm_session.h):
// weak, so an image or a host with no session thread calls the row in place.
int moy_wasm_on_vm(void (*fn)(void *arg), void *arg) __attribute__((weak));

// How a native's row runs: hopped to the VM's task, or read in place under
// the seqlock.
enum { HOP = 0, SEQ = 1 };

static moy_appabi_t *volatile s_a;
static volatile uint32_t s_g, s_roles;
static uint32_t s_hops, s_hop_us, s_hop_work_us;

void moy_app_wasm_bind(moy_appabi_t *a, uint32_t g, uint32_t roles) {
    s_a = a;
    s_g = a != NULL ? g : 0u;
    s_roles = a != NULL ? roles : 0u;
}

void moy_app_wasm_end(void) {
    moy_appabi_t *a = s_a;
    uint32_t g = s_g;
    moy_app_wasm_bind(NULL, 0u, 0u);
    if (a != NULL && g != 0u) {
        moy_app_end(a, g);
    }
}

void moy_app_wasm_forget(const moy_appabi_t *a) {
    if (a != NULL && s_a == a) {
        moy_app_wasm_bind(NULL, 0u, 0u);
    }
}

uint32_t moy_app_wasm_grant(void) {
    return s_g;
}

void moy_app_wasm_hops(uint32_t *count, uint32_t *us, uint32_t *work_us) {
    *count = s_hops;
    *us = s_hop_us;
    *work_us = s_hop_work_us;
}

// -- running a row -----------------------------------------------------------------

typedef struct {
    const uint8_t *p;
    uint32_t n;
} span_t;

typedef struct {
    moy_appabi_t *a;
    uint32_t g, row;
    const uint8_t *arg;
    size_t n;
    uint8_t *ans;
    size_t cap;
    int32_t r;
    uint32_t work_us;           // the row's own time on the VM's task
} hop_t;

static void hop_fn(void *p) {
    hop_t *h = p;
    uint32_t t0 = now_us();
    h->r = moy_app_role(h->a, h->g, h->row, h->arg, h->n, h->ans, h->cap);
    h->work_us = now_us() - t0;
}

static void le32(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
    q[2] = (uint8_t)(v >> 16);
    q[3] = (uint8_t)(v >> 24);
}

// Row `row` with its text fields `s` and, where `num` is not NULL, one number
// field first: packed as moy_app_role's fields, then run as `how` says.
static int32_t run(int how, int row, const int32_t *num, const span_t *s, int ns,
                   uint8_t *ans, uint32_t cap) {
    moy_appabi_t *a = s_a;
    uint32_t g = s_g;
    if (a == NULL || row < 0) {
        return -MOY_APP_STALE;
    }
    uint8_t stack[320];
    size_t n = num != NULL ? 8u : 0u;
    for (int i = 0; i < ns; i++) {
        n += 4u + s[i].n;
    }
    uint8_t *buf = n <= sizeof stack ? stack : malloc(n);
    if (buf == NULL) {
        return -MOY_APP_NOMEM;
    }
    size_t at = 0;
    if (num != NULL) {
        le32(buf, 4u);
        le32(buf + 4, (uint32_t)*num);
        at = 8;
    }
    for (int i = 0; i < ns; i++) {
        le32(buf + at, s[i].n);
        if (s[i].n) {
            memcpy(buf + at + 4, s[i].p, s[i].n);
        }
        at += 4u + s[i].n;
    }
    int32_t r;
    if (how == SEQ) {
        for (unsigned spins = 0;; spins++) {
            uint32_t q = moy_app_seq_begin(a);
            if (!(q & 1u)) {
                r = moy_app_role(a, g, (uint32_t)row, buf, n, ans, cap);
                if (moy_app_seq_end(a, q)) {
                    break;
                }
            }
            if (spins >= 64u) {
                yield();
            }
        }
    } else if (moy_wasm_on_vm == NULL) {
        r = moy_app_role(a, g, (uint32_t)row, buf, n, ans, cap);
    } else {
        hop_t h = { a, g, (uint32_t)row, buf, n, ans, cap, -MOY_APP_NEEDS_VM, 0u };
        uint32_t t0 = now_us();
        if (moy_wasm_on_vm(hop_fn, &h) != 0) {
            h.r = -MOY_APP_NEEDS_VM;
        }
        s_hop_us += now_us() - t0;
        s_hop_work_us += h.work_us;
        s_hops++;
        r = h.r;
    }
    if (buf != stack) {
        free(buf);
    }
    return r;
}

// -- the natives -----------------------------------------------------------------------
//
// Each is the engine's calling shape: the engine's handle on the call first
// (a WAMR exec env, or the browser's binding), then the row's arguments, a
// '*~' a pointer the engine has checked and translated and its length.

enum {
    N_THEME_TOKEN, N_THEME_GEN, N_THEME_LIGHT, N_THEME_NAME, N_THEME_VARIANT,
    N_THEME_SKIN, N_THEME_SET, N_THEME_SET_VARIANT, N_THEME_SET_SKIN,
    N_FILES_READABLE, N_FILES_READY, N_FILES_LIST, N_FILES_COUNT, N_FILES_LOAD,
    N_FILES_SAVE, N_FILES_DELETE,
    N_NAV_OPEN_APP,
    N_PREFS_GET, N_PREFS_SET, N_PREFS_CLEAR,
    N_CLIPBOARD_PUT_TEXT, N_CLIPBOARD_TEXT, N_CLIPBOARD_KIND, N_CLIPBOARD_SEQ,
    N_COUNT
};

// Each native's "role.verb", in the table's order.
static const char *const ROW_OF[N_COUNT] = {
    "theme.token", "theme.gen", "theme.light", "theme.name", "theme.variant",
    "theme.skin", "theme.set", "theme.set_variant", "theme.set_skin",
    "files.readable", "files.ready", "files.list", "files.count", "files.load",
    "files.save", "files.delete",
    "nav.open_app",
    "prefs.get", "prefs.set", "prefs.clear",
    "clipboard.put_text", "clipboard.text", "clipboard.kind", "clipboard.seq",
};

static int s_rows[N_COUNT];
static int s_rows_made;

int moy_app_wasm_row(uint32_t i) {
    if (i >= N_COUNT) {
        return -1;
    }
    if (!s_rows_made) {
        for (int k = 0; k < N_COUNT; k++) {
            s_rows[k] = -1;
            for (uint32_t r = 0; r < MOY_APP_TABLE_N; r++) {
                if (strcmp(moy_app_table_name(r), ROW_OF[k]) == 0) {
                    s_rows[k] = (int)r;
                    break;
                }
            }
        }
        s_rows_made = 1;
    }
    return s_rows[i];
}

#define R(k) moy_app_wasm_row(k)

static int32_t w_theme_token(void *e, int32_t role) {
    (void)e;
    return run(SEQ, R(N_THEME_TOKEN), &role, NULL, 0, NULL, 0);
}
static int32_t w_theme_gen(void *e) {
    (void)e;
    return run(SEQ, R(N_THEME_GEN), NULL, NULL, 0, NULL, 0);
}
static int32_t w_theme_light(void *e) {
    (void)e;
    return run(SEQ, R(N_THEME_LIGHT), NULL, NULL, 0, NULL, 0);
}
static int32_t w_theme_name(void *e, uint8_t *buf, uint32_t cap) {
    (void)e;
    return run(SEQ, R(N_THEME_NAME), NULL, NULL, 0, buf, cap);
}
static int32_t w_theme_variant(void *e, uint8_t *buf, uint32_t cap) {
    (void)e;
    return run(SEQ, R(N_THEME_VARIANT), NULL, NULL, 0, buf, cap);
}
static int32_t w_theme_skin(void *e, uint8_t *buf, uint32_t cap) {
    (void)e;
    return run(SEQ, R(N_THEME_SKIN), NULL, NULL, 0, buf, cap);
}
static int32_t w_theme_set(void *e, const uint8_t *nm, uint32_t nn, const uint8_t *v,
                           uint32_t vn) {
    (void)e;
    span_t s[2] = { { nm, nn }, { v, vn } };
    return run(HOP, R(N_THEME_SET), NULL, s, 2, NULL, 0);
}
static int32_t w_theme_set_variant(void *e, const uint8_t *v, uint32_t vn) {
    (void)e;
    span_t s[1] = { { v, vn } };
    return run(HOP, R(N_THEME_SET_VARIANT), NULL, s, 1, NULL, 0);
}
static int32_t w_theme_set_skin(void *e, const uint8_t *v, uint32_t vn) {
    (void)e;
    span_t s[1] = { { v, vn } };
    return run(HOP, R(N_THEME_SET_SKIN), NULL, s, 1, NULL, 0);
}
static int32_t w_files_readable(void *e) {
    (void)e;
    return run(HOP, R(N_FILES_READABLE), NULL, NULL, 0, NULL, 0);
}
static int32_t w_files_ready(void *e) {
    (void)e;
    return run(HOP, R(N_FILES_READY), NULL, NULL, 0, NULL, 0);
}
static int32_t w_files_list(void *e, const uint8_t *k, uint32_t kn, uint8_t *buf,
                            uint32_t cap) {
    (void)e;
    span_t s[1] = { { k, kn } };
    return run(HOP, R(N_FILES_LIST), NULL, s, 1, buf, cap);
}
static int32_t w_files_count(void *e, const uint8_t *k, uint32_t kn) {
    (void)e;
    span_t s[1] = { { k, kn } };
    return run(HOP, R(N_FILES_COUNT), NULL, s, 1, NULL, 0);
}
static int32_t w_files_load(void *e, const uint8_t *k, uint32_t kn, const uint8_t *nm,
                            uint32_t nn, uint8_t *buf, uint32_t cap) {
    (void)e;
    span_t s[2] = { { k, kn }, { nm, nn } };
    return run(HOP, R(N_FILES_LOAD), NULL, s, 2, buf, cap);
}
static int32_t w_files_save(void *e, const uint8_t *k, uint32_t kn, const uint8_t *nm,
                            uint32_t nn, const uint8_t *d, uint32_t dn, uint8_t *out,
                            uint32_t cap) {
    (void)e;
    span_t s[3] = { { k, kn }, { nm, nn }, { d, dn } };
    return run(HOP, R(N_FILES_SAVE), NULL, s, 3, out, cap);
}
static int32_t w_files_delete(void *e, const uint8_t *k, uint32_t kn, const uint8_t *nm,
                              uint32_t nn, uint8_t *out, uint32_t cap) {
    (void)e;
    span_t s[2] = { { k, kn }, { nm, nn } };
    return run(HOP, R(N_FILES_DELETE), NULL, s, 2, out, cap);
}
static int32_t w_nav_open_app(void *e, const uint8_t *id, uint32_t n) {
    (void)e;
    span_t s[1] = { { id, n } };
    return run(HOP, R(N_NAV_OPEN_APP), NULL, s, 1, NULL, 0);
}
static int32_t w_prefs_get(void *e, const uint8_t *k, uint32_t kn, uint8_t *buf,
                           uint32_t cap) {
    (void)e;
    span_t s[1] = { { k, kn } };
    return run(HOP, R(N_PREFS_GET), NULL, s, 1, buf, cap);
}
static int32_t w_prefs_set(void *e, const uint8_t *k, uint32_t kn, const uint8_t *j,
                           uint32_t jn) {
    (void)e;
    span_t s[2] = { { k, kn }, { j, jn } };
    return run(HOP, R(N_PREFS_SET), NULL, s, 2, NULL, 0);
}
static int32_t w_prefs_clear(void *e, const uint8_t *k, uint32_t kn) {
    (void)e;
    span_t s[1] = { { k, kn } };
    return run(HOP, R(N_PREFS_CLEAR), NULL, s, 1, NULL, 0);
}
static int32_t w_clipboard_put_text(void *e, const uint8_t *t, uint32_t n) {
    (void)e;
    span_t s[1] = { { t, n } };
    return run(HOP, R(N_CLIPBOARD_PUT_TEXT), NULL, s, 1, NULL, 0);
}
static int32_t w_clipboard_text(void *e, uint8_t *buf, uint32_t cap) {
    (void)e;
    return run(SEQ, R(N_CLIPBOARD_TEXT), NULL, NULL, 0, buf, cap);
}
static int32_t w_clipboard_kind(void *e) {
    (void)e;
    return run(SEQ, R(N_CLIPBOARD_KIND), NULL, NULL, 0, NULL, 0);
}
static int32_t w_clipboard_seq(void *e) {
    (void)e;
    return run(SEQ, R(N_CLIPBOARD_SEQ), NULL, NULL, 0, NULL, 0);
}

#if defined(__GNUC__)
#define FN(f) (__extension__ (void *)(f))
#else
#define FN(f) ((void *)(f))
#endif

// The rows, roles.json's wasm types: what tests/test_roles.py holds equal.
static const moy_app_native_t NATIVES[N_COUNT] = {
    { "theme_token", FN(w_theme_token), "(i)i", NULL },
    { "theme_gen", FN(w_theme_gen), "()i", NULL },
    { "theme_light", FN(w_theme_light), "()i", NULL },
    { "theme_name", FN(w_theme_name), "(*~)i", NULL },
    { "theme_variant", FN(w_theme_variant), "(*~)i", NULL },
    { "theme_skin", FN(w_theme_skin), "(*~)i", NULL },
    { "theme_set", FN(w_theme_set), "(*~*~)i", NULL },
    { "theme_set_variant", FN(w_theme_set_variant), "(*~)i", NULL },
    { "theme_set_skin", FN(w_theme_set_skin), "(*~)i", NULL },
    { "files_readable", FN(w_files_readable), "()i", NULL },
    { "files_ready", FN(w_files_ready), "()i", NULL },
    { "files_list", FN(w_files_list), "(*~*~)i", NULL },
    { "files_count", FN(w_files_count), "(*~)i", NULL },
    { "files_load", FN(w_files_load), "(*~*~*~)i", NULL },
    { "files_save", FN(w_files_save), "(*~*~*~*~)i", NULL },
    { "files_delete", FN(w_files_delete), "(*~*~*~)i", NULL },
    { "nav_open_app", FN(w_nav_open_app), "(*~)i", NULL },
    { "prefs_get", FN(w_prefs_get), "(*~*~)i", NULL },
    { "prefs_set", FN(w_prefs_set), "(*~*~)i", NULL },
    { "prefs_clear", FN(w_prefs_clear), "(*~)i", NULL },
    { "clipboard_put_text", FN(w_clipboard_put_text), "(*~)i", NULL },
    { "clipboard_text", FN(w_clipboard_text), "(*~)i", NULL },
    { "clipboard_kind", FN(w_clipboard_kind), "()i", NULL },
    { "clipboard_seq", FN(w_clipboard_seq), "()i", NULL },
};

const moy_app_native_t *moy_app_wasm_natives(uint32_t *count) {
    *count = N_COUNT;
    return NATIVES;
}

int moy_app_wasm_row_named(const char *name, size_t n) {
    for (int k = 0; k < N_COUNT; k++) {
        if (strlen(NATIVES[k].symbol) == n && memcmp(NATIVES[k].symbol, name, n) == 0) {
            return moy_app_wasm_row((uint32_t)k);
        }
    }
    return -1;
}

// -- a module's imports ----------------------------------------------------------------

static int uleb(const uint8_t *b, size_t n, size_t *at, uint32_t *v) {
    uint32_t r = 0;
    for (int shift = 0; shift < 35; shift += 7) {
        if (*at >= n) {
            return -1;
        }
        uint8_t c = b[(*at)++];
        r |= (uint32_t)(c & 0x7Fu) << shift;
        if (!(c & 0x80u)) {
            *v = r;
            return 0;
        }
    }
    return -1;
}

static int limits(const uint8_t *b, size_t n, size_t *at) {
    uint32_t v;
    if (*at >= n) {
        return -1;
    }
    uint8_t flags = b[(*at)++];
    if (uleb(b, n, at, &v) != 0) {
        return -1;
    }
    return flags & 1u ? uleb(b, n, at, &v) : 0;
}

int moy_app_wasm_imports(const uint8_t *head, size_t n, moy_app_import_fn fn, void *ctx) {
    if (n < 8 || memcmp(head, "\0asm", 4) != 0) {
        return -1;
    }
    size_t at = 8;
    while (at < n) {
        uint8_t id = head[at++];
        uint32_t size;
        if (uleb(head, n, &at, &size) != 0) {
            return -1;
        }
        if (id != 2) {
            if (id > 2) {
                return 0;
            }
            at += size;
            continue;
        }
        uint32_t count;
        if (uleb(head, n, &at, &count) != 0) {
            return -1;
        }
        for (uint32_t i = 0; i < count; i++) {
            uint32_t mn, nn, t;
            if (uleb(head, n, &at, &mn) != 0 || mn > n - at) {
                return -1;
            }
            const uint8_t *mod = head + at;
            at += mn;
            if (uleb(head, n, &at, &nn) != 0 || nn > n - at) {
                return -1;
            }
            const uint8_t *name = head + at;
            at += nn;
            if (at >= n) {
                return -1;
            }
            uint8_t kind = head[at++];
            int bad = kind == 0 ? uleb(head, n, &at, &t)
                      : kind == 1 ? (at < n ? (at++, limits(head, n, &at)) : -1)
                      : kind == 2 ? limits(head, n, &at)
                      : kind == 3 ? (n - at >= 2 ? (at += 2, 0) : -1) : -1;
            if (bad) {
                return -1;
            }
            if (fn(ctx, mod, mn, name, nn) != 0) {
                return 1;
            }
        }
        return 0;
    }
    return 0;
}

typedef struct {
    char *err;
    size_t errlen;
} admit_t;

static int is_ext(const uint8_t *mod, size_t mn) {
    return mn == sizeof(MOY_APP_EXT) - 1u && memcmp(mod, MOY_APP_EXT, mn) == 0;
}

static int admit_one(void *ctx, const uint8_t *mod, size_t mn, const uint8_t *name,
                     size_t nn) {
    admit_t *c = ctx;
    if (!is_ext(mod, mn)) {
        return 0;
    }
    int row = moy_app_wasm_row_named((const char *)name, nn);
    if (s_a == NULL) {
        snprintf(c->err, c->errlen, "imports %s.%.*s, and this run has no app grant",
                 MOY_APP_EXT, (int)nn, (const char *)name);
        return 1;
    }
    if (row < 0) {
        snprintf(c->err, c->errlen, "imports %s.%.*s, which is not in its extension's table",
                 MOY_APP_EXT, (int)nn, (const char *)name);
        return 1;
    }
    int role = moy_app_table_role((uint32_t)row);
    int ungated = role == MOY_ROLE_THEME && moy_app_table_c_row((uint32_t)row) != MOY_APP_SHELL;
    if ((!ungated && !(s_roles & (1u << role))) || moy_app_holds(s_a, s_g, role) != MOY_APP_OK) {
        const char *perm = moy_app_perm_of(role);
        snprintf(c->err, c->errlen,
                 "imports %s.%.*s, and the manifest grants no %s (permission \"%s\")",
                 MOY_APP_EXT, (int)nn, (const char *)name, moy_app_role_name(role),
                 perm != NULL && *perm ? perm : "none");
        return 1;
    }
    return 0;
}

int moy_app_wasm_admit(const uint8_t *head, size_t n, char *err, size_t errlen) {
    admit_t c = { err, errlen };
    if (errlen) {
        err[0] = 0;
    }
    int r = moy_app_wasm_imports(head, n, admit_one, &c);
    if (r < 0) {
        snprintf(err, errlen, "the module's imports do not parse");
    }
    return r != 0;
}
