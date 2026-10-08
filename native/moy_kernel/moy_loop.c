// moy_loop: the console's frame. moy_loop.h has the contract and the order.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_devch.h"
#include "moy_loop.h"
#include "moy_perf.h"

// The stage budgets in per-mille of the pacing slot; -1 is no deadline (the
// tail: a browser pulling the console bundle owns the frame it lands in, the
// honest behaviour of a single-threaded board). They sum to 1000 with the
// tail's share counted in the frame's: four fifths of a slot reach the glass.
static const struct {
    const char *name;
    int16_t share;
} STAGES[MOY_ST_COUNT] = {
    {"inputs", 60},         // every input source
    {"dev", 20},            // the dev channel's read
    {"idle", 5},            // the idle ladder
    {"pointer", 10},        // click latch + pointer tick
    {"present", 30},        // pre-frame buffer work (present_pending)
    {"frame", 780},         // handle_input + handle_pointer + draw/composite/flush
    {"backlight", 5},       // the one-shot first-frame gate and its fence
    {"pump_tail", 30},      // first light's report + the OTA confirm
    {"tail", -1},           // the per-frame services
    {"pace", 10},           // the cadence arithmetic, never the sleep
    {"account", 50},        // the PERF window + the board's diag accounting
};

// Where a meter halves its rolling sum and count: a mean that is a window,
// never an accumulator that overflows.
#define MEAN_CAP (1u << 28)

// The OTA confirm: something reached the glass and the loop kept running.
#define HEALTHY_PAINTS 1u
#define HEALTHY_LOOPS 120u

#define PERF_PERIOD_MS 2000u

typedef struct {
    int32_t budget[MOY_ST_COUNT];
    uint32_t last[MOY_ST_COUNT], max[MOY_ST_COUNT], misses[MOY_ST_COUNT];
    uint32_t n[MOY_ST_COUNT], total[MOY_ST_COUNT], seen[MOY_ST_COUNT];
    uint32_t slot_ms;
    uint32_t t;
    uint32_t mask;              // the stages marked this frame
    bool skip;
} meters_t;

// What the loop allocates once from its tier (PSRAM on a board: kernel data
// is PSRAM by rule) -- the meters and the PERF window.
// The LOOP window: each stage's microseconds summed over the PERF period, the
// stages that ran in it, the frames, their work and their sleep.
typedef struct {
    uint32_t sum[MOY_ST_COUNT];
    uint32_t mask, n, frame_ms, sleep_ms;
} window_t;

#define DIAG_HITCH 1u
#define DIAG_LOOP 2u

typedef struct {
    meters_t m;
    moy_perf_t perf;
    window_t w;
    char line[MOY_PERF_LINE_MAX];
    // The last HITCH and LOOP lines, for a board that rings them as well.
    char hitch[MOY_PERF_LINE_MAX], loop[MOY_PERF_LINE_MAX];
    uint8_t pending;
} heavy_t;

static struct {
    const moy_loop_ops_t *ops;
    moy_loop_up_fn up;
    uint32_t registered;
    bool vm;
    bool inited;
    // the pump
    uint32_t frame_ms, slot, debt, slack, expected, last, tick_ms;
    bool slept;
    // the frame
    bool lit, capture, health_armed, measuring;
    uint32_t frames, drawn, started_at;
    uint32_t services, once;
    uint32_t last_elapsed, last_sleep;
    int end;                    // an upcall asked the loop to end (MOY_LOOP_*)
    bool first_done;
    uint32_t health_loops;
    // counts
    uint32_t up_frame[MOY_UPC_CLASSES], up_last[MOY_UPC_CLASSES], up_total[MOY_UPC_CLASSES];
    moy_idle_t idle;
    heavy_t *heavy;
} L = {.vm = true, .frame_ms = 16, .slot = 16};

// -- the tier's helpers ---------------------------------------------------------

void *moy_loop_alloc(size_t n) {
    if (L.ops != NULL && L.ops->alloc != NULL) {
        return L.ops->alloc(n);
    }
    return malloc(n);
}

void moy_loop_say(const char *fmt, ...) {
    char buf[384];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (L.ops != NULL && L.ops->say != NULL) {
        L.ops->say(buf);
    } else {
        printf("%s\n", buf);
    }
}

static uint32_t now_ms(void) {
    return L.ops->ticks_ms != NULL ? L.ops->ticks_ms() : 0;
}

