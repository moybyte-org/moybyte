/* A Lua cart's layers, drawn by libmoy's own verbs (#225).
 *
 * Included by modmoycore.c (the boards and the browser) and by
 * runtime/moyhost_lua.c (the host), so the two Lua hosts draw into a layer
 * through one body.
 *
 * A moybyte layer is the CONSOLE's object -- device_canvas's off-heap RGB565
 * buffer, lent to the run, composited by draw_layer's blit_window -- and the
 * prelude in runtime/lua_ext.py is what a Lua cart holds. Drawing into it is
 * libmoy's: a layer method points con->canvas at a moy_canvas over the
 * layer's buffer for the length of one call and runs the screen's own verb,
 * the trick libmoy's binding plays for its own layers (moy_lua.c's
 * l_layer_method) and the shape of the wasm binding's target(). So a layer
 * answers every verb SPEC.md 6 gives it with the screen's C, its draw state
 * (camera, clip, pal, palt, fillp) is its own and outlives the frame, and
 * nothing per call crosses into Python -- sspr's ten arguments included.
 *
 * The buffer reaches the VM in two steps, because only Python holds it and
 * only Lua code may allocate on the VM: the host's bind (moycore.layer_bind,
 * hl_layer_bind), which the __layer_new trampoline calls, PARKS it here, and
 * __layer_canvas() -- the next thing the prelude's make_layer calls -- builds
 * the canvas over it as a userdata the layer's table holds. An allocation
 * failing inside the trampoline would unwind through the host frames under
 * it; failing in __layer_canvas it is an ordinary Lua error.
 *
 * The userdata never owns the pixels. The prelude's handle registry pins the
 * console's layer object for the run, and the run's VM -- with every canvas
 * in it -- is closed before the console reclaims the buffers.
 */
#ifndef MOYCORE_LAYERS_H
#define MOYCORE_LAYERS_H

#include <stddef.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"

#define MOYCORE_LAYER_MT "moybyte.layer"

typedef struct {
    moy_console *con;            /* whose canvas a layer method swaps */
    moy_pixel   *pix;            /* parked by the bind, taken by the canvas */
    int          w, h;
} moycore_layers;

/* Park a layer's buffer for the next __layer_canvas. 0 on success; nonzero
 * when the buffer cannot hold w x h pixels. Touches no Lua state. */
static int moycore_layers_park(moycore_layers *ls, void *pix, size_t nbytes,
                               int w, int h)
{
    if (!pix || w <= 0 || h <= 0
        || nbytes / sizeof(moy_pixel) < (size_t)w * (size_t)h)
        return 1;
    ls->pix = (moy_pixel *)pix;
    ls->w = w;
    ls->h = h;
    return 0;
}

/* __layer_canvas() -> the parked buffer as a canvas, wired as the screen is:
 * a layer is composited onto the screen verbatim, so it must encode colours
 * the way the screen does (and that includes a cart's SPEC.md 3.1 palette,
 * which the console's layer carries too). */
static int moycore_layers_canvas(lua_State *L)
{
    moycore_layers *ls = (moycore_layers *)lua_touserdata(L, lua_upvalueindex(1));
    moy_canvas *c;
    if (!ls->pix) return luaL_error(L, "make_layer: no layer buffer was bound");
    c = (moy_canvas *)lua_newuserdatauv(L, sizeof(moy_canvas), 0);
    moy_canvas_init(c, ls->pix, ls->w, ls->h);
#ifdef MOY_PIXEL_RGB565
    moy_canvas_wire(c, ls->con->canvas->wire);
#endif
    ls->pix = NULL;
    luaL_setmetatable(L, MOYCORE_LAYER_MT);
    return 1;
}

/* A layer method: upvalue 1 the context, upvalue 2 the screen verb it runs.
 * `self` is the prelude's layer table; its `__c` is the canvas, and `__e` is
 * set so draw_layer can tell the console the pixels moved behind its back.
 * The verb takes self's stack slot, so it sees exactly the arguments it
 * always does. Protected, so an error cannot leave the screen's verbs drawing
 * into the layer: the canvas is put back first and the error re-raised. */
static int moycore_layers_call(lua_State *L)
{
    moycore_layers *ls = (moycore_layers *)lua_touserdata(L, lua_upvalueindex(1));
    moy_canvas *c = NULL, *save;
    int st;
    if (lua_type(L, 1) == LUA_TTABLE) {
        lua_getfield(L, 1, "__c");
        c = (moy_canvas *)luaL_testudata(L, -1, MOYCORE_LAYER_MT);
        lua_pop(L, 1);
    }
    if (!c)
        return luaL_error(L, "a layer method needs its layer: "
                             "call it with a colon, lay:rect(...)");
    lua_pushboolean(L, 1);
    lua_setfield(L, 1, "__e");
    lua_pushvalue(L, lua_upvalueindex(2));
    lua_replace(L, 1);
    save = ls->con->canvas;
    ls->con->canvas = c;
    st = lua_pcall(L, lua_gettop(L) - 1, LUA_MULTRET, 0);
    ls->con->canvas = save;
    if (st != LUA_OK) return lua_error(L);
    return lua_gettop(L);
}

/* __layer_verb(fn) -> the layer method that runs `fn` against the layer. */
static int moycore_layers_verb(lua_State *L)
{
    luaL_checktype(L, 1, LUA_TFUNCTION);
    lua_pushvalue(L, lua_upvalueindex(1));
    lua_pushvalue(L, 1);
    lua_pushcclosure(L, moycore_layers_call, 2);
    return 1;
}

/* Install __layer_canvas and __layer_verb, which the prelude captures and
 * clears before the cart runs. `ls` must outlive the VM. */
static void moycore_layers_open(lua_State *L, moycore_layers *ls,
                                moy_console *con)
{
    ls->con = con;
    ls->pix = NULL;
    luaL_newmetatable(L, MOYCORE_LAYER_MT);
    lua_pop(L, 1);
    lua_pushlightuserdata(L, ls);
    lua_pushcclosure(L, moycore_layers_canvas, 1);
    lua_setglobal(L, "__layer_canvas");
    lua_pushlightuserdata(L, ls);
    lua_pushcclosure(L, moycore_layers_verb, 1);
    lua_setglobal(L, "__layer_verb");
}

#endif
