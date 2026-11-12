// The queue in front of the console's notifications and the achievements
// process (moy_notes.h has the contract).

#include "moy_platform.h"

#include "moy_notes.h"

#include <stdio.h>
#include <string.h>

#include "moy_evq.h"
#include "moy_sync.h"

// The kernel's frame (moy_loop.h's jobs): weak, for a harness without it,
// whose scheduler runs moy_notes_run itself.
#include "moy_loop.h"
#pragma weak moy_loop_job

enum { WANT_NONE, WANT_AWARD, WANT_VALUE };

typedef struct {
    uint32_t pid;                   // 0: free
    uint32_t serial;                // moves with each new use of the row
    char id[MOY_NOTES_ID + 1u];
    uint8_t want;
    bool found, has_sent, has_at, has_goal;
    int32_t want_v, sent_v, goal, ans;
    uint64_t at;                    // the last send's ms
} badge_t;

static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;
// The waiting notifications and the badges: one block made at the first
// post, award, progress or goal (moy_sync.h's THE TABLE), none before.
static struct {
    moy_notes_note_t wait[MOY_NOTES_WAITING];
    badge_t b[MOY_NOTES_BADGES];
} *s_t;
static uint32_t s_wait_n;
static uint32_t s_next_id = 1;
static uint32_t s_serial;
static bool s_job;


// -- notifications ----------------------------------------------------------------

uint32_t moy_notes_id(void) {
    MOY_SYNC_LOCK(&s_lock);
    uint32_t id = s_next_id++;
    if (s_next_id > 0x3FFFFFFFu) {
        s_next_id = 1;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return id;
}

static void put_text(char *dst, size_t cap, const char *src, size_t n) {
    n = src == NULL ? 0u : n < cap - 1u ? n : cap - 1u;
    for (size_t i = 0; i < n; i++) {
        char c = src[i];
        dst[i] = c == '\n' || c == '\r' || c == 0 ? ' ' : c;
    }
    dst[n] = 0;
}

static int32_t queue(uint32_t pid, const char *app, const char *summary, size_t sn,
                     const char *body, size_t bn) {
    moy_notes_note_t n;
    n.pid = pid;
    snprintf(n.app, sizeof n.app, "%s", app != NULL ? app : "");
    put_text(n.summary, sizeof n.summary, summary, sn);
    put_text(n.body, sizeof n.body, body, bn);
    if (MOY_SYNC_TABLE(s_t) == NULL) {
        return -MOY_EVQ_FULL;
    }
    n.id = moy_notes_id();
    MOY_SYNC_LOCK(&s_lock);
    if (s_wait_n == MOY_NOTES_WAITING) {
        memmove(s_t->wait, s_t->wait + 1, (MOY_NOTES_WAITING - 1u) * sizeof s_t->wait[0]);
        s_wait_n--;
    }
    s_t->wait[s_wait_n++] = n;
    MOY_SYNC_UNLOCK(&s_lock);
    moy_evq_want(MOY_EVQ_WANT_POLL);
    return (int32_t)n.id;
}

int32_t moy_notes_post(uint32_t pid, const char *summary, size_t sn, const char *body,
                       size_t bn) {
    if (!moy_evq_has(pid)) {
        return -MOY_EVQ_STALE;
    }
    if (!(moy_evq_flags(pid) & MOY_EVQ_NOTIFY)) {
        return -MOY_EVQ_DENIED;
    }
    return queue(pid, moy_evq_app(pid), summary, sn, body, bn);
}

int32_t moy_notes_system(const char *summary, size_t sn, const char *body, size_t bn) {
    return queue(MOY_NOTES_SYSTEM, NULL, summary, sn, body, bn);
}

uint32_t moy_notes_waiting(void) {
    return __atomic_load_n(&s_wait_n, __ATOMIC_RELAXED);
}

bool moy_notes_take(moy_notes_note_t *out) {
    bool got = false;
    MOY_SYNC_LOCK(&s_lock);
    if (s_wait_n != 0u) {
        *out = s_t->wait[0];
        memmove(s_t->wait, s_t->wait + 1, (size_t)(--s_wait_n) * sizeof s_t->wait[0]);
        got = true;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return got;
}

// -- badges -----------------------------------------------------------------------

// Process `pid`'s row of badge `id`, made where `make` (NULL: none, or no
// row left, or no table). Under the lock.
static badge_t *row(uint32_t pid, const char *id, size_t n, bool make) {
    badge_t *free_row = NULL;
    if (s_t == NULL) {
        return NULL;
    }
    for (uint32_t i = 0; i < MOY_NOTES_BADGES; i++) {
        badge_t *b = &s_t->b[i];
        if (b->pid == 0u) {
            free_row = free_row != NULL ? free_row : b;
        } else if (b->pid == pid && strncmp(b->id, id, n) == 0 && b->id[n] == 0) {
            return b;
        }
    }
    if (!make || free_row == NULL) {
        return NULL;
    }
    memset(free_row, 0, sizeof *free_row);
    free_row->pid = pid;
    free_row->serial = ++s_serial;
    memcpy(free_row->id, id, n);
    return free_row;
}

static bool bad_id(const char *id, size_t n) {
    return id == NULL || n == 0u || n > MOY_NOTES_ID || memchr(id, 0, n) != NULL;
}

// The message (award: [id]; progress: [id, value]) sent as `pid`: how many
// took it, or -MOY_EVQ_FULL.
static int32_t send(uint32_t pid, const char *id, size_t n, bool award, int32_t v) {
    char j[MOY_NOTES_ID * 6u + 32u];
    size_t k = 0;
    j[k++] = '[';
    j[k++] = '"';
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)id[i];
        if (c == '"' || c == '\\') {
            j[k++] = '\\';
            j[k++] = (char)c;
        } else if (c < 0x20u) {
            k += (size_t)snprintf(j + k, sizeof j - k, "\\u%04x", c);
        } else {
            j[k++] = (char)c;
        }
    }
    j[k++] = '"';
    if (!award) {
        k += (size_t)snprintf(j + k, sizeof j - k, ", %ld", (long)v);
    }
    j[k++] = ']';
    j[k] = 0;
    int took = moy_evq_send(pid, MOY_NOTES_TO, award ? "award" : "progress", j, k, NULL);
    return took >= 0 ? took : took == -MOY_EVQ_FULL ? -MOY_EVQ_FULL : 0;
}