static uint32_t now_us(void) {
    return L.ops->ticks_us != NULL ? L.ops->ticks_us() : now_ms() * 1000u;
}

// -- the meters -------------------------------------------------------------------

static void rebudget(meters_t *m, uint32_t slot_ms) {
    m->slot_ms = slot_ms;
    for (int i = 0; i < MOY_ST_COUNT; i++) {
        m->budget[i] = STAGES[i].share < 0 ? -1
                       : (int32_t)(slot_ms * 1000u * (uint32_t)STAGES[i].share / 1000u);
    }
}

static void meters_clear(meters_t *m) {
    memset(m->last, 0, sizeof(m->last));
    memset(m->max, 0, sizeof(m->max));
    memset(m->misses, 0, sizeof(m->misses));
    memset(m->n, 0, sizeof(m->n));
    memset(m->total, 0, sizeof(m->total));
    memset(m->seen, 0, sizeof(m->seen));
}

static void meters_start(meters_t *m, uint32_t slot_ms) {
    if (slot_ms != m->slot_ms) {
        rebudget(m, slot_ms);
    }
    m->skip = false;
    m->mask = 0;
    m->t = now_us();
}

static void mark(int i) {
    if (!L.measuring) {
        return;
    }
    meters_t *m = &L.heavy->m;
    if (m->skip) {
        return;
    }
    uint32_t t = now_us();
    uint32_t us = t - m->t;
    m->t = t;
    m->mask |= 1u << i;
    m->last[i] = us;
    if (us > m->max[i]) {
        m->max[i] = us;
    }
    m->n[i]++;
    uint32_t tot = m->total[i] + us;
    uint32_t k = m->seen[i] + 1u;
    if (tot > MEAN_CAP || tot < m->total[i]) {
        tot >>= 1;
        k >>= 1;
    }
    m->total[i] = tot;
    m->seen[i] = k;
    if (m->budget[i] >= 0 && us > (uint32_t)m->budget[i]) {
        m->misses[i]++;
    }
}

void moy_loop_meters_reset(void) {
    if (L.heavy == NULL) {
        return;
    }
    meters_clear(&L.heavy->m);
    L.heavy->m.skip = true;
}

const char *moy_loop_stage_name(int i) {
    return i >= 0 && i < MOY_ST_COUNT ? STAGES[i].name : NULL;
}

bool moy_loop_meter(int i, moy_loop_meter_t *out) {
    if (i < 0 || i >= MOY_ST_COUNT) {
        return false;
    }
    memset(out, 0, sizeof(*out));
    out->budget_us = STAGES[i].share < 0 ? -1
                     : (int32_t)(L.slot * 1000u * (uint32_t)STAGES[i].share / 1000u);
    if (L.heavy == NULL) {
        return false;
    }
    meters_t *m = &L.heavy->m;
    out->budget_us = m->slot_ms ? m->budget[i] : out->budget_us;
    out->n = m->n[i];
    out->avg_us = m->seen[i] ? m->total[i] / m->seen[i] : 0;
    out->last_us = m->last[i];
    out->max_us = m->max[i];
    out->misses = m->misses[i];
    return m->n[i] != 0;
}

uint32_t moy_loop_slot(void) {
    return L.slot;
}

// -- the pump ---------------------------------------------------------------------

// The cadence one frame is measured against: the running cart's tick while
// the Player paces a game, else the loop's cap, never faster than the cap.
static uint32_t slot_ms(void) {
    return L.tick_ms > L.frame_ms ? L.tick_ms : L.frame_ms;
}

// The top of the loop: dt, clamped to 0..100 ms so a hitch cannot teleport a
// cart's physics. Also the sleep-overshoot learner: a saturating +-1 ms a
// frame walker over the real period of frames that slept (or whose sleep the
// slack cut), floored at 0 and capped at 8 so a hitch stays the debt's.
static uint32_t pump_begin(uint32_t now, uint32_t *dt_ms) {
    uint32_t real = now - L.last;
    if (L.slept) {
        int32_t over = (int32_t)(real - L.expected);
        if (over > 0 && L.slack < 8u) {
            L.slack++;
        } else if (over < 0 && L.slack > 0u) {
            L.slack--;
        }
    }
    *dt_ms = real > 100u ? 100u : real;
    L.last = now;
    return now;
}

