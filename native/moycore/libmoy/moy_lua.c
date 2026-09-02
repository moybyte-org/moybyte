/* The Lua binding (SPEC.md 4).
 *
 * SPEC.md 15 puts it plainly: "the verb table above is the contract; Lua is the
 * first binding of it, not its definition." This file is that first binding,
 * and it is deliberately small -- if binding a language to moy took a thousand
 * lines, the claim that the verb table is a narrow waist would be false.
 *
 * libmoy does NOT embed a VM. You hand it a lua_State and it installs the
 * sandbox and the globals; which Lua that is -- yours, your RTOS's, one you
 * vendored -- stays your decision, exactly as SPEC.md 4 intends. vendor/lua is
 * offered for convenience and is not required.
 *
 * THE SANDBOX IS A CEILING, NOT A SUGGESTION (SPEC.md 4.1). base minus load,
 * loadstring, dofile, require and collectgarbage; math, string, table and
 * coroutine; and nothing else. io, os, debug and package are absent -- and not
 * merely unregistered here: their SOURCES are not compiled into vendor/lua at
 * all, so there is no reachable implementation to be re-exposed by accident.
 * A host that hands out more accumulates carts that run nowhere else, which
 * breaks the format for everybody.
 */

#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

#include "moy.h"

#define CONSOLE_KEY "moy.console"

static moy_console *con_of(lua_State *L)
{
    moy_console *c;
    lua_getfield(L, LUA_REGISTRYINDEX, CONSOLE_KEY);
    c = (moy_console *)lua_touserdata(L, -1);
    lua_pop(L, 1);
    return c;
}

/* Cart coordinates are numbers, and a cart may pass a float where the console
 * wants a cell -- `circ(x, y, ...)` after a physics step. Lua's own
 * luaL_checkinteger REJECTS a non-integral float, which would turn ordinary
 * arithmetic into a crashed cart, so truncate like the reference does. */
static int argi(lua_State *L, int idx, int dflt)
{
    if (lua_isnoneornil(L, idx)) return dflt;
    return (int)lua_tonumber(L, idx);
}

/* -- drawing ------------------------------------------------------------- */

static int l_cls(lua_State *L)
{
    moy_cls(con_of(L)->canvas, argi(L, 1, 0));
    return 0;
}

static int l_pix(lua_State *L)
{
    moy_console *con = con_of(L);
    if (lua_gettop(L) >= 3) {
        moy_pix(con->canvas, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0));
        return 0;
    }
    lua_pushinteger(L, moy_pget(con->canvas, argi(L, 1, 0), argi(L, 2, 0)));
    return 1;
}

static int l_line(lua_State *L)
{
    moy_line(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
             argi(L, 3, 0), argi(L, 4, 0), argi(L, 5, 0));
    return 0;
}

static int l_rect(lua_State *L)
{
    moy_rect(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
             argi(L, 3, 0), argi(L, 4, 0), argi(L, 5, 0));
    return 0;
}

static int l_rectb(lua_State *L)
{
    moy_rectb(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
              argi(L, 3, 0), argi(L, 4, 0), argi(L, 5, 0));
    return 0;
}

static int l_circ(lua_State *L)
{
    moy_circ(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
             argi(L, 3, 0), argi(L, 4, 0));
    return 0;
}

static int l_circb(lua_State *L)
{
    moy_circb(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
              argi(L, 3, 0), argi(L, 4, 0));
    return 0;
}

static int l_tri(lua_State *L)
{
    moy_tri(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0),
            argi(L, 4, 0), argi(L, 5, 0), argi(L, 6, 0), argi(L, 7, 0));
    return 0;
}

static int l_trib(lua_State *L)
{
    moy_trib(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0),
             argi(L, 4, 0), argi(L, 5, 0), argi(L, 6, 0), argi(L, 7, 0));
    return 0;
}

static int l_print(lua_State *L)
{
    /* lua_tolstring gives the raw BYTES and their count, which is exactly what
     * SPEC.md 6 wants: print walks bytes, and a Lua string IS a byte string.
     * No decoding happens anywhere on this path, which is the whole point --
     * decoding is what made the reference disagree with itself. */
    size_t len = 0;
    const char *s;
    luaL_tolstring(L, 1, &len);         /* accepts numbers too, like Lua's print */
    s = lua_tolstring(L, -1, &len);
    moy_print(con_of(L)->canvas, (const uint8_t *)s, len,
              argi(L, 2, 0), argi(L, 3, 0), argi(L, 4, 0));
    lua_pop(L, 1);
    return 0;
}

