/*
 * doomgeneric's platform half for the compiled-cart tier: Doom as a
 * "runtime": "wasm" cart whose imports are the console's own, module "moy"
 * (moy-spec proposals/wasm-runtime.md). build_cart.py stages it with
 * doomgeneric and builds the cart; dg_moy.c is the spike's half, on the spike
 * app's imports.
 *
 * The console keeps the time (time()), the input (btn, touch, key), the WAD
 * (the cart's own read) and the screen (blit, with Doom's 256-entry palette).
 * The pacing is the tick model's: _update runs the game tics time() says are
 * due and returns, _draw renders and blits. Nothing here sleeps or waits, and
 * the cart is silent (-nosound).
 *
 * pmem carries what a test reads back (frames.py reads it; README.md lays it
 * out): the frame CRC at every CRC_EVERY-th gametic, the zone's low-water
 * mark, the heap's headroom, and the text of a fatal I_Error.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "d_loop.h"
#include "doomgeneric.h"
#include "doomkeys.h"
#include "doomstat.h"
#include "f_wipe.h"
#include "i_timer.h"
#include "i_video.h"
#include "m_menu.h"
#include "w_file.h"
#include "z_zone.h"

#define MOY(name) __attribute__((import_module("moy"), import_name(name)))
#define EXPORT(name) __attribute__((export_name(name)))

MOY("blit") void moy_blit(const uint8_t *frame, const uint8_t *pal);
MOY("read") int32_t moy_read(const char *name, int32_t name_len, int32_t offset,
                             void *dst, int32_t len);
MOY("time") int32_t moy_time(void);
MOY("btn") int32_t moy_btn(int32_t b, int32_t player);
MOY("key") int32_t moy_key(int32_t code);
MOY("touch") int32_t moy_touch(int32_t *out);
MOY("cfg") int32_t moy_cfg(const char *key, int32_t key_len, char *dst, int32_t dst_len);
MOY("pmem") int32_t moy_pmem(int32_t slot, int32_t v, int32_t write);
MOY("quit") void moy_quit(void);

/* The canvas the manifest declares, and Doom's 320 x 200 letterboxed in it. */
#define W 320
#define H 240
#define TOP ((H - DOOMGENERIC_RESY) / 2)

/* pmem slots */
#define PM_CRC_LAST 0      /* the last CRC index recorded */
#define PM_CRC 1           /* 1..CRC_SLOTS: the frame CRC at gametic k * CRC_EVERY */
#define CRC_EVERY 500
#define CRC_SLOTS 199
#define PM_ERROR 200       /* 200..231: a fatal I_Error's text, 4 bytes a slot */
#define PM_ERROR_SLOTS 32
#define PM_ZONE_MB 240     /* the zone this run asked for */
#define PM_ZONE_LOW 241    /* the zone's lowest free bytes seen, in KB */
#define PM_HEAP_FREE 242   /* linear memory the heap could still hand out after _init, in KB */
#define PM_GAMETIC 243
#define PM_MAP 244         /* episode * 10 + map */

extern boolean menuactive;
extern int messageToPrint;
extern boolean messageNeedsInput;
extern boolean screenvisible;
extern int fuzzpos;
extern struct color colors[256];

void D_Display(void);
void __wasm_call_ctors(void);

static uint8_t frame[W * H];
static uint8_t pal[768];

pixel_t *DG_ScreenMemory(void)
{
    return frame + TOP * W;
}

/* ---- the host's side of doomgeneric -------------------------------------- */

void DG_Init(void) {}
void DG_DrawFrame(void) {}          /* the frame is already in DG_ScreenBuffer */
void DG_SleepMs(uint32_t ms) { (void)ms; }
void DG_SetWindowTitle(const char *title) { (void)title; }

uint32_t DG_GetTicksMs(void)
{
    return (uint32_t)moy_time();
}

/* Key events for doomgeneric's I_GetEvent, built by _update from the
 * console's input. */
