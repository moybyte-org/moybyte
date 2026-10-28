// moy_input: the merged input table. moy_input.h has the contract.

#include "moy_input.h"

#include <string.h>

#include "moy_htab.h"

#ifdef MOY_INPUT_BOARD
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#define LOCK(t) portENTER_CRITICAL_SAFE(&(t)->front->mux)
#define UNLOCK(t) portEXIT_CRITICAL_SAFE(&(t)->front->mux)
#else
#include <stdlib.h>
#define LOCK(t) ((void)0)
#define UNLOCK(t) ((void)0)
#endif

const char *const MOY_INPUT_NAMES[MOY_INPUT_BUTTONS] = {
    "left", "right", "up", "down", "a", "b", "run", "home",
    "x", "y", "stop", "save", "share", "select", "start", "full",
};

// A source's latch: what its producer wrote since the last merge. Internal RAM
// on a board, every field written under the table's spinlock.
typedef struct {
    uint32_t held;
    int32_t key;                    // the key this source holds now (a level)
    int32_t q[MOY_INPUT_KEYQ];      // one-shot keys awaiting their frame
    uint8_t qn;
    uint8_t queued;                 // the queue drives `key` at every begin_frame
    uint8_t mods;                   // the modifiers it holds
    int32_t typing;                 // the key it holds down for typing, which repeats
    uint8_t pbuttons;               // its pointer's buttons
    uint8_t pbseen;                 // ...and every one held since the merge
    int16_t wx, wy;                 // wheel notches since the merge
    int32_t qlast;                  // the key the queue delivered last frame
    uint8_t pflags;                 // the pointer sample: P_SET | P_DOWN | P_EDGE | P_FRESH
    int16_t px, py;
    uint32_t pseq;                  // when it was written, for the newest-wins merge
} moy_input_latch_t;

enum { P_SET = 1, P_DOWN = 2, P_EDGE = 4, P_FRESH = 8 };

typedef struct {
#ifdef MOY_INPUT_BOARD
    portMUX_TYPE mux;
#endif
    int8_t key_src;                 // which source owns last_key, -1 for none
    int32_t last_key;
    uint32_t pseq;
    moy_input_latch_t src[MOY_INPUT_SOURCES];
} moy_input_front_t;

// The table: PSRAM on a board. Masks are bits in MOY_INPUT_NAMES order.
struct moy_input {
    moy_input_front_t *front;
    uint8_t nbuttons;
    uint8_t nsrc;
    uint8_t solo;                   // the one player slot, while there is one
    bool multi;                     // two sources disagree about their player
    bool taken;                     // a logic tick already took this frame's edges
    bool text_mode;
    bool cart_keys;
    uint8_t mods;                   // every source's held modifiers at the merge
    uint8_t pb_held, pb_pressed;    // the pointer's buttons, and their press edges
    int32_t wheel_x, wheel_y;       // the frame's wheel notches
    uint8_t player[MOY_INPUT_SOURCES];
    char name[MOY_INPUT_SOURCES][MOY_INPUT_NAME + 1];
    uint32_t held, last, pressed, released, kept;
    uint32_t p_held[MOY_INPUT_PLAYERS];
    uint32_t p_last[MOY_INPUT_PLAYERS];
    uint32_t p_pressed[MOY_INPUT_PLAYERS];
    uint32_t p_kept[MOY_INPUT_PLAYERS];
    uint32_t was[MOY_INPUT_SOURCES];        // each source's held set at the last merge
    int32_t was_key[MOY_INPUT_SOURCES];     // and its key
    uint8_t news;                           // the source that brought news last, slot + 1
    moy_input_sample_t smp;
    int32_t rep_delay, rep_rate;            // the repeat's parameters, ms
    uint32_t now;                           // the clock at the last merge
    uint32_t rep_due[MOY_INPUT_SOURCES][MOY_INPUT_BUTTONS];   // each held button's next repeat
    uint8_t rep[MOY_INPUT_BUTTONS];         // this frame's repeats and added steps
    int32_t krep_key[MOY_INPUT_SOURCES];    // the typing level each source repeats
    uint32_t krep_due[MOY_INPUT_SOURCES];
    int32_t krep_ev;                        // this frame's repeated key, and its count
    uint8_t krep;
};

#define HANDLE(slot) ((1u << MOY_HTAB_GEN_SHIFT) | ((uint32_t)MOY_KIND_SRC << MOY_HTAB_KIND_SHIFT) | (slot))

