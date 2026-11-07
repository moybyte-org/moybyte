// The Player (moy_play.h has the contract): a run's verdict, its frame and
// what it reports.

// nanosleep and the pthread watch under the host's -std=c99 (a board's IDF
// declares them itself).
#if !defined(ESP_PLATFORM) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif

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

_Static_assert(MOY_PLAY_UPC == MOY_UPC_CLASSES, "a run's books carry every crossing class");

// The calling task's stack, on a board: FreeRTOS's high-water mark.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#define MOY_PLAY_STACK 1
#endif
#endif

// -- the runaway watch (moy_play.h) ----------------------------------------------
//
// The tick's start and budget, written by the run's task around each tick
// and read by the watcher's, under one lock, so a fire can never land on a
// tick after the one that overran: the run's task takes the lock to close
// the tick, and from then the watcher sees no tick.

static struct {
    volatile uint32_t since;        // when the live tick began (ms, never 0); 0: none
    volatile uint32_t budget;       // its budget, ms
    volatile bool fired;            // the watcher ended it
    uint32_t override;              // moy_play_watch_budget's, 0: the slots' rule
    const char *what;               // the stuck words the live tick's run ends with
    bool on;                        // the watcher polls
} W;

#if defined(MOY_PLAY_STACK)
#include "esp_timer.h"
#include "freertos/semphr.h"
static SemaphoreHandle_t s_watch_lock;
static esp_timer_handle_t s_watch_timer;
static void watch_lock(void) {
    if (s_watch_lock != NULL) {
        xSemaphoreTake(s_watch_lock, portMAX_DELAY);
    }
}
static void watch_unlock(void) {
    if (s_watch_lock != NULL) {
        xSemaphoreGive(s_watch_lock);
    }
}
static void watch_cb(void *arg) {
    (void)arg;
    moy_play_watch(moycore_run_now_ms());
}
// Made once, before the VM's first area on a board (moy_play_reserve).
static void watch_make(void) {
    if (s_watch_lock == NULL) {
        s_watch_lock = xSemaphoreCreateMutex();
    }
    if (s_watch_timer == NULL) {
        const esp_timer_create_args_t a = { .callback = watch_cb, .name = "moy_watch" };
        if (esp_timer_create(&a, &s_watch_timer) != ESP_OK) {
            s_watch_timer = NULL;
        }
    }
}
static void watch_run(bool on) {
    watch_make();
    if (s_watch_timer == NULL || W.on == on) {
        return;
    }
    W.on = on;
    if (on) {
        esp_timer_start_periodic(s_watch_timer, MOY_PLAY_WATCH_POLL_MS * 1000u);
    } else {
        esp_timer_stop(s_watch_timer);
    }
}
#elif !defined(__EMSCRIPTEN__)
#include <pthread.h>
#include <time.h>
static pthread_mutex_t s_watch_mu = PTHREAD_MUTEX_INITIALIZER;
static bool s_watch_thread;
static void watch_lock(void) {
    pthread_mutex_lock(&s_watch_mu);
}
static void watch_unlock(void) {
    pthread_mutex_unlock(&s_watch_mu);
}
static void *watch_main(void *arg) {
    (void)arg;
    const struct timespec poll = { 0, (long)MOY_PLAY_WATCH_POLL_MS * 1000000L };
    for (;;) {
        nanosleep(&poll, NULL);
        if (W.on) {
            moy_play_watch(moycore_run_now_ms());
        }
    }
    return NULL;
}
static void watch_make(void) {
}
static void watch_run(bool on) {
    if (on && !s_watch_thread) {
        pthread_t t;
        if (pthread_create(&t, NULL, watch_main, NULL) == 0) {
            pthread_detach(t);
            s_watch_thread = true;
        }
    }
    W.on = on;
}
#else
// The browser's page runs one thread: nothing can watch a tick that never
// returns, and the page's own "not responding" is the floor.
static void watch_lock(void) {
}
static void watch_unlock(void) {
}
static void watch_make(void) {
}
static void watch_run(bool on) {
    W.on = on;
}
#endif

