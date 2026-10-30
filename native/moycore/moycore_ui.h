/* The public UI API's Lua face (docs/ui_api_2026-10.md): the vendor
 * extension `moybyte.ui`, the table `ui` a Lua cart may use when the host has
 * it (`if ui then`, moy-spec SPEC.md §10). Its verbs are the RUN face's
 * (native/moy_ui/moy_ui_wasm.h), drawing into the canvas the cart's own verbs
 * draw on, over the run's one context, so a page here moves focus and draws
 * exactly as the Python face's and a compiled app's do.
 *
 *   ui.page(title, o)              -> x, y, w, h (the content rect)
 *   ui.label(text, o)
 *   ui.button(label, kind, o)      -> true the frame it is activated
 *   ui.toggle(label, on, o)        -> the new on
 *   ui.slider(label, v, lo, hi, o) -> the new v
 *   ui.list(label, items, cur, o)  -> the new cur (from 1; 0 is none)
 *   ui.group(caption, o)           (ui.group() ends the group)
 *   ui.info(label, value, o)
 *   ui.link(label, value, o)       -> true the frame it is activated
 *   ui.cycle(label, items, cur, o) -> the new cur (from 1; 0 is none)
 *   ui.seg(label, items, cur, o)   -> the new cur
 *   ui.swatches(label, colours, cur, o) -> the new cur (colours 0xRRGGBB)
 *   ui.hints(items, o)             (an action or keys, then what it does, alternating)
 *   ui.icon(name, o)               -> whether `name` names an icon (the next row's)
 *   ui.progress(label, v, hi, o)   (v of hi done)
 *   ui.table(label, columns, rows, cur, o) -> the new cur (from 1; 0 is none), opened
 *   ui.table_part()                -> the next part's row (from 1), x, y, w, h of its
 *                                    cell, the canvas clipped to it; nil after the last
 *   ui.table_dist()                -> that part's distance from the view's centre along
 *                                    the table's axis, in 256ths of a row
 *   ui.scroll(id, o), ui.scroll()  (a scroll region; ui.scroll() ends it)
 *   ui.field(label, text, max, o)  -> the new text, done (Enter pressed in it)
 *   ui.text_view(id, lines, o)     -> the first line shown, the rows shown
 *   ui.dialog(title, buttons, kind, o) -> the button activated (from 1; 0 none);
 *                                    ui.dialog() ends its body
 *   ui.popover(id, w, o)           -> true, false the frame it is dismissed;
 *                                    ui.popover() ends its body
 *   ui.menu(label, items, icons, o) -> the item activated (from 1; 0 none)
 *   ui.tool(icon, label, o)        -> true the frame it is activated
 *   ui.bar(title, tools, off, back) -> the tool the host's bar activated (from 1; 0
 *                                    none; -1 its way back, where `back`); off[i] true
 *                                    draws tool i at half strength
 *   ui.tabs(label, items, cur, o)  -> the new cur (from 1)
 *   ui.grid(id, w, h, o), ui.grid() (a grid of cards w x h; ui.grid() ends it)
 *   ui.card(title, sub, o)         -> x, y, w, h, state (the picture's rect)
 *   ui.rule()                      (a divider across ui.at's rect, else a row)
 *   ui.part(id, w, h, o)           -> x, y, w, h, state
 *   ui.part_at()                   -> x, y where the pointer is inside the part
 *                                    answered last (the activating tap's release, or
 *                                    pressing or hovering it), from its top-left; nil
 *   ui.split(id, dir, sizes, o, out) -> the focused pane (from 1; 0 none, or no
 *                                    o.focus); dir "x" or "y"; out[4i-3..4i] pane
 *                                    i's x, y, w, h, then the divider held or let go
 *                                    (from 1; 0 none), where its gap is dragged to,
 *                                    the side it stopped at (-1 none, 0 before, 1
 *                                    after) and its bits (1 live, 2 let go); o: drag,
 *                                    focus (from 1), gap, slop, line, hit {w, l},
 *                                    handle {w, l}, mins
 *   ui.focus(id), ui.focus()       -> the focused id, or nil
 *   ui.keys_taken()                -> whether the ui used this frame's keys
 *   ui.at(x, y, w, h)
 *
 * `o` is a table of §3's keys: fill, ink, border (a role name or a colour
 * 0xRRGGBB), id, current, disabled, nav, mark, steps, hint, wash, lead, part
 * (a table's {w, h}) and bar.
 *
 * Included by native/moycore/moycore_lua.c and opened beside the superset
 * when the build has native/moy_ui (MOYCORE_UI). */
#ifndef MOYCORE_UI_H
#define MOYCORE_UI_H

#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy_ui_wasm.h"
#include "moycore_lua.h"

static void *moycore_ui_canvas(void)
{
    return moycore_RUN.c.con.canvas;
}

