// moy_evq: every process's event queue and the one wait (moy_evq.h has the
// design).

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99
#endif

#include "moy_evq.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_super.h"
#include "moy_sync.h"

#ifdef MOY_SYNC_RTOS
#include "esp_heap_caps.h"
#include "esp_timer.h"
#else
#include <time.h>
#endif

// The kernel's frame (moy_loop.h's jobs): weak, for an image or a harness
// without the loop, where whoever stands in for the frame runs the timers.
bool moy_loop_job(bool (*fn)(void *), void *arg) __attribute__((weak));

#ifdef MOY_SYNC_THREADS
pthread_mutex_t moy_sync_mu = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t moy_sync_cv = PTHREAD_COND_INITIALIZER;
#endif

typedef struct {
    moy_evq_ev_t h;
    char args[];                // h.n bytes and a NUL
} ev_t;

typedef struct {
    char name[MOY_EVQ_NAME];
    uint16_t n;                 // its `on`s, 0: a free row
} listen_t;

typedef struct {
    uint32_t who;               // a sender (who_of)
    uint32_t at;                // the take that last served it, 0: none
} peer_t;

typedef struct {
    uint32_t id;                // 0: a free row
    uint64_t due;               // ms, the timers' clock (now_ms); an alarm's
                                // moves with the wall clock (moy_evq_clock)
    uint32_t every;             // 0: once
    bool alarm;                 // posts MOY_EVQ_AT, and wakes the screen
} evq_timer_t;

typedef struct {
    uint32_t flags;
    evq_timer_t tm[MOY_EVQ_TIMERS];
    char app[MOY_EVQ_APP];
    char *receives;             // one name a line, NUL ended; NULL: none
    listen_t on[MOY_EVQ_LISTEN];
    ev_t *ev[MOY_EVQ_DEPTH];    // oldest first
    uint8_t count;
    int8_t left;                // takes this step leaves; -1: never stepped
    uint32_t takes;
    peer_t peer[MOY_EVQ_PEERS];
} q_t;

_Static_assert(MOY_EVQ_DEPTH <= 255u && MOY_EVQ_BATCH <= 127u, "a queue's counts fit");

// A slot's pid, wait word and waiter live outside its queue, so a waiter
// blocked on a slot outlives the queue's close.
static q_t *s_q[MOY_EVQ_PROCS];
static uint32_t s_pid[MOY_EVQ_PROCS];
static volatile uint32_t s_seq[MOY_EVQ_PROCS];
static moy_sync_who_t s_who[MOY_EVQ_PROCS];
static uint32_t s_turn;
static volatile uint32_t s_want;    // THE VM'S WAKE's bits
static uint32_t s_timer_n;          // the last timer id handed out, its bit apart
static uint32_t s_timers;           // timers held, every queue's
static uint32_t s_woke[8];          // the pids whose alarms went off, oldest first
static uint32_t s_woke_n;
static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;

static void *mem(size_t n) {
    #ifdef MOY_SYNC_RTOS
    return heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    #else
    return calloc(1, n);
    #endif
}

static bool system_pid(uint32_t pid) {
    return pid <= 1u;
}

// A message's sender as BACKPRESSURE and IN TURN count it: pid 0 and 1 are
// one, the system (0).
static uint32_t who_of(uint32_t pid) {
    return system_pid(pid) ? 0u : pid;
}

// The slot of `pid`'s queue, or -1. Under the lock.
static int slot_of(uint32_t pid) {
    for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
        if (s_q[i] != NULL && s_pid[i] == pid) {
            return (int)i;
        }
    }
    return -1;
}

static void copy_name(char *dst, size_t cap, const char *src) {
    size_t n = src != NULL ? strlen(src) : 0;
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(dst, src != NULL ? src : "", n);
    dst[n] = 0;
}

static bool listens(const q_t *q, const char *name) {
    for (uint32_t i = 0; i < MOY_EVQ_LISTEN; i++) {
        if (q->on[i].n != 0 && strcmp(q->on[i].name, name) == 0) {
            return true;
        }
    }
    return false;
}

