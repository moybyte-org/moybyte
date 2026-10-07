// The glass's tables: OWNER, BUF, CANVAS and SURF rows, the pool and the
// surface gens (moy_buf.h, moy_canvas.h and moy_surface.h have the contracts).
//
// One state per image, made by moy_glass_init. The CANVAS table is reserved
// whole, so a canvas row never moves and the gates and the binding's views may
// hold its address.

#include <string.h>

#include "moy_buf.h"
#include "moy_canvas.h"
#include "moy_surface.h"

typedef struct {
    const moy_htab_mem_t *mem;
    const moy_glass_px_t *px;
    moy_htab_t *owners;
    moy_htab_t *bufs;
    moy_htab_t *canvases;
    moy_htab_t *surfs;
    uint32_t pool_bound;
    uint32_t pool_bytes;
    uint32_t pool_rows;
    uint32_t peak;
    uint32_t evictions;
    uint32_t gen;               // the surface mint
    uint32_t epoch;             // the set-level un-attributed gen
    uint32_t kepoch;            // the kernel's own draws
} glass_t;

static glass_t G;

int moy_glass_ready(void) {
    return G.bufs != NULL;
}

int moy_glass_init(const moy_htab_mem_t *mem, const moy_glass_px_t *px,
                   uint32_t pool_bound) {
    if (G.bufs != NULL) {
        return MOY_GLASS_OK;
    }
    memset(&G, 0, sizeof(G));
    G.mem = mem;
    G.px = px;
    G.pool_bound = pool_bound;
    G.gen = 1u;
    G.epoch = 1u;
    G.kepoch = 1u;
    G.owners = moy_htab_new(mem, MOY_KIND_OWNER, MOY_GLASS_ROWS,
                            sizeof(moy_owner_row_t));
    G.bufs = moy_htab_new(mem, MOY_KIND_BUF, MOY_GLASS_ROWS,
                          sizeof(moy_buf_row_t));
    G.canvases = moy_htab_new(mem, MOY_KIND_CANVAS, MOY_GLASS_ROWS,
                              sizeof(moy_canvas_row_t));
    G.surfs = moy_htab_new(mem, MOY_KIND_SURF, MOY_GLASS_ROWS,
                           sizeof(moy_surf_row_t));
    if (G.owners == NULL || G.bufs == NULL || G.canvases == NULL
        || G.surfs == NULL || moy_htab_reserve(G.canvases) != MOY_HTAB_OK) {
        moy_htab_free(G.owners);
        moy_htab_free(G.bufs);
        moy_htab_free(G.canvases);
        moy_htab_free(G.surfs);
        memset(&G, 0, sizeof(G));
        return MOY_GLASS_NOMEM;
    }
    return MOY_GLASS_OK;
}

static void free_px(moy_buf_row_t *r) {
    if (r->px != NULL) {
        G.px->release(r->px, r->nbytes);
        r->px = NULL;
    }
}

void moy_glass_deinit(void) {
    if (G.bufs == NULL) {
        return;
    }
    for (uint32_t s = 0; s < moy_htab_slots(G.bufs); s++) {
        if (moy_htab_live(G.bufs, s)) {
            free_px((moy_buf_row_t *)moy_htab_row(G.bufs, s));
        }
    }
    moy_htab_free(G.owners);
    moy_htab_free(G.bufs);
    moy_htab_free(G.canvases);
    moy_htab_free(G.surfs);
    memset(&G, 0, sizeof(G));
}

// -- owners ---------------------------------------------------------------------

int moy_owner_new(uint32_t *h, const char *tag, uint8_t cls) {
    void *row;
    int rc = moy_htab_add(G.owners, h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc;
    }
    moy_owner_row_t *o = row;
    size_t n = strlen(tag);
    memcpy(o->tag, tag, n > MOY_OWNER_TAG ? MOY_OWNER_TAG : n);
    o->cls = cls;
    return MOY_GLASS_OK;
}

int moy_owner_get(uint32_t h, moy_owner_row_t **row) {
    return moy_htab_get(G.owners, h, (void **)row);
}

