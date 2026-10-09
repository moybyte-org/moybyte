/* A Lua cart's placed-actor scenes in C (docs/kernel_cartpath_2026-10.md §2's
 * table: scene, load_scene, actors, touching, move_actor, move_actor_to,
 * remove_actor, draw_scene).
 *
 * The cart's .moyscene texts are the run's, handed over before the load
 * (moycore_scenes_put, in the order runtime/widgets.py's Scenes keeps, the
 * first the default active scene), so the Editor's unsaved placement reaches
 * the run as it reaches a Python cart. Three natives, which the handles
 * prelude (runtime/lua_ext.py's PRELUDE_HANDLES) captures and clears:
 *
 *   __scene_names()     the scene names in order
 *   __scene_rows(name)  a scene's rows as fresh tables {tag, tile, x, y,
 *                       flip, flags}, parsed from its JSON as Scenes._parse
 *                       parses it; an unknown name or a text that is not a
 *                       JSON list is no rows
 *   __draw_scene(rows)  the rows drawn as cart_api's draw_scene draws the
 *                       world: hidden, size, the three rotation styles, say
 *
 * The live world is the prelude's Lua tables in the run's own state, so a
 * frame that moves, removes or draws actors makes no crossing.
 *
 * Included by moycore_lua.c (the boards and the browser) and by
 * runtime/moyhost_lua.c (the host); each owns one moycore_scenes_t and its
 * allocator.
 */
#ifndef MOYCORE_SCENE_H
#define MOYCORE_SCENE_H

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"
#include "moy_json.h"
#include "moycore_run.h"

#define MOYCORE_SCENES 32

typedef struct {
    int n;
    char *name[MOYCORE_SCENES];      /* NUL-terminated */
    char *text[MOYCORE_SCENES];      /* the .moyscene, len[i] bytes */
    size_t len[MOYCORE_SCENES];
} moycore_scenes_t;

typedef void *(*moycore_scene_alloc_fn)(size_t n);
typedef void (*moycore_scene_free_fn)(void *p);

/* Scene `name`'s text, replacing one already put by that name. 0, or -1 when
 * the table is full or the allocation fails. */
static inline int moycore_scenes_put(moycore_scenes_t *s, const char *name,
                                     const char *text, size_t n,
                                     moycore_scene_alloc_fn alloc,
                                     moycore_scene_free_fn fr)
{
    size_t nn = strlen(name);
    int i;
    char *t = (char *)alloc(n ? n : 1);
    if (!t) return -1;
    memcpy(t, text, n);
    for (i = 0; i < s->n; i++) {
        if (strcmp(s->name[i], name) == 0) {
            fr(s->text[i]);
            s->text[i] = t;
            s->len[i] = n;
            return 0;
        }
    }
    if (s->n >= MOYCORE_SCENES) {
        fr(t);
        return -1;
    }
    s->name[s->n] = (char *)alloc(nn + 1);
    if (!s->name[s->n]) {
        fr(t);
        return -1;
    }
    memcpy(s->name[s->n], name, nn + 1);
    s->text[s->n] = t;
    s->len[s->n] = n;
    s->n++;
    return 0;
}

static inline void moycore_scenes_release(moycore_scenes_t *s, moycore_scene_free_fn fr)
{
    for (int i = 0; i < s->n; i++) {
        fr(s->name[i]);
        fr(s->text[i]);
    }
    memset(s, 0, sizeof(*s));
}

/* -- the parse: Scenes._parse and Actor's constructor ----------------------- */

/* A decoded JSON string onto the Lua stack. */
static inline void mcs_push_jstr(lua_State *L, const char *v, const char *ve)
{
    luaL_Buffer b;
    size_t cap = moy_json_strlen(v, ve);
    char *out = luaL_buffinitsize(L, &b, cap ? cap : 1);
    luaL_pushresultsize(&b, moy_json_str(v, ve, out));
}