static moy_ui_span_t moycore_ui_span(lua_State *L, int i)
{
    size_t n = 0;
    const char *s = lua_isnoneornil(L, i) ? "" : luaL_checklstring(L, i, &n);
    moy_ui_span_t t = { (const uint8_t *)s, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n) };
    return t;
}

static int32_t moycore_ui_colour(lua_State *L, int t, const char *key)
{
    int32_t v = MOY_UI_O_NONE;
    lua_getfield(L, t, key);
    if (lua_type(L, -1) == LUA_TSTRING) {
        const char *name = lua_tostring(L, -1);
        for (int i = 0; i < MOY_UI_TOKENS; i++) {
            if (strcmp(name, moy_ui_token_names[i]) == 0) {
                v = MOY_UI_O_ROLE + i;
                break;
            }
        }
    } else if (lua_isinteger(L, -1)) {
        v = (int32_t)(lua_tointeger(L, -1) & 0xffffff);
    }
    lua_pop(L, 1);
    return v;
}

static int8_t moycore_ui_flag(lua_State *L, int t, const char *key)
{
    lua_getfield(L, t, key);
    int8_t v = lua_isnil(L, -1) ? -1 : (int8_t)(lua_toboolean(L, -1) ? 1 : 0);
    lua_pop(L, 1);
    return v;
}

/* The override table at stack index `i` into *o. An `id` and a `hint` string
 * are left on the stack, so the spans stay valid for the call. */
static const moy_ui_over_t *moycore_ui_over(lua_State *L, int i, moy_ui_over_t *o)
{
    moy_ui_over_none(o);
    if (lua_type(L, i) != LUA_TTABLE) return o;
    o->fill = moycore_ui_colour(L, i, "fill");
    o->ink = moycore_ui_colour(L, i, "ink");
    o->border = moycore_ui_colour(L, i, "border");
    o->current = moycore_ui_flag(L, i, "current");
    o->disabled = moycore_ui_flag(L, i, "disabled");
    o->nav = moycore_ui_flag(L, i, "nav");
    o->mark = moycore_ui_flag(L, i, "mark");
    o->steps = moycore_ui_flag(L, i, "steps");
    o->wash = moycore_ui_flag(L, i, "wash");
    o->lead = moycore_ui_flag(L, i, "lead");
    o->bar = moycore_ui_flag(L, i, "bar");
    o->snap = (int8_t)(moycore_ui_flag(L, i, "snap") == 1);
    lua_getfield(L, i, "axis");
    if (lua_type(L, -1) == LUA_TSTRING) {
        size_t n = 0;
        const char *a = lua_tolstring(L, -1, &n);
        o->axis_x = (int8_t)(n == 1 && a[0] == 'x');
    }
    lua_pop(L, 1);
    lua_getfield(L, i, "offset");
    if (lua_isnumber(L, -1)) {
        o->has_offset = 1;
        o->offset = (int32_t)lua_tointeger(L, -1);
    }
    lua_pop(L, 1);
    lua_getfield(L, i, "spill");
    if (lua_isnumber(L, -1)) {
        lua_Integer sp = lua_tointeger(L, -1);
        o->spill = (int16_t)(sp < 0 ? 0 : sp > 4096 ? 4096 : sp);
    }
    lua_pop(L, 1);
    lua_getfield(L, i, "part");
    if (lua_type(L, -1) == LUA_TTABLE) {
        lua_geti(L, -1, 1);
        lua_geti(L, -2, 2);
        o->has_part = 1;
        o->part_w = (int16_t)lua_tointeger(L, -2);
        o->part_h = (int16_t)lua_tointeger(L, -1);
        lua_pop(L, 2);
    }
    lua_pop(L, 1);
    lua_getfield(L, i, "id");
    if (lua_type(L, -1) == LUA_TSTRING) {
        size_t n = 0;
        const char *s = lua_tolstring(L, -1, &n);
        o->id.p = (const uint8_t *)s;
        o->id.n = (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n);
    }
    lua_getfield(L, i, "hint");
    if (lua_type(L, -1) == LUA_TSTRING) {
        size_t n = 0;
        const char *s = lua_tolstring(L, -1, &n);
        o->hint.p = (const uint8_t *)s;
        o->hint.n = (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n);
    }
    return o;
}

static int32_t moycore_ui_int(lua_State *L, int i)
{
    lua_Number v = luaL_checknumber(L, i);
    if (!(v == v) || v > (lua_Number)2.0e9f || v < (lua_Number)-2.0e9f) return 0;
    lua_Integer k = (lua_Integer)v;
    if ((lua_Number)k > v) k--;              /* toward -inf, as flr() */
    return (int32_t)k;
}

