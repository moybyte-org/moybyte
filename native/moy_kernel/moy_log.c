// moy_log: the OS log's ring (moy_log.h has the design).

#include "moy_platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_log.h"
#include "moy_proc_exit.h"

#include "moy_kmem.h"

#define HDR ((uint32_t)sizeof(moy_log_rec_t))

typedef struct {
    uint32_t pid;
    char name[MOY_LOG_NAME_MAX];
} name_t;

static struct {
    uint8_t *buf;                   // the ring, `cap` bytes
    uint32_t cap;
    uint32_t head, used;            // the oldest record's offset; bytes held
    uint32_t first, next;           // seqs: the oldest held, the next written
    uint32_t next_pid, current;
    uint64_t pinned_us;
    name_t *names;                  // MOY_LOG_NAMES, by (pid - 2) % MOY_LOG_NAMES
    char *scratch;                  // MOY_LOG_TEXT_MAX: the dump's text
} G = { .first = 1, .next = 1, .next_pid = 2, .current = MOY_LOG_SHELL };

#ifdef MOY_ON_BOARD
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
#define LOCK() portENTER_CRITICAL(&s_mux)
#define UNLOCK() portEXIT_CRITICAL(&s_mux)
#else
static volatile char s_lock;
#define LOCK() while (__atomic_test_and_set(&s_lock, __ATOMIC_ACQUIRE)) { }
#define UNLOCK() __atomic_clear(&s_lock, __ATOMIC_RELEASE)
#endif

static const char *const LEVELS[MOY_LOG_LEVELS] = {
    "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug",
};

static void *log_psram(size_t n) {
    return moy_kalloc(n, MOY_KMEM_PSRAM | MOY_KMEM_INTERNAL_OK | MOY_KMEM_ZERO);
}

static void log_free(void *p) {
    moy_kfree(p);
}

static uint64_t now_us(void) {
    if (G.pinned_us) {
        return G.pinned_us;
    }
    return (uint64_t)moy_now_us();
}

const uint8_t moy_log_exits[4] = {
    MOY_PROC_NOT_RUN, MOY_PROC_INTERRUPTED, MOY_PROC_TRAPPED, MOY_PROC_KILLED,
};

void moy_log_pin_clock(uint64_t us) {
    G.pinned_us = us;
}

// -- the ring --------------------------------------------------------------------

static bool make(size_t bytes) {
    if (bytes < HDR + 16u) {
        return false;
    }
    if (G.names == NULL) {
        G.names = log_psram(MOY_LOG_NAMES * sizeof(name_t));
        G.scratch = log_psram(MOY_LOG_TEXT_MAX + 1u);
        if (G.names == NULL || G.scratch == NULL) {
            return false;
        }
    }
    uint8_t *b = log_psram(bytes);
    if (b == NULL) {
        return false;
    }
    LOCK();
    uint8_t *old = G.buf;
    G.buf = b;
    G.cap = (uint32_t)bytes;
    G.head = G.used = 0;
    G.first = G.next;
    UNLOCK();
    if (old != NULL) {
        log_free(old);
    }
    return true;
}

void moy_log_reserve(void) {
    if (G.buf == NULL) {
        make(MOY_LOG_BYTES);
    }
}

bool moy_log_init(size_t bytes) {
    return make(bytes);
}

static void put(uint32_t off, const void *src, uint32_t n) {
    uint32_t a = G.cap - off;
    if (n <= a) {
        memcpy(G.buf + off, src, n);
    } else {
        memcpy(G.buf + off, src, a);
        memcpy(G.buf, (const uint8_t *)src + a, n - a);
    }
}

static void get(uint32_t off, void *dst, uint32_t n) {
    uint32_t a = G.cap - off;
    if (n <= a) {
        memcpy(dst, G.buf + off, n);
    } else {
        memcpy(dst, G.buf + off, a);
        memcpy((uint8_t *)dst + a, G.buf, n - a);
    }
}

static uint32_t wrap(uint32_t off) {
    return off >= G.cap ? off - G.cap : off;
}

static void drop_oldest(void) {
    moy_log_rec_t r;
    get(G.head, &r, HDR);
    uint32_t n = HDR + r.len;
    G.head = wrap(G.head + n);
    G.used -= n;
    G.first++;
}

void moy_log_write(int level, uint32_t pid, const char *text, size_t n) {
    if (G.buf == NULL) {
        moy_log_reserve();
        if (G.buf == NULL) {
            return;
        }
    }
    if (n > MOY_LOG_TEXT_MAX) {
        n = MOY_LOG_TEXT_MAX;
    }
    if (n > G.cap - HDR) {
        n = G.cap - HDR;
    }
    moy_log_rec_t r;
    r.t_us = now_us();
    r.pid = pid == MOY_LOG_CURRENT ? G.current : pid;
    r.len = (uint16_t)n;
    r.level = (uint8_t)(level < 0 ? 0 : level >= MOY_LOG_LEVELS ? MOY_LOG_DEBUG : level);
    r.flags = 0;
    uint32_t need = HDR + (uint32_t)n;
    LOCK();
    while (G.cap - G.used < need) {
        drop_oldest();
    }
    r.seq = G.next++;
    uint32_t at = wrap(G.head + G.used);
    put(at, &r, HDR);
    if (n) {
        put(wrap(at + HDR), text, (uint32_t)n);
    }
    G.used += need;
    UNLOCK();
}