static bool receives(const q_t *q, const char *name) {
    const char *p = q->receives;
    size_t n = strlen(name);
    while (p != NULL && *p) {
        const char *e = strchr(p, '\n');
        size_t k = e != NULL ? (size_t)(e - p) : strlen(p);
        if (k == n && memcmp(p, name, n) == 0) {
            return true;
        }
        p = e != NULL ? e + 1 : NULL;
    }
    return false;
}

// Whether the rule lets a message `name` from app `app` (the system where
// `sys`) reach queue `q`.
static bool allowed(const q_t *q, bool sys, const char *app, const char *name) {
    return sys || strcmp(q->app, app) == 0 || receives(q, name);
}

// Event `k` of `q` out of its queue. Under the lock.
static ev_t *unlink_at(q_t *q, uint32_t k) {
    ev_t *e = q->ev[k];
    memmove(&q->ev[k], &q->ev[k + 1u], (q->count - k - 1u) * sizeof q->ev[0]);
    q->count--;
    q->ev[q->count] = NULL;
    return e;
}

// Event `e` queued on slot `i` under BACKPRESSURE: OK, or FULL with nothing
// queued; *old an event the system's post pushed out, to free. Under the lock.
static int push(uint32_t i, ev_t *e, ev_t **old) {
    q_t *q = s_q[i];
    uint32_t me = who_of(e->h.from);
    uint32_t mine = 0, first = MOY_EVQ_DEPTH;
    *old = NULL;
    for (uint32_t k = 0; k < q->count; k++) {
        if (who_of(q->ev[k]->h.from) == me) {
            first = mine++ == 0 ? k : first;
        }
    }
    if (mine >= MOY_EVQ_SENDER || q->count == MOY_EVQ_DEPTH) {
        if (me != 0u) {
            return MOY_EVQ_FULL;
        }
        *old = unlink_at(q, first < MOY_EVQ_DEPTH ? first : 0u);
    }
    q->ev[q->count++] = e;
    return MOY_EVQ_OK;
}

// The take that last served sender `who` of `q`, 0 for none. Under the lock.
static uint32_t served(const q_t *q, uint32_t who) {
    for (uint32_t k = 0; k < MOY_EVQ_PEERS; k++) {
        if (q->peer[k].at != 0u && q->peer[k].who == who) {
            return q->peer[k].at;
        }
    }
    return 0u;
}

// Sender `who` of `q` served now: its row, else the row served longest ago.
// Under the lock.
static void serve(q_t *q, uint32_t who) {
    if (++q->takes == 0u) {
        memset(q->peer, 0, sizeof q->peer);
        q->takes = 1u;
    }
    peer_t *row = &q->peer[0];
    for (uint32_t k = 0; k < MOY_EVQ_PEERS; k++) {
        peer_t *p = &q->peer[k];
        if (p->at != 0u && p->who == who) {
            row = p;
            break;
        }
        if (p->at < row->at) {
            row = p;
        }
    }
    row->who = who;
    row->at = q->takes;
}

// IN TURN: the index of the oldest event of the sender `q` served longest
// ago. Under the lock; `q` holds one at least.
static uint32_t next_of(const q_t *q) {
    uint32_t best = 0, best_at = UINT32_MAX;
    for (uint32_t k = 0; k < q->count && best_at != 0u; k++) {
        uint32_t at = served(q, who_of(q->ev[k]->h.from));
        if (at < best_at) {
            best = k;
            best_at = at;
        }
    }
    return best;
}

static ev_t *make(uint32_t from, const char *app, const char *name, const char *args, size_t n) {
    ev_t *e = mem(sizeof(ev_t) + n + 1u);
    if (e == NULL) {
        return NULL;
    }
    e->h.from = from;
    e->h.n = (uint32_t)n;
    copy_name(e->h.app, sizeof e->h.app, app);
    copy_name(e->h.name, sizeof e->h.name, name);
    if (n) {
        memcpy(e->args, args, n);
    }
    e->args[n] = 0;
    return e;
}