static int moycore_ui_page(lua_State *L)
{
    moy_ui_over_t o;
    int32_t b[4] = {0, 0, 0, 0};
    moy_ui_run_page(moycore_ui_canvas(), moycore_ui_span(L, 1), moycore_ui_over(L, 2, &o), b);
    for (int i = 0; i < 4; i++) lua_pushinteger(L, b[i]);
    return 4;
}

static int moycore_ui_label(lua_State *L)
{
    moy_ui_over_t o;
    moy_ui_run_label(moycore_ui_canvas(), moycore_ui_span(L, 1), moycore_ui_over(L, 2, &o));
    return 0;
}

static int moycore_ui_button(lua_State *L)
{
    int kind = 0;
    if (lua_type(L, 2) == LUA_TSTRING) {
        const char *k = lua_tostring(L, 2);
        for (int i = 0; i < 5; i++) {
            if (strcmp(k, moy_ui_button_kinds[i][0]) == 0) {
                kind = i;
                break;
            }
        }
    }
    moy_ui_over_t o;
    int32_t v = 0;
    moy_ui_run_button(moycore_ui_canvas(), moycore_ui_span(L, 1), kind, moycore_ui_over(L, 3, &o),
                      &v);
    lua_pushboolean(L, v != 0);
    return 1;
}

static int moycore_ui_toggle(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v = lua_toboolean(L, 2);
    moy_ui_run_toggle(moycore_ui_canvas(), moycore_ui_span(L, 1), v, moycore_ui_over(L, 3, &o),
                      &v);
    lua_pushboolean(L, v != 0);
    return 1;
}

static int moycore_ui_slider(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v = moycore_ui_int(L, 2);
    moy_ui_run_slider(moycore_ui_canvas(), moycore_ui_span(L, 1), v, moycore_ui_int(L, 3),
                      moycore_ui_int(L, 4), moycore_ui_over(L, 5, &o), &v);
    lua_pushinteger(L, v);
    return 1;
}

static int moycore_ui_list(lua_State *L)
{
    luaL_checktype(L, 2, LUA_TTABLE);
    int32_t cur = lua_isnoneornil(L, 3) ? -1 : moycore_ui_int(L, 3) - 1;
    moy_ui_over_t o;
    moycore_ui_over(L, 4, &o);
    luaL_Buffer b;
    luaL_buffinit(L, &b);
    lua_Integer n = luaL_len(L, 2);
    for (lua_Integer i = 1; i <= n; i++) {
        lua_geti(L, 2, i);
        size_t len = 0;
        const char *s = luaL_tolstring(L, -1, &len);
        lua_remove(L, -2);
        luaL_addvalue(&b);
        (void)s;
        (void)len;
        luaL_addchar(&b, '\0');
    }
    luaL_pushresult(&b);
    size_t len = 0;
    const char *items = lua_tolstring(L, -1, &len);
    int32_t v = cur;
    moy_ui_run_list(moycore_ui_canvas(), moycore_ui_span(L, 1), (const uint8_t *)items, len, cur,
                    &o, &v);
    lua_pushinteger(L, (lua_Integer)v + 1);
    return 1;
}

/* The strings of the table at stack index `i`, NUL-separated, pushed as one
 * string: its bytes. */
static const char *moycore_ui_items(lua_State *L, int i, size_t *len)
{
    luaL_checktype(L, i, LUA_TTABLE);
    luaL_Buffer b;
    luaL_buffinit(L, &b);
    lua_Integer n = luaL_len(L, i);
    for (lua_Integer k = 1; k <= n; k++) {
        lua_geti(L, i, k);
        luaL_tolstring(L, -1, NULL);
        lua_remove(L, -2);
        luaL_addvalue(&b);
        if (k < n) luaL_addchar(&b, '\0');
    }
    luaL_pushresult(&b);
    return lua_tolstring(L, -1, len);
}

static int moycore_ui_group(lua_State *L)
{
    moy_ui_over_t o;
    moy_ui_run_group(moycore_ui_canvas(), moycore_ui_span(L, 1), moycore_ui_over(L, 2, &o));
    return 0;
}

static int moycore_ui_value(lua_State *L, int kind)
{
    moy_ui_over_t o;
    moycore_ui_over(L, 3, &o);
    size_t n = 0;
    const char *v = lua_isnoneornil(L, 2) ? "" : luaL_tolstring(L, 2, &n);
    moy_ui_span_t vs = { (const uint8_t *)v, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n) };
    int32_t got = 0;
    moy_ui_run_value(moycore_ui_canvas(), kind, moycore_ui_span(L, 1), vs, &o, &got);
    lua_pushboolean(L, got != 0);
    return 1;
}

static int moycore_ui_info(lua_State *L)
{
    moycore_ui_value(L, MOY_UI_V_INFO);
    return 0;
}

static int moycore_ui_link(lua_State *L)
{
    return moycore_ui_value(L, MOY_UI_V_LINK);
}

