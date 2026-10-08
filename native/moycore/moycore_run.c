// The console a cart runs against (moycore_run.h has the contract).

// The board's clock is the IDF's; every other tier's is POSIX's
// CLOCK_MONOTONIC, which -std=c99 hides unless asked for before any include.
#if defined(__has_include)
#if __has_include("esp_timer.h")
#define MOYCORE_RUN_ESP 1
#endif
#endif
#if !defined(MOYCORE_RUN_ESP) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif

#include <stdlib.h>
#include <string.h>

#ifdef MOYCORE_RUN_ESP
#include "esp_timer.h"
#else
#include <time.h>
#endif

#include "moycore_run.h"

moycore_run_t *moycore_run_cur;

static uint32_t g_tick_ms;

uint32_t moycore_run_now_ms(void) {
#ifdef MOYCORE_RUN_ESP
    return (uint32_t)(esp_timer_get_time() / 1000);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000));
#endif
}

void moycore_run_tick_begin(void) {
    g_tick_ms = moycore_run_now_ms();
}

#define CUR moycore_run_cur

static int h_btn(void *u, moy_button b, int p) {
    (void)u;
    return CUR && CUR->snap ? (CUR->snap[p > 0 ? SNAP_BTN_P1 : SNAP_BTN] >> (int)b) & 1 : 0;
}

static int h_btnp(void *u, moy_button b, int p) {
    (void)u;
    return CUR && CUR->snap ? (CUR->snap[p > 0 ? SNAP_BTNP_P1 : SNAP_BTNP] >> (int)b) & 1 : 0;
}

static int h_players(void *u) {
    (void)u;
    int n = CUR && CUR->snap ? (int)CUR->snap[SNAP_PLAYERS] : 1;
    return n < 1 ? 1 : n;
}

// time(): the snapshot's frame base PLUS the milliseconds elapsed inside this
// tick. Input is frozen for a frame (a cart polling btn() 60 times gets one
// answer), and the base alone froze the clock with it: anything measuring its
// own work inside a frame read zero forever (Bench Lua grows a batch until it
// costs TARGET_MS, so it grew without bound). The host keeps authority over
// the base; C adds only what the host cannot see.
static uint32_t h_time(void *u) {
    (void)u;
    uint32_t base = CUR && CUR->snap ? (uint32_t)CUR->snap[SNAP_TIME_MS] : 0;
    return base + (moycore_run_now_ms() - g_tick_ms);
}

static int32_t h_pget(void *u, int s) {
    (void)u;
    return CUR && s >= 0 && s < 256 ? CUR->pmem[s] : 0;
}

static void h_pset(void *u, int s, int32_t v) {
    (void)u;
    if (CUR && s >= 0 && s < 256 && CUR->pmem[s] != v) {
        CUR->pmem[s] = v;
        CUR->pmem_dirty = 1;
    }
}

static void aq_push(int op, int a, int b, int c) {
    if (!CUR || !CUR->aq || CUR->aq_cap < 1 + AQ_SLOTS) {
        return;
    }
    int n = CUR->aq[0];
    if (n < 0) {
        n = 0;
    }
    if (1 + (n + 1) * AQ_SLOTS > CUR->aq_cap || n >= AQ_MAX) {
        return;
    }
    int32_t *p = CUR->aq + 1 + n * AQ_SLOTS;
    p[0] = op;
    p[1] = a;
    p[2] = b;
    p[3] = c;
    CUR->aq[0] = n + 1;
}

static void h_sfx(void *u, int n, int ch) { (void)u; aq_push(AQ_SFX, n, ch, 0); }
static void h_music(void *u, int t, int l) { (void)u; aq_push(AQ_MUSIC, t, l, 0); }
static void h_mstop(void *u) { (void)u; aq_push(AQ_MUSIC_STOP, 0, 0, 0); }
static void h_sstop(void *u, int ch) { (void)u; aq_push(AQ_SOUND_STOP, ch, 0, 0); }
static void h_vol(void *u, int l) { (void)u; aq_push(AQ_VOLUME, l, 0, 0); }

// Whole hertz and milliseconds, each clamped to 0..32000: a beep's precision
// beyond that is inaudible, and ~32 s is longer than any beep anybody meant.
static void h_beep(void *u, float freq_hz, float dur_s) {
    (void)u;
    int ms = (int)(dur_s * 1000.0f);
    int hz = (int)freq_hz;
    ms = ms < 0 ? 0 : ms > 32000 ? 32000 : ms;
    hz = hz < 0 ? 0 : hz > 32000 ? 32000 : hz;
    aq_push(AQ_BEEP, hz, ms, 0);
}

// The pointer slot is FLAGS, not a level: it is the only one touch() has, and
// it answers three things out of it. 0 is no pointer, which reads as nil.
static int h_touch(void *u, int out[4]) {
    (void)u;
    int st;
    if (!CUR || !CUR->snap || !(st = CUR->snap[SNAP_TOUCH_DOWN])) {
        return 0;
    }
    out[0] = CUR->snap[SNAP_TOUCH_X];
    out[1] = CUR->snap[SNAP_TOUCH_Y];
    out[2] = (st & 4) != 0;     // tapped: the press edge
    out[3] = (st & 2) != 0;     // held
    return 1;
}

