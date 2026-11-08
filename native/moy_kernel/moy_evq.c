// moy_evq: every process's event queue and the one wait (moy_evq.h has the
// design).

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99
#endif

#include "moy_evq.h"

#include <stdlib.h>
#include <string.h>

#include "moy_super.h"
#include "moy_sync.h"

#ifdef MOY_SYNC_RTOS
#include "esp_heap_caps.h"
#endif

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
    uint32_t flags;
    char app[MOY_EVQ_APP];
    char *receives;             // one name a line, NUL ended; NULL: none
    listen_t on[MOY_EVQ_LISTEN];
    ev_t *ev[MOY_EVQ_DEPTH];    // a ring, its oldest at `at`
    uint8_t at, count;
} q_t;

// A slot's pid, wait word and waiter live outside its queue, so a waiter
// blocked on a slot outlives the queue's close.
static q_t *s_q[MOY_EVQ_PROCS];
static uint32_t s_pid[MOY_EVQ_PROCS];
static volatile uint32_t s_seq[MOY_EVQ_PROCS];
static moy_sync_who_t s_who[MOY_EVQ_PROCS];
static uint32_t s_turn;
static volatile uint32_t s_want;    // THE VM'S WAKE's bits
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

// Event `e` queued on slot `i` and its waiter woken; the oldest it pushes out
// handed back to free. Under the lock.
static ev_t *push(uint32_t i, ev_t *e) {
    q_t *q = s_q[i];
    ev_t *old = NULL;
    if (q->count == MOY_EVQ_DEPTH) {
        old = q->ev[q->at];
        q->at = (uint8_t)((q->at + 1u) % MOY_EVQ_DEPTH);
        q->count--;
    }
    q->ev[(q->at + q->count) % MOY_EVQ_DEPTH] = e;
    q->count++;
    return old;
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
    copy_name(fresh->app, sizeof fresh->app, app);
    s_pid[i] = pid;
    s_who[i] = NULL;
    s_q[i] = fresh;
    MOY_SYNC_UNLOCK(&s_lock);
    return MOY_EVQ_OK;
}

int moy_evq_close(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i < 0) {
        MOY_SYNC_UNLOCK(&s_lock);
        return MOY_EVQ_STALE;
    }
    q_t *q = s_q[i];
    s_q[i] = NULL;
    s_pid[i] = 0;
    moy_sync_who_t who = s_who[i];
    s_who[i] = NULL;
    MOY_SYNC_UNLOCK(&s_lock);
    moy_sync_wake(&s_seq[i], who);
    for (uint32_t k = 0; k < q->count; k++) {
        free(q->ev[(q->at + k) % MOY_EVQ_DEPTH]);
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
        old = push((uint32_t)i, e);
        e = NULL;
        vm = (s_q[i]->flags & MOY_EVQ_VM) != 0u;
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

// Message `name` to every listening queue `want` picks, one copy each.
static int fan(uint32_t from, const char *app, const char *to_app, const char *name,
               const char *args, size_t n, uint32_t *denied) {
    int took = 0;
    uint32_t refused = 0;
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
            if (allowed(q, sys, app, name)) {
                old = push(i, e);
                e = NULL;
                sent = true;
                vm = (q->flags & MOY_EVQ_VM) != 0u;
            } else {
                refused++;
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
    return fan(from, me, app != NULL ? app : "", name, args, n, denied);
}

int moy_evq_route(const char *name, const char *args, size_t n) {
    return fan(0u, MOY_EVQ_SYSTEM, NULL, name, args, n, NULL);
}

int moy_evq_take(uint32_t pid, moy_evq_ev_t *ev, char *args, size_t cap) {
    ev_t *e = NULL;
    int rc = MOY_EVQ_OK;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(pid);
    if (i < 0) {
        rc = MOY_EVQ_STALE;
    } else if (s_q[i]->count == 0) {
        rc = MOY_EVQ_EMPTY;
    } else {
        q_t *q = s_q[i];
        e = q->ev[q->at];
        *ev = e->h;
        if ((size_t)e->h.n + 1u > cap) {
            rc = MOY_EVQ_RANGE;
            e = NULL;
        } else {
            q->ev[q->at] = NULL;
            q->at = (uint8_t)((q->at + 1u) % MOY_EVQ_DEPTH);
            q->count--;
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
        if (q != NULL && (q->flags & MOY_EVQ_VM) && q->count != 0 && !moy_super_held(s_pid[i])) {
            pid = s_pid[i];
            s_turn = i + 1u;
            break;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return pid;
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