static int moycore_ui_choice(lua_State *L, int kind)
{
    int32_t cur = lua_isnoneornil(L, 3) ? -1 : moycore_ui_int(L, 3) - 1;
    moy_ui_over_t o;
    moycore_ui_over(L, 4, &o);
    size_t len = 0;
    const char *opts;
    if (kind == MOY_UI_V_SWATCH) {
        luaL_checktype(L, 2, LUA_TTABLE);
        luaL_Buffer b;
        luaL_buffinit(L, &b);
        lua_Integer n = luaL_len(L, 2);
        for (lua_Integer k = 1; k <= n; k++) {
            lua_geti(L, 2, k);
            uint32_t c = (uint32_t)(lua_tointeger(L, -1) & 0xffffff);
            lua_pop(L, 1);
            char w[4] = { (char)(c & 0xff), (char)((c >> 8) & 0xff), (char)((c >> 16) & 0xff), 0 };
            luaL_addlstring(&b, w, 4);
        }
        luaL_pushresult(&b);
        opts = lua_tolstring(L, -1, &len);
    } else {
        opts = moycore_ui_items(L, 2, &len);
    }
    int32_t got = cur;
    moy_ui_run_choice(moycore_ui_canvas(), kind, moycore_ui_span(L, 1), (const uint8_t *)opts,
                      len, cur, &o, &got);
    lua_pushinteger(L, (lua_Integer)got + 1);
    return 1;
}

static int moycore_ui_cycle(lua_State *L)
{
    return moycore_ui_choice(L, MOY_UI_V_CYCLE);
}

static int moycore_ui_seg(lua_State *L)
{
    return moycore_ui_choice(L, MOY_UI_V_SEG);
}

static int moycore_ui_swatches(lua_State *L)
{
    return moycore_ui_choice(L, MOY_UI_V_SWATCH);
}

static int moycore_ui_tabs(lua_State *L)
{
    return moycore_ui_choice(L, MOY_UI_V_TABS);
}

static int moycore_ui_grid(lua_State *L)
{
    moy_ui_over_t o;
    int w = lua_isnoneornil(L, 2) ? 0 : moycore_ui_int(L, 2);
    int h = lua_isnoneornil(L, 3) ? 0 : moycore_ui_int(L, 3);
    moy_ui_run_grid(moycore_ui_canvas(), moycore_ui_span(L, 1), w, h, moycore_ui_over(L, 4, &o));
    return 0;
}

static int moycore_ui_card(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v[5] = {0, 0, 0, 0, 0};
    moy_ui_run_card(moycore_ui_canvas(), moycore_ui_span(L, 1), moycore_ui_span(L, 2),
                    moycore_ui_over(L, 3, &o), v);
    for (int i = 0; i < 5; i++) lua_pushinteger(L, v[i]);
    return 5;
}

static int moycore_ui_hints(lua_State *L)
{
    moy_ui_over_t o;
    moycore_ui_over(L, 2, &o);
    size_t len = 0;
    const char *items = moycore_ui_items(L, 1, &len);
    moy_ui_run_hints(moycore_ui_canvas(), (const uint8_t *)items, len, &o);
    return 0;
}

static int moycore_ui_icon(lua_State *L)
{
    int32_t v = 0;
    moy_ui_run_icon(moycore_ui_canvas(), moycore_ui_span(L, 1), &v);
    lua_pushboolean(L, v != 0);
    return 1;
}

static int moycore_ui_progress(lua_State *L)
{
    moy_ui_over_t o;
    moy_ui_run_progress(moycore_ui_canvas(), moycore_ui_span(L, 1), moycore_ui_int(L, 2),
                        moycore_ui_int(L, 3), moycore_ui_over(L, 4, &o));
    return 0;
}

/* The strings of the table at stack index `i`, each ending in a NUL, appended to b. */
static void moycore_ui_cells(lua_State *L, int i, luaL_Buffer *b, lua_Integer n)
{
    for (lua_Integer k = 1; k <= n; k++) {
        lua_geti(L, i, k);
        if (!lua_isnil(L, -1)) {
            luaL_tolstring(L, -1, NULL);
            lua_remove(L, -2);
            luaL_addvalue(b);
        } else {
            lua_pop(L, 1);
        }
        luaL_addchar(b, '\0');
    }
}

