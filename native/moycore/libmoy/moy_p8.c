/* The PICO-8 machine, for ported carts (PICO8.md). Opt-in, host verbs.
 *
 * A PICO-8 cart is written against a memory map: the sheet, the map, the
 * flags, the draw state and the screen all have addresses, and the
 * community's idioms lean on that -- palette fades by memcpy into 0x5f00,
 * screen-to-sheet copies to bake a texture, a shadow drawn by peek/poke over
 * 0x6000. The p8 port shim gives those verbs a sparse Lua table unless the
 * host offers this: a flat 64 KB byte array that is the truth, with every
 * region that has a console object behind it kept in step on the write, and
 * the SCREEN reading and writing the canvas itself, so pix()/spr() and poke()
 * see one picture.
 *
 *   0x0000-0x1fff  sheet, 4bpp, low nibble = left pixel (rows 64-127 double
 *                  as map rows 32-63, as on PICO-8)
 *   0x2000-0x2fff  map rows 0-31 (console cells hold tile+1)
 *   0x3000-0x30ff  tile flags -- the console's own table (SPEC.md 3.5)
 *   0x5f00-0x5f0f  draw palette (bit 4 = transparent, bit 7 = the secret
 *                  sixteen, which a ported cart's palette ships at 16-31)
 *   0x5f10-0x5f1f  screen palette
 *   0x5f20-0x5f23  clip
 *   0x5f25         pen colour, 0x5f26-0x5f27 the print cursor
 *   0x5f28-0x5f2b  camera, int16 LE
 *   0x5f31-0x5f33  fill pattern, then its transparency bit
 *   0x6000-0x7fff  screen, 128x128 4bpp
 *
 * Everything else is plain RAM. The palette, camera and clip bytes READ from
 * the canvas rather than the array, so whichever path wrote them -- pal() or
 * poke() -- peek() answers the truth. Draw state still resets every frame
 * (SPEC.md 6); __moy_p8_frame re-applies what PICO-8 keeps.
 *
 * THE DRAW VERBS ARE HERE TOO, and that is what the addresses above are for.
 * A shim draw verb was four to six binding calls -- fl() on each coordinate,
 * pcol() on the colour, a fill-pattern check, then the console verb -- and a
 * binding call has a floor of ~1.65us on the reference console's slow boards,
 * so a p8 `pset` cost five of them where PICO-8 costs one. Each __moy_p8_*
 * verb below takes the cart's raw arguments and resolves p8's semantics from
 * the machine: the pen at 0x5f25 when the colour is nil, the fill pattern at
 * 0x5f31, the draw palette and transparency at 0x5f00, the camera and clip
 * the console already holds. The state the shim used to keep in Lua locals
 * moved into those bytes, so peek() and poke() of them agree with the verbs.
 *
 * These are `__moy_*` globals a ported cart's shim probes for nil-safe, not
 * verbs of SPEC.md: a host that opens them is offering a machine, not a
 * language. The binding is deliberately the cheapest this VM allows -- no
 * registry lookup per byte, the integer fast path, one array index -- because
 * a PICO-8 screen is 8,192 bytes a frame.
 *
 * Measured (moy-spec proposals/p8-memory-map.md): ~0.9 us a byte on the
 * reference console's fast board, ~1.5 us on its slow ones, against 8-13 us
 * for the sparse table the shim keeps for hosts without this.
 */

#include <math.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"
#include "moy_pixel.h"

/* The machine rides each verb as an UPVALUE, not a registry entry: a registry
 * lookup is a string hash per call, and on the reference console's fast board
 * that alone was the difference between 0.9 us and 1.8 us a byte -- the very
 * tax the memory-map proposal measured libmoy's generic binding paying. */
static inline moy_p8 *p8_of(lua_State *L)
{
    return (moy_p8 *)lua_touserdata(L, lua_upvalueindex(1));
}

/* The p8 draw state that used to be Lua locals in the shim, at the addresses
 * PICO-8 keeps it -- so `poke(0x5f25, 8)` and `color(8)` are one thing. */
#define P8_PEN   0x5f25u        /* pen colour, the byte color() masks to 0x8f */
#define P8_CURX  0x5f26u        /* print cursor; the full value is in moy_p8 */
#define P8_CURY  0x5f27u
#define P8_FILLP 0x5f31u        /* fill pattern lo, hi, then its transparency */
#define P8_SPAL  0x5f10u        /* screen palette, kept across frames */

/* uint32 -> the int32 with the same bits, without leaning on the
 * implementation-defined narrowing conversion. */
static inline int32_t u2i(uint32_t v)
{
    return (v & 0x80000000u) ? (int32_t)(v & 0x7fffffffu) - 2147483647 - 1
                             : (int32_t)v;
}

/* float -> int32, wrapping instead of trapping. C leaves the cast UNDEFINED
 * out of range and a p8 cart reaches out of it routinely -- a garbage
 * address, a multiply that overflows -- so the answer is pinned here rather
 * than left to the CPU. fmod is exact, so every build agrees on it. */
static int32_t f2i(lua_Number f)
{
    double d = (double)f;
    if (d >= -2147483648.0 && d < 2147483648.0) return (int32_t)d;
    if (!(d == d)) return 0;                             /* NaN */
    d = fmod(d, 4294967296.0);
    if (d < 0) d += 4294967296.0;
    if (d >= 2147483648.0) d -= 4294967296.0;
    return (int32_t)d;
}

static inline int32_t iarg(lua_State *L, int i)
{
    int isnum;
    lua_Integer v = lua_tointegerx(L, i, &isnum);
    if (isnum) return (int32_t)v;
    return f2i(lua_tonumber(L, i));
}

/* The shim spelled the multi-byte forms `cpeek(a + i)`, so the offset lands
 * on the ARGUMENT and is truncated after -- and on this VM (LUA_32BITS) that
 * addition wraps at 32 bits. Reproduced rather than simplified, because
 * trunc(a) + i and trunc(a + i) part company for a negative fraction. */
static int32_t iarg_off(lua_State *L, int i, int32_t off)
{
    int isnum;
    lua_Integer v = lua_tointegerx(L, i, &isnum);
    if (isnum) return u2i((uint32_t)(int32_t)v + (uint32_t)off);
    return f2i((lua_Number)(lua_tonumber(L, i) + (lua_Number)off));
}

/* The shim's fl(): p8 coerces every API number argument -- nil is 0, a
 * numeric string is its number, and anything else (`pset(x, y, color)`, the
 * API function, in picooffroad) is 0 rather than an error -- and then floors.
 * An integer keeps every bit; only the float lane goes through lua_Number,
 * which is a SINGLE-PRECISION float here. */
static int32_t p8_fl(lua_State *L, int i)
{
    int isnum;
    lua_Number f;
    if (lua_isinteger(L, i)) return (int32_t)lua_tointeger(L, i);
    f = lua_tonumberx(L, i, &isnum);
    if (!isnum) return 0;
    return f2i((lua_Number)floor((double)f));
}

/* -- the screen: the canvas IS the screen region ------------------------- */

static inline moy_pixel raw_pixel(const moy_canvas *c, int idx)
{
#ifdef MOY_PIXEL_RGB565
    return c->wire[idx & 15];
#else
    (void)c;
    return (moy_pixel)(idx & 15);
#endif
}

static inline int pixel_index(const moy_canvas *c, moy_pixel px)
{
#ifdef MOY_PIXEL_RGB565
    int i;
    for (i = 0; i < 16; i++) if (c->wire[i] == px) return i;
    return 0;
#else
    (void)c;
    return (int)px & 15;
#endif
}

static inline void scr_write(moy_p8 *p, uint32_t a, uint8_t v)
{
    moy_canvas *c = p->con->canvas;
    uint32_t off = a - 0x6000;
    int x = (int)((off & 63) * 2), y = (int)(off >> 6);
    if (x + 1 < c->w && y < c->h) {
        moy_pixel *q = c->pix + (size_t)y * (size_t)c->w + (size_t)x;
        q[0] = raw_pixel(c, v & 15);
        q[1] = raw_pixel(c, v >> 4);
    }
}

static inline uint8_t scr_read(const moy_p8 *p, uint32_t a)
{
    const moy_canvas *c = p->con->canvas;
    uint32_t off = a - 0x6000;
    int x = (int)((off & 63) * 2), y = (int)(off >> 6);
    if (x + 1 < c->w && y < c->h) {
        const moy_pixel *q = c->pix + (size_t)y * (size_t)c->w + (size_t)x;
        return (uint8_t)(pixel_index(c, q[0]) | (pixel_index(c, q[1]) << 4));
    }
    return p->mem[a];
}

/* -- the other mirrored regions ------------------------------------------ */

static inline void sheet_write(moy_p8 *p, uint32_t a, uint8_t v)
{
    if (p->con->sheet) {
        uint8_t *px = p->con->sheet->pix + (size_t)(a >> 6) * MOY_SHEET_W + (a & 63) * 2;
        px[0] = v & 15;
        px[1] = v >> 4;
    }
}

static inline void map_write(moy_p8 *p, int row, int col, uint8_t v)
{
    moy_map *m = p->con->map;
    if (m && m->w == 128 && row < m->h)
        m->cells[(size_t)row * 128 + (size_t)col] = (uint8_t)(v == 255 ? 255 : v + 1);
}

/* A PICO-8 colour byte -> a console index. The SCREEN palette (0x5f10) may
 * name the secret sixteen with bit 7, which a ported cart's manifest palette
 * carries at 16-31; the DRAW palette (0x5f00) is four bits, because PICO-8's
 * screen memory holds a nibble -- a cart that ORs 0x80 into a draw-palette
 * entry (dank tomb does, for colour 3) draws the low nibble there. And back. */
static inline int col_in(uint8_t v)  { return (v & 0x80) ? 16 + (v & 15) : (v & 15); }
static inline uint8_t col_out(int i) { return (i >= 16 && i < 32) ? (uint8_t)(0x80 | (i - 16)) : (uint8_t)(i & 15); }

/* The side effect of a byte landing at `a` (already stored in mem). */
static void apply(moy_p8 *p, uint32_t a, uint8_t v)
{
    moy_canvas *c = p->con->canvas;
    if (a < 0x2000) {
        sheet_write(p, a, v);
        if (a >= 0x1000) map_write(p, 32 + (int)((a - 0x1000) >> 7), (int)(a & 127), v);
    } else if (a < 0x3000) {
        map_write(p, (int)((a - 0x2000) >> 7), (int)(a & 127), v);
    } else if (a < 0x3100) {
        if (p->con->flags) p->con->flags[a - 0x3000] = v;
    } else if (a >= 0x6000 && a < 0x8000) {
        scr_write(p, a, v);
    } else if (a >= 0x5f00 && a < 0x5f10) {
        /* Four colour bits, as VRAM is. Transparency is bit 4 (what palt()
         * writes) OR bit 7: dank tomb marks its sprite key, colour 3, by
         * ORing 0x80 into every light-level palette it copies here, and
         * nothing else it does could make that colour transparent. */
        moy_pal(c, (int)(a - 0x5f00), v & 15);
        moy_palt(c, (int)(a - 0x5f00), (v & 0x90) != 0);
    } else if (a >= 0x5f10 && a < 0x5f20) {
        moy_pal_screen(c, (int)(a - 0x5f10), col_in(v));
    } else if (a >= 0x5f20 && a < 0x5f24) {
        const uint8_t *m = p->mem + 0x5f20;
        c->clip_x0 = m[0]; c->clip_y0 = m[1];
        c->clip_x1 = m[2]; c->clip_y1 = m[3];
    } else if (a >= 0x5f28 && a < 0x5f2c) {
        const uint8_t *m = p->mem + 0x5f28;
        c->cam_x = (int16_t)(m[0] | (m[1] << 8));
        c->cam_y = (int16_t)(m[2] | (m[3] << 8));
    } else if (a == 0x5f26) {
        p->cur_x = v;                    /* the cursor's byte half; see moy.h */
    } else if (a == 0x5f27) {
        p->cur_y = v;
    }
}

static inline uint8_t rd(const moy_p8 *p, uint32_t a)
{
    const moy_canvas *c = p->con->canvas;
    if (a >= 0x6000 && a < 0x8000) return scr_read(p, a);
    if (a >= 0x5f00 && a < 0x5f10) {
        int i = (int)(a - 0x5f00);
        return (uint8_t)((c->pal[i] & 15) | (c->palt[i] ? 0x10 : 0));
    }
    if (a >= 0x5f10 && a < 0x5f20) return col_out(c->spal[a - 0x5f10]);
    if (a >= 0x5f20 && a < 0x5f24) {
        int v[4] = { c->clip_x0, c->clip_y0, c->clip_x1, c->clip_y1 };
        return (uint8_t)v[a - 0x5f20];
    }
    if (a >= 0x5f28 && a < 0x5f2c) {
        int v[4] = { c->cam_x & 255, (c->cam_x >> 8) & 255,
                     c->cam_y & 255, (c->cam_y >> 8) & 255 };
        return (uint8_t)v[a - 0x5f28];
    }
    if (a == 0x5f26) return (uint8_t)p->cur_x;
    if (a == 0x5f27) return (uint8_t)p->cur_y;
    if (a >= 0x3000 && a < 0x3100 && p->con->flags) return p->con->flags[a - 0x3000];
    return p->mem[a];
}