static int slot_of(const moy_input_t *t, uint32_t h) {
    uint32_t slot = h & 0xFFu;
    if (h != HANDLE(slot) || slot >= t->nsrc) {
        return -1;
    }
    return (int)slot;
}

static uint32_t vocab(const moy_input_t *t) {
    return (1u << t->nbuttons) - 1u;
}

#ifdef MOY_INPUT_BOARD
// The kernel's small-block pool (moy_kernel.h), where the image has it.
void *moy_kpool_alloc(size_t n) __attribute__((weak));
bool moy_kpool_free(void *p) __attribute__((weak));
static void *tab_alloc(size_t n) {
    void *q = moy_kpool_alloc != NULL ? moy_kpool_alloc(n) : NULL;
    if (q != NULL) {
        return q;
    }
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    return p ? p : heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
}
static void tab_free(void *p) {
    if (moy_kpool_free != NULL && moy_kpool_free(p)) {
        return;
    }
    heap_caps_free(p);
}
static void *front_alloc(void) {
    return heap_caps_calloc(1, sizeof(moy_input_front_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}
static void front_free(void *p) {
    heap_caps_free(p);
}
#else
static void *tab_alloc(size_t n) {
    return calloc(1, n);
}
static void tab_free(void *p) {
    free(p);
}
static void *front_alloc(void) {
    return calloc(1, sizeof(moy_input_front_t));
}
static void front_free(void *p) {
    free(p);
}
#endif

static void rescan(moy_input_t *t) {
    t->multi = false;
    t->solo = t->nsrc ? t->player[0] : 0;
    for (uint8_t i = 1; i < t->nsrc; i++) {
        if (t->player[i] != t->solo) {
            t->multi = true;
        }
    }
}

static int init(moy_input_t *t, moy_input_front_t *front, uint8_t nbuttons) {
    memset(t, 0, sizeof(*t));
    memset(front, 0, sizeof(*front));
#ifdef MOY_INPUT_BOARD
    portMUX_INITIALIZE(&front->mux);
#endif
    front->key_src = -1;
    t->front = front;
    t->nbuttons = nbuttons;
    t->rep_delay = MOY_INPUT_REPEAT_DELAY_MS;
    t->rep_rate = MOY_INPUT_REPEAT_RATE_MS;
    uint32_t h;
    return moy_input_source(t, "local", 0, &h);
}

moy_input_t *moy_input_new(uint8_t nbuttons) {
    if (nbuttons == 0 || nbuttons > MOY_INPUT_BUTTONS) {
        return NULL;
    }
    moy_input_t *t = tab_alloc(sizeof(*t));
    moy_input_front_t *front = front_alloc();
    if (t == NULL || front == NULL) {
        if (t) {
            tab_free(t);
        }
        if (front) {
            front_free(front);
        }
        return NULL;
    }
    init(t, front, nbuttons);
    return t;
}

void moy_input_free(moy_input_t *t) {
    if (t == NULL || t == moy_input_kernel()) {
        return;
    }
    front_free(t->front);
    tab_free(t);
}

// The kernel table's latches: .bss, which is internal RAM on a board.
static moy_input_front_t s_kernel_front;
static moy_input_t *s_kernel;

moy_input_t *moy_input_kernel(void) {
    if (s_kernel == NULL) {
        moy_input_t *t = tab_alloc(sizeof(*t));
        if (t == NULL) {
            return NULL;
        }
        init(t, &s_kernel_front, MOY_INPUT_BUTTONS);
        s_kernel = t;
    }
    return s_kernel;
}

uint8_t moy_input_nbuttons(const moy_input_t *t) {
    return t->nbuttons;
}

int moy_input_source(moy_input_t *t, const char *name, uint8_t player, uint32_t *h) {
    size_t n = strlen(name);
    if (n == 0 || n > MOY_INPUT_NAME || player >= MOY_INPUT_PLAYERS) {
        return MOY_INPUT_BAD;
    }
    for (uint8_t i = 0; i < t->nsrc; i++) {
        if (strcmp(t->name[i], name) == 0) {
            *h = HANDLE(i);
            return MOY_INPUT_OK;
        }
    }
    if (t->nsrc == MOY_INPUT_SOURCES) {
        return MOY_INPUT_FULL;
    }
    uint8_t i = t->nsrc;
    memcpy(t->name[i], name, n + 1);
    t->player[i] = player;
    t->nsrc = i + 1;
    rescan(t);
    *h = HANDLE(i);
    return MOY_INPUT_OK;
}

int moy_input_source_name(const moy_input_t *t, uint32_t h, char *out, size_t n) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    size_t len = strlen(t->name[s]);
    if (len + 1 > n) {
        return MOY_INPUT_BAD;
    }
    memcpy(out, t->name[s], len + 1);
    return MOY_INPUT_OK;
}

int moy_input_source_player(const moy_input_t *t, uint32_t h, uint8_t *player) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    *player = t->player[s];
    return MOY_INPUT_OK;
}

