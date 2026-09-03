/* libmoy -- the moy console, as a C library.
 *
 * https://github.com/moybyte-org/moy-spec is the spec; this implements its
 * raster and cart model in plain C99 with no dependencies, no allocation, and
 * no opinions about your platform.
 *
 * WHY IT EXISTS. moy's premise is that several vendors' handhelds run the same
 * cart. The reference implementation is MicroPython, so "implement moy" has so
 * far meant "adopt MicroPython" -- a large ask of an ESP-IDF or Arduino
 * firmware author, and the wrong one, because none of it is what the spec
 * actually requires. Here the whole console is a library you link, and the only
 * thing you write is the part that is genuinely yours: pixels out, buttons in.
 *
 * THE CONTRACT.
 *   - No allocation. You own every buffer; the library never calls malloc.
 *     A moy_canvas is a struct you can place in static storage or PSRAM.
 *   - No I/O, no time, no threads. Nothing here reads a file or a clock.
 *   - C99, freestanding-friendly: string.h is the only header the library
 *     needs, and no libm -- the raster is integer arithmetic throughout.
 *   - Every drawing verb honours camera, clip, pal and palt (SPEC.md 6),
 *     because they all funnel through moy_put or moy_rect.
 *
 * WHAT IT DELIBERATELY DOES NOT DO. There is no Lua VM in here, and no host
 * loop. The cart language is a binding on top of this (SPEC.md 4 says Lua 5.4,
 * and which Lua that is belongs to you), and the frame loop belongs to your
 * platform. That seam is the point: the verb table is the narrow waist, so a
 * Lua binding, a WASM import table and a native binding are each a few hundred
 * lines of glue rather than a new port of the console.
 *
 * VERIFICATION. libmoy is checked against the spec's own conformance suite --
 * the same golden frames the WebAssembly player and an ESP32-P4 are checked
 * against. See test/.
 *
 * MIT licensed, on purpose: a spec is only portable if its core is.
 */

#ifndef MOY_H_INCLUDED
#define MOY_H_INCLUDED

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MOY_VERSION "0.3.0"

/* SPEC.md 1: the console is a fixed-size machine. */
#define MOY_W            320
#define MOY_H            240
#define MOY_PALETTE      64      /* SPEC.md 2 */
#define MOY_TILE         8
#define MOY_SHEET_COLS   16      /* SPEC.md 3.2: 128 x 256 pixels ... */
#define MOY_SHEET_ROWS   32
#define MOY_SHEET_W      (MOY_SHEET_COLS * MOY_TILE)
#define MOY_SHEET_H      (MOY_SHEET_ROWS * MOY_TILE)
#define MOY_TILES        (MOY_SHEET_COLS * MOY_SHEET_ROWS)   /* ... 512 tiles */
#define MOY_MAP_MAX      128     /* SPEC.md 3.3: 128 x 128 cells = 16 KB */
#define MOY_MAP_MAX_ID   254     /* a cell holds id+1 in one byte */

/* SPEC.md 7.1 flip bits. */
#define MOY_FLIP_NONE 0
#define MOY_FLIP_X    1
#define MOY_FLIP_Y    2
#define MOY_FLIP_XY   3

/* -- what a pixel is ----------------------------------------------------- *
 *
 * By default the framebuffer is palette INDICES, one byte per pixel
 * (SPEC.md 1). Your display almost certainly wants something else; resolve at
 * flush time with moy_palette_rgb565 / moy_palette_rgb888, which is then the
 * only place the console cares what a colour looks like.
 *
 * Define MOY_PIXEL_RGB565 to build the direct-colour variant instead: the same
 * kernels, resolving colour at DRAW time into a 16-bit buffer, so the flush is
 * a copy rather than a lookup. SPEC.md 1.1 already allows this -- "A host
 * rendering direct to RGB565 pays 150 KB instead, its choice, not the cart's"
 * -- and it is observationally identical rather than merely close, because
 * SPEC.md 12.1 gives the console no display-time palette. Nothing can re-mean a
 * pixel after it is written, so resolving early is not an approximation of
 * resolving late; it is the same answer computed sooner. A cart cannot tell.
 *
 * WHICH ONE IS A PROPERTY OF YOUR HARDWARE, NOT YOUR TASTE. Measured on the
 * reference consoles with one rasterizer built both ways:
 *   - INDICES win where memory bandwidth is the constraint. On an ESP32-S3
 *     (no L2, octal PSRAM) the index build draws 1.1-3.3x faster, its flush is
 *     CHEAPER than the copy the 565 build needs, and the 150 KB buffer does not
 *     fit in internal SRAM at all where the 75 KB one does.
 *   - RGB565 wins where a 2D accelerator or panel consumes 565 directly and an
 *     index buffer would force a per-frame resolve onto the CPU. On an ESP32-P4
 *     the index build still draws 4-24% faster, but its blitter cannot read
 *     indices, so the resolve costs more than the drawing saves.
 *
 * Both builds are checked against the same SPEC.md 11 goldens: the index build
 * hashes its framebuffer, the 565 build hashes the frame it resolves to. They
 * must agree, and the suite is what says so.
 */