static bool run_job(void *arg) {
    (void)arg;
    return moy_notes_run() != UINT32_MAX;
}

static void later(void) {
    if (!s_job && moy_loop_job != NULL) {
        s_job = moy_loop_job(run_job, NULL);
    }
}

// What waits of row `b` sent now (b's lock let go for the send, which takes
// the queues' own): its answer.
static int32_t send_row(badge_t *b, uint64_t now) {
    uint32_t pid = b->pid, serial = b->serial;
    char id[MOY_NOTES_ID + 1u];
    size_t n = strlen(b->id);
    memcpy(id, b->id, n + 1u);
    bool award = b->want == WANT_AWARD;
    int32_t v = b->want_v;
    MOY_SYNC_UNLOCK(&s_lock);
    int32_t took = send(pid, id, n, award, v);
    MOY_SYNC_LOCK(&s_lock);
    if (b->pid != pid || b->serial != serial) {
        return took;                // its process ended meanwhile
    }
    b->at = now;
    b->has_at = true;
    b->ans = took;
    if (took <= 0) {
        later();                    // refused, or nobody there: again in its turn
        return took;
    }
    if (award) {
        b->want = WANT_NONE;
        b->found = true;
        return took;
    }
    if (b->want == WANT_VALUE && b->want_v == v) {
        b->want = WANT_NONE;
    }
    b->sent_v = v;
    b->has_sent = true;
    if (b->has_goal && v >= b->goal) {
        b->found = true;
    }
    return took;
}

