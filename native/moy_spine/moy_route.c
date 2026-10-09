// Routing, the C twin (moy_route.h has the contract).

#include <string.h>

#include "moy_route.h"

static const char *const lease_tags[MOY_LEASE_TAGS] = {
    "web", "update", "settings", "cart", "link", "carts", "dev",
};

static int kind_ok(size_t n) {
    return n >= 1u && n <= MOY_ID_MAX;
}

static void kind_set(moy_kind_t *k, const char *s, size_t n) {
    k->len = (uint8_t)n;
    memset(k->s, 0, sizeof k->s);
    memcpy(k->s, s, n);
}

// -- the app registry --------------------------------------------------------

struct moy_apps {
    const moy_htab_mem_t *mem;
    moy_htab_t *t;
};

moy_apps_t *moy_apps_new(const moy_htab_mem_t *mem) {
    moy_apps_t *a = mem->alloc(sizeof(moy_apps_t));
    if (a == NULL) {
        return NULL;
    }
    a->mem = mem;
    a->t = moy_htab_new(mem, MOY_KIND_APP, MOY_APP_SLOTS, sizeof(moy_app_t));
    if (a->t == NULL) {
        mem->release(a, sizeof(moy_apps_t));
        return NULL;
    }
    return a;
}

void moy_apps_free(moy_apps_t *a) {
    if (a == NULL) {
        return;
    }
    for (uint32_t s = 0, n = moy_htab_slots(a->t); s < n; s++) {
        if (moy_htab_live(a->t, s)) {
            moy_app_t *app = moy_htab_row(a->t, s);
            a->mem->release(app->title, app->title_len + 1u);
        }
    }
    moy_htab_free(a->t);
    a->mem->release(a, sizeof(moy_apps_t));
}

void moy_apps_clear(moy_apps_t *a) {
    for (uint32_t s = 0, n = moy_htab_slots(a->t); s < n; s++) {
        if (moy_htab_live(a->t, s)) {
            moy_app_t *app = moy_htab_row(a->t, s);
            a->mem->release(app->title, app->title_len + 1u);
            app->title = NULL;
            moy_htab_release(a->t, moy_htab_handle(a->t, s));
        }
    }
}

uint32_t moy_apps_find(const moy_apps_t *a, const char *id, size_t id_len) {
    for (uint32_t s = 0, n = moy_htab_slots(a->t); s < n; s++) {
        if (moy_htab_live(a->t, s)
            && moy_kind_is(&((const moy_app_t *)moy_htab_row(a->t, s))->id, id,
                           id_len)) {
            return moy_htab_handle(a->t, s);
        }
    }
    return 0u;
}

int moy_apps_register(moy_apps_t *a, const char *id, size_t id_len,
                      const char *title, size_t title_len, int text_mode,
                      int has_min, int32_t min_w, int32_t min_h, uint32_t *h) {
    if (!kind_ok(id_len)) {
        return MOY_ROUTE_BAD;
    }
    if (moy_apps_find(a, id, id_len)) {
        return MOY_ROUTE_DUP;
    }
    if (moy_htab_full(a->t)) {
        return MOY_ROUTE_FULL;
    }
    if (title_len > 0x7fffffffu) {
        return MOY_ROUTE_NOMEM;
    }
    char *copy = a->mem->alloc(title_len + 1u);
    if (copy == NULL) {
        return MOY_ROUTE_NOMEM;
    }
    memcpy(copy, title, title_len);
    moy_app_t *app;
    int rc = moy_htab_add(a->t, h, (void **)&app);
    if (rc != MOY_HTAB_OK) {
        a->mem->release(copy, title_len + 1u);
        return rc;
    }
    kind_set(&app->id, id, id_len);
    app->text_mode = text_mode != 0;
    app->has_min = has_min != 0;
    app->min_w = app->has_min ? min_w : 0;
    app->min_h = app->has_min ? min_h : 0;
    app->title_len = (uint32_t)title_len;
    app->title = copy;
    return MOY_ROUTE_OK;
}

int moy_apps_get(const moy_apps_t *a, uint32_t h, const moy_app_t **app) {
    void *row;
    int rc = moy_htab_get(a->t, h, &row);
    if (rc == MOY_HTAB_OK) {
        *app = row;
    }
    return rc;
}

int moy_apps_valid(const moy_apps_t *a, uint32_t h) {
    return moy_htab_slot_of(a->t, h) != MOY_HTAB_NOSLOT;
}

uint32_t moy_apps_count(const moy_apps_t *a) {
    return moy_htab_count(a->t);
}