#ifdef MOY_PIXEL_RGB565
typedef uint16_t moy_pixel;
#define MOY_PIXEL_BYTES 2
#else
typedef uint8_t  moy_pixel;
#define MOY_PIXEL_BYTES 1
#endif

typedef struct {
    moy_pixel *pix;              /* w*h pixels -- YOURS, never allocated here */
    int      w, h;
    int      cam_x, cam_y;       /* SPEC.md 6 camera */
    int      clip_x0, clip_y0;   /* SPEC.md 6 clip, screen space (post-camera) */
    int      clip_x1, clip_y1;
    uint8_t  pal[MOY_PALETTE];   /* draw-time index remap */
    uint8_t  palt[MOY_PALETTE];  /* per-index sprite transparency */
    /* SPEC.md 6 / 12.1 screen palette: a second remap COMPOSED after pal into
     * store[] as pixels are drawn -- never applied to the canvas afterwards.
     * Kept as a table so a host can read it back (the PICO-8 machine peeks it
     * at 0x5f10); the raster only ever reads store[]. */
    uint8_t  spal[MOY_PALETTE];
    /* SPEC.md 6 fill pattern: 16 bits, row-major from the top-left of a 4x4
     * cell anchored to the SCREEN, a set bit is a hole. A hole pixel takes
     * colour fillp_col (through pal, at draw time) when that is >= 0 and is
     * left alone otherwise. 0 is solid, and the only test on a hot path.
     *
     * A host that fills a moy_canvas BY HAND for a kernel call, instead of
     * through moy_canvas_init, must set these two (0 and -1): every shape
     * verb reads them, and an uninitialised pattern is a circle full of
     * holes. That is exactly how the reference console found out. */
    uint16_t fillp;
    int      fillp_col;
    /* What a colour index becomes in the buffer, with pal already folded in.
     * The ONE thing the two builds disagree about on the hot path, and it is
     * precomputed: every verb writes store[index], so the per-pixel cost is a
     * lookup and a store either way, and the geometry above it is byte-for-byte
     * the same source. Maintained by moy_pal / moy_pal_reset / moy_reset_state
     * -- never assign c->pal directly. */
    moy_pixel store[MOY_PALETTE];
#ifdef MOY_PIXEL_RGB565
    uint16_t wire[MOY_PALETTE];  /* index -> panel word; see moy_canvas_wire */
#endif
} moy_canvas;

/* SPEC.md 3.2: sprite pixels are indices 0-15, one nibble each on disk. */
typedef struct {
    uint8_t *pix;                /* MOY_SHEET_W * MOY_SHEET_H, yours */
} moy_sheet;

/* SPEC.md 3.3: one byte per cell holding tile_id + 1, so 0 is empty. */
typedef struct {
    uint8_t *cells;              /* w*h, yours */
    int      w, h;
} moy_map;

/* -- lifecycle ---------------------------------------------------------- */

/* Point a canvas at your buffer and reset its draw state. `pix` must hold
 * w*h pixels -- w*h*MOY_PIXEL_BYTES bytes. Sizes other than 320x240 exist for
 * the `layers` extension. */
void moy_canvas_init(moy_canvas *c, moy_pixel *pix, int w, int h);

#ifdef MOY_PIXEL_RGB565
/* Tell a direct-colour canvas what each of the 64 palette indices looks like as
 * a 16-bit word, and rebuild the draw-time table.
 *
 * THE BYTE ORDER IS YOURS. Hand it byte-swapped words if that is what your
 * panel clocks out and nothing in the library notices -- the words are opaque
 * here, which is what keeps a device's wire format out of the cart contract.
 * The same freedom is why the index build exists at all.
 *
 * Optional: until it is called a canvas uses the SPEC.md 2.2 palette in
 * canonical RGB565, so a host that forgets gets correct colours rather than a
 * black screen. Call it again after changing a cart's 2.2 palette. */
