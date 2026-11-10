// The ui parts an app composes a chat from (docs/ui_api_2026-10.md), one
// page each, as a compiled app in C built by tools/wasm_cart.py: parts_py.moy
// is the same app in Python, and tests/test_app_ui_parts.py holds the tiers
// to the same pixels. pmem 0 names the page the test asks for.
#include <stddef.h>
#include <stdint.h>

#include "moybyte/app.h"

#define EXPORT(name) __attribute__((export_name(#name)))
#define S(lit) lit, (uint32_t)(sizeof(lit) - 1)

int32_t pmem(int32_t slot, int32_t v, int32_t write)
    __attribute__((import_module("moy"), import_name("pmem")));

EXPORT(_layout) void _layout(int32_t w, int32_t h, int32_t fs) {
    (void)w;
    (void)h;
    (void)fs;
}

EXPORT(_init) void _init(void) {
}

EXPORT(_update) void _update(float dt) {
    (void)dt;
}

EXPORT(_draw) void _draw(void) {
    int32_t page = pmem(0, 0, 0);
    ui_page(S("Parts"), NULL, 0, NULL, 0);
    if (page == 1) {
        ui_empty(S("bubble"), S("No messages yet"), S("Say hello to the mesh\0Messages show here"));
        ui_button(S("Write one"), 1, NULL, 0);
    }
}