int moy_input_set_player(moy_input_t *t, uint32_t h, uint8_t player) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (player >= MOY_INPUT_PLAYERS) {
        return MOY_INPUT_BAD;
    }
    t->player[s] = player;
    rescan(t);
    return MOY_INPUT_OK;
}

// -- the latches -----------------------------------------------------------------

int moy_input_set_held(moy_input_t *t, uint32_t h, uint8_t button, bool held) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (button >= t->nbuttons) {
        return MOY_INPUT_BAD;
    }
    moy_input_latch_t *l = &t->front->src[s];
    LOCK(t);
    if (held) {
        l->held |= 1u << button;
    } else {
        l->held &= ~(1u << button);
    }
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_set_mask(moy_input_t *t, uint32_t h, uint32_t held) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (held & ~vocab(t)) {
        return MOY_INPUT_BAD;
    }
    LOCK(t);
    t->front->src[s].held = held;
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_release(moy_input_t *t, uint32_t h) {
    return moy_input_set_mask(t, h, 0);
}

// The key's ownership rule, under the lock: a new nonzero value takes the
// slot, the owner re-asserts, the owner going quiet hands it to the first
// source still holding a key.
static void key_locked(moy_input_t *t, int s, int32_t key) {
    moy_input_front_t *f = t->front;
    int32_t old = f->src[s].key;
    f->src[s].key = key;
    if (key) {
        if (key != old || f->key_src < 0) {
            f->key_src = (int8_t)s;
            f->last_key = key;
        } else if (f->key_src == s) {
            f->last_key = key;
        }
    } else if (f->key_src == s) {
        int32_t k = 0;
        int8_t owner = -1;
        for (uint8_t i = 0; i < t->nsrc; i++) {
            if (f->src[i].key) {
                k = f->src[i].key;
                owner = (int8_t)i;
                break;
            }
        }
        f->key_src = owner;
        f->last_key = k;
    }
}

int moy_input_set_key(moy_input_t *t, uint32_t h, int32_t key) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    t->front->src[s].queued = 0;
    t->front->src[s].qn = 0;
    key_locked(t, s, key);
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_key(moy_input_t *t, uint32_t h, int32_t key) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (key <= 0 || key > 0xFFFFFF) {
        return MOY_INPUT_BAD;
    }
    moy_input_latch_t *l = &t->front->src[s];
    int r = MOY_INPUT_OK;
    LOCK(t);
    l->queued = 1;
    if (l->qn < MOY_INPUT_KEYQ) {
        l->q[l->qn++] = key;
    } else {
        r = MOY_INPUT_FULL;
    }
    UNLOCK(t);
    return r;
}

int moy_input_set_mods(moy_input_t *t, uint32_t h, uint8_t mods) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    t->front->src[s].mods = mods;
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_set_typing(moy_input_t *t, uint32_t h, int32_t key) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    t->front->src[s].typing = key;
    UNLOCK(t);
    return MOY_INPUT_OK;
}

static int16_t sat16(int32_t v) {
    return (int16_t)(v < INT16_MIN ? INT16_MIN : (v > INT16_MAX ? INT16_MAX : v));
}

int moy_input_scroll(moy_input_t *t, uint32_t h, int32_t dx, int32_t dy) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    moy_input_latch_t *l = &t->front->src[s];
    LOCK(t);
    l->wx = sat16(l->wx + dx);
    l->wy = sat16(l->wy + dy);
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_set_pointer_buttons(moy_input_t *t, uint32_t h, uint8_t held) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    t->front->src[s].pbuttons = held;
    t->front->src[s].pbseen |= held;
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_source_key(const moy_input_t *t, uint32_t h, int32_t *key) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    *key = t->front->src[s].key;
    return MOY_INPUT_OK;
}