void moy_canvas_wire(moy_canvas *c, const uint16_t tab[MOY_PALETTE]);
#endif

/* Camera to 0,0; clip to full screen; pal to identity; palt all opaque.
 * A host calls this before each cart frame: draw state is per-frame and must
 * not leak between carts, or from host UI into a cart's first frame. */
void moy_reset_state(moy_canvas *c);

/* SPEC.md 6 layers: copy the dst-sized window of `src` whose top-left is
 * (cam_x, cam_y) into `dst`. The point of a layer is that a wide level is
 * drawn ONCE and window-copied per frame instead of re-rendered, so this is
 * the per-frame half of it.
 *
 * Like cls, this is a COMPOSITING verb rather than a drawing one: it ignores
 * dst's camera, clip and pal, and writes whole rows. Source coordinates are
 * clamped, so a window hanging off the layer copies the edge rather than
 * reading past it. */
void moy_blit_window(moy_canvas *dst, const moy_canvas *src, int cam_x, int cam_y);

/* -- drawing (SPEC.md 6) ------------------------------------------------- */

void moy_cls   (moy_canvas *c, int col);
void moy_pix   (moy_canvas *c, int x, int y, int col);
int  moy_pget  (const moy_canvas *c, int x, int y);   /* camera-relative; 0 outside */
void moy_line  (moy_canvas *c, int x0, int y0, int x1, int y1, int col);
void moy_rect  (moy_canvas *c, int x, int y, int w, int h, int col);  /* FILLED */
void moy_rectb (moy_canvas *c, int x, int y, int w, int h, int col);  /* outline */
void moy_circ  (moy_canvas *c, int cx, int cy, int r, int col);       /* FILLED */
void moy_circb (moy_canvas *c, int cx, int cy, int r, int col);       /* outline */

/* Text, fixed 8px cell. `s` is BYTES and `len` their count -- SPEC.md 6 says
 * print walks bytes, not characters, because a Lua string is a byte string and
 * a host that decoded first would advance the cursor differently from one that
 * did not. Bytes outside 0x20-0x7F draw nothing and still advance. */
void moy_print (moy_canvas *c, const uint8_t *s, size_t len, int x, int y, int col);

void moy_camera(moy_canvas *c, int x, int y);
void moy_camera_reset(moy_canvas *c);
void moy_clip  (moy_canvas *c, int x, int y, int w, int h);
void moy_clip_reset(moy_canvas *c);
void moy_pal   (moy_canvas *c, int c0, int c1);
void moy_pal_reset(moy_canvas *c);          /* BOTH palettes: pal() with no args */
/* pal(c0, c1, 1): show c0 as c1, composed AFTER pal for every pixel drawn
 * from here on. There is no flush-time pass (SPEC.md 12.1): the canvas holds
 * what is shown, so a cart sets this before its cls to recolour a frame. */
void moy_pal_screen(moy_canvas *c, int c0, int c1);
void moy_pal_screen_reset(moy_canvas *c);
void moy_palt  (moy_canvas *c, int col, int on);
void moy_palt_reset(moy_canvas *c);
/* The fill pattern (SPEC.md 6): honoured by line, rect, rectb, circ, circb,
 * tri, trib, oval and ovalb; never by pix, print, cls, sprites or the map.
 * `col` < 0 leaves hole pixels untouched. */
void moy_fillp (moy_canvas *c, int p, int col);
void moy_fillp_reset(moy_canvas *c);
/* The ellipse inscribed in the w x h box at x, y -- FILLED, and its outline,
 * which is the fill's own rim pixel for pixel (one walk produces both). */
void moy_oval  (moy_canvas *c, int x, int y, int w, int h, int col);
void moy_ovalb (moy_canvas *c, int x, int y, int w, int h, int col);

/* PROVISIONAL -- SPEC.md 6.1 is unsettled and these are not part of core 0.3. */
void moy_tri   (moy_canvas *c, int x1, int y1, int x2, int y2, int x3, int y3, int col);
void moy_trib  (moy_canvas *c, int x1, int y1, int x2, int y2, int x3, int y3, int col);

