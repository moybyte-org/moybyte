// moycore: the cart's whole frame in C (moycore plan stage 2).
//
// A Lua cart's frame used to be a conversation. The VM called a verb, the verb
// was a C shim that upcalled a Python closure from `make_api`, that closure
// drew through DeviceCanvas, and control came back -- hundreds of times per
// frame, each crossing marshalling arguments and leaving garbage the
// MicroPython GC would collect in one of the ~190ms sweeps that show up as
// stutter. Stages 1a/1b cut the hottest of those crossings (the sprite batch,
// the solid draw family, sspr/tline). This module deletes the rest by moving
// the whole loop: `run_begin()` builds a libmoy console over the buffers the
// console already owns, and `tick(dt)` runs the cart's _update and _draw end to
// end in C. ONE upcall per frame.
//
// THE ENGINE IS NOT WRITTEN HERE, AND THAT IS THE POINT. `libmoy/moy_lua.c` is
// moy-spec's own Lua binding: it registers all 38 SPEC.md verbs as C functions
// against a `moy_console`, and `moy.h` exports the loop
// (`moy_lua_open`/`init`/`update`/`draw`) with the error text crossing as a
// buffer. Re-implementing any of that here -- which is what "finish stage 1 by
// crossing cls, map, camera, clip and pal" would have meant -- would be a
// second C implementation of code that already exists upstream, which is the
// duplication this whole project exists to end. See the plan's 6.0.
//
// What this file IS, therefore, is the HOST half: the glue that says what a
// moybyte console is made of.
//
//   * The canvas is the DeviceCanvas framebuffer, not a copy. libmoy's canvas
//     takes a caller-owned `pix` and (on the RGB565 build) a caller-supplied
//     wire table, explicitly so the device's byte order stays out of the cart
//     contract -- so the cart draws straight into the buffer the compositor is
//     about to present.
//   * Input, time and the pointer arrive through a SNAPSHOT the frame loop
//     refreshes before the tick, not through callbacks into Python. A cart
//     polling btn() 60 times a frame must not cost 60 crossings.
//   * Audio goes the other way, into a small command QUEUE the host drains
//     after the tick. sfx() from inside a cart is a two-int append, and the
//     Python side plays it when the frame is over -- same order, same frame.
//   * pmem lives in a C array with a dirty flag, which is the shape the device
//     already deferred it to (#66): RAM during play, persisted at boundaries.
//
// Verbs moybyte adds ON TOP of the spec -- layers/images, scenes, tables,
// texts, view() -- are NOT bound here. The cart census that decided this is in
// the plan: exactly one Lua cart in the tree uses layers (its Python twin, for
// A/B), and its cost is one blit per frame rather than one per sprite, so
// implementing a second console in C to serve it would trade the duplication
// this module deletes for a smaller one. They stay Python-side; a cart that
// uses them makes a second upcall per frame and the status says so.

#include <stdlib.h>
#include <string.h>

#include "py/obj.h"
#include "py/objarray.h"
#include "py/runtime.h"
#include "py/mphal.h"

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"

// The snapshot the host refreshes before every tick. Plain int32 slots in a
// buffer Python owns, so a cart's btn()/time()/touch() are array reads on this
// side of the wall and one array write on the other.
enum {
    SNAP_BTN = 0,        // held bitmask, player 0 (moy_button bit positions)
    SNAP_BTNP,           // pressed-this-tick bitmask, player 0
    SNAP_BTN_P1,         // ...and player 1, for the two-player forms
    SNAP_BTNP_P1,
    SNAP_PLAYERS,        // always >= 1
    SNAP_TIME_MS,        // since the cart started
    SNAP_TOUCH_X,
    SNAP_TOUCH_Y,
    SNAP_TOUCH_DOWN,     // 0 = no pointer at all (touch() reads nil)
    SNAP_TOUCH_MS,       // how long the current press has lasted
    SNAP_KEY,            // last typed code, or 0
    SNAP_KEY_DOWN,       // bitmap-free: the code currently held, or 0
    SNAP_TEXTMODE,       // written BY the cart (textmode)
    SNAP_QUIT,           // written BY the cart (quit)
    SNAP_LEN,
};

// The audio queue. One int16 op code plus three int16 args, appended by the
// cart and drained by the host after the tick. Deliberately fixed and small: a
// frame that asks for more sound than this is not a frame anybody wanted.
enum { AQ_SFX = 0, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME };
#define AQ_SLOTS 4
#define AQ_MAX   32