/* -- the verbs ------------------------------------------------------------ */

static void poke_byte(moy_p8 *p, uint32_t a, uint8_t v)
{
    a &= 0xffffu;                                  /* p8 wraps addresses */
    p->mem[a] = v;
    apply(p, a, v);
}

static inline uint8_t peek_byte(moy_p8 *p, uint32_t a)
{
    return rd(p, a & 0xffffu);
}

/* poke(a, v, b1, b2, ...): p8 0.2 pokes a whole run from one call, and the
 * shim's Lua paid a select() per byte for it. */
static int l_poke(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int top = lua_gettop(L), i;
    poke_byte(p, (uint32_t)iarg(L, 1), (uint8_t)iarg(L, 2));
    for (i = 3; i <= top; i++)
        poke_byte(p, (uint32_t)iarg_off(L, 1, (int32_t)(i - 2)), (uint8_t)iarg(L, i));
    return 0;
}

/* peek(a) and peek(a, n): n bytes as n RESULTS, which is what the shim built
 * a table and unpacked to get. */
static int l_peek(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t k, i;
    if (!lua_isnoneornil(L, 2)) {
        lua_Number n = luaL_checknumber(L, 2);
        if (!(n <= 1)) {                     /* NaN takes the multi path too */
            k = p8_fl(L, 2);
            if (k <= 0) return 0;
            if (!lua_checkstack(L, k))
                return luaL_error(L, "too many results to unpack");
            for (i = 0; i < k; i++)
                lua_pushinteger(L, peek_byte(p, (uint32_t)iarg_off(L, 1, i)));
            return k;
        }
    }
    lua_pushinteger(L, peek_byte(p, (uint32_t)iarg(L, 1)));
    return 1;
}

/* peek2/poke2 are int16 LE; peek4/poke4 are the 16.16 fixed-point word, which
 * is the only place a p8 number's real representation surfaces in this API.
 * Both address every byte separately, so a read at 0xffff wraps to 0. */
static int l_peek2(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t a = (uint32_t)p8_fl(L, 1);
    int32_t v = peek_byte(p, a) | (peek_byte(p, a + 1u) << 8);
    lua_pushinteger(L, v >= 0x8000 ? v - 0x10000 : v);
    return 1;
}

static int l_poke2(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t a = (uint32_t)p8_fl(L, 1);
    uint32_t v = (uint32_t)p8_fl(L, 2) & 0xffffu;
    poke_byte(p, a, (uint8_t)v);
    poke_byte(p, a + 1u, (uint8_t)(v >> 8));
    return 0;
}

static int l_peek4(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t a = (uint32_t)p8_fl(L, 1);
    uint32_t v = (uint32_t)peek_byte(p, a)
               | ((uint32_t)peek_byte(p, a + 1u) << 8)
               | ((uint32_t)peek_byte(p, a + 2u) << 16)
               | ((uint32_t)peek_byte(p, a + 3u) << 24);
    lua_pushnumber(L, (lua_Number)((lua_Number)u2i(v) / (lua_Number)65536.0));
    return 1;
}

static int l_poke4(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t a = (uint32_t)p8_fl(L, 1), raw;
    /* fl(v * 65536): an INTEGER v multiplies and wraps as an integer, a float
     * multiplies as a float and is floored after. The two round differently
     * past 24 bits, and the shim's Lua parts them the same way. */
    if (lua_isinteger(L, 2)) {
        raw = (uint32_t)(int32_t)lua_tointeger(L, 2) * 65536u;
    } else {
        int isnum;
        lua_Number f = lua_tonumberx(L, 2, &isnum);
        raw = isnum ? (uint32_t)f2i((lua_Number)floor(
                          (double)(lua_Number)(f * (lua_Number)65536.0))) : 0u;
    }
    poke_byte(p, a, (uint8_t)raw);
    poke_byte(p, a + 1u, (uint8_t)(raw >> 8));
    poke_byte(p, a + 2u, (uint8_t)(raw >> 16));
    poke_byte(p, a + 3u, (uint8_t)(raw >> 24));
    return 0;
}

/* A range's mirrored bytes are only ever current on the console side: pull
 * them into the array before a copy reads them. */
static void sync_in(moy_p8 *p, uint32_t lo, uint32_t n)
{
    uint32_t a, hi = lo + n;
    if (hi > MOY_P8_MEM) hi = MOY_P8_MEM;
    for (a = lo; a < hi; a++) {
        if (a >= 0x6000 && a < 0x8000) { p->mem[a] = scr_read(p, a); continue; }
        if (a >= 0x5f00 && a < 0x5f2c) { p->mem[a] = rd(p, a); continue; }
        if (a >= 0x3000 && a < 0x3100) { p->mem[a] = rd(p, a); continue; }
        if (a >= 0x8000) break;
        if (a == 0x3100) a = 0x5eff;              /* plain RAM: skip ahead */
    }
}

static void apply_range(moy_p8 *p, uint32_t d, uint32_t n)
{
    uint32_t a;
    for (a = d; a < d + n; a++) {
        if (a >= 0x3100 && a < 0x5f00) { a = 0x5eff; continue; }   /* plain RAM */
        if (a >= 0x8000) break;
        apply(p, a, p->mem[a]);
    }
}

static int l_memcpy(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t d = (uint32_t)iarg(L, 1) & 0xffffu, s = (uint32_t)iarg(L, 2) & 0xffffu;
    int32_t n = iarg(L, 3);
    if (n <= 0 || d >= MOY_P8_MEM || s >= MOY_P8_MEM) return 0;
    if ((uint32_t)n > MOY_P8_MEM - d) n = (int32_t)(MOY_P8_MEM - d);
    if ((uint32_t)n > MOY_P8_MEM - s) n = (int32_t)(MOY_P8_MEM - s);
    if (s < 0x8000) sync_in(p, s, (uint32_t)n);
    memmove(p->mem + d, p->mem + s, (size_t)n);
    apply_range(p, d, (uint32_t)n);
    return 0;
}

static int l_memset(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t d = (uint32_t)iarg(L, 1) & 0xffffu;
    uint8_t v = (uint8_t)iarg(L, 2);
    int32_t n = iarg(L, 3);
    if (n <= 0 || d >= MOY_P8_MEM) return 0;
    if ((uint32_t)n > MOY_P8_MEM - d) n = (int32_t)(MOY_P8_MEM - d);
    memset(p->mem + d, v, (size_t)n);
    apply_range(p, d, (uint32_t)n);
    return 0;
}

/* __moy_lut_span(from, to, lut): the span a ported cart lights its screen
 * with -- `for a = from, to do poke(a, peek(lut | peek(a))) end`, a run of
 * memory pushed through a lookup table. The porter folds that statement into
 * one call (p8_lua_port.fold_lut_span) because a PICO-8 screen is 8,192 bytes
 * and the loop spends three to five binding calls on each of them.
 *
 * THREE PLAIN INTEGERS OR NOTHING. Lua's numeric `for` coerces its bounds and
 * `|` refuses a non-integral float, and transcribing either of those here
 * would be a second copy of rules the shim already owns -- so anything else
 * returns false and the shim runs the loop this is the image of. Everything
 * that is here goes through peek_byte/poke_byte, so the address wrap and the
 * screen window (which reads and writes the canvas, not the array) are the
 * ones every other verb sees.
 *
 * `lut | v` is an ORDINARY integer OR, no 16.16 conversion: the shim's `|` is
 * the VM's, both operands are already integers by the time it runs, and an
 * address it lands outside 0x0000-0xffff wraps exactly as peek's would. */
static int l_lut_span(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    lua_Integer from, to, lut;
    lua_Unsigned i, n;
    uint32_t a;
    if (!lua_isinteger(L, 1) || !lua_isinteger(L, 2) || !lua_isinteger(L, 3)) {
        lua_pushboolean(L, 0);
        return 1;
    }
    from = lua_tointeger(L, 1);
    to = lua_tointeger(L, 2);
    lut = lua_tointeger(L, 3);
    lua_pushboolean(L, 1);
    if (to < from) return 1;                          /* p8's empty range */
    n = (lua_Unsigned)to - (lua_Unsigned)from;
    /* The address is carried already truncated, and stepping the truncated
     * one is what the loop does: peek narrows to int32 every iteration, and
     * consecutive addresses narrow to consecutive int32s. */
    a = (uint32_t)(int32_t)from;
    for (i = 0; ; i++, a++) {
        uint8_t v = peek_byte(p, a);
        poke_byte(p, a, peek_byte(p, (uint32_t)(int32_t)(lut | (lua_Integer)v)));
        if (i == n) break;
    }
    return 1;
}

/* reload(dst, src, len): PICO-8 copies from the cart ROM -- the sheet, map,
 * flags and sound data as the cart file holds them -- into RAM. The ROM here
 * is the seeded image, snapshotted before the cart's first write; cstore is
 * the reverse, into the snapshot only (nothing is persisted). */
static int l_reload(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t d = (uint32_t)iarg(L, 1) & 0xffffu, s = (uint32_t)iarg(L, 2) & 0xffffu;
    int32_t n = lua_isnoneornil(L, 3) ? MOY_P8_ROM : iarg(L, 3);
    if (!p->rom || n <= 0 || d >= MOY_P8_MEM || s >= MOY_P8_ROM) return 0;
    if ((uint32_t)n > MOY_P8_MEM - d) n = (int32_t)(MOY_P8_MEM - d);
    if ((uint32_t)n > MOY_P8_ROM - s) n = (int32_t)(MOY_P8_ROM - s);
    memcpy(p->mem + d, p->rom + s, (size_t)n);
    apply_range(p, d, (uint32_t)n);
    return 0;
}

static int l_cstore(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t d = (uint32_t)iarg(L, 1) & 0xffffu, s = (uint32_t)iarg(L, 2) & 0xffffu;
    int32_t n = lua_isnoneornil(L, 3) ? MOY_P8_ROM : iarg(L, 3);
    if (!p->rom || n <= 0 || d >= MOY_P8_ROM || s >= MOY_P8_MEM) return 0;
    if ((uint32_t)n > MOY_P8_ROM - d) n = (int32_t)(MOY_P8_ROM - d);
    if ((uint32_t)n > MOY_P8_MEM - s) n = (int32_t)(MOY_P8_MEM - s);
    if (s < 0x8000) sync_in(p, s, (uint32_t)n);
    memcpy(p->rom + d, p->mem + s, (size_t)n);
    return 0;
}

/* -- the map and flag verbs ----------------------------------------------
 *
 * Memory is the truth on a host with the machine: a cell poked at 0x2000 (or
 * 0x1000, the rows the map shares with the sheet) is the cell mget reads and
 * map() draws. The shim reached it through peek/poke and floored on the way;
 * this is the same walk with the Lua taken out.
 *
 * The coordinates stay DOUBLE until the bound check, because math.floor of a
 * float too big for an integer hands the float back, and such a value is out
 * of every bound here -- narrowing first would wrap it into range.
 */

/* math.floor(v or 0), undecided between integer and float. */
static double p8_flr_d(lua_State *L, int i)
{
    if (lua_isinteger(L, i)) return (double)lua_tointeger(L, i);
    if (!lua_toboolean(L, i)) return 0;
    return floor((double)luaL_checknumber(L, i));
}

/* fl(v), the coercing one, same treatment. */
static double p8_fl_d(lua_State *L, int i)
{
    int isnum;
    lua_Number f;
    if (lua_isinteger(L, i)) return (double)lua_tointeger(L, i);
    f = lua_tonumberx(L, i, &isnum);
    if (!isnum) return 0;
    return floor((double)f);
}

/* Lua's `//` on integers: a FLOOR, not a truncation, which is a whole cell of
 * difference for a camera left of the origin. */
static int32_t p8_floordiv(int32_t m, int32_t n)
{
    int32_t q = m / n;
    if ((m % n) != 0 && ((m < 0) != (n < 0))) q--;
    return q;
}

static uint32_t p8_maddr(double x, double y)
{
    double a = (y < 32) ? 0x2000 + y * 128 + x
                        : 0x1000 + (y - 32) * 128 + x;
    return (uint32_t)f2i((lua_Number)a) & 0xffffu;
}

static int l_mget(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    double x = p8_flr_d(L, 1), y = p8_flr_d(L, 2);
    if (x < 0 || x > 127 || y < 0 || y > 63) { lua_pushinteger(L, 0); return 1; }
    lua_pushinteger(L, rd(p, p8_maddr(x, y)));
    return 1;
}

static int l_mset(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    double x = p8_flr_d(L, 1), y = p8_flr_d(L, 2);
    if (x < 0 || x > 127 || y < 0 || y > 63) return 0;
    poke_byte(p, p8_maddr(x, y), (uint8_t)iarg(L, 3));
    return 0;
}

/* fget/fset read the console's own flag table (SPEC.md 3.5), which the
 * machine keeps in step with 0x3000, so both names see one answer. */
static int l_p8fget(lua_State *L)
{
    moy_console *con = p8_of(L)->con;
    double n = p8_fl_d(L, 1);
    int v = (con->flags && n >= 0 && n < MOY_FLAGS)
            ? con->flags[(int)n] : 0;
    if (lua_isnoneornil(L, 2)) lua_pushinteger(L, v);
    else lua_pushboolean(L, (v >> (f2i((lua_Number)p8_fl_d(L, 2)) & 7)) & 1);
    return 1;
}