/* int() of a field, as Actor's constructor applies it; 0 where int() raises. */
static inline lua_Integer mcs_int(const char *v, const char *ve)
{
    int64_t i = 0;
    int got = moy_json_int(v, ve, &i);
    if (got == 2) return (lua_Integer)((*v == '-') ? INT32_MIN : INT32_MAX);
    if (got != 1) return 0;
    if (i > INT32_MAX) i = INT32_MAX;
    if (i < INT32_MIN) i = INT32_MIN;
    return (lua_Integer)i;
}

static inline lua_Integer mcs_int_field(const char *o, const char *oe, const char *key)
{
    const char *v, *ve;
    if (!moy_json_get(o, oe, key, &v, &ve)) return 0;
    return mcs_int(v, ve);
}

/* A JSON number as a Lua number: an int that fits stays an integer. */
static inline void mcs_push_num(lua_State *L, const char *v, const char *ve, int kind)
{
    char tmp[64];
    size_t n = (size_t)(ve - v);
    if (kind == MOY_JSON_INT) {
        int64_t i;
        if (moy_json_int(v, ve, &i) == 1 && i >= INT32_MIN && i <= INT32_MAX) {
            lua_pushinteger(L, (lua_Integer)i);
            return;
        }
    }
    if (n >= sizeof(tmp)) n = sizeof(tmp) - 1;
    memcpy(tmp, v, n);
    tmp[n] = 0;
    lua_pushnumber(L, (lua_Number)strtod(tmp, NULL));
}

/* str() of the tag: a string as it stands, true/false/null as Python spells
 * them, anything else as its JSON text. */
static inline void mcs_push_tag(lua_State *L, const char *o, const char *oe)
{
    const char *v, *ve;
    if (!moy_json_get(o, oe, "tag", &v, &ve)) {
        lua_pushliteral(L, "");
        return;
    }
    switch (moy_json_kind(v, ve)) {
    case MOY_JSON_STR: mcs_push_jstr(L, v, ve); return;
    case MOY_JSON_TRUE: lua_pushliteral(L, "True"); return;
    case MOY_JSON_FALSE: lua_pushliteral(L, "False"); return;
    case MOY_JSON_NULL: lua_pushliteral(L, "None"); return;
    default: lua_pushlstring(L, v, (size_t)(ve - v)); return;
    }
}

/* A row's flags: the scalar members of its "flags" object (a flag Lua has no
 * shape for -- null, a list, an object -- is left out). */
