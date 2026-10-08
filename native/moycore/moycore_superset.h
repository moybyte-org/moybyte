/* The superset names a Lua run gets from C (docs/kernel_cartpath_2026-10.md
 * §2's table): moybyte's names outside libmoy's verb table whose answer is a
 * number or the pointer, so a frame that calls them makes no crossing.
 *
 *   col(name_or_index)  a colour's index: a name from the base sixteen
 *                       (runtime/palette.py's NAMES; an unknown name is 7), or
 *                       a number truncated and masked to 0..63
 *   mouse()             x, y, left, middle, right, scroll_x, scroll_y: the
 *                       pointer in the cart's coordinates from the run's
 *                       snapshot, `left` the press edge (cart_api.mouse)
 *
 * Included by modmoycore.c (the boards and the browser) and by
 * runtime/moyhost_lua.c (the host), and opened beside moycore_layers_open, so
 * the two Lua hosts answer these names with one body. The registration loops
 * skip them (runtime/lua_ext.py's NATIVE_NAMES): a trampoline registered over
 * them would shadow the C.
 */
#ifndef MOYCORE_SUPERSET_H
#define MOYCORE_SUPERSET_H

#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moycore_run.h"

static const char *const MOYCORE_COL_NAMES[16] = {
    "black", "dark_blue", "dark_purple", "dark_green", "brown", "dark_grey",
    "light_grey", "white", "red", "orange", "yellow", "green", "blue", "indigo",
    "pink", "peach",
};

static int moycore_col(lua_State *L)
{
    int t = lua_type(L, 1);
    if (t == LUA_TSTRING) {
        const char *name = lua_tostring(L, 1);
        for (int i = 0; i < 16; i++) {
            if (strcmp(name, MOYCORE_COL_NAMES[i]) == 0) {
                lua_pushinteger(L, i);
                return 1;
            }
        }
        lua_pushinteger(L, 7);
        return 1;
    }
    if (t == LUA_TBOOLEAN) {
        lua_pushinteger(L, lua_toboolean(L, 1));
        return 1;
    }
    if (t == LUA_TNUMBER) {
        lua_Integer i;
        if (lua_isinteger(L, 1)) {
            i = lua_tointeger(L, 1);
        } else {
            lua_Number n = lua_tonumber(L, 1);
            if (!(n == n) || n >= (lua_Number)2.0e9f || n <= (lua_Number)-2.0e9f)
                return luaL_error(L, "col: not a colour");
            i = (lua_Integer)n;      /* toward zero, as int() */
        }
        lua_pushinteger(L, i & 63);
        return 1;
    }
    return luaL_error(L, "col: not a colour");
}

static int moycore_mouse(lua_State *L)
{
    const moycore_run_t *c = moycore_run_cur;
    int st = (c && c->snap) ? c->snap[SNAP_TOUCH_DOWN] : 0;
    lua_pushinteger(L, st ? c->snap[SNAP_TOUCH_X] : 0);
    lua_pushinteger(L, st ? c->snap[SNAP_TOUCH_Y] : 0);
    lua_pushboolean(L, (st & 4) != 0);      /* the press edge */
    lua_pushboolean(L, 0);
    lua_pushboolean(L, 0);
    lua_pushinteger(L, 0);
    lua_pushinteger(L, 0);
    return 7;
}

static void moycore_superset_open(lua_State *L)
{
    lua_pushcfunction(L, moycore_col);
    lua_setglobal(L, "col");
    lua_pushcfunction(L, moycore_mouse);
    lua_setglobal(L, "mouse");
}

#endif
