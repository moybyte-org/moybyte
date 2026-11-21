/* The UI tree engine's Lua face (native/moy_uitree/moy_uitree.h): the global
 * function `__moy_uitree()`, which makes a store, an engine of its own. The
 * UI tree layer's C back end (runtime/run/uitree_engine.lua) takes it and
 * clears the global before a cart's own scripts run; a cart never calls it.
 * A store's methods are the engine's calls, one each, by the names
 * modmoy_uitree.c gives them on MicroPython; a refusal answers below 0, and
 * `error()` and `error_code()` say why. A text crosses as a Lua string, a
 * NUL-separated list as one string with its NULs.
 *
 *   e:state(name, type, values) e:model(slot, names, types, key)
 *   e:row_of(slot, model)       e:labels(name, keys, words)
 *   e:action(name, label, hint, when, off, flags, letters, icon)
 *   e:action_row(a, slot)
 *   e:set_int(slot, v) e:set_num(slot, v) e:set_text(slot, s)
 *   e:set_absent(slot) e:set_model(slot, n, cells)
 *   e:get_int(slot) e:get_text(slot) e:get_cell(slot, row, f) e:absent(slot)
 *   e:build_begin(page_id) e:node(kind, key, parent) e:prop(node, name, src, repr)
 *   e:node_action(node, a) e:build_end()
 *   e:open() e:close() e:want_focus(key)
 *   e:frame(flags)              -> uit_run_frame over the run's canvas
 *   e:on_draw(fn)               a canvas's draw, fn(node, x, y, w, h), called
 *                               inside the frame; what it raises the frame raises
 *   e:next()                    -> kind, a, b, c, text; nil where none waits
 *   e:trace_next()              -> "KIND\1NAME\1TEXT", nil where none waits
 *   e:can(a, row) e:letter(ch) e:dialog() e:unfocused()
 *   e:back_dialog() e:back_popover() -> action (-1 none, -2 no popover), key
 *   e:focused() e:popover() e:hints() e:leave() e:dump()
 *   e:error() e:error_code() e:free()
 *
 * Included by native/moycore/moycore_lua.c and opened beside the UI API's
 * face when the build has native/moy_uitree (MOYCORE_UITREE). */
#ifndef MOYCORE_UITREE_H
#define MOYCORE_UITREE_H

#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy_uitree.h"
#include "moycore_lua.h"

#define MOYCORE_UIT_MT "moy.uitree"

typedef struct {
    uit_t *e;
    lua_State *L;           // the state a frame runs in, while it runs
    int draw;               // on_draw's function (a registry ref), LUA_NOREF none
    int raised;             // what a draw raised inside the frame (a registry ref), raised after it
} moycore_uit_t;

static moycore_uit_t *moycore_uit_of(lua_State *L)
{
    moycore_uit_t *u = (moycore_uit_t *)luaL_checkudata(L, 1, MOYCORE_UIT_MT);
    if (u->e == NULL) luaL_error(L, "the UI tree store is freed");
    return u;
}

static uit_t *moycore_uit(lua_State *L)
{
    return moycore_uit_of(L)->e;
}

static int moycore_uit_int(lua_State *L, int i)
{
    return (int)luaL_checkinteger(L, i);
}

static const char *moycore_uit_str(lua_State *L, int i, size_t *n)
{
    size_t k = 0;
    const char *s = luaL_checklstring(L, i, &k);
    if (n) *n = k;
    return s;
}

/* An optional text: NULL for nil. */
static const char *moycore_uit_opt(lua_State *L, int i)
{
    return lua_isnoneornil(L, i) ? NULL : luaL_checkstring(L, i);
}

static int l_uit_new(lua_State *L)
{
    moycore_uit_t *u = (moycore_uit_t *)lua_newuserdatauv(L, sizeof *u, 0);
    u->e = uit_new();
    u->L = NULL;
    u->draw = u->raised = LUA_NOREF;
    if (u->e == NULL) return luaL_error(L, "no memory for a UI tree store");
    luaL_setmetatable(L, MOYCORE_UIT_MT);
    return 1;
}