static int moycore_ui_table(lua_State *L)
{
    luaL_checktype(L, 2, LUA_TTABLE);
    luaL_checktype(L, 3, LUA_TTABLE);
    int32_t cur = lua_isnoneornil(L, 4) ? -1 : moycore_ui_int(L, 4) - 1;
    moy_ui_over_t o;
    moycore_ui_over(L, 5, &o);
    lua_Integer nc = luaL_len(L, 2);
    luaL_Buffer b;
    luaL_buffinit(L, &b);
    moycore_ui_cells(L, 2, &b, nc);
    luaL_pushresult(&b);
    lua_Integer nr = luaL_len(L, 3);
    if (nr > 4096) nr = 4096;
    luaL_checkstack(L, (int)nr + 4, "ui.table");
    for (lua_Integer r = 1; r <= nr; r++) {
        lua_geti(L, 3, r);
        int row = lua_gettop(L);
        luaL_buffinit(L, &b);       /* its slot above the row: the cells go on top */
        if (lua_type(L, row) == LUA_TTABLE) {
            moycore_ui_cells(L, row, &b, nc);
        } else {
            for (lua_Integer k = 0; k < nc; k++) luaL_addchar(&b, '\0');
        }
        luaL_pushresult(&b);
        lua_remove(L, row);
    }
    lua_concat(L, (int)nr);
    size_t cn = 0, en = 0;
    const char *cols = lua_tolstring(L, -2, &cn);
    const char *cells = lua_tolstring(L, -1, &en);
    int32_t v[2] = {cur, 0};
    moy_ui_run_table(moycore_ui_canvas(), moycore_ui_span(L, 1), (const uint8_t *)cols, cn,
                     (const uint8_t *)cells, en, cur, &o, v);
    lua_pushinteger(L, (lua_Integer)v[0] + 1);
    lua_pushboolean(L, v[1] != 0);
    return 2;
}

static int moycore_ui_table_part(lua_State *L)
{
    int32_t v[8];
    int32_t i = moy_ui_run_table_part(moycore_ui_canvas(), v);
    if (i < 0) {
        lua_pushnil(L);
        return 1;
    }
    lua_pushinteger(L, (lua_Integer)i + 1);
    for (int k = 0; k < 4; k++) lua_pushinteger(L, v[k]);
    return 5;
}

/* A verb that opens a nest raises where MOY_UI_API_NEST were open already. */
static void moycore_ui_nested(lua_State *L, int rc)
{
    if (rc == -MOY_UI_DEEP) {
        luaL_error(L, "ui: nested too deep");
    }
}

static int moycore_ui_scroll(lua_State *L)
{
    moy_ui_over_t o;
    moycore_ui_nested(L, moy_ui_run_scroll(moycore_ui_canvas(), moycore_ui_span(L, 1),
                                           moycore_ui_over(L, 2, &o)));
    return 0;
}

/* ui.text_view(id, lines, o) -> the first line shown (from 1), the rows
 * shown. `lines` a table of strings from 1; `o`'s rows, wrap, syntax ("sgr"
 * by default, "plain", "python", "lua", "json", "markdown"), follow, gutter
 * and caret ({line, col}, each from 1). */
typedef struct {
    lua_State *L;
    int t;
} moycore_ui_lines_t;

static const uint8_t *moycore_ui_line(void *src, int32_t i, size_t *n)
{
    moycore_ui_lines_t *ls = (moycore_ui_lines_t *)src;
    lua_rawgeti(ls->L, ls->t, (lua_Integer)i + 1);
    const char *p = lua_type(ls->L, -1) == LUA_TSTRING ? lua_tolstring(ls->L, -1, n) : NULL;
    if (p == NULL) {
        *n = 0;
    }
    lua_pop(ls->L, 1);          /* the table holds the string */
    return (const uint8_t *)p;
}

static int moycore_ui_text_view(lua_State *L)
{
    static const char *const syn[] = {"plain", "python", "lua", "json", "sgr", "markdown"};
    luaL_checktype(L, 2, LUA_TTABLE);
    moycore_ui_lines_t ls = {L, 2};
    moy_ui_lines_t lines = {&ls, (int32_t)lua_rawlen(L, 2), moycore_ui_line};
    int32_t opt[MOY_UI_TXO_LEN] = MOY_UI_TXO_DEFAULTS;
    if (lua_type(L, 3) == LUA_TTABLE) {
        lua_getfield(L, 3, "rows");
        opt[MOY_UI_TXO_ROWS] = lua_isinteger(L, -1) ? (int32_t)lua_tointeger(L, -1) : 0;
        lua_pop(L, 1);
        opt[MOY_UI_TXO_WRAP] = moycore_ui_flag(L, 3, "wrap") == 1;
        opt[MOY_UI_TXO_FOLLOW] = moycore_ui_flag(L, 3, "follow") == 1;
        opt[MOY_UI_TXO_GUTTER] = moycore_ui_flag(L, 3, "gutter") == 1;
        lua_getfield(L, 3, "syntax");
        if (lua_type(L, -1) == LUA_TSTRING) {
            const char *name = lua_tostring(L, -1);
            for (int i = 0; i < (int)(sizeof(syn) / sizeof(syn[0])); i++) {
                if (strcmp(name, syn[i]) == 0) {
                    opt[MOY_UI_TXO_SYNTAX] = i;
                }
            }
        }
        lua_pop(L, 1);
        lua_getfield(L, 3, "caret");
        if (lua_type(L, -1) == LUA_TTABLE) {
            lua_rawgeti(L, -1, 1);
            lua_rawgeti(L, -2, 2);
            opt[MOY_UI_TXO_CARET_LINE] = (int32_t)luaL_optinteger(L, -2, 0) - 1;
            opt[MOY_UI_TXO_CARET_COL] = (int32_t)luaL_optinteger(L, -1, 1) - 1;
            lua_pop(L, 2);
        }
        lua_pop(L, 1);
    }
    moy_ui_over_t o;
    int32_t out[2] = {0, 0};
    moycore_ui_nested(L, moy_ui_run_text_view(moycore_ui_canvas(), moycore_ui_span(L, 1), &lines,
                                              opt, moycore_ui_over(L, 3, &o), out));
    lua_pushinteger(L, out[0] + 1);
    lua_pushinteger(L, out[1]);
    return 2;
}