int moy_notes_goal(uint32_t pid, const char *id, size_t n, int32_t goal) {
    if (bad_id(id, n) || goal < 1) {
        return -MOY_EVQ_DENIED;
    }
    (void)MOY_SYNC_TABLE(s_t);         // without it, row() answers NULL
    MOY_SYNC_LOCK(&s_lock);
    badge_t *b = row(pid, id, n, true);
    if (b != NULL) {
        b->goal = goal;
        b->has_goal = true;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return b != NULL ? 0 : -MOY_EVQ_FULL;
}

int32_t moy_notes_award(uint32_t pid, const char *id, size_t n) {
    if (bad_id(id, n)) {
        return 0;
    }
    uint64_t now = moy_now_ms();
    (void)MOY_SYNC_TABLE(s_t);         // without it, row() answers NULL
    MOY_SYNC_LOCK(&s_lock);
    badge_t *b = row(pid, id, n, true);
    int32_t r;
    if (b == NULL) {
        MOY_SYNC_UNLOCK(&s_lock);
        return send(pid, id, n, true, 0);
    }
    if (b->found) {
        r = 1;
    } else {
        b->want = WANT_AWARD;
        if (!b->has_at || b->ans > 0 || now - b->at >= MOY_NOTES_EVERY_MS) {
            r = send_row(b, now);
        } else {
            later();
            r = b->ans;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return r;
}

int32_t moy_notes_progress(uint32_t pid, const char *id, size_t n, int32_t value) {
    if (bad_id(id, n)) {
        return 0;
    }
    uint64_t now = moy_now_ms();
    (void)MOY_SYNC_TABLE(s_t);         // without it, row() answers NULL
    MOY_SYNC_LOCK(&s_lock);
    badge_t *b = row(pid, id, n, true);
    int32_t r;
    if (b == NULL) {
        MOY_SYNC_UNLOCK(&s_lock);
        return send(pid, id, n, false, value);
    }
    if (b->found || b->want == WANT_AWARD) {
        r = b->found ? 1 : b->ans;
    } else if (b->want == WANT_NONE && b->has_sent && value == b->sent_v) {
        r = b->ans;
    } else {
        b->want = WANT_VALUE;
        b->want_v = value;
        if (!b->has_at || now - b->at >= MOY_NOTES_EVERY_MS
            || (b->has_goal && value >= b->goal)) {
            r = send_row(b, now);
        } else {
            later();
            r = b->ans;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return r;
}

// What waits sent: of process `pid` alone and whatever its turn (pid not 0),
// else every process's whose turn came. The ms to the next turn.
static uint32_t flush(uint32_t pid) {
    uint64_t now = moy_now_ms();
    uint32_t next = UINT32_MAX;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; s_t != NULL && i < MOY_NOTES_BADGES; i++) {
        badge_t *b = &s_t->b[i];
        if (b->pid == 0u || b->want == WANT_NONE || (pid != 0u && b->pid != pid)) {
            continue;
        }
        if (pid == 0u && b->has_at && now - b->at < MOY_NOTES_EVERY_MS) {
            uint32_t left = (uint32_t)(MOY_NOTES_EVERY_MS - (now - b->at));
            next = left < next ? left : next;
            continue;
        }
        send_row(b, now);
        if (b->want != WANT_NONE) {
            next = next < MOY_NOTES_EVERY_MS ? next : MOY_NOTES_EVERY_MS;
        }
    }
    if (next == UINT32_MAX) {
        s_job = false;              // the job answers false and goes
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return next;
}

uint32_t moy_notes_run(void) {
    return flush(0u);
}

void moy_notes_end(uint32_t pid) {
    if (pid == 0u) {
        return;
    }
    flush(pid);
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; s_t != NULL && i < MOY_NOTES_BADGES; i++) {
        if (s_t->b[i].pid == pid) {
            s_t->b[i].pid = 0u;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
}

void moy_notes_reset(void) {
    MOY_SYNC_LOCK(&s_lock);
    if (s_t != NULL) {
        memset(s_t->b, 0, sizeof s_t->b);
    }
    s_wait_n = 0;
    MOY_SYNC_UNLOCK(&s_lock);
}
