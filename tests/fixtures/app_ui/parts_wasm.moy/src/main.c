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

// An override block (moy_ui_api.h, docs/ui_api_2026-10.md section 3) naming
// only the id: nine words, every key but the id's length absent (-1), then
// the id's bytes. Its length in over_n.
static uint8_t over_buf[128];
static uint32_t over_n;

static const void *over_id(const char *id, uint32_t n) {
    int32_t w[9] = {-1, -1, -1, (int32_t)n, -1, -1, -1, -1, -1};
    for (uint32_t i = 0; i < 36; i++) {
        over_buf[i] = (uint8_t)(w[i / 4] >> (8 * (i % 4)));
    }
    for (uint32_t i = 0; i < n; i++) {
        over_buf[36 + i] = (uint8_t)id[i];
    }
    over_n = 36 + n;
    return over_buf;
}

// An override block naming only `signal` (0..4 bars): the nine words, no
// hint (-1), the flags word's MOY_UI_O_SIGNAL and the signal's word.
static const void *over_signal(int32_t bars) {
    int32_t w[12] = {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 2097152, bars};
    for (uint32_t i = 0; i < 48; i++) {
        over_buf[i] = (uint8_t)(w[i / 4] >> (8 * (i % 4)));
    }
    over_n = 48;
    return over_buf;
}

// An override block naming only a list's `values` (NUL-separated) and
// `check`: the nine words, no hint, the flags word's MOY_UI_O_VALUES and
// MOY_UI_O_CHECK, the values' length and their text.
static const void *over_values(const char *v, uint32_t n) {
    int32_t w[12] = {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 4194304 | 8388608, (int32_t)n};
    for (uint32_t i = 0; i < 48; i++) {
        over_buf[i] = (uint8_t)(w[i / 4] >> (8 * (i % 4)));
    }
    for (uint32_t i = 0; i < n; i++) {
        over_buf[48 + i] = (uint8_t)v[i];
    }
    over_n = 48 + n;
    return over_buf;
}

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
    int32_t room[4] = {0, 0, 0, 0};
    ui_page(S("Parts"), NULL, 0, room, sizeof(room));
    if (page == 1) {
        ui_empty(S("bubble"), S("No messages yet"), S("Say hello to the mesh\0Messages show here"));
        ui_button(S("Write one"), 1, NULL, 0);
    } else if (page == 2) {
        int32_t v[5];
        int32_t in3[6] = {0, 3, 0, 75, 4, 0}, out0[6] = {1, -1, 0, 75, 4, 0};
        int32_t failed[6] = {1, -1, 1, 75, 4, 0}, cut[6] = {0, 1, 0, 50, 3, 1};
        ui_bubble(S("See you at the top"), S("Ana Ridge"), S(""), (const char *)in3, sizeof(in3), NULL, 0, v,
                  sizeof(v));
        ui_bubble(S("Ten minutes"), S(""), S("Sending\xe2\x80\xa6"), (const char *)out0, sizeof(out0), NULL, 0,
                  v, sizeof(v));
        ui_bubble(S("Wait for me at the gate"), S(""), S("Not sent"), (const char *)failed, sizeof(failed),
                  over_id(S("gate")), over_n, v, sizeof(v));
        ui_bubble(S("Clear skies up here, wind from the north. Bring a warm layer, the hut is cold after dark and the stove is out. Bring tea for the morning, and the long rope from the shed by the gate."), S("1E2F"), S(""), (const char *)cut, sizeof(cut), NULL, 0, v, sizeof(v));
        pmem(1, v[4] & 16, 1);
        ui_focus(S("gate"), NULL, 0);
    } else if (page == 3) {
        ui_group(S("Heard"), NULL, 0);
        ui_info(S("From"), S("Ana Ridge"), NULL, 0);
        ui_info(S("Signal"), S("-104 dBm"), over_signal(2), over_n);
        ui_info(S("SNR"), S("2.0 dB"), NULL, 0);
        ui_group("", 0, NULL, 0);
        ui_link(S("Base Camp"), S("-96 dBm"), over_signal(4), over_n);
    } else if (page == 4) {
        int32_t v[5];
        int32_t failed[6] = {1, -1, 1, 75, 4, 1};
        ui_bubble(S("Wait for me at the gate"), S(""), S("Not sent"), (const char *)failed,
                  sizeof(failed), NULL, 0, v, sizeof(v));
        ui_at(v[0], v[1], v[2], v[3]);
        ui_popover(S("details"), room[2] - 16, NULL, 0);
        ui_info(S("Channel"), S("LongFast"), NULL, 0);
        ui_info(S("Status"), S("Not sent"), NULL, 0);
        ui_button(S("Delete"), 3, NULL, 0);
        ui_button(S("Send again"), 1, NULL, 0);
        ui_popover("", 0, 0, NULL, 0);
        ui_focus(S("Send again"), NULL, 0);
    } else if (page == 5) {
        ui_list(S("Region"), S("EU_868\0US\0CN"), 1,
                over_values(S("Europe\0United States\0China")), over_n);
    }
}