static int l_p8fset(lua_State *L)
{
    moy_console *con = p8_of(L)->con;
    double n = p8_fl_d(L, 1);
    int i;
    if (!con->flags || !(n >= 0 && n < MOY_FLAGS)) return 0;
    i = (int)n;
    if (lua_isnoneornil(L, 3)) {                       /* fset(n, byte) */
        con->flags[i] = (uint8_t)(f2i((lua_Number)p8_fl_d(L, 2)) & 0xff);
    } else {                                           /* fset(n, bit, on) */
        int bit = 1 << (f2i((lua_Number)p8_fl_d(L, 2)) & 7);
        if (lua_toboolean(L, 3)) con->flags[i] = (uint8_t)(con->flags[i] | bit);
        else con->flags[i] = (uint8_t)(con->flags[i] & ~bit);
    }
    return 0;
}

/* -- the PICO-8 system font ----------------------------------------------
 *
 * 3x5 glyphs on a 4px advance, 6px line height; the P8SCII picture glyphs
 * 128-153 are 7x5 on an 8px advance. The SAME bitmaps the shim carries in
 * Lua for a host without this (p8_lua_port.py, P8_GLYPHS / P8_WIDE) --
 * test/p8_font_check.py holds the two equal. The six button glyphs resolve
 * to letters: this console's buttons ARE named A and B, and the arrows are
 * the d-pad. Through moy_put, so pal, camera and clip apply and the fill
 * pattern never does, exactly like the console's own print. */

static const uint16_t P8_GLYPHS[96] = {
    0x0000, 0x2092, 0x002d, 0x5f7d, 0x2f9f, 0x52a5, 0x7adb, 0x000a,
    0x224a, 0x2922, 0x55d5, 0x05d0, 0x1400, 0x01c0, 0x2000, 0x1494,
    0x7b6f, 0x7493, 0x73e7, 0x79a7, 0x49ed, 0x79cf, 0x7bc9, 0x4927,
    0x7bef, 0x49ef, 0x0410, 0x1410, 0x4454, 0x0e38, 0x1511, 0x21a7,
    0x636a, 0x5f78, 0x7ad8, 0x7278, 0x3b58, 0x72f8, 0x12f8, 0x7a78,
    0x5f68, 0x74b8, 0x34b8, 0x5ae8, 0x7248, 0x5bf8, 0x5b58, 0x3b70,
    0x1f78, 0x6750, 0x5778, 0x3870, 0x24b8, 0x6b68, 0x2f68, 0x7f68,
    0x5aa8, 0x79e8, 0x7338, 0x324b, 0x4491, 0x6926, 0x002a, 0x7000,
    0x0022, 0x5bef, 0x7aef, 0x624e, 0x7b6b, 0x72cf, 0x12cf, 0x7a4e,
    0x5bed, 0x7497, 0x3497, 0x5aed, 0x7249, 0x5b7f, 0x5b6b, 0x3b6e,
    0x13ef, 0x676a, 0x5aef, 0x39ce, 0x2497, 0x6b6d, 0x2f6d, 0x7f6d,
    0x5aad, 0x79ed, 0x72a7, 0x64d6, 0x2492, 0x3593, 0x03e0, 0x7b50,
};

static const uint8_t P8_WIDE[26 * 5] = {
    0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x55, 0x2a, 0x55, 0x2a, 0x55,
    0x41, 0x77, 0x7f, 0x55, 0x3e, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x11, 0x44, 0x11, 0x44, 0x11, 0x08, 0x49, 0x3e, 0x49, 0x08,
    0x1c, 0x3e, 0x7f, 0x3e, 0x1c, 0x36, 0x7f, 0x7f, 0x3e, 0x08,
    0x1c, 0x22, 0x49, 0x22, 0x1c, 0x1c, 0x1c, 0x7f, 0x08, 0x14,
    0x08, 0x3e, 0x7f, 0x41, 0x5d, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x3e, 0x41, 0x55, 0x5d, 0x3e, 0x78, 0x48, 0x08, 0x0e, 0x0e,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x08, 0x1c, 0x3e, 0x1c, 0x08,
    0x00, 0x00, 0x00, 0x00, 0x49, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x08, 0x1c, 0x7f, 0x3e, 0x36, 0x7f, 0x3e, 0x08, 0x3e, 0x7f,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3e, 0x41, 0x00, 0x00,
    0x08, 0x14, 0x22, 0x41, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x7f, 0x00, 0x7f, 0x00, 0x7f, 0x55, 0x55, 0x55, 0x55, 0x55,
};

static int btn_glyph(int b)
{
    switch (b) {
    case 139: return '<'; case 145: return '>'; case 148: return '^';
    case 131: return 'v'; case 142: return 'A'; case 151: return 'B';
    default:  return b;
    }
}

/* P8SCII control codes (PICO-8 0.2.2+), the subset carts print with: a
 * byte below 32 is a command, most take one parameter character read as a
 * base-36 digit ("0"-"9", "a"-"z").
 *   \0 stop    \* repeat    \# background    \- \| \+ cursor nudges
 *   \^ special: w wide, t tall, i invert, b border, - off, g home, c clear,
 *      j jump, s tab width, x y d r params skipped, 1-9 delays ignored
 *   \a audio (skipped to the next space)   \b backspace   \t tab   \n   \r
 *   \v decorate (param skipped)   \f foreground   \014 \015 font (ignored)
 * Wide doubles every column, tall every row -- the 3x5 becomes 6x10 on an
 * 8x12 cell, which is how a title is set. */
static int p8_digit(int ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'z') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'Z') return ch - 'A' + 10;
    return 0;
}

typedef struct {
    moy_canvas *c;
    moy_ds ds;                   /* camera, clip and the raster, read once */
    int fg, bg, wide, tall, invert;
    int ocol, obits, oonly;      /* \^o outline: colour (-1 none), 8 neighbour bits, interior skipped */
    int ouse_fg;                 /* outline in the current colour ("$" / "!") */
} p8_pen;

static void p8_cell(const p8_pen *pen, int cx, int cy, int w, int h, int col)
{
    const moy_ds *d = &pen->ds;
    moy_pixel px = pen->c->store[col & 63];
    int x0 = cx - d->cam_x, y0 = cy - d->cam_y, x1 = x0 + w, y1 = y0 + h, y;
    if (x0 < d->cx0) x0 = d->cx0;
    if (y0 < d->cy0) y0 = d->cy0;
    if (x1 > d->cx1) x1 = d->cx1;
    if (y1 > d->cy1) y1 = d->cy1;
    for (y = y0; y < y1 && x1 > x0; y++)
        moy_fill(d->pix + (size_t)y * (size_t)d->cw + (size_t)x0, px,
                 (size_t)(x1 - x0));
}


/* One glyph at the pen: returns the advance.
 *
 * Rasterised into a small local bitmap first -- ONE WORD A ROW, bit xx a
 * column -- so an outline (\^o) can be painted only where the glyph itself
 * is not: PICO-8 draws the neighbours and then the interior, and "!" skips
 * the interior, which is only empty if the outline never covered it. Scaled
 * dots and a 1px outline both fit in 16x12 with a one-pixel margin all round,
 * which is what puts every shift below in range without a test.
 *
 * The rows are what make the outline affordable. Cell by cell it was 18x14
 * cells each testing eight neighbours -- 94% of this function, and 28% of one
 * cart's entire frame, for a title drawn wide, tall and outlined every frame.
 * A row's outline is the union of its eight shifted neighbour rows with the
 * glyph's own row taken out, which is eight shifts and an AND. Same pixels: a
 * cell is painted exactly when it is unlit and an active direction finds a
 * lit neighbour, and the colour is the same however many times it is written. */
#define P8_BW 18
#define P8_BH 14
#define P8_BMASK ((uint32_t)((1u << P8_BW) - 1u))

static void p8_lit(const p8_pen *pen, int b, uint32_t lit[P8_BH])
{
    int sx = 1 + pen->wide, sy = 1 + pen->tall, q, r, kk, yy;
    uint32_t col = (uint32_t)((1u << sx) - 1u);
    memset(lit, 0, sizeof(uint32_t) * P8_BH);
#define LIT(gx, gy) \
        for (yy = 0; yy < sy; yy++) \
            lit[1 + (gy) * sy + yy] |= col << (1 + (gx) * sx)
    if (b >= 128 && b < 128 + 26) {
        const uint8_t *rows = P8_WIDE + (b - 128) * 5;
        for (r = 0; r < 5; r++)
            for (kk = 0; kk < 7; kk++)
                if ((rows[r] >> kk) & 1) { LIT(kk, r); }
    } else if (b >= 32 && b < 128) {
        unsigned g = P8_GLYPHS[b - 32];
        for (q = 0; q < 15; q++)
            if ((g >> q) & 1u) { LIT(q % 3, q / 3); }
    }
#undef LIT
}

/* One bitmap row onto the canvas at screen row y. The clip test on y is the
 * whole row's, so it is asked once; the colour is resolved once a glyph. */
static void p8_row(const p8_pen *pen, uint32_t bits, int cx, int y, moy_pixel px)
{
    const moy_ds *d = &pen->ds;
    moy_pixel *row;
    int sy = y - d->cam_y, xx = 0, base = cx - 1 - d->cam_x;
    if (!bits || sy < d->cy0 || sy >= d->cy1) return;
    row = d->pix + (size_t)sy * (size_t)d->cw;
    while (bits) {
        if (bits & 1u) {
            int sx = base + xx;
            if (sx >= d->cx0 && sx < d->cx1) row[sx] = px;
        }
        bits >>= 1;
        xx++;
    }
}

static int p8_glyph(const p8_pen *pen, int b, int cx, int cy)
{
    static const int dx[8] = { -1, 0, 1, -1, 1, -1, 0, 1 };
    static const int dy[8] = { -1, -1, -1, 0, 0, 1, 1, 1 };
    int sx = 1 + pen->wide, sy = 1 + pen->tall;
    int fg = pen->fg, bg = pen->bg;
    int adv, yy, i, hmax = 1 + 5 * sy;   /* the last row an outline can touch */
    uint32_t lit[P8_BH];
    b = btn_glyph(b);
    if (b >= 128 && b < 128 + 26) {
        const uint8_t *rows = P8_WIDE + (b - 128) * 5;
        int r, any = 0;
        for (r = 0; r < 5; r++) any |= rows[r];
        adv = any ? 8 * sx : 4 * sx;
    } else {
        adv = 4 * sx;
    }
    if (pen->invert) { p8_cell(pen, cx, cy, adv, 6 * sy, fg); fg = bg < 0 ? 0 : bg; }
    else if (bg >= 0) p8_cell(pen, cx, cy, adv, 6 * sy, bg);
    p8_lit(pen, b, lit);
    if (pen->ocol >= 0 || pen->ouse_fg) {
        moy_pixel opx = pen->c->store[(pen->ouse_fg ? fg : pen->ocol) & 63];
        for (yy = 0; yy <= hmax; yy++) {
            uint32_t o = 0;
            for (i = 0; i < 8; i++) {
                int ny = yy + dy[i];             /* the row a lit neighbour is in */
                if (!((pen->obits >> i) & 1) || ny < 0 || ny >= P8_BH) continue;
                o |= dx[i] > 0 ? lit[ny] >> 1 : (dx[i] < 0 ? lit[ny] << 1 : lit[ny]);
            }
            p8_row(pen, o & ~lit[yy] & P8_BMASK, cx, cy + yy - 1, opx);
        }
        if (pen->oonly) return adv;
    }
    {
        moy_pixel px = pen->c->store[fg & 63];
        for (yy = 0; yy <= hmax; yy++) p8_row(pen, lit[yy], cx, cy + yy - 1, px);
    }
    return adv;
}

/* The string, at (x, y), in colour `col` -- everything print does once its
 * arguments are resolved. Split out so the shim's `print` (which resolves the
 * pen, the cursor and p8's number formatting) is one binding call and not
 * five: __moy_p8_print below is the whole verb, __moy_p8print the raw one an
 * older shim calls. */
