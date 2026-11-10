// The responsive fixture app (docs/app_api_v1.md section 3) as a compiled
// app in C, built by tools/wasm_cart.py as a reactor: layout_py.moy's page,
// `_layout` an export. Into pmem 0: the calls `_layout` had heard when
// `_init` ran.
#include <stddef.h>
#include <stdint.h>

#include "moybyte/app.h"

#define EXPORT(name) __attribute__((export_name(#name)))

int32_t pmem(int32_t slot, int32_t v, int32_t write)
    __attribute__((import_module("moy"), import_name("pmem")));

static int32_t calls;
static char last[40] = "none";
static uint32_t last_n = 4;

// `v` in decimal at `out`: the characters written.
static uint32_t put_int(char *out, int32_t v) {
    char tmp[12];
    uint32_t n = 0, k = 0;
    uint32_t u = v < 0 ? (uint32_t)(-(int64_t)v) : (uint32_t)v;
    do {
        tmp[n++] = (char)('0' + u % 10u);
        u /= 10u;
    } while (u != 0u);
    if (v < 0) {
        out[k++] = '-';
    }
    while (n > 0u) {
        out[k++] = tmp[--n];
    }
    return k;
}

EXPORT(_layout) void _layout(int32_t w, int32_t h, int32_t fs) {
    calls++;
    uint32_t k = put_int(last, w);
    last[k++] = ' ';
    k += put_int(last + k, h);
    last[k++] = ' ';
    k += put_int(last + k, fs);
    last_n = k;
}

EXPORT(_init) void _init(void) {
    pmem(0, calls, 1);
}

EXPORT(_update) void _update(float dt) {
    (void)dt;
}

EXPORT(_draw) void _draw(void) {
    char line[24] = "calls ";
    uint32_t n = 6u + put_int(line + 6, calls);
    ui_page("Layout", 6, NULL, 0, NULL, 0);
    ui_label(line, n, NULL, 0);
    ui_label(last, last_n, NULL, 0);
}
