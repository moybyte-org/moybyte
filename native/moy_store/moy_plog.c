// THE PMEM LOG (moy_plog.h has the format and the rules).

#include <string.h>

#include "moy_fs.h"
#include "moy_platform.h"
#include "moy_plog.h"

#define MAGIC 0x52594f4du           // "MOYR"
#define HEAD 16u
#define CELLS_BYTES (MOY_PMEM_CELLS * 4u)
#define PAD4(n) (((n) + 3u) & ~3u)
#define REC_MAX (HEAD + PAD4(MOY_PLOG_KEY) + CELLS_BYTES)
#define ERASED 0xffffffffu

typedef struct {
    uint16_t fill;      // bytes of the sector its records take
    uint8_t closed;     // something not a record follows them
    uint16_t alive;     // of them, records not marked dead
} sect_t;

static struct {
    moy_plog_medium_t m;
    const void *owner;
    uint32_t n;         // sectors used
    uint32_t seq;       // the highest sequence number on the medium
    uint32_t head;      // the sector the last record went to
    uint32_t alive;
    sect_t s[MOY_PLOG_SECTORS];
    moy_plog_stats_t st;
} L;
static int s_on;

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16
           | (uint32_t)p[3] << 24;
}

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t sum(const uint8_t *p, uint32_t len) {
    uint32_t c = moy_fs_crc32(0, p + 4, 4);
    c = moy_fs_crc32(c, p + 12, 3);
    return moy_fs_crc32(c, p + HEAD, len - HEAD);
}

static const uint8_t *at(uint32_t off) {
    return L.m.map + off;
}

// The length of the valid record at `off`, whose sector ends at `end`; 0
// for anything else.
static uint32_t rec_len(uint32_t off, uint32_t end) {
    if (end - off < HEAD) {
        return 0;
    }
    const uint8_t *p = at(off);
    if (get32(p) != MAGIC) {
        return 0;
    }
    uint32_t len = (uint32_t)p[12] | (uint32_t)p[13] << 8, klen = p[14];
    if (klen == 0 || klen > MOY_PLOG_KEY || len % 4u != 0 || len < HEAD + PAD4(klen)
        || len - HEAD - PAD4(klen) > CELLS_BYTES || len > end - off) {
        return 0;
    }
    return get32(p + 8) == sum(p, len) ? len : 0;
}

static int is_alive(uint32_t off) {
    return at(off)[15] == 0xff;
}

static int key_is(uint32_t off, const char *key, size_t klen) {
    const uint8_t *p = at(off);
    return p[14] == klen && memcmp(p + HEAD, key, klen) == 0;
}

// Whether the key of the record at `off` is `prefix` or under it ("" any).
static int key_under(uint32_t off, const char *prefix, size_t pn) {
    const uint8_t *p = at(off);
    if (pn == 0) {
        return 1;
    }
    return p[14] >= pn && memcmp(p + HEAD, prefix, pn) == 0
           && (p[14] == pn || p[HEAD + pn] == '/');
}

// The records of sector `s`: calls fn(off) for each, stops when it answers
// 1. Every record below a sector's fill was checked whole when the scan or
// the append put the fill past it, so the walk reads lengths alone.
typedef int (*each_fn)(uint32_t off, void *ctx);

static int each_in(uint32_t s, each_fn fn, void *ctx) {
    uint32_t base = s * MOY_PLOG_SECTOR, off = 0;
    while (off + HEAD <= L.s[s].fill) {
        const uint8_t *p = at(base + off);
        uint32_t len = (uint32_t)p[12] | (uint32_t)p[13] << 8;
        if (len < HEAD || off + len > L.s[s].fill) {
            break;
        }
        if (fn(base + off, ctx)) {
            return 1;
        }
        off += len;
    }
    return 0;
}

static int each(each_fn fn, void *ctx) {
    for (uint32_t s = 0; s < L.n; s++) {
        if (each_in(s, fn, ctx)) {
            return 1;
        }
    }
    return 0;
}

// -- the scan ---------------------------------------------------------------------