/* Returns the PREVIOUS offset, which is what both consoles this verb is
 * modelled on do (TIC-80 and PICO-8 alike) and what the save/restore idiom
 * needs: `local px, py = camera(x, y)` ... `camera(px, py)`. The table in
 * SPEC.md 6 documented the effect and not the return, so a cart written
 * against a real console -- or ported from one -- read nil here and parked its
 * camera at the origin. Two values rather than a table: no per-call garbage,
 * the same reason touch() fans out. */
static int l_camera(lua_State *L)
{
    moy_canvas *c = con_of(L)->canvas;
    int px = c->cam_x, py = c->cam_y;
    if (lua_gettop(L) == 0) moy_camera_reset(c);
    else moy_camera(c, argi(L, 1, 0), argi(L, 2, 0));
    lua_pushinteger(L, px);
    lua_pushinteger(L, py);
    return 2;
}

static int l_clip(lua_State *L)
{
    moy_canvas *c = con_of(L)->canvas;
    if (lua_gettop(L) == 0) moy_clip_reset(c);
    else moy_clip(c, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0), argi(L, 4, 0));
    return 0;
}

static int l_pal(lua_State *L)
{
    moy_canvas *c = con_of(L)->canvas;
    if (lua_gettop(L) == 0) moy_pal_reset(c);              /* both palettes */
    else if (argi(L, 3, 0) == 1) moy_pal_screen(c, argi(L, 1, 0), argi(L, 2, 0));
    else moy_pal(c, argi(L, 1, 0), argi(L, 2, 0));
    return 0;
}

static int l_palt(lua_State *L)
{
    moy_canvas *c = con_of(L)->canvas;
    if (lua_gettop(L) == 0) moy_palt_reset(c);
    else moy_palt(c, argi(L, 1, 0), lua_toboolean(L, 2));
    return 0;
}

static int l_fillp(lua_State *L)
{
    moy_canvas *c = con_of(L)->canvas;
    if (lua_gettop(L) == 0) moy_fillp_reset(c);
    else moy_fillp(c, argi(L, 1, 0), argi(L, 2, -1));
    return 0;
}

static int l_oval(lua_State *L)
{
    moy_oval(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
             argi(L, 3, 0), argi(L, 4, 0), argi(L, 5, 0));
    return 0;
}

static int l_ovalb(lua_State *L)
{
    moy_ovalb(con_of(L)->canvas, argi(L, 1, 0), argi(L, 2, 0),
              argi(L, 3, 0), argi(L, 4, 0), argi(L, 5, 0));
    return 0;
}

/* -- sprites and map ----------------------------------------------------- */

/* moy_console's sheet and map are POINTERS, so a host may legitimately supply
 * neither: a brand-new project has no sheet drawn and no map painted yet, and
 * an embedder that only wants the geometry verbs need not invent either. The
 * raster takes them by reference and dereferences without asking -- correct
 * for a caller that has the data, fatal for a cart that calls spr() before its
 * host has any. Not a theoretical hole: two lines of Lua in an empty project
 * (`function _draw() spr(0, 0, 0) end`) segfaulted the process, which on a
 * microcontroller is a silent reset.
 *
 * So the BINDING is where the console's optional halves get checked -- once
 * per call, rather than a NULL test inside per-pixel loops that already hold
 * the pointer. The semantics follow the rule that keeps these verbs OUT of
 * SPEC.md 10: degrade truthfully. No sheet means every sprite is empty,
 * which is what an unpainted sheet looks like; no map means every cell is
 * empty, which is what mget already answers for a cell out of range. A cart
 * cannot tell "no sheet" from "a sheet full of colour 0", and should not have
 * to. */
static int l_spr(lua_State *L)
{
    moy_console *con = con_of(L);
    if (!con->sheet) return 0;
    moy_spr(con->canvas, con->sheet, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0),
            argi(L, 4, -1), argi(L, 5, 1), argi(L, 6, 0));
    return 0;
}

