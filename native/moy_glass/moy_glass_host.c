// The glass on the host: its allocators over the C library, and the calls the
// headers keep inline, for the ctypes binding (runtime/glass_binding.py) and the
// fuzz driver. Pixels come from calloc.

#include <stdlib.h>
#include <string.h>

#include "moy_buf.h"
#include "moy_canvas.h"
#include "moy_surface.h"

static size_t host_live_bytes;

static void *tab_alloc(size_t n) {
    return calloc(1, n ? n : 1u);
}

static void tab_release(void *p, size_t n) {
    (void)n;
    free(p);
}

static void *px_alloc(size_t n) {
    void *p = calloc(1, n);
    if (p != NULL) {
        host_live_bytes += n;
    }
    return p;
}

static void px_release(void *p, size_t n) {
    host_live_bytes -= n;
    free(p);
}

static size_t px_free_total(void) {
    return 0u;
}

static size_t px_largest(void) {
    return 0u;
}

static const moy_htab_mem_t host_mem = { tab_alloc, tab_release };
static const moy_glass_px_t host_px = { px_alloc, px_release, px_free_total,
                                        px_largest };

int moy_glass_host_init(uint32_t pool_bound) {
    return moy_glass_init(&host_mem, &host_px, pool_bound);
}

size_t moy_glass_host_live_bytes(void) {
    return host_live_bytes;
}

void *moy_glass_host_px(uint32_t h) {
    moy_buf_row_t *r;
    return moy_buf_get(h, &r) == MOY_GLASS_OK ? r->px : NULL;
}

int moy_glass_host_row(uint32_t h, uint32_t out[6]) {
    moy_buf_row_t *r;
    if (moy_buf_get(h, &r) != MOY_GLASS_OK) {
        return MOY_GLASS_STALE;
    }
    out[0] = r->nbytes;
    out[1] = r->role;
    out[2] = r->origin;
    out[3] = r->owner;
    out[4] = r->holder;
    out[5] = r->cls;
    return MOY_GLASS_OK;
}

void *moy_glass_host_canvas_row(uint32_t h) {
    moy_canvas_row_t *r;
    return moy_canvas_get(h, &r) == MOY_GLASS_OK ? (void *)r : NULL;
}

size_t moy_glass_host_canvas_state_offset(void) {
    return offsetof(moy_canvas_row_t, st);
}

size_t moy_glass_host_canvas_pal_offset(void) {
    return offsetof(moy_canvas_row_t, pal);
}

int moy_glass_host_surface_row(uint32_t h, int32_t out[9]) {
    moy_surf_row_t *r;
    if (moy_surface_row(h, &r) != MOY_GLASS_OK) {
        return MOY_GLASS_STALE;
    }
    out[0] = (int32_t)r->content_gen;
    out[1] = (int32_t)r->place_gen;
    out[2] = r->x;
    out[3] = r->y;
    out[4] = r->w;
    out[5] = r->h;
    out[6] = r->z;
    out[7] = r->scale;
    out[8] = r->animating;
    return MOY_GLASS_OK;
}

int moy_glass_host_surface_place(uint32_t h, int x, int y, int w, int hh, int z) {
    moy_surf_row_t *r;
    if (moy_surface_row(h, &r) != MOY_GLASS_OK) {
        return -1;
    }
    if (r->x == x && r->y == y && r->w == w && r->h == hh && r->z == z) {
        return 0;
    }
    r->x = (int16_t)x;
    r->y = (int16_t)y;
    r->w = (uint16_t)w;
    r->h = (uint16_t)hh;
    r->z = (int16_t)z;
    moy_surface_move(h);
    return 1;
}

// -- the present engines over a fake transport (tests/test_banded_panel.py) -----
//
// Every transport call is appended to a log the test reads back, so a test
// asserts the ORDER the engine drives the panel in, as the board's would see it.

#include <stdio.h>

#include "moy_present.h"

static char host_log[4096];
static size_t host_log_n;
static int host_kick_err;

static void host_note(const char *fmt, int n) {
    if (host_log_n < sizeof(host_log) - 16u) {
        host_log_n += (size_t)snprintf(host_log + host_log_n,
                                       sizeof(host_log) - host_log_n, fmt, n);
    }
}

static bool fake_wait(void *ctx) {
    (void)ctx;
    host_note("drain ", 0);
    return true;
}

static int fake_kick(void *ctx, int n) {
    (void)ctx;
    host_note("kick%d ", n);
    return host_kick_err;
}

static int fake_ship(void *ctx, int n) {
    (void)ctx;
    host_note("show%d ", n);
    return 0;
}

static const moy_banded_ops_t fake_ops = { fake_wait, fake_kick, fake_ship, NULL };
static const moy_banded_ops_t fake_ops_nokick = { fake_wait, NULL, fake_ship, NULL };
static moy_banded_t host_banded;

void moy_glass_host_banded_init(int nfbs, int overlap, int has_kick) {
    host_log_n = 0;
    host_log[0] = '\0';
    host_kick_err = 0;
    moy_banded_init(&host_banded, has_kick ? &fake_ops : &fake_ops_nokick, nfbs,
                    overlap != 0);
}

int moy_glass_host_banded_present(void) {
    return moy_banded_present(&host_banded);
}

int moy_glass_host_banded_fence(void) {
    return moy_banded_fence(&host_banded);
}

int moy_glass_host_banded_back(void) {
    return host_banded.back;
}

int moy_glass_host_banded_overlap(void) {
    return host_banded.overlap;
}

void moy_glass_host_kick_err(int e) {
    host_kick_err = e;
}

// The log so far, then cleared.
const char *moy_glass_host_log(void) {
    static char out[sizeof(host_log)];
    memcpy(out, host_log, host_log_n + 1u);
    host_log_n = 0;
    host_log[0] = '\0';
    return out;
}