static int p8_text(moy_p8 *p, const char *s, size_t len, int x, int y, int col)
{
    p8_pen pen;
    size_t k;
    int cx, cy, tabw = 16, repeat = 1;
    pen.c = p->con->canvas;
    pen.ds = moy_ds_of(pen.c);           /* cls is the only thing print calls
                                            that touches the raster, and it
                                            moves neither camera nor clip */
    pen.fg = col; pen.bg = -1; pen.wide = pen.tall = pen.invert = 0;
    pen.ocol = -1; pen.obits = 0; pen.oonly = 0; pen.ouse_fg = 0;
    cx = x; cy = y;
    for (k = 0; k < len; k++) {
        int b = (unsigned char)s[k];
        if (b >= 32 || b >= 128) {
            int n = repeat, adv = 0;
            repeat = 1;
            while (n-- > 0) adv = p8_glyph(&pen, b, cx += adv, cy);
            cx += adv;
            continue;
        }
        switch (b) {
        case 0:  k = len; break;
        case 1:  if (k + 1 < len) repeat = p8_digit((unsigned char)s[++k]); if (repeat < 1) repeat = 1; break;
        case 2:  if (k + 1 < len) pen.bg = p8_digit((unsigned char)s[++k]) & 15; break;
        case 3:  if (k + 1 < len) cx += p8_digit((unsigned char)s[++k]) - 16; break;
        case 4:  if (k + 1 < len) cy += p8_digit((unsigned char)s[++k]) - 16; break;
        case 5:  if (k + 2 < len) { cx += p8_digit((unsigned char)s[k + 1]) - 16;
                                     cy += p8_digit((unsigned char)s[k + 2]) - 16; }
                 k += 2; break;
        case 6:
            if (k + 1 >= len) break;
            switch ((unsigned char)s[++k]) {
            case 'w': pen.wide = 1; break;
            case 't': pen.tall = 1; break;
            case 'i': pen.invert = 1; break;
            case '-':
                if (k + 1 >= len) break;
                switch ((unsigned char)s[++k]) {
                case 'w': pen.wide = 0; break;
                case 't': pen.tall = 0; break;
                case 'i': pen.invert = 0; break;
                case 'o': pen.ocol = -1; pen.oonly = 0; pen.obits = 0; pen.ouse_fg = 0; break;
                case '#': pen.bg = -1; break;
                default: break;
                }
                break;
            case 'o':                                 /* outline: colour, then two hex digits */
                if (k + 3 < len) {
                    int oc = (unsigned char)s[k + 1];
                    pen.oonly = 0;
                    if (oc == '$') pen.ocol = -2;         /* the current colour, resolved at draw */
                    else if (oc == '!') { pen.ocol = -2; pen.oonly = 1; }
                    else pen.ocol = p8_digit(oc) & 15;
                    pen.obits = (p8_digit((unsigned char)s[k + 2]) << 4)
                              | p8_digit((unsigned char)s[k + 3]);
                    if (pen.ocol == -2) { pen.ocol = -1; pen.ouse_fg = 1; } else pen.ouse_fg = 0;
                    k += 3;
                }
                break;
            case '#': pen.bg = pen.bg < 0 ? 0 : pen.bg; break;   /* solid background on */
            case 'g': cx = x; cy = y; break;
            case 'c': if (k + 1 < len) moy_cls(pen.c, p8_digit((unsigned char)s[++k]) & 15); cx = x; cy = y; break;
            case 'j': if (k + 2 < len) { cx = p8_digit((unsigned char)s[k + 1]) * 4;
                                         cy = p8_digit((unsigned char)s[k + 2]) * 4; }
                      k += 2; break;
            case 's': if (k + 1 < len) tabw = p8_digit((unsigned char)s[++k]); if (tabw < 1) tabw = 16; break;
            case 'x': case 'y': case 'd': case 'r': k++; break;   /* one param, ignored */
            default: break;                                       /* b = p 1-9: nothing to do */
            }
            break;
        case 7:  while (k + 1 < len && s[k + 1] != ' ') k++; break;
        case 8:  cx -= 4 * (1 + pen.wide); break;
        case 9:  cx = x + ((cx - x) / tabw + 1) * tabw; break;
        case 10: cx = x; cy += 6 * (1 + pen.tall); break;
        case 11: k++; break;
        case 12: if (k + 1 < len) pen.fg = p8_digit((unsigned char)s[++k]) & 15; break;
        case 13: cx = x; break;
        default: break;                                           /* 14, 15: font switch */
        }
    }
    return cx;                           /* PICO-8 0.2: print returns the pen x */
}

static int l_p8print(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    size_t len = 0;
    const char *s;
    int cx;
    lua_settop(L, 4);            /* the string is PUSHED, so the arguments it
                                    sits above have to be there already */
    luaL_tolstring(L, 1, &len);
    s = lua_tolstring(L, -1, &len);
    cx = p8_text(p, s, len, iarg(L, 2), iarg(L, 3), iarg(L, 4));
    lua_pop(L, 1);
    lua_pushinteger(L, cx);
    return 1;
}


/* -- the p8 DRAW verbs ----------------------------------------------------
 *
 * Each one is the shim's Lua wrapper (p8_lua_port.py) with the Lua taken
 * out, and that is the whole point: the wrapper's work was never arithmetic,
 * it was BINDING CALLS. `pset(x, y, c)` was fl(x), fl(y), fl inside pcol()
 * and then pix() -- four crossings for one pixel, each with a fixed cost the
 * board pays whatever is on the other side. Here the coercions, the pen, the
 * fill pattern and the console verb are one crossing.
 *
 * TRANSCRIBED, not reimplemented. fl() is p8's coercing floor (nil is 0, a
 * numeric string is its number, anything else is 0); a nil colour is the pen
 * at 0x5f25; a colour is four bits, because PICO-8's screen memory holds a
 * nibble; the shape verbs re-apply the fill pattern before they draw and the
 * transparent full pattern draws nothing at all. Where the shim's Lua does
 * something odd -- sset() defaulting to 6 rather than the pen, sspr() not
 * flooring at all -- the odd thing is kept, because a cart may reach either
 * lane and the two must answer alike (libmoy/test/p8lib.moy).
 *
 * ONE EDGE IS DELIBERATELY DIFFERENT. A coordinate past a 32-bit integer
 * (1e30) made the shim's `fl` hand back a float, which the binding then cast
 * to `int` -- undefined in C, and whatever the CPU did. Here it wraps, by
 * f2i, like every other address and coordinate in this file.
 */

static inline int p8_pen_col(const moy_p8 *p) { return p->mem[P8_PEN]; }

static inline unsigned p8_pat(const moy_p8 *p)
{
    return (unsigned)p->mem[P8_FILLP] | ((unsigned)p->mem[P8_FILLP + 1] << 8);
}

static inline int p8_pat_transparent(const moy_p8 *p)
{
    return p->mem[P8_FILLP + 2] & 1;
}

/* fill_skip(): a fully transparent pattern draws nothing, so the shape verbs
 * return before they touch the canvas. */
static inline int p8_fill_skip(const moy_p8 *p)
{
    return p8_pat_transparent(p) && p8_pat(p) == 0xffffu;
}

/* pcol(c): the colour a draw verb takes, four bits, the pen when nil. */
static int p8_pcol(lua_State *L, moy_p8 *p, int i)
{
    int32_t v = lua_isnoneornil(L, i) ? (int32_t)p8_pen_col(p) : p8_fl(L, i);
    return (int)(v & 15);
}

/* scol(c): the SCREEN palette's, the one place the secret sixteen (shipped at
 * 16-31 by a ported cart's manifest) can be named. */
static int p8_scol(lua_State *L, int i)
{
    int32_t v = lua_toboolean(L, i) ? p8_fl(L, i) : 0;
    if (v >= 128) return 16 + (int)(v & 15);
    return (int)(v & 15);
}

/* shape_col(c): pcol, plus the fill pattern the SHAPE verbs carry (SPEC.md 6
 * gives it to those and not to sprites or text). The high nibble of the
 * colour byte is what a pattern's holes take, unless the pattern's fractional
 * bit said they are transparent. The reset arm is what a poke at 0x5f31 needs
 * and the shim's Lua got from fillp() alone. */
static int p8_shape_col(lua_State *L, moy_p8 *p, int i)
{
    int32_t v = lua_isnoneornil(L, i) ? (int32_t)p8_pen_col(p) : p8_fl(L, i);
    moy_canvas *c = p->con->canvas;
    unsigned pat = p8_pat(p);
    if (pat)
        moy_fillp(c, (int)pat, p8_pat_transparent(p)
                               ? -1 : (int)(((uint32_t)v >> 4) & 15u));
    else if (c->fillp)
        moy_fillp_reset(c);
    return (int)(v & 15);
}

/* p8's rectangles take the FAR CORNER; the console's take a size. */
static void p8_corners(lua_State *L, int32_t *x, int32_t *y,
                       int32_t *w, int32_t *h)
{
    int32_t x0 = p8_fl(L, 1), y0 = p8_fl(L, 2);
    int32_t x1 = p8_fl(L, 3), y1 = p8_fl(L, 4), t;
    if (x1 < x0) { t = x0; x0 = x1; x1 = t; }
    if (y1 < y0) { t = y0; y0 = y1; y1 = t; }
    *x = x0;
    *y = y0;
    *w = u2i((uint32_t)x1 - (uint32_t)x0 + 1u);   /* 32-bit, as the shim's Lua */
    *h = u2i((uint32_t)y1 - (uint32_t)y0 + 1u);
}

static int l_p8_pset(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x = p8_fl(L, 1), y = p8_fl(L, 2);
    moy_pix(p->con->canvas, (int)x, (int)y, p8_pcol(L, p, 3));
    return 0;
}

static int l_p8_pget(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x = p8_fl(L, 1), y = p8_fl(L, 2);
    lua_pushinteger(L, moy_pget(p->con->canvas, (int)x, (int)y));
    return 1;
}

/* No "continue from the last endpoint" form: the shim has none either -- p8's
 * `line(x1, y1)` draws from (x1, y1) to (0, 0) here, because fl(nil) is 0.
 * That is why 0x5f3c-0x5f3f holds nothing; there is no endpoint to keep. */
static int l_p8_line(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x0, y0, x1, y1;
    if (p8_fill_skip(p)) return 0;
    x0 = p8_fl(L, 1); y0 = p8_fl(L, 2);
    x1 = p8_fl(L, 3); y1 = p8_fl(L, 4);
    moy_line(p->con->canvas, (int)x0, (int)y0, (int)x1, (int)y1,
             p8_shape_col(L, p, 5));
    return 0;
}

static int l_p8_rectfill(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x, y, w, h;
    if (p8_fill_skip(p)) return 0;
    p8_corners(L, &x, &y, &w, &h);
    moy_rect(p->con->canvas, (int)x, (int)y, (int)w, (int)h,
             p8_shape_col(L, p, 5));
    return 0;
}

static int l_p8_rect(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x, y, w, h;
    if (p8_fill_skip(p)) return 0;
    p8_corners(L, &x, &y, &w, &h);
    moy_rectb(p->con->canvas, (int)x, (int)y, (int)w, (int)h,
              p8_shape_col(L, p, 5));
    return 0;
}

static int l_p8_circfill(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x, y, r;
    if (p8_fill_skip(p)) return 0;
    x = p8_fl(L, 1); y = p8_fl(L, 2); r = p8_fl(L, 3);
    moy_circ(p->con->canvas, (int)x, (int)y, (int)r, p8_shape_col(L, p, 4));
    return 0;
}

static int l_p8_circ(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x, y, r;
    if (p8_fill_skip(p)) return 0;
    x = p8_fl(L, 1); y = p8_fl(L, 2); r = p8_fl(L, 3);
    moy_circb(p->con->canvas, (int)x, (int)y, (int)r, p8_shape_col(L, p, 4));
    return 0;
}

static int l_p8_ovalfill(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x, y, w, h;
    if (p8_fill_skip(p)) return 0;
    p8_corners(L, &x, &y, &w, &h);
    moy_oval(p->con->canvas, (int)x, (int)y, (int)w, (int)h,
             p8_shape_col(L, p, 5));
    return 0;
}

static int l_p8_oval(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x, y, w, h;
    if (p8_fill_skip(p)) return 0;
    p8_corners(L, &x, &y, &w, &h);
    moy_ovalb(p->con->canvas, (int)x, (int)y, (int)w, (int)h,
              p8_shape_col(L, p, 5));
    return 0;
}

/* `v or 1` for spr's w/h, which the shim does NOT floor. `is_one` is Lua's
 * `w == 1`, and a numeric STRING is not equal to 1 there however it converts
 * for the arithmetic below -- so the two questions are answered separately. */
static double p8_or1(lua_State *L, int i, int *is_one)
{
    int isnum;
    lua_Number f;
    if (!lua_toboolean(L, i)) { *is_one = 1; return 1.0; }
    if (lua_type(L, i) == LUA_TNUMBER) {
        f = lua_tonumber(L, i);
        *is_one = (f == (lua_Number)1);
        return (double)f;
    }
    *is_one = 0;
    f = lua_tonumberx(L, i, &isnum);
    if (!isnum) {
        luaL_error(L, "attempt to perform arithmetic on a %s value",
                   luaL_typename(L, i));
        return 0;
    }
    return (double)f;
}

/* `for k = 0, v - 1`: Lua floors a float limit onto the integer grid, and a
 * limit below zero is an empty loop. Clamped at the top because the loop that
 * would follow is not one anybody survives either way. */
static int32_t p8_for_limit(double v)
{
    double lim = floor(v - 1.0);
    if (!(lim >= 0)) return -1;                 /* NaN takes this arm too */
    if (lim > 2147483647.0) return 2147483647;
    return (int32_t)lim;
}