// How long to sleep after a frame of `elapsed` ms. A PACED GAME DOES NOT
// SLEEP (#217): the Player places logic ticks on the cart's own clock and a
// sleep would quantize them onto the FreeRTOS tick. Otherwise the frame is
// slept into the slot, minus the learned overshoot, and an over-budget frame
// accrues DEBT that the next frames' sleeps pay down (#77), capped at two
// slots so a real hitch does not eat a second of sleeps.
uint32_t moy_loop_pace(uint32_t elapsed) {
    uint32_t fms = L.slot = slot_ms();
    if (L.tick_ms) {
        L.debt = 0;
        L.expected = elapsed;
        L.slept = false;
        return 0;
    }
    if (elapsed < fms) {
        uint32_t sleep = fms - elapsed;
        if (L.debt) {
            uint32_t take = sleep < L.debt ? sleep : L.debt;
            sleep -= take;
            L.debt -= take;
        }
        uint32_t cut = 0;
        if (L.slack && sleep) {
            cut = L.slack < sleep ? L.slack : sleep;
            sleep -= cut;
        }
        L.expected = fms;
        L.slept = sleep > 0 || cut > 0;
        return sleep;
    }
    L.expected = elapsed;
    L.slept = false;
    L.debt += elapsed - fms;
    if (L.debt > 2u * fms) {
        L.debt = 2u * fms;
    }
    return 0;
}

void moy_loop_pump(moy_loop_pump_t *out) {
    out->frame_ms = L.frame_ms;
    out->slot = L.slot;
    out->debt = L.debt;
    out->slack = L.slack;
    out->tick_ms = L.tick_ms;
}

// -- the tier's side ---------------------------------------------------------------

static const moy_loop_ops_t NO_OPS = {0};

void moy_loop_init(const moy_loop_ops_t *ops, int fps_cap) {
    L.ops = ops ? ops : &NO_OPS;
    L.frame_ms = 1000u / (uint32_t)(fps_cap > 0 ? fps_cap : 60);
    L.slot = L.frame_ms;
    L.debt = L.slack = L.expected = 0;
    L.slept = false;
    L.last = now_ms();
    L.frames = 0;
    L.drawn = 0;
    L.first_done = false;
    L.started_at = L.last;
    L.health_loops = 0;
    L.health_armed = false;
    L.capture = false;
    L.lit = false;
    L.tick_ms = 0;
    L.services = 0;
    L.once = 0;
    L.last_elapsed = L.last_sleep = 0;
    L.end = 0;
    memset(L.up_frame, 0, sizeof(L.up_frame));
    memset(L.up_last, 0, sizeof(L.up_last));
    memset(L.up_total, 0, sizeof(L.up_total));
    if (!L.inited) {
        moy_devch_init();
        bool can_dim = L.ops->backlight != NULL;
        moy_idle_init(&L.idle, can_dim, L.last);
        L.inited = true;
    } else {
        L.idle.idle_at = L.last;
    }
    if (L.heavy == NULL) {
        L.heavy = moy_loop_alloc(sizeof(heavy_t));
        if (L.heavy != NULL) {
            memset(L.heavy, 0, sizeof(*L.heavy));
            rebudget(&L.heavy->m, L.slot);
        }
    }
    if (L.heavy != NULL) {
        meters_clear(&L.heavy->m);
        L.heavy->m.skip = false;
        rebudget(&L.heavy->m, L.slot);
        moy_perf_init(&L.heavy->perf, L.last, PERF_PERIOD_MS);
    }
}

const moy_loop_ops_t *moy_loop_ops(void) {
    return L.ops;
}

bool moy_loop_has_ops(void) {
    return L.ops != NULL && L.ops != &NO_OPS;
}

void moy_loop_set_upcall(moy_loop_up_fn fn) {
    L.up = fn;
}

void moy_loop_set_registered(uint32_t bits) {
    L.registered = bits;
}

uint32_t moy_loop_registered(void) {
    return L.registered;
}

void moy_loop_set_vm(bool up) {
    L.vm = up;
    if (!up) {
        L.registered = 0;
    }
}

bool moy_loop_vm(void) {
    return L.vm;
}

void moy_loop_set_fps(int fps_cap) {
    L.frame_ms = 1000u / (uint32_t)(fps_cap > 0 ? fps_cap : 60);
}

void moy_loop_set_tick(uint32_t tick_ms) {
    L.tick_ms = tick_ms;
}

