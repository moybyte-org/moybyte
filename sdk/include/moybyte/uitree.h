// The UI tree for a compiled app in C (native/moy_uitree/moy_uitree_wasm.h
// states each row's contract): the engine's numbers and `ut_*` helpers that
// take C strings, over app.h's `uitree_*` imports. A page is built once with
// the engine's generic calls -- a node of a kind under a parent, its props by
// the spec's names, each value a literal, a template, a condition or a name
// as the spec has it -- and drawn each frame by the engine; the app runs a
// handler for each action `uitree_next` hands it:
//
//     static int s_count, a_add;
//     EXPORT(_init) void _init(void) {
//         s_count = ut_state("count", UT_INT);
//         a_add = ut_action("add", "Add one");
//         uitree_build("1 page", 6);
//         int p = ut_node(UT_PAGE, "page", -1);
//         ut_prop(p, "title", "Counter");
//         int l = ut_node(UT_LINK, "add", p);
//         uitree_node_action(l, a_add);
//         ut_prop(ut_node(UT_INFO, "info#0", p), "value", "{count}");
//         uitree_build_end();
//         uitree_open();
//     }
//     EXPORT(_draw) void _draw(void) {
//         int32_t ev[2];
//         uitree_frame(0);
//         for (int a; (a = uitree_next(ev, sizeof ev)) >= 0;)
//             if (a == a_add) uitree_set_int(s_count, uitree_get_int(s_count) + 1);
//     }
//
// The numbers are moy_uitree.h's, held equal to it by tests/test_uitree_wasm.py.

#ifndef MOYBYTE_UITREE_H
#define MOYBYTE_UITREE_H

#include <stdint.h>
#include <string.h>

#include "moybyte/app.h"

#ifdef __cplusplus
extern "C" {
#endif

// A state's type (uitree_state).
enum { UT_BOOL = 1, UT_INT, UT_NUM, UT_TEXT, UT_ENUM, UT_ROWOF, UT_MODEL };

// A node's kind (uitree_node).
enum {
    UT_PAGE = 1, UT_GROUP, UT_LABEL, UT_INFO, UT_LINK, UT_BUTTON, UT_TOGGLE,
    UT_ROWS, UT_DIALOG, UT_SEG, UT_SLIDER, UT_FIELD, UT_EMPTY, UT_PROGRESS, UT_TEXT_VIEW,
    UT_SCROLL, UT_MENU, UT_ITEM, UT_SPLIT, UT_CANVAS, UT_TABS, UT_SWITCH, UT_CASE, UT_TABLE,
    UT_COL, UT_FOOT, UT_CARDS, UT_CARD, UT_PICTURE, UT_POPOVER,
    UT_TOOLS, UT_ROW, UT_STATUS, UT_TEXT_EDITOR, UT_PANES, UT_PANE, UT_LEGACY, UT_SLOT,
    UT_PANEL, UT_LENT,
    UT_RULE, UT_NOTE,
    UT_GRID,
};

// An action's flags and how its nodes look while it is off (uitree_action).
enum { UT_A_HOOK = 1, UT_A_ROLE_CONFIRM = 2, UT_A_ROLE_BACK = 4, UT_A_ROW = 8, UT_A_SHIFT_ENTER = 16 };
enum { UT_OFF_NONE = 0, UT_OFF_HIDE = 1, UT_OFF_GREY = 2 };

// The refusals every uitree_* call may answer (uitree_error says which and where).
enum {
    UT_E_NAME = -1, UT_E_LANG = -2, UT_E_KIND = -3, UT_E_NOMEM = -4, UT_E_ARG = -5,
    UT_E_CHECK = -6, UT_E_BLOB = -7,
};

static inline uint32_t ut_len_(const char *s) {
    return s != NULL ? (uint32_t)strlen(s) : 0u;
}

// A state of `type` named `name`: its slot.
static inline int32_t ut_state(const char *name, int32_t type) {
    return uitree_state(name, ut_len_(name), type, NULL, 0);
}

// An action a person sees, named `name`, labelled `label`: its index.
static inline int32_t ut_action(const char *name, const char *label) {
    return uitree_action(name, ut_len_(name), label, ut_len_(label), NULL, 0, NULL, 0, UT_OFF_NONE, 0,
                         NULL, 0, NULL, 0);
}

// ...that is off while the condition `when` is false, looking as `off` says.
static inline int32_t ut_action_when(const char *name, const char *label, const char *when, int32_t off) {
    return uitree_action(name, ut_len_(name), label, ut_len_(label), NULL, 0, when, ut_len_(when), off, 0,
                         NULL, 0, NULL, 0);
}

// A handler no person sees, named `name` (`opened`: the engine's, run as the visit opens): its index.
static inline int32_t ut_hook(const char *name) {
    return uitree_action(name, ut_len_(name), "", 0, NULL, 0, NULL, 0, UT_OFF_NONE, UT_A_HOOK, NULL, 0, NULL, 0);
}

// A node of `kind` keyed `key` under node `parent` (-1 for the page): its index.
static inline int32_t ut_node(int32_t kind, const char *key, int32_t parent) {
    return uitree_node(kind, key, ut_len_(key), parent);
}

// Prop `name` of node `node`, its value's source `src`.
static inline int32_t ut_prop(int32_t node, const char *name, const char *src) {
    return uitree_prop(node, name, ut_len_(name), src, ut_len_(src));
}

// A text state's value.
static inline int32_t ut_set_text(int32_t slot, const char *s) {
    return uitree_set_text(slot, s, ut_len_(s));
}

#ifdef __cplusplus
}
#endif

#endif // MOYBYTE_UITREE_H
