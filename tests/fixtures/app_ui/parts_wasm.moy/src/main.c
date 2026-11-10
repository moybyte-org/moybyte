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
static uint8_t over_buf[512];
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

// An override block naming only the flags word `fl` (MOY_UI_O_*): the nine
// words, no hint, the flags.
static const void *over_flags(int32_t fl) {
    int32_t w[11] = {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, fl};
    for (uint32_t i = 0; i < 44; i++) {
        over_buf[i] = (uint8_t)(w[i / 4] >> (8 * (i % 4)));
    }
    over_n = 44;
    return over_buf;
}

// An override block built a word at a time: over_begin's nine words (every
// key absent), no hint and the flags word `fl` (MOY_UI_O_*), then each word
// or text (its length's word and its bytes) the flags name, in their order.
static void over_word(int32_t v) {
    for (uint32_t i = 0; i < 4; i++) {
        over_buf[over_n++] = (uint8_t)(v >> (8 * i));
    }
}

static void over_begin(int32_t fl) {
    over_n = 0;
    for (int i = 0; i < 10; i++) {
        over_word(-1);
    }
    over_word(fl);
}

static void over_text(const char *t, uint32_t n) {
    over_word((int32_t)n);
    for (uint32_t i = 0; i < n; i++) {
        over_buf[over_n++] = (uint8_t)t[i];
    }
}

