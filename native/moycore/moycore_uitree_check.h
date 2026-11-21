/* The UI API's prop check on Lua (native/moy_uitree/moy_uitree_check.c): the
 * global function `__moy_uitree_check`, which the UI tree layer
 * (runtime/run/uitree.lua) takes and clears as it loads.
 *
 *   __moy_uitree_check(kind, prop, vc, word)
 *     -> TYPE                           the kind takes the prop, so given
 *     -> TYPE or nil, CODE, WHAT, EXPECTED, FIX
 *                                       a refusal: TYPE nil where the kind
 *                                       has no such prop; EXPECTED and FIX
 *                                       "" where the message has none
 *
 * TYPE is the contract's name of the prop's type ("tpl", "cond", "int",
 * "word", "list:tpl", ...), vc a UIT_VC_* class (or'ed with UIT_VC_BUILDS),
 * word the value where it is a text (nil otherwise).
 *
 * Needs only lua.h and moy_uitree.h, so the host checker's bare Lua
 * (tools/uitree_lua_check.py) opens it as the console's moycore does. */
#ifndef MOYCORE_UITREE_CHECK_H
#define MOYCORE_UITREE_CHECK_H

#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy_uitree_check.h"

static int l_uit_check(lua_State *L)
{
    const char *kind = luaL_checkstring(L, 1), *prop = luaL_checkstring(L, 2);
    int vc = (int)luaL_checkinteger(L, 3);
    const char *word = lua_isnoneornil(L, 4) ? NULL : luaL_checkstring(L, 4);
    const char *ty = uit_prop_type(kind, prop);
    char out[1024];
    const char *code = uit_check_prop(kind, prop, vc, word, out, sizeof out);
    if (ty) lua_pushstring(L, ty);
    else lua_pushnil(L);
    if (!code) return 1;
    lua_pushstring(L, code);
    const char *s = out;
    for (int i = 0; i < 3; i++) {
        const char *e = strchr(s, '\x1f');
        size_t k = e ? (size_t)(e - s) : strlen(s);
        lua_pushlstring(L, s, k);
        s = e ? e + 1 : s + k;
    }
    return 5;
}

static void moycore_uitree_check_open(lua_State *L)
{
    lua_pushcfunction(L, l_uit_check);
    lua_setglobal(L, "__moy_uitree_check");
}

#endif /* MOYCORE_UITREE_CHECK_H */