static int l_uit_free(lua_State *L)
{
    moycore_uit_t *u = (moycore_uit_t *)luaL_checkudata(L, 1, MOYCORE_UIT_MT);
    if (u->e != NULL) {
        uit_free(u->e);
        u->e = NULL;
    }
    luaL_unref(L, LUA_REGISTRYINDEX, u->draw);
    luaL_unref(L, LUA_REGISTRYINDEX, u->raised);
    u->draw = u->raised = LUA_NOREF;
    return 0;
}

static int l_uit_state(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    size_t vn = 0;
    const char *v = lua_isnoneornil(L, 4) ? NULL : moycore_uit_str(L, 4, &vn);
    lua_pushinteger(L, uit_state(e, moycore_uit_str(L, 2, NULL), moycore_uit_int(L, 3), v, vn));
    return 1;
}

static int l_uit_model(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    size_t nn = 0, tn = 0;
    const char *names = moycore_uit_str(L, 3, &nn);
    const char *types = moycore_uit_str(L, 4, &tn);
    lua_pushinteger(L, uit_model(e, moycore_uit_int(L, 2), names, nn, (const uint8_t *)types, (int)tn,
                                 moycore_uit_int(L, 5)));
    return 1;
}

static int l_uit_row_of(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_row_of(e, moycore_uit_int(L, 2), moycore_uit_int(L, 3)));
    return 1;
}

static int l_uit_labels(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    size_t kn = 0, wn = 0;
    const char *keys = moycore_uit_str(L, 3, &kn);
    const char *words = moycore_uit_str(L, 4, &wn);
    lua_pushinteger(L, uit_labels(e, moycore_uit_str(L, 2, NULL), keys, kn, words, wn));
    return 1;
}

static int l_uit_action(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_action(e, moycore_uit_str(L, 2, NULL), moycore_uit_str(L, 3, NULL),
                                  moycore_uit_str(L, 4, NULL), moycore_uit_opt(L, 5), moycore_uit_int(L, 6),
                                  moycore_uit_int(L, 7), moycore_uit_str(L, 8, NULL),
                                  moycore_uit_str(L, 9, NULL)));
    return 1;
}

static int l_uit_action_row(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_action_row(e, moycore_uit_int(L, 2), moycore_uit_int(L, 3)));
    return 1;
}

static int l_uit_set_int(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_set_int(e, moycore_uit_int(L, 2), (int32_t)luaL_checkinteger(L, 3)));
    return 1;
}

static int l_uit_set_num(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_set_num(e, moycore_uit_int(L, 2), (double)luaL_checknumber(L, 3)));
    return 1;
}

static int l_uit_set_text(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    size_t n = 0;
    const char *s = moycore_uit_str(L, 3, &n);
    lua_pushinteger(L, uit_set_text(e, moycore_uit_int(L, 2), s, n));
    return 1;
}

static int l_uit_set_absent(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_set_absent(e, moycore_uit_int(L, 2)));
    return 1;
}

static int l_uit_set_model(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    size_t n = 0;
    const char *cells = moycore_uit_str(L, 4, &n);
    lua_pushinteger(L, uit_set_model(e, moycore_uit_int(L, 2), moycore_uit_int(L, 3), cells, n));
    return 1;
}

static int l_uit_get_int(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_get_int(e, moycore_uit_int(L, 2)));
    return 1;
}

static int l_uit_get_text(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    size_t n = 0;
    const char *s = uit_get_text(e, moycore_uit_int(L, 2), &n);
    if (s == NULL) lua_pushnil(L);
    else lua_pushlstring(L, s, n);
    return 1;
}

static int l_uit_get_cell(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    const char *s = uit_get_cell(e, moycore_uit_int(L, 2), moycore_uit_int(L, 3), moycore_uit_int(L, 4));
    if (s == NULL) lua_pushnil(L);
    else lua_pushstring(L, s);
    return 1;
}

static int l_uit_absent(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushboolean(L, uit_absent(e, moycore_uit_int(L, 2)));
    return 1;
}

static int l_uit_build_begin(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    uit_build_begin(e, moycore_uit_str(L, 2, NULL));
    return 0;
}

static int l_uit_node(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_node(e, moycore_uit_int(L, 2), moycore_uit_str(L, 3, NULL), moycore_uit_int(L, 4)));
    return 1;
}

static int l_uit_prop(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_prop(e, moycore_uit_int(L, 2), moycore_uit_str(L, 3, NULL),
                                moycore_uit_str(L, 4, NULL), moycore_uit_opt(L, 5)));
    return 1;
}