static int h_key(void *u, int code) {
    (void)u;
    if (!CUR || !CUR->snap) {
        return 0;
    }
    return code < 0 ? CUR->snap[SNAP_KEY] : CUR->snap[SNAP_KEY_DOWN] == code;
}

static int h_keyp(void *u, int code) {
    (void)u;
    if (!CUR || !CUR->snap) {
        return 0;
    }
    return code < 0 ? CUR->snap[SNAP_KEY] : CUR->snap[SNAP_KEY] == code;
}

static void h_textmode(void *u, int on) {
    (void)u;
    if (CUR && CUR->snap) {
        CUR->snap[SNAP_TEXTMODE] = on ? 1 : 0;
    }
}

static void h_quit(void *u) {
    (void)u;
    if (CUR && CUR->snap) {
        CUR->snap[SNAP_QUIT] = 1;
    }
}

// A config value as its text: a string without its quotes, a number as
// config.json spells it, a boolean as 1 or 0 (lua_ext.cfg_blob writes the
// table). libmoy's cfg() turns a whole-number text back into a number.
const char *moycore_run_cfg(const moycore_run_t *c, const char *k) {
    if (!c || !c->cfg || !k) {
        return NULL;
    }
    const char *p = c->cfg, *end = c->cfg + c->cfg_len;
    while (p < end) {
        const char *v = p + strlen(p) + 1;
        if (v >= end) {
            break;
        }
        if (strcmp(p, k) == 0) {
            return v;
        }
        p = v + strlen(v) + 1;
    }
    return NULL;
}

static const char *h_cfg(void *u, const char *k) {
    (void)u;
    return moycore_run_cfg(CUR, k);
}

void moycore_run_open(moycore_run_t *c, int32_t *snap, int32_t *aq, int aq_cap) {
    moy_host *hs = &c->con.host;
    c->snap = snap;
    c->aq = aq;
    c->aq_cap = aq_cap;
    if (aq && aq_cap > 0) {
        aq[0] = 0;
    }
    c->con.canvas = &c->canvas;
    c->con.flags = c->flags;
    hs->user = NULL;
    hs->btn = h_btn;
    hs->btnp = h_btnp;
    hs->players = h_players;
    hs->time_ms = h_time;
    hs->pmem_get = h_pget;
    hs->pmem_set = h_pset;
    hs->sfx = h_sfx;
    hs->music = h_music;
    hs->beep = h_beep;
    hs->music_stop = h_mstop;
    hs->sound_stop = h_sstop;
    hs->volume = h_vol;
    hs->touch = h_touch;
    hs->key = h_key;
    hs->keyp = h_keyp;
    hs->textmode = h_textmode;
    hs->quit = h_quit;
    hs->cfg = h_cfg;
    CUR = c;
}

void moycore_run_set_sheet(moycore_run_t *c, uint8_t *pix, size_t nbytes) {
    if (pix && nbytes >= (size_t)MOY_SHEET_W * MOY_SHEET_H) {
        moy_sheet_init(&c->sheet, pix);
        c->con.sheet = &c->sheet;
    } else {
        c->con.sheet = NULL;
    }
}

void moycore_run_set_map(moycore_run_t *c, uint8_t *cells, size_t nbytes, int w, int h) {
    if (cells && w > 0 && h > 0 && (size_t)w * (size_t)h <= nbytes) {
        moy_map_init(&c->map, cells, w, h);
        c->con.map = &c->map;
    } else {
        c->con.map = NULL;
    }
}

void moycore_run_set_flags(moycore_run_t *c, const uint8_t *flags, size_t nbytes) {
    if (nbytes > MOY_FLAGS) {
        nbytes = MOY_FLAGS;
    }
    memset(c->flags, 0, sizeof(c->flags));
    if (flags && nbytes > 0) {
        memcpy(c->flags, flags, nbytes);
    }
}

int moycore_run_set_cfg(moycore_run_t *c, const char *blob, size_t len) {
    free(c->cfg);
    c->cfg = NULL;
    c->cfg_len = 0;
    if (!blob || len == 0) {
        return 0;
    }
    c->cfg = (char *)malloc(len + 1);
    if (c->cfg == NULL) {
        return -1;
    }
    memcpy(c->cfg, blob, len);
    c->cfg[len] = 0;
    c->cfg_len = (int)len;
    return 0;
}

int moycore_run_pmem_image(moycore_run_t *c, int32_t *out, int n) {
    if (n > 256) {
        n = 256;
    }
    memcpy(out, c->pmem, (size_t)n * sizeof(int32_t));
    int d = c->pmem_dirty;
    c->pmem_dirty = 0;
    return d;
}

void moycore_run_pmem_load(moycore_run_t *c, const int32_t *in, int n) {
    if (n > 256) {
        n = 256;
    }
    memcpy(c->pmem, in, (size_t)n * sizeof(int32_t));
}

int moycore_run_view(const moycore_run_t *c, int *w, int *h) {
    if (c->con.view_w <= 0) {
        return 0;
    }
    *w = c->con.view_w;
    *h = c->con.view_h;
    return 1;
}

void moycore_run_close(moycore_run_t *c) {
    free(c->cfg);
    c->cfg = NULL;
    c->cfg_len = 0;
    if (CUR == c) {
        CUR = NULL;
    }
}