int moy_owner_reclaim(uint32_t h, uint32_t roles) {
    moy_owner_row_t *o;
    if (h == 0u || moy_owner_get(h, &o) != MOY_HTAB_OK) {
        return MOY_GLASS_STALE;
    }
    for (uint32_t s = 0; s < moy_htab_slots(G.bufs) && o->loans; s++) {
        if (!moy_htab_live(G.bufs, s)) {
            continue;
        }
        moy_buf_row_t *r = moy_htab_row(G.bufs, s);
        if (r->owner == h && (roles == 0u || (roles & (1u << r->role)))) {
            moy_buf_release(moy_htab_handle(G.bufs, s));
        }
    }
    return MOY_GLASS_OK;
}

int moy_owner_end(uint32_t h) {
    int rc = moy_owner_reclaim(h, 0u);
    if (rc != MOY_GLASS_OK) {
        return rc;
    }
    return moy_htab_release(G.owners, h);
}

// -- buffers --------------------------------------------------------------------

static void note_peak(void) {
    uint32_t n = moy_htab_count(G.bufs);
    if (n > G.peak) {
        G.peak = n;
    }
}

static int add_row(uint32_t *h, moy_buf_row_t **out) {
    void *row;
    int rc = moy_htab_add(G.bufs, h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc;
    }
    *out = row;
    note_peak();
    return MOY_GLASS_OK;
}

// A pool row of exactly `nbytes`, or NOSLOT.
static uint32_t pool_find(uint32_t nbytes) {
    for (uint32_t s = 0; s < moy_htab_slots(G.bufs); s++) {
        if (moy_htab_live(G.bufs, s)) {
            moy_buf_row_t *r = moy_htab_row(G.bufs, s);
            if (r->role == MOY_ROLE_POOL && r->nbytes == nbytes) {
                return s;
            }
        }
    }
    return MOY_HTAB_NOSLOT;
}

static void pool_drop(uint32_t slot) {
    moy_buf_row_t *r = moy_htab_row(G.bufs, slot);
    G.pool_bytes -= r->nbytes;
    G.pool_rows--;
    free_px(r);
    moy_htab_release(G.bufs, moy_htab_handle(G.bufs, slot));
}

void moy_glass_evict(void) {
    for (uint32_t s = 0; G.pool_rows && s < moy_htab_slots(G.bufs); s++) {
        if (moy_htab_live(G.bufs, s)
            && ((moy_buf_row_t *)moy_htab_row(G.bufs, s))->role == MOY_ROLE_POOL) {
            pool_drop(s);
        }
    }
}

void moy_glass_set_pool_bound(uint32_t bytes) {
    G.pool_bound = bytes;
    for (uint32_t s = 0; G.pool_bytes > G.pool_bound && s < moy_htab_slots(G.bufs); s++) {
        if (moy_htab_live(G.bufs, s)
            && ((moy_buf_row_t *)moy_htab_row(G.bufs, s))->role == MOY_ROLE_POOL) {
            pool_drop(s);
        }
    }
}

static int owner_class(uint32_t owner, uint8_t *cls) {
    *cls = MOY_CLASS_KERNEL;
    if (owner == 0u) {
        return MOY_GLASS_OK;
    }
    moy_owner_row_t *o;
    if (moy_owner_get(owner, &o) != MOY_HTAB_OK) {
        return MOY_GLASS_STALE;
    }
    *cls = o->cls;
    return MOY_GLASS_OK;
}

static void lend(moy_buf_row_t *r, uint32_t owner, uint8_t cls) {
    r->owner = owner;
    r->cls = cls;
    if (owner != 0u) {
        moy_owner_row_t *o;
        if (moy_owner_get(owner, &o) == MOY_HTAB_OK) {
            o->loans++;
        }
    }
}