static unsigned short events[64];
static unsigned ev_head, ev_tail;

static void post(int pressed, unsigned char key)
{
    if (ev_tail - ev_head < sizeof(events) / sizeof(events[0]))
        events[ev_tail++ % 64] = (unsigned short)((pressed << 8) | key);
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    if (ev_head == ev_tail)
        return 0;
    unsigned short e = events[ev_head++ % 64];
    *pressed = e >> 8;
    *key = e & 0xff;
    return 1;
}

/* ---- the WAD: the cart's own file, through `read` ------------------------ */

static const char WAD[] = "doom1.wad";

static wad_file_t *W_Cart_OpenFile(char *path);
static void W_Cart_CloseFile(wad_file_t *wad);
static size_t W_Cart_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t len);

static wad_file_class_t cart_wad = { W_Cart_OpenFile, W_Cart_CloseFile, W_Cart_Read };

static wad_file_t *W_Cart_OpenFile(char *path)
{
    if (strcmp(path, WAD) != 0)
        return NULL;
    int32_t size = moy_read(WAD, sizeof(WAD) - 1, 0, 0, 0);
    if (size <= 0)
        return NULL;
    wad_file_t *w = Z_Malloc(sizeof(*w), PU_STATIC, 0);
    w->file_class = &cart_wad;
    w->mapped = NULL;
    w->length = (unsigned int)size;
    return w;
}

static void W_Cart_CloseFile(wad_file_t *wad)
{
    Z_Free(wad);
}

static size_t W_Cart_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t len)
{
    (void)wad;
    return (size_t)moy_read(WAD, sizeof(WAD) - 1, (int32_t)offset, buffer, (int32_t)len);
}

wad_file_t *W_OpenFile(char *path)
{
    return cart_wad.OpenFile(path);
}

void W_CloseFile(wad_file_t *wad)
{
    wad->file_class->CloseFile(wad);
}

size_t W_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t len)
{
    return wad->file_class->Read(wad, offset, buffer, len);
}

/* ---- the wipe, one step a frame ------------------------------------------ */

/* The screen melt. Doom runs it as a loop inside D_Display that waits on the
 * clock while no game tic runs; here D_Display begins it, every later
 * D_Display takes one step for the tics of time() that have passed since the
 * last, and _update runs no game tic until it is done -- the game then catches
 * up, as Doom's own loop does after a wipe. */
static int wiping, wipe_time;

static void wipe_step(int tics)
{
    if (wipe_ScreenWipe(wipe_Melt, 0, 0, SCREENWIDTH, SCREENHEIGHT, tics))
        wiping = 0;
    I_UpdateNoBlit();
    M_Drawer();
    I_FinishUpdate();
}

void DG_WipeBegin(void)
{
    wiping = 1;
    wipe_time = I_GetTime();
    wipe_step(1);
}

int DG_WipeStep(void)
{
    if (!wiping)
        return 0;
    int now = I_GetTime();
    if (now > wipe_time) {
        int tics = now - wipe_time;
        wipe_time = now;
        wipe_step(tics);
    }
    return 1;
}

/* ---- the palette and the frame CRC --------------------------------------- */

static void palette(void)
{
    for (int i = 0; i < 256; i++) {
        pal[3 * i] = colors[i].r;
        pal[3 * i + 1] = colors[i].g;
        pal[3 * i + 2] = colors[i].b;
    }
}

