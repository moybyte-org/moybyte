// Conformance fixture `counter` (level 1), the compiled half: a count, an
// action, an action that is off while the count is 0, a toggle and the
// templates that show them, built on the UI tree engine through the uitree
// rows (sdk/include/moybyte/uitree.h). Its Python half is ../app.py, its Lua
// half ../counter.moy.
#include <stdint.h>

#include "moybyte/uitree.h"

#define EXPORT(name) __attribute__((export_name(#name)))

static int32_t s_count, s_loud, a_add, a_reset;

static void declare(void) {
    s_count = ut_state("count", UT_INT);
    s_loud = ut_state("loud", UT_BOOL);
    a_add = ut_action("add", "Add one");
    a_reset = ut_action_when("reset", "Reset", "count", UT_OFF_GREY);
    uitree_set_int(s_count, 0);
    uitree_set_int(s_loud, 0);
}

static void build(void) {
    uitree_build("1 counter", 9);
    int32_t page = ut_node(UT_PAGE, "page", -1);
    ut_prop(page, "title", "Counter");
    int32_t group = ut_node(UT_GROUP, "group#0", page);
    ut_prop(group, "caption", "Count");
    int32_t info = ut_node(UT_INFO, "info#0", group);
    ut_prop(info, "label", "Count");
    ut_prop(info, "value", "{count}");
    int32_t loud = ut_node(UT_TOGGLE, "loud", group);
    ut_prop(loud, "bind", "loud");
    ut_prop(loud, "label", "Loud");
    int32_t add = ut_node(UT_LINK, "add", group);
    ut_prop(add, "value", "");
    uitree_node_action(add, a_add);
    int32_t reset = ut_node(UT_LINK, "reset", group);
    ut_prop(reset, "value", "");
    uitree_node_action(reset, a_reset);
    int32_t label = ut_node(UT_LABEL, "label#4", group);
    ut_prop(label, "text", "{count|plural press,presses} so far, {loud?loud:quiet}");
    uitree_build_end();
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

// The actions the engine hands out, each run.
static void handlers(void) {
    int32_t ev[2];
    for (int32_t a; (a = uitree_next(ev, sizeof ev)) >= 0;) {
        if (a == a_add) {
            uitree_set_int(s_count, uitree_get_int(s_count) + 1);
        } else if (a == a_reset) {
            uitree_set_int(s_count, 0);
        }
    }
}

EXPORT(_draw) void _draw(void) {
    handlers();
    ui_bar("", 0, "", 0, 0);
    uitree_frame(0);
    handlers();
}