/* -- sprites and map (SPEC.md 7.1, 7.2) ---------------------------------- */

void moy_sheet_init(moy_sheet *s, uint8_t *pix);
int  moy_sheet_pget(const moy_sheet *s, int x, int y);
/* SPEC.md 7.1 sset: the index is masked to 0-15, a write off the sheet is
 * dropped, and the next spr of that tile draws it. */
void moy_sheet_pset(moy_sheet *s, int x, int y, int c);

/* Tile `n` of 0..511 at x,y. `colorkey` is the transparent index or -1 for
 * opaque; `scale` an integer enlargement; `flip` one of MOY_FLIP_*. A tile id
 * out of range draws nothing -- SPEC.md 3.2 lets a short sheet leave the rest
 * blank, so asking for a blank tile is legal. */
void moy_spr(moy_canvas *c, const moy_sheet *s, int n, int x, int y,
             int colorkey, int scale, int flip);

/* Stretch a sheet PIXEL region into a dw x dh rect, nearest-neighbour.
 * PROVISIONAL (SPEC.md 6.1). */
void moy_sspr(moy_canvas *c, const moy_sheet *s, int sx, int sy, int sw, int sh,
              int dx, int dy, int dw, int dh, int colorkey, int flip);

void moy_map_init(moy_map *m, uint8_t *cells, int w, int h);
int  moy_mget(const moy_map *m, int x, int y);          /* -1 empty OR out of range */
void moy_mset(moy_map *m, int x, int y, int tile);      /* negative clears */
void moy_map_draw(moy_canvas *c, const moy_map *m, const moy_sheet *s,
                  int mx, int my, int w, int h, int sx, int sy,
                  int colorkey, int scale);
/* SPEC.md 7.2 map(..., layers): with `layers` non-zero a cell draws only when
 * its tile's flag byte shares a bit with it; `flags` is the MOY_FLAGS-byte
 * table (NULL: no cell passes). layers == 0 is moy_map_draw. */
#define MOY_FLAGS 512
void moy_map_draw_layers(moy_canvas *c, const moy_map *m, const moy_sheet *s,
                         int mx, int my, int w, int h, int sx, int sy,
                         int colorkey, int scale, int layers,
                         const uint8_t *flags);

/* Textured line: exactly moy_line's pixels, sampling the MAP as a virtual
 * texture of m->w*8 x m->h*8 pixels. u, v, du, dv are 16.16 fixed point --
 * the texel (u>>16, v>>16) is sampled before each pixel, then u += du,
 * v += dv, for every walked pixel whether drawn or not. Coordinates wrap
 * modulo the map's pixel size; empty cells draw nothing. The Mode 7 verb.
 * PROVISIONAL (SPEC.md 6.1). */
void moy_tline(moy_canvas *c, const moy_sheet *s, const moy_map *m,
               int x0, int y0, int x1, int y1,
               int32_t u, int32_t v, int32_t du, int32_t dv, int colorkey);

/* -- the host seam (SPEC.md 7.3, 9) -------------------------------------- */

/* SPEC.md 7.3's logical buttons. Each host maps its own hardware onto them --
 * a d-pad, a keyboard, a trackball, an on-screen pad -- and no two
 * implementations need the same physical controls. `run` is the only optional
 * one; a cart MUST be playable with the other six. */
typedef enum {
    MOY_BTN_LEFT = 0, MOY_BTN_RIGHT, MOY_BTN_UP, MOY_BTN_DOWN,
    MOY_BTN_A, MOY_BTN_B, MOY_BTN_RUN, MOY_BTN_COUNT
} moy_button;

/* Everything the console needs FROM a platform, and nothing else. This is the
 * porting surface: implement these and moy runs. Any of them may be NULL --
 * a console with no audio hardware leaves sfx NULL, and SPEC.md 8.3 says
 * silence is a valid rendering, so the cart neither knows nor cares. */