uint32_t moy_apps_slots(const moy_apps_t *a) {
    return moy_htab_slots(a->t);
}

uint32_t moy_apps_at(const moy_apps_t *a, uint32_t slot) {
    return moy_htab_at(a->t, slot);
}

// -- the back-stack ----------------------------------------------------------

struct moy_back {
    const moy_htab_mem_t *mem;
    uint32_t depth;
    moy_kind_t k[MOY_BACK_DEPTH];
};

moy_back_t *moy_back_new(const moy_htab_mem_t *mem) {
    moy_back_t *b = mem->alloc(sizeof(moy_back_t));
    if (b == NULL) {
        return NULL;
    }
    b->mem = mem;
    kind_set(&b->k[0], MOY_ROOT, sizeof MOY_ROOT - 1u);
    b->depth = 1u;
    return b;
}

void moy_back_reset(moy_back_t *b) {
    memset(b->k, 0, sizeof b->k);
    kind_set(&b->k[0], MOY_ROOT, sizeof MOY_ROOT - 1u);
    b->depth = 1u;
}

void moy_back_free(moy_back_t *b) {
    if (b != NULL) {
        b->mem->release(b, sizeof(moy_back_t));
    }
}

int moy_back_index(const moy_back_t *b, const char *kind, size_t n) {
    for (uint32_t i = 0; i < b->depth; i++) {
        if (moy_kind_is(&b->k[i], kind, n)) {
            return (int)i;
        }
    }
    return -1;
}

int moy_back_goto(moy_back_t *b, const char *kind, size_t n, int *answer) {
    if (!kind_ok(n)) {
        return MOY_ROUTE_BAD;
    }
    if (moy_kind_is(&b->k[b->depth - 1u], kind, n)) {
        *answer = MOY_GOTO_STAYED;
        return MOY_ROUTE_OK;
    }
    int at = moy_back_index(b, kind, n);
    if (at >= 0) {
        for (uint32_t i = (uint32_t)at + 1u; i < b->depth; i++) {
            memset(&b->k[i], 0, sizeof b->k[i]);
        }
        b->depth = (uint32_t)at + 1u;
        *answer = MOY_GOTO_RETURNED;
        return MOY_ROUTE_OK;
    }
    if (b->depth >= MOY_BACK_DEPTH) {
        return MOY_ROUTE_FULL;
    }
    kind_set(&b->k[b->depth++], kind, n);
    *answer = MOY_GOTO_PUSHED;
    return MOY_ROUTE_OK;
}

int moy_back_remove(moy_back_t *b, const char *kind, size_t n, int *removed) {
    if (!kind_ok(n)) {
        return MOY_ROUTE_BAD;
    }
    int at = moy_back_index(b, kind, n);
    *removed = 0;
    if (at <= 0) {                  // absent, or the root
        return MOY_ROUTE_OK;
    }
    for (uint32_t i = (uint32_t)at; i + 1u < b->depth; i++) {
        b->k[i] = b->k[i + 1u];
    }
    memset(&b->k[--b->depth], 0, sizeof b->k[0]);
    *removed = 1;
    return MOY_ROUTE_OK;
}

const moy_kind_t *moy_back_top(const moy_back_t *b) {
    return &b->k[b->depth - 1u];
}

uint32_t moy_back_depth(const moy_back_t *b) {
    return b->depth;
}

const moy_kind_t *moy_back_at(const moy_back_t *b, uint32_t depth) {
    return depth < b->depth ? &b->k[depth] : NULL;
}

// -- the return records ------------------------------------------------------

struct moy_returns {
    const moy_htab_mem_t *mem;
    const moy_apps_t *apps;
    moy_kind_t caller, back;        // len 0: none
};

moy_returns_t *moy_returns_new(const moy_htab_mem_t *mem,
                               const moy_apps_t *apps) {
    moy_returns_t *r = mem->alloc(sizeof(moy_returns_t));
    if (r != NULL) {
        r->mem = mem;
        r->apps = apps;
    }
    return r;
}

void moy_returns_reset(moy_returns_t *r) {
    memset(&r->caller, 0, sizeof r->caller);
    memset(&r->back, 0, sizeof r->back);
}

void moy_returns_free(moy_returns_t *r) {
    if (r != NULL) {
        r->mem->release(r, sizeof(moy_returns_t));
    }
}

int moy_returns_run(moy_returns_t *r, const char *kind, size_t n) {
    if (kind == NULL) {
        memset(&r->caller, 0, sizeof r->caller);
        return MOY_ROUTE_OK;
    }
    if (!kind_ok(n)) {
        return MOY_ROUTE_BAD;
    }
    kind_set(&r->caller, kind, n);
    return MOY_ROUTE_OK;
}