static int l_sspr(lua_State *L)
{
    moy_console *con = con_of(L);
    int sw, sh;
    if (!con->sheet) return 0;
    sw = argi(L, 3, 0); sh = argi(L, 4, 0);
    moy_sspr(con->canvas, con->sheet, argi(L, 1, 0), argi(L, 2, 0), sw, sh,
             argi(L, 5, 0), argi(L, 6, 0), argi(L, 7, sw), argi(L, 8, sh),
             argi(L, 9, -1), argi(L, 10, 0));
    return 0;
}

static int l_tline(lua_State *L)
{
    moy_console *con = con_of(L);
    if (!con->sheet || !con->map) return 0;
    moy_tline(con->canvas, con->sheet, con->map,
              argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0), argi(L, 4, 0),
              (int32_t)argi(L, 5, 0), (int32_t)argi(L, 6, 0),
              (int32_t)argi(L, 7, 0), (int32_t)argi(L, 8, 0),
              argi(L, 9, -1));
    return 0;
}

static int l_map(lua_State *L)
{
    moy_console *con = con_of(L);
    int mx, my;
    if (!con->sheet || !con->map) return 0;
    mx = argi(L, 1, 0); my = argi(L, 2, 0);
    moy_map_draw_layers(con->canvas, con->map, con->sheet, mx, my,
                        argi(L, 3, con->map->w - mx), argi(L, 4, con->map->h - my),
                        argi(L, 5, 0), argi(L, 6, 0), argi(L, 7, -1), argi(L, 8, 1),
                        argi(L, 9, 0), con->flags);
    return 0;
}

/* SPEC.md 7.1 tile flags. A host with no table reads 0 and drops writes --
 * the same truthful degrade as an unpainted sheet. */
static int l_fget(lua_State *L)
{
    moy_console *con = con_of(L);
    int n = argi(L, 1, -1);
    int v = (con->flags && n >= 0 && n < MOY_FLAGS) ? con->flags[n] : 0;
    if (lua_isnoneornil(L, 2)) lua_pushinteger(L, v);
    else lua_pushboolean(L, (v >> (argi(L, 2, 0) & 7)) & 1);
    return 1;
}

static int l_fset(lua_State *L)
{
    moy_console *con = con_of(L);
    int n = argi(L, 1, -1);
    if (!con->flags || n < 0 || n >= MOY_FLAGS) return 0;
    if (lua_isnoneornil(L, 3)) {                 /* fset(n, byte) */
        con->flags[n] = (uint8_t)(argi(L, 2, 0) & 0xFF);
    } else {                                     /* fset(n, bit, on) */
        int bit = 1 << (argi(L, 2, 0) & 7);
        if (lua_toboolean(L, 3)) con->flags[n] = (uint8_t)(con->flags[n] | bit);
        else con->flags[n] = (uint8_t)(con->flags[n] & ~bit);
    }
    return 0;
}

static int l_sget(lua_State *L)
{
    moy_console *con = con_of(L);
    /* 0 is what an unpainted sheet reads, so "no sheet" needs no second case. */
    lua_pushinteger(L, con->sheet
                    ? moy_sheet_pget(con->sheet, argi(L, 1, 0), argi(L, 2, 0)) : 0);
    return 1;
}

static int l_sset(lua_State *L)
{
    moy_console *con = con_of(L);
    if (!con->sheet) return 0;
    moy_sheet_pset(con->sheet, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, 0));
    return 0;
}

static int l_mget(lua_State *L)
{
    moy_console *con = con_of(L);
    /* -1 is what mget already answers off the edge of a map, so a cart's
     * collision code needs no second case for "no map at all". */
    lua_pushinteger(L, con->map
                    ? moy_mget(con->map, argi(L, 1, 0), argi(L, 2, 0)) : -1);
    return 1;
}

static int l_mset(lua_State *L)
{
    moy_console *con = con_of(L);
    if (!con->map) return 0;
    moy_mset(con->map, argi(L, 1, 0), argi(L, 2, 0), argi(L, 3, -1));
    return 0;
}

/* -- input (SPEC.md 7.3) ------------------------------------------------- */

