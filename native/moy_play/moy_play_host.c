// moy_play on the host, by ctypes (runtime/play_binding.py): the runtime
// map's rows a host image has, and the census's question of one cart folder.

#include <stdio.h>
#include <string.h>

#include "moy_json.h"
#include "moy_play.h"
#include "moy_rt.h"

static const moy_rt_ops_t LUA = { "lua", false };
static const moy_rt_ops_t WASM = { "wasm", false };

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
    char *out;
    size_t n;
} census_t;

static int one(void *ctx, const moy_cat_entry_t *e) {
    census_t *c = ctx;
    char rt[32] = "";
    if (e->runtime.v == NULL) {
        snprintf(rt, sizeof(rt), "%s", e->spec ? "lua" : "python");
    } else if (*e->runtime.v == '"' && moy_json_strlen(e->runtime.v, e->runtime.e) < sizeof(rt)) {
        rt[moy_json_str(e->runtime.v, e->runtime.e, rt)] = 0;
    } else {
        snprintf(rt, sizeof(rt), "?");
    }
    uint8_t why = 0;
    bool free_ = moy_play_vm_free(e, &why);
    snprintf(c->out, c->n, "%s %s %s", rt, free_ ? "free" : "vm", moy_play_why_name(why));
    return 0;
}

// "<runtime> <free|vm> <why>" for the cart folder at `path`; -1 when it is no
// cart.
int moy_play_census(const char *path, char *out, size_t n) {
    census_t c = { out, n };
    out[0] = 0;
    return moy_cat_entry(path, one, NULL, &c) == 0 && out[0] ? 0 : -1;
}

// The tick model's size, so the ctypes mirror of moy_tick_t refuses a layout
// that drifted from the header's.
#include "moy_tick.h"

size_t moy_play_tick_size(void) {
    return sizeof(moy_tick_t);
}