int moy_buf_new(uint32_t *h, size_t nbytes, uint8_t role, uint32_t owner) {
    uint8_t cls;
    if (nbytes == 0u || nbytes > 0x7fffffffu || role == 0u
        || role >= MOY_ROLE_POOL) {
        return MOY_GLASS_BAD;
    }
    if (owner_class(owner, &cls) != MOY_GLASS_OK) {
        return MOY_GLASS_STALE;
    }
    uint8_t *px = NULL;
    uint8_t origin = MOY_ORIGIN_ALLOC;
    uint32_t s = pool_find((uint32_t)nbytes);
    if (s != MOY_HTAB_NOSLOT) {
        // The pooled row goes and a fresh one takes its bytes, so a handle to
        // the buffer's last loan never validates against this one.
        moy_buf_row_t *p = moy_htab_row(G.bufs, s);
        px = p->px;
        p->px = NULL;
        G.pool_bytes -= p->nbytes;
        G.pool_rows--;
        moy_htab_release(G.bufs, moy_htab_handle(G.bufs, s));
        memset(px, 0, nbytes);
        origin = MOY_ORIGIN_POOL;
    } else {
        if (moy_htab_full(G.bufs)) {
            return MOY_GLASS_FULL;
        }
        px = G.px->alloc(nbytes);
        if (px == NULL && G.pool_rows) {
            G.evictions++;
            moy_glass_evict();
            px = G.px->alloc(nbytes);
        }
        if (px == NULL) {
            return MOY_GLASS_NOMEM;
        }
    }
    moy_buf_row_t *r;
    int rc = add_row(h, &r);
    if (rc != MOY_GLASS_OK) {
        G.px->release(px, nbytes);
        return rc;
    }
    r->px = px;
    r->nbytes = (uint32_t)nbytes;
    r->role = role;
    r->origin = origin;
    lend(r, owner, cls);
    return MOY_GLASS_OK;
}

int moy_buf_heap(uint32_t *h, size_t nbytes, uint8_t role, uint32_t owner) {
    uint8_t cls;
    if (nbytes > 0x7fffffffu || role == 0u || role >= MOY_ROLE_POOL) {
        return MOY_GLASS_BAD;
    }
    if (owner_class(owner, &cls) != MOY_GLASS_OK) {
        return MOY_GLASS_STALE;
    }
    moy_buf_row_t *r;
    int rc = add_row(h, &r);
    if (rc != MOY_GLASS_OK) {
        return rc;
    }
    r->nbytes = (uint32_t)nbytes;
    r->role = role;
    r->origin = MOY_ORIGIN_HEAP;
    lend(r, owner, cls);
    return MOY_GLASS_OK;
}

int moy_buf_get(uint32_t h, moy_buf_row_t **row) {
    if (G.bufs == NULL) {
        return MOY_GLASS_STALE;
    }
    return moy_htab_get(G.bufs, h, (void **)row);
}

int moy_buf_set_holder(uint32_t h, uint32_t holder) {
    moy_buf_row_t *r;
    if (moy_buf_get(h, &r) != MOY_HTAB_OK) {
        return MOY_GLASS_STALE;
    }
    r->holder = holder;
    return MOY_GLASS_OK;
}

int moy_buf_release(uint32_t h) {
    moy_buf_row_t *r;
    if (moy_buf_get(h, &r) != MOY_HTAB_OK || r->role == MOY_ROLE_POOL) {
        return MOY_GLASS_STALE;
    }
    if (r->owner != 0u) {
        moy_owner_row_t *o;
        if (moy_owner_get(r->owner, &o) == MOY_HTAB_OK && o->loans) {
            o->loans--;
        }
    }
    // A lent layer waits in the pool for the next of its size, while the pool
    // has room; a window, a bake, a scratch and a HEAP row are let go.
    int keep = r->px != NULL && r->owner != 0u && r->role == MOY_ROLE_LAYER
               && G.pool_bytes + r->nbytes <= G.pool_bound;
    if (!keep) {
        free_px(r);
        return moy_htab_release(G.bufs, h);
    }
    uint8_t *px = r->px;
    uint32_t n = r->nbytes;
    r->px = NULL;
    moy_htab_release(G.bufs, h);
    uint32_t ph;
    moy_buf_row_t *p;
    if (add_row(&ph, &p) != MOY_GLASS_OK) {     // the slot just freed: never
        G.px->release(px, n);
        return MOY_GLASS_OK;
    }
    p->px = px;
    p->nbytes = n;
    p->role = MOY_ROLE_POOL;
    p->origin = MOY_ORIGIN_POOL;
    p->cls = MOY_CLASS_KERNEL;
    G.pool_bytes += n;
    G.pool_rows++;
    return MOY_GLASS_OK;
}