uint8_t moy_input_source_mods(const moy_input_t *t, uint32_t h) {
    int s = slot_of(t, h);
    return s < 0 ? 0 : t->front->src[s].mods;
}

uint8_t moy_input_source_pointer_buttons(const moy_input_t *t, uint32_t h) {
    int s = slot_of(t, h);
    return s < 0 ? 0 : t->front->src[s].pbuttons;
}

uint32_t moy_input_source_held(const moy_input_t *t, uint32_t h) {
    int s = slot_of(t, h);
    return s < 0 ? 0 : t->front->src[s].held;
}

int32_t moy_input_source_typing(const moy_input_t *t, uint32_t h) {
    int s = slot_of(t, h);
    return s < 0 ? 0 : t->front->src[s].typing;
}

int moy_input_point(moy_input_t *t, uint32_t h, int32_t x, int32_t y, bool down, bool edge,
                    bool fresh) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    moy_input_latch_t *l = &t->front->src[s];
    LOCK(t);
    l->px = (int16_t)(x < INT16_MIN ? INT16_MIN : (x > INT16_MAX ? INT16_MAX : x));
    l->py = (int16_t)(y < INT16_MIN ? INT16_MIN : (y > INT16_MAX ? INT16_MAX : y));
    l->pflags = (uint8_t)(P_SET | (down ? P_DOWN : 0) | (edge ? P_EDGE : 0) | (fresh ? P_FRESH : 0)
                          | (l->pflags & P_EDGE));
    l->pseq = ++t->front->pseq;
    UNLOCK(t);
    return MOY_INPUT_OK;
}

// The next one-shot key for this frame: none when the queue is empty, and a
// zero frame between two equal keys.
static int32_t dequeue_locked(moy_input_latch_t *l) {
    if (l->qn == 0 || l->q[0] == l->qlast) {
        l->qlast = 0;
        return 0;
    }
    int32_t k = l->q[0];
    memmove(&l->q[0], &l->q[1], (size_t)(l->qn - 1) * sizeof(l->q[0]));
    l->qn--;
    l->qlast = k;
    return k;
}

// -- the frame -------------------------------------------------------------------

// Is a's sample the better one: a source that is down beats one that is not,
// then the newer wins (pseq wraps; differences stay small).
static bool better(const moy_input_latch_t *a, const moy_input_latch_t *b) {
    if (b == NULL) {
        return true;
    }
    bool ad = (a->pflags & P_DOWN) != 0;
    bool bd = (b->pflags & P_DOWN) != 0;
    if (ad != bd) {
        return ad;
    }
    return (int32_t)(a->pseq - b->pseq) > 0;
}

// Repeats owed by a level held since before this merge, its next one due at
// *due: one a period from the delay on, the clock never more than a
// saturated count behind.
static uint32_t owed(const moy_input_t *t, uint32_t *due, uint32_t now) {
    int32_t late = moy_input_ticks_diff(now, *due);
    if (late < 0 || t->rep_rate <= 0) {
        return 0;
    }
    uint32_t n = (uint32_t)late / (uint32_t)t->rep_rate + 1u;
    if (n > MOY_INPUT_REP_MAX) {
        n = MOY_INPUT_REP_MAX;
    }
    *due = (*due + n * (uint32_t)t->rep_rate) & (MOY_INPUT_TICKS_PERIOD - 1u);
    if (moy_input_ticks_diff(now, *due) >= 0) {
        *due = (now + (uint32_t)t->rep_rate) & (MOY_INPUT_TICKS_PERIOD - 1u);
    }
    return n;
}

// The key a typing level repeats: one that types a byte, with no chord.
static int32_t repeats_as(int32_t ev) {
    if (MOY_INPUT_MODS(ev) & (MOY_INPUT_MOD_CTRL | MOY_INPUT_MOD_ALT | MOY_INPUT_MOD_SUPER)) {
        return 0;
    }
    return moy_input_typed(ev) ? ev : 0;
}

static uint32_t later(const moy_input_t *t, uint32_t now) {
    return (now + (uint32_t)t->rep_delay) & (MOY_INPUT_TICKS_PERIOD - 1u);
}