static const char *const BTN_NAMES[MOY_BTN_COUNT] = {
    "left", "right", "up", "down", "a", "b", "run"
};

static int btn_index(lua_State *L, int idx)
{
    const char *name = lua_tostring(L, idx);
    int i;
    if (!name) return -1;
    for (i = 0; i < MOY_BTN_COUNT; i++)
        if (!strcmp(name, BTN_NAMES[i])) return i;
    /* An unknown name reads as not-pressed rather than raising, which is what
     * a conforming console with different hardware would report anyway. */
    return -1;
}

static int l_btn(lua_State *L)
{
    moy_console *con = con_of(L);
    int b = btn_index(L, 1);
    lua_pushboolean(L, b >= 0 && con->host.btn &&
                       con->host.btn(con->host.user, (moy_button)b, argi(L, 2, 0)));
    return 1;
}

static int l_btnp(lua_State *L)
{
    moy_console *con = con_of(L);
    int b = btn_index(L, 1);
    lua_pushboolean(L, b >= 0 && con->host.btnp &&
                       con->host.btnp(con->host.user, (moy_button)b, argi(L, 2, 0)));
    return 1;
}

static int l_players(lua_State *L)
{
    moy_console *con = con_of(L);
    /* Always at least one (SPEC.md 7.3), so a single-player cart never sees
     * this exist and a two-player cart can ask and adapt. */
    int n = con->host.players ? con->host.players(con->host.user) : 1;
    lua_pushinteger(L, n < 1 ? 1 : n);
    return 1;
}

/* -- state and utility (SPEC.md 9) --------------------------------------- */

static int l_time(lua_State *L)
{
    moy_console *con = con_of(L);
    lua_pushinteger(L, con->host.time_ms ? (lua_Integer)con->host.time_ms(con->host.user) : 0);
    return 1;
}

static int l_pmem(lua_State *L)
{
    moy_console *con = con_of(L);
    int slot = argi(L, 1, 0);
    if (slot < 0 || slot >= 256) {          /* SPEC.md 9: 256 slots */
        if (lua_gettop(L) < 2) lua_pushinteger(L, 0);
        return lua_gettop(L) < 2 ? 1 : 0;
    }
    if (lua_gettop(L) >= 2) {
        if (con->host.pmem_set)
            con->host.pmem_set(con->host.user, slot, (int32_t)lua_tointeger(L, 2));
        return 0;
    }
    lua_pushinteger(L, con->host.pmem_get ? con->host.pmem_get(con->host.user, slot) : 0);
    return 1;
}

static int l_cfg(lua_State *L)
{
    moy_console *con = con_of(L);
    const char *key = lua_tostring(L, 1);
    const char *v = (key && con->host.cfg) ? con->host.cfg(con->host.user, key) : NULL;
    if (v) {
        /* config.json is JSON, so a number in it must reach the cart AS a
         * number. The host seam passes `const char *` -- it cannot express
         * type -- so the conversion belongs here, and moycore (which parses
         * the JSON directly) is what this has to agree with.
         *
         * Pushing everything as a string was a real bug with a real victim:
         * `cfg("autoplay", 0)` returned "0", and in Lua `"0" ~= 0` is TRUE, so
         * a cart guarding its attract mode with `if auto ~= 0` played itself
         * forever on any libmoy host while behaving correctly on the reference.
         * lua_stringtonumber converts only when the WHOLE string is a number,
         * so a genuine string value stays a string. */
        if (lua_stringtonumber(L, v) == 0) lua_pushstring(L, v);
    } else {
        lua_pushvalue(L, 2);                /* the caller's default, or nil */
    }
    return 1;
}

static int l_rnd(lua_State *L)
{
    lua_Number n = lua_isnoneornil(L, 1) ? 1.0 : lua_tonumber(L, 1);
    lua_pushnumber(L, (lua_Number)moy_rnd(con_of(L), (float)n));
    return 1;
}

/* SPEC.md 9: the same seed on the same host replays the same sequence. The
 * sequence itself is this library's (xorshift32, see moy_rnd) and no other
 * host's, which is why no conformance scene may call rnd() even seeded. */