static int l_p8_spr(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_console *con = p->con;
    int fx = lua_toboolean(L, 6), fy = lua_toboolean(L, 7);
    int flip = (fx ? MOY_FLIP_X : 0) | (fy ? MOY_FLIP_Y : 0);
    int wone, hone;
    int32_t n, x, y;
    double w, h;
    if (!con->sheet) return 0;
    n = p8_fl(L, 1); x = p8_fl(L, 2); y = p8_fl(L, 3);
    w = p8_or1(L, 4, &wone);
    h = p8_or1(L, 5, &hone);
    if (wone && hone) {
        moy_spr(con->canvas, con->sheet, (int)n, (int)x, (int)y, -1, 1, flip);
        return 0;
    }
    {
        /* The shim's own nested loop: p8 numbers the tiles of a w x h sprite
         * across the sheet's 16-wide grid, and a flip mirrors WHICH tile goes
         * where as well as the tile itself. */
        int32_t tx, ty, wlim = p8_for_limit(w), hlim = p8_for_limit(h);
        for (ty = 0; ty <= hlim; ty++) {
            for (tx = 0; tx <= wlim; tx++) {
                double cx = fx ? (w - 1.0 - (double)tx) : (double)tx;
                double cy = fy ? (h - 1.0 - (double)ty) : (double)ty;
                int32_t sx = u2i((uint32_t)x + (uint32_t)(tx * 8));
                int32_t sy = u2i((uint32_t)y + (uint32_t)(ty * 8));
                moy_spr(con->canvas, con->sheet,
                        (int)f2i((lua_Number)((double)n + cx + cy * 16.0)),
                        (int)sx, (int)sy, -1, 1, flip);
            }
        }
    }
    return 0;
}

/* argi()'s coercion, which is what sspr's arguments meet: TRUNCATED toward
 * zero rather than floored, a non-number is 0, and nil is the default. The
 * shim floors nothing here, so neither does this. */
static int32_t p8_trunc(lua_State *L, int i, int32_t dflt)
{
    if (lua_isnoneornil(L, i)) return dflt;
    return f2i(lua_tonumber(L, i));
}

static int l_p8_sspr(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_console *con = p->con;
    int32_t sx, sy, sw, sh, dw, dh;
    int flip;
    if (!con->sheet) return 0;
    sx = p8_trunc(L, 1, 0); sy = p8_trunc(L, 2, 0);
    sw = p8_trunc(L, 3, 0); sh = p8_trunc(L, 4, 0);
    dw = lua_toboolean(L, 7) ? p8_trunc(L, 7, sw) : sw;   /* dw or sw */
    dh = lua_toboolean(L, 8) ? p8_trunc(L, 8, sh) : sh;
    flip = (lua_toboolean(L, 9) ? 1 : 0) + (lua_toboolean(L, 10) ? 2 : 0);
    moy_sspr(con->canvas, con->sheet, (int)sx, (int)sy, (int)sw, (int)sh,
             (int)p8_trunc(L, 5, 0), (int)p8_trunc(L, 6, 0),
             (int)dw, (int)dh, -1, flip);
    return 0;
}

/* -- the draw state ------------------------------------------------------ */

static int l_p8_camera(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x = p8_fl(L, 1), y = p8_fl(L, 2);
    moy_camera(p->con->canvas, (int)x, (int)y);   /* peek reads it back there */
    return 0;                                     /* p8's returns nothing */
}

static int l_p8_color(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t v = lua_toboolean(L, 1) ? p8_fl(L, 1) : 6;   /* fl(c or 6) */
    p->mem[P8_PEN] = (uint8_t)(v & 0x8f);
    return 0;
}

static int l_p8_cursor(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    p->cur_x = lua_toboolean(L, 1) ? p8_fl(L, 1) : 0;
    p->cur_y = lua_toboolean(L, 2) ? p8_fl(L, 2) : 0;
    if (!lua_isnoneornil(L, 3))
        p->mem[P8_PEN] = (uint8_t)(p8_fl(L, 3) & 0x8f);
    return 0;
}

/* p8's palt default: colour 0 transparent, the rest opaque. */
static void p8_palt_default(moy_canvas *c)
{
    moy_palt_reset(c);
    moy_palt(c, 0, 1);
}

/* The SCREEN palette lives in memory (0x5f10) rather than on the canvas,
 * because the console resets draw state every frame and PICO-8 keeps this one
 * across them -- a cart sets its fade once and draws. __moy_p8_frame puts it
 * back at the top of each _draw, so a memcpy fade into 0x5f10 now persists
 * exactly as pal(c, d, 1) does. */
static void p8_spal_set(moy_p8 *p, int c0, int c1)
{
    p->mem[P8_SPAL + (unsigned)(c0 & 15)] = col_out(c1);
    moy_pal_screen(p->con->canvas, c0, c1);
}

static int l_p8_pal(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_canvas *c = p->con->canvas;
    unsigned i;
    if (lua_isnoneornil(L, 1)) {              /* pal(): both palettes and palt */
        moy_pal_reset(c);
        for (i = 0; i < 16; i++) p->mem[P8_SPAL + i] = (uint8_t)i;
        p8_palt_default(c);
        return 0;
    }
    if (lua_type(L, 1) == LUA_TTABLE) {
        /* p8 0.2's TABLE form, a whole palette in one call: a table with a
         * [0] entry keys by colour, a plain array maps its i-th entry onto
         * colour i-1. Walked with next(), which is what pairs() hands back. */
        int shift, screen;
        lua_geti(L, 1, 0);
        shift = lua_isnil(L, -1) ? 1 : 0;
        lua_pop(L, 1);
        /* `b == 1`: a NUMBER equal to one. The string "1" is not, in Lua. */
        screen = lua_type(L, 2) == LUA_TNUMBER
                 && lua_tonumber(L, 2) == (lua_Number)1;
        lua_pushnil(L);
        while (lua_next(L, 1) != 0) {
            /* key at -2, value at -1; both must be NUMBERS, not strings that
             * look like one -- the shim asks type(), not tonumber(). */
            if (lua_type(L, -2) == LUA_TNUMBER && lua_type(L, -1) == LUA_TNUMBER) {
                int32_t k = p8_fl(L, -2) - shift;
                if (screen) p8_spal_set(p, (int)k, p8_scol(L, -1));
                else moy_pal(c, (int)k, p8_pcol(L, p, -1));
            }
            lua_pop(L, 1);
        }
        return 0;
    }
    if (lua_type(L, 3) == LUA_TNUMBER && lua_tonumber(L, 3) == (lua_Number)1) {
        p8_spal_set(p, (int)(p8_fl(L, 1) & 15), p8_scol(L, 2));
        return 0;
    }
    moy_pal(c, (int)(p8_fl(L, 1) & 15), p8_pcol(L, p, 2));
    return 0;
}

static int l_p8_palt(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_canvas *c = p->con->canvas;
    if (lua_isnoneornil(L, 1)) { p8_palt_default(c); return 0; }
    if (lua_isnoneornil(L, 2)) {          /* palt(bits): all sixteen at once */
        uint32_t bits = (uint32_t)p8_fl(L, 1);
        int i;
        moy_palt_reset(c);
        for (i = 0; i < 16; i++)
            moy_palt(c, i, (int)((bits >> (15 - i)) & 1u));
        return 0;
    }
    moy_palt(c, (int)(p8_fl(L, 1) & 15), lua_toboolean(L, 2));
    return 0;
}

/* fillp(p): the DITHER PATTERN the shape verbs carry, and its fractional
 * half-bit, which says the pattern's holes are TRANSPARENT rather than a
 * second colour. That half matters more than the dithering: carts fade
 * between scenes by filling the screen with a transparent pattern, and
 * ignoring it turns every fade into a solid black frame. */
static int l_p8_fillp(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    lua_Integer pat;
    int transparent = 0;
    /* p = p or 0, then tonumber(p) or 0 -- an integer keeps every bit, which
     * is why this converts on the stack rather than through lua_Number (a
     * SINGLE-PRECISION float here). */
    if (!lua_toboolean(L, 1)) {
        lua_pushinteger(L, 0);
    } else if (lua_type(L, 1) == LUA_TNUMBER) {
        lua_pushvalue(L, 1);
    } else {
        size_t len = 0;
        const char *str = lua_type(L, 1) == LUA_TSTRING
                          ? lua_tolstring(L, 1, &len) : NULL;
        if (!str || lua_stringtonumber(L, str) != len + 1) lua_pushinteger(L, 0);
    }
    if (lua_isinteger(L, -1)) {
        pat = lua_tointeger(L, -1);           /* an integer has no fraction */
    } else {
        lua_Number v = lua_tonumber(L, -1), frac;
        /* mfloor(p) & 0xffff: a float too big for an integer reaches `&` as a
         * float in Lua and raises there, so it raises here too. */
        if (!lua_numbertointeger((lua_Number)l_mathop(floor)(v), &pat))
            return luaL_error(L, "number has no integer representation");
        /* (p % 1) >= 0.5, Lua's FLOORED modulo -- fillp(-0.5) IS transparent. */
        frac = (lua_Number)l_mathop(fmod)(v, (lua_Number)1);
        if (frac < 0) frac = (lua_Number)(frac + (lua_Number)1);
        transparent = frac >= (lua_Number)0.5;
    }
    lua_pop(L, 1);
    p->mem[P8_FILLP] = (uint8_t)pat;
    p->mem[P8_FILLP + 1] = (uint8_t)((uint32_t)(int32_t)pat >> 8);
    p->mem[P8_FILLP + 2] = (uint8_t)transparent;
    if (p8_pat(p) == 0) moy_fillp_reset(p->con->canvas);
    return 0;
}

/* -- text ---------------------------------------------------------------- */

/* p8str(v): p8 has ONE kind of number and prints 3, never 3.0 -- a cart that
 * keys a table by `x..","..y` sees the difference. Leaves the string on top. */
static void p8_tostr(lua_State *L, int i)
{
    if (lua_type(L, i) == LUA_TNUMBER && !lua_isinteger(L, i)) {
        lua_Number f = lua_tonumber(L, i);
        lua_Integer n;
        if (f == (lua_Number)l_mathop(floor)(f)
            && f > -(lua_Number)2147483648.0 && f < (lua_Number)2147483648.0
            && lua_numbertointeger((lua_Number)l_mathop(floor)(f), &n)) {
            lua_pushinteger(L, n);
            luaL_tolstring(L, -1, NULL);
            lua_remove(L, -2);
            return;
        }
    }
    luaL_tolstring(L, i, NULL);
}

static int l_p8_print(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    size_t len = 0;
    const char *s;
    int col, cx;
    int32_t x, y;
    lua_settop(L, 4);            /* see l_p8print: p8_tostr pushes */
    p8_tostr(L, 1);
    s = lua_tolstring(L, -1, &len);
    if (lua_isnoneornil(L, 3)) {
        /* print(s) and print(s, c) take the CURSOR and advance it a line, as
         * PICO-8 does (without its scrolling); the four-argument form is
         * placed. The colour then comes from what was the x argument. */
        col = p8_pcol(L, p, 2);
        x = p->cur_x;
        y = p->cur_y;
        p->cur_y = u2i((uint32_t)p->cur_y + 6u);
    } else {
        col = p8_pcol(L, p, 4);
        x = p8_fl(L, 2);
        y = p8_fl(L, 3);
    }
    cx = p8_text(p, s, len, (int)x, (int)y, col);
    lua_pop(L, 1);
    lua_pushinteger(L, cx);
    return 1;
}

/* -- the sheet ------------------------------------------------------------
 *
 * The sheet IS memory here (0x0000-0x1fff, two pixels a byte, low nibble the
 * left one), so what sset writes is what spr draws. */
static int l_p8_sget(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x = p8_fl(L, 1), y = p8_fl(L, 2);
    uint8_t b;
    if (x < 0 || x > 127 || y < 0 || y > 127) { lua_pushinteger(L, 0); return 1; }
    b = rd(p, (uint32_t)(y * 64 + (x >> 1)));
    lua_pushinteger(L, (x & 1) ? (b >> 4) : (b & 15));
    return 1;
}

static int l_p8_sset(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int32_t x = p8_fl(L, 1), y = p8_fl(L, 2), c;
    uint32_t a;
    uint8_t b;
    if (x < 0 || x > 127 || y < 0 || y > 127) return 0;
    /* fl(c or 6) % 16 -- the shim's own default, which is 6 and not the pen. */
    c = (lua_toboolean(L, 3) ? p8_fl(L, 3) : 6) % 16;
    if (c < 0) c += 16;                        /* Lua's % is floored */
    a = (uint32_t)(y * 64 + (x >> 1));
    b = rd(p, a);
    poke_byte(p, a, (x & 1) ? (uint8_t)((b & 0x0f) | (c << 4))
                            : (uint8_t)((b & 0xf0) | c));
    return 0;
}

/* -- the map --------------------------------------------------------------
 *
 * PICO-8's defaults are the WHOLE map (128 x 64 cells), which is how a room
 * cart draws its world under a camera -- so the walk is clipped to the 128
 * pixels the camera shows first, and the default costs the cells on screen
 * rather than 8,192 a frame. The camera read is the CONSOLE's, which is the
 * machine's truth: a cart that pokes 0x5f28 moves this too, where the shim's
 * Lua copy would have gone stale. */
