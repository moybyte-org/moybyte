// Routing as a C ABI: the app registry, the back-stack, the return records and
// the WiFi leases.
//
// runtime/moy_spine.py's AppRegistry, BackStack, Returns and Leases are the
// interface and the reference; this header is their native form
// (docs/kernel_spine_2026-10.md section 3). Each holds KINDS, never objects: a
// kind is 1 .. MOY_ID_MAX bytes of any value, an app id or a back-stack kind,
// compared bytewise (the bindings pass a str's UTF-8). The bindings map the
// codes below to the exceptions the Python twin raises.
//
// Every byte comes from the moy_htab_mem_t the component is made with, and goes
// back through it: PSRAM on a board, malloc on the host, a failure-injecting
// counter under the fuzzer. No component keeps global state.

#ifndef MOY_ROUTE_H
#define MOY_ROUTE_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "moy_htab.h"

#define MOY_ID_MAX 15u
#define MOY_BACK_DEPTH 32u
#define MOY_APP_SLOTS 64u

enum {
    MOY_ROUTE_OK = MOY_HTAB_OK,
    MOY_ROUTE_STALE = MOY_HTAB_STALE,   // a handle that names no live app
    MOY_ROUTE_FULL = MOY_HTAB_FULL,     // the registry or the back-stack is full
    MOY_ROUTE_NOMEM = MOY_HTAB_NOMEM,   // the allocator refused; nothing changed
    MOY_ROUTE_DUP = 4,                  // an app with that id is registered
    MOY_ROUTE_BAD = 5,                  // a kind that is empty or too long
};

// A kind: its length (1 .. MOY_ID_MAX, 0 for none) and its bytes.
typedef struct {
    uint8_t len;
    char s[MOY_ID_MAX];
} moy_kind_t;

// 1 when `a` is the kind of `n` bytes at `s`.
static inline int moy_kind_is(const moy_kind_t *a, const char *s, size_t n) {
    return a->len == n && memcmp(a->s, s, n) == 0;
}

// -- the app registry --------------------------------------------------------

typedef struct {
    moy_kind_t id;
    uint8_t text_mode;
    uint8_t has_min;
    int32_t min_w, min_h;
    uint32_t title_len;
    char *title;            // title_len bytes, then a NUL
} moy_app_t;

typedef struct moy_apps moy_apps_t;

moy_apps_t *moy_apps_new(const moy_htab_mem_t *mem);    // or NULL
void moy_apps_free(moy_apps_t *a);                      // NULL is a no-op
// Every app unregistered and its title freed: the registry as moy_apps_new
// made it, at the same address (a kernel singleton the console re-registers
// at each VM start).
void moy_apps_clear(moy_apps_t *a);

// OK with the app's handle in `h`; BAD, DUP, FULL or NOMEM. The apps are
// numbered in registration order and never unregister.
int moy_apps_register(moy_apps_t *a, const char *id, size_t id_len,
                      const char *title, size_t title_len, int text_mode,
                      int has_min, int32_t min_w, int32_t min_h, uint32_t *h);
// The app's handle, or 0 when no app has that id.
uint32_t moy_apps_find(const moy_apps_t *a, const char *id, size_t id_len);
// OK, or STALE. The row stays valid while the registry lives.
int moy_apps_get(const moy_apps_t *a, uint32_t h, const moy_app_t **app);
int moy_apps_valid(const moy_apps_t *a, uint32_t h);
uint32_t moy_apps_count(const moy_apps_t *a);
uint32_t moy_apps_slots(const moy_apps_t *a);           // bounds the walk
uint32_t moy_apps_at(const moy_apps_t *a, uint32_t slot);

// -- the back-stack ----------------------------------------------------------

#define MOY_ROOT "launcher"

enum {
    MOY_GOTO_STAYED = 0,    // the kind was already the top
    MOY_GOTO_PUSHED = 1,    // it was not open: now the top
    MOY_GOTO_RETURNED = 2,  // it was open below: everything above it went
};

typedef struct moy_back moy_back_t;

moy_back_t *moy_back_new(const moy_htab_mem_t *mem);    // launcher at the root
void moy_back_free(moy_back_t *b);
void moy_back_reset(moy_back_t *b);                     // the launcher alone