uint32_t moy_buf_slots(void) {
    return G.bufs ? moy_htab_slots(G.bufs) : 0u;
}

uint32_t moy_buf_at(uint32_t slot) {
    return G.bufs ? moy_htab_at(G.bufs, slot) : 0u;
}

void moy_glass_stats(moy_glass_stats_t *out) {
    memset(out, 0, sizeof(*out));
    if (G.bufs == NULL) {
        return;
    }
    out->rows = moy_htab_count(G.bufs);
    out->peak = G.peak;
    out->pool_rows = G.pool_rows;
    out->pool_bytes = G.pool_bytes;
    out->pool_bound = G.pool_bound;
    out->owners = moy_htab_count(G.owners);
    out->evictions = G.evictions;
    for (uint32_t s = 0; s < moy_htab_slots(G.bufs); s++) {
        if (!moy_htab_live(G.bufs, s)) {
            continue;
        }
        moy_buf_row_t *r = moy_htab_row(G.bufs, s);
        if (r->role == MOY_ROLE_POOL) {
            continue;
        }
        if (r->origin == MOY_ORIGIN_HEAP) {
            out->heap_rows++;
            out->heap_bytes += r->nbytes;
        }
        if (r->cls == MOY_CLASS_KERNEL) {
            out->kernel_bytes += r->nbytes;
        } else {
            out->cart_bytes += r->nbytes;
        }
    }
    if (G.px->free_total != NULL) {
        out->px_free = (uint32_t)G.px->free_total();
    }
    if (G.px->largest != NULL) {
        out->px_largest = (uint32_t)G.px->largest();
    }
}

// -- canvases -------------------------------------------------------------------

int moy_canvas_new(uint32_t *h, uint16_t w, uint16_t h_px, uint32_t caps,
                   uint32_t owner) {
    void *row;
    int rc = moy_htab_add(G.canvases, h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc;
    }
    moy_canvas_row_t *c = row;
    c->w = w;
    c->h = h_px;
    c->caps = caps;
    c->owner = owner;
    c->st[MOY_ST_W] = w;
    c->st[MOY_ST_H] = h_px;
    c->st[MOY_ST_CX1] = w;
    c->st[MOY_ST_CY1] = h_px;
    c->st[MOY_ST_FONT_SCALE] = 1;
    return MOY_GLASS_OK;
}

int moy_canvas_get(uint32_t h, moy_canvas_row_t **row) {
    if (G.canvases == NULL) {
        return MOY_GLASS_STALE;
    }
    return moy_htab_get(G.canvases, h, (void **)row);
}

int moy_canvas_point(uint32_t h, uint16_t *px, uint32_t cap, uint32_t buf) {
    moy_canvas_row_t *c;
    if (moy_canvas_get(h, &c) != MOY_HTAB_OK) {
        return MOY_GLASS_STALE;
    }
    c->px = px;
    c->cap = cap;
    c->buf = buf;
    return MOY_GLASS_OK;
}

int moy_canvas_release(uint32_t h) {
    if (G.canvases == NULL) {
        return MOY_GLASS_STALE;
    }
    return moy_htab_release(G.canvases, h);
}

const moy_htab_t *moy_canvas_table(void) {
    return G.canvases;
}

uint32_t moy_canvas_word(uint32_t h) {
    uint32_t s = moy_htab_slot_of(G.canvases, h);
    return s == MOY_HTAB_NOSLOT ? 0u : G.canvases->meta[s];
}

uint32_t moy_canvas_count(void) {
    return G.canvases ? moy_htab_count(G.canvases) : 0u;
}