typedef struct {
    void *user;
    int      (*btn)(void *user, moy_button b, int player);   /* held now */
    int      (*btnp)(void *user, moy_button b, int player);  /* pressed this tick */
    int      (*players)(void *user);                         /* always >= 1 */
    uint32_t (*time_ms)(void *user);                         /* since the cart started */
    int32_t  (*pmem_get)(void *user, int slot);              /* 256 slots, signed 32-bit */
    void     (*pmem_set)(void *user, int slot, int32_t value);
    void     (*sfx)(void *user, int n, int chan);
    void     (*music)(void *user, int track, int loop);
    /* The rest of SPEC.md 8.2, same rule: NULL is a conforming no-op. The
     * verbs still EXIST in the cart's world either way -- a cart calling
     * music_stop() on a silent host must get silence, not an error. */
    void     (*beep)(void *user, float freq_hz, float dur_s);
    void     (*music_stop)(void *user);
    void     (*sound_stop)(void *user, int chan);            /* chan < 0: all */
    void     (*volume)(void *user, int level);
    /* SPEC.md 7.3's optional input. NULL means the hardware is absent, and
     * the verbs answer accordingly: touch() reads nil, key()/keyp() read
     * false (0 for the no-argument form), textmode() is a no-op. */
    int      (*touch)(void *user, int out_xyth[4]);          /* 0 = no pointer */
    int      (*key)(void *user, int code);                   /* code < 0: last typed */
    int      (*keyp)(void *user, int code);
    void     (*textmode)(void *user, int on);
    void     (*quit)(void *user);                            /* SPEC.md 9 */
    /* SPEC.md 9: a value from the cart's config.json, or NULL. The console
     * never parses JSON -- config is the author's tuning surface and reading
     * it is a host's job. */
    const char *(*cfg)(void *user, const char *key);

    /* -- what the host VARIES, never withholds (SPEC.md 6) ---------------
     *
     * These back core verbs, so leaving one NULL changes what the console does
     * with a call, never whether the call exists: the verb is a global either
     * way and a cart needs no nil-guard. That is precisely why SPEC.md 10 does
     * not list them -- a capability a cart can be shielded from is not an
     * extension.
     *
     * view(w, h) declares a logical viewport smaller than the canvas.
     * Compositing it centered at the largest integer scale that fits is the
     * host's job, because only the host knows what it is compositing onto;
     * libmoy relays the declaration and also records it in moy_console, so a
     * host may poll instead of taking the callback. A host that does neither
     * presents the whole canvas, which is the cart's region unscaled. */
    void (*view)(void *user, int w, int h);        /* optional; see moy_console */

    /* Layers (SPEC.md 6): the host owns the memory, as it does for every
     * other buffer here -- libmoy allocates nothing. 1.1 guarantees a cart ONE
     * full-screen layer, so a conforming host implements this. layer_new
     * returns w*h pixels (or NULL to decline a FURTHER one, which makes
     * make_layer return nil rather than fail), and
     * layer_free is optional: a host whose layers live until the cart exits
     * may leave it NULL and reclaim them wholesale. */
    moy_pixel *(*layer_new)(void *user, int w, int h);
    void (*layer_free)(void *user, moy_pixel *pix);
    /* background(col): OPTIONAL, and only as an optimisation -- libmoy clears
     * to the declared colour itself when this is NULL (see moy_console.bg), so
     * the verb always works. A host takes it over when it can do better than a
     * full clear, e.g. restoring a cached backdrop. */
    void (*background)(void *user, int col);
} moy_host;

/* What a cart is given: the canvas it draws on, the assets it draws from, and
 * the platform underneath. One struct so a language binding has one thing to
 * bind to -- which is the whole reason the verb table is the narrow waist. */
typedef struct {
    moy_canvas *canvas;
    moy_sheet  *sheet;
    moy_map    *map;
    /* SPEC.md 3.5 tile flags: MOY_FLAGS bytes, YOURS, or NULL for a host
     * with none -- then fget reads 0, fset is a no-op and a non-zero map
     * layer mask draws nothing. */
    uint8_t    *flags;
    moy_host    host;
    uint32_t    rng;        /* see moy_rnd */
    /* SPEC.md 6, the always-present half. A cart calls view() or background()
     * unguarded; what it declared lands HERE whether or not the host took the
     * callback, so a host may read state instead of accepting calls, and a
     * host that does neither still runs the cart correctly -- unscaled for
     * view, cleared by libmoy for background. view_w == 0 means undeclared. */
    int         view_w, view_h;
    int         bg, has_bg;
} moy_console;

void moy_console_init(moy_console *con, moy_canvas *c, moy_sheet *s, moy_map *m);