typedef struct {
    lua_State  *L;
    moy_console con;
    moy_canvas  canvas;
    moy_sheet   sheet;
    moy_map     map;
    int32_t    *snap;            // Python-owned array("i"), SNAP_LEN entries
    int32_t     pmem[256];
    int         pmem_dirty;
    int16_t    *aq;              // Python-owned array("h"): [n, (op,a,b,c)*]
    size_t      aq_cap;
    mp_obj_t    cfg;             // the cart's config dict, or MP_OBJ_NULL
    int         open;
} moycore_run;

static moycore_run RUN;

// -- the host callbacks ------------------------------------------------------
// Every one of these is a read or a write against the snapshot/queue above.
// None of them re-enters Python: that is the entire point of the module.

static int h_btn(void *user, moy_button b, int player)
{
    (void)user;
    if (!RUN.snap) return 0;
    int32_t mask = RUN.snap[player > 0 ? SNAP_BTN_P1 : SNAP_BTN];
    return (mask >> (int)b) & 1;
}

static int h_btnp(void *user, moy_button b, int player)
{
    (void)user;
    if (!RUN.snap) return 0;
    int32_t mask = RUN.snap[player > 0 ? SNAP_BTNP_P1 : SNAP_BTNP];
    return (mask >> (int)b) & 1;
}

static int h_players(void *user)
{
    (void)user;
    int n = RUN.snap ? (int)RUN.snap[SNAP_PLAYERS] : 1;
    return n < 1 ? 1 : n;
}

static uint32_t h_time_ms(void *user)
{
    (void)user;
    return RUN.snap ? (uint32_t)RUN.snap[SNAP_TIME_MS] : 0;
}

static int32_t h_pmem_get(void *user, int slot)
{
    (void)user;
    if (slot < 0 || slot > 255) return 0;
    return RUN.pmem[slot];
}

static void h_pmem_set(void *user, int slot, int32_t value)
{
    (void)user;
    if (slot < 0 || slot > 255) return;
    if (RUN.pmem[slot] != value) {
        RUN.pmem[slot] = value;
        RUN.pmem_dirty = 1;
    }
}

static void aq_push(int op, int a, int b, int c)
{
    if (!RUN.aq || RUN.aq_cap < 1 + AQ_SLOTS) return;
    int n = RUN.aq[0];
    if (n < 0) n = 0;
    if ((size_t)(1 + (n + 1) * AQ_SLOTS) > RUN.aq_cap || n >= AQ_MAX) return;
    int16_t *p = RUN.aq + 1 + n * AQ_SLOTS;
    p[0] = (int16_t)op; p[1] = (int16_t)a; p[2] = (int16_t)b; p[3] = (int16_t)c;
    RUN.aq[0] = (int16_t)(n + 1);
}

static void h_sfx(void *user, int n, int chan) { (void)user; aq_push(AQ_SFX, n, chan, 0); }
static void h_music(void *user, int t, int loop) { (void)user; aq_push(AQ_MUSIC, t, loop, 0); }
static void h_music_stop(void *user) { (void)user; aq_push(AQ_MUSIC_STOP, 0, 0, 0); }
static void h_sound_stop(void *user, int chan) { (void)user; aq_push(AQ_SOUND_STOP, chan, 0, 0); }
static void h_volume(void *user, int level) { (void)user; aq_push(AQ_VOLUME, level, 0, 0); }

static void h_beep(void *user, float freq_hz, float dur_s)
{
    (void)user;
    // Milliseconds and whole hertz: the queue is int16 and a beep's precision
    // beyond that is inaudible. dur is clamped to the int16 ceiling (~32s),
    // which is longer than any beep anybody meant.
    int ms = (int)(dur_s * 1000.0f);
    if (ms < 0) ms = 0;
    if (ms > 32000) ms = 32000;
    int hz = (int)freq_hz;
    if (hz < 0) hz = 0;
    if (hz > 32000) hz = 32000;
    aq_push(AQ_BEEP, hz, ms, 0);
}

static int h_touch(void *user, int out_xyth[4])
{
    (void)user;
    if (!RUN.snap || !RUN.snap[SNAP_TOUCH_DOWN]) return 0;
    out_xyth[0] = RUN.snap[SNAP_TOUCH_X];
    out_xyth[1] = RUN.snap[SNAP_TOUCH_Y];
    out_xyth[2] = RUN.snap[SNAP_TOUCH_DOWN];
    out_xyth[3] = RUN.snap[SNAP_TOUCH_MS];
    return 1;
}