void moy_log_say(int level, uint32_t pid, const char *fmt, ...) {
    char buf[384];
    va_list ap;
    va_start(ap, fmt);
    int k = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (k < 0) {
        return;
    }
    moy_log_write(level, pid, buf, (size_t)k < sizeof(buf) ? (size_t)k : sizeof(buf) - 1u);
}

bool moy_log_read(moy_log_cursor_t *c, moy_log_rec_t *rec, char *text, size_t cap) {
    bool got = false;
    LOCK();
    if (G.buf != NULL && G.used) {
        uint32_t seq = G.first, off = G.head;
        if (c->seq > G.first && c->seq < G.next) {
            if (c->off < G.cap) {
                seq = c->seq;
                off = c->off;
            } else {
                while (seq < c->seq) {
                    moy_log_rec_t r;
                    get(off, &r, HDR);
                    off = wrap(off + HDR + r.len);
                    seq++;
                }
            }
        }
        if (c->seq < G.next && seq < G.next) {
            get(off, rec, HDR);
            if (cap) {
                get(wrap(off + HDR), text, rec->len < cap ? rec->len : (uint32_t)cap);
            }
            c->seq = rec->seq + 1u;
            c->off = wrap(off + HDR + rec->len);
            got = true;
        }
    }
    UNLOCK();
    return got;
}

void moy_log_stats(moy_log_stats_t *s) {
    LOCK();
    s->first = G.first;
    s->next = G.next;
    s->used = G.used;
    s->cap = G.cap;
    UNLOCK();
}

// -- processes ---------------------------------------------------------------------

uint32_t moy_log_spawn(const char *name) {
    if (G.names == NULL) {
        moy_log_reserve();
    }
    LOCK();
    uint32_t pid = G.next_pid++;
    if (G.next_pid == MOY_LOG_CURRENT) {
        G.next_pid = 2;
    }
    if (G.names != NULL) {
        name_t *e = &G.names[(pid - 2u) % MOY_LOG_NAMES];
        e->pid = pid;
        size_t n = name != NULL ? strlen(name) : 0;
        if (n > MOY_LOG_NAME_MAX - 1u) {
            n = MOY_LOG_NAME_MAX - 1u;
        }
        memcpy(e->name, name, n);
        e->name[n] = 0;
    }
    UNLOCK();
    return pid;
}

const char *moy_log_name(uint32_t pid) {
    if (pid == MOY_LOG_KERNEL) {
        return "kernel";
    }
    if (pid == MOY_LOG_SHELL) {
        return "shell";
    }
    if (G.names == NULL || pid == MOY_LOG_CURRENT) {
        return "";
    }
    const name_t *e = &G.names[(pid - 2u) % MOY_LOG_NAMES];
    return e->pid == pid ? e->name : "";
}

uint32_t moy_log_current(void) {
    return G.current;
}

void moy_log_set_current(uint32_t pid) {
    G.current = pid;
}

// -- readers -----------------------------------------------------------------------

const char *moy_log_level_name(int level) {
    return level >= 0 && level < MOY_LOG_LEVELS ? LEVELS[level] : "?";
}

int moy_log_level_of(const char *name, size_t n) {
    for (int i = 0; i < MOY_LOG_LEVELS; i++) {
        if (strlen(LEVELS[i]) == n && memcmp(LEVELS[i], name, n) == 0) {
            return i;
        }
    }
    return -1;
}

size_t moy_log_format(const moy_log_rec_t *r, const char *line, size_t n, char *out, size_t cap) {
    uint64_t ms = r->t_us / 1000u;
    int k = snprintf(out, cap, "LOG %u %u.%03u %s %s/%u ", (unsigned)r->seq,
                     (unsigned)(ms / 1000u), (unsigned)(ms % 1000u),
                     moy_log_level_name(r->level), moy_log_name(r->pid), (unsigned)r->pid);
    if (k < 0 || cap == 0) {
        return 0;
    }
    size_t at = (size_t)k < cap ? (size_t)k : cap - 1u;
    size_t room = cap - 1u - at;
    if (n > room) {
        n = room;
    }
    memcpy(out + at, line, n);
    out[at + n] = 0;
    return at + n;
}

static bool name_holds(uint32_t pid, const char *want) {
    return want == NULL || strstr(moy_log_name(pid), want) != NULL;
}

void moy_log_dump(uint32_t since, int level, const char *name, void (*say)(const char *line)) {
    char out[256];
    moy_log_cursor_t c = { since, UINT32_MAX };
    moy_log_rec_t r;
    uint32_t n = 0;
    char *text = G.scratch;
    while (text != NULL && moy_log_read(&c, &r, text, MOY_LOG_TEXT_MAX)) {
        if (r.level > level || !name_holds(r.pid, name)) {
            continue;
        }
        n++;
        // one serial line a line of the text, a long one in pieces
        size_t room = sizeof(out) - 1u - moy_log_format(&r, "", 0, out, sizeof(out));
        size_t len = r.len < MOY_LOG_TEXT_MAX ? r.len : MOY_LOG_TEXT_MAX;
        size_t at = 0;
        do {
            size_t e = at;
            while (e < len && text[e] != '\n') {
                e++;
            }
            do {
                size_t k = e - at < room ? e - at : room;
                moy_log_format(&r, text + at, k, out, sizeof(out));
                say(out);
                at += k;
            } while (at < e);
            at = e + 1u;
        } while (at < len);
    }
    moy_log_stats_t s;
    moy_log_stats(&s);
    snprintf(out, sizeof(out), "LOG end n=%u next=%u first=%u", (unsigned)n,
             (unsigned)s.next, (unsigned)s.first);
    say(out);
}