static int l_p8_map(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_console *con = p->con;
    int32_t celx, cely, sx, sy, cw, ch, mask, i0, i1, j0, j1;
    if (!con->sheet || !con->map) return 0;
    celx = (int32_t)p8_flr_d(L, 1);
    cely = (int32_t)p8_flr_d(L, 2);
    sx = (int32_t)p8_flr_d(L, 3);
    sy = (int32_t)p8_flr_d(L, 4);
    cw = lua_isnoneornil(L, 5) ? 128 : (int32_t)p8_flr_d(L, 5);
    ch = lua_isnoneornil(L, 6) ? 64 : (int32_t)p8_flr_d(L, 6);
    mask = lua_toboolean(L, 7) ? (int32_t)p8_flr_d(L, 7) : 0;
    i0 = p8_floordiv(con->canvas->cam_x - sx, 8);
    i1 = p8_floordiv(con->canvas->cam_x + 127 - sx, 8);
    if (i0 > 0) { celx += i0; sx += i0 * 8; cw -= i0; i1 -= i0; }
    if (i1 + 1 < cw) cw = i1 + 1;
    j0 = p8_floordiv(con->canvas->cam_y - sy, 8);
    j1 = p8_floordiv(con->canvas->cam_y + 127 - sy, 8);
    if (j0 > 0) { cely += j0; sy += j0 * 8; ch -= j0; j1 -= j0; }
    if (j1 + 1 < ch) ch = j1 + 1;
    if (cw <= 0 || ch <= 0) return 0;
    moy_map_draw_layers(con->canvas, con->map, con->sheet, (int)celx, (int)cely,
                        (int)cw, (int)ch, (int)sx, (int)sy, -1, 1,
                        (int)mask, con->flags);
    return 0;
}

/* -- the frame ------------------------------------------------------------
 *
 * The top of the port's _draw: the console resets pal, palt, camera and clip
 * after every cart frame (SPEC.md 6) and PICO-8 keeps the transparency and
 * the screen palette across them, so those go back before the cart draws --
 * one call rather than the eighteen the shim's Lua made when a fade was live.
 * The camera is NOT here: the shim re-parks it through camera(), which a host
 * with its own native map still owns.
 *
 * 0x5f10 is where the screen palette survives, and peek() still reads it off
 * the canvas: the two agree except in the window between the console's frame
 * reset and this call, which is before any cart code runs. */
static int l_p8_frame(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_canvas *c = p->con->canvas;
    int i;
    p8_palt_default(c);
    for (i = 0; i < 16; i++) {
        int v = col_in(p->mem[P8_SPAL + (unsigned)i]);
        if (v != i) moy_pal_screen(c, i, v);
    }
    return 0;
}

/* -- btn / btnp -----------------------------------------------------------
 *
 * PICO-8's rule, and the shim's, moved whole (its comment is the reasoning;
 * this is the transcription). An edge is LATCHED once per console frame,
 * because a 30fps cart ticks every other one and an engine edge lasts one; it
 * is cleared by the tick that consumes it, so one press is one edge at any
 * pair of rates. Held, btnp repeats after 15 CART ticks and then every 4 --
 * the cart's clock, not the host's, which is what makes a menu scroll at the
 * same speed on a fast board and a slow one.
 *
 * ONE DELIBERATE DIVERGENCE from fake-08, kept: its predicate fires on tick
 * 15 AND 16, two edges one tick apart, which is the doubled menu move the
 * repeat was added alongside fixing. The steady cadence (16, 20, 24, ...) is
 * the same and the first repeat is one tick later. */
#define P8_RPT_DELAY 15
#define P8_RPT_EVERY 4

static int p8_btn_down(moy_console *con, int i)
{
    return con->host.btn && con->host.btn(con->host.user, (moy_button)i, 0);
}

/* Once per CONSOLE frame, before the ticks: drop an edge a tick has already
 * consumed, then latch whatever the engine reports now. */
static int l_p8_input_frame(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_console *con = p->con;
    int i;
    if (p->consumed) {
        memset(p->pending, 0, sizeof p->pending);
        p->consumed = 0;
    }
    for (i = 0; i < 6; i++)
        if (con->host.btnp && con->host.btnp(con->host.user, (moy_button)i, 0))
            p->pending[i] = 1;
    return 0;
}

/* Once per CART tick: the hold counters advance. `catchup` is the shim's
 * `n > 1` -- a second tick inside one console frame, whose edge the first
 * already took. */
static int l_p8_input_tick(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int i;
    for (i = 0; i < 6; i++) {
        if (!p8_btn_down(p->con, i)) p->hold[i] = 0;
        else if (p->hold[i] < 0xffff) p->hold[i]++;
    }
    if (lua_toboolean(L, 1)) memset(p->pending, 0, sizeof p->pending);
    p->consumed = 1;
    return 0;
}

/* btn() with NO argument is a different verb: p8 returns a BITFIELD, bit i for
 * button i, and a cart reads it and does arithmetic on it. An index outside
 * 0-5 reads button 4, which is what `BTN[i] or "a"` did. */
static int p8_btn_index(lua_State *L, int *ok)
{
    lua_Integer n;
    *ok = 1;
    if (lua_isinteger(L, 1)) n = lua_tointeger(L, 1);
    else if (lua_type(L, 1) == LUA_TNUMBER) {
        if (!lua_numbertointeger(lua_tonumber(L, 1), &n)) return 4;
    } else {
        if (lua_isnoneornil(L, 1)) { *ok = 0; return 0; }
        return 4;                       /* not a number: BTN[i] is nil -> "a" */
    }
    if (n < 0 || n > 5) return 4;
    return (int)n;
}

static int l_p8_btn(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int ok, i = p8_btn_index(L, &ok);
    if (ok) { lua_pushboolean(L, p8_btn_down(p->con, i)); return 1; }
    {
        int m = 0, k;
        for (k = 0; k < 6; k++) if (p8_btn_down(p->con, k)) m |= 1 << k;
        lua_pushinteger(L, m);
    }
    return 1;
}

static int p8_btnp_of(const moy_p8 *p, int i)
{
    unsigned h = p->hold[i];
    if (p->pending[i]) return 1;
    return h > P8_RPT_DELAY && (h - P8_RPT_DELAY - 1) % P8_RPT_EVERY == 0;
}

static int l_p8_btnp(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    int ok, i = p8_btn_index(L, &ok);
    if (ok) { lua_pushboolean(L, p8_btnp_of(p, i)); return 1; }
    {
        int m = 0, k;
        for (k = 0; k < 6; k++) if (p8_btnp_of(p, k)) m |= 1 << k;
        lua_pushinteger(L, m);
    }
    return 1;
}

/* -- the p8 number verbs --------------------------------------------------
 *
 * The shim's own Lua again, and the reason it is worth C is not the
 * arithmetic: fl() costs a Lua call, an _ENV lookup for type() and a second
 * call into math.floor, and it sits on the argument of every draw verb. flr()
 * is the same shape and was 13% of dank tomb's Lua time, because the porter
 * emits it around every operand of a native bit operator.
 *
 * lua_Number is a SINGLE-PRECISION float on this VM (LUA_32BITS, which
 * SPEC.md 4.2 requires) and lua_Integer a 32-bit int, and the two are not
 * interchangeable: math.floor of a float hands back an INTEGER when one fits
 * and the float itself when it does not, and the p8 shim reads that type back
 * (p8str prints 3 rather than 3.0; the bit verbs branch on math.type). So
 * every one of these keeps the result type the Lua would have produced, and
 * every float expression is cast back to lua_Number so a wider intermediate
 * cannot change where it lands.
 *
 * sqrt and ceil are NOT here: the shim aliases them straight to math.sqrt and
 * math.ceil, so there is no wrapper to remove. Neither is rnd -- it draws
 * from math.random's own state, and reimplementing that would move the
 * sequence a cart's world is built from.
 */

#define P8_TAU ((lua_Number)6.283185307179586)

/* Lua's own pushnumint: the floor of a float is an integer when one fits. */
static void push_numint(lua_State *L, lua_Number d)
{
    lua_Integer n;
    if (lua_numbertointeger(d, &n)) lua_pushinteger(L, n);
    else lua_pushnumber(L, d);
}

/* `v or 0`, pushed. lua_toboolean is false for none, nil and false, which is
 * exactly the set Lua's `or` replaces. */
static void push_or0(lua_State *L, int i)
{
    if (lua_toboolean(L, i)) lua_pushvalue(L, i);
    else lua_pushinteger(L, 0);
}

/* `v or 0` as a number, raising on the things the shim's Lua raised on. */
static lua_Number num_or0(lua_State *L, int i)
{
    if (!lua_toboolean(L, i)) return 0;
    return luaL_checknumber(L, i);
}

/* fl(v): p8 coerces every API number argument -- nil is 0, a numeric string
 * is its number, anything else is 0 rather than an error -- and floors. */
static int l_fl(lua_State *L)
{
    int isnum;
    lua_Number f;
    if (lua_isinteger(L, 1)) { lua_settop(L, 1); return 1; }
    f = lua_tonumberx(L, 1, &isnum);
    if (!isnum) { lua_pushinteger(L, 0); return 1; }
    push_numint(L, (lua_Number)l_mathop(floor)(f));
    return 1;
}

/* flr(v) is NOT fl(v): it is math.floor(v or 0), so a string that is not a
 * number raises here where fl() reads 0. */
static int l_flr(lua_State *L)
{
    if (lua_isinteger(L, 1)) { lua_settop(L, 1); return 1; }
    if (!lua_toboolean(L, 1)) { lua_pushinteger(L, 0); return 1; }
    push_numint(L, (lua_Number)l_mathop(floor)(luaL_checknumber(L, 1)));
    return 1;
}

static int l_abs(lua_State *L)
{
    if (lua_isinteger(L, 1)) {
        lua_Integer n = lua_tointeger(L, 1);
        if (n < 0) n = (lua_Integer)((lua_Unsigned)0 - (lua_Unsigned)n);
        lua_pushinteger(L, n);
        return 1;
    }
    if (!lua_toboolean(L, 1)) { lua_pushinteger(L, 0); return 1; }
    lua_pushnumber(L, (lua_Number)l_mathop(fabs)(luaL_checknumber(L, 1)));
    return 1;
}

/* math.min/math.max return the ARGUMENT, not a converted copy, so min(1, 1.0)
 * is the integer 1 and the type survives. Compared with `<` for the same
 * reason all() compares with `==`: a cart's __lt is its own. */
static int l_min(lua_State *L)
{
    lua_settop(L, 2);            /* a push moves what index 2 names */
    push_or0(L, 1);
    push_or0(L, 2);
    lua_pushvalue(L, lua_compare(L, -1, -2, LUA_OPLT) ? -1 : -2);
    return 1;
}

static int l_max(lua_State *L)
{
    lua_settop(L, 2);
    push_or0(L, 1);
    push_or0(L, 2);
    lua_pushvalue(L, lua_compare(L, -2, -1, LUA_OPLT) ? -1 : -2);
    return 1;
}

/* mid(a, b, c) = max(min(a, b), min(max(a, b), c)), the shim's spelling, so
 * every `or 0` lands where it did. */
static int l_mid(lua_State *L)
{
    int lo, hi, inner;
    lua_settop(L, 3);
    push_or0(L, 1);                              /* 4 */
    push_or0(L, 2);                              /* 5 */
    push_or0(L, 3);                              /* 6 */
    lo = lua_compare(L, 5, 4, LUA_OPLT) ? 5 : 4;
    hi = lua_compare(L, 4, 5, LUA_OPLT) ? 5 : 4;
    inner = lua_compare(L, 6, hi, LUA_OPLT) ? 6 : hi;
    lua_pushvalue(L, lua_compare(L, lo, inner, LUA_OPLT) ? inner : lo);
    return 1;
}

static int l_sgn(lua_State *L)
{
    push_or0(L, 1);
    lua_pushinteger(L, 0);
    lua_pushinteger(L, lua_compare(L, -2, -1, LUA_OPLT) ? -1 : 1);
    return 1;
}

/* p8 angles are TURNS (0..1) and its sin is flipped (+y is down). */
static int l_sin(lua_State *L)
{
    lua_Number t = num_or0(L, 1);
    lua_pushnumber(L, (lua_Number)(-(lua_Number)l_mathop(sin)(
                       (lua_Number)(t * P8_TAU))));
    return 1;
}

static int l_cos(lua_State *L)
{
    lua_Number t = num_or0(L, 1);
    lua_pushnumber(L, (lua_Number)l_mathop(cos)((lua_Number)(t * P8_TAU)));
    return 1;
}

static int l_atan2(lua_State *L)
{
    lua_Number x, y, m;
    lua_settop(L, 2);
    /* -(dy or 0) negates as an INTEGER when it is one, and that is not simply
     * -num_or0 twice over: negating the 32-bit minimum wraps to itself where
     * negating the float it becomes does not, and a missing dy is -(integer
     * 0), which is +0 rather than the -0.0 a float negation gives. */
    if (!lua_toboolean(L, 2)) {
        y = 0;
    } else if (lua_isinteger(L, 2)) {
        lua_Integer v = lua_tointeger(L, 2);
        y = (lua_Number)(lua_Integer)((lua_Unsigned)0 - (lua_Unsigned)v);
    } else {
        y = (lua_Number)(-luaL_checknumber(L, 2));
    }
    x = num_or0(L, 1);
    m = (lua_Number)l_mathop(fmod)(
            (lua_Number)((lua_Number)l_mathop(atan2)(y, x) / P8_TAU),
            (lua_Number)1);
    if (m < 0) m = (lua_Number)(m + (lua_Number)1);   /* Lua's % is floored */
    lua_pushnumber(L, m);
    return 1;
}

/* tonum(v): a number passes, anything else goes through tonumber, which keeps
 * an integer-looking string an INTEGER. */