bool moy_play_watch(uint32_t now_ms) {
    bool fire = false;
    watch_lock();
    if (W.since != 0u && !W.fired && now_ms - W.since >= W.budget) {
        W.fired = fire = true;
        moycore_stuck(W.what != NULL ? W.what : "stuck");
    }
    watch_unlock();
    return fire;
}

void moy_play_watch_budget(uint32_t ms) {
    W.override = ms;
}

// The budget a run's ticks get: MOY_PLAY_STUCK_SLOTS of its pacing slot
// (a paced run's rate, else the console's 30), within the floor and ceiling.
static uint32_t watch_budget(const moy_tick_t *t) {
    if (W.override != 0u) {
        return W.override;
    }
    uint32_t rate = t != NULL && t->rate > 0 ? (uint32_t)t->rate : 30u;
    uint32_t ms = MOY_PLAY_STUCK_SLOTS * 1000u / rate;
    return ms < MOY_PLAY_STUCK_MIN_MS ? MOY_PLAY_STUCK_MIN_MS
         : ms > MOY_PLAY_STUCK_MAX_MS ? MOY_PLAY_STUCK_MAX_MS : ms;
}

static void watch_begin(uint32_t budget, const char *what) {
    uint32_t now = moycore_run_now_ms();
    watch_lock();
    W.budget = budget;
    W.what = what;
    W.fired = false;
    W.since = now != 0u ? now : 1u;
    watch_unlock();
}

// The tick is over: whether the watcher ended it.
static bool watch_end(void) {
    watch_lock();
    W.since = 0u;
    bool fired = W.fired;
    W.fired = false;
    watch_unlock();
    return fired;
}

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
// build (runtime/host/moycore.py) has them, its sources side by side.
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
    uint32_t last;                  // the row's last run's, live or ended
    const moy_rt_ops_t *rt;
    moy_play_info_t info;
    moy_input_t *in;
    uint32_t audio;
    moy_tick_t *tick;
    uint32_t flags;
    uint32_t start_ms;
    int32_t ptr[3];                 // x, y, the touch() flags
    int32_t window;                 // the console's: the run lies in a window
    uint32_t upc0[MOY_UPC_CLASSES];
    uint32_t owner;                 // the run's OWNER row (moy_buf.h), or 0
    moycore_run *mc;                // its runtime's row (moycore_lua.h), from its open
    int view;                       // what the cart had declared last frame
    int view_w, view_h;
    uint32_t budget;                // the runaway watch's, for each tick (ms)
    char what[96];                  // the stuck words its ticks end with
    bool muted;                     // held aside: its snapshot reads no input
    uint32_t gen;                   // its launch's number: the oldest is ended for a new one
    bool armed;                     // the crash record names this run
    bool reseed;                    // a lockstep frame starts: seed libmoy first
    char path[192];                 // the cart's folder
} run_t;

// THE RUNS ARE ROWS: MOY_PLAY_RUNS of them, each a run's state, allocated
// at the first launch from the kernel's allocator (PSRAM on a board): what the
// S3s count as internal SRAM is the pointer. A handle is the launch's number,
// the row and the tag (gen << 10 | slot << 8 | MOY_PLAY_TAG). `cur` is the
// row launched last, which the stop's path and flags, `current` and the
// front's state read; the crash record names the row that ticked last
// (`armed`).
typedef struct {
    run_t row[MOY_PLAY_RUNS];
    uint32_t gen;                   // the last launch's number
    uint32_t last;                  // the last run launched, live or ended
    uint8_t cur;
    int8_t armed;                   // the row the crash record names, -1: none
} runs_t;

static runs_t *g_runs;
#define R (g_runs->row[g_runs->cur])

static run_t *live(uint32_t run) {
    if (g_runs == NULL || run == 0u) {
        return NULL;
    }
    run_t *r = &g_runs->row[(run >> 8) & (MOY_PLAY_RUNS - 1u)];
    return run == r->h ? r : NULL;
}

