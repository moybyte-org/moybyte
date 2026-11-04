// moy_input: the merged input table. moy_input.h has the contract.

#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99
#endif

#include "moy_input.h"

#include <string.h>

#include "moy_hid.h"
#include "moy_htab.h"

#ifdef MOY_INPUT_BOARD
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#define LOCK(t) portENTER_CRITICAL_SAFE(&(t)->front->mux)
#define UNLOCK(t) portEXIT_CRITICAL_SAFE(&(t)->front->mux)
#else
#include <stdlib.h>
#include <time.h>
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

// A source's event ring (moy_event.h): `n` records, the newest at seq
// head - 1, each at seq & mask. A `late` ring (an ISR ring source's) is
// written without the readers' state, so its drops are counted as readers
// find them: `kinds` is every kind it has held, `seen` the seq below which
// every drop is counted.
struct moy_input_ring {
    uint32_t head;                  // the seq the next record takes
    uint32_t mask;                  // depth - 1
    uint32_t n;                     // records held, at most the depth
    uint32_t drops;                 // records overwritten before a reader read them
    uint32_t kinds;
    uint32_t seen;
    uint16_t src;                   // its records' `src`
    uint8_t prov;
    bool internal;                  // internal RAM: an ISR writes it
    bool late;
    moy_event_t rec[];
};

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
    char name[MOY_INPUT_RINGS][MOY_INPUT_NAME + 1];     // the latch sources', then the ring sources'
    uint8_t gen[MOY_INPUT_RINGS];                       // a ring source's generation
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
    // the event rings and their readers, written under the lock
    moy_input_ring_t *ring[MOY_INPUT_RINGS];   // the latch sources', then the ring sources'
    uint8_t ev_used;                        // a bit a reader
    uint32_t ev_kinds[MOY_EV_READERS];
    uint8_t ev_only[MOY_EV_READERS];        // the one source a reader reads, slot + 1; 0: every
    uint32_t ev_cur[MOY_EV_READERS][MOY_INPUT_RINGS];    // the seq each reads next
    uint32_t ev_lost[MOY_EV_READERS][MOY_INPUT_RINGS];   // its records dropped unread
};

#define ONLY_NONE 0xFFu      // a reader's one source closed: it reads nothing

#define HANDLE(slot) ((1u << MOY_HTAB_GEN_SHIFT) | ((uint32_t)MOY_KIND_SRC << MOY_HTAB_KIND_SHIFT) | (slot))

static int slot_of(const moy_input_t *t, uint32_t h) {
    uint32_t slot = h & 0xFFu;
    if (h != HANDLE(slot) || slot >= t->nsrc) {
        return -1;
    }
    return (int)slot;
}

#define RING_HANDLE(t, slot) (((uint32_t)(t)->gen[slot] << MOY_HTAB_GEN_SHIFT) \
                              | ((uint32_t)MOY_KIND_SRC << MOY_HTAB_KIND_SHIFT) | (slot))

// A ring source's slot, or -1.
static int ring_slot_of(const moy_input_t *t, uint32_t h) {
    uint32_t slot = h & 0xFFu;
    if (slot < MOY_INPUT_SOURCES || slot >= MOY_INPUT_RINGS || t->ring[slot] == NULL
        || h != RING_HANDLE(t, slot)) {
        return -1;
    }
    return (int)slot;
}

// Either kind of source's slot, for what both have: a ring.
static int any_slot_of(const moy_input_t *t, uint32_t h) {
    int s = slot_of(t, h);
    return s >= 0 ? s : ring_slot_of(t, h);
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
// A ring an ISR writes is internal, as the latches are; any other is the
// table's (PSRAM).
static void *ring_alloc(size_t n, bool internal) {
    return internal ? heap_caps_calloc(1, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) : tab_alloc(n);
}
static void ring_free(void *p, bool internal) {
    if (internal) {
        heap_caps_free(p);
    } else {
        tab_free(p);
    }
}
static uint64_t clock_us(void) {
    return (uint64_t)esp_timer_get_time();
}
#define ISR_TEXT IRAM_ATTR
#define ISR_CLOCK() esp_timer_get_time()    // IRAM in ESP-IDF
#define ISR_LOCK(w) portENTER_CRITICAL_SAFE((portMUX_TYPE *)(w)->mux)
#define ISR_UNLOCK(w) portEXIT_CRITICAL_SAFE((portMUX_TYPE *)(w)->mux)
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
static void *ring_alloc(size_t n, bool internal) {
    (void)internal;
    return calloc(1, n);
}
static void ring_free(void *p, bool internal) {
    (void)internal;
    free(p);
}
static uint64_t clock_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}
#define ISR_TEXT
#define ISR_CLOCK() clock_us()
#define ISR_LOCK(w) ((void)(w))
#define ISR_UNLOCK(w) ((void)(w))
#endif

