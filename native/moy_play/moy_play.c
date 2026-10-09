// The Player (moy_play.h has the contract): a run's verdict, its frame and
// what it reports.

#include <stdio.h>
#include <string.h>

#include "moy_cat.h"
#include "moy_crash.h"
#include "moy_input.h"
#include "moy_json.h"
#include "moy_match.h"
#include "moy_chrome.h"
#include "moy_loop.h"
#include "moy_play.h"
#include "moy_rt.h"
#include "moy_tick.h"
#include "moycore_lua.h"
#include "moycore_run.h"
#include "moy_buf.h"

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

// The crash record's OPEN id (moy_crash.h's GAME role): the board's kernel
// keeps it; weak, so an image or a host without one arms nothing.
void moy_kernel_arm(int role, const char *id) __attribute__((weak));

static void arm(const char *id) {
    if (moy_kernel_arm != NULL) {
        moy_kernel_arm(MOY_ROLE_GAME, id);
    }
}

// The audio sessions, where the image takes moy_audio (the Guition S3, with no
// speaker, does not): a run there is silent, its queue dropped. The host's
// build (runtime/moycore.py) has them, its sources side by side.
#if defined(MOY_PLAY_HOST)
#include "moy_aud.h"
#define MOY_PLAY_AUDIO 1
#elif defined(__has_include)
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
    uint32_t owner;                 // the run's OWNER row (moy_buf.h), or 0
    int view;                       // what the cart had declared last frame
    int view_w, view_h;
    bool armed;                     // the crash record names this run
    bool reseed;                    // a lockstep frame starts: seed libmoy first
    bool lock_any;                  // the match's last send: lock_at is set
    uint32_t lock_at;
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
    c->info->game = moy_play_is_game(e);
    c->info->title[0] = 0;
    if (e->title.v != NULL && *e->title.v == '"') {
        size_t tn = moy_json_strlen(e->title.v, e->title.e);
        if (tn < sizeof(c->info->title)) {
            moy_json_str(e->title.v, e->title.e, c->info->title);
            c->info->title[tn] = 0;
        }
    }
    // The id the crash record names: the folder's stem (".moy" dropped).
    size_t fn = e->folder_n;
    if (fn > 4 && memcmp(e->folder + fn - 4, ".moy", 4) == 0) {
        fn -= 4;
    }
    if (fn >= sizeof(c->info->id)) {
        fn = sizeof(c->info->id) - 1u;
    }
    memcpy(c->info->id, e->folder, fn);
    c->info->id[fn] = 0;
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
    R.reseed = false;
    R.lock_any = false;
    R.info.stack_open = R.info.stack_frame = MOY_PLAY_NO_STACK;
    // Armed before the runtime loads: a fault in the load names the cart.
    R.armed = R.info.game;
    if (R.armed) {
        arm(R.info.id);
    }
    // The run's lifetime in the glass: what its runtime loads -- a Lua run's
    // layer and image pixels -- is on loan to it and goes with it.
    R.owner = 0;
    if (moy_glass_ready() && moy_owner_new(&R.owner, "run", MOY_CLASS_CART) != MOY_GLASS_OK) {
        R.owner = 0;
    }
    moycore_lua_owner(R.owner);
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
    moy_match_t *m = moy_match_kernel();
    if (m != NULL && m->s.live) {
        // A match: the session's two global players, as its last advance
        // applied them -- the same truth on both consoles.
        s[SNAP_BTN] = m->s.held[0] & MOY_PLAY_BUTTON_MASK;
        s[SNAP_BTNP] = m->s.pressed[0] & MOY_PLAY_BUTTON_MASK;
        s[SNAP_BTN_P1] = m->s.held[1] & MOY_PLAY_BUTTON_MASK;
        s[SNAP_BTNP_P1] = m->s.pressed[1] & MOY_PLAY_BUTTON_MASK;
        s[SNAP_PLAYERS] = 2;
        s[SNAP_KEY] = 0;
        s[SNAP_TOUCH_X] = s[SNAP_TOUCH_Y] = s[SNAP_TOUCH_DOWN] = s[SNAP_TOUCH_MS] = 0;
        s[SNAP_TIME_MS] = (int32_t)(moycore_run_now_ms() - r->start_ms);
        return;
    }
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
    if (r->info.raised) {
        return MOY_PLAY_RAISED;         // its runtime is broken: report, never rerun
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
        if (r->reseed) {
            moy_match_t *mm = moy_match_kernel();
            if (mm != NULL && mm->s.live) {
                moy_srand(&moycore_RUN.c.con, mm->s.frame_seed);
            }
            r->reseed = false;
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
    if (r->armed) {
        arm("");                        // the board survived the run
        r->armed = false;
    }
    if (r->owner != 0u) {
        moy_owner_end(r->owner);        // any loan the runtime's close left
        r->owner = 0;
    }
    moycore_lua_owner(0);
    return MOY_PLAY_OK;
}

bool moy_play_lock_seed(uint32_t *seed) {
    moy_match_t *m = moy_match_kernel();
    if (m == NULL || !m->s.live) {
        return false;
    }
    *seed = m->s.frame_seed ? m->s.frame_seed : 1u;
    return true;
}

bool moy_play_lockstep(uint32_t run, uint32_t now, uint8_t *ticks) {
    *ticks = 0;
    run_t *r = live(run);
    moy_match_t *m = moy_match_kernel();
    if (r == NULL || m == NULL || !m->s.live) {
        return false;
    }
    // The radio drained BEFORE the tick looks for the peer's input: the
    // tail's drain left it up to a frame stale (8.0% -> 6.4% stalled ticks
    // at DELAY=2 from this alone, on glass 2026-08-24).
    moy_match_drain_input(m, now, MOY_MATCH_DRAIN_MAX);
    // Every advance and every resend is a broadcast. A due tick always runs;
    // a stalled tick's retry and the resend between ticks wait out
    // MOY_PLAY_LOCK_MS since the last send, the cadence of a console's loop
    // frame. The kernel's front loops far faster: a send each pass flooded
    // the air (T-Deck and P4, 2026-10-09: 17 ticks/s, 47% of ticks stalled),
    // and a 4 ms retry did the same.
    bool due = moy_lockstep_due(m, now);
    if (!due && r->lock_any && now - r->lock_at < MOY_PLAY_LOCK_MS) {
        return true;
    }
    r->lock_any = true;
    r->lock_at = now;
    if (due || m->s.waiting) {
        // A stalled tick retries every loop frame: the missing input usually
        // lands a few ms after it was first needed.
        uint32_t held = 0, pressed = 0;
        if (r->in != NULL) {
            moy_input_masks(r->in, MOY_INPUT_UNION, &held, &pressed);
        }
        if (moy_lockstep_advance(m, (uint8_t)(held & MOY_PLAY_BUTTON_MASK), now, true) == 1) {
            *ticks = 1;
            r->reseed = true;
        }
    } else {
        // Between ticks: no simulation, but the newest input goes out again
        // (the radio's ack lies; more copies serve a stalled peer sooner).
        moy_lockstep_resend(m);
    }
    return true;
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

// -- the run in front ------------------------------------------------------------

static struct {
    const moy_front_ops_t *ops;
    uint32_t run;
    bool live;
    bool holding;
    uint32_t since;
    moy_chrome_list_t *overlay;
} F;

void moy_play_front_ops(const moy_front_ops_t *ops) {
    F.ops = ops;
}

bool moy_play_front_live(void) {
    return F.live && live(F.run) != NULL;
}

int moy_play_front(uint32_t run) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    // A Lua run: its frame is the console's canvas. A compiled cart's frame
    // is its own memory, which the console's present composes (CartFrame).
    if (F.ops == NULL || r->rt->vm || r->in == NULL || r->view || r->info.raised
            || strcmp(r->rt->name, "lua") != 0 || moycore_RUN.wasm) {
        return MOY_PLAY_NORT;
    }
    int w = 0, h = 0;
    if (F.ops->canvas(F.ops->ctx, &w, &h) == NULL || w != moycore_RUN.c.canvas.w
            || h != moycore_RUN.c.canvas.h) {
        return MOY_PLAY_NORT;           // a canvas the present does not compose
    }
    if (F.overlay == NULL) {
        F.overlay = moy_loop_alloc(sizeof(moy_chrome_list_t));
        if (F.overlay == NULL) {
            return MOY_PLAY_NOMEM;
        }
    }
    F.run = run;
    F.live = true;
    F.holding = false;
    return MOY_PLAY_OK;
}

static int front_ended(int why) {
    moy_play_end(F.run, why);
    F.live = false;
    return -1;
}

bool moy_play_front_end(int why) {
    if (!moy_play_front_live()) {
        return false;
    }
    front_ended(why);
    return true;
}

static int front_back(void) {
    F.live = false;                     // the console drives the run from here
    return -1;
}

int moy_play_front_frame(uint32_t now, uint32_t dt_us) {
    run_t *r = live(F.run);
    if (!F.live || r == NULL) {
        F.live = false;
        return -1;
    }
    // A Ctrl-C ends the run; the console's next upcall raises it.
    if (moy_play_interrupt_pending != NULL && moy_play_interrupt_pending()) {
        return front_ended(MOY_PLAY_END_SERIAL);
    }
    // The link asks the console for a cart: the console's frame opens it.
    moy_match_t *m = moy_match_kernel();
    if (m != NULL && m->act != MOY_MATCH_ACT_NONE) {
        return front_back();
    }
    // The hold-to-exit gesture: home held MOY_PLAY_HOLD_MS. A text-mode cart
    // never sees home asserted (BACKSPACE is a typed byte there).
    uint32_t held = 0, pressed = 0;
    moy_input_masks(r->in, MOY_INPUT_UNION, &held, &pressed);
    uint32_t held_ms = 0;
    if (held & (1u << MOY_PLAY_HOME_BIT)) {
        if (!F.holding) {
            F.holding = true;
            F.since = now;
        }
        held_ms = now - F.since;
        if (held_ms >= MOY_PLAY_HOLD_MS) {
            return front_ended(MOY_PLAY_END_HOLD);
        }
        if (held_ms == 0) {
            held_ms = 1;                // the pill shows from the first held frame
        }
    } else {
        F.holding = false;
    }
    // The pointer, in the run's canvas.
    moy_input_sample_t smp;
    moy_input_sample(r->in, &smp);
    if (smp.any && F.ops->map != NULL) {
        int32_t x = smp.x, y = smp.y;
        bool in = F.ops->map(F.ops->ctx, &x, &y);
        r->ptr[0] = x;
        r->ptr[1] = y;
        r->ptr[2] = (in && smp.down ? 3 : 0) | (in && smp.edge ? 4 : 0);
    }
    // The ticks: the match's clock, the tick model's plan, or the loop's.
    float dt = (float)dt_us / 1000000.0f;
    uint8_t ticks = 1;
    bool render = true;
    if (moy_play_lockstep(F.run, now, &ticks)) {
        dt = 1.0f / (float)MOY_MATCH_TICK_HZ;
        // A matched game draws the frames it simulates and no others: a draw
        // between ticks, or over a stalled one, is a present the stall's
        // retry waits behind (the console's path draws on the due tick).
        render = ticks > 0;
    } else if ((r->flags & MOY_PLAY_PACED) && r->tick != NULL && r->tick->rate > 0) {
        render = moy_tick_plan(r->tick, (moy_tick_real_t)dt, &ticks);
        if (ticks == 0) {
            moy_input_keep_edges(r->in);
        }
        dt = 1.0f / (float)r->tick->rate;
    } else {
        ticks = 1;                      // unpaced: one tick a loop frame
        if (dt > MOY_PLAY_FREE_DT) {
            dt = MOY_PLAY_FREE_DT;      // a stall slows time, never jumps it
        }
    }
    int w = 0, h = 0;
    uint16_t *px = F.ops->canvas(F.ops->ctx, &w, &h);
    if (px == NULL || w != moycore_RUN.c.canvas.w || h != moycore_RUN.c.canvas.h) {
        return front_back();
    }
    moycore_RUN.c.canvas.pix = (moy_pixel *)px;
    uint32_t out = 0;
    int rc = moy_play_frame(F.run, ticks, dt, render, &out);
    if (rc == MOY_PLAY_RAISED) {
        return front_back();            // the console's next frame reports it
    }
    if (rc != MOY_PLAY_OK) {
        return front_back();
    }
    if (out & MOY_PLAY_QUIT) {
        return front_ended(MOY_PLAY_END_QUIT);
    }
    if (out & MOY_PLAY_VIEW) {
        return front_back();            // a view the console composes
    }
    if (render) {
        moy_chrome_list_t *l = F.overlay;
        if (moy_chrome_overlay(l, now, w, h, w, 1, 18, held_ms, MOY_PLAY_HOLD_MS)) {
            moy_chrome_raster(l, px, w, h, moycore_RUN.c.canvas.wire, 1);
        }
    }
    F.ops->present(F.ops->ctx, render);
    return render ? 1 : 0;
}

// -- the serial words' Player half -------------------------------------------------

static int jstr(char *o, size_t n, const char *v) {
    char q[128];
    size_t k = moy_json_quote(v, strlen(v), q, sizeof(q));
    if (k == 0 || k >= sizeof(q)) {
        return snprintf(o, n, "null");
    }
    q[k] = 0;
    return snprintf(o, n, "%s", q);
}

#define PUT(...) do { int _k = snprintf(o + at, at < n ? n - at : 0, __VA_ARGS__); \
                      if (_k > 0) at += (size_t)_k; } while (0)