static int moycore_ui_field(lua_State *L)
{
    size_t n = 0;
    const char *text = lua_isnoneornil(L, 2) ? "" : luaL_checklstring(L, 2, &n);
    int32_t cap = lua_isnoneornil(L, 3) ? 32 : moycore_ui_int(L, 3);
    if (cap < 1) cap = 1;
    if (cap > 255) cap = 255;
    uint8_t buf[255];
    size_t len = n < (size_t)cap ? n : (size_t)cap;
    memcpy(buf, text, len);
    moy_ui_over_t o;
    int32_t v = (int32_t)len;
    moy_ui_run_field(moycore_ui_canvas(), moycore_ui_span(L, 1), buf, (size_t)cap, len,
                     moycore_ui_over(L, 4, &o), &v);
    lua_pushlstring(L, (const char *)buf, (size_t)(v & 0xffff));
    lua_pushboolean(L, (v & MOY_UI_FIELD_DONE) != 0);
    return 2;
}

static int moycore_ui_dialog(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v = -1;
    if (lua_isnoneornil(L, 1)) {
        static const uint8_t none[1] = {0};
        moy_ui_span_t end = {none, 0};
        moy_ui_over_none(&o);
        moy_ui_run_dialog(moycore_ui_canvas(), end, none, 0, 0, &o, &v);
        return 0;
    }
    int kind = 1;
    if (lua_type(L, 3) == LUA_TSTRING) {
        const char *k = lua_tostring(L, 3);
        for (int i = 0; i < 5; i++) {
            if (strcmp(k, moy_ui_button_kinds[i][0]) == 0) {
                kind = i;
                break;
            }
        }
    }
    moycore_ui_over(L, 4, &o);
    size_t len = 0;
    const char *items = lua_isnoneornil(L, 2) ? "" : moycore_ui_items(L, 2, &len);
    moycore_ui_nested(L, moy_ui_run_dialog(moycore_ui_canvas(), moycore_ui_span(L, 1),
                                           (const uint8_t *)items, len, kind, &o, &v));
    lua_pushinteger(L, (lua_Integer)v + 1);
    return 1;
}

static int moycore_ui_popover(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v = 0;
    if (lua_isnoneornil(L, 1)) {
        static const uint8_t none[1] = {0};
        moy_ui_span_t end = {none, 0};
        moy_ui_over_none(&o);
        moy_ui_run_popover(moycore_ui_canvas(), end, 0, &o, &v);
        return 0;
    }
    int w = lua_isnoneornil(L, 2) ? 0 : moycore_ui_int(L, 2);
    moycore_ui_nested(L, moy_ui_run_popover(moycore_ui_canvas(), moycore_ui_span(L, 1), w,
                                            moycore_ui_over(L, 3, &o), &v));
    lua_pushboolean(L, v != 0);
    return 1;
}

static int moycore_ui_menu(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v = -1;
    moycore_ui_over(L, 4, &o);
    size_t len = 0, ilen = 0;
    const char *items = lua_isnoneornil(L, 2) ? "" : moycore_ui_items(L, 2, &len);
    const char *icons = lua_isnoneornil(L, 3) ? "" : moycore_ui_items(L, 3, &ilen);
    moy_ui_run_menu(moycore_ui_canvas(), moycore_ui_span(L, 1), (const uint8_t *)items, len,
                    (const uint8_t *)icons, ilen, &o, &v);
    lua_pushinteger(L, (lua_Integer)v + 1);
    return 1;
}

static int moycore_ui_tool(lua_State *L)
{
    moy_ui_over_t o;
    int32_t v = 0;
    moy_ui_run_tool(moycore_ui_canvas(), moycore_ui_span(L, 1), moycore_ui_span(L, 2),
                    moycore_ui_over(L, 3, &o), &v);
    lua_pushboolean(L, v != 0);
    return 1;
}