static uint32_t s_pinned_us;

uint32_t moy_input_now_us(void) {
    return s_pinned_us ? s_pinned_us : (uint32_t)clock_us();
}

void moy_input_pin_clock(uint32_t us) {
    s_pinned_us = us;
}

static uint32_t depth_of(uint32_t depth) {
    uint32_t d = 1;
    while (d < depth && d < MOY_EV_DEPTH_MAX) {
        d <<= 1;
    }
    return d;
}

// A ring a task writes is a take from THE SHARES' RINGS row
// (native/moy_play/moy_play.h, MOY_SHARE_RINGS): over the row, the source has
// no ring. Weak: an image without the Player counts nothing.
#define RINGS_ROW 2u
int moy_share_take(uint8_t row, uint32_t n) __attribute__((weak));
void moy_share_give(uint8_t row, uint32_t n) __attribute__((weak));

static uint32_t ring_bytes(uint32_t depth) {
    return (uint32_t)(sizeof(moy_input_ring_t) + depth * sizeof(moy_event_t));
}

static moy_input_ring_t *ring_new(uint8_t prov, uint32_t depth, uint16_t src) {
    depth = depth_of(depth);
    bool internal = prov == MOY_EV_ISR;
    if (internal && depth > MOY_EV_ISR_DEPTH_MAX) {
        depth = MOY_EV_ISR_DEPTH_MAX;
    }
    uint32_t n = ring_bytes(depth);
    if (!internal && moy_share_take && moy_share_take(RINGS_ROW, n) != 0) {
        return NULL;
    }
    moy_input_ring_t *g = ring_alloc(n, internal);
    if (g != NULL) {
        g->mask = depth - 1u;
        g->prov = prov;
        g->internal = internal;
        g->src = src;
    } else if (!internal && moy_share_give) {
        moy_share_give(RINGS_ROW, n);
    }
    return g;
}

static void ring_drop(moy_input_ring_t *g) {
    if (!g->internal && moy_share_give) {
        moy_share_give(RINGS_ROW, ring_bytes(g->mask + 1u));
    }
    ring_free(g, g->internal);
}

#define SRC16(s) ((uint16_t)((1u << 8) | (s)))

// The record at the ring's head, stamped now; a full ring's oldest goes.
__attribute__((always_inline)) static inline void ring_put(moy_input_ring_t *g, uint32_t now,
                                                          uint8_t kind, uint16_t a, uint16_t id,
                                                          uint32_t b) {
    if (g->n <= g->mask) {
        g->n++;
    }
    moy_event_t *e = &g->rec[g->head & g->mask];
    e->t_us = now;
    e->src = g->src;
    e->kind = kind;
    e->prov = g->prov;
    e->a = a;
    e->id = id;
    e->b = b;
    g->kinds |= MOY_EV_BIT(kind);
    g->head++;
}

// One record into source s's ring, under the lock. A full ring drops its
// oldest record; the drop is counted against the source and against each
// reader of that kind that had not read it (a late ring's, as readers find it).
static void put_locked(moy_input_t *t, int s, uint8_t kind, uint16_t a, uint16_t id, uint32_t b) {
    moy_input_ring_t *g = t->ring[s];
    if (g == NULL) {
        return;
    }
    if (g->n > g->mask && !g->late) {
        uint32_t old = g->head - g->n;
        uint32_t bit = MOY_EV_BIT(g->rec[old & g->mask].kind);
        bool dropped = false;
        for (uint8_t r = 0; r < MOY_EV_READERS; r++) {
            if ((t->ev_used & (1u << r)) && (t->ev_kinds[r] & bit)
                && (int32_t)(t->ev_cur[r][s] - old) <= 0) {
                t->ev_lost[r][s]++;
                dropped = true;
            }
        }
        g->drops += dropped;
    }
    ring_put(g, moy_input_now_us(), kind, a, id, b);
}

static void put_buttons_locked(moy_input_t *t, int s, uint32_t was, uint32_t now) {
    for (uint32_t d = was ^ now, i = 0; d >> i; i++) {
        if (d & (1u << i)) {
            put_locked(t, s, MOY_EV_BTN, (uint16_t)i, 0, (now >> i) & 1u);
        }
    }
}

