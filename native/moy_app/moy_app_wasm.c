// The app ABI's wasm import adapter (moy_app_wasm.h has the contract).

#include "moy_kmem.h"

#include "moy_platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_app_wasm.h"

#ifdef MOY_ON_BOARD
static void yield(void) {
    vTaskDelay(1);
}
#else
#include <sched.h>
static void yield(void) {
    sched_yield();
}
#endif

// The engine's hop to the VM's task (native/moy_wasm/moy_wasm_session.h):
// weak, so an image or a host with no session thread calls the row in place.
#if __has_include("moy_wasm_session.h") && __has_include("wasm_export.h")
#include "moy_wasm_session.h"
#else
int moy_wasm_on_vm(void (*fn)(void *arg), void *arg);
#endif
#pragma weak moy_wasm_on_vm

// How a native's row runs: hopped to the VM's task, read in place under the
// seqlock, or run in place over state that locks itself (a row that answers
// NEEDS_VM there is hopped).
enum { HOP = 0, SEQ = 1, HERE = 2 };

static moy_appabi_t *volatile s_live;   // the console's state: what the theme reads
static uint32_t s_hops, s_hop_us, s_hop_work_us, s_here;

void moy_app_wasm_live(moy_appabi_t *a) {
    if (a != NULL) {
        s_live = a;
    }
}

void moy_app_wasm_forget(const moy_appabi_t *a) {
    if (a != NULL && s_live == a) {
        s_live = NULL;
    }
}

// The run the engine's handle on a call is for: the engine glue's.
#pragma weak moy_app_wasm_run_of

static const moy_app_run_t *run_of(void *e) {
    const moy_app_run_t *r = moy_app_wasm_run_of != NULL ? moy_app_wasm_run_of(e) : NULL;
    return moy_app_run_live(r) ? r : NULL;
}

uint32_t moy_app_wasm_theme(int32_t *out, uint32_t have, char *name, uint32_t cap) {
    moy_appabi_t *a = s_live;
    if (a == NULL) {
        return 0u;
    }
    for (unsigned spins = 0;; spins++) {
        uint32_t q = moy_app_seq_begin(a);
        if (!(q & 1u)) {
            uint32_t gen = moy_app_theme_read_near(a, out) + 1u;
            gen = gen != 0u ? gen : 1u;
            if (gen != have && cap > 0u) {
                name[moy_app_name_read(a, name, cap - 1u)] = 0;
            }
            if (moy_app_seq_end(a, q)) {
                return gen;
            }
        }
        if (spins >= 64u) {
            yield();
        }
    }
}

void moy_app_wasm_hops(uint32_t *count, uint32_t *us, uint32_t *work_us, uint32_t *here) {
    *count = s_hops;
    *us = s_hop_us;
    *work_us = s_hop_work_us;
    *here = s_here;
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
    uint32_t t0 = (uint32_t)moy_now_us();
    h->r = moy_app_role(h->a, h->g, h->row, h->arg, h->n, h->ans, h->cap);
    h->work_us = (uint32_t)moy_now_us() - t0;
}

static void le32(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
    q[2] = (uint8_t)(v >> 16);
    q[3] = (uint8_t)(v >> 24);
}

// A number as a field: its four bytes, little-endian, in `q`.
#define NUM(q, v) { (le32((q), (uint32_t)(v)), (q)), 4u }

// Row `row` with its fields `f` (a text, a blob or a NUM each, in the row's
// order) packed as moy_app_role's, then run as `how` says.
static int32_t run(void *e, int how, int row, const span_t *f, int nf, uint8_t *ans,
                   uint32_t cap) {
    const moy_app_run_t *rn = run_of(e);
    if (rn == NULL || row < 0) {
        return -MOY_APP_STALE;
    }
    moy_appabi_t *a = rn->a;
    uint32_t g = rn->g;
    uint8_t stack[320];
    size_t n = 0;
    for (int i = 0; i < nf; i++) {
        n += 4u + f[i].n;
    }
    uint8_t *buf = n <= sizeof stack ? stack : moy_kalloc(n, MOY_KMEM_LIBC);
    if (buf == NULL) {
        return -MOY_APP_NOMEM;
    }
    size_t at = 0;
    for (int i = 0; i < nf; i++) {
        le32(buf + at, f[i].n);
        if (f[i].n) {
            memcpy(buf + at + 4, f[i].p, f[i].n);
        }
        at += 4u + f[i].n;
    }
    int32_t r = -MOY_APP_NEEDS_VM;
    if (how == HERE) {
        r = moy_app_role_here(a, g, (uint32_t)row, buf, n, ans, cap);
    }
    if (how == HERE && r != -MOY_APP_NEEDS_VM) {
        s_here++;
    } else if (how == SEQ) {
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
        uint32_t t0 = (uint32_t)moy_now_us();
        if (moy_wasm_on_vm(hop_fn, &h) != 0) {
            h.r = -MOY_APP_NEEDS_VM;
        }
        s_hop_us += (uint32_t)moy_now_us() - t0;
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
// '*~' a pointer the engine has checked and translated and its length. The
// natives and their table rows are generated from native/os_calls.json.

#if defined(__GNUC__)
#define FN(f) (__extension__ (void *)(f))
#else
#define FN(f) ((void *)(f))
#endif

// The toolkit's rows are native/moy_ui's (moy_ui_wasm.h), linked where the
// image carries the toolkit: weak here, so an image without it answers NULL
// for them.
#define UI_WEAK __attribute__((weak))

#include "moy_app_wasm_rows.h"

int moy_app_wasm_row(uint32_t i) {
    return i < MOY_APP_WASM_N ? NATIVE_ROWS[i] : -1;
}

const moy_app_native_t *moy_app_wasm_natives(uint32_t *count) {
    *count = MOY_APP_WASM_N;
    return NATIVES;
}

int moy_app_wasm_row_named(const char *name, size_t n) {
    for (int k = 0; k < MOY_APP_WASM_N; k++) {
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
    const moy_app_run_t *r;
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
    const moy_app_run_t *r = c->r;
    if (r == NULL) {
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
    if (role == MOY_ROLE_UI) {
        return 0;                   // the toolkit's rows: every compiled app's, no grant
    }
    // The console calls are every grant's (MOY_ROLE_FLOOR), each answered under
    // the permission it names; the theme's reads need none.
    int ungated = ((MOY_ROLE_FLOOR >> role) & 1u)
                  || (role == MOY_ROLE_THEME
                      && moy_app_table_c_row((uint32_t)row) != MOY_APP_SHELL);
    if ((!ungated && !(r->roles & (1u << role)))
        || moy_app_holds(r->a, r->g, role) != MOY_APP_OK) {
        const char *perm = moy_app_perm_of(role);
        snprintf(c->err, c->errlen,
                 "imports %s.%.*s, and the manifest grants no %s (permission \"%s\")",
                 MOY_APP_EXT, (int)nn, (const char *)name, moy_app_role_name(role),
                 perm != NULL && *perm ? perm : "none");
        return 1;
    }
    return 0;
}

int moy_app_wasm_admit(const moy_app_run_t *run, const uint8_t *head, size_t n, char *err,
                       size_t errlen) {
    admit_t c = { moy_app_run_live(run) ? run : NULL, err, errlen };
    if (errlen) {
        err[0] = 0;
    }
    int r = moy_app_wasm_imports(head, n, admit_one, &c);
    if (r < 0) {
        snprintf(err, errlen, "the module's imports do not parse");
    }
    return r != 0;
}