#define PUTS(v) do { int _k = jstr(o + at, at < n ? n - at : 0, (v)); \
                     if (_k > 0) at += (size_t)_k; } while (0)

static void ups(char *o, size_t n, size_t *pat, const uint32_t *u) {
    size_t at = *pat;
    PUT("[%u, %u, %u, %u, %u]", (unsigned)u[0], (unsigned)u[1], (unsigned)u[2],
        (unsigned)u[3], (unsigned)u[4]);
    *pat = at;
}

size_t moy_play_state_json(char *o, size_t n) {
    size_t at = 0;
    if (!moy_play_front_live()) {
        PUT("null");
        return at;
    }
    const moy_play_info_t *i = &R.info;
    uint32_t frame[MOY_UPC_CLASSES], total[MOY_UPC_CLASSES], since[MOY_UPC_CLASSES];
    moy_loop_upcalls(frame, total);
    for (int k = 0; k < MOY_UPC_CLASSES; k++) {
        since[k] = total[k] - R.upc0[k];
    }
    PUT("{\"screen\": \"desktop\", \"front\": true, \"vm\": true, \"cart\": ");
    PUTS(i->title);
    PUT(", \"cart_error\": null, \"notice\": null, \"frames\": %u, \"run\": "
        "{\"runtime\": ", (unsigned)i->frames);
    PUTS(i->runtime);
    PUT(", \"vm_free\": %s, \"why\": \"%s\"}, \"play\": {\"runtime\": ",
        i->vm_free ? "true" : "false", moy_play_why_name(i->why));
    PUTS(i->runtime);
    PUT(", \"frames\": %u, \"ticks\": %u, \"upcalls\": ", (unsigned)i->frames,
        (unsigned)i->ticks);
    ups(o, n, &at, since);
    if (i->stack_open == MOY_PLAY_NO_STACK) {
        PUT(", \"stack\": [null, null, null]}");
    } else {
        PUT(", \"stack\": [null, %u, %u]}", (unsigned)i->stack_open,
            (unsigned)i->stack_frame);
    }
    PUT(", \"upcalls\": ");
    ups(o, n, &at, frame);
    PUT(", \"upcall_totals\": ");
    ups(o, n, &at, total);
    if ((R.flags & MOY_PLAY_PACED) && R.tick != NULL && R.tick->rate > 0) {
        PUT(", \"tick\": [%d, %u, %u, %s]", R.tick->rate, (unsigned)R.tick->div,
            (unsigned)R.tick->misses, R.tick->steady ? "true" : "false");
    } else {
        PUT(", \"tick\": null");
    }
    moy_match_t *m = moy_match_kernel();
    if (m == NULL) {
        PUT(", \"link\": null}");
    } else if (m->s.live) {
        // `lockstep`: the frame, the distinct ticks that stalled (the stall
        // rate #65 holds is this over the frame), every stalled advance, the delay.
        PUT(", \"link\": [%s, %d, true, %ld], \"lockstep\": [%ld, %u, %u, %d]}",
            m->active ? "true" : "false", moy_match_peer_count(m, 0), (long)m->s.frame,
            (long)m->s.frame, (unsigned)m->s.stall_ticks, (unsigned)m->s.stalls,
            m->s.delay);
    } else {
        PUT(", \"link\": [%s, %d, false, null]}", m->active ? "true" : "false",
            moy_match_peer_count(m, 0));
    }
    return at;
}