void moy_input_begin_frame(moy_input_t *t, uint32_t now) {
    uint32_t held = 0;
    uint32_t was[MOY_INPUT_SOURCES];
    int32_t typing[MOY_INPUT_SOURCES];
    uint32_t ph[MOY_INPUT_PLAYERS] = {0};
    const moy_input_latch_t *best = NULL;
    moy_input_sample_t smp = {0};
    uint8_t mods = 0, pb = 0;
    int32_t wx = 0, wy = 0;
    LOCK(t);
    for (uint8_t i = 0; i < t->nsrc; i++) {
        moy_input_latch_t *l = &t->front->src[i];
        if (l->queued) {
            key_locked(t, i, dequeue_locked(l));
        }
        mods |= l->mods;
        pb |= l->pbuttons | l->pbseen;     // a click between merges is held for one
        l->pbseen = 0;
        wx += l->wx;
        wy += l->wy;
        l->wx = l->wy = 0;
        held |= l->held;
        ph[t->player[i]] |= l->held;
        if ((l->pflags & P_SET) && better(l, best)) {
            best = l;
        }
        // news: a button going down, a new key, or a press of its pointer
        if ((l->held & ~t->was[i]) || (l->key != 0 && l->key != t->was_key[i])
            || (l->pflags & (P_DOWN | P_EDGE)) == (P_DOWN | P_EDGE)) {
            t->news = (uint8_t)(i + 1);
        }
        was[i] = t->was[i];
        typing[i] = repeats_as(l->typing);
        t->was[i] = l->held;
        t->was_key[i] = l->key;
    }
    if (best != NULL) {
        smp.any = true;
        smp.down = (best->pflags & P_DOWN) != 0;
        smp.edge = smp.down && (best->pflags & P_EDGE) != 0;
        smp.fresh = (best->pflags & P_FRESH) != 0;
        smp.x = best->px;
        smp.y = best->py;
    }
    for (uint8_t i = 0; i < t->nsrc; i++) {
        t->front->src[i].pflags &= (uint8_t)~P_EDGE;
    }
    UNLOCK(t);
    t->mods = mods;
    t->pb_pressed = (uint8_t)(pb & ~t->pb_held);
    t->pb_held = pb;
    t->wheel_x = wx;
    t->wheel_y = wy;
    t->smp = smp;
    t->held = held;
    t->pressed = held & ~t->last;
    t->released = t->last & ~held;
    t->last = held;
    t->taken = false;
    for (uint8_t p = 0; p < MOY_INPUT_PLAYERS; p++) {
        t->p_held[p] = ph[p];
        t->p_pressed[p] = ph[p] & ~t->p_last[p];
        t->p_last[p] = ph[p];
    }
    // the repeats: a level new at this merge starts its delay
    now &= MOY_INPUT_TICKS_PERIOD - 1u;
    bool clock = now != 0 && t->now != 0;   // a clock that just started arms anew
    t->now = now;
    memset(t->rep, 0, sizeof(t->rep));
    t->krep = 0;
    t->krep_ev = 0;
    for (uint8_t i = 0; i < t->nsrc; i++) {
        uint32_t h = t->was[i];
        for (uint8_t b = 0; h >> b; b++) {
            if (!(h & (1u << b))) {
                continue;
            }
            if (!(was[i] & (1u << b)) || !clock) {
                t->rep_due[i][b] = later(t, now);
                continue;
            }
            uint32_t n = owed(t, &t->rep_due[i][b], now);
            if (n > t->rep[b]) {
                t->rep[b] = (uint8_t)n;
            }
        }
        int32_t k = typing[i];
        if (k != t->krep_key[i] || !clock) {
            t->krep_key[i] = k;
            t->krep_due[i] = later(t, now);
        } else if (k) {
            uint32_t n = owed(t, &t->krep_due[i], now);
            if (n > t->krep) {
                t->krep = (uint8_t)n;
                t->krep_ev = k;
            }
        }
    }
}

void moy_input_set_repeat(moy_input_t *t, int32_t delay_ms, int32_t rate_ms) {
    t->rep_delay = delay_ms < 0 ? 0 : delay_ms;
    t->rep_rate = rate_ms < 0 ? 0 : rate_ms;
}

void moy_input_get_repeat(const moy_input_t *t, int32_t *delay_ms, int32_t *rate_ms) {
    *delay_ms = t->rep_delay;
    *rate_ms = t->rep_rate;
}

int moy_input_add_steps(moy_input_t *t, uint8_t button, uint32_t n) {
    if (button >= t->nbuttons) {
        return MOY_INPUT_BAD;
    }
    uint32_t v = t->rep[button] + n;
    t->rep[button] = (uint8_t)(v > MOY_INPUT_REP_MAX ? MOY_INPUT_REP_MAX : v);
    return MOY_INPUT_OK;
}