static int moycore_ui_bar(lua_State *L)
{
    size_t len = 0;
    const char *tools = lua_isnoneornil(L, 2) ? "" : moycore_ui_items(L, 2, &len);
    uint32_t off = 0;
    if (lua_type(L, 3) == LUA_TTABLE) {
        for (int i = 0; i < MOY_UI_API_BAR; i++) {
            lua_rawgeti(L, 3, i + 1);
            if (lua_toboolean(L, -1)) off |= 1u << i;
            lua_pop(L, 1);
        }
    }
    if (lua_toboolean(L, 4)) off |= MOY_UI_BAR_BACK;
    int32_t v = -1;
    moy_ui_run_bar(moycore_ui_canvas(), moycore_ui_span(L, 1), (const uint8_t *)tools, len, off,
                   &v);
    lua_pushinteger(L, v == MOY_UI_BAR_UP ? -1 : (lua_Integer)v + 1);
    return 1;
}

static int moycore_ui_rule(lua_State *L)
{
    (void)L;
    moy_ui_run_rule(moycore_ui_canvas());
    return 0;
}

static int moycore_ui_part(lua_State *L)
{
    moy_ui_over_t o;
    int w = lua_isnoneornil(L, 2) ? 0 : moycore_ui_int(L, 2);
    int h = lua_isnoneornil(L, 3) ? 0 : moycore_ui_int(L, 3);
    int32_t v[5] = {0, 0, 0, 0, 0};
    moy_ui_run_part(moycore_ui_canvas(), moycore_ui_span(L, 1), w, h, moycore_ui_over(L, 4, &o),
                    v);
    for (int i = 0; i < 5; i++) lua_pushinteger(L, v[i]);
    return 5;
}

static int moycore_ui_part_at(lua_State *L)
{
    int32_t v[2];
    if (!moy_ui_run_part_at(moycore_ui_canvas(), v)) {
        lua_pushnil(L);
        return 1;
    }
    lua_pushinteger(L, v[0]);
    lua_pushinteger(L, v[1]);
    return 2;
}

/* Field `k` of the table at `i`, an integer (0 where it is none). */
static int32_t moycore_ui_field_int(lua_State *L, int i, const char *k)
{
    lua_getfield(L, i, k);
    int32_t v = lua_isnumber(L, -1) ? (int32_t)lua_tointeger(L, -1) : 0;
    lua_pop(L, 1);
    return v;
}

/* Field `k` of the table at `i`, a pair {a, b}, into v[0], v[1]. */
static void moycore_ui_field_pair(lua_State *L, int i, const char *k, int32_t *v)
{
    lua_getfield(L, i, k);
    if (lua_type(L, -1) == LUA_TTABLE) {
        lua_geti(L, -1, 1);
        lua_geti(L, -2, 2);
        v[0] = (int32_t)lua_tointeger(L, -2);
        v[1] = (int32_t)lua_tointeger(L, -1);
        lua_pop(L, 2);
    }
    lua_pop(L, 1);
}