static int l_srand(lua_State *L)
{
    lua_Number v = lua_tonumber(L, 1);
    moy_srand(con_of(L), (uint32_t)(int64_t)v);
    return 0;
}

static int l_flr(lua_State *L)
{
    lua_Number v = lua_tonumber(L, 1);
    lua_pushinteger(L, (lua_Integer)(v >= 0 ? (lua_Integer)v
                                            : -(lua_Integer)(-v) - ((-v) != (lua_Number)(lua_Integer)(-v))));
    return 1;
}

static int l_quit(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.quit) con->host.quit(con->host.user);
    /* SPEC.md 9: quit ends THIS cart. Raising is how a Lua function stops the
     * cart mid-frame without the host having to poll a flag afterwards. */
    return luaL_error(L, "moy.quit");
}

static int l_sfx(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.sfx) con->host.sfx(con->host.user, argi(L, 1, 0), argi(L, 2, -1));
    return 0;
}

static int l_music(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.music)
        con->host.music(con->host.user, argi(L, 1, 0),
                        lua_isnoneornil(L, 2) ? 1 : lua_toboolean(L, 2));
    return 0;
}

/* The rest of SPEC.md 8.2 and 7.3's optional input. Every one of these must
 * EXIST in the cart's globals whether or not the host wired a hook: 8.2 says
 * a silent host "MUST NOT error". Absence of hardware is expressed in the
 * RETURN VALUES the spec assigns to it, never as a missing symbol. */

static int l_beep(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.beep)
        con->host.beep(con->host.user, (float)lua_tonumber(L, 1),
                       lua_isnoneornil(L, 2) ? 0.15f : (float)lua_tonumber(L, 2));
    return 0;
}

static int l_music_stop(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.music_stop) con->host.music_stop(con->host.user);
    return 0;
}

static int l_sound_stop(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.sound_stop) con->host.sound_stop(con->host.user, argi(L, 1, -1));
    return 0;
}

static int l_volume(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.volume) con->host.volume(con->host.user, argi(L, 1, 0));
    return 0;
}

static int l_touch(lua_State *L)
{
    moy_console *con = con_of(L);
    int v[4];
    if (!con->host.touch || !con->host.touch(con->host.user, v))
        return 0;                       /* no pointer: touch() reads as nil */
    lua_pushinteger(L, v[0]);
    lua_pushinteger(L, v[1]);
    lua_pushboolean(L, v[2]);
    lua_pushboolean(L, v[3]);
    return 4;
}

static int push_key(lua_State *L, int (*fn)(void *, int), void *user)
{
    if (lua_isnoneornil(L, 1))          /* no argument: the last typed code */
        lua_pushinteger(L, fn ? fn(user, -1) : 0);
    else
        lua_pushboolean(L, fn && fn(user, (int)lua_tointeger(L, 1)));
    return 1;
}

static int l_key(lua_State *L)
{
    moy_console *con = con_of(L);
    return push_key(L, con->host.key, con->host.user);
}

static int l_keyp(lua_State *L)
{
    moy_console *con = con_of(L);
    return push_key(L, con->host.keyp, con->host.user);
}

static int l_textmode(lua_State *L)
{
    moy_console *con = con_of(L);
    if (con->host.textmode) con->host.textmode(con->host.user, lua_toboolean(L, 1));
    return 0;
}


/* -- the host-dependent core verbs (SPEC.md 6) -----------------------------
 *
 * view, background and the layer verbs. Each does more on a host that can and
 * something truthful on one that cannot, so none is gated on a host callback
 * and none needs a cart-side guard -- SPEC.md 10 lists no standard extension
 * precisely because a verb that degrades honestly belongs in core instead.
 *
 * `layers` is a full drawing surface, and the trick that keeps it from being a
 * second implementation of the verb table is that it reuses the FIRST one: a
 * layer method swaps con->canvas, calls the ordinary verb unchanged, and swaps
 * back. Every verb already takes its canvas from the console, none of them
 * raise (argi() coerces rather than checks), and the layer's method receiver is
 * removed before the call so the verb sees exactly the arguments it always
 * does. Twenty verbs, one wrapper, and no way for the two paths to disagree
 * about what rect() means.
 */

#define LAYER_MT "moy.layer"