static void scan(void) {
    L.seq = 0;
    L.head = 0;
    L.alive = 0;
    for (uint32_t s = 0; s < L.n; s++) {
        uint32_t base = s * MOY_PLOG_SECTOR, off = 0;
        sect_t *t = &L.s[s];
        memset(t, 0, sizeof *t);
        while (off < MOY_PLOG_SECTOR) {
            if (get32(at(base + off)) == ERASED) {
                break;
            }
            uint32_t len = rec_len(base + off, base + MOY_PLOG_SECTOR);
            if (len == 0) {
                t->closed = 1;
                break;
            }
            uint32_t seq = get32(at(base + off + 4));
            if (seq >= L.seq) {
                L.seq = seq;
                L.head = s;
            }
            if (is_alive(base + off)) {
                t->alive++;
                L.alive++;
            }
            off += len;
        }
        t->fill = (uint16_t)off;
    }
}

void moy_plog_attach(const moy_plog_medium_t *m, const void *owner) {
    s_on = 0;
    memset(&L, 0, sizeof L);
    if (m == NULL || m->size < MOY_PLOG_SECTOR) {
        return;
    }
    L.m = *m;
    L.owner = owner;
    L.n = m->size / MOY_PLOG_SECTOR;
    if (L.n > MOY_PLOG_SECTORS) {
        L.n = MOY_PLOG_SECTORS;
    }
    scan();
    s_on = 1;
}

int moy_plog_owns(const moy_vol_t *v) {
    return s_on && v->kind == MOY_VOL_KIND_LFS2 && v->fs == L.owner;
}

const void *moy_plog_owner(void) {
    return s_on ? L.owner : NULL;
}

// -- the newest alive record of a key -----------------------------------------------

typedef struct {
    const char *key;
    size_t klen;
    uint32_t seq;           // the newest's, with `off` its place
    uint32_t off;
    int found;
    uint32_t above;         // only records newer than this count (0: any)
} find_t;

static int find_one(uint32_t off, void *ctx) {
    find_t *f = ctx;
    if (is_alive(off) && key_is(off, f->key, f->klen)) {
        uint32_t seq = get32(at(off + 4));
        if (seq > f->above && (!f->found || seq > f->seq)) {
            f->found = 1;
            f->seq = seq;
            f->off = off;
        }
    }
    return 0;
}

static int newest(const char *key, size_t klen, uint32_t above, uint32_t *off) {
    find_t f = { .key = key, .klen = klen, .above = above };
    if (L.alive == 0) {
        return 0;
    }
    each(find_one, &f);
    *off = f.off;
    return f.found;
}

int moy_plog_read(const char *key, int32_t cells[MOY_PMEM_CELLS]) {
    uint32_t off;
    size_t klen = strlen(key);
    if (!s_on || !newest(key, klen, 0, &off)) {
        return 0;
    }
    const uint8_t *p = at(off);
    uint32_t len = (uint32_t)p[12] | (uint32_t)p[13] << 8;
    uint32_t body = HEAD + PAD4(p[14]), n = (len - body) / 4u;
    memset(cells, 0, CELLS_BYTES);
    for (uint32_t i = 0; i < n; i++) {
        cells[i] = (int32_t)get32(p + body + 4u * i);
    }
    return 1;
}

// -- sectors ------------------------------------------------------------------------

// Whether a record of sector `s` is its key's newest alive one.
typedef struct {
    int holds;
} holds_t;

static int holds_one(uint32_t off, void *ctx) {
    if (!is_alive(off)) {
        return 0;
    }
    char key[MOY_PLOG_KEY + 1];
    size_t klen = at(off)[14];
    memcpy(key, at(off) + HEAD, klen);
    key[klen] = 0;
    uint32_t newer;
    if (!newest(key, klen, get32(at(off + 4)), &newer)) {
        ((holds_t *)ctx)->holds = 1;
        return 1;
    }
    return 0;
}

static int holds_newest(uint32_t s) {
    holds_t h = { 0 };
    if (L.s[s].alive == 0) {
        return 0;
    }
    each_in(s, holds_one, &h);
    return h.holds;
}

static int erase(uint32_t s) {
    int rc = L.m.erase(L.m.ctx, s * MOY_PLOG_SECTOR);
    L.alive -= L.s[s].alive;
    memset(&L.s[s], 0, sizeof L.s[s]);
    L.s[s].closed = rc != 0;
    return rc;
}

