// moy_play on the host, by ctypes (runtime/play_binding.py): the runtime
// map's rows a host image has, and the census's question of one cart folder.

#include <stdio.h>
#include <string.h>

#include "moy_play.h"
#include "moy_rt.h"

static const moy_rt_ops_t LUA = { "lua", NULL, NULL, false };
static const moy_rt_ops_t WASM = { "wasm", NULL, NULL, false };

// The host's rows: which of the two runtimes this image takes.
void moy_play_host_runtimes(int lua, int wasm) {
    moy_rt_remove("lua");
    moy_rt_remove("wasm");
    if (lua) {
        moy_rt_add(&LUA);
    }
    if (wasm) {
        moy_rt_add(&WASM);
    }
}

typedef struct {
    const char *path;
    char *out;
    size_t n;
} census_t;

static int one(void *ctx, const moy_cat_entry_t *e) {
    census_t *c = ctx;
    moy_play_census_line_at(c->path, e, c->out, c->n);
    return 0;
}

// "<runtime> <free|vm> <why>" for the cart folder at `path`; -1 when it is no
// cart.
int moy_play_census(const char *path, char *out, size_t n) {
    census_t c = { path, out, n };
    out[0] = 0;
    return moy_cat_entry(path, one, NULL, &c) == 0 && out[0] ? 0 : -1;
}

// The tick model's size, so the ctypes mirror of moy_tick_t refuses a layout
// that drifted from the header's.
#include "moy_tick.h"

size_t moy_play_tick_size(void) {
    return sizeof(moy_tick_t);
}