static int moycore_ui_split(lua_State *L)
{
    moy_ui_span_t id = moycore_ui_span(L, 1);
    const char *dir = luaL_checkstring(L, 2);
    luaL_checktype(L, 3, LUA_TTABLE);
    luaL_checktype(L, 5, LUA_TTABLE);
    int n = (int)luaL_len(L, 3);
    if (n < 1 || n > MOY_UI_SPLIT_MAX) return luaL_error(L, "ui.split: 1 to 16 panes");
    int32_t sz[MOY_UI_SPLIT_MAX], mi[MOY_UI_SPLIT_MAX], opt[MOY_UI_SPO_N];
    int32_t w[4 * MOY_UI_SPLIT_MAX + MOY_UI_SPLIT_OUT];
    memset(opt, 0, sizeof(opt));
    for (int i = 0; i < n; i++) {
        lua_geti(L, 3, i + 1);
        sz[i] = (int32_t)lua_tointeger(L, -1);
        lua_pop(L, 1);
    }
    opt[MOY_UI_SPO_FLAGS] = dir[0] == 'y' ? MOY_UI_SPLIT_Y : 0;
    int mins = 0;
    if (lua_type(L, 4) == LUA_TTABLE) {
        lua_getfield(L, 4, "drag");
        if (lua_toboolean(L, -1)) opt[MOY_UI_SPO_FLAGS] |= MOY_UI_SPLIT_DRAG;
        lua_pop(L, 1);
        lua_getfield(L, 4, "focus");
        if (lua_isnumber(L, -1)) {
            opt[MOY_UI_SPO_FLAGS] |= MOY_UI_SPLIT_FOCUS;
            opt[MOY_UI_SPO_CUR] = (int32_t)lua_tointeger(L, -1) - 1;
        }
        lua_pop(L, 1);
        opt[MOY_UI_SPO_GAP] = moycore_ui_field_int(L, 4, "gap");
        opt[MOY_UI_SPO_SLOP] = moycore_ui_field_int(L, 4, "slop");
        opt[MOY_UI_SPO_LINE] = moycore_ui_field_int(L, 4, "line");
        moycore_ui_field_pair(L, 4, "hit", opt + MOY_UI_SPO_HIT_W);
        moycore_ui_field_pair(L, 4, "handle", opt + MOY_UI_SPO_HANDLE_W);
        lua_getfield(L, 4, "mins");
        if (lua_type(L, -1) == LUA_TTABLE) {
            mins = 1;
            for (int i = 0; i < n; i++) {
                lua_geti(L, -1, i + 1);
                mi[i] = (int32_t)lua_tointeger(L, -1);
                lua_pop(L, 1);
            }
        }
        lua_pop(L, 1);
    }
    int32_t cur = -1;
    if (moy_ui_run_split(moycore_ui_canvas(), id, sz, n, mins ? mi : NULL, opt, w, &cur) != 0) {
        lua_pushinteger(L, 0);
        return 1;
    }
    int32_t *d = w + 4 * n;
    if ((d[3] & (MOY_UI_SPD_LIVE | MOY_UI_SPD_LET_GO)) == (MOY_UI_SPD_LIVE | MOY_UI_SPD_LET_GO)) {
        for (int i = 0; i < n; i++) {
            lua_pushinteger(L, sz[i]);
            lua_seti(L, 3, i + 1);
        }
    }
    d[0] += 1;                          /* the divider from 1, 0 none */
    for (int i = 0; i < 4 * n + MOY_UI_SPLIT_OUT; i++) {
        lua_pushinteger(L, w[i]);
        lua_seti(L, 5, i + 1);
    }
    lua_pushinteger(L, (lua_Integer)cur + 1);
    return 1;
}

static int moycore_ui_focus(lua_State *L)
{
    moy_ui_span_t id = { NULL, 0 };
    if (!lua_isnoneornil(L, 1)) id = moycore_ui_span(L, 1);
    char buf[MOY_UI_API_NAME];
    int n = moy_ui_run_focus(moycore_ui_canvas(), id, buf, sizeof(buf));
    if (n < 0) lua_pushnil(L);
    else lua_pushstring(L, buf);
    return 1;
}

static int moycore_ui_table_dist(lua_State *L)
{
    lua_pushinteger(L, (lua_Integer)moy_ui_run_table_dist(moycore_ui_canvas()));
    return 1;
}

static int moycore_ui_keys_taken(lua_State *L)
{
    lua_pushboolean(L, moy_ui_run_keys_taken(moycore_ui_canvas()) != 0);
    return 1;
}

static int moycore_ui_at(lua_State *L)
{
    moy_ui_run_at(moycore_ui_canvas(), moycore_ui_int(L, 1), moycore_ui_int(L, 2),
                  moycore_ui_int(L, 3), moycore_ui_int(L, 4));
    return 0;
}

static void moycore_ui_open(lua_State *L)
{
    static const luaL_Reg verbs[] = {
        {"page", moycore_ui_page}, {"label", moycore_ui_label}, {"button", moycore_ui_button},
        {"toggle", moycore_ui_toggle}, {"slider", moycore_ui_slider}, {"list", moycore_ui_list},
        {"group", moycore_ui_group}, {"info", moycore_ui_info}, {"link", moycore_ui_link},
        {"cycle", moycore_ui_cycle}, {"seg", moycore_ui_seg}, {"swatches", moycore_ui_swatches},
        {"hints", moycore_ui_hints}, {"part", moycore_ui_part}, {"focus", moycore_ui_focus},
        {"keys_taken", moycore_ui_keys_taken}, {"table_dist", moycore_ui_table_dist}, {"at", moycore_ui_at}, {"icon", moycore_ui_icon},
        {"progress", moycore_ui_progress}, {"table", moycore_ui_table},
        {"table_part", moycore_ui_table_part}, {"part_at", moycore_ui_part_at},
        {"split", moycore_ui_split},
        {"scroll", moycore_ui_scroll}, {"field", moycore_ui_field}, {"dialog", moycore_ui_dialog},
        {"tabs", moycore_ui_tabs}, {"grid", moycore_ui_grid}, {"card", moycore_ui_card},
        {"popover", moycore_ui_popover}, {"menu", moycore_ui_menu},
        {"tool", moycore_ui_tool}, {"bar", moycore_ui_bar},
        {"rule", moycore_ui_rule}, {"text_view", moycore_ui_text_view},
        {NULL, NULL},
    };
    luaL_newlib(L, verbs);
    lua_setglobal(L, "ui");
}

#endif /* MOYCORE_UI_H */
