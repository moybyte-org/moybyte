// The Player (moy_play.h has the contract): a run's verdict, its frame and
// what it reports.

#include <stdio.h>
#include <string.h>

#include "moy_cat.h"
#include "moy_input.h"
#include "moy_loop.h"
#include "moy_play.h"
#include "moy_rt.h"
#include "moy_tick.h"
#include "moycore_lua.h"
#include "moycore_run.h"

// The calling task's stack, on a board: FreeRTOS's high-water mark.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#define MOY_PLAY_STACK 1
#endif
#endif

uint32_t moy_play_stack_free(void) {
#ifdef MOY_PLAY_STACK
    return (uint32_t)uxTaskGetStackHighWaterMark(NULL) * (uint32_t)sizeof(StackType_t);
#else
    return MOY_PLAY_NO_STACK;
#endif
}

// The audio sessions, where the image takes moy_audio (the Guition S3, with no
// speaker, does not): a run there is silent, its queue dropped.
#if defined(__has_include)
#if __has_include("../moy_audio/moy_aud.h")
#include "../moy_audio/moy_aud.h"
#define MOY_PLAY_AUDIO 1
#endif
#endif

// -- the runtime map's rows ----------------------------------------------------

#ifdef MOY_WITH_LUA
static int lua_open(char *err, size_t n) {
    if (!moycore_RUN.open || moycore_RUN.L == NULL) {
        snprintf(err, n, "no Lua run is open");
        return -1;
    }
    return 0;
}

static const moy_rt_ops_t RT_LUA = { "lua", lua_open, moycore_frame, false };

static int wasm_open(char *err, size_t n) {
    if (!moycore_RUN.open || !moycore_RUN.wasm) {
        snprintf(err, n, "no compiled run is open");
        return -1;
    }
    return 0;
}

static const moy_rt_ops_t RT_WASM = { "wasm", wasm_open, moycore_frame, false };
#endif

static const moy_rt_ops_t RT_PYTHON = { "python", NULL, NULL, true };

void moy_play_rows(bool lua, bool wasm, bool python) {
    moy_rt_remove("lua");
    moy_rt_remove("wasm");
    moy_rt_remove("python");
#ifdef MOY_WITH_LUA
    if (lua) {
        moy_rt_add(&RT_LUA);
    }
    if (wasm) {
        moy_rt_add(&RT_WASM);
    }
#else
    (void)lua;
    (void)wasm;
#endif
    if (python) {
        moy_rt_add(&RT_PYTHON);
    }
}

// -- the run -------------------------------------------------------------------

typedef struct {
    uint32_t h;                     // the live run's handle, 0 with none
    uint32_t last;                  // the last run's, live or ended
    uint32_t gen;
    const moy_rt_ops_t *rt;
    moy_play_info_t info;
    moy_input_t *in;
    uint32_t audio;
    moy_tick_t *tick;
    uint32_t flags;
    uint32_t start_ms;
    int32_t ptr[3];                 // x, y, the touch() flags
    uint32_t upc0[MOY_UPC_CLASSES];
    int view;                       // what the cart had declared last frame
    int view_w, view_h;
} run_t;

// The run's state, allocated at the first launch from the kernel's allocator
// (PSRAM on a board): what the S3s count as internal SRAM is the pointer.
static run_t *g_run;
#define R (*g_run)

static run_t *live(uint32_t run) {
    return g_run != NULL && run != 0u && run == R.h ? g_run : NULL;
}

typedef struct {
    moy_play_info_t *info;
    bool seen;
} launch_ctx_t;

static int entry_seen(void *ctx, const moy_cat_entry_t *e) {
    launch_ctx_t *c = ctx;
    char line[48];
    moy_play_census_line(e, line, sizeof(line));
    char *sp = strchr(line, ' ');
    if (sp != NULL) {
        *sp = 0;
    }
    size_t n = strlen(line);
    if (n >= sizeof(c->info->runtime)) {
        n = sizeof(c->info->runtime) - 1u;
    }
    memcpy(c->info->runtime, line, n);
    c->info->runtime[n] = 0;
    c->info->vm_free = moy_play_vm_free(e, &c->info->why);
    c->seen = true;
    return 0;
}

