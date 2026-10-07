// The glass's tables under a random walk: buffers made, released and reclaimed
// by owners that begin and end, canvases and surfaces, with the pixel and the
// table allocators failing on a schedule. A model beside the C says what each
// handle must be; ASan and UBSan say what the C did to memory. A seeded driver
// (`fuzz_glass SEED STEPS`) or libFuzzer (`-DMOY_LIBFUZZER`) over the same step.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_buf.h"
#include "moy_canvas.h"
#include "moy_surface.h"

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "fuzz_glass: %s:%d %s\n", \
                                          __FILE__, __LINE__, #c); abort(); } } while (0)

static uint32_t rng;
static int fail_px, fail_tab;
static size_t px_live, px_peak_bytes;

static uint32_t next(void) {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

static void *tab_alloc(size_t n) {
    if (fail_tab && next() % 8u == 0u) {
        return NULL;
    }
    return calloc(1, n ? n : 1u);
}

static void tab_release(void *p, size_t n) {
    (void)n;
    free(p);
}

static void *px_alloc(size_t n) {
    if (fail_px && next() % 4u == 0u) {
        return NULL;
    }
    void *p = calloc(1, n);
    if (p != NULL) {
        px_live += n;
        if (px_live > px_peak_bytes) {
            px_peak_bytes = px_live;
        }
        memset(p, 0xa5, n);        // the glass must zero what it hands out
        memset(p, 0, n);
    }
    return p;
}

static void px_release(void *p, size_t n) {
    px_live -= n;
    free(p);
}

static const moy_htab_mem_t mem = { tab_alloc, tab_release };
static const moy_glass_px_t px = { px_alloc, px_release, NULL, NULL };

#define NB 64
#define NO 8

typedef struct {
    uint32_t h;
    uint32_t owner;
    uint32_t n;
    uint8_t role;
} mbuf_t;

static mbuf_t bufs[NB];
static uint32_t owners[NO];
static uint32_t canvases[8];
static uint32_t stale[NB];
static int nstale;

static void forget_owner_loans(uint32_t o) {
    for (int i = 0; i < NB; i++) {
        if (bufs[i].h && bufs[i].owner == o) {
            stale[nstale++ % NB] = bufs[i].h;
            bufs[i].h = 0;
        }
    }
}

static void check_model(void) {
    moy_glass_stats_t st;
    moy_glass_stats(&st);
    uint32_t live = 0;
    for (int i = 0; i < NB; i++) {
        if (bufs[i].h) {
            moy_buf_row_t *r;
            CHECK(moy_buf_get(bufs[i].h, &r) == MOY_GLASS_OK);
            CHECK(r->nbytes == bufs[i].n && r->owner == bufs[i].owner);
            CHECK(r->role == bufs[i].role);
            CHECK(r->origin == MOY_ORIGIN_HEAP || r->px != NULL);
            live++;
        }
    }
    for (int i = 0; i < NB; i++) {
        moy_buf_row_t *r;
        if (stale[i]) {
            CHECK(moy_buf_get(stale[i], &r) != MOY_GLASS_OK);
        }
    }
    CHECK(st.rows == live + st.pool_rows);
    CHECK(st.pool_bytes <= st.pool_bound);
    CHECK(st.rows <= MOY_GLASS_ROWS);
}

static void step(void) {
    uint32_t r = next();
    int i = (int)(r >> 8) % NB;
    int o = (int)(r >> 16) % NO;
    switch (r % 10u) {
        case 0: case 1: {                           // a buffer
            if (bufs[i].h) {
                break;
            }
            uint32_t n = 64u << (next() % 6u);
            uint8_t role = (uint8_t)(1u + next() % 5u);
            uint32_t own = (next() & 1u) ? owners[o] : 0u;
            uint32_t h;
            int rc = (next() % 16u == 0u) ? moy_buf_heap(&h, n, role, own)
                                          : moy_buf_new(&h, n, role, own);
            CHECK(rc == MOY_GLASS_OK || rc == MOY_GLASS_NOMEM || rc == MOY_GLASS_FULL);
            if (rc == MOY_GLASS_OK) {
                bufs[i] = (mbuf_t){ h, own, n, role };
                moy_buf_row_t *row;
                CHECK(moy_buf_get(h, &row) == MOY_GLASS_OK);
                if (row->px != NULL) {
                    for (uint32_t k = 0; k < n; k += 61u) {
                        CHECK(row->px[k] == 0u);
                    }
                    memset(row->px, (int)(r & 0xffu), n);
                }
            }
            break;
        }
        case 2: case 3:                             // give one back
            if (bufs[i].h) {
                CHECK(moy_buf_release(bufs[i].h) == MOY_GLASS_OK);
                CHECK(moy_buf_release(bufs[i].h) == MOY_GLASS_STALE);
                stale[nstale++ % NB] = bufs[i].h;
                bufs[i].h = 0;
            }
            break;
        case 4:                                     // an owner begins
            if (owners[o] == 0u) {
                uint32_t h;
                if (moy_owner_new(&h, "fuzz", MOY_CLASS_CART) == MOY_GLASS_OK) {
                    owners[o] = h;
                }
            }
            break;
        case 5:                                     // ...and ends
            if (owners[o] != 0u) {
                CHECK(moy_owner_end(owners[o]) == MOY_GLASS_OK);
                CHECK(moy_owner_end(owners[o]) == MOY_GLASS_STALE);
                forget_owner_loans(owners[o]);
                owners[o] = 0u;
            }
            break;
        case 6:                                     // a canvas
            if (canvases[o]) {
                CHECK(moy_canvas_release(canvases[o]) == MOY_GLASS_OK);
                uint32_t w = moy_canvas_word(canvases[o]);
                CHECK(w == 0u);
                canvases[o] = 0u;
            } else {
                uint32_t h;
                if (moy_canvas_new(&h, 32, 16, 0, 0) == MOY_GLASS_OK) {
                    canvases[o] = h;
                    uint32_t w = moy_canvas_word(h);
                    CHECK(moy_canvas_check(moy_canvas_table(), h & 0xffu, w) != NULL);
                }
            }
            break;
        case 7: {                                   // the surface table
            char sid[12];
            snprintf(sid, sizeof(sid), "win:%u", (unsigned)(r >> 20) % 12u);
            uint32_t h;
            if (moy_surface_get(&h, sid, 0) == MOY_GLASS_OK) {
                uint32_t g0 = moy_surface_content_gen(h);
                moy_surface_touch(h);
                CHECK(moy_surface_content_gen(h) != g0);
                if (next() & 1u) {
                    moy_surface_drop(h);
                    CHECK(moy_surface_find(sid) == 0u);
                }
            }
            if ((r >> 4) % 5u == 0u) {
                const char *alive[1] = { "win:3" };
                moy_surface_sync(alive, 1, "win:");
            }
            break;
        }
        case 8:                                     // the bound moves
            moy_glass_set_pool_bound((next() % 4u) * 4096u);
            break;
        default:                                    // reclaim a role
            if (owners[o] != 0u) {
                uint32_t roles = 1u << (1u + next() % 5u);
                CHECK(moy_owner_reclaim(owners[o], roles) == MOY_GLASS_OK);
                for (int k = 0; k < NB; k++) {
                    if (bufs[k].h && bufs[k].owner == owners[o]
                        && (roles & (1u << bufs[k].role))) {
                        stale[nstale++ % NB] = bufs[k].h;
                        bufs[k].h = 0;
                    }
                }
            }
            break;
    }
    check_model();
}

static void run(uint32_t seed, long steps) {
    rng = seed ? seed : 1u;
    fail_px = (int)(seed & 1u);
    fail_tab = 0;
    memset(bufs, 0, sizeof(bufs));
    memset(owners, 0, sizeof(owners));
    memset(canvases, 0, sizeof(canvases));
    memset(stale, 0, sizeof(stale));
    nstale = 0;
    CHECK(moy_glass_init(&mem, &px, 8192u) == MOY_GLASS_OK);
    for (long s = 0; s < steps; s++) {
        step();
    }
    moy_glass_deinit();
    CHECK(px_live == 0u);
}

#ifdef MOY_LIBFUZZER
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t n) {
    uint32_t seed = 1u;
    for (size_t i = 0; i < n && i < 4; i++) {
        seed = seed * 31u + data[i];
    }
    run(seed, 400);
    return 0;
}
#else
int main(int argc, char **argv) {
    uint32_t seed = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 1u;
    long steps = argc > 2 ? strtol(argv[2], NULL, 0) : 20000;
    long seeds = argc > 3 ? strtol(argv[3], NULL, 0) : 16;
    for (long k = 0; k < seeds; k++) {
        run(seed + (uint32_t)k, steps);
    }
    printf("fuzz_glass: %ld seeds x %ld steps, peak pixels %zu\n", seeds, steps,
           px_peak_bytes);
    return 0;
}
#endif