// A key record; `usage` is moy_hid_usage(ev), found before the lock is taken.
static void put_key_locked(moy_input_t *t, int s, uint8_t kind, int32_t ev, uint16_t usage) {
    put_locked(t, s, kind, usage, 0, (uint32_t)ev);
    int32_t c = kind == MOY_EV_KEY_DOWN ? moy_input_typed(ev) : 0;
    if (c) {
        put_locked(t, s, MOY_EV_TEXT, 0, 0, (uint32_t)c);
    }
}

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
    for (uint8_t i = 0; i < MOY_INPUT_RINGS; i++) {
        if (t->ring[i] != NULL) {
            ring_drop(t->ring[i]);
        }
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
    t->ring[i] = ring_new(MOY_EV_POLL, MOY_EV_DEPTH, SRC16(i));
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
    uint32_t was = l->held;
    if (held) {
        l->held |= 1u << button;
    } else {
        l->held &= ~(1u << button);
    }
    put_buttons_locked(t, s, was, l->held);
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
    put_buttons_locked(t, s, t->front->src[s].held, held);
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
    // a source's key is its one producer's, so the level read here is the
    // one the lock finds
    int32_t was = t->front->src[s].key;
    uint16_t u_was = was && was != key ? moy_hid_usage(was) : 0;
    uint16_t u_key = key && key != was ? moy_hid_usage(key) : 0;
    LOCK(t);
    t->front->src[s].queued = 0;
    t->front->src[s].qn = 0;
    int32_t old = t->front->src[s].key;
    if (key != old) {
        if (old) {
            put_key_locked(t, s, MOY_EV_KEY_UP, old, old == was ? u_was : moy_hid_usage(old));
        }
        if (key) {
            put_key_locked(t, s, MOY_EV_KEY_DOWN, key, key != was ? u_key : moy_hid_usage(key));
        }
    }
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
    uint16_t usage = moy_hid_usage(key);
    LOCK(t);
    put_key_locked(t, s, MOY_EV_KEY_DOWN, key, usage);
    put_key_locked(t, s, MOY_EV_KEY_UP, key, usage);
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
    bool was = (l->pflags & P_DOWN) != 0;
    int16_t cx = sat16(x), cy = sat16(y);
    uint32_t at = (uint32_t)(uint16_t)cx << 16 | (uint16_t)cy;
    if (down && (!was || edge)) {
        if (was) {
            put_locked(t, s, MOY_EV_TOUCH_UP, 0, 0, (uint32_t)(uint16_t)l->px << 16 | (uint16_t)l->py);
        }
        put_locked(t, s, MOY_EV_TOUCH_DOWN, 0, 0, at);
    } else if (down && (cx != l->px || cy != l->py)) {
        put_locked(t, s, MOY_EV_TOUCH_MOVE, 0, 0, at);
    } else if (!down && was) {
        put_locked(t, s, MOY_EV_TOUCH_UP, 0, 0, at);
    }
    l->px = cx;
    l->py = cy;
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

// -- the event rings ----------------------------------------------------------------

int moy_input_source_events(moy_input_t *t, uint32_t h, uint8_t prov, uint32_t depth) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (prov > MOY_EV_POLL || depth == 0) {
        return MOY_INPUT_BAD;
    }
    moy_input_ring_t *g = t->ring[s];
    if (g != NULL && g->prov == prov && g->mask + 1u == depth_of(depth)) {
        return MOY_INPUT_OK;
    }
    moy_input_ring_t *n = ring_new(prov, depth, SRC16(s));
    if (n == NULL) {
        return MOY_INPUT_NOMEM;
    }
    LOCK(t);
    t->ring[s] = n;
    for (uint8_t r = 0; r < MOY_EV_READERS; r++) {
        t->ev_cur[r][s] = 0;
        t->ev_lost[r][s] = 0;
    }
    UNLOCK(t);
    if (g != NULL) {
        ring_drop(g);
    }
    return MOY_INPUT_OK;
}

int moy_input_post(moy_input_t *t, uint32_t h, uint8_t kind, uint16_t a, uint16_t id, uint32_t b) {
    int s = any_slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (kind == MOY_EV_NONE || kind >= MOY_EV_LOST) {
        return MOY_INPUT_BAD;
    }
    LOCK(t);
    put_locked(t, s, kind, a, id, b);
    UNLOCK(t);
    return MOY_INPUT_OK;
}

int moy_input_ring_info(const moy_input_t *t, uint32_t h, moy_input_ring_info_t *out) {
    int s = any_slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    const moy_input_ring_t *g = t->ring[s];
    if (g == NULL) {
        return MOY_INPUT_NOMEM;
    }
    out->depth = g->mask + 1u;
    out->head = g->head;
    out->count = g->n;
    out->drops = g->drops;
    out->prov = g->prov;
    out->internal = g->internal;
    out->src = g->src;
    return MOY_INPUT_OK;
}