static uint32_t crc32(const uint8_t *p, size_t n, uint32_t crc)
{
    crc = ~crc;
    while (n--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int zone_low_kb = -1;

/* After every game tic (d_loop.c calls it): at each CRC_EVERY-th gametic,
 * render the frame of that tic and record its CRC -- Doom's picture at a
 * gametic is the same on every host, whenever the host happens to draw. */
void DG_AfterTic(void)
{
    if (gametic % 35 == 0) {
        int kb = Z_FreeMemory() / 1024;
        if (zone_low_kb < 0 || kb < zone_low_kb) {
            zone_low_kb = kb;
            moy_pmem(PM_ZONE_LOW, kb, 1);
        }
        moy_pmem(PM_GAMETIC, gametic, 1);
        moy_pmem(PM_MAP, gameepisode * 10 + gamemap, 1);
    }
    if (gametic % CRC_EVERY || gametic / CRC_EVERY > CRC_SLOTS || !screenvisible)
        return;
    /* The spectre's fuzz walks a table one drawn pixel at a time across
     * frames, so its phase is the count of frames drawn before this one --
     * which is the host's cadence, not the game's. This frame starts it at 0. */
    int fuzz = fuzzpos;
    fuzzpos = 0;
    D_Display();
    fuzzpos = fuzz;
    palette();
    uint32_t crc = crc32(I_VideoBuffer, SCREENWIDTH * SCREENHEIGHT, 0);
    crc = crc32(pal, sizeof(pal), crc);
    moy_pmem(PM_CRC + gametic / CRC_EVERY - 1, (int32_t)crc, 1);
    moy_pmem(PM_CRC_LAST, gametic / CRC_EVERY, 1);
}

/* ---- input ---------------------------------------------------------------- */

/* SPEC.md 7.3's buttons, in the import's order. */
enum { B_LEFT, B_RIGHT, B_UP, B_DOWN, B_A, B_B, B_RUN, N_BUTTONS };

/* The key a button presses in play, and in a menu. */
static unsigned char key_for(int b)
{
    switch (b) {
    case B_LEFT: return KEY_LEFTARROW;
    case B_RIGHT: return KEY_RIGHTARROW;
    case B_UP: return KEY_UPARROW;
    case B_DOWN: return KEY_DOWNARROW;
    case B_A:
        if (messageToPrint && messageNeedsInput) return 'y';
        return menuactive ? KEY_ENTER : KEY_FIRE;
    case B_B:
        if (messageToPrint && messageNeedsInput) return 'n';
        return menuactive ? KEY_BACKSPACE : KEY_USE;
    default: return KEY_ESCAPE;
    }
}

/* The touch screen as a 3 x 3 pad over the whole canvas, for boards with
 * no buttons: the top row walks forward (turning at the corners), the middle
 * row turns and fires, the bottom row uses, walks back and opens the menu. */
static unsigned touch_buttons(void)
{
    int32_t t[4];
    if (!moy_touch(t) || !t[3])
        return 0;
    int col = t[0] < W / 4 ? 0 : t[0] >= W - W / 4 ? 2 : 1;
    int row = t[1] < H / 3 ? 0 : t[1] >= H - H / 3 ? 2 : 1;
    static const unsigned PAD[3][3] = {
        { 1u << B_UP | 1u << B_LEFT, 1u << B_UP, 1u << B_UP | 1u << B_RIGHT },
        { 1u << B_LEFT, 1u << B_A, 1u << B_RIGHT },
        { 1u << B_B, 1u << B_DOWN, 1u << B_RUN },
    };
    return PAD[row][col];
}

/* Keyboard extras, where there is a keyboard: strafing on Doom's own keys. */
static const struct { int32_t ascii; unsigned char key; } EXTRA[] = {
    { ',', KEY_STRAFE_L }, { '.', KEY_STRAFE_R },
};

static unsigned char held_key[N_BUTTONS];
static unsigned char held_extra[sizeof(EXTRA) / sizeof(EXTRA[0])];

static void input(void)
{
    unsigned down = touch_buttons();
    for (int b = 0; b < N_BUTTONS; b++)
        if (moy_btn(b, 0))
            down |= 1u << b;
    for (int b = 0; b < N_BUTTONS; b++) {
        int on = (down >> b) & 1;
        if (on && !held_key[b]) {
            held_key[b] = key_for(b);
            post(1, held_key[b]);
        } else if (!on && held_key[b]) {
            post(0, held_key[b]);
            held_key[b] = 0;
        }
    }
    for (unsigned i = 0; i < sizeof(EXTRA) / sizeof(EXTRA[0]); i++) {
        int on = moy_key(EXTRA[i].ascii) != 0;
        if (on != held_extra[i]) {
            post(on, EXTRA[i].key);
            held_extra[i] = (unsigned char)on;
        }
    }
}

/* ---- stdout, stderr and exit, with no WASI ------------------------------- */

/* wasi-libc's stdio and exit reach the host through these, and a compiled
 * cart imports nothing but "moy": stdout and stderr keep their last line for
 * a fatal I_Error, and exit ends the cart -- quit() for a clean exit, a trap
 * for an error, with the error's text in pmem. */
static char err_line[PM_ERROR_SLOTS * 4], err_last[PM_ERROR_SLOTS * 4];
static unsigned err_n, err_last_n;

typedef struct { uint32_t buf, len; } iovec_t;

int32_t __imported_wasi_snapshot_preview1_fd_write(int32_t fd, int32_t iovs, int32_t n,
                                                    int32_t nwritten)
{
    const iovec_t *v = (const iovec_t *)(uintptr_t)iovs;
    uint32_t total = 0;
    for (int32_t i = 0; i < n; i++) {
        const char *p = (const char *)(uintptr_t)v[i].buf;
        for (uint32_t j = 0; fd == 2 && j < v[i].len; j++) {
            if (p[j] != '\n') {
                if (err_n < sizeof(err_line))
                    err_line[err_n++] = p[j];
            } else if (err_n) {
                memcpy(err_last, err_line, err_n);
                err_last_n = err_n;
                err_n = 0;
            }
        }
        total += v[i].len;
    }
    *(uint32_t *)(uintptr_t)nwritten = total;
    return 0;
}

/* The last line written to stderr -- I_Error's message -- into pmem. */
static void report_error(void)
{
    const char *t = err_n ? err_line : err_last;
    unsigned n = err_n ? err_n : err_last_n;
    for (int s = 0; s < PM_ERROR_SLOTS; s++) {
        int32_t word = 0;
        for (int b = 0; b < 4; b++) {
            unsigned at = (unsigned)s * 4 + (unsigned)b;
            word |= (int32_t)(at < n ? (uint8_t)t[at] : 0) << (8 * b);
        }
        moy_pmem(PM_ERROR + s, word, 1);
    }
}

_Noreturn void __imported_wasi_snapshot_preview1_proc_exit(int32_t code)
{
    if (code == 0)
        moy_quit();
    report_error();
    __builtin_trap();
}

/* i_system.c shells out to show a crash on a desktop; here there is no shell. */
int system(const char *command)
{
    (void)command;
    return -1;
}

/* No files but the WAD, no environment, no clock but time(). */
#define WASI_EBADF 8
#define WASI_ENOENT 44
#define WASI_ENOSYS 52

int32_t __imported_wasi_snapshot_preview1_fd_close(int32_t fd) { (void)fd; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_fd_seek(int32_t fd, int64_t off, int32_t whence,
                                                   int32_t out)
{ (void)fd; (void)off; (void)whence; (void)out; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_fd_read(int32_t fd, int32_t iovs, int32_t n,
                                                   int32_t nread)
{ (void)fd; (void)iovs; (void)n; *(uint32_t *)(uintptr_t)nread = 0; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_fd_fdstat_get(int32_t fd, int32_t out)
{ (void)fd; (void)out; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_fd_prestat_get(int32_t fd, int32_t out)
{ (void)fd; (void)out; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_fd_prestat_dir_name(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_path_open(int32_t fd, int32_t df, int32_t path,
                                                     int32_t plen, int32_t of, int64_t rb,
                                                     int64_t ri, int32_t ff, int32_t out)
{ (void)fd; (void)df; (void)path; (void)plen; (void)of; (void)rb; (void)ri; (void)ff; (void)out;
  return WASI_ENOENT; }
int32_t __imported_wasi_snapshot_preview1_fd_fdstat_set_flags(int32_t fd, int32_t flags)
{ (void)fd; (void)flags; return WASI_EBADF; }
int32_t __imported_wasi_snapshot_preview1_path_create_directory(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return WASI_ENOSYS; }
int32_t __imported_wasi_snapshot_preview1_path_remove_directory(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return WASI_ENOSYS; }
int32_t __imported_wasi_snapshot_preview1_path_unlink_file(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return WASI_ENOSYS; }
int32_t __imported_wasi_snapshot_preview1_path_rename(int32_t fd, int32_t p, int32_t n,
                                                       int32_t nfd, int32_t np, int32_t nn)
{ (void)fd; (void)p; (void)n; (void)nfd; (void)np; (void)nn; return WASI_ENOSYS; }
int32_t __imported_wasi_snapshot_preview1_environ_sizes_get(int32_t count, int32_t size)
{ *(uint32_t *)(uintptr_t)count = 0; *(uint32_t *)(uintptr_t)size = 0; return 0; }
int32_t __imported_wasi_snapshot_preview1_environ_get(int32_t env, int32_t buf)
{ (void)env; (void)buf; return 0; }
int32_t __imported_wasi_snapshot_preview1_clock_time_get(int32_t id, int64_t precision,
                                                          int32_t out)
{
    (void)id; (void)precision;
    *(uint64_t *)(uintptr_t)out = (uint64_t)(uint32_t)moy_time() * 1000000ull;
    return 0;
}

/* ---- the hooks ------------------------------------------------------------ */

static char cfg_zone[8] = "4";
static char cfg_args[96];
static char *argv[24];

static int cfg(const char *key, char *dst, int cap)
{
    int32_t n = moy_cfg(key, (int32_t)strlen(key), dst, cap - 1);
    if (n < 0 || n >= cap)
        return 0;
    dst[n] = 0;
    return 1;
}

/* Called through volatile pointers: a malloc whose block is freed unused is
 * one the compiler may assume succeeded, and the probe would read nothing. */
static void *(*volatile probe_alloc)(size_t) = malloc;
static void (*volatile probe_free)(void *) = free;

static int32_t heap_headroom_kb(void)
{
    /* The largest block the heap still hands out, to 16 KB. */
    uint32_t lo = 0, hi = 64u << 20;
    while (hi - lo > 16384) {
        uint32_t mid = lo + (hi - lo) / 2;
        void *p = probe_alloc(mid);
        if (p) {
            probe_free(p);
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return (int32_t)(lo / 1024);
}

EXPORT("_init") void cart_init(void)
{
    __wasm_call_ctors();
    int argc = 0;
    argv[argc++] = "doom";
    argv[argc++] = "-iwad";
    argv[argc++] = (char *)WAD;
    argv[argc++] = "-nosound";
    cfg("zone", cfg_zone, sizeof(cfg_zone));
    argv[argc++] = "-mb";
    argv[argc++] = cfg_zone;
    /* "args" in config.json: more of Doom's own flags, e.g. "-warp 1 3". */
    if (cfg("args", cfg_args, sizeof(cfg_args))) {
        for (char *p = strtok(cfg_args, " "); p && argc < 23; p = strtok(NULL, " "))
            argv[argc++] = p;
    }
    argv[argc] = NULL;
    for (int s = 0; s <= PM_MAP; s++)
        moy_pmem(s, 0, 1);
    moy_pmem(PM_ZONE_MB, atoi(cfg_zone), 1);
    doomgeneric_Create(argc, argv);
    moy_pmem(PM_HEAP_FREE, heap_headroom_kb(), 1);
}

EXPORT("_update") void cart_update(float dt)
{
    (void)dt;
    input();
    if (!wiping)
        TryRunTics();
}

EXPORT("_draw") void cart_draw(void)
{
    if (screenvisible)
        D_Display();
    palette();
    moy_blit(frame, pal);
}