static int h_key(void *user, int code)
{
    (void)user;
    if (!RUN.snap) return 0;
    if (code < 0) return RUN.snap[SNAP_KEY];          // the last typed code
    return RUN.snap[SNAP_KEY_DOWN] == code;
}

static int h_keyp(void *user, int code)
{
    (void)user;
    if (!RUN.snap) return 0;
    if (code < 0) return RUN.snap[SNAP_KEY];
    return RUN.snap[SNAP_KEY] == code;
}

static void h_textmode(void *user, int on)
{
    (void)user;
    if (RUN.snap) RUN.snap[SNAP_TEXTMODE] = on ? 1 : 0;
}

static void h_quit(void *user)
{
    (void)user;
    if (RUN.snap) RUN.snap[SNAP_QUIT] = 1;
}

static const char *h_cfg(void *user, const char *key)
{
    (void)user;
    if (RUN.cfg == MP_OBJ_NULL || key == NULL) return NULL;
    mp_obj_t k = mp_obj_new_str(key, strlen(key));
    mp_map_elem_t *e = mp_map_lookup(mp_obj_dict_get_map(RUN.cfg), k,
                                     MP_MAP_LOOKUP);
    if (e == NULL || e->value == MP_OBJ_NULL) return NULL;
    if (!mp_obj_is_str(e->value)) return NULL;
    // The dict owns the string; libmoy only reads it during the call.
    return mp_obj_str_get_str(e->value);
}

// -- helpers -----------------------------------------------------------------

static void *buf_w(mp_obj_t o, size_t *len)
{
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_WRITE);
    if (len) *len = bi.len;
    return bi.buf;
}

static void *buf_r(mp_obj_t o, size_t *len)
{
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_READ);
    if (len) *len = bi.len;
    return bi.buf;
}

// The VM's allocator. Lua's interface is realloc-shaped, so this is the system
// heap -- NOT the MicroPython gc heap, which is the point: a cart's whole Lua
// world stays outside the heap MP sweeps, exactly as moy_lua's allocator
// arranged, so cart churn cannot lengthen a shell collect.
//
// It is not yet moy_lua's SRAM-first policy (that module measured the
// all-PSRAM version ~2x slower on the S3's 120MHz OCT bus). When moycore runs
// on the S3 it should adopt the same floor; on the host and the P4 this is the
// right allocator already.
static void *l_alloc(void *ud, void *ptr, size_t osize, size_t nsize)
{
    (void)ud; (void)osize;
    if (nsize == 0) { free(ptr); return NULL; }
    return realloc(ptr, nsize);
}

// -- the module surface ------------------------------------------------------

