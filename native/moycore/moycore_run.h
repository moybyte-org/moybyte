// moycore_run: the console a cart runs against, with no VM in it
// (docs/kernel_cartpath_2026-10.md §1, the modmoycore.c split).
//
// libmoy's console over buffers its caller holds -- the canvas, the sheet and
// the map -- and the host callbacks libmoy's verbs call: input, time and the
// pointer read from a SNAPSHOT the frame refreshes before the tick, audio
// appended to a QUEUE drained after it, pmem in a C array with a dirty mark,
// the cart's config as a C table, the tile flags copied in. None of it calls
// into a VM, which is what lets one copy serve every tier: modmoycore.c (the
// boards, the browser and the desktop MicroPython) and the host's ctypes
// shims (runtime/moyhost_lua.c, runtime/moyhost_wasm.c), one run at a time.
//
// The callbacks take libmoy's `void *user` and read `moycore_run_cur`, the
// run being driven.

#ifndef MOYCORE_RUN_H
#define MOYCORE_RUN_H

#include <stdint.h>

#include "moy.h"

// The snapshot's slots, int32 each.
enum {
    SNAP_BTN = 0,        // held bitmask, player 0 (moy_button bit positions)
    SNAP_BTNP,           // pressed-this-tick bitmask, player 0
    SNAP_BTN_P1,         // ...and player 1, for the two-player forms
    SNAP_BTNP_P1,
    SNAP_PLAYERS,        // always >= 1
    SNAP_TIME_MS,        // since the cart started
    SNAP_TOUCH_X,
    SNAP_TOUCH_Y,
    SNAP_TOUCH_DOWN,     // flags: 1 live, 2 held, 4 the press edge; 0 no pointer
    SNAP_TOUCH_MS,       // how long the current press has lasted
    SNAP_KEY,            // last typed code, or 0
    SNAP_KEY_DOWN,       // the code currently held, or 0
    SNAP_TEXTMODE,       // written BY the cart (textmode)
    SNAP_QUIT,           // written BY the cart (quit)
    SNAP_LEN,
};

// The audio queue: [n, (op, a, b, c) * n], int32, at most AQ_MAX a tick.
enum { AQ_SFX = 0, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME };
#define AQ_SLOTS 4
#define AQ_MAX 32

typedef struct {
    moy_console con;
    moy_canvas canvas;
    moy_sheet sheet;
    moy_map map;
    int32_t *snap;              // SNAP_LEN slots, the caller's
    int32_t pmem[256];
    int pmem_dirty;
    int32_t *aq;                // the caller's
    int aq_cap;                 // in int32s
    uint8_t flags[MOY_FLAGS];   // SPEC.md 3.5
    char *cfg;                  // "key\0value\0" pairs, or NULL: every key absent
    int cfg_len;
} moycore_run_t;

extern moycore_run_t *moycore_run_cur;

// A monotonic millisecond clock: the elapsed-inside-this-tick term of time().
uint32_t moycore_run_now_ms(void);
// The tick begins: time() counts from here.
void moycore_run_tick_begin(void);
// A monotonic microsecond clock, for the frame's halves.
uint32_t moycore_run_now_us(void);
// The last frame's two halves, _update and _draw, in microseconds: set by the
// runtime that ran it, read by the Player's logic/render split.
void moycore_run_set_split(uint32_t update_us, uint32_t draw_us);
void moycore_run_split(uint32_t *update_us, uint32_t *draw_us);

// `c` made the run the callbacks read, its console over the caller's snapshot
// and audio queue, with libmoy's host callbacks installed. The canvas is the
// caller's to initialise.
void moycore_run_open(moycore_run_t *c, int32_t *snap, int32_t *aq, int aq_cap);
// A sheet shorter than SPEC.md 3.2's 128x256, or a map shorter than its w*h,
// is declined (NULL): libmoy would read past it on every sprite.
void moycore_run_set_sheet(moycore_run_t *c, uint8_t *pix, size_t nbytes);
void moycore_run_set_map(moycore_run_t *c, uint8_t *cells, size_t nbytes, int w, int h);
// The tile flags, copied; a short blob leaves the rest zero.
void moycore_run_set_flags(moycore_run_t *c, const uint8_t *flags, size_t nbytes);
// The config table, copied: "key\0value\0" pairs. 0, or -1 when it would not
// allocate (every key then reads absent).
int moycore_run_set_cfg(moycore_run_t *c, const char *blob, size_t len);
// A config value's text, or NULL: what the cart's cfg() reads. Read-only, so
// any thread may ask while the run is open.
const char *moycore_run_cfg(const moycore_run_t *c, const char *key);
// The pmem image out (and whether it moved since the last ask), and in.
int moycore_run_pmem_image(moycore_run_t *c, int32_t *out, int n);
void moycore_run_pmem_load(moycore_run_t *c, const int32_t *in, int n);
// What the cart last declared with view(): 1 and its size, or 0.
int moycore_run_view(const moycore_run_t *c, int *w, int *h);
// The config freed; `c` is no longer the current run.
void moycore_run_close(moycore_run_t *c);

// A compiled cart's run opened and closed from C with no VM (modmoycore.c, the
// boards' binding: docs/kernel_cartpath_2026-10.md section 5.2). Every
// pointer is the caller's and outlives the run.
typedef struct {
    uint16_t *fb;                // the canvas, w x h words
    int w, h;
    const uint16_t *wire;        // the palette's 64 panel words, or NULL
    int32_t *snap;               // SNAP_LEN slots
    int32_t *aq;                 // the audio queue, aq_cap int32s
    int aq_cap;
    const int32_t *pmem;         // 256 cells in, or NULL
    const char *cfg;             // "key\0value\0" pairs, or NULL
    size_t cfg_len;
    const char *module;          // the module file to load
    const uint8_t *head;         // main.wasm's head (to its memory section)
    size_t head_len;
    uint32_t pages;              // the manifest's "memory"
    const char *sha;             // main.wasm's sha256 an AOT key must name, or NULL
    const char *dir;             // the cart's folder
    const char *writable;        // the manifest's "writable", NUL-joined, or NULL
    size_t writable_len;
    int swapped, allow_unsigned, interp;
} moycore_open_c_t;

int moycore_wasm_open_c(const moycore_open_c_t *o, char *err, size_t n);
void moycore_close_c(void);

#endif // MOYCORE_RUN_H