int moy_input_src_name(const moy_input_t *t, uint16_t src, char *out, size_t n) {
    uint8_t s = src & 0xFFu;
    if (s >= MOY_INPUT_RINGS || t->ring[s] == NULL || t->ring[s]->src != src
        || (s < MOY_INPUT_SOURCES && s >= t->nsrc)) {
        return MOY_INPUT_STALE;
    }
    size_t len = strlen(t->name[s]);
    if (len + 1 > n) {
        return MOY_INPUT_BAD;
    }
    memcpy(out, t->name[s], len + 1);
    return MOY_INPUT_OK;
}

// -- ring sources ---------------------------------------------------------------------

int moy_input_ring_open(moy_input_t *t, const char *name, uint8_t prov, uint32_t depth,
                        uint32_t *h) {
    size_t len = strlen(name);
    if (len == 0 || len > MOY_INPUT_NAME || prov > MOY_EV_POLL || depth == 0) {
        return MOY_INPUT_BAD;
    }
    uint8_t s = MOY_INPUT_SOURCES;
    while (s < MOY_INPUT_RINGS && t->ring[s] != NULL) {
        s++;
    }
    if (s == MOY_INPUT_RINGS) {
        return MOY_INPUT_FULL;
    }
    uint8_t gen = (uint8_t)(t->gen[s] + 1u);
    gen = gen ? gen : 1u;
    moy_input_ring_t *g = ring_new(prov, depth, (uint16_t)((uint16_t)gen << 8 | s));
    if (g == NULL) {
        return MOY_INPUT_NOMEM;
    }
    g->late = prov == MOY_EV_ISR;
    memcpy(t->name[s], name, len + 1);
    LOCK(t);
    t->gen[s] = gen;
    t->ring[s] = g;
    for (uint8_t r = 0; r < MOY_EV_READERS; r++) {
        t->ev_cur[r][s] = 0;
        t->ev_lost[r][s] = 0;
    }
    UNLOCK(t);
    *h = RING_HANDLE(t, s);
    return MOY_INPUT_OK;
}

int moy_input_ring_close(moy_input_t *t, uint32_t h) {
    int s = ring_slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    moy_input_ring_t *g = t->ring[s];
    t->ring[s] = NULL;
    for (uint8_t r = 0; r < MOY_EV_READERS; r++) {
        if (t->ev_only[r] == (uint8_t)(s + 1)) {
            t->ev_only[r] = ONLY_NONE;
        }
    }
    UNLOCK(t);
    ring_drop(g);
    return MOY_INPUT_OK;
}

int moy_input_isr_attach(moy_input_t *t, uint32_t h, moy_input_isr_t *w) {
    int s = ring_slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    if (!t->ring[s]->late) {
        return MOY_INPUT_BAD;
    }
#ifdef MOY_INPUT_BOARD
    w->mux = &t->front->mux;
#else
    w->mux = NULL;
#endif
    LOCK(t);
    w->ring = t->ring[s];
    UNLOCK(t);
    return MOY_INPUT_OK;
}

void moy_input_isr_detach(moy_input_isr_t *w) {
    if (w->ring == NULL) {
        return;
    }
    ISR_LOCK(w);
    w->ring = NULL;
    ISR_UNLOCK(w);
}

void ISR_TEXT moy_input_isr_put(moy_input_isr_t *w, uint8_t kind, uint16_t a, uint16_t id,
                                uint32_t b) {
    uint32_t now = s_pinned_us ? s_pinned_us : (uint32_t)ISR_CLOCK();
    ISR_LOCK(w);
    moy_input_ring_t *g = w->ring;
    if (g != NULL) {
        ring_put(g, now, kind, a, id, b);
    }
    ISR_UNLOCK(w);
}

int moy_input_reader(moy_input_t *t, uint32_t kinds, int *r) {
    int got = MOY_INPUT_FULL;
    LOCK(t);
    for (uint8_t i = 0; i < MOY_EV_READERS; i++) {
        if (!(t->ev_used & (1u << i))) {
            t->ev_used |= (uint8_t)(1u << i);
            t->ev_kinds[i] = kinds & MOY_EV_ALL;
            t->ev_only[i] = 0;
            for (uint8_t s = 0; s < MOY_INPUT_RINGS; s++) {
                t->ev_cur[i][s] = t->ring[s] ? t->ring[s]->head : 0;
                t->ev_lost[i][s] = 0;
            }
            *r = i;
            got = MOY_INPUT_OK;
            break;
        }
    }
    UNLOCK(t);
    return got;
}