static int l_uit_node_action(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_node_action(e, moycore_uit_int(L, 2), moycore_uit_int(L, 3)));
    return 1;
}

static int l_uit_build_end(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_build_end(e));
    return 1;
}

static int l_uit_open(lua_State *L)
{
    uit_open(moycore_uit(L));
    return 0;
}

static int l_uit_close(lua_State *L)
{
    uit_close(moycore_uit(L));
    return 0;
}

static int l_uit_want_focus(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    uit_want_focus(e, moycore_uit_opt(L, 2));
    return 0;
}

/* A canvas's draw, called by the walk inside the frame: on_draw's function
 * with the node and its rect. What it raises is kept, the frame's other draws
 * skipped, and raised once the frame is over (never through the walk). */
static void moycore_uit_draw(void *ud, int node, int x, int y, int w, int h)
{
    moycore_uit_t *u = (moycore_uit_t *)ud;
    lua_State *L = u->L;
    if (L == NULL || u->raised != LUA_NOREF || u->draw == LUA_NOREF) return;
    lua_rawgeti(L, LUA_REGISTRYINDEX, u->draw);
    lua_pushinteger(L, node);
    lua_pushinteger(L, x);
    lua_pushinteger(L, y);
    lua_pushinteger(L, w);
    lua_pushinteger(L, h);
    if (lua_pcall(L, 5, 0, 0) != LUA_OK) u->raised = luaL_ref(L, LUA_REGISTRYINDEX);
}

static int l_uit_on_draw(lua_State *L)
{
    moycore_uit_t *u = moycore_uit_of(L);
    luaL_unref(L, LUA_REGISTRYINDEX, u->draw);
    u->draw = LUA_NOREF;
    if (!lua_isnoneornil(L, 2)) {
        luaL_checktype(L, 2, LUA_TFUNCTION);
        lua_pushvalue(L, 2);
        u->draw = luaL_ref(L, LUA_REGISTRYINDEX);
    }
    uit_on_draw(u->e, u->draw == LUA_NOREF ? NULL : moycore_uit_draw, u);
    return 0;
}

static int l_uit_frame(lua_State *L)
{
    moycore_uit_t *u = moycore_uit_of(L);
    int flags = moycore_uit_int(L, 2);
    u->L = L;
    int r = uit_run_frame(u->e, moycore_ui_canvas(), flags);
    u->L = NULL;
    if (u->raised != LUA_NOREF) {
        lua_rawgeti(L, LUA_REGISTRYINDEX, u->raised);
        luaL_unref(L, LUA_REGISTRYINDEX, u->raised);
        u->raised = LUA_NOREF;
        return lua_error(L);
    }
    lua_pushinteger(L, r);
    return 1;
}

static int l_uit_next(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    int32_t ev[3] = {0, 0, 0};
    char buf[512];
    buf[0] = 0;
    int k = uit_next(e, ev, buf, sizeof buf);
    if (k == UIT_EV_NONE) {
        lua_pushnil(L);
        return 1;
    }
    lua_pushinteger(L, k);
    lua_pushinteger(L, ev[0]);
    lua_pushinteger(L, ev[1]);
    lua_pushinteger(L, ev[2]);
    lua_pushstring(L, buf);
    return 5;
}

static int l_uit_trace_next(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    char buf[512];
    int n = uit_trace_next(e, buf, sizeof buf);
    if (n < 0) lua_pushnil(L);
    else lua_pushlstring(L, buf, (size_t)n < sizeof buf ? (size_t)n : sizeof buf - 1);
    return 1;
}

static int l_uit_can(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushboolean(L, uit_can(e, moycore_uit_int(L, 2), (int)luaL_optinteger(L, 3, -1)));
    return 1;
}

static int l_uit_letter(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    lua_pushinteger(L, uit_letter(e, moycore_uit_int(L, 2)));
    return 1;
}

static int l_uit_dialog(lua_State *L)
{
    lua_pushinteger(L, uit_dialog(moycore_uit(L)));
    return 1;
}

static int l_uit_unfocused(lua_State *L)
{
    lua_pushboolean(L, uit_unfocused(moycore_uit(L)));
    return 1;
}