static int l_tonum(lua_State *L)
{
    size_t len;
    const char *s;
    lua_settop(L, 1);            /* the shim declared the parameter, so a
                                    missing argument reaches tonumber as nil */
    if (lua_type(L, 1) == LUA_TNUMBER) return 1;
    s = lua_tolstring(L, 1, &len);
    if (s != NULL && lua_stringtonumber(L, s) == len + 1) return 1;
    luaL_checkany(L, 1);
    lua_pushnil(L);
    return 1;
}

/* -- the p8 bit verbs -----------------------------------------------------
 *
 * PICO-8's numbers are 16.16 fixed point and its bit verbs work on all 32
 * bits, fraction included: band(x, 0xffff.fffe) drops the lowest fractional
 * bit, and band(x, -1) is how a cart spells floor. Two integers take the
 * plain path; anything with a fraction goes onto the 32-bit fixed image and
 * comes back divided.
 *
 * fx/unfx here are the shim's, transcribed. Both are EXACT in float
 * arithmetic -- multiplying and dividing by a power of two neither rounds nor
 * loses a bit -- so nothing in this file is where a build's float width
 * shows. What the width decides is the VALUE that arrives: on a 24-bit
 * mantissa a cart's computed fraction has already lost its low bits, and
 * band(x, -1) turns a sub-ulp difference into an integer one. See PICO8.md.
 *
 * The integer half is 32-bit by contract, not by accident: SPEC.md 4.2 pins
 * LUA_32BITS, so `v << 16` wraps, `0xffffffff` is -1 and the masks the shim
 * writes are the no-ops they read as. A 64-bit Lua would answer differently
 * in BOTH lanes -- that is a property of the shim's Lua, reproduced, not one
 * introduced here.
 */

/* Lua's own shift: a count at or past the width is 0, a negative count goes
 * the other way, and the right shift is LOGICAL. */
static int32_t p8_shiftl(int32_t x, lua_Integer y)
{
    if (y < 0) {
        if (y <= -32) return 0;
        return u2i((uint32_t)x >> (unsigned)(-y));
    }
    if (y >= 32) return 0;
    return u2i((uint32_t)x << (unsigned)y);
}

static int32_t p8_shiftr(int32_t x, lua_Integer y)
{
    return p8_shiftl(x, (lua_Integer)((lua_Unsigned)0 - (lua_Unsigned)y));
}

/* `v or 0` is an integer? nil and false become the integer 0, so yes. */
static int or0_isint(lua_State *L, int i)
{
    return !lua_toboolean(L, i) || lua_isinteger(L, i);
}

static lua_Integer or0_int(lua_State *L, int i)
{
    return lua_toboolean(L, i) ? lua_tointeger(L, i) : 0;
}

/* The number `v or 0` then reaches arithmetic as: a numeric string converts,
 * because Lua's own `*` would have converted it, and anything else raises. */
static void p8_bit_arg(lua_State *L, int i)
{
    if (!lua_toboolean(L, i)) { lua_pushinteger(L, 0); return; }
    if (lua_type(L, i) == LUA_TNUMBER) { lua_pushvalue(L, i); return; }
    if (lua_type(L, i) == LUA_TSTRING) {
        size_t len;
        const char *s = lua_tolstring(L, i, &len);
        if (lua_stringtonumber(L, s) == len + 1) return;
    }
    luaL_error(L, "attempt to perform arithmetic on a %s value",
               luaL_typename(L, i));
}

/* fx(v), on the number p8_bit_arg left on top, which it pops. */
static int32_t p8_fx(lua_State *L)
{
    lua_Number r;
    int32_t out;
    if (lua_isinteger(L, -1)) {
        out = u2i((uint32_t)(int32_t)lua_tointeger(L, -1) << 16);
        lua_pop(L, 1);
        return out;
    }
    r = (lua_Number)(lua_tonumber(L, -1) * (lua_Number)65536);
    lua_pop(L, 1);
    if (r >= (lua_Number)2147483648.0 || r < -(lua_Number)2147483648.0) {
        /* past 16.16's range, which p8 cannot reach; wrap like it would */
        r = (lua_Number)(r - (lua_Number)4294967296.0 * (lua_Number)l_mathop(floor)(
                (lua_Number)(r / (lua_Number)4294967296.0)));
        if (r >= (lua_Number)2147483648.0)
            r = (lua_Number)(r - (lua_Number)4294967296.0);
    }
    r = (lua_Number)l_mathop(floor)(r);
    if (!(r >= -(lua_Number)2147483648.0 && r < (lua_Number)2147483648.0)) {
        /* an infinity or a NaN: math.floor hands the float back and the
         * bitwise operator it feeds refuses it, exactly here */
        luaL_error(L, "number has no integer representation");
        return 0;
    }
    return (int32_t)r;
}

/* Back from the image: an INTEGER when the fraction is clear (p8 has one kind
 * of number and prints 12, not 12.0 -- a cart that keys a table by the result
 * must see the same), a float otherwise. */
static void p8_unfx(lua_State *L, int32_t r)
{
    if ((r & 0xffff) == 0) lua_pushinteger(L, (lua_Integer)(r / 65536));
    else lua_pushnumber(L, (lua_Number)((lua_Number)r / (lua_Number)65536));
}

/* flr(n or 0) as a shift count: math.floor hands back a float it cannot make
 * an integer of, and the shift then refuses it. */
static lua_Integer p8_shift_count(lua_State *L, int i)
{
    lua_Integer n;
    lua_Number f;
    if (lua_isinteger(L, i)) return lua_tointeger(L, i);
    if (!lua_toboolean(L, i)) return 0;
    f = (lua_Number)l_mathop(floor)(luaL_checknumber(L, i));
    if (!lua_numbertointeger(f, &n)) {
        luaL_error(L, "number has no integer representation");
        return 0;
    }
    return n;
}

/* flr(n or 0) % 32, the rotate count. A float too big for an integer is
 * already a multiple of 32 by then (float32's step is 256 or wider past
 * 2^31), so it lands on 0; an infinity or a NaN raises. */
static lua_Integer p8_rot_count(lua_State *L, int i)
{
    lua_Integer n;
    lua_Number f;
    if (lua_isinteger(L, i)) {
        n = lua_tointeger(L, i);
    } else if (!lua_toboolean(L, i)) {
        n = 0;
    } else {
        f = (lua_Number)l_mathop(floor)(luaL_checknumber(L, i));
        if (!lua_numbertointeger(f, &n)) {
            f = (lua_Number)l_mathop(fmod)(f, (lua_Number)32);
            if (f < 0) f = (lua_Number)(f + (lua_Number)32);
            if (!lua_numbertointeger(f, &n)) {
                luaL_error(L, "number has no integer representation");
                return 0;
            }
            return n;
        }
    }
    n = n % 32;
    if (n < 0) n += 32;                       /* Lua's % is floored */
    return n;
}

/* x // n, Lua's integer floor division, division by zero and all. */
static lua_Integer p8_idiv(lua_State *L, lua_Integer m, lua_Integer n)
{
    lua_Integer q;
    if (n == 0) { luaL_error(L, "attempt to perform 'n//0'"); return 0; }
    if (n == -1) return (lua_Integer)((lua_Unsigned)0 - (lua_Unsigned)m);
    q = m / n;
    if ((m ^ n) < 0 && m % n != 0) q -= 1;
    return q;
}

#define P8_BITOP(NAME, INTEXPR, FIXEXPR)                                   \
    static int NAME(lua_State *L)                                          \
    {                                                                      \
        uint32_t a, b;                                                     \
        lua_settop(L, 2);                                                  \
        if (or0_isint(L, 1) && or0_isint(L, 2)) {                          \
            a = (uint32_t)(int32_t)or0_int(L, 1);                          \
            b = (uint32_t)(int32_t)or0_int(L, 2);                          \
            lua_pushinteger(L, u2i(INTEXPR));                              \
            return 1;                                                      \
        }                                                                  \
        p8_bit_arg(L, 1); a = (uint32_t)p8_fx(L);                          \
        p8_bit_arg(L, 2); b = (uint32_t)p8_fx(L);                          \
        p8_unfx(L, u2i(FIXEXPR));                                          \
        return 1;                                                          \
    }

P8_BITOP(l_band, a & b, a & b)
P8_BITOP(l_bor,  a | b, a | b)
P8_BITOP(l_bxor, a ^ b, a ^ b)

static int l_bnot(lua_State *L)
{
    lua_settop(L, 1);
    if (or0_isint(L, 1)) {
        lua_pushinteger(L, u2i(~(uint32_t)(int32_t)or0_int(L, 1)));
        return 1;
    }
    p8_bit_arg(L, 1);
    p8_unfx(L, u2i(~(uint32_t)p8_fx(L)));
    return 1;
}

static int l_shl(lua_State *L)
{
    lua_Integer n;
    lua_settop(L, 2);
    n = p8_shift_count(L, 2);
    if (or0_isint(L, 1)) {
        lua_pushinteger(L, p8_shiftl((int32_t)or0_int(L, 1), n));
        return 1;
    }
    p8_bit_arg(L, 1);
    p8_unfx(L, p8_shiftl(p8_fx(L), n));
    return 1;
}

/* ARITHMETIC, as PICO-8's is: a floor division by 1 << n, which is also where
 * a count of 32 or more turns into a division by zero. */
static int l_shr(lua_State *L)
{
    lua_Integer n, d;
    lua_settop(L, 2);
    n = p8_shift_count(L, 2);
    d = p8_shiftl(1, n);
    if (or0_isint(L, 1)) {
        lua_pushinteger(L, p8_idiv(L, or0_int(L, 1), d));
        return 1;
    }
    p8_bit_arg(L, 1);
    p8_unfx(L, (int32_t)p8_idiv(L, p8_fx(L), d));
    return 1;
}

static int l_lshr(lua_State *L)
{
    lua_Integer n;
    lua_settop(L, 2);
    n = p8_shift_count(L, 2);
    if (or0_isint(L, 1)) {
        lua_pushinteger(L, p8_shiftr((int32_t)or0_int(L, 1), n));
        return 1;
    }
    p8_bit_arg(L, 1);
    p8_unfx(L, p8_shiftr(p8_fx(L), n));
    return 1;
}

static int l_rotl(lua_State *L)
{
    lua_Integer n;
    int32_t v;
    lua_settop(L, 2);
    n = p8_rot_count(L, 2);
    p8_bit_arg(L, 1);
    v = p8_fx(L);
    p8_unfx(L, u2i((uint32_t)p8_shiftl(v, n) | (uint32_t)p8_shiftr(v, 32 - n)));
    return 1;
}

static int l_rotr(lua_State *L)
{
    lua_Integer n;
    int32_t v;
    lua_settop(L, 2);
    n = p8_rot_count(L, 2);
    p8_bit_arg(L, 1);
    v = p8_fx(L);
    p8_unfx(L, u2i((uint32_t)p8_shiftr(v, n) | (uint32_t)p8_shiftl(v, 32 - n)));
    return 1;
}

/* -- the NATIVE bit operators ---------------------------------------------
 *
 * A different thing from the nine verbs above, sharing only their spelling.
 * Those are p8's band()/shl()/rotl() FUNCTIONS, which work on all 32 bits of
 * the 16.16 image, fraction included. These are p8's `a|b`, `a<<b`, `~a` as
 * the PORTER reads them: flr() on each operand and then Lua's own integer
 * operator, which is what the emitted cart ran when it spelled the expansion
 * out for itself (`flr(a) | flr(b)`). One crossing here instead of two, and
 * dank tomb's lighting made four thousand of them a frame.
 *
 * The shim's Lua is the reference and libmoy/test/p8lib.moy holds the two
 * lanes to one answer over the operands and counts a cart reaches them with.
 */

/* flr(v or 0), and then the coercion the bitwise operator applies to it, in
 * one: nil and false are 0, a numeric string converts as math.floor's
 * argument does, and a float with no integer representation -- an infinity, a
 * NaN, anything past 2^31 -- raises exactly where `flr(x) | y` raised. Never
 * undefined: math.floor hands such a float straight back and the operator
 * refuses it, which is this branch. */
static lua_Integer p8_op_int(lua_State *L, int i)
{
    lua_Integer n;
    lua_Number f;
    if (lua_isinteger(L, i)) return lua_tointeger(L, i);
    if (!lua_toboolean(L, i)) return 0;
    f = (lua_Number)l_mathop(floor)(luaL_checknumber(L, i));
    if (!lua_numbertointeger(f, &n)) {
        luaL_error(L, "number has no integer representation");
        return 0;
    }
    return n;
}

/* The integer half is 32-bit by contract (SPEC.md 4.2 pins LUA_32BITS), the
 * same contract the 16.16 verbs above are written to. */
#define P8_NATIVE_BITOP(NAME, EXPR)                                        \
    static int NAME(lua_State *L)                                          \
    {                                                                      \
        uint32_t a, b;                                                     \
        lua_settop(L, 2);                                                  \
        a = (uint32_t)(int32_t)p8_op_int(L, 1);                            \
        b = (uint32_t)(int32_t)p8_op_int(L, 2);                            \
        lua_pushinteger(L, u2i(EXPR));                                     \
        return 1;                                                          \
    }

P8_NATIVE_BITOP(l_p8_bor,  a | b)
P8_NATIVE_BITOP(l_p8_band, a & b)
P8_NATIVE_BITOP(l_p8_bxor, a ^ b)