static bool reader_ok(const moy_input_t *t, int r) {
    return r >= 0 && r < (int)MOY_EV_READERS && (t->ev_used & (1u << r));
}

int moy_input_reader_only(moy_input_t *t, int r, uint32_t h) {
    if (!reader_ok(t, r)) {
        return MOY_INPUT_STALE;
    }
    int s = h == 0 ? -1 : any_slot_of(t, h);
    if (h != 0 && s < 0) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    t->ev_only[r] = (uint8_t)(s + 1);
    UNLOCK(t);
    return MOY_INPUT_OK;
}

// Does reader r read source slot s?
static inline bool reads(const moy_input_t *t, int r, uint8_t s) {
    return t->ev_only[r] == 0 || t->ev_only[r] == (uint8_t)(s + 1);
}

int moy_input_reader_free(moy_input_t *t, int r) {
    if (!reader_ok(t, r)) {
        return MOY_INPUT_STALE;
    }
    LOCK(t);
    t->ev_used &= (uint8_t)~(1u << r);
    UNLOCK(t);
    return MOY_INPUT_OK;
}

// A reader's cursor on source s moved up to the oldest record the ring holds;
// on a late ring, what it passed is the reader's gap, and the source's drops
// count what no reader had counted.
static uint32_t *cursor_locked(moy_input_t *t, int r, uint8_t s) {
    moy_input_ring_t *g = t->ring[s];
    uint32_t *c = &t->ev_cur[r][s];
    uint32_t oldest = g->head - g->n;
    if ((int32_t)(*c - oldest) < 0) {
        if (g->late && (t->ev_kinds[r] & g->kinds)) {
            t->ev_lost[r][s] += oldest - *c;
            uint32_t from = (int32_t)(g->seen - *c) > 0 ? g->seen : *c;
            if ((int32_t)(oldest - from) > 0) {
                g->drops += oldest - from;
                g->seen = oldest;
            }
        }
        *c = oldest;
    }
    return c;
}

size_t moy_input_events(moy_input_t *t, int r, moy_event_t *out, size_t n) {
    if (!reader_ok(t, r)) {
        return 0;
    }
    size_t k = 0;
    LOCK(t);
    uint32_t kinds = t->ev_kinds[r];
    while (k < n) {
        // the oldest record any source holds for this reader, its gap first
        int best = -1;
        uint32_t best_t = 0;
        for (uint8_t s = 0; s < MOY_INPUT_RINGS; s++) {
            const moy_input_ring_t *g = t->ring[s];
            if (g == NULL || !reads(t, r, s)) {
                continue;
            }
            uint32_t c = *cursor_locked(t, r, s);
            if (c == g->head) {
                continue;
            }
            uint32_t ts = g->rec[c & g->mask].t_us;
            if (best < 0 || (int32_t)(ts - best_t) < 0) {
                best = s;
                best_t = ts;
            }
        }
        if (best < 0) {
            break;
        }
        moy_input_ring_t *g = t->ring[best];
        if (t->ev_lost[r][best]) {
            moy_event_t *e = &out[k++];
            e->t_us = best_t;
            e->src = g->src;
            e->kind = MOY_EV_LOST;
            e->prov = g->prov;
            e->a = 0;
            e->id = 0;
            e->b = t->ev_lost[r][best];
            t->ev_lost[r][best] = 0;
            continue;
        }
        const moy_event_t *e = &g->rec[t->ev_cur[r][best]++ & g->mask];
        if (kinds & MOY_EV_BIT(e->kind)) {
            out[k++] = *e;
        }
    }
    UNLOCK(t);
    return k;
}

size_t moy_input_pending(moy_input_t *t, int r) {
    if (!reader_ok(t, r)) {
        return 0;
    }
    size_t k = 0;
    LOCK(t);
    uint32_t kinds = t->ev_kinds[r];
    for (uint8_t s = 0; s < MOY_INPUT_RINGS; s++) {
        const moy_input_ring_t *g = t->ring[s];
        if (g == NULL || !reads(t, r, s)) {
            continue;
        }
        uint32_t *c = cursor_locked(t, r, s);
        k += t->ev_lost[r][s] != 0;
        // a record this reader would skip is skipped now, so it never reads
        // as unread when the ring wraps
        while (*c != g->head && !(kinds & MOY_EV_BIT(g->rec[*c & g->mask].kind))) {
            (*c)++;
        }
        for (uint32_t i = *c; i != g->head; i++) {
            k += (kinds & MOY_EV_BIT(g->rec[i & g->mask].kind)) != 0;
        }
    }
    UNLOCK(t);
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
        put_buttons_locked(t, i, t->front->src[i].held, 0);
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