int32_t moy_input_ball_coast(int32_t *rate) {
    if (rate != NULL) {
        *rate = MOY_INPUT_BALL_COAST_RATE;
    }
    return MOY_INPUT_BALL_COAST;
}

uint32_t moy_input_repeats(const moy_input_t *t, uint8_t button) {
    return button < MOY_INPUT_BUTTONS ? t->rep[button] : 0;
}

uint32_t moy_input_rep_word(const moy_input_t *t) {
    return (uint32_t)t->rep[0] | (uint32_t)t->rep[1] << 8 | (uint32_t)t->rep[2] << 16
           | (uint32_t)t->rep[3] << 24;
}

uint32_t moy_input_key_repeats(const moy_input_t *t, int32_t *ev) {
    if (ev != NULL) {
        *ev = t->krep_ev;
    }
    return t->krep;
}

int moy_input_news(moy_input_t *t, uint32_t *h) {
    if (t->news == 0) {
        return 0;
    }
    *h = HANDLE(t->news - 1u);
    t->news = 0;
    return 1;
}

void moy_input_release_all(moy_input_t *t) {
    LOCK(t);
    for (uint8_t i = 0; i < t->nsrc; i++) {
        t->front->src[i].held = 0;
        t->front->src[i].mods = 0;
        t->front->src[i].pbuttons = 0;
    }
    UNLOCK(t);
    t->held = 0;
    t->mods = 0;
    t->pb_held = 0;
    memset(t->p_held, 0, sizeof(t->p_held));
}

void moy_input_spend_edges(moy_input_t *t) {
    memset(t->rep, 0, sizeof(t->rep));
    t->krep = 0;
    t->krep_ev = 0;
    t->pb_pressed = 0;
    t->wheel_x = t->wheel_y = 0;
    t->pressed = 0;
    t->released = 0;
    memset(t->p_pressed, 0, sizeof(t->p_pressed));
}

void moy_input_clear_edges(moy_input_t *t) {
    moy_input_spend_edges(t);
    for (uint8_t i = 0; i < t->nsrc; i++) {
        for (uint8_t b = 0; b < MOY_INPUT_BUTTONS; b++) {
            t->rep_due[i][b] = later(t, t->now);
        }
        t->krep_due[i] = later(t, t->now);
    }
}

void moy_input_keep_edges(moy_input_t *t) {
    t->kept |= t->pressed;
    for (uint8_t p = 0; p < MOY_INPUT_PLAYERS; p++) {
        t->p_kept[p] |= t->p_pressed[p];
    }
}

void moy_input_tick_edges(moy_input_t *t) {
    if (t->taken) {
        t->pressed = 0;
        memset(t->p_pressed, 0, sizeof(t->p_pressed));
        return;
    }
    t->taken = true;
    t->pressed |= t->kept;
    t->kept = 0;
    for (uint8_t p = 0; p < MOY_INPUT_PLAYERS; p++) {
        t->p_pressed[p] |= t->p_kept[p];
        t->p_kept[p] = 0;
    }
}

void moy_input_drop_edges(moy_input_t *t) {
    t->kept = 0;
    memset(t->p_kept, 0, sizeof(t->p_kept));
}

// -- the reads -------------------------------------------------------------------

void moy_input_masks(const moy_input_t *t, uint8_t player, uint32_t *held, uint32_t *pressed) {
    if (player == MOY_INPUT_UNION || (!t->multi && player == t->solo)) {
        *held = t->held;
        *pressed = t->pressed;
    } else if (!t->multi || player >= MOY_INPUT_PLAYERS) {
        *held = 0;
        *pressed = 0;
    } else {
        *held = t->p_held[player];
        *pressed = t->p_pressed[player];
    }
}

uint32_t moy_input_released(const moy_input_t *t) {
    return t->released;
}

void moy_input_sample(const moy_input_t *t, moy_input_sample_t *out) {
    *out = t->smp;
}

uint32_t moy_input_kept(const moy_input_t *t) {
    return t->kept;
}

int32_t moy_input_typed(int32_t ev) {
    int32_t c = MOY_INPUT_CODE(ev);
    uint8_t m = MOY_INPUT_MODS(ev);
    if (c > 0xFF || (m & (MOY_INPUT_MOD_ALT | MOY_INPUT_MOD_SUPER))) {
        return 0;
    }
    if (m & MOY_INPUT_MOD_CTRL) {
        if (c >= 'a' && c <= 'z') {
            return c - 'a' + 1;
        }
        return c >= 'A' && c <= 'Z' ? c - 'A' + 1 : 0;
    }
    return c;
}

