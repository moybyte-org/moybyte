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
    "x", "y", "stop", "save", "share", "select", "start",
};

// A source's latch: what its producer wrote since the last merge. Internal RAM
// on a board, every field written under the table's spinlock.
typedef struct {
    uint32_t held;
    int32_t key;                    // the key this source holds now (a level)
    uint16_t q[MOY_INPUT_KEYQ];     // one-shot keys awaiting their frame
    uint8_t qn;
    uint8_t queued;                 // the queue drives `key` at every begin_frame
    uint16_t qlast;                 // the key the queue delivered last frame
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
    uint8_t player[MOY_INPUT_SOURCES];
    char name[MOY_INPUT_SOURCES][MOY_INPUT_NAME + 1];
    uint32_t held, last, pressed, released, kept;
    uint32_t p_held[MOY_INPUT_PLAYERS];
    uint32_t p_last[MOY_INPUT_PLAYERS];
    uint32_t p_pressed[MOY_INPUT_PLAYERS];
    uint32_t p_kept[MOY_INPUT_PLAYERS];
    moy_input_sample_t smp;
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
    if (key <= 0 || key > 0xFFFF) {
        return MOY_INPUT_BAD;
    }
    moy_input_latch_t *l = &t->front->src[s];
    int r = MOY_INPUT_OK;
    LOCK(t);
    l->queued = 1;
    if (l->qn < MOY_INPUT_KEYQ) {
        l->q[l->qn++] = (uint16_t)key;
    } else {
        r = MOY_INPUT_FULL;
    }
    UNLOCK(t);
    return r;
}

int moy_input_source_key(const moy_input_t *t, uint32_t h, int32_t *key) {
    int s = slot_of(t, h);
    if (s < 0) {
        return MOY_INPUT_STALE;
    }
    *key = t->front->src[s].key;
    return MOY_INPUT_OK;
}

uint32_t moy_input_source_held(const moy_input_t *t, uint32_t h) {
    int s = slot_of(t, h);
    return s < 0 ? 0 : t->front->src[s].held;
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
    uint16_t k = l->q[0];
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

void moy_input_begin_frame(moy_input_t *t) {
    uint32_t held = 0;
    uint32_t ph[MOY_INPUT_PLAYERS] = {0};
    const moy_input_latch_t *best = NULL;
    moy_input_sample_t smp = {0};
    LOCK(t);
    for (uint8_t i = 0; i < t->nsrc; i++) {
        moy_input_latch_t *l = &t->front->src[i];
        if (l->queued) {
            key_locked(t, i, dequeue_locked(l));
        }
        held |= l->held;
        ph[t->player[i]] |= l->held;
        if ((l->pflags & P_SET) && better(l, best)) {
            best = l;
        }
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
}

void moy_input_release_all(moy_input_t *t) {
    LOCK(t);
    for (uint8_t i = 0; i < t->nsrc; i++) {
        t->front->src[i].held = 0;
    }
    UNLOCK(t);
    t->held = 0;
    memset(t->p_held, 0, sizeof(t->p_held));
}

void moy_input_clear_edges(moy_input_t *t) {
    t->pressed = 0;
    t->released = 0;
    t->last = 0;
    memset(t->p_pressed, 0, sizeof(t->p_pressed));
    memset(t->p_last, 0, sizeof(t->p_last));
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

int32_t moy_input_last_key(const moy_input_t *t) {
    return t->front->last_key;
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
    p->sampled = now;
    p->last_move = now;
    p->fresh = true;
    p->visible = true;
}

void moy_input_ptr_move(moy_input_ptr_t *p, int32_t dx, int32_t dy, uint32_t now) {
    p->x = clamp(p->x + dx, p->w - 1);
    p->y = clamp(p->y + dy, p->h - 1);
    p->visible = true;
    p->last_move = now;
    p->sampled = now;
}

void moy_input_ptr_place(moy_input_ptr_t *p, int32_t x, int32_t y, uint32_t now) {
    p->x = clamp(x, p->w - 1);
    p->y = clamp(y, p->h - 1);
    p->visible = false;
    p->sampled = now;
}

bool moy_input_ptr_live(const moy_input_ptr_t *p, uint32_t now) {
    return p->down || p->hovers
           || moy_input_ticks_diff(now, p->sampled) < MOY_INPUT_POINTER_LINGER_MS;
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