typedef struct {
    moy_canvas c;
    moy_pixel *pix;          /* the host's buffer, for layer_free */
} moy_lua_layer;

/* The verbs a layer answers: everything that draws, and the draw STATE that
 * scopes it. Not spr's siblings elsewhere in core, and not input/audio --
 * a layer is a surface, not a console. */
static const luaL_Reg LAYER_VERBS[] = {
    {"cls", l_cls}, {"pix", l_pix}, {"line", l_line}, {"rect", l_rect},
    {"rectb", l_rectb}, {"circ", l_circ}, {"circb", l_circb},
    {"oval", l_oval}, {"ovalb", l_ovalb},
    {"print", l_print}, {"camera", l_camera}, {"clip", l_clip},
    {"pal", l_pal}, {"palt", l_palt}, {"fillp", l_fillp},
    {"spr", l_spr}, {"map", l_map},
    {"tri", l_tri}, {"trib", l_trib}, {"sspr", l_sspr}, {"tline", l_tline},
    {NULL, NULL}
};

static int l_layer_method(lua_State *L)
{
    moy_lua_layer *ly = (moy_lua_layer *)luaL_checkudata(L, 1, LAYER_MT);
    int idx = (int)lua_tointeger(L, lua_upvalueindex(1));
    moy_console *con = con_of(L);
    moy_canvas *save;
    int n;
    lua_remove(L, 1);                       /* drop `self`: the verb sees its
                                               own argument list, unshifted */
    save = con->canvas;
    con->canvas = &ly->c;
    n = LAYER_VERBS[idx].func(L);
    con->canvas = save;
    return n;
}

static int l_make_layer(lua_State *L)
{
    moy_console *con = con_of(L);
    int w = argi(L, 1, MOY_W), h = argi(L, 2, MOY_H);
    moy_pixel *pix;
    moy_lua_layer *ly;
    /* A host that supplies no allocator can still be conforming only if no
     * cart asks -- 1.1 reserves one layer, so this is the "asked for more than
     * I reserved" path and nil is its answer, same as a refused allocation. */
    if (w <= 0 || h <= 0 || !con->host.layer_new) return 0;      /* nil */
    pix = con->host.layer_new(con->host.user, w, h);
    if (!pix) return 0;          /* the host declined (no room): nil, not an
                                    error -- a cart can test for it */
    ly = (moy_lua_layer *)lua_newuserdata(L, sizeof(moy_lua_layer));
    ly->pix = pix;
    moy_canvas_init(&ly->c, pix, w, h);
#ifdef MOY_PIXEL_RGB565
    /* A layer is composited onto the screen verbatim, so it must encode
     * colours the way the screen does. */
    moy_canvas_wire(&ly->c, con->canvas->wire);
#endif
    luaL_getmetatable(L, LAYER_MT);
    lua_setmetatable(L, -2);
    return 1;
}

static int l_draw_layer(lua_State *L)
{
    moy_console *con = con_of(L);
    moy_lua_layer *ly = (moy_lua_layer *)luaL_checkudata(L, 1, LAYER_MT);
    moy_blit_window(con->canvas, &ly->c, argi(L, 2, 0), argi(L, 3, 0));
    return 0;
}

static int l_background(lua_State *L)
{
    moy_console *con = con_of(L);
    con->bg = argi(L, 1, 0);
    con->has_bg = 1;
    /* A host that can do better than a full clear -- restore a cached
     * backdrop, blit a prepared layer -- takes it here. One that cannot does
     * nothing, and moy_lua_draw clears for it. Either way the cart's call
     * worked, which is why this verb needs no guard. */
    if (con->host.background) con->host.background(con->host.user, con->bg);
    return 0;
}

static int l_layer_gc(lua_State *L)
{
    moy_lua_layer *ly = (moy_lua_layer *)luaL_checkudata(L, 1, LAYER_MT);
    moy_console *con = con_of(L);
    if (ly->pix && con->host.layer_free) {
        con->host.layer_free(con->host.user, ly->pix);
    }
    ly->pix = NULL;
    return 0;
}