int moy_play_launch(const char *cart, const char *caller, uint32_t flags, uint32_t *run) {
    (void)caller;
    if (g_run == NULL) {
        g_run = moy_loop_alloc(sizeof(run_t));
        if (g_run == NULL) {
            return MOY_PLAY_NOMEM;
        }
        memset(g_run, 0, sizeof(run_t));
    }
    if (R.h != 0u) {
        moy_play_end(R.h, MOY_PLAY_END_QUIT);
    }
    memset(&R.info, 0, sizeof(R.info));
    launch_ctx_t c = { &R.info, false };
    if (moy_cat_entry(cart, entry_seen, NULL, &c) != 0 || !c.seen) {
        return MOY_PLAY_NOCART;
    }
    const moy_rt_ops_t *rt = moy_rt_get(R.info.runtime);
    if (rt == NULL) {
        return MOY_PLAY_NORT;
    }
    R.gen = R.gen >= 0xFFFFFFu ? 1u : R.gen + 1u;
    R.h = R.last = R.gen << 8 | MOY_PLAY_TAG;
    R.rt = rt;
    R.in = NULL;
    R.audio = 0;
    R.tick = NULL;
    R.flags = flags;
    R.start_ms = moycore_run_now_ms();
    R.ptr[0] = R.ptr[1] = R.ptr[2] = 0;
    R.view = 0;
    R.info.stack_open = R.info.stack_frame = MOY_PLAY_NO_STACK;
    uint32_t frame[MOY_UPC_CLASSES];
    moy_loop_upcalls(frame, R.upc0);
    *run = R.h;
    return MOY_PLAY_OK;
}

int moy_play_bind(uint32_t run, moy_input_t *in, uint32_t audio, moy_tick_t *tick) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    r->in = in;
    r->audio = audio;
    r->tick = tick;
    return MOY_PLAY_OK;
}

int moy_play_open(uint32_t run) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    if (r->rt->vm) {
        return MOY_PLAY_NEEDS_VM;
    }
    if (r->rt->open(r->info.error, sizeof(r->info.error)) != 0) {
        r->info.raised = true;
        return MOY_PLAY_RAISED;
    }
    r->view = moycore_run_view(&moycore_RUN.c, &r->view_w, &r->view_h);
    r->info.stack_open = moy_play_stack_free();
    return MOY_PLAY_OK;
}

int moy_play_input(uint32_t run, const moy_play_in_t *in) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    r->ptr[0] = in->x;
    r->ptr[1] = in->y;
    r->ptr[2] = in->touch;
    return MOY_PLAY_OK;
}

// The snapshot the tick reads (moycore_run.h's slots), from the run's input
// table: the union of every source as player one (libmoy's seven buttons are
// the table's first seven bits), player two's own when more than one slot is
// in play, the last typed key, the pointer the console published and the
// cart's clock.
static void refresh(run_t *r, int32_t *s) {
    uint32_t held = 0, pressed = 0;
    uint8_t slots[MOY_INPUT_SOURCES];
    uint8_t n = 1;
    if (r->in != NULL) {
        moy_input_masks(r->in, MOY_INPUT_UNION, &held, &pressed);
        n = moy_input_players(r->in, slots);
        if (n > 1) {
            uint32_t h1 = 0, p1 = 0;
            moy_input_masks(r->in, 1, &h1, &p1);
            s[SNAP_BTN_P1] = (int32_t)(h1 & MOY_PLAY_BUTTON_MASK);
            s[SNAP_BTNP_P1] = (int32_t)(p1 & MOY_PLAY_BUTTON_MASK);
        }
        s[SNAP_KEY] = moy_input_last_key(r->in);
    }
    s[SNAP_BTN] = (int32_t)(held & MOY_PLAY_BUTTON_MASK);
    s[SNAP_BTNP] = (int32_t)(pressed & MOY_PLAY_BUTTON_MASK);
    s[SNAP_PLAYERS] = n < 1 ? 1 : n;
    s[SNAP_TOUCH_X] = r->ptr[0];
    s[SNAP_TOUCH_Y] = r->ptr[1];
    s[SNAP_TOUCH_DOWN] = r->ptr[2];
    s[SNAP_TOUCH_MS] = 0;
    s[SNAP_TIME_MS] = (int32_t)(moycore_run_now_ms() - r->start_ms);
}