static int l_p8_bnot(lua_State *L)
{
    lua_settop(L, 1);
    lua_pushinteger(L, u2i(~(uint32_t)(int32_t)p8_op_int(L, 1)));
    return 1;
}

/* Lua's own shifts, which p8_shiftl already is: a count at or past the width
 * gives 0 and a negative one goes the other way, so `flr(a) << flr(b)` is
 * defined for every count a cart can reach. */
#define P8_NATIVE_SHIFT(NAME, SHIFT)                                       \
    static int NAME(lua_State *L)                                          \
    {                                                                      \
        int32_t v;                                                         \
        lua_Integer n;                                                     \
        lua_settop(L, 2);                                                  \
        v = (int32_t)p8_op_int(L, 1);                                      \
        n = p8_op_int(L, 2);                                               \
        lua_pushinteger(L, SHIFT(v, n));                                   \
        return 1;                                                          \
    }

P8_NATIVE_SHIFT(l_p8_shl, p8_shiftl)
P8_NATIVE_SHIFT(l_p8_shr, p8_shiftr)
/* p8's `>>>`: its own name, and the same answer -- Lua's `>>` is already the
 * logical shift, which is what the porter has always emitted for it. */
P8_NATIVE_SHIFT(l_p8_lshr, p8_shiftr)

/* `<<>` and `>><`, the two p8 operators with no Lua spelling at all: a 32-bit
 * rotate of the floored value, by `flr(n) % 32` -- p8_rot_count, which is
 * that expression including what Lua's floored `%` does to a float too big
 * for an integer. */
#define P8_NATIVE_ROT(NAME, FIRST, SECOND)                                 \
    static int NAME(lua_State *L)                                          \
    {                                                                      \
        int32_t v;                                                         \
        lua_Integer n;                                                     \
        lua_settop(L, 2);                                                  \
        v = (int32_t)p8_op_int(L, 1);                                      \
        n = p8_rot_count(L, 2);                                            \
        lua_pushinteger(L, u2i((uint32_t)FIRST(v, n)                       \
                               | (uint32_t)SECOND(v, 32 - n)));            \
        return 1;                                                          \
    }

P8_NATIVE_ROT(l_p8_rotl, p8_shiftl, p8_shiftr)
P8_NATIVE_ROT(l_p8_rotr, p8_shiftr, p8_shiftl)

/* -- the p8 table verbs ---------------------------------------------------
 *
 * No machine behind these -- they are the shim's own Lua, promoted because
 * all()'s iterator was half of one cart's Lua time: a Lua closure per element
 * is a VM re-entry per element.
 *
 * The DELETE TOLERANCE is the semantics that has to survive. A p8 cart
 * destroys the object it is iterating (celeste's foreach over its object
 * list), so the cursor advances only when the element it last handed out is
 * still where it left it; when the table shifted under it, the slot already
 * holds the next one. Kept as `t[i]` and `==` (lua_geti / lua_compare) rather
 * than raw access, so a table with __index or __eq answers as it did in Lua.
 */

static int l_nil_iter(lua_State *L)
{
    lua_pushnil(L);
    return 1;
}

/* upvalues: 1 the table, 2 the cursor, 3 the element last handed out */
static int l_all_iter(lua_State *L)
{
    lua_Integer i = lua_tointeger(L, lua_upvalueindex(2));
    lua_settop(L, 0);
    lua_geti(L, lua_upvalueindex(1), i);
    if (lua_compare(L, 1, lua_upvalueindex(3), LUA_OPEQ)) {
        lua_settop(L, 0);
        lua_geti(L, lua_upvalueindex(1), ++i);
        lua_pushinteger(L, i);
        lua_replace(L, lua_upvalueindex(2));
    }
    lua_pushvalue(L, 1);
    lua_replace(L, lua_upvalueindex(3));
    return 1;
}

static int l_all(lua_State *L)
{
    if (lua_isnoneornil(L, 1)) {          /* p8's all(nil) is an empty loop */
        lua_pushcfunction(L, l_nil_iter);
        return 1;
    }
    lua_settop(L, 1);
    lua_pushinteger(L, 0);
    lua_pushnil(L);
    lua_pushcclosure(L, l_all_iter, 3);
    return 1;
}

static int l_foreach(lua_State *L)
{
    lua_Integer i = 0;
    if (lua_isnoneornil(L, 1)) return 0;
    lua_settop(L, 2);
    lua_pushnil(L);                       /* 3: the element last handed out */
    for (;;) {
        lua_geti(L, 1, i);
        if (lua_compare(L, -1, 3, LUA_OPEQ)) {
            lua_pop(L, 1);
            lua_geti(L, 1, ++i);
        }
        if (lua_isnil(L, -1)) break;
        lua_replace(L, 3);
        lua_pushvalue(L, 2);
        lua_pushvalue(L, 3);
        lua_call(L, 1, 0);
    }
    return 0;
}

static int l_add(lua_State *L)
{
    lua_settop(L, 2);
    lua_pushvalue(L, 2);
    lua_seti(L, 1, luaL_len(L, 1) + 1);
    return 1;                             /* p8's add returns what it added */
}

/* table.remove(t, pos), transcribed: the shift, then the hole. */
static void tbl_remove(lua_State *L, int t, lua_Integer pos)
{
    lua_Integer size = luaL_len(L, t);
    if (pos != size)
        luaL_argcheck(L, (lua_Unsigned)pos - 1u <= (lua_Unsigned)size, 2,
                      "position out of bounds");
    for (; pos < size; pos++) {
        lua_geti(L, t, pos + 1);
        lua_seti(L, t, pos);
    }
    lua_pushnil(L);
    lua_seti(L, t, pos);
}

static int l_del(lua_State *L)
{
    lua_Integer n = luaL_len(L, 1), i;
    lua_settop(L, 2);
    for (i = 1; i <= n; i++) {
        lua_geti(L, 1, i);
        if (lua_compare(L, -1, 2, LUA_OPEQ)) {
            lua_pop(L, 1);
            tbl_remove(L, 1, i);
            return 0;
        }
        lua_pop(L, 1);
    }
    return 0;
}

static int l_deli(lua_State *L)
{
    lua_Integer i;
    if (lua_isnoneornil(L, 1)) { lua_pushnil(L); return 1; }
    i = luaL_optinteger(L, 2, luaL_len(L, 1));
    lua_settop(L, 1);
    lua_geti(L, 1, i);
    tbl_remove(L, 1, i);
    return 1;
}

static int l_count(lua_State *L)
{
    lua_Integer n = luaL_len(L, 1), i, c = 0;
    if (lua_isnoneornil(L, 2)) { lua_pushinteger(L, n); return 1; }
    lua_settop(L, 2);
    for (i = 1; i <= n; i++) {
        lua_geti(L, 1, i);
        if (lua_compare(L, -1, 2, LUA_OPEQ)) c++;
        lua_pop(L, 1);
    }
    lua_pushinteger(L, c);
    return 1;
}

/* -- installation --------------------------------------------------------- */

/* The ROM half of the map: sheet, map and flag bytes as PICO-8 lays them out,
 * so a cart that reads its art or its tiles as memory reads the real thing. */
static void seed(moy_p8 *p)
{
    uint32_t a;
    memset(p->mem, 0, MOY_P8_MEM);
    if (p->con->sheet) {
        const uint8_t *px = p->con->sheet->pix;
        for (a = 0; a < 0x2000; a++)
            p->mem[a] = (uint8_t)((px[a * 2] & 15) | ((px[a * 2 + 1] & 15) << 4));
    }
    if (p->con->map && p->con->map->w == 128) {
        int rows = p->con->map->h < 32 ? p->con->map->h : 32;
        for (a = 0; a < (uint32_t)rows * 128; a++) {
            uint8_t cell = p->con->map->cells[a];
            p->mem[0x2000 + a] = cell ? (uint8_t)(cell - 1) : 0;
        }
    }
    if (p->con->flags) memcpy(p->mem + 0x3000, p->con->flags, 256);
    for (a = 0; a < 16; a++) {
        p->mem[0x5f00 + a] = (uint8_t)a;
        p->mem[0x5f10 + a] = (uint8_t)a;
    }
    p->mem[0x5f22] = 128; p->mem[0x5f23] = 128;  /* clip: the whole screen */
    p->mem[P8_PEN] = 6;                          /* p8's default pen colour */
    p->cur_x = p->cur_y = 0;
    memset(p->hold, 0, sizeof p->hold);
    memset(p->pending, 0, sizeof p->pending);
    p->consumed = 0;
}

int moy_p8_open(struct lua_State *Ls, moy_console *con, moy_p8 *p,
                uint8_t *mem, uint8_t *rom)
{
    lua_State *L = (lua_State *)Ls;
    static const struct { const char *name; lua_CFunction fn; } T[] = {
        {"__moy_poke", l_poke}, {"__moy_peek", l_peek},
        {"__moy_memcpy", l_memcpy}, {"__moy_memset", l_memset},
        {"__moy_peek2", l_peek2}, {"__moy_poke2", l_poke2},
        {"__moy_peek4", l_peek4}, {"__moy_poke4", l_poke4},
        {"__moy_reload", l_reload}, {"__moy_cstore", l_cstore},
        {"__moy_p8print", l_p8print},
        {"__moy_lut_span", l_lut_span},
        {"__moy_mget", l_mget}, {"__moy_mset", l_mset},
        {"__moy_fget", l_p8fget}, {"__moy_fset", l_p8fset},
        /* The DRAW verbs: p8's semantics resolved in C from the machine's own
         * state, so a shim draw call is one crossing rather than four. */
        {"__moy_p8_pset", l_p8_pset}, {"__moy_p8_pget", l_p8_pget},
        {"__moy_p8_line", l_p8_line},
        {"__moy_p8_rect", l_p8_rect}, {"__moy_p8_rectfill", l_p8_rectfill},
        {"__moy_p8_circ", l_p8_circ}, {"__moy_p8_circfill", l_p8_circfill},
        {"__moy_p8_oval", l_p8_oval}, {"__moy_p8_ovalfill", l_p8_ovalfill},
        {"__moy_p8_spr", l_p8_spr}, {"__moy_p8_sspr", l_p8_sspr},
        {"__moy_p8_map", l_p8_map},
        {"__moy_p8_camera", l_p8_camera}, {"__moy_p8_color", l_p8_color},
        {"__moy_p8_cursor", l_p8_cursor}, {"__moy_p8_print", l_p8_print},
        {"__moy_p8_pal", l_p8_pal}, {"__moy_p8_palt", l_p8_palt},
        {"__moy_p8_fillp", l_p8_fillp},
        {"__moy_p8_sget", l_p8_sget}, {"__moy_p8_sset", l_p8_sset},
        {"__moy_p8_frame", l_p8_frame},
        {"__moy_p8_btn", l_p8_btn}, {"__moy_p8_btnp", l_p8_btnp},
        {"__moy_p8_input_frame", l_p8_input_frame},
        {"__moy_p8_input_tick", l_p8_input_tick},
    };
    /* The stdlib half: no machine behind it, so no upvalue to carry. */
    static const struct { const char *name; lua_CFunction fn; } S[] = {
        {"__moy_all", l_all}, {"__moy_foreach", l_foreach},
        {"__moy_add", l_add}, {"__moy_del", l_del},
        {"__moy_deli", l_deli}, {"__moy_count", l_count},
        {"__moy_fl", l_fl}, {"__moy_flr", l_flr}, {"__moy_abs", l_abs},
        {"__moy_min", l_min}, {"__moy_max", l_max}, {"__moy_mid", l_mid},
        {"__moy_sgn", l_sgn}, {"__moy_sin", l_sin}, {"__moy_cos", l_cos},
        {"__moy_atan2", l_atan2}, {"__moy_tonum", l_tonum},
        {"__moy_band", l_band}, {"__moy_bor", l_bor}, {"__moy_bxor", l_bxor},
        {"__moy_bnot", l_bnot}, {"__moy_shl", l_shl}, {"__moy_shr", l_shr},
        {"__moy_lshr", l_lshr}, {"__moy_rotl", l_rotl}, {"__moy_rotr", l_rotr},
        /* The porter's NATIVE bit operators, which are not those: flr() on
         * each operand and then Lua's own integer operator, in one call. */
        {"__moy_p8_bor", l_p8_bor}, {"__moy_p8_band", l_p8_band},
        {"__moy_p8_bxor", l_p8_bxor}, {"__moy_p8_bnot", l_p8_bnot},
        {"__moy_p8_shl", l_p8_shl}, {"__moy_p8_shr", l_p8_shr},
        {"__moy_p8_lshr", l_p8_lshr},
        {"__moy_p8_rotl", l_p8_rotl}, {"__moy_p8_rotr", l_p8_rotr},
    };
    size_t i;
    if (!con || !p || !mem) return 1;
    p->con = con;
    p->mem = mem;
    p->rom = rom;
    seed(p);
    if (rom) memcpy(rom, mem, MOY_P8_ROM);
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        lua_pushlightuserdata(L, p);
        lua_pushcclosure(L, T[i].fn, 1);
        lua_setglobal(L, T[i].name);
    }
    for (i = 0; i < sizeof S / sizeof S[0]; i++) {
        lua_pushcfunction(L, S[i].fn);
        lua_setglobal(L, S[i].name);
    }
    return 0;
}