int32_t moy_input_last_key(const moy_input_t *t) {
    return moy_input_typed(t->front->last_key);
}

int32_t moy_input_key_event(const moy_input_t *t) {
    return t->front->last_key;
}

uint8_t moy_input_mods(const moy_input_t *t) {
    return (uint8_t)(t->mods | MOY_INPUT_MODS(t->front->last_key));
}

void moy_input_wheel(const moy_input_t *t, int32_t *dx, int32_t *dy) {
    *dx = t->wheel_x;
    *dy = t->wheel_y;
}

uint8_t moy_input_pointer_buttons(const moy_input_t *t, uint8_t *pressed) {
    if (pressed != NULL) {
        *pressed = t->pb_pressed;
    }
    return t->pb_held;
}

bool moy_input_cart_keys(const moy_input_t *t) {
    return t->cart_keys;
}

void moy_input_set_cart_keys(moy_input_t *t, bool on) {
    t->cart_keys = on;
}

void moy_input_set_last_key(moy_input_t *t, int32_t key) {
    LOCK(t);
    t->front->last_key = key;
    UNLOCK(t);
}

uint8_t moy_input_players(const moy_input_t *t, uint8_t *out) {
    if (!t->multi) {
        out[0] = t->solo;
        return 1;
    }
    uint8_t n = 0;
    for (uint8_t i = 0; i < t->nsrc; i++) {
        uint8_t p = t->player[i];
        bool seen = false;
        for (uint8_t j = 0; j < n; j++) {
            seen = seen || out[j] == p;
        }
        if (!seen) {
            out[n++] = p;
        }
    }
    return n;
}

bool moy_input_multi(const moy_input_t *t) {
    return t->multi;
}

bool moy_input_text_mode(const moy_input_t *t) {
    return t->text_mode;
}

void moy_input_set_text_mode(moy_input_t *t, bool on) {
    t->text_mode = on;
}

// -- the pointer -----------------------------------------------------------------

static int32_t clamp(int32_t v, int32_t hi) {
    return v < 0 ? 0 : (v > hi ? hi : v);
}

void moy_input_ptr_init(moy_input_ptr_t *p, int32_t w, int32_t h, int32_t idle_ms, uint32_t now) {
    memset(p, 0, sizeof(*p));
    p->w = w;
    p->h = h;
    p->x = w / 2;
    p->y = h / 2;
    p->idle_ms = idle_ms;
    p->last_move = now;
    p->fresh = true;
    p->visible = true;
    p->div = 1;
}

void moy_input_ptr_move(moy_input_ptr_t *p, int32_t dx, int32_t dy, uint32_t now) {
    p->x = clamp(p->x + dx, p->w - 1);
    p->y = clamp(p->y + dy, p->h - 1);
    p->visible = true;
    p->last_move = now;
    p->sampled = now;
    p->touched = true;
}

void moy_input_ptr_place(moy_input_ptr_t *p, int32_t x, int32_t y, uint32_t now) {
    if (p->div > 1) {
        x /= p->div;
        y /= p->div;
    }
    p->x = clamp(x, p->w - 1);
    p->y = clamp(y, p->h - 1);
    p->visible = false;
    p->sampled = now;
    p->touched = true;
}

bool moy_input_ptr_live(const moy_input_ptr_t *p, uint32_t now) {
    return p->down || p->hovers
           || (p->touched && moy_input_ticks_diff(now, p->sampled) < MOY_INPUT_POINTER_LINGER_MS);
}

void moy_input_ptr_tick(moy_input_ptr_t *p, uint32_t now) {
    if (p->visible && moy_input_ticks_diff(now, p->last_move) >= p->idle_ms) {
        p->visible = false;
    }
}

int moy_input_ptr_apply(const moy_input_t *t, moy_input_ptr_t *p, uint32_t now) {
    const moy_input_sample_t *s = &t->smp;
    if (!s->any) {
        return 0;
    }
    p->down = s->down;
    p->fresh = s->fresh;
    if (!s->down) {
        return 0;
    }
    moy_input_ptr_place(p, s->x, s->y, now);
    return MOY_INPUT_P_HELD | (s->edge ? MOY_INPUT_P_CLICK : 0);
}