// The tick's audio, in its order, into the run's session. A session that is
// gone answers STALE, and the rest of the queue is dropped with it.
static void drain(run_t *r, int32_t *aq) {
    int n = aq[0];
    aq[0] = 0;
#ifndef MOY_PLAY_AUDIO
    (void)r;
    (void)n;
#else
    if (r->audio == 0u) {
        return;
    }
    for (int i = 0; i < n; i++) {
        const int32_t *q = aq + 1 + i * AQ_SLOTS;
        int a = q[1], b = q[2];
        int rc = 0;
        switch (q[0]) {
            case AQ_SFX: rc = moy_aud_sfx(r->audio, a, b < 0 ? -1 : b); break;
            case AQ_MUSIC: rc = moy_aud_music(r->audio, a, b != 0); break;
            case AQ_BEEP:
                if (a > 0 && b > 0) {
                    rc = moy_aud_beep(r->audio, (float)a, (float)b / 1000.0f);
                }
                break;
            case AQ_MUSIC_STOP: rc = moy_aud_music_stop(r->audio); break;
            case AQ_SOUND_STOP: rc = moy_aud_stop(r->audio, a < 0 ? -1 : a); break;
            case AQ_VOLUME: rc = moy_aud_level(r->audio, a); break;
            default: break;
        }
        if (rc == MOY_AUD_STALE) {
            r->audio = 0;
            return;
        }
    }
#endif
}

int moy_play_frame(uint32_t run, uint8_t ticks, float dt, bool render, uint32_t *out) {
    run_t *r = live(run);
    *out = 0;
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    if (r->info.ended) {
        return MOY_PLAY_ENDED;
    }
    if (r->rt->vm) {
        return MOY_PLAY_NEEDS_VM;
    }
    int32_t *snap = moycore_RUN.c.snap;
    int32_t *aq = moycore_RUN.c.aq;
    for (uint8_t i = 0; i < ticks; i++) {
        if ((r->flags & MOY_PLAY_PACED) && r->in != NULL) {
            moy_input_tick_edges(r->in);
        }
        if (snap != NULL) {
            refresh(r, snap);
        }
        bool draw = render && i + 1u == ticks;
        if (r->rt->frame(dt, draw, r->info.error, sizeof(r->info.error)) != 0) {
            r->info.raised = true;
            return MOY_PLAY_RAISED;
        }
        r->info.ticks++;
        if (r->tick != NULL) {
            uint32_t upd = 0, drw = 0;
            moycore_run_split(&upd, &drw);
            moy_tick_note(r->tick, (moy_tick_real_t)upd / (moy_tick_real_t)1000000);
        }
        if (aq != NULL && aq[0] > 0) {
            drain(r, aq);
        }
        if (snap != NULL && snap[SNAP_QUIT]) {
            snap[SNAP_QUIT] = 0;
            *out |= MOY_PLAY_QUIT;
            break;
        }
    }
    r->info.frames++;
    r->info.stack_frame = moy_play_stack_free();
    int w = 0, h = 0;
    int v = moycore_run_view(&moycore_RUN.c, &w, &h);
    if (v != r->view || (v && (w != r->view_w || h != r->view_h))) {
        r->view = v;
        r->view_w = w;
        r->view_h = h;
        *out |= MOY_PLAY_VIEW;
    }
    return MOY_PLAY_OK;
}

int moy_play_end(uint32_t run, int why) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    r->info.ended = true;
    r->info.end_why = (uint8_t)why;
    uint32_t frame[MOY_UPC_CLASSES], total[MOY_UPC_CLASSES];
    moy_loop_upcalls(frame, total);
    for (int i = 0; i < MOY_UPC_CLASSES; i++) {
        r->info.upcalls[i] = total[i] - r->upc0[i];
    }
    r->h = 0;
    return MOY_PLAY_OK;
}

uint32_t moy_play_current(void) {
    return g_run != NULL ? R.h : 0u;
}

uint32_t moy_play_last(void) {
    return g_run != NULL ? R.last : 0u;
}

int moy_play_info(uint32_t run, moy_play_info_t *out) {
    if (g_run == NULL || run == 0u || run != R.last) {
        return MOY_PLAY_STALE;
    }
    *out = R.info;
    if (!R.info.ended) {
        uint32_t frame[MOY_UPC_CLASSES], total[MOY_UPC_CLASSES];
        moy_loop_upcalls(frame, total);
        for (int i = 0; i < MOY_UPC_CLASSES; i++) {
            out->upcalls[i] = total[i] - R.upc0[i];
        }
    }
    return MOY_PLAY_OK;
}