void moy_loop_set_capture(bool on) {
    L.capture = on;
}

bool moy_loop_capture(void) {
    return L.capture;
}

void moy_loop_arm_health(bool on) {
    L.health_armed = on;
    L.health_loops = 0;
}

void moy_loop_set_services(uint32_t bits) {
    L.services = bits;
}

uint32_t moy_loop_services(void) {
    return L.services;
}

void moy_loop_set_lit(bool lit) {
    L.lit = lit;
}

moy_idle_t *moy_loop_idle(void) {
    return &L.idle;
}

void moy_loop_count(int cls) {
    if (cls >= 0 && cls < MOY_UPC_CLASSES) {
        L.up_frame[cls]++;
        L.up_total[cls]++;
    }
}

void moy_loop_upcalls(uint32_t frame[MOY_UPC_CLASSES], uint32_t total[MOY_UPC_CLASSES]) {
    for (int i = 0; i < MOY_UPC_CLASSES; i++) {
        if (frame != NULL) {
            frame[i] = L.up_last[i];
        }
        if (total != NULL) {
            total[i] = L.up_total[i];
        }
    }
}

uint32_t moy_loop_frame_at(void) {
    return L.last;
}

uint32_t moy_loop_frames(void) {
    return L.frames;
}

void moy_loop_last(uint32_t *elapsed, uint32_t *sleep) {
    *elapsed = L.last_elapsed;
    *sleep = L.last_sleep;
}

uint32_t moy_loop_drawn(void) {
    return L.drawn;
}

// -- upcalls ------------------------------------------------------------------------

static int up(int which, uint32_t arg, const char *line, int cls) {
    if (!L.vm || L.up == NULL) {
        return MOY_UP_ABSENT;
    }
    if (which <= MOY_UP_FRAME && !(L.registered & (1u << which))) {
        return MOY_UP_ABSENT;
    }
    moy_loop_count(cls);
    int r = L.up(which, arg, line);
    if (r == MOY_UP_INTERRUPTED && !L.end) {
        L.end = MOY_LOOP_INTERRUPT;
    } else if (r == MOY_UP_EXIT && !L.end) {
        L.end = MOY_LOOP_EXIT;
    }
    return r;
}

// The end an upcall asked for, taken once.
static int take_end(void) {
    int e = L.end;
    L.end = 0;
    return e;
}

void moy_loop_end(int why) {
    if (!L.end) {
        L.end = why;
    }
}

int moy_loop_word(const char *line) {
    return up(MOY_UP_WORD, 0, line, MOY_UPC_CONSOLE);
}

int moy_loop_service(uint32_t which) {
    return up(MOY_UP_SERVICE, which, NULL, MOY_UPC_SERVICE);
}

// -- HITCH and LOOP (docs/kernel_survival_2026-10.md §7.3) ----------------------------
//
// Under PERF DIAG, from the stage meters: HITCH names every stage of one frame
// whose work (`ms`) ran past MOY_LOOP_HITCH_MS, LOOP the average frame of the
// PERF period by stage. Both cover the stages inside the frame's work (inputs to
// tail); the pacing sleep is LOOP's `sleep`, and `other` is the work no stage
// holds. A stage with no op on this tier was never metered and prints `-`.

static size_t put_ms(char *out, size_t cap, const char *name, bool have, uint32_t us) {
    if (!have) {
        return (size_t)snprintf(out, cap, " %s=-", name);
    }
    uint32_t t = (us + 50u) / 100u;             // tenths of a millisecond
    return (size_t)snprintf(out, cap, " %s=%u.%u", name, (unsigned)(t / 10u),
                            (unsigned)(t % 10u));
}

static void diag_frame(uint32_t elapsed, uint32_t sleep) {
    if (!L.measuring || L.heavy->m.skip) {
        return;
    }
    heavy_t *h = L.heavy;
    meters_t *m = &h->m;
    window_t *w = &h->w;
    for (int i = 0; i <= MOY_ST_TAIL; i++) {
        if (m->mask & (1u << i)) {
            w->sum[i] += m->last[i];
        }
    }
    w->mask |= m->mask;
    w->n++;
    w->frame_ms += elapsed;
    w->sleep_ms += sleep;
    if (elapsed < MOY_LOOP_HITCH_MS) {
        return;
    }
    size_t cap = sizeof(h->hitch);
    size_t n = (size_t)snprintf(h->hitch, cap, "HITCH ms=%u", (unsigned)elapsed);
    for (int i = 0; i <= MOY_ST_TAIL && n < cap; i++) {
        n += put_ms(h->hitch + n, cap - n, STAGES[i].name, (m->mask >> i) & 1u, m->last[i]);
    }
    h->pending |= DIAG_HITCH;
    moy_loop_say("%s", h->hitch);
}