static int blank(uint32_t off, uint32_t n) {
    for (uint32_t i = 0; i < n; i += 4) {
        if (get32(at(off + i)) != ERASED) {
            return 0;
        }
    }
    return 1;
}

// Whether `len` bytes fit sector `s` past its records, in flash that reads
// erased. Flash that does not (a cut write or erase) closes the sector.
static int fits(uint32_t s, uint32_t len) {
    sect_t *t = &L.s[s];
    if (t->closed || t->fill + len > MOY_PLOG_SECTOR) {
        return 0;
    }
    if (!blank(s * MOY_PLOG_SECTOR + t->fill, len)) {
        t->closed = 1;
        return 0;
    }
    return 1;
}

// The sector the next `len` bytes go to: the head, else the first after it
// with erased room; with none, one erased now that holds no newest record.
static int room(uint32_t len) {
    for (uint32_t i = 0; i < L.n; i++) {
        uint32_t s = (L.head + i) % L.n;
        if (fits(s, len)) {
            return (int)s;
        }
    }
    for (uint32_t i = 1; i <= L.n; i++) {
        uint32_t s = (L.head + i) % L.n;
        if (!holds_newest(s) && erase(s) == 0 && fits(s, len)) {
            L.st.erases_now++;
            return (int)s;
        }
    }
    return -1;
}

int moy_plog_append(const char *key, const int32_t cells[MOY_PMEM_CELLS]) {
    size_t klen = strlen(key);
    if (!s_on || klen == 0 || klen > MOY_PLOG_KEY) {
        return MOY_EINVAL;
    }
    uint32_t ncells = MOY_PMEM_CELLS;
    while (ncells > 0 && cells[ncells - 1] == 0) {
        ncells--;
    }
    uint32_t body = HEAD + PAD4((uint32_t)klen), len = body + 4u * ncells;
    int s = room(len);
    if (s < 0) {
        L.st.refused++;
        return MOY_ENOSPC;
    }
    uint8_t *rec = moy_store_alloc(REC_MAX);
    if (rec == NULL) {
        return MOY_ENOMEM;
    }
    memset(rec, 0, len);
    put32(rec, MAGIC);
    put32(rec + 4, L.seq + 1u);
    rec[12] = (uint8_t)len;
    rec[13] = (uint8_t)(len >> 8);
    rec[14] = (uint8_t)klen;
    rec[15] = 0xff;
    memcpy(rec + HEAD, key, klen);
    for (uint32_t i = 0; i < ncells; i++) {
        put32(rec + body + 4u * i, (uint32_t)cells[i]);
    }
    put32(rec + 8, sum(rec, len));
    sect_t *t = &L.s[s];
    uint32_t off = (uint32_t)s * MOY_PLOG_SECTOR + t->fill;
    int rc = L.m.prog(L.m.ctx, off, rec, len);
    moy_store_free(rec, REC_MAX);
    if (rc != 0 || rec_len(off, (uint32_t)s * MOY_PLOG_SECTOR + MOY_PLOG_SECTOR) != len) {
        t->closed = 1;              // the fill stays below what did not land
        return MOY_EIO;
    }
    t->fill = (uint16_t)(t->fill + len);
    L.seq++;
    L.head = (uint32_t)s;
    t->alive++;
    L.alive++;
    L.st.appends++;
    return 0;
}

// -- killing, settling ---------------------------------------------------------------

typedef struct {
    const char *key;
    size_t klen;
    uint32_t seq;
    uint32_t off;
    int found;
} oldest_t;

static int oldest_one(uint32_t off, void *ctx) {
    oldest_t *o = ctx;
    if (is_alive(off) && key_is(off, o->key, o->klen)) {
        uint32_t seq = get32(at(off + 4));
        if (!o->found || seq < o->seq) {
            o->found = 1;
            o->seq = seq;
            o->off = off;
        }
    }
    return 0;
}

int moy_plog_kill(const char *key) {
    static const uint8_t dead = 0;
    size_t klen = strlen(key);
    while (s_on && L.alive > 0) {
        oldest_t o = { .key = key, .klen = klen };
        each(oldest_one, &o);
        if (!o.found) {
            return 0;
        }
        uint32_t s = o.off / MOY_PLOG_SECTOR;
        if (L.m.prog(L.m.ctx, o.off + 15u, &dead, 1) != 0 || is_alive(o.off)) {
            // A record that will not die keeps its key's pmem in the log: the
            // sector holding it is closed and nothing newer is written over.
            L.s[s].closed = 1;
            return MOY_EIO;
        }
        L.s[s].alive--;
        L.alive--;
    }
    return 0;
}

