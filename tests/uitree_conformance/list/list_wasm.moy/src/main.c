// Conformance fixture `list` (level 1), the compiled half: a model filled at
// open, rows with a cursor, a row action and a label it writes, built on the
// UI tree engine through the uitree rows (sdk/include/moybyte/uitree.h). Its
// Python half is ../app.py, its Lua half ../list.moy.
#include <stdint.h>

#include "moybyte/uitree.h"

#define EXPORT(name) __attribute__((export_name(#name)))

// the model's fields, in the engine's order (sorted): id (the key), name
enum { F_ID, F_NAME };

static int32_t s_items, s_msg, s_sel, a_opened, a_pick;

static void declare(void) {
    static const uint8_t types[] = { UT_TEXT, UT_TEXT };
    s_items = ut_state("items", UT_MODEL);
    s_msg = ut_state("msg", UT_TEXT);
    s_sel = ut_state("sel", UT_ROWOF);
    uitree_model(s_items, "id\0name", 7, types, sizeof types, F_ID);
    uitree_row_of(s_sel, s_items);
    a_opened = ut_hook("opened");
    a_pick = ut_action("pick", "Pick");
}

static void build(void) {
    uitree_build("1 shopping", 10);
    int32_t page = ut_node(UT_PAGE, "page", -1);
    ut_prop(page, "title", "Shopping");
    int32_t group = ut_node(UT_GROUP, "group#0", page);
    ut_prop(group, "caption", "Things");
    int32_t rows = ut_node(UT_ROWS, "items", group);
    ut_prop(rows, "bind", "items");
    ut_prop(rows, "cur", "sel");
    int32_t pick = ut_node(UT_LINK, "pick", rows);
    ut_prop(pick, "label", "{.name}");
    ut_prop(pick, "value", "");
    uitree_node_action(pick, a_pick);
    int32_t label = ut_node(UT_LABEL, "label#1", group);
    ut_prop(label, "text", "{msg}");
    uitree_build_end();
}

// The actions the engine hands out, each run.
static void handlers(void) {
    int32_t ev[2];
    for (int32_t a; (a = uitree_next(ev, sizeof ev)) >= 0;) {
        if (a == a_opened) {
            static const char rows[] = "a\0Apples\0b\0Bread\0c\0Cheese";
            uitree_set_model(s_items, 3, rows, sizeof rows);
        } else if (a == a_pick) {
            char msg[64] = "Picked ";
            int32_t n = uitree_get_cell(s_items, ev[1], F_NAME, msg + 7, sizeof msg - 7);
            uitree_set_text(s_msg, msg, (uint32_t)(7 + (n < (int32_t)sizeof msg - 7 ? n : (int32_t)sizeof msg - 7)));
        }
    }
}

// A responsive app: it draws in the shell's look on its window's surface.
EXPORT(_layout) void _layout(int32_t w, int32_t h, int32_t fs) {
    (void)w;
    (void)h;
    (void)fs;
}

EXPORT(_init) void _init(void) {
    declare();
    build();
    uitree_open();
}

EXPORT(_update) void _update(float dt) {
    (void)dt;
}

EXPORT(_draw) void _draw(void) {
    handlers();
    ui_bar("", 0, "", 0, 0);
    uitree_frame(0);
    handlers();
}