static void wake(uint32_t i) {
    moy_sync_wake(&s_seq[i], s_who[i]);
}

int moy_evq_open(uint32_t pid, const char *app, const char *recv, uint32_t flags) {
    char *r = NULL;
    if (recv != NULL && *recv) {
        size_t n = strlen(recv);
        r = mem(n + 1u);
        if (r == NULL) {
            return MOY_EVQ_NOMEM;
        }
        memcpy(r, recv, n);
    }
    q_t *fresh = mem(sizeof(q_t));
    if (fresh == NULL) {
        free(r);
        return MOY_EVQ_NOMEM;
    }
    char *old_r = NULL;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i >= 0) {
        q_t *q = s_q[i];
        old_r = q->receives;
        q->receives = r;
        q->flags = flags;
        copy_name(q->app, sizeof q->app, app);
        MOY_SYNC_UNLOCK(&s_lock);
        free(old_r);
        free(fresh);
        return MOY_EVQ_OK;
    }
    for (uint32_t k = 0; k < MOY_EVQ_PROCS; k++) {
        if (s_q[k] == NULL) {
            i = (int)k;
            break;
        }
    }
    if (i < 0) {
        MOY_SYNC_UNLOCK(&s_lock);
        free(r);
        free(fresh);
        return MOY_EVQ_FULL;
    }
    fresh->receives = r;
    fresh->flags = flags;
    fresh->left = -1;
    copy_name(fresh->app, sizeof fresh->app, app);
    s_pid[i] = pid;
    s_who[i] = NULL;
    s_q[i] = fresh;
    MOY_SYNC_UNLOCK(&s_lock);
    return MOY_EVQ_OK;
}

static uint32_t held_timers(const q_t *q) {
    uint32_t n = 0;
    for (uint32_t k = 0; k < MOY_EVQ_TIMERS; k++) {
        n += q->tm[k].id != 0u;
    }
    return n;
}

int moy_evq_close(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i < 0) {
        MOY_SYNC_UNLOCK(&s_lock);
        return MOY_EVQ_STALE;
    }
    q_t *q = s_q[i];
    s_timers -= held_timers(q);
    s_q[i] = NULL;
    s_pid[i] = 0;
    moy_sync_who_t who = s_who[i];
    s_who[i] = NULL;
    MOY_SYNC_UNLOCK(&s_lock);
    moy_sync_wake(&s_seq[i], who);
    for (uint32_t k = 0; k < q->count; k++) {
        free(q->ev[k]);
    }
    free(q->receives);
    free(q);
    return MOY_EVQ_OK;
}

bool moy_evq_has(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    bool got = slot_of(pid) >= 0;
    MOY_SYNC_UNLOCK(&s_lock);
    return got;
}

const char *moy_evq_app(uint32_t pid) {
    if (system_pid(pid)) {
        return MOY_EVQ_SYSTEM;
    }
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    const char *app = i >= 0 ? s_q[i]->app : NULL;
    MOY_SYNC_UNLOCK(&s_lock);
    return app;
}

int moy_evq_listen(uint32_t pid, const char *name, bool on) {
    int rc = MOY_EVQ_OK;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i < 0) {
        rc = MOY_EVQ_STALE;
    } else {
        q_t *q = s_q[i];
        listen_t *free_row = NULL;
        listen_t *row = NULL;
        for (uint32_t k = 0; k < MOY_EVQ_LISTEN; k++) {
            if (q->on[k].n == 0) {
                free_row = free_row != NULL ? free_row : &q->on[k];
            } else if (strncmp(q->on[k].name, name, MOY_EVQ_NAME) == 0) {
                row = &q->on[k];
            }
        }
        if (on) {
            if (row == NULL && free_row == NULL) {
                rc = MOY_EVQ_FULL;
            } else {
                if (row == NULL) {
                    row = free_row;
                    copy_name(row->name, sizeof row->name, name);
                }
                row->n++;
            }
        } else if (row != NULL) {
            row->n--;
        }
        if (rc == MOY_EVQ_OK && !(q->flags & MOY_EVQ_VM)) {
            moy_evq_want(MOY_EVQ_WANT_POLL);
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return rc;
}

bool moy_evq_hears(uint32_t pid, const char *name) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    bool got = i >= 0 && listens(s_q[i], name);
    MOY_SYNC_UNLOCK(&s_lock);
    return got;
}