// -- surfaces -------------------------------------------------------------------

uint32_t moy_surface_mint(void) {
    G.gen = G.gen >= 0x7fffffffu ? 2u : G.gen + 1u;
    return G.gen;
}

uint32_t moy_surface_find(const char *sid) {
    if (G.surfs == NULL) {
        return 0u;
    }
    for (uint32_t s = 0; s < moy_htab_slots(G.surfs); s++) {
        if (moy_htab_live(G.surfs, s)
            && strncmp(((moy_surf_row_t *)moy_htab_row(G.surfs, s))->sid, sid,
                       MOY_SURF_SID + 1u) == 0) {
            return moy_htab_handle(G.surfs, s);
        }
    }
    return 0u;
}

int moy_surface_get(uint32_t *h, const char *sid, uint8_t domain) {
    size_t n = strlen(sid);
    if (n == 0u || n > MOY_SURF_SID) {
        return MOY_GLASS_BAD;
    }
    uint32_t f = moy_surface_find(sid);
    if (f != 0u) {
        *h = f;
        return MOY_GLASS_OK;
    }
    void *row;
    int rc = moy_htab_add(G.surfs, h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc;
    }
    moy_surf_row_t *r = row;
    memcpy(r->sid, sid, n);
    r->domain = domain;
    r->scale = 1u;
    r->content_gen = r->place_gen = moy_surface_mint();
    return MOY_GLASS_OK;
}

int moy_surface_row(uint32_t h, moy_surf_row_t **row) {
    if (G.surfs == NULL) {
        return MOY_GLASS_STALE;
    }
    return moy_htab_get(G.surfs, h, (void **)row);
}

void moy_surface_touch(uint32_t h) {
    moy_surf_row_t *r;
    if (moy_surface_row(h, &r) == MOY_HTAB_OK) {
        r->content_gen = moy_surface_mint();
    }
}

void moy_surface_move(uint32_t h) {
    moy_surf_row_t *r;
    if (moy_surface_row(h, &r) == MOY_HTAB_OK) {
        r->place_gen = moy_surface_mint();
    }
}

void moy_surface_animating(uint32_t h, bool on) {
    moy_surf_row_t *r;
    if (moy_surface_row(h, &r) == MOY_HTAB_OK) {
        r->animating = on ? 1u : 0u;
    }
}

void moy_surface_epoch(void) {
    G.epoch = moy_surface_mint();
}

uint32_t moy_surface_content_gen(uint32_t h) {
    moy_surf_row_t *r;
    uint32_t g = 0u;
    if (moy_surface_row(h, &r) == MOY_HTAB_OK) {
        g = r->content_gen;
    }
    return g > G.epoch ? g : G.epoch;
}

int moy_surface_drop(uint32_t h) {
    if (G.surfs == NULL) {
        return MOY_GLASS_STALE;
    }
    return moy_htab_release(G.surfs, h);
}

void moy_surface_sync(const char *const *alive, size_t n, const char *prefix) {
    size_t pn = strlen(prefix);
    for (uint32_t s = 0; G.surfs && s < moy_htab_slots(G.surfs); s++) {
        if (!moy_htab_live(G.surfs, s)) {
            continue;
        }
        moy_surf_row_t *r = moy_htab_row(G.surfs, s);
        if (strncmp(r->sid, prefix, pn) != 0) {
            continue;
        }
        size_t i = 0;
        while (i < n && strncmp(alive[i], r->sid, MOY_SURF_SID + 1u) != 0) {
            i++;
        }
        if (i == n) {
            moy_htab_release(G.surfs, moy_htab_handle(G.surfs, s));
        }
    }
}

uint32_t moy_surface_kernel_epoch(void) {
    return G.kepoch;
}

void moy_surface_kernel_bump(void) {
    G.kepoch = G.kepoch >= 0x7fffffffu ? 2u : G.kepoch + 1u;
}

uint32_t moy_surface_count(void) {
    return G.surfs ? moy_htab_count(G.surfs) : 0u;
}