// A field's block: its id (NULL none), its hint and `disabled` (-1 absent).
static void over_field(const char *id, uint32_t idn, const char *hint, uint32_t hn,
                       int32_t disabled) {
    int32_t w[9] = {-1, -1, -1, id ? (int32_t)idn : -1, -1, disabled, -1, -1, -1};
    over_n = 0;
    for (int i = 0; i < 9; i++) {
        over_word(w[i]);
    }
    for (uint32_t i = 0; id && i < idn; i++) {
        over_buf[over_n++] = (uint8_t)id[i];
    }
    over_text(hint, hn);
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
    } else if (page == 6) {
        int32_t v[5];
        int32_t in3[6] = {0, 3, 0, 75, 4, 0}, failed[6] = {1, -1, 1, 75, 4, 1};
        ui_label(S("LongFast"), NULL, 0);
        ui_scroll(S("history"), NULL, 0);
        ui_bubble(S("See you at the top"), S("Ana Ridge"), S(""), (const char *)in3, sizeof(in3), NULL, 0,
                  v, sizeof(v));
        ui_bubble(S("Wait for me at the gate"), S(""), S("Not sent"), (const char *)failed,
                  sizeof(failed), NULL, 0, v, sizeof(v));
        ui_at(v[0], v[1], v[2], v[3]);
        ui_scroll("", 0, NULL, 0);
        // wash "view": MOY_UI_O_WASH | MOY_UI_O_WASH_VIEW
        ui_popover(S("details"), room[2] - 16, over_flags(1 | 16777216), over_n);
        ui_info(S("Status"), S("Not sent"), NULL, 0);
        ui_button(S("Send again"), 1, NULL, 0);
        ui_popover("", 0, 0, NULL, 0);
        ui_focus(S("Send again"), NULL, 0);
    } else if (page == 5) {
        ui_list(S("Region"), S("EU_868\0US\0CN"), 1,
                over_values(S("Europe\0United States\0China")), over_n);
    } else if (page == 7) {
        over_begin(67108864);               // MOY_UI_O_CARD
        ui_group("", 0, over_buf, over_n);
        // MOY_UI_O_VALUES, MOY_UI_O_ROWS, then all five of MOY_UI_OR_*
        over_begin(4194304 | 33554432);
        over_text(S("2 min\0" "20 min\0" "4 min\0" "1 h"));
        over_word(31);
        over_text(S("Base Camp: Gate opens at seven\0Milo's Deck: Snow above the hut\0"
                    "Bring the spare headlamp, mine is dead\0You: Back by noon"));
        over_text(S("bubble\0bubble\0@Ana Ridge\0@base camp"));
        over_text(S("0\0" "3\0" "2\0"));
        over_text(S("Public\0\0\0"));
        over_text(S("\0lock\0lock\0lock"));
        ui_list("", 0, S("Srbija\0Planinari\0Ana Ridge\0Base Camp"), -1, over_buf, over_n);
        ui_group("", 0, NULL, 0);
        ui_link(S("Nodes"), S("4 heard"), NULL, 0);
    } else if (page == 8) {
        over_begin(2048);                   // MOY_UI_O_SNAP
        ui_scroll(S("radio"), over_buf, over_n);
        ui_group(S("This node"), NULL, 0);
        ui_info(S("Name"), S("moybyte-beef"), NULL, 0);
        ui_info(S("Short name"), S("BEEF"), NULL, 0);
        ui_info(S("Id"), S("!A1B2BEEF"), NULL, 0);
        ui_group(S("Mesh"), NULL, 0);
        ui_link(S("Preset"), S("MediumFast"), NULL, 0);
        ui_link(S("Region"), S("EU_868"), NULL, 0);
        ui_group(S("Channels"), NULL, 0);
        over_begin(4194304);                // MOY_UI_O_VALUES
        over_text(S("Primary\0" "256-bit key\0" "128-bit key\0No key\0" "256-bit key"));
        ui_list("", 0, S("LongFast\0Planinari\0Base\0Open\0Family"), 0, over_buf, over_n);
        ui_group("", 0, NULL, 0);
        ui_scroll("", 0, NULL, 0);
    } else if (page == 9) {
        ui_note(S("warn"), S("Every radio on a mesh uses one preset.\0"
                             "On another one you hear none of them."));
        over_begin(134217728);              // MOY_UI_O_TAG
        over_text(S("Public"));
        ui_link(S("LongFast"), S(""), over_buf, over_n);
        ui_group(S("Channel"), NULL, 0);
        over_begin(134217728);
        over_text(S("Private"));
        ui_info(S("Key"), S("256-bit"), over_buf, over_n);
        ui_group("", 0, NULL, 0);
        ui_note(S("paste"), S("Your clipboard holds a channel link."));
    } else if (page == 10) {
        static char name[39] = "moybyte-beef", shrt[4] = "BEEF", msg[200] = "Headed up the ridge trail now, the weather is turning";
        static char off[200];
        ui_group(S("This node"), NULL, 0);
        ui_field(S("Name"), name, sizeof(name), 12, NULL, 0);
        ui_field(S("Short name"), shrt, sizeof(shrt), 4, NULL, 0);
        ui_info(S("Id"), S("!A1B2BEEF"), NULL, 0);
        ui_group("", 0, NULL, 0);
        over_field(NULL, 0, S("Replaced by Srbija"), 1);
        ui_field("", 0, off, sizeof(off), 0, over_buf, over_n);
        over_field(S("long"), S("Message"), -1);
        ui_field("", 0, msg, sizeof(msg), 53, over_buf, over_n);
        ui_focus(S("long"), NULL, 0);
    } else if (page == 11) {
        ui_label(S("Behind"), NULL, 0);
        over_begin(268435456 | 8);          // MOY_UI_O_FOCUS, and no wash given
        over_text(S("Cancel"));
        ui_dialog(S("Switch to ShortFast?"), S("Cancel\0Switch"), 1, over_buf, over_n);
        ui_label(S("You hear only radios on ShortFast."), NULL, 0);
        ui_toggle(S("Keep channels"), 1, NULL, 0);
        ui_dialog("", 0, "", 0, 0, NULL, 0);
    } else if (page == 12) {
        char name[64];
        int32_t n = moybyte_name(name, sizeof(name));
        ui_info(S("Name"), name, n > 0 ? (uint32_t)n : 0, NULL, 0);
    } else if (page == 13) {
        static char pasted[8] = "ab";
        static int32_t pn = 2;
        over_field(S("paste"), NULL, 0, -1);
        pn = ui_field(S("Name"), pasted, sizeof(pasted), pn, over_buf, over_n) & 0xffff;
        ui_focus(S("paste"), NULL, 0);
        pmem(1, pn, 1);
    }
}