// OK with MOY_GOTO_* in `answer`; BAD or FULL (a push at MOY_BACK_DEPTH).
int moy_back_goto(moy_back_t *b, const char *kind, size_t n, int *answer);
// OK with `removed` 1 when the kind was open and not the root; BAD.
int moy_back_remove(moy_back_t *b, const char *kind, size_t n, int *removed);
const moy_kind_t *moy_back_top(const moy_back_t *b);
uint32_t moy_back_depth(const moy_back_t *b);
const moy_kind_t *moy_back_at(const moy_back_t *b, uint32_t depth);
// `kind`'s depth from the root, or -1; a kind that cannot be on the stack is
// not.
int moy_back_index(const moy_back_t *b, const char *kind, size_t n);

// -- the return records ------------------------------------------------------

#define MOY_EDITOR "menu"

enum {
    MOY_ROUTE_HOME = 0,     // the launcher root, or back into the opening app
    MOY_ROUTE_EDITOR = 1,   // the Editor, on the tab it left
    MOY_ROUTE_APP = 2,      // the calling app's own surface
    MOY_ROUTE_WINDOW = 3,   // the windowed desk: close the player window only
};

typedef struct moy_returns moy_returns_t;

// The records over `apps`, which must outlive them.
moy_returns_t *moy_returns_new(const moy_htab_mem_t *mem,
                               const moy_apps_t *apps);
void moy_returns_free(moy_returns_t *r);
void moy_returns_reset(moy_returns_t *r);               // no caller, no back

// The kind a starting run returns to; `kind` NULL records none (home). BAD.
int moy_returns_run(moy_returns_t *r, const char *kind, size_t n);
const moy_kind_t *moy_returns_caller(const moy_returns_t *r);   // NULL: none
// The caller, cleared: 1 and `out` filled when there was one.
int moy_returns_spend(moy_returns_t *r, moy_kind_t *out);
// Where the recorded run's exit lands (MOY_ROUTE_*); it spends nothing.
int moy_returns_route(const moy_returns_t *r, int windowed);
// Record `kind` as the app a jump is leaving, when it is a registered app:
// OK with `set` 1 when it did; BAD.
int moy_returns_note(moy_returns_t *r, const char *kind, size_t n, int *set);
const moy_kind_t *moy_returns_back(const moy_returns_t *r);     // NULL: none
int moy_returns_take_back(moy_returns_t *r, moy_kind_t *out);

// -- the WiFi leases ---------------------------------------------------------

// A mask over a closed tag table, bit i the tag moy_lease_tag(i) names.
#define MOY_LEASE_TAGS 7u

typedef struct moy_leases moy_leases_t;

moy_leases_t *moy_leases_new(const moy_htab_mem_t *mem);
void moy_leases_free(moy_leases_t *l);
void moy_leases_reset(moy_leases_t *l);                 // nothing held

const char *moy_lease_tag(uint32_t i);                  // NULL past the table
uint32_t moy_lease_bit(const char *tag, size_t n);      // 0: no such tag
// OK with the mask after; BAD for a tag outside the table. A hold is
// idempotent, and releasing a tag nobody holds is not an error.
int moy_leases_hold(moy_leases_t *l, const char *tag, size_t n, uint32_t *mask);
int moy_leases_release(moy_leases_t *l, const char *tag, size_t n,
                       uint32_t *mask);
uint32_t moy_leases_mask(const moy_leases_t *l);

// -- the kernel's own tables --------------------------------------------------

// The console's registry, back-stack, return records and leases as kernel
// singletons (docs/kernel_cartpath_2026-10.md §5.4): made once, from `mem`, at
// the first call that passes one, and never freed, so a VM stop's sweep leaves
// them and a return start reads them. The VM's objects are views of these
// (modmoy_spine.c's kernel()). NULL `mem` answers them only once made: NULL
// before.
typedef struct {
    moy_apps_t *apps;
    moy_back_t *back;
    moy_returns_t *returns;
    moy_leases_t *leases;
} moy_spine_kernel_t;

const moy_spine_kernel_t *moy_spine_kernel(const moy_htab_mem_t *mem);

#endif // MOY_ROUTE_H