static void diag_window(bool emit) {
    heavy_t *h = L.heavy;
    window_t *w = &h->w;
    if (emit && w->n) {
        size_t cap = sizeof(h->loop);
        uint32_t staged = 0;
        size_t n = (size_t)snprintf(h->loop, cap, "LOOP n=%u", (unsigned)w->n);
        n += put_ms(h->loop + n, cap - n, "ms", true, w->frame_ms * 1000u / w->n);
        for (int i = 0; i <= MOY_ST_TAIL && n < cap; i++) {
            bool have = (w->mask >> i) & 1u;
            staged += have ? w->sum[i] / w->n : 0u;
            n += put_ms(h->loop + n, cap - n, STAGES[i].name, have, w->sum[i] / w->n);
        }
        uint32_t frame_us = w->frame_ms * 1000u / w->n;
        if (n < cap) {
            n += put_ms(h->loop + n, cap - n, "sleep", true, w->sleep_ms * 1000u / w->n);
        }
        if (n < cap) {
            put_ms(h->loop + n, cap - n, "other", true, frame_us > staged ? frame_us - staged : 0u);
        }
        h->pending |= DIAG_LOOP;
        moy_loop_say("%s", h->loop);
    }
    memset(w, 0, sizeof(*w));
}

int moy_loop_diag_take(char *hitch, char *loop, size_t cap) {
    if (L.heavy == NULL || cap == 0) {
        return 0;
    }
    int got = L.heavy->pending;
    if (got & DIAG_HITCH) {
        snprintf(hitch, cap, "%s", L.heavy->hitch);
    }
    if (got & DIAG_LOOP) {
        snprintf(loop, cap, "%s", L.heavy->loop);
    }
    L.heavy->pending = 0;
    return got;
}

// -- the PERF window ------------------------------------------------------------------

moy_perf_values_t *moy_loop_perf_console(void) {
    return L.heavy != NULL ? &L.heavy->perf.console : NULL;
}

bool moy_loop_perf_due(void) {
    return L.capture && L.heavy != NULL && (int32_t)(now_ms() - L.heavy->perf.at) >= 0;
}

static void account(uint32_t elapsed) {
    if (L.heavy == NULL) {
        return;
    }
    moy_perf_t *p = &L.heavy->perf;
    uint32_t now = now_ms();
    if (!moy_perf_account(p, now, elapsed)) {
        return;
    }
    if (L.capture) {
        moy_perf_overlap_t ov;
        uint32_t gc[3];
        const moy_perf_overlap_t *ovp = NULL;
        const uint32_t *gcp = NULL;
        if (L.ops->overlap != NULL && L.ops->overlap(&ov)) {
            ovp = &ov;
        }
        if (L.ops->gc_pauses != NULL && L.ops->gc_pauses(gc)) {
            gcp = gc;
        }
        moy_perf_sample(p, L.drawn, ovp, gcp, L.heavy->line, sizeof(L.heavy->line));
        if (L.ops->perf_line != NULL) {
            L.ops->perf_line(L.heavy->line);
        } else {
            moy_loop_say("%s", L.heavy->line);
        }
        diag_window(true);
    } else {
        moy_perf_skip(p, L.drawn);
        diag_window(false);
    }
    moy_perf_close(p, now_ms(), L.drawn);
}

// -- the frame -----------------------------------------------------------------------