void moy_plog_settled(void) {
    L.st.settles++;
}

typedef struct {
    const char *prefix;
    size_t pn;
    char *key;
    int found;
} next_t;

static int next_one(uint32_t off, void *ctx) {
    next_t *x = ctx;
    if (is_alive(off) && key_under(off, x->prefix, x->pn)) {
        size_t klen = at(off)[14];
        memcpy(x->key, at(off) + HEAD, klen);
        x->key[klen] = 0;
        x->found = 1;
        return 1;
    }
    return 0;
}

int moy_plog_next(const char *prefix, char key[MOY_PLOG_KEY + 1]) {
    next_t x = { .prefix = prefix, .pn = strlen(prefix), .key = key };
    if (!s_on || L.alive == 0) {
        return 0;
    }
    each(next_one, &x);
    return x.found;
}

// A sector with records (or closed) and none its key's newest: the first
// after the head, the head last and only when it has no room for a record.
int moy_plog_erase_one(void) {
    if (!s_on) {
        return 0;
    }
    for (uint32_t i = 1; i <= L.n; i++) {
        uint32_t s = (L.head + i) % L.n;
        sect_t *t = &L.s[s];
        if (t->fill == 0 && !t->closed) {
            continue;
        }
        if (s == L.head && !t->closed && t->fill + REC_MAX <= MOY_PLOG_SECTOR) {
            continue;
        }
        if (holds_newest(s)) {
            continue;
        }
        erase(s);
        L.st.erases++;
        return 1;
    }
    return 0;
}

void moy_plog_stats(moy_plog_stats_t *out, int reset) {
    L.st.sectors = s_on ? L.n : 0;
    L.st.alive = L.alive;
    uint32_t b = 0;
    for (uint32_t s = 0; s_on && s < L.n; s++) {
        b += L.s[s].fill == 0 && !L.s[s].closed;
    }
    L.st.blank = b;
    *out = L.st;
    if (reset) {
        memset(&L.st, 0, sizeof L.st);
    }
}

// -- a board's partition ------------------------------------------------------------

#if defined(MOY_ON_BOARD) && defined(__has_include)
#if __has_include("esp_partition.h")
#include "esp_partition.h"

#if __has_include("moy_flush.h")
#include "moy_flush.h"
#else
bool moy_flush_kdrain(void);
#endif
#pragma weak moy_flush_kdrain

// A program or an erase turns the cache off on both cores: the panel feeder
// is waited out first, as moy_kvfs.c's writes do (THE FEEDER PAUSES FOR A
// WRITE).
static void quiet(void) {
    if (moy_flush_kdrain != NULL) {
        moy_flush_kdrain();
    }
}

static int b_prog(void *ctx, uint32_t off, const void *buf, uint32_t n) {
    quiet();
    return esp_partition_write(ctx, off, buf, n) == ESP_OK ? 0 : MOY_EIO;
}

static int b_erase(void *ctx, uint32_t off) {
    quiet();
    return esp_partition_erase_range(ctx, off, MOY_PLOG_SECTOR) == ESP_OK ? 0 : MOY_EIO;
}

void moy_plog_board(const void *owner) {
    static esp_partition_mmap_handle_t handle;
    if (s_on) {
        return;
    }
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                                        ESP_PARTITION_SUBTYPE_ANY,
                                                        MOY_PLOG_PART);
    const void *map;
    if (p == NULL || p->size < MOY_PLOG_SECTOR
        || esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &map, &handle) != ESP_OK) {
        return;
    }
    moy_plog_medium_t m = { .map = map, .size = (uint32_t)p->size, .ctx = (void *)p,
                            .prog = b_prog, .erase = b_erase };
    moy_plog_attach(&m, owner);
}
#define MOY_PLOG_BOARD 1
#endif
#endif

#ifndef MOY_PLOG_BOARD
void moy_plog_board(const void *owner) {
    (void)owner;
}
#endif