const moy_kind_t *moy_returns_caller(const moy_returns_t *r) {
    return r->caller.len ? &r->caller : NULL;
}

int moy_returns_spend(moy_returns_t *r, moy_kind_t *out) {
    int had = r->caller.len != 0u;
    *out = r->caller;
    memset(&r->caller, 0, sizeof r->caller);
    return had;
}

int moy_returns_route(const moy_returns_t *r, int windowed) {
    const moy_kind_t *c = &r->caller;
    if (windowed) {
        return MOY_ROUTE_WINDOW;
    }
    if (moy_kind_is(c, MOY_EDITOR, sizeof MOY_EDITOR - 1u)) {
        return MOY_ROUTE_EDITOR;
    }
    if (c->len && moy_apps_find(r->apps, c->s, c->len)) {
        return MOY_ROUTE_APP;
    }
    return MOY_ROUTE_HOME;
}

int moy_returns_note(moy_returns_t *r, const char *kind, size_t n, int *set) {
    if (!kind_ok(n)) {
        return MOY_ROUTE_BAD;
    }
    *set = moy_apps_find(r->apps, kind, n) != 0u;
    if (*set) {
        kind_set(&r->back, kind, n);
    }
    return MOY_ROUTE_OK;
}

const moy_kind_t *moy_returns_back(const moy_returns_t *r) {
    return r->back.len ? &r->back : NULL;
}

int moy_returns_take_back(moy_returns_t *r, moy_kind_t *out) {
    int had = r->back.len != 0u;
    *out = r->back;
    memset(&r->back, 0, sizeof r->back);
    return had;
}

// -- the WiFi leases ---------------------------------------------------------

struct moy_leases {
    const moy_htab_mem_t *mem;
    uint32_t mask;
};

moy_leases_t *moy_leases_new(const moy_htab_mem_t *mem) {
    moy_leases_t *l = mem->alloc(sizeof(moy_leases_t));
    if (l != NULL) {
        l->mem = mem;
    }
    return l;
}

void moy_leases_reset(moy_leases_t *l) {
    l->mask = 0;
}

void moy_leases_free(moy_leases_t *l) {
    if (l != NULL) {
        l->mem->release(l, sizeof(moy_leases_t));
    }
}

const char *moy_lease_tag(uint32_t i) {
    return i < MOY_LEASE_TAGS ? lease_tags[i] : NULL;
}

uint32_t moy_lease_bit(const char *tag, size_t n) {
    for (uint32_t i = 0; i < MOY_LEASE_TAGS; i++) {
        if (strlen(lease_tags[i]) == n && memcmp(lease_tags[i], tag, n) == 0) {
            return 1u << i;
        }
    }
    return 0u;
}

int moy_leases_hold(moy_leases_t *l, const char *tag, size_t n,
                    uint32_t *mask) {
    uint32_t bit = moy_lease_bit(tag, n);
    if (bit == 0u) {
        return MOY_ROUTE_BAD;
    }
    l->mask |= bit;
    *mask = l->mask;
    return MOY_ROUTE_OK;
}

int moy_leases_release(moy_leases_t *l, const char *tag, size_t n,
                       uint32_t *mask) {
    uint32_t bit = moy_lease_bit(tag, n);
    if (bit == 0u) {
        return MOY_ROUTE_BAD;
    }
    l->mask &= ~bit;
    *mask = l->mask;
    return MOY_ROUTE_OK;
}

uint32_t moy_leases_mask(const moy_leases_t *l) {
    return l->mask;
}

// -- the kernel's own tables ---------------------------------------------------

static moy_spine_kernel_t s_kernel;
static int s_kernel_made;

const moy_spine_kernel_t *moy_spine_kernel(const moy_htab_mem_t *mem) {
    if (s_kernel_made) {
        return &s_kernel;
    }
    if (mem == NULL) {
        return NULL;
    }
    moy_spine_kernel_t k;
    k.apps = moy_apps_new(mem);
    k.back = moy_back_new(mem);
    k.returns = k.apps != NULL ? moy_returns_new(mem, k.apps) : NULL;
    k.leases = moy_leases_new(mem);
    if (k.apps == NULL || k.back == NULL || k.returns == NULL || k.leases == NULL) {
        moy_returns_free(k.returns);
        moy_apps_free(k.apps);
        moy_back_free(k.back);
        moy_leases_free(k.leases);
        return NULL;
    }
    s_kernel = k;
    s_kernel_made = 1;
    return &s_kernel;
}