int moy_loop_step(void) {
    const moy_loop_ops_t *ops = L.ops;
    if (ops == NULL || !L.vm || L.up == NULL || (L.registered & 7u) != 7u) {
        return MOY_LOOP_STOPPED;
    }
    memset(L.up_frame, 0, sizeof(L.up_frame));
    uint32_t dt;
    uint32_t now = pump_begin(now_ms(), &dt);
    L.measuring = L.capture && L.heavy != NULL;
    if (L.measuring) {
        meters_start(&L.heavy->m, L.slot);
    }
    bool click = false, active = false;
    if (ops->inputs != NULL) {
        ops->inputs(now, &click, &active);
        mark(MOY_ST_INPUTS);
    }
    bool quit = false;
    bool ran = false;
    if (ops->getc != NULL || moy_devch_gesture()) {
        ran = moy_devch_poll(&quit);
        if (L.end) {
            return take_end();
        }
        if (quit) {
            return MOY_LOOP_QUIT;
        }
        mark(MOY_ST_DEV);
    }
    // After EVERY input source and the dev channel, before the pointer
    // reaches the console: the order that swallows the waking touch.
    moy_idle_out_t io;
    moy_idle_tick(&L.idle, now, active || ran, &io);
    if (io.light >= 0 && ops->backlight != NULL && L.lit) {
        ops->backlight(io.light);
    }
    if (io.repaint && ops->repaint != NULL) {
        ops->repaint();
    }
    if (io.swallow) {
        click = false;
    }
    if (io.entered) {
        moy_loop_say("Moybyte power save: %s (idle %us)", moy_idle_name(L.idle.state),
                     (unsigned)moy_idle_get(&L.idle, L.idle.state));
    }
    mark(MOY_ST_IDLE);
    if (ops->pointer != NULL) {
        ops->pointer(now, click, io.swallow);
        mark(MOY_ST_POINTER);
    }
    if (ops->present != NULL) {
        ops->present();
        mark(MOY_ST_PRESENT);
    }
    uint32_t before = L.drawn;
    up(MOY_UP_INPUT, 0, NULL, MOY_UPC_CONSOLE);
    if (!L.end) {
        up(MOY_UP_POINTER, 0, NULL, MOY_UPC_CONSOLE);
    }
    if (!L.end) {
        int r = up(MOY_UP_FRAME, dt * 1000u, NULL, MOY_UPC_CONSOLE);
        if (r >= 0) {
            L.drawn = (uint32_t)r;
        }
    }
    if (L.end) {
        return take_end();
    }
    bool drew = L.drawn != before;
    mark(MOY_ST_FRAME);
    // The first composed frame lights the panel (#45), behind the fence, and
    // never over a panel the ladder darkened (the board renders while dark).
    if (!L.lit && L.idle.state < MOY_IDLE_BLANK && L.drawn > 0) {
        if (ops->fence != NULL) {
            ops->fence();
        }
        if (ops->backlight != NULL) {
            ops->backlight(moy_idle_light(&L.idle));
        }
        L.lit = true;
    }
    mark(MOY_ST_BACKLIGHT);
    if (!L.first_done && L.drawn > 0) {
        L.first_done = true;
        if (ops->first_light != NULL) {
            ops->first_light(now_ms() - L.started_at);
        }
    }
    if (L.health_armed) {
        L.health_loops++;
        if (L.drawn >= HEALTHY_PAINTS && L.health_loops >= HEALTHY_LOOPS) {
            L.health_armed = false;
            if (ops->healthy != NULL) {
                ops->healthy();
            }
            L.once |= MOY_SVC_HEALTHY;
        }
    }
    mark(MOY_ST_PUMP_TAIL);
    uint32_t svc = L.services | L.once | (ops->services != NULL ? ops->services() : 0u);
    if (ops->tail != NULL || svc) {
        if (ops->tail != NULL) {
            ops->tail(now, drew);
        }
        if (svc) {
            L.once = 0;
            int r = moy_loop_service(svc);
            if (L.end) {
                return take_end();
            }
            if (r >= 0) {
                L.services = (uint32_t)r;
            }
        }
        mark(MOY_ST_TAIL);
    }
    uint32_t elapsed = now_ms() - now;
    uint32_t sleep = moy_loop_pace(elapsed);
    mark(MOY_ST_PACE);
    diag_frame(elapsed, sleep);
    account(elapsed);
    if (ops->account != NULL) {
        ops->account(now, elapsed, sleep);
    }
    mark(MOY_ST_ACCOUNT);
    L.frames++;
    L.last_elapsed = elapsed;
    L.last_sleep = sleep;
    memcpy(L.up_last, L.up_frame, sizeof(L.up_last));
    if (ops->feed != NULL) {
        ops->feed();
    }
    if (sleep && ops->sleep_ms != NULL) {
        ops->sleep_ms(sleep);
    }
    return MOY_LOOP_OK;
}

int moy_loop_run(void) {
    for (;;) {
        int r = moy_loop_step();
        if (r != MOY_LOOP_OK) {
            return r;
        }
    }
}