static int l_uit_back_dialog(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    char buf[64];
    buf[0] = 0;
    lua_pushinteger(L, uit_back_dialog(e, buf, sizeof buf));
    lua_pushstring(L, buf);
    return 2;
}

static int l_uit_back_popover(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    char buf[64];
    buf[0] = 0;
    lua_pushinteger(L, uit_back_popover(e, buf, sizeof buf));
    lua_pushstring(L, buf);
    return 2;
}

static int l_uit_focused(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    char buf[64];
    buf[0] = 0;
    int n = uit_focused(e, buf, sizeof buf);
    lua_pushlstring(L, buf, n > 0 ? ((size_t)n < sizeof buf ? (size_t)n : sizeof buf - 1) : 0);
    return 1;
}

static int l_uit_popover(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    char buf[64];
    buf[0] = 0;
    if (uit_popover(e, buf, sizeof buf)) lua_pushstring(L, buf);
    else lua_pushnil(L);
    return 1;
}

static int l_uit_hints(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    char buf[512];
    int n = uit_hints(e, buf, sizeof buf);
    lua_pushlstring(L, buf, n > 0 && (size_t)n <= sizeof buf ? (size_t)n : 0);
    return 1;
}

static int l_uit_leave(lua_State *L)
{
    lua_pushinteger(L, uit_leave(moycore_uit(L)));
    return 1;
}

static int l_uit_dump(lua_State *L)
{
    uit_t *e = moycore_uit(L);
    int n = uit_dump(e, NULL, 0);
    if (n < 0) n = 0;
    luaL_Buffer b;
    char *p = luaL_buffinitsize(L, &b, (size_t)n + 1);
    uit_dump(e, p, (size_t)n + 1);
    luaL_pushresultsize(&b, (size_t)n);
    return 1;
}

static int l_uit_error(lua_State *L)
{
    lua_pushstring(L, uit_error(moycore_uit(L)));
    return 1;
}

static int l_uit_error_code(lua_State *L)
{
    lua_pushstring(L, uit_error_code(moycore_uit(L)));
    return 1;
}

static void moycore_uitree_open(lua_State *L)
{
    static const luaL_Reg methods[] = {
        {"state", l_uit_state}, {"model", l_uit_model}, {"row_of", l_uit_row_of},
        {"labels", l_uit_labels}, {"action", l_uit_action}, {"action_row", l_uit_action_row},
        {"set_int", l_uit_set_int}, {"set_num", l_uit_set_num}, {"set_text", l_uit_set_text},
        {"set_absent", l_uit_set_absent}, {"set_model", l_uit_set_model},
        {"get_int", l_uit_get_int}, {"get_text", l_uit_get_text}, {"get_cell", l_uit_get_cell},
        {"absent", l_uit_absent},
        {"build_begin", l_uit_build_begin}, {"node", l_uit_node}, {"prop", l_uit_prop},
        {"node_action", l_uit_node_action}, {"build_end", l_uit_build_end},
        {"open", l_uit_open}, {"close", l_uit_close}, {"want_focus", l_uit_want_focus},
        {"frame", l_uit_frame}, {"on_draw", l_uit_on_draw}, {"next", l_uit_next}, {"trace_next", l_uit_trace_next},
        {"can", l_uit_can}, {"letter", l_uit_letter}, {"dialog", l_uit_dialog},
        {"unfocused", l_uit_unfocused}, {"back_dialog", l_uit_back_dialog},
        {"back_popover", l_uit_back_popover}, {"focused", l_uit_focused},
        {"popover", l_uit_popover}, {"hints", l_uit_hints}, {"leave", l_uit_leave},
        {"dump", l_uit_dump}, {"error", l_uit_error}, {"error_code", l_uit_error_code},
        {"free", l_uit_free},
        {NULL, NULL},
    };
    if (luaL_newmetatable(L, MOYCORE_UIT_MT)) {
        luaL_newlib(L, methods);
        lua_setfield(L, -2, "__index");
        lua_pushcfunction(L, l_uit_free);
        lua_setfield(L, -2, "__gc");
    }
    lua_pop(L, 1);
    lua_pushcfunction(L, l_uit_new);
    lua_setglobal(L, "__moy_uitree");
}

#endif /* MOYCORE_UITREE_H */