static int l_view(lua_State *L)
{
    moy_console *con = con_of(L);
    con->view_w = argi(L, 1, MOY_W);
    con->view_h = argi(L, 2, MOY_H);
    if (con->view_w < 0) con->view_w = 0;
    if (con->view_h < 0) con->view_h = 0;
    /* The declaration is recorded whatever the host does with it: a host may
     * poll con->view_w instead of taking a callback, and a host that ignores
     * it entirely simply presents the whole canvas -- the cart's region drawn
     * unscaled, which is the honest degrade and the reason this verb does not
     * need guarding. */
    if (con->host.view) con->host.view(con->host.user, con->view_w, con->view_h);
    return 0;
}

/* Install the core verbs whose effect -- not existence -- depends on the host. */
static void open_host_verbs(lua_State *L, moy_console *con)
{
    /* All CORE (SPEC.md 6). None of these is gated on a host callback, because
     * none of them can leave a cart unable to tell it was denied: view and
     * background degrade truthfully (unscaled presentation; a clear), and
     * 1.1's floor reserves one full-screen layer, so make_layer succeeds at
     * least once everywhere. What a host may still refuse is the SECOND layer
     * -- which surfaces as nil from make_layer, an ordinary allocation failure
     * a cart tests for rather than a verb that is missing. */
    lua_pushcfunction(L, l_view);
    lua_setglobal(L, "view");
    lua_pushcfunction(L, l_background);
    lua_setglobal(L, "background");
    {
        int i;
        luaL_newmetatable(L, LAYER_MT);
        lua_newtable(L);                              /* the method table */
        for (i = 0; LAYER_VERBS[i].name; i++) {
            lua_pushinteger(L, i);
            lua_pushcclosure(L, l_layer_method, 1);
            lua_setfield(L, -2, LAYER_VERBS[i].name);
        }
        lua_setfield(L, -2, "__index");
        lua_pushcfunction(L, l_layer_gc);
        lua_setfield(L, -2, "__gc");
        lua_pop(L, 1);                                /* the metatable */
        lua_pushcfunction(L, l_make_layer);
        lua_setglobal(L, "make_layer");
        lua_pushcfunction(L, l_draw_layer);
        lua_setglobal(L, "draw_layer");
    }
}

/* -- installation -------------------------------------------------------- */


static const luaL_Reg VERBS[] = {
    {"cls", l_cls}, {"pix", l_pix}, {"line", l_line}, {"rect", l_rect},
    {"rectb", l_rectb}, {"circ", l_circ}, {"circb", l_circb},
    {"oval", l_oval}, {"ovalb", l_ovalb},
    {"print", l_print}, {"camera", l_camera}, {"clip", l_clip},
    {"pal", l_pal}, {"palt", l_palt}, {"fillp", l_fillp},
    {"spr", l_spr}, {"map", l_map}, {"mget", l_mget}, {"mset", l_mset},
    {"sget", l_sget}, {"sset", l_sset}, {"fget", l_fget}, {"fset", l_fset},
    {"btn", l_btn}, {"btnp", l_btnp}, {"players", l_players},
    {"time", l_time}, {"pmem", l_pmem}, {"cfg", l_cfg},
    {"rnd", l_rnd}, {"srand", l_srand}, {"flr", l_flr}, {"quit", l_quit},
    {"sfx", l_sfx}, {"music", l_music}, {"beep", l_beep},
    {"music_stop", l_music_stop}, {"sound_stop", l_sound_stop},
    {"volume", l_volume},
    {"touch", l_touch}, {"key", l_key}, {"keyp", l_keyp},
    {"textmode", l_textmode},
    /* PROVISIONAL -- SPEC.md 6.1, not part of core 0.2. */
    {"tri", l_tri}, {"trib", l_trib}, {"sspr", l_sspr}, {"tline", l_tline},
    {NULL, NULL}
};

/* SPEC.md 4.1 removes these from base. base is opened for the rest of what it
 * provides (print is replaced by ours, pairs/ipairs/type/tostring/... stay), so
 * these are cleared afterwards. The LIBRARIES it names are never opened at all
 * -- see the requiref list below. */
static const char *const BANNED[] = {
    "load", "loadstring", "dofile", "loadfile", "require", "collectgarbage",
    "io", "os", "debug", "package", NULL
};