static bool runs_make(void *(*alloc)(size_t n)) {
    if (g_runs == NULL) {
        g_runs = alloc(sizeof(runs_t));
        if (g_runs == NULL) {
            return false;
        }
        memset(g_runs, 0, sizeof(runs_t));
        g_runs->armed = -1;
    }
    return true;
}

static bool any_live(void) {
    for (uint32_t i = 0; i < MOY_PLAY_RUNS; i++) {
        if (g_runs->row[i].h != 0u) {
            return true;
        }
    }
    return false;
}

// The crash record names `r` (its id, the GAME role): armed when it ticks
// and it was not the one named.
static void arm_row(run_t *r) {
    int8_t slot = (int8_t)(r - g_runs->row);
    if (r->armed && g_runs->armed != slot) {
        arm(r->info.id);
        g_runs->armed = slot;
    }
}

typedef struct {
    moy_play_info_t *info;
    bool seen;
    const char *path;
    uint32_t flags;
} launch_ctx_t;

static const char *const STOP_NAMES[MOY_PLAY_STOP_WHYS] = {
    "stops", "rule", "lever", "fits", "place", "lease", "ota", "front", "big",
};

const char *moy_play_stop_name(uint8_t why) {
    return why < MOY_PLAY_STOP_WHYS ? STOP_NAMES[why] : "?";
}

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
    c->info->vm_free = moy_play_vm_free_at(c->path, e, &c->info->why, NULL, 0);
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
    // Whether the VM stops for it: decided here, from the entry, before
    // anything of the cart loads (section 5.2's step 1).
    // A VM-free run is a game's or a compiled app's (rule 2).
    if (!c->info->vm_free) {
        c->info->stop_why = MOY_PLAY_KEEP_RULE;
    } else if (moy_play_stop_verdict == NULL) {
        c->info->stop_why = MOY_PLAY_KEEP_LEVER;
    } else {
        c->info->stop_why = moy_play_stop_verdict(e, c->path, c->flags, c->info->fit);
    }
    c->seen = true;
    return 0;
}

int moy_play_launch(const char *cart, const char *caller, uint32_t flags, uint32_t *run) {
    (void)caller;
    if (!runs_make(moy_loop_alloc)) {
        return MOY_PLAY_NOMEM;
    }
    // A free row, else the oldest run's: it ends for this one.
    uint32_t slot = 0, oldest = 0;
    bool found = false;
    for (uint32_t i = 0; i < MOY_PLAY_RUNS; i++) {
        run_t *ri = &g_runs->row[i];
        if (ri->h == 0u) {
            slot = i;
            found = true;
            break;
        }
        if (ri->gen < g_runs->row[oldest].gen) {
            oldest = i;
        }
    }
    if (!found) {
        slot = oldest;
        moy_play_end(g_runs->row[slot].h, MOY_PLAY_END_QUIT);
    }
    g_runs->cur = (uint8_t)slot;
    memset(&R.info, 0, sizeof(R.info));
    R.mc = NULL;
    launch_ctx_t c = { &R.info, false, cart, flags };
    if (moy_cat_entry(cart, entry_seen, NULL, &c) != 0 || !c.seen) {
        return MOY_PLAY_NOCART;
    }
    const moy_rt_ops_t *rt = moy_rt_get(R.info.runtime);
    if (rt == NULL) {
        return MOY_PLAY_NORT;
    }
    g_runs->gen = g_runs->gen >= 0xFFFFFu ? 1u : g_runs->gen + 1u;
    R.gen = g_runs->gen;
    R.h = R.last = g_runs->last = R.gen << 10 | slot << 8 | MOY_PLAY_TAG;
    R.rt = rt;
    R.in = NULL;
    R.audio = 0;
    R.tick = NULL;
    R.flags = flags;
    snprintf(R.path, sizeof(R.path), "%s", cart);
    R.start_ms = moycore_run_now_ms();
    R.ptr[0] = R.ptr[1] = R.ptr[2] = 0;
    R.view = 0;
    R.reseed = false;
    R.muted = false;
    R.info.stack_open = R.info.stack_frame = MOY_PLAY_NO_STACK;
    R.budget = watch_budget(NULL);
    moy_chrome_stuck_text(R.what, sizeof(R.what), R.budget);
    watch_run(true);
    // Armed before the runtime loads: a fault in the load names the cart.
    R.armed = R.info.game;
    arm_row(&R);
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
    r->budget = watch_budget(tick);
    moy_chrome_stuck_text(r->what, sizeof(r->what), r->budget);
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
    r->mc = moycore_run_row;
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
    r->window = in->window ? 1 : 0;
    return MOY_PLAY_OK;
}