// run_begin(fb, w, h, wire, sheet_pix, map_cells, map_w, map_h,
//           snap, audio_q, pmem_bytes, cfg, source, chunkname)
//
// Everything is a buffer the console already owns: the framebuffer the
// compositor presents, the sheet and tilemap the project holds, the snapshot
// and queue the loop refreshes. Nothing is copied and nothing is allocated
// here except the VM.
static mp_obj_t mod_run_begin(size_t n_args, const mp_obj_t *a)
{
    if (n_args != 14) mp_raise_TypeError(MP_ERROR_TEXT("run_begin: 14 args"));
    if (RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                               MP_ERROR_TEXT("moycore: a run is already open"));
    memset(&RUN, 0, sizeof(RUN));

    size_t fblen = 0;
    moy_pixel *fb = (moy_pixel *)buf_w(a[0], &fblen);
    int w = mp_obj_get_int(a[1]), h = mp_obj_get_int(a[2]);
    if (w <= 0 || h <= 0 || fblen < (size_t)w * (size_t)h * sizeof(moy_pixel))
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: framebuffer too small"));
    moy_canvas_init(&RUN.canvas, fb, w, h);
#ifdef MOY_PIXEL_RGB565
    if (a[3] != mp_const_none) {
        size_t wlen = 0;
        const uint16_t *wire = (const uint16_t *)buf_r(a[3], &wlen);
        if (wlen < MOY_PALETTE * 2)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: wire table too small"));
        moy_canvas_wire(&RUN.canvas, wire);
    }
#endif

    if (a[4] != mp_const_none) {
        size_t slen = 0;
        RUN.sheet.pix = (uint8_t *)buf_r(a[4], &slen);
        if (slen < (size_t)MOY_SHEET_W * MOY_SHEET_H)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: sheet too small"));
    }
    if (a[5] != mp_const_none) {
        size_t mlen = 0;
        RUN.map.cells = (uint8_t *)buf_r(a[5], &mlen);
        RUN.map.w = mp_obj_get_int(a[6]);
        RUN.map.h = mp_obj_get_int(a[7]);
        if (RUN.map.w < 0 || RUN.map.h < 0
            || (size_t)RUN.map.w * (size_t)RUN.map.h > mlen)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: map too small"));
    }

    size_t snlen = 0;
    RUN.snap = (int32_t *)buf_w(a[8], &snlen);
    if (snlen < SNAP_LEN * sizeof(int32_t))
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: snapshot too small"));
    size_t aqlen = 0;
    RUN.aq = (int16_t *)buf_w(a[9], &aqlen);
    RUN.aq_cap = aqlen / sizeof(int16_t);
    if (RUN.aq_cap < 1 + AQ_SLOTS)
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: audio queue too small"));
    RUN.aq[0] = 0;

    if (a[10] != mp_const_none) {                 // pmem image in, 256 int32
        size_t plen = 0;
        const int32_t *p = (const int32_t *)buf_r(a[10], &plen);
        size_t n = plen / sizeof(int32_t);
        if (n > 256) n = 256;
        memcpy(RUN.pmem, p, n * sizeof(int32_t));
    }
    RUN.cfg = (a[11] == mp_const_none) ? MP_OBJ_NULL : a[11];

    RUN.con.canvas = &RUN.canvas;
    RUN.con.sheet  = RUN.sheet.pix ? &RUN.sheet : NULL;
    RUN.con.map    = RUN.map.cells ? &RUN.map : NULL;
    RUN.con.rng    = 0;
    moy_host *hs = &RUN.con.host;
    hs->user = NULL;
    hs->btn = h_btn;  hs->btnp = h_btnp;  hs->players = h_players;
    hs->time_ms = h_time_ms;
    hs->pmem_get = h_pmem_get;  hs->pmem_set = h_pmem_set;
    hs->sfx = h_sfx;  hs->music = h_music;  hs->beep = h_beep;
    hs->music_stop = h_music_stop;  hs->sound_stop = h_sound_stop;
    hs->volume = h_volume;
    hs->touch = h_touch;  hs->key = h_key;  hs->keyp = h_keyp;
    hs->textmode = h_textmode;  hs->quit = h_quit;
    hs->cfg = h_cfg;

    RUN.L = lua_newstate(l_alloc, NULL);
    if (RUN.L == NULL) mp_raise_msg(&mp_type_MemoryError,
                                    MP_ERROR_TEXT("moycore: no VM"));
    if (moy_lua_open(RUN.L, &RUN.con) != 0) {
        lua_close(RUN.L); RUN.L = NULL;
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moycore: sandbox failed"));
    }

    size_t srclen = 0;
    const char *src = mp_obj_str_get_data(a[12], &srclen);
    const char *name = mp_obj_str_get_str(a[13]);
    RUN.open = 1;
    if (luaL_loadbuffer(RUN.L, src, srclen, name) != LUA_OK
        || lua_pcall(RUN.L, 0, 0, 0) != LUA_OK) {
        const char *msg = lua_tostring(RUN.L, -1);
        mp_obj_t err = mp_obj_new_str(msg ? msg : "load failed",
                                      strlen(msg ? msg : "load failed"));
        return err;                                // the caller reports it
    }
    char err[192];
    if (moy_lua_init(RUN.L, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    return mp_const_none;                          // started clean
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_run_begin_obj, 14, 14, mod_run_begin);

// tick(dt) -> None on a clean frame, else the error text.
//
// The whole frame: reset the per-frame draw state (SPEC.md's rule that draw
// state must not leak between frames or from host UI into a cart), then
// _update and _draw. The host refreshed the snapshot before calling and drains
// the audio queue after.
static mp_obj_t mod_tick(mp_obj_t dt_obj)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    char err[192];
    moy_reset_state(&RUN.canvas);
    float dt = (float)mp_obj_get_float(dt_obj);
    if (moy_lua_update(RUN.L, dt, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    if (moy_lua_draw(RUN.L, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_tick_obj, mod_tick);

// pmem_image(out) -> dirty flag. The host persists at boundaries (#66), so it
// asks for the image rather than being told about every poke.
static mp_obj_t mod_pmem_image(mp_obj_t out)
{
    size_t len = 0;
    int32_t *p = (int32_t *)buf_w(out, &len);
    size_t n = len / sizeof(int32_t);
    if (n > 256) n = 256;
    memcpy(p, RUN.pmem, n * sizeof(int32_t));
    int d = RUN.pmem_dirty;
    RUN.pmem_dirty = 0;
    return mp_obj_new_bool(d);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_pmem_image_obj, mod_pmem_image);

// retarget(fb) -- the double/triple-buffered tiers swap the framebuffer under
// the canvas every frame, exactly as DeviceCanvas.sync_back does.
static mp_obj_t mod_retarget(mp_obj_t fb_obj)
{
    if (!RUN.open) return mp_const_none;
    size_t len = 0;
    moy_pixel *fb = (moy_pixel *)buf_w(fb_obj, &len);
    if (len < (size_t)RUN.canvas.w * (size_t)RUN.canvas.h * sizeof(moy_pixel))
        mp_raise_ValueError(MP_ERROR_TEXT("retarget: framebuffer too small"));
    RUN.canvas.pix = fb;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_retarget_obj, mod_retarget);

static mp_obj_t mod_close(void)
{
    if (RUN.L) lua_close(RUN.L);
    RUN.L = NULL;
    RUN.open = 0;
    RUN.snap = NULL;
    RUN.aq = NULL;
    RUN.cfg = MP_OBJ_NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_close_obj, mod_close);

static mp_obj_t mod_active(void) { return mp_obj_new_bool(RUN.open); }
static MP_DEFINE_CONST_FUN_OBJ_0(mod_active_obj, mod_active);

static const mp_rom_map_elem_t moycore_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),    MP_OBJ_NEW_QSTR(MP_QSTR_moycore) },
    { MP_ROM_QSTR(MP_QSTR_run_begin),   MP_ROM_PTR(&mod_run_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick),        MP_ROM_PTR(&mod_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_pmem_image),  MP_ROM_PTR(&mod_pmem_image_obj) },
    { MP_ROM_QSTR(MP_QSTR_retarget),    MP_ROM_PTR(&mod_retarget_obj) },
    { MP_ROM_QSTR(MP_QSTR_close),       MP_ROM_PTR(&mod_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_active),      MP_ROM_PTR(&mod_active_obj) },
    // The snapshot layout, exported so the Python side cannot drift from it.
    { MP_ROM_QSTR(MP_QSTR_SNAP_LEN),    MP_ROM_INT(SNAP_LEN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTN),    MP_ROM_INT(SNAP_BTN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTNP),   MP_ROM_INT(SNAP_BTNP) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTN_P1), MP_ROM_INT(SNAP_BTN_P1) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTNP_P1), MP_ROM_INT(SNAP_BTNP_P1) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_PLAYERS), MP_ROM_INT(SNAP_PLAYERS) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TIME_MS), MP_ROM_INT(SNAP_TIME_MS) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_X), MP_ROM_INT(SNAP_TOUCH_X) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_Y), MP_ROM_INT(SNAP_TOUCH_Y) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_DOWN), MP_ROM_INT(SNAP_TOUCH_DOWN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_MS), MP_ROM_INT(SNAP_TOUCH_MS) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_KEY),    MP_ROM_INT(SNAP_KEY) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_KEY_DOWN), MP_ROM_INT(SNAP_KEY_DOWN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TEXTMODE), MP_ROM_INT(SNAP_TEXTMODE) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_QUIT),   MP_ROM_INT(SNAP_QUIT) },
    // ...and the audio queue's.
    { MP_ROM_QSTR(MP_QSTR_AQ_SLOTS),    MP_ROM_INT(AQ_SLOTS) },
    { MP_ROM_QSTR(MP_QSTR_AQ_MAX),      MP_ROM_INT(AQ_MAX) },
    { MP_ROM_QSTR(MP_QSTR_AQ_SFX),      MP_ROM_INT(AQ_SFX) },
    { MP_ROM_QSTR(MP_QSTR_AQ_MUSIC),    MP_ROM_INT(AQ_MUSIC) },
    { MP_ROM_QSTR(MP_QSTR_AQ_BEEP),     MP_ROM_INT(AQ_BEEP) },
    { MP_ROM_QSTR(MP_QSTR_AQ_MUSIC_STOP), MP_ROM_INT(AQ_MUSIC_STOP) },
    { MP_ROM_QSTR(MP_QSTR_AQ_SOUND_STOP), MP_ROM_INT(AQ_SOUND_STOP) },
    { MP_ROM_QSTR(MP_QSTR_AQ_VOLUME),   MP_ROM_INT(AQ_VOLUME) },
};
static MP_DEFINE_CONST_DICT(moycore_globals, moycore_globals_table);

const mp_obj_module_t moycore_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moycore_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moycore, moycore_user_cmodule);