/* Exactly SPEC.md 4.1's list, and no luaL_openlibs anywhere near it.
 *
 * This is not a stylistic choice. luaL_openlibs lives in linit.c, which
 * references every standard library including the four the spec forbids -- so
 * calling it would pull io, os, debug and package into the binary and leave
 * the sandbox depending on nil-ing them out afterwards. Opening the five
 * permitted libraries by hand means linit.c is not compiled, those four have
 * no reachable implementation, and "absent entirely" is true of the machine
 * code rather than only of the global table. coroutine joined the permitted
 * set on 2026-09-02 (SPEC.md 4.1): pure VM, no reach outside it. */
static const luaL_Reg SANDBOX_LIBS[] = {
    {LUA_GNAME,      luaopen_base},
    {LUA_MATHLIBNAME, luaopen_math},
    {LUA_STRLIBNAME,  luaopen_string},
    {LUA_TABLIBNAME,  luaopen_table},
    {LUA_COLIBNAME,   luaopen_coroutine},
    {NULL, NULL}
};

int moy_lua_open(struct lua_State *Ls, moy_console *con)
{
    lua_State *L = (lua_State *)Ls;
    const luaL_Reg *v;
    int i;

    for (v = SANDBOX_LIBS; v->name; v++) {  /* base, math, string, table ... */
        luaL_requiref(L, v->name, v->func, 1);
        lua_pop(L, 1);
    }
    for (i = 0; BANNED[i]; i++) {           /* ... minus SPEC.md 4.1's list */
        lua_pushnil(L);
        lua_setglobal(L, BANNED[i]);
    }
    lua_pushlightuserdata(L, con);
    lua_setfield(L, LUA_REGISTRYINDEX, CONSOLE_KEY);

    for (v = VERBS; v->name; v++) {
        lua_pushcfunction(L, v->func);
        lua_setglobal(L, v->name);
    }
    /* The rest of core: the verbs whose EFFECT the host varies, never their
     * presence (SPEC.md 6). Installed unconditionally, like the table above. */
    open_host_verbs(L, con);
    /* SPEC.md 9: read these, do not assume 320x240. */
    lua_pushinteger(L, con->canvas->w);
    lua_setglobal(L, "W");
    lua_pushinteger(L, con->canvas->h);
    lua_setglobal(L, "H");
    return 0;
}

static int call_hook(lua_State *L, const char *name, int nargs,
                     char *err, size_t errlen)
{
    /* An absent hook is not an error: SPEC.md 4 says all three are optional. */
    if (lua_getglobal(L, name) != LUA_TFUNCTION) {
        lua_pop(L, 1 + nargs);
        return 0;
    }
    if (nargs) lua_insert(L, -1 - nargs);
    if (lua_pcall(L, nargs, 0, 0) != LUA_OK) {
        const char *msg = lua_tostring(L, -1);
        /* quit() is a normal ending, not a failure (SPEC.md 9). */
        if (msg && strstr(msg, "moy.quit")) {
            lua_pop(L, 1);
            return 0;
        }
        if (err && errlen) {
            /* The message carries the script line number, which SPEC.md 4.3
             * requires a host to show the player. */
            strncpy(err, msg ? msg : "unknown error", errlen - 1);
            err[errlen - 1] = 0;
        }
        lua_pop(L, 1);
        return 1;
    }
    return 0;
}

int moy_lua_init(struct lua_State *L, char *err, size_t errlen)
{
    return call_hook((lua_State *)L, "_init", 0, err, errlen);
}

int moy_lua_update(struct lua_State *L, float dt, char *err, size_t errlen)
{
    lua_pushnumber((lua_State *)L, (lua_Number)dt);
    return call_hook((lua_State *)L, "_update", 1, err, errlen);
}

int moy_lua_draw(struct lua_State *L, char *err, size_t errlen)
{
    /* SPEC.md 6: background(x) declares a backdrop repainted each frame. A
     * host that took the callback has already done it its own way; one that
     * did not gets it here, which is what lets a cart call background()
     * unguarded on every console. Before _draw, where the cart would have
     * cls()'d itself. */
    moy_console *con = con_of((lua_State *)L);
    if (con && con->has_bg && !con->host.background) {
        moy_cls(con->canvas, con->bg);
    }
    return call_hook((lua_State *)L, "_draw", 0, err, errlen);
}