// The snapshot the tick reads (moycore_run.h's slots), from the run's input
// table: the union of every source as player one (libmoy's seven buttons are
// the table's first seven bits), player two's own when more than one slot is
// in play, the last typed key, the pointer the console published and the
// cart's clock, and whether its surface is a window's body.
static void refresh(run_t *r, int32_t *s) {
    s[SNAP_WINDOW] = r->window;
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
        // time() is the match's clock: two consoles' wall clocks are two worlds.
        s[SNAP_TIME_MS] = moy_lockstep_ms(m, m->s.frame);
        return;
    }
    uint32_t held = 0, pressed = 0;
    uint8_t slots[MOY_INPUT_SOURCES];
    uint8_t n = 1;
    s[SNAP_KEY] = 0;
    if (r->in != NULL && !r->muted) {
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
    s[SNAP_TOUCH_X] = r->muted ? 0 : r->ptr[0];
    s[SNAP_TOUCH_Y] = r->muted ? 0 : r->ptr[1];
    s[SNAP_TOUCH_DOWN] = r->muted ? 0 : r->ptr[2];
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

// A tick the runaway watch ended: the run raised, with the stuck words. A
// Lua run's own raise carries them after the line it was on; a compiled
// cart's says it was terminated, and a tick that came back as the watch
// fired had run past its budget all the same.
void moy_kernel_say(const char *line) __attribute__((weak));

static void stuck(run_t *r, bool raised) {
    moycore_lua_unstuck();
    if (!raised || strstr(r->info.error, r->what) == NULL) {
        size_t n = strnlen(r->what, sizeof(r->what));
        if (n >= sizeof(r->info.error)) {
            n = sizeof(r->info.error) - 1u;
        }
        memcpy(r->info.error, r->what, n);     // two fields of one row: no snprintf
        r->info.error[n] = 0;
    }
    r->info.raised = true;
    r->info.stuck = true;
    if (moy_kernel_say != NULL) {
        char line[240];
        snprintf(line, sizeof(line), "PLAY stuck: %s: %s", r->info.id, r->info.error);
        moy_kernel_say(line);
    }
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
    moycore_run_use(r->mc);             // its own row, whichever was current
    arm_row(r);
    int32_t *snap = moycore_RUN.c.snap;
    int32_t *aq = moycore_RUN.c.aq;
    moy_match_t *lm = moy_match_kernel();
    if (lm != NULL && lm->s.live) {
        // A match's dt is the C float of its tick on every path: a dt that
        // crossed a VM (REPR_C's 30-bit float, the console path) is not
        // the bits the kernel's front frame hands the other console.
        dt = 1.0f / (float)moy_lockstep_hz_at(lm, lm->s.frame - 1);
    }
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
        watch_begin(r->budget, r->what);
        int rc = r->rt->frame(dt, draw, r->info.error, sizeof(r->info.error));
        if (watch_end()) {
            stuck(r, rc != 0);
            return MOY_PLAY_RAISED;
        }
        if (rc != 0) {
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

int moy_play_mute(uint32_t run, bool on) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    r->muted = on;
    return MOY_PLAY_OK;
}

int moy_play_hold(uint32_t run, uint32_t ms) {
    run_t *r = live(run);
    if (r == NULL) {
        return MOY_PLAY_STALE;
    }
    r->start_ms += ms;
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
    watch_run(any_live());
    if (g_runs->armed == (int8_t)(r - g_runs->row)) {
        arm("");                        // the board survived the run
        g_runs->armed = -1;
    }
    r->armed = false;
    if (r == &R) {
        // `cur` moves to the newest run still live, if any.
        uint32_t best = g_runs->cur;
        for (uint32_t i = 0; i < MOY_PLAY_RUNS; i++) {
            run_t *ri = &g_runs->row[i];
            if (ri->h != 0u && (g_runs->row[best].h == 0u || ri->gen > g_runs->row[best].gen)) {
                best = i;
            }
        }
        g_runs->cur = (uint8_t)best;
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
    // at DELAY=2 from this alone, on glass).
    moy_match_drain_input(m, now, MOY_MATCH_DRAIN_MAX);
    // Every advance and every resend is a broadcast: a stalled tick's retry
    // and the resend between ticks wait out MOY_MATCH_LOCK_MS (the gate).
    bool due = moy_lockstep_due(m, now);
    if (!moy_lockstep_gate(m, now, due)) {
        return true;
    }
    if (due || m->s.waiting) {
        // A stalled tick retries every loop frame: the missing input usually
        // lands a few ms after it was first needed.
        uint32_t held = 0, pressed = 0;
        if (r->in != NULL) {
            moy_input_masks(r->in, MOY_INPUT_UNION, &held, &pressed);
        }
        if (moy_lockstep_world_due(m)) {
            moy_lockstep_world(m, m->s.frame, moycore_lua_digest());
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

const char *moy_play_path(void) {
    return g_runs != NULL && R.h != 0u ? R.path : NULL;
}

uint32_t moy_play_flags(void) {
    return g_runs != NULL ? R.flags : 0u;
}

void moy_play_set_down(uint32_t run) {
    run_t *r = live(run);
    if (r != NULL) {
        r->info.vm_down = true;
    }
}

uint32_t moy_play_current(void) {
    return g_runs != NULL ? R.h : 0u;
}

uint32_t moy_play_last(void) {
    return g_runs != NULL ? g_runs->last : 0u;
}

uint32_t moy_play_run_at(uint32_t slot) {
    return g_runs != NULL && slot < MOY_PLAY_RUNS ? g_runs->row[slot].h : 0u;
}

uint32_t moy_play_runs(void) {
    uint32_t n = 0;
    for (uint32_t i = 0; g_runs != NULL && i < MOY_PLAY_RUNS; i++) {
        n += g_runs->row[i].h != 0u;
    }
    return n;
}

int moy_play_info(uint32_t run, moy_play_info_t *out) {
    if (g_runs == NULL || run == 0u) {
        return MOY_PLAY_STALE;
    }
    run_t *r = &g_runs->row[(run >> 8) & (MOY_PLAY_RUNS - 1u)];
    if (run != r->last) {
        return MOY_PLAY_STALE;
    }
    *out = r->info;
    if (!r->info.ended) {
        uint32_t frame[MOY_UPC_CLASSES], total[MOY_UPC_CLASSES];
        moy_loop_upcalls(frame, total);
        for (int i = 0; i < MOY_UPC_CLASSES; i++) {
            out->upcalls[i] = total[i] - r->upc0[i];
        }
    }
    return MOY_PLAY_OK;
}

// Whether a VM runs (moy_loop.c): weak, for a build of the Player without
// the loop (the host's), where one always does.
extern __typeof__(moy_loop_vm) moy_loop_vm __attribute__((weak));

// -- the run in front ------------------------------------------------------------

static struct {
    const moy_front_ops_t *ops;
    uint32_t run;
    bool live;
} F;

void moy_play_reserve(void *(*alloc)(size_t n)) {
    runs_make(alloc);
    watch_make();
}

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
    moycore_run_use(r->mc);
    // A Lua run: its frame is the console's canvas. A compiled cart's frame
    // is its own memory, which the console's present composes (CartFrame).
    // A run with the VM down is the exception: the kernel's present shows
    // its canvas, which the cart's blits write (no frame hand-off is taken).
    if (F.ops == NULL || r->rt->vm || r->in == NULL || r->view || r->info.raised
            || (!r->info.vm_down && (strcmp(r->rt->name, "lua") != 0 || moycore_RUN.wasm))) {
        return MOY_PLAY_NORT;
    }
    int w = 0, h = 0;
    if (F.ops->canvas(F.ops->ctx, &w, &h) == NULL || w != moycore_RUN.c.canvas.w
            || h != moycore_RUN.c.canvas.h) {
        return MOY_PLAY_NORT;           // a canvas the present does not compose
    }
    F.run = run;
    F.live = true;
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
    moycore_run_use(r->mc);             // the canvas below is its row's
    // A Ctrl-C ends the run; the console's next upcall raises it.
    if (moy_play_interrupt_pending != NULL && moy_play_interrupt_pending()) {
        return front_ended(MOY_PLAY_END_SERIAL);
    }
    // The link asks the console for a cart: the console's frame opens it.
    moy_match_t *m = moy_match_kernel();
    if (m != NULL && m->act != MOY_MATCH_ACT_NONE) {
        return front_back();
    }
    uint32_t held = 0, pressed = 0;
    moy_input_masks(r->in, MOY_INPUT_UNION, &held, &pressed);
    // Super (`start`): the console's pause menu holds the run, and the
    // console's frame -- this same loop frame -- takes the press.
    if (pressed & (1u << MOY_PLAY_START_BIT)) {
        return front_back();
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
        moy_match_t *mm = moy_match_kernel();
        dt = 1.0f / (float)moy_lockstep_hz_at(mm, mm->s.frame - 1);
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
        moy_chrome_overlay_draw(px, w, h, moycore_RUN.c.canvas.wire, now);
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
    for (int k = 0; k < MOY_UPC_CLASSES; k++) {
        PUT("%s%u", k ? ", " : "[", (unsigned)u[k]);
    }
    PUT("]");
    *pat = at;
}

size_t moy_play_state_json(char *o, size_t n) {
    size_t at = 0;
    if (!moy_play_front_live()) {
        PUT("null");
        return at;
    }
    run_t *fr = live(F.run);
    const moy_play_info_t *i = &fr->info;
    uint32_t frame[MOY_UPC_CLASSES], total[MOY_UPC_CLASSES], since[MOY_UPC_CLASSES];
    moy_loop_upcalls(frame, total);
    for (int k = 0; k < MOY_UPC_CLASSES; k++) {
        since[k] = total[k] - fr->upc0[k];
    }
    PUT("{\"screen\": \"desktop\", \"front\": true, \"vm\": %s, \"cart\": ",
        moy_loop_vm == NULL || moy_loop_vm() ? "true" : "false");
    PUTS(i->title);
    PUT(", \"cart_error\": null, \"notice\": null, \"frames\": %u, \"run\": "
        "{\"runtime\": ", (unsigned)i->frames);
    PUTS(i->runtime);
    PUT(", \"vm_free\": %s, \"why\": \"%s\", \"stop\": \"%s\", \"vm_down\": %s}, "
        "\"play\": {\"runtime\": ",
        i->vm_free ? "true" : "false", moy_play_why_name(i->why),
        moy_play_stop_name(i->stop_why), i->vm_down ? "true" : "false");
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
    if ((fr->flags & MOY_PLAY_PACED) && fr->tick != NULL && fr->tick->rate > 0) {
        PUT(", \"tick\": [%d, %u, %u, %s]", fr->tick->rate, (unsigned)fr->tick->div,
            (unsigned)fr->tick->misses, fr->tick->steady ? "true" : "false");
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