/* SPEC.md 9's rnd(). NOTE: the spec defines the RANGE but not the SEQUENCE, so
 * two conforming hosts may differ on every number and both be right. This is
 * xorshift32, stated here so the question is at least askable; a conformance
 * scene cannot call rnd() until the spec picks one. */
float moy_rnd(moy_console *con, float n);
void  moy_srand(moy_console *con, uint32_t seed);

/* -- the Lua binding (SPEC.md 4) ----------------------------------------- */
/* Built only when MOY_WITH_LUA is defined, because the VM is yours: libmoy
 * binds to whatever lua_State you hand it rather than embedding one. */
#ifdef MOY_WITH_LUA
struct lua_State;
/* Install the SPEC.md 4.1 sandbox (base minus load/dofile/require/
 * collectgarbage, plus math, string and table) and the whole verb table as
 * globals on `L`, bound to `con`. Returns 0 on success. */
int moy_lua_open(struct lua_State *L, moy_console *con);
/* Run the three cart hooks, if the cart defined them. Return 0 on success; on
 * a Lua error, non-zero with the message in `err` (SPEC.md 4.3: an error
 * terminates the cart and the host reports it with the script line number). */
int moy_lua_init  (struct lua_State *L, char *err, size_t errlen);
int moy_lua_update(struct lua_State *L, float dt, char *err, size_t errlen);
int moy_lua_draw  (struct lua_State *L, char *err, size_t errlen);

/* -- the PICO-8 machine, for ported carts (src/moy_p8.c, PICO8.md) --------
 *
 * OPT-IN and not part of SPEC.md: a host that calls moy_p8_open offers the
 * `__moy_*` globals the p8 port shim probes for -- a 64 KB memory map with
 * the sheet, map, flags, palettes, camera/clip and the screen behind their
 * PICO-8 addresses, the ROM snapshot reload()/cstore() copy from, the 3x5
 * system font, and the shim's own hot verbs (the table walk, the number and
 * bit verbs, the map and flag reads) so a ported cart does not run them as
 * Lua closures. Both buffers are YOURS (libmoy allocates nothing):
 * MOY_P8_MEM bytes of memory and MOY_P8_ROM of ROM, or NULL for no ROM.
 * Call it after moy_lua_open and before the cart's source runs; it seeds
 * memory from the console's assets as they stand. */
#define MOY_P8_MEM 0x10000
#define MOY_P8_ROM 0x4300
typedef struct {
    moy_console *con;
    uint8_t *mem;
    uint8_t *rom;
    /* The p8 PRINT CURSOR. PICO-8 keeps it at 0x5f26/0x5f27 and so does this
     * (peek and poke reach it there), but as one BYTE each -- and the port
     * shim never wrapped it, because it does not scroll the screen the way
     * PICO-8 does when the cursor runs off the bottom. So the full value
     * lives here and the bytes are its low half, the same arrangement the
     * camera already has. */
    int32_t cur_x, cur_y;
    /* btn/btnp's latch, in CART ticks (PICO8.md): `hold` is how many ticks a
     * button has been down, `pending` an edge seen this console frame and not
     * yet consumed by a tick. Six buttons, p8's own numbering. */
    uint16_t hold[6];
    uint8_t pending[6];
    uint8_t consumed;
} moy_p8;
int moy_p8_open(struct lua_State *L, moy_console *con, moy_p8 *p8,
                uint8_t *mem, uint8_t *rom);
#endif

/* -- palette and font (SPEC.md 2, 6) ------------------------------------- */

/* The default table, straight from the spec's palette.json (generated, see
 * tools/embed_data.py). 64 entries of r,g,b. A cart may replace it wholesale
 * (SPEC.md 2.2) -- pass your own to the resolvers below. */
extern const uint8_t moy_palette_default[MOY_PALETTE * 3];

/* The 8x8 font, from the spec's font.bin: 96 glyphs for 0x20-0x7F, 8 bytes per
 * glyph, one byte per COLUMN, LSB = top row. */
extern const uint8_t moy_font_data[96 * 8];

/* Resolve the whole framebuffer at flush time. `pal` may be NULL for the
 * default. rgb565 is big-endian, which is what most panels want. */
void moy_palette_rgb888(const moy_canvas *c, const uint8_t *pal, uint8_t *out);
void moy_palette_rgb565(const moy_canvas *c, const uint8_t *pal, uint16_t *out);

#ifdef __cplusplus
}
#endif
#endif /* MOY_H_INCLUDED */