// The sender's app copied into `app` under the lock: false where a process
// that is not the system has no queue.
static bool sender(uint32_t from, char *app) {
    if (system_pid(from)) {
        copy_name(app, MOY_EVQ_APP, MOY_EVQ_SYSTEM);
        return true;
    }
    int i = slot_of(from);
    if (i < 0) {
        return false;
    }
    copy_name(app, MOY_EVQ_APP, s_q[i]->app);
    return true;
}

int moy_evq_post(uint32_t to, uint32_t from, const char *name, const char *args, size_t n) {
    char app[MOY_EVQ_APP];
    MOY_SYNC_LOCK(&s_lock);
    bool ok = sender(from, app);
    MOY_SYNC_UNLOCK(&s_lock);
    if (!ok) {
        return MOY_EVQ_STALE;
    }
    ev_t *e = make(from, app, name, args, n);
    if (e == NULL) {
        return MOY_EVQ_NOMEM;
    }
    ev_t *old = NULL;
    int rc = MOY_EVQ_OK;
    bool vm = false;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(to);
    if (i < 0) {
        rc = MOY_EVQ_STALE;
    } else if (!listens(s_q[i], name)) {
        rc = MOY_EVQ_DEAF;
    } else if (!allowed(s_q[i], system_pid(from), app, name)) {
        rc = MOY_EVQ_DENIED;
    } else {
        rc = push((uint32_t)i, e, &old);
        if (rc == MOY_EVQ_OK) {
            e = NULL;
            vm = (s_q[i]->flags & MOY_EVQ_VM) != 0u;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    free(e);
    free(old);
    if (rc == MOY_EVQ_OK) {
        wake((uint32_t)i);
        if (vm) {
            moy_evq_want(MOY_EVQ_WANT_MAIL);
        }
    }
    return rc;
}

// Message `name` to every listening queue of app `to_app` (NULL: every
// one), one copy each: how many took it; *full counts the queues that were.
static int fan(uint32_t from, const char *app, const char *to_app, const char *name,
               const char *args, size_t n, uint32_t *denied, uint32_t *full) {
    int took = 0;
    uint32_t refused = 0, fulls = 0;
    bool sys = system_pid(from);
    for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
        ev_t *e = make(from, app, name, args, n);
        if (e == NULL) {
            break;
        }
        ev_t *old = NULL;
        bool sent = false, vm = false;
        MOY_SYNC_LOCK(&s_lock);
        q_t *q = s_q[i];
        if (q != NULL && s_pid[i] != from && listens(q, name)
            && (to_app == NULL || strcmp(q->app, to_app) == 0)) {
            if (!allowed(q, sys, app, name)) {
                refused++;
            } else if (push(i, e, &old) == MOY_EVQ_OK) {
                e = NULL;
                sent = true;
                vm = (q->flags & MOY_EVQ_VM) != 0u;
            } else {
                fulls++;
            }
        }
        MOY_SYNC_UNLOCK(&s_lock);
        free(e);
        free(old);
        if (sent) {
            wake(i);
            took++;
            if (vm) {
                moy_evq_want(MOY_EVQ_WANT_MAIL);
            }
        }
    }
    if (denied != NULL) {
        *denied = refused;
    }
    if (full != NULL) {
        *full = fulls;
    }
    return took;
}

int moy_evq_send(uint32_t from, const char *app, const char *name, const char *args, size_t n,
                 uint32_t *denied) {
    char me[MOY_EVQ_APP];
    MOY_SYNC_LOCK(&s_lock);
    bool ok = sender(from, me);
    MOY_SYNC_UNLOCK(&s_lock);
    if (!ok) {
        if (denied != NULL) {
            *denied = 0;
        }
        return -MOY_EVQ_STALE;
    }
    uint32_t full = 0;
    int took = fan(from, me, app != NULL ? app : "", name, args, n, denied, &full);
    return took == 0 && full != 0u ? -MOY_EVQ_FULL : took;
}

int moy_evq_route(const char *name, const char *args, size_t n) {
    return fan(0u, MOY_EVQ_SYSTEM, NULL, name, args, n, NULL, NULL);
}

int moy_evq_take(uint32_t pid, moy_evq_ev_t *ev, char *args, size_t cap) {
    ev_t *e = NULL;
    int rc = MOY_EVQ_OK;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i < 0) {
        rc = MOY_EVQ_STALE;
    } else if (s_q[i]->count == 0 || s_q[i]->left == 0) {
        rc = MOY_EVQ_EMPTY;
    } else {
        q_t *q = s_q[i];
        uint32_t k = next_of(q);
        e = q->ev[k];
        *ev = e->h;
        if ((size_t)e->h.n + 1u > cap) {
            rc = MOY_EVQ_RANGE;
            e = NULL;
        } else {
            unlink_at(q, k);
            serve(q, who_of(e->h.from));
            if (q->left > 0) {
                q->left--;
            }
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (e != NULL) {
        memcpy(args, e->args, (size_t)e->h.n + 1u);
        free(e);
    }
    return rc;
}

_Static_assert(sizeof(moy_evq_ev_t) == 8u + MOY_EVQ_APP + MOY_EVQ_NAME,
               "the record a compiled program reads has no padding");

int moy_evq_read(uint32_t pid, void *buf, size_t cap, size_t *len) {
    moy_evq_ev_t ev;
    size_t head = sizeof ev;
    int rc = moy_evq_take(pid, &ev, cap > head ? (char *)buf + head : NULL,
                          cap > head ? cap - head : 0u);
    if ((rc == MOY_EVQ_OK || rc == MOY_EVQ_RANGE) && cap >= head) {
        memcpy(buf, &ev, head);
    }
    if (rc == MOY_EVQ_OK) {
        *len = head + ev.n;
    }
    return rc;
}

uint32_t moy_evq_queued(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    uint32_t n = i >= 0 ? s_q[i]->count : 0u;
    MOY_SYNC_UNLOCK(&s_lock);
    return n;
}

uint32_t moy_evq_ready(void) {
    uint32_t pid = 0;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t k = 0; k < MOY_EVQ_PROCS; k++) {
        uint32_t i = (s_turn + k) % MOY_EVQ_PROCS;
        q_t *q = s_q[i];
        if (q != NULL && (q->flags & MOY_EVQ_VM) && q->count != 0 && q->left != 0
            && !moy_super_held(s_pid[i])) {
            pid = s_pid[i];
            s_turn = i + 1u;
            break;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return pid;
}

uint32_t moy_evq_step(uint32_t pid) {
    uint32_t n = 0;
    MOY_SYNC_LOCK(&s_lock);
    if (pid != 0u) {
        int i = slot_of(pid);
        if (i >= 0) {
            s_q[i]->left = (int8_t)MOY_EVQ_BATCH;
            n = s_q[i]->count;
        }
    } else {
        for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
            q_t *q = s_q[i];
            if (q != NULL && (q->flags & MOY_EVQ_VM)) {
                q->left = (int8_t)MOY_EVQ_BATCH;
                n += q->count != 0 && !moy_super_held(s_pid[i]);
            }
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return n;
}

uint32_t moy_evq_wait(uint32_t pid, uint32_t ms) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    uint32_t n = 0, seen = 0;
    if (i >= 0) {
        n = s_q[i]->count;
        seen = s_seq[i];
        s_who[i] = moy_sync_me();
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (i < 0 || n != 0) {
        return n;
    }
    moy_sync_wait(&s_seq[i], seen, ms);
    return moy_evq_queued(pid);
}

void moy_evq_kick(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    MOY_SYNC_UNLOCK(&s_lock);
    if (i >= 0) {
        wake((uint32_t)i);
    }
}

void moy_evq_unwait(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i >= 0) {
        s_who[i] = NULL;
    }
    MOY_SYNC_UNLOCK(&s_lock);
}

// Every queue whose flags hold `mask` closed (0: every queue).
static uint32_t close_all(uint32_t mask) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
        MOY_SYNC_LOCK(&s_lock);
        bool live = s_q[i] != NULL && (mask == 0u || (s_q[i]->flags & mask) != 0u);
        uint32_t pid = live ? s_pid[i] : 0u;
        MOY_SYNC_UNLOCK(&s_lock);
        if (live && moy_evq_close(pid) == MOY_EVQ_OK) {
            n++;
        }
    }
    return n;
}

uint32_t moy_evq_vm_stop(void) {
    return close_all(MOY_EVQ_VM);
}

uint32_t moy_evq_live(void) {
    uint32_t n = 0;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
        n += s_q[i] != NULL;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return n;
}

void moy_evq_want(uint32_t bits) {
    __atomic_fetch_or(&s_want, bits, __ATOMIC_RELEASE);
}

uint32_t moy_evq_wants(void) {
    return __atomic_load_n(&s_want, __ATOMIC_ACQUIRE);
}

uint32_t moy_evq_wanted(uint32_t mask) {
    return __atomic_fetch_and(&s_want, ~mask, __ATOMIC_ACQ_REL) & mask;
}

uint32_t moy_evq_listening(const char *name) {
    uint32_t n = 0;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
        q_t *q = s_q[i];
        n += q != NULL && !(q->flags & MOY_EVQ_VM) && listens(q, name);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return n;
}

void moy_evq_reset(void) {
    close_all(0u);
    s_turn = 0;
    __atomic_store_n(&s_want, 0u, __ATOMIC_RELEASE);
}

// -- the timers --------------------------------------------------------------------

static uint64_t now_ms(void) {
    #ifdef MOY_SYNC_RTOS
    return (uint64_t)(esp_timer_get_time() / 1000);
    #else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
    #endif
}

static bool timers_job(void *arg) {
    (void)arg;
    moy_evq_timers_run();
    MOY_SYNC_LOCK(&s_lock);
    bool any = s_timers != 0u;
    MOY_SYNC_UNLOCK(&s_lock);
    return any;
}

static int timer_add(uint32_t pid, uint32_t ms, uint32_t every, bool alarm, uint32_t *id) {
    const char *name = alarm ? MOY_EVQ_AT : MOY_EVQ_TIMER;
    if (moy_evq_listen(pid, name, true) != MOY_EVQ_OK) {
        return moy_evq_has(pid) ? MOY_EVQ_FULL : MOY_EVQ_STALE;
    }
    int rc = MOY_EVQ_FULL;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i < 0) {
        rc = MOY_EVQ_STALE;
    } else {
        for (uint32_t k = 0; k < MOY_EVQ_TIMERS; k++) {
            evq_timer_t *t = &s_q[i]->tm[k];
            if (t->id == 0u) {
                s_timer_n = (s_timer_n + 1u) & (MOY_EVQ_TIMER_ID - 1u);
                if (s_timer_n == 0u) {
                    s_timer_n = 1u;
                }
                t->id = MOY_EVQ_TIMER_ID | s_timer_n;
                t->due = now_ms() + ms;
                t->every = alarm ? 0u : every;
                t->alarm = alarm;
                *id = t->id;
                s_timers++;
                rc = MOY_EVQ_OK;
                break;
            }
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (rc != MOY_EVQ_OK) {
        moy_evq_listen(pid, name, false);
    } else if (moy_loop_job != NULL) {
        moy_loop_job(timers_job, NULL);
    }
    return rc;
}

int moy_evq_timer(uint32_t pid, uint32_t ms, uint32_t every, uint32_t *id) {
    return timer_add(pid, ms, every, false, id);
}

int moy_evq_alarm(uint32_t pid, uint32_t ms, uint32_t *id) {
    return timer_add(pid, ms, 0u, true, id);
}

void moy_evq_clock(int64_t step_ms) {
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; step_ms != 0 && i < MOY_EVQ_PROCS; i++) {
        for (uint32_t k = 0; s_q[i] != NULL && k < MOY_EVQ_TIMERS; k++) {
            evq_timer_t *t = &s_q[i]->tm[k];
            if (t->id != 0u && t->alarm) {
                int64_t due = (int64_t)t->due - step_ms;
                t->due = due > 0 ? (uint64_t)due : 0u;
            }
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
}

uint32_t moy_evq_woke(void) {
    MOY_SYNC_LOCK(&s_lock);
    uint32_t pid = 0;
    if (s_woke_n != 0u) {
        pid = s_woke[0];
        memmove(s_woke, s_woke + 1, (size_t)(--s_woke_n) * sizeof s_woke[0]);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return pid;
}

bool moy_evq_timer_cancel(uint32_t pid, uint32_t id) {
    bool got = false, alarm = false;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    for (uint32_t k = 0; i >= 0 && id != 0u && k < MOY_EVQ_TIMERS; k++) {
        if (s_q[i]->tm[k].id == id) {
            s_q[i]->tm[k].id = 0u;
            alarm = s_q[i]->tm[k].alarm;
            s_timers--;
            got = true;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (got) {
        moy_evq_listen(pid, alarm ? MOY_EVQ_AT : MOY_EVQ_TIMER, false);
    }
    return got;
}

uint32_t moy_evq_timers(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    uint32_t n = i >= 0 ? held_timers(s_q[i]) : 0u;
    MOY_SYNC_UNLOCK(&s_lock);
    return n;
}

uint32_t moy_evq_timers_run(void) {
    enum { BURST = 16 };
    enum { ONCE = 1, ALARM = 2 };
    uint32_t to[BURST], ids[BURST], ends[BURST];
    uint32_t next = UINT32_MAX;
    if (__atomic_load_n(&s_timers, __ATOMIC_RELAXED) == 0u) {
        return next;
    }
    for (;;) {
        uint32_t n = 0;
        uint64_t now = now_ms();
        next = UINT32_MAX;
        MOY_SYNC_LOCK(&s_lock);
        for (uint32_t i = 0; i < MOY_EVQ_PROCS; i++) {
            q_t *q = s_q[i];
            for (uint32_t k = 0; q != NULL && k < MOY_EVQ_TIMERS; k++) {
                evq_timer_t *t = &q->tm[k];
                if (t->id == 0u) {
                    continue;
                }
                if (t->due > now) {
                    uint64_t left = t->due - now;
                    next = left < next ? (uint32_t)left : next;
                    continue;
                }
                if (n == BURST) {
                    next = 0;
                    continue;
                }
                to[n] = s_pid[i];
                ids[n] = t->id;
                ends[n] = (t->every == 0u ? ONCE : 0) | (t->alarm ? ALARM : 0);
                if (t->alarm && s_woke_n < sizeof s_woke / sizeof s_woke[0]) {
                    s_woke[s_woke_n++] = s_pid[i];
                }
                if (t->every == 0u) {
                    t->id = 0u;
                    s_timers--;
                } else {
                    // the next tick from now: one a late frame missed is not owed
                    t->due = now - t->due >= t->every ? now + t->every : t->due + t->every;
                    next = t->due - now < next ? (uint32_t)(t->due - now) : next;
                }
                n++;
            }
        }
        MOY_SYNC_UNLOCK(&s_lock);
        for (uint32_t j = 0; j < n; j++) {
            char args[16];
            int k = snprintf(args, sizeof args, "[%lu]", (unsigned long)ids[j]);
            const char *name = ends[j] & ALARM ? MOY_EVQ_AT : MOY_EVQ_TIMER;
            moy_evq_post(to[j], 0u, name, args, (size_t)k);
            if (ends[j] & ONCE) {
                moy_evq_listen(to[j], name, false);
            }
        }
        if (n < BURST) {
            return next;
        }
    }
}
