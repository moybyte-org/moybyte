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
 *   ui.part(id, w, h, o)           -> x, y, w, h, state
 *   ui.focus(id), ui.focus()       -> the focused id, or nil
 *   ui.keys_taken()                -> whether the ui used this frame's keys
 *   ui.at(x, y, w, h)
 *
 * `o` is a table of §3's keys: fill, ink, border (a role name or a colour
 * 0xRRGGBB), id, current, disabled, nav, mark.
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

/* The override table at stack index `i` into *o. An `id` string is left on
 * the stack, so the span stays valid for the call. */
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
    lua_getfield(L, i, "id");
    if (lua_type(L, -1) == LUA_TSTRING) {
        size_t n = 0;
        const char *s = lua_tolstring(L, -1, &n);
        o->id.p = (const uint8_t *)s;
        o->id.n = (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n);
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
        {"part", moycore_ui_part}, {"focus", moycore_ui_focus},
        {"keys_taken", moycore_ui_keys_taken}, {"at", moycore_ui_at}, {NULL, NULL},
    };
    luaL_newlib(L, verbs);
    lua_setglobal(L, "ui");
}

#endif /* MOYCORE_UI_H */
