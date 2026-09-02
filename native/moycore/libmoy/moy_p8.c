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
 *   0x5f20-0x5f2b  clip, then camera, int16 LE
 *   0x6000-0x7fff  screen, 128x128 4bpp
 *
 * Everything else is plain RAM. The palette, camera and clip bytes READ from
 * the canvas rather than the array, so whichever path wrote them -- pal() or
 * poke() -- peek() answers the truth. Draw state still resets every frame
 * (SPEC.md 6); the shim re-applies what PICO-8 keeps.
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

static inline int32_t iarg(lua_State *L, int i)
{
    int isnum;
    lua_Integer v = lua_tointegerx(L, i, &isnum);
    if (isnum) return (int32_t)v;
    return (int32_t)lua_tonumber(L, i);
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

/* A PICO-8 colour byte -> a console index: bit 7 is the secret sixteen,
 * which a ported cart's manifest palette carries at 16-31. And back. */
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
        moy_pal(c, (int)(a - 0x5f00), col_in(v));
        moy_palt(c, (int)(a - 0x5f00), (v & 0x10) != 0);
    } else if (a >= 0x5f10 && a < 0x5f20) {
        moy_pal_screen(c, (int)(a - 0x5f10), col_in(v));
    } else if (a >= 0x5f20 && a < 0x5f2c) {
        const uint8_t *m = p->mem + 0x5f20;
        c->clip_x0 = m[0]; c->clip_y0 = m[1];
        c->clip_x1 = m[2]; c->clip_y1 = m[3];
        c->cam_x = (int16_t)(m[8] | (m[9] << 8));
        c->cam_y = (int16_t)(m[10] | (m[11] << 8));
    }
}

static inline uint8_t rd(const moy_p8 *p, uint32_t a)
{
    const moy_canvas *c = p->con->canvas;
    if (a >= 0x6000 && a < 0x8000) return scr_read(p, a);
    if (a >= 0x5f00 && a < 0x5f10) {
        int i = (int)(a - 0x5f00);
        return (uint8_t)(col_out(c->pal[i]) | (c->palt[i] ? 0x10 : 0));
    }
    if (a >= 0x5f10 && a < 0x5f20) return col_out(c->spal[a - 0x5f10]);
    if (a >= 0x5f20 && a < 0x5f2c) {
        int i = (int)(a - 0x5f20);
        int v[12] = { c->clip_x0, c->clip_y0, c->clip_x1, c->clip_y1, 0, 0, 0, 0,
                      c->cam_x & 255, (c->cam_x >> 8) & 255,
                      c->cam_y & 255, (c->cam_y >> 8) & 255 };
        return (uint8_t)v[i];
    }
    if (a >= 0x3000 && a < 0x3100 && p->con->flags) return p->con->flags[a - 0x3000];
    return p->mem[a];
}

/* -- the verbs ------------------------------------------------------------ */

static int l_poke(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t a = (uint32_t)iarg(L, 1);
    uint8_t v = (uint8_t)iarg(L, 2);
    if (a >= MOY_P8_MEM) return 0;
    p->mem[a] = v;
    apply(p, a, v);
    return 0;
}

static int l_peek(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t a = (uint32_t)iarg(L, 1);
    lua_pushinteger(L, a < MOY_P8_MEM ? rd(p, a) : 0);
    return 1;
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
    uint32_t d = (uint32_t)iarg(L, 1), s = (uint32_t)iarg(L, 2);
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
    uint32_t d = (uint32_t)iarg(L, 1);
    uint8_t v = (uint8_t)iarg(L, 2);
    int32_t n = iarg(L, 3);
    if (n <= 0 || d >= MOY_P8_MEM) return 0;
    if ((uint32_t)n > MOY_P8_MEM - d) n = (int32_t)(MOY_P8_MEM - d);
    memset(p->mem + d, v, (size_t)n);
    apply_range(p, d, (uint32_t)n);
    return 0;
}

/* reload(dst, src, len): PICO-8 copies from the cart ROM -- the sheet, map,
 * flags and sound data as the cart file holds them -- into RAM. The ROM here
 * is the seeded image, snapshotted before the cart's first write; cstore is
 * the reverse, into the snapshot only (nothing is persisted). */
static int l_reload(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    uint32_t d = (uint32_t)iarg(L, 1), s = (uint32_t)iarg(L, 2);
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
    uint32_t d = (uint32_t)iarg(L, 1), s = (uint32_t)iarg(L, 2);
    int32_t n = lua_isnoneornil(L, 3) ? MOY_P8_ROM : iarg(L, 3);
    if (!p->rom || n <= 0 || d >= MOY_P8_ROM || s >= MOY_P8_MEM) return 0;
    if ((uint32_t)n > MOY_P8_ROM - d) n = (int32_t)(MOY_P8_ROM - d);
    if ((uint32_t)n > MOY_P8_MEM - s) n = (int32_t)(MOY_P8_MEM - s);
    if (s < 0x8000) sync_in(p, s, (uint32_t)n);
    memcpy(p->rom + d, p->mem + s, (size_t)n);
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

static int l_p8print(lua_State *L)
{
    moy_p8 *p = p8_of(L);
    moy_canvas *c = p->con->canvas;
    size_t len = 0, k;
    const char *s;
    int x, y, col, cx, cy;
    luaL_tolstring(L, 1, &len);
    s = lua_tolstring(L, -1, &len);
    x = iarg(L, 2); y = iarg(L, 3); col = iarg(L, 4);
    cx = x; cy = y;
    for (k = 0; k < len; k++) {
        int b = (unsigned char)s[k];
        if (b == 10) { cx = x; cy += 6; continue; }
        b = btn_glyph(b);
        if (b >= 32 && b < 128) {
            unsigned g = P8_GLYPHS[b - 32];
            int q;
            for (q = 0; q < 15; q++)
                if ((g >> q) & 1u) moy_put(c, cx + q % 3, cy + q / 3, col);
            cx += 4;
        } else if (b >= 128 && b < 128 + 26) {
            const uint8_t *rows = P8_WIDE + (b - 128) * 5;
            int r, any = 0;
            for (r = 0; r < 5; r++) any |= rows[r];
            if (any) {
                for (r = 0; r < 5; r++) {
                    int v = rows[r], kk;
                    for (kk = 0; kk < 7; kk++)
                        if ((v >> kk) & 1) moy_put(c, cx + kk, cy + r, col);
                }
                cx += 8;
            } else {
                cx += 4;
            }
        } else {
            cx += 4;
        }
    }
    lua_pop(L, 1);
    lua_pushinteger(L, cx);              /* PICO-8 0.2: print returns the pen x */
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
}

int moy_p8_open(struct lua_State *Ls, moy_console *con, moy_p8 *p,
                uint8_t *mem, uint8_t *rom)
{
    lua_State *L = (lua_State *)Ls;
    static const struct { const char *name; lua_CFunction fn; } T[] = {
        {"__moy_poke", l_poke}, {"__moy_peek", l_peek},
        {"__moy_memcpy", l_memcpy}, {"__moy_memset", l_memset},
        {"__moy_reload", l_reload}, {"__moy_cstore", l_cstore},
        {"__moy_p8print", l_p8print},
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
    return 0;
}