static inline void mcs_push_flags(lua_State *L, const char *o, const char *oe)
{
    const char *v, *ve, *k, *ke, *m, *me;
    moy_json_iter_t it;
    lua_newtable(L);
    if (!moy_json_get(o, oe, "flags", &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_OBJ)
        return;
    moy_json_iter(&it, v, ve);
    while (moy_json_next(&it, &k, &ke, &m, &me)) {
        int kind = moy_json_kind(m, me);
        switch (kind) {
        case MOY_JSON_TRUE: case MOY_JSON_FALSE:
            mcs_push_jstr(L, k, ke);
            lua_pushboolean(L, kind == MOY_JSON_TRUE);
            break;
        case MOY_JSON_INT: case MOY_JSON_FLOAT:
            mcs_push_jstr(L, k, ke);
            mcs_push_num(L, m, me, kind);
            break;
        case MOY_JSON_STR:
            mcs_push_jstr(L, k, ke);
            mcs_push_jstr(L, m, me);
            break;
        default:
            continue;
        }
        lua_rawset(L, -3);
    }
}

static inline moycore_scenes_t *mcs_self(lua_State *L)
{
    return (moycore_scenes_t *)lua_touserdata(L, lua_upvalueindex(1));
}

static int mcs_names(lua_State *L)
{
    moycore_scenes_t *s = mcs_self(L);
    int n = s ? s->n : 0;
    lua_createtable(L, n, 0);
    for (int i = 0; i < n; i++) {
        lua_pushstring(L, s->name[i]);
        lua_rawseti(L, -2, i + 1);
    }
    return 1;
}

static int mcs_rows(lua_State *L)
{
    moycore_scenes_t *s = mcs_self(L);
    const char *name = lua_type(L, 1) == LUA_TSTRING ? lua_tostring(L, 1) : NULL;
    const char *t = NULL, *end, *v, *ve, *k, *ke, *e, *ee;
    moy_json_iter_t it;
    lua_Integer n = 0;
    lua_newtable(L);
    for (int i = 0; s && name && i < s->n; i++) {
        if (strcmp(s->name[i], name) == 0) {
            t = s->text[i];
            end = t + s->len[i];
            break;
        }
    }
    if (!t) return 1;
    v = moy_json_ws(t, end);
    ve = moy_json_value(v, end, 0);
    if (!ve || moy_json_ws(ve, end) != end || moy_json_kind(v, ve) != MOY_JSON_ARR)
        return 1;
    moy_json_iter(&it, v, ve);
    while (moy_json_next(&it, &k, &ke, &e, &ee)) {
        if (moy_json_kind(e, ee) != MOY_JSON_OBJ) continue;
        lua_createtable(L, 0, 6);
        mcs_push_tag(L, e, ee);
        lua_setfield(L, -2, "tag");
        lua_pushinteger(L, mcs_int_field(e, ee, "tile"));
        lua_setfield(L, -2, "tile");
        lua_pushinteger(L, mcs_int_field(e, ee, "x"));
        lua_setfield(L, -2, "x");
        lua_pushinteger(L, mcs_int_field(e, ee, "y"));
        lua_setfield(L, -2, "y");
        lua_pushinteger(L, mcs_int_field(e, ee, "flip"));
        lua_setfield(L, -2, "flip");
        mcs_push_flags(L, e, ee);
        lua_setfield(L, -2, "flags");
        lua_rawseti(L, -2, ++n);
    }
    return 1;
}

/* -- the draw: cart_api's draw_scene --------------------------------------- */

/* int() of a row's number; 0 for anything that is not one. */
static inline int mcs_num_field(lua_State *L, int t, const char *key)
{
    int ok = 0;
    lua_Number v;
    lua_getfield(L, t, key);
    v = lua_tonumberx(L, -1, &ok);
    lua_pop(L, 1);
    if (!ok || !(v == v)) return 0;
    if (v >= (lua_Number)2.0e9f) return 2000000000;
    if (v <= (lua_Number)-2.0e9f) return -2000000000;
    return (int)v;                   /* toward zero, as int() */
}

/* Python's truth of a flag value: nil, false, 0 and "" are false. */
static inline int mcs_truthy(lua_State *L, int i)
{
    switch (lua_type(L, i)) {
    case LUA_TNIL: return 0;
    case LUA_TBOOLEAN: return lua_toboolean(L, i);
    case LUA_TNUMBER: return lua_tonumber(L, i) != 0;
    case LUA_TSTRING: return lua_rawlen(L, i) != 0;
    default: return 1;
    }
}

static inline int mcs_floordiv(int a, int b)
{
    int q = a / b;
    if ((a % b != 0) && ((a < 0) != (b < 0))) q--;
    return q;
}

/* The canvas's Image path for a rotated tile: -1 and palt-transparent pixels
 * skipped, the rest through the draw-time store, at `scale`, camera and clip
 * applied (device_canvas's _cache_rgb and blit565). */
static void mcs_blit(moy_canvas *c, const int8_t *pix, int w, int h, int x, int y, int scale)
{
    int px0 = x - c->cam_x, py0 = y - c->cam_y;
    for (int sy = 0; sy < h; sy++) {
        for (int sx = 0; sx < w; sx++) {
            int p = pix[sy * w + sx];
            if (p < 0 || c->palt[p & 63]) continue;
            moy_pixel col = c->store[p & 63];
            int bx = px0 + sx * scale, by = py0 + sy * scale;
            int x0 = bx > c->clip_x0 ? bx : c->clip_x0;
            int y0 = by > c->clip_y0 ? by : c->clip_y0;
            int x1 = bx + scale < c->clip_x1 ? bx + scale : c->clip_x1;
            int y1 = by + scale < c->clip_y1 ? by + scale : c->clip_y1;
            for (int yy = y0; yy < y1; yy++) {
                moy_pixel *row = c->pix + (size_t)yy * (size_t)c->w;
                for (int xx = x0; xx < x1; xx++) row[xx] = col;
            }
        }
    }
}

/* widgets.rotate_indices over one sheet tile, in float as the boards'
 * MicroPython computes it: nearest-neighbour, the canvas grown so no corner
 * clips, -1 outside the source. `out` holds 16x16. */
static void mcs_rotate(const moy_sheet *s, int tile, float deg, int8_t *out, int *ow, int *oh)
{
    float a = deg * 0.017453292519943295f;
    float ca = cosf(a), sa = sinf(a);
    int w = MOY_TILE, h = MOY_TILE;
    int ox0 = (tile % MOY_SHEET_COLS) * MOY_TILE, oy0 = (tile / MOY_SHEET_COLS) * MOY_TILE;
    int W = (int)(fabsf((float)w * ca) + fabsf((float)h * sa) + 0.5f);
    int H = (int)(fabsf((float)w * sa) + fabsf((float)h * ca) + 0.5f);
    float ocx, ocy, icx = (float)(w - 1) * 0.5f, icy = (float)(h - 1) * 0.5f;
    if (W < 1) W = 1;
    if (H < 1) H = 1;
    if (W > 16) W = 16;
    if (H > 16) H = 16;
    ocx = (float)(W - 1) * 0.5f;
    ocy = (float)(H - 1) * 0.5f;
    for (int oy = 0; oy < H; oy++) {
        float ry = (float)oy - ocy;
        for (int ox = 0; ox < W; ox++) {
            float rx = (float)ox - ocx;
            float sx = ca * rx + sa * ry + icx;
            float sy = -sa * rx + ca * ry + icy;
            int ix = (int)(sx + 0.5f), iy = (int)(sy + 0.5f);
            int8_t p = -1;
            if (ix >= 0 && ix < w && iy >= 0 && iy < h)
                p = (int8_t)s->pix[(size_t)(oy0 + iy) * MOY_SHEET_W + (size_t)(ox0 + ix)];
            out[oy * W + ox] = p;
        }
    }
    *ow = W;
    *oh = H;
}

/* The say bubble: str(value)[:10] in black on a white box above the actor
 * (below it near the top edge). */
static void mcs_say(lua_State *L, moy_canvas *c, int i, int x, int y)
{
    size_t n = 0, cut = 0;
    int chars = 0;
    const char *t;
    if (lua_type(L, i) == LUA_TBOOLEAN) {
        t = lua_toboolean(L, i) ? "True" : "False";
        n = strlen(t);
    } else {
        t = luaL_tolstring(L, i, &n);   /* left on the stack; the caller drops it */
    }
    while (cut < n && chars < 10) {  /* ten characters, not bytes */
        cut++;
        while (cut < n && ((unsigned char)t[cut] & 0xC0) == 0x80) cut++;
        chars++;
    }
    int bw = chars * 8 + 4;
    int by = y >= 11 ? y - 11 : y + 9;
    moy_rect(c, x, by, bw, 10, 7);
    moy_rectb(c, x, by, bw, 10, 0);
    moy_print(c, (const uint8_t *)t, cut, x + 2, by + 1, 0);
}

#define SPR(c, sh, ...) do { if (sh) moy_spr(c, sh, __VA_ARGS__); } while (0)

static void mcs_draw_one(lua_State *L, moycore_run_t *run, int t)
{
    moy_canvas *c = run->con.canvas;
    const moy_sheet *sh = run->con.sheet;    /* NULL: no sheet, no sprites */
    int x = mcs_num_field(L, t, "x"), y = mcs_num_field(L, t, "y");
    int tile = mcs_num_field(L, t, "tile"), flip = mcs_num_field(L, t, "flip");
    int sc = 1, top;
    lua_getfield(L, t, "flags");
    if (!lua_istable(L, -1)) {
        lua_pop(L, 1);
        SPR(c, sh, tile, x, y, -1, 1, flip);
        return;
    }
    top = lua_gettop(L);
    lua_getfield(L, top, "hidden");
    if (mcs_truthy(L, -1)) {
        lua_settop(L, top - 1);
        return;
    }
    lua_getfield(L, top, "size");                 /* top + 2 */
    if (lua_isinteger(L, -1)) {
        lua_Integer v = lua_tointeger(L, -1);
        sc = mcs_floordiv((int)(v > 2000000000 ? 2000000000 : v < -2000000000 ? -2000000000 : v), 100);
    } else if (lua_type(L, -1) == LUA_TNUMBER) {
        lua_Number v = floorf((float)lua_tonumber(L, -1) / 100.0f);
        sc = v != v ? 1 : v > (lua_Number)1024 ? 1024 : (int)v;
    }
    if (sc < 1) sc = 1;
    if (sc > 1024) sc = 1024;
    lua_getfield(L, top, "dir");                  /* top + 3 */
    if (lua_type(L, -1) != LUA_TNUMBER) {
        SPR(c, sh, tile, x, y, -1, sc, flip);
    } else {
        lua_Number dir = lua_tonumber(L, -1);
        const char *style;
        lua_getfield(L, top, "rot");              /* top + 4 */
        style = lua_type(L, -1) == LUA_TSTRING ? lua_tostring(L, -1) : "all";
        if (strcmp(style, "none") == 0) {
            SPR(c, sh, tile, x, y, -1, sc, flip);
        } else if (strcmp(style, "leftright") == 0) {
            float m = fmodf((float)dir, 360.0f);
            if (m < 0) m += 360.0f;
            SPR(c, sh, tile, x, y, -1, sc, m > 180.0f ? 1 : 0);
        } else if (!sh || tile < 0 || tile >= MOY_TILES) {
            SPR(c, sh, tile, x, y, -1, sc, flip);
        } else {
            int8_t rot[16 * 16];
            int rw, rh;
            mcs_rotate(sh, tile, (float)dir - 90.0f, rot, &rw, &rh);
            int cx = x + 4 * sc, cy = y + 4 * sc;
            mcs_blit(c, rot, rw, rh, cx - mcs_floordiv(rw * sc, 2), cy - mcs_floordiv(rh * sc, 2), sc);
        }
    }
    lua_getfield(L, top, "say");
    if (mcs_truthy(L, -1)) mcs_say(L, c, lua_gettop(L), x, y);
    lua_settop(L, top - 1);
}

#undef SPR

static int mcs_draw(lua_State *L)
{
    moycore_run_t *run = moycore_run_cur;
    lua_Integer n;
    luaL_checktype(L, 1, LUA_TTABLE);
    if (!run || !run->con.canvas) return 0;
    n = (lua_Integer)lua_rawlen(L, 1);
    for (lua_Integer i = 1; i <= n; i++) {
        if (lua_rawgeti(L, 1, i) == LUA_TTABLE) mcs_draw_one(L, run, lua_gettop(L));
        lua_pop(L, 1);
    }
    return 0;
}

/* The three natives, over `s` (which outlives the Lua state; NULL: no scenes). */
static inline void moycore_scene_open(lua_State *L, moycore_scenes_t *s)
{
    lua_pushlightuserdata(L, s);
    lua_pushcclosure(L, mcs_names, 1);
    lua_setglobal(L, "__scene_names");
    lua_pushlightuserdata(L, s);
    lua_pushcclosure(L, mcs_rows, 1);
    lua_setglobal(L, "__scene_rows");
    lua_pushcfunction(L, mcs_draw);
    lua_setglobal(L, "__draw_scene");
}

#endif
