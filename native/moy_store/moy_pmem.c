// A cart's pmem record (moy_pmem.h has the format and the contract).

#include <string.h>

#include "moy_fs.h"
#include "moy_plog.h"
#include "moy_pmem.h"
#include "moy_vol.h"

#ifndef MOY_ENOENT
#define MOY_ENOENT 2
#endif

#define HEAD 12
#define BODY (MOY_PMEM_CELLS * 4)

_Static_assert(HEAD + BODY <= MOY_PMEM_SLOT, "a record fits its slot");
_Static_assert(MOY_PMEM_SLOT % 512 == 0, "a slot is whole sectors");

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

static uint32_t sum(const uint8_t *slot) {
    return moy_fs_crc32(moy_fs_crc32(0, slot + 4, 4), slot + HEAD, BODY);
}

static int valid(const uint8_t *slot, size_t have) {
    return have >= HEAD + BODY && memcmp(slot, "MOYP", 4) == 0
           && get32(slot + 8) == sum(slot);
}

// The newest valid slot of the `have` bytes read (0 or 1), or -1.
static int newest(const uint8_t *buf, size_t have) {
    size_t h1 = have > MOY_PMEM_SLOT ? have - MOY_PMEM_SLOT : 0;
    int a = valid(buf, have), b = valid(buf + MOY_PMEM_SLOT, h1);
    if (a && b) {
        return (int32_t)(get32(buf + MOY_PMEM_SLOT + 4) - get32(buf + 4)) > 0;
    }
    return a ? 0 : b ? 1 : -1;
}

// Both slots into `buf` (2 x MOY_PMEM_SLOT): the bytes the file holds of them.
static size_t load(const moy_vol_t *v, const char *rest, uint8_t *buf) {
    moy_vol_file_t *f;
    size_t got = 0;
    if (moy_vol_open(v, rest, MOY_VOL_READ, &f) == 0) {
        if (moy_vol_read(f, buf, 2u * MOY_PMEM_SLOT, &got) != 0) {
            got = 0;
        }
        moy_vol_close(f);
    }
    return got;
}

// The log's key of a path inside its volume: no leading slash.
static const char *keyof(const char *rest) {
    while (*rest == '/') {
        rest++;
    }
    return strlen(rest) <= MOY_PLOG_KEY ? rest : NULL;
}

static int file_read(const moy_vol_t *v, const char *rest, int32_t cells[MOY_PMEM_CELLS]) {
    uint8_t *buf = moy_store_alloc(2u * MOY_PMEM_SLOT);
    if (buf == NULL) {
        return 0;
    }
    int at = newest(buf, load(v, rest, buf));
    if (at >= 0) {
        const uint8_t *p = buf + (size_t)at * MOY_PMEM_SLOT + HEAD;
        for (int i = 0; i < MOY_PMEM_CELLS; i++) {
            cells[i] = (int32_t)get32(p + 4 * i);
        }
    }
    moy_store_free(buf, 2u * MOY_PMEM_SLOT);
    return at >= 0;
}

static int file_write(const moy_vol_t *v, const char *rest, const int32_t cells[MOY_PMEM_CELLS]) {
    uint8_t *buf = moy_store_alloc(2u * MOY_PMEM_SLOT);
    if (buf == NULL) {
        return MOY_ENOMEM;
    }
    int at = newest(buf, load(v, rest, buf));
    uint8_t *slot = buf + (at == 0 ? MOY_PMEM_SLOT : 0);
    uint32_t seq = at >= 0 ? get32(buf + (size_t)at * MOY_PMEM_SLOT + 4) + 1u : 1u;
    memset(slot, 0, MOY_PMEM_SLOT);
    memcpy(slot, "MOYP", 4);
    put32(slot + 4, seq);
    for (int i = 0; i < MOY_PMEM_CELLS; i++) {
        put32(slot + HEAD + 4 * i, (uint32_t)cells[i]);
    }
    put32(slot + 8, sum(slot));
    moy_vol_file_t *f;
    int rc = moy_vol_open(v, rest, MOY_VOL_UPDATE, &f);
    if (rc == 0) {
        rc = moy_vol_seek(f, slot == buf ? 0u : MOY_PMEM_SLOT);
        if (rc == 0) {
            rc = moy_vol_write(f, slot, MOY_PMEM_SLOT);
        }
        int crc = moy_vol_close(f);
        rc = rc ? rc : crc;
    }
    moy_store_free(buf, 2u * MOY_PMEM_SLOT);
    return rc;
}

int moy_pmem_read(const char *path, int32_t cells[MOY_PMEM_CELLS]) {
    memset(cells, 0, BODY);
    moy_vol_t v;
    const char *rest, *key;
    if (moy_vol_at(path, &v, &rest) != 0) {
        return 0;
    }
    moy_store_lock();
    int got = moy_plog_owns(&v) && (key = keyof(rest)) != NULL && moy_plog_read(key, cells);
    if (!got) {
        got = file_read(&v, rest, cells);
    }
    moy_store_unlock();
    return got;
}

int moy_pmem_write(const char *path, const int32_t cells[MOY_PMEM_CELLS]) {
    moy_vol_t v;
    const char *rest, *key = NULL;
    int rc = moy_vol_at(path, &v, &rest);
    if (rc != 0) {
        return rc;
    }
    moy_store_lock();
    if (moy_plog_owns(&v) && (key = keyof(rest)) != NULL && moy_plog_append(key, cells) == 0) {
        moy_store_unlock();
        return 0;
    }
    rc = file_write(&v, rest, cells);
    if (rc == 0 && key != NULL) {
        moy_plog_kill(key);         // the file is newer than anything the log holds
    }
    moy_store_unlock();
    return rc;
}

// The log's records at or under `path` written back to their files (`back`)
// or dropped: the log's prefix, or NULL when the log does not keep the path.
static const char *prefix_of(const char *path, char *out, size_t cap) {
    moy_vol_t v;
    const char *rest;
    if (moy_vol_at(path, &v, &rest) != 0 || !moy_plog_owns(&v)) {
        return NULL;
    }
    while (*rest == '/') {
        rest++;
    }
    size_t n = strlen(rest);
    while (n > 0 && rest[n - 1] == '/') {
        n--;
    }
    if (n >= cap) {
        return NULL;
    }
    memcpy(out, rest, n);
    out[n] = 0;
    return out;
}

// One key written back: its newest record into its file, then its records
// dead. A file whose folder is gone drops them.
static int settle_key(const char *key) {
    int32_t cells[MOY_PMEM_CELLS];
    moy_vol_t v = { .kind = MOY_VOL_KIND_LFS2, .fs = (void *)moy_plog_owner() };
    moy_plog_read(key, cells);
    int rc = file_write(&v, key, cells);
    if (rc == 0 || rc == MOY_ENOENT) {
        return moy_plog_kill(key) == 0 ? (moy_plog_settled(), 0) : MOY_EIO;
    }
    return rc;
}

static int s_err;                   // a write-back failed: tidy waits for the next save
static uint32_t s_err_at;           // the appends count then

int moy_pmem_settle(const char *path) {
    char prefix[MOY_PLOG_KEY + 1], key[MOY_PLOG_KEY + 1];
    int rc = 0;
    moy_store_lock();
    if (path == NULL || prefix_of(path, prefix, sizeof prefix) != NULL) {
        const char *p = path == NULL ? "" : prefix;
        while (rc == 0 && moy_plog_next(p, key)) {
            rc = settle_key(key);
        }
    }
    moy_store_unlock();
    return rc;
}

void moy_pmem_forget(const char *path) {
    char prefix[MOY_PLOG_KEY + 1], key[MOY_PLOG_KEY + 1];
    moy_store_lock();
    if (prefix_of(path, prefix, sizeof prefix) != NULL) {
        while (moy_plog_next(prefix, key) && moy_plog_kill(key) == 0) {
        }
    }
    moy_store_unlock();
}

int moy_pmem_tidy(void) {
    char key[MOY_PLOG_KEY + 1];
    int did;
    moy_store_lock();
    moy_plog_stats_t st;
    moy_plog_stats(&st, 0);
    if (s_err && st.appends != s_err_at) {
        s_err = 0;
    }
    if (!s_err && moy_plog_next("", key)) {
        if (settle_key(key) != 0) {
            s_err = 1;
            s_err_at = st.appends;
        }
        did = 1;
    } else {
        did = moy_plog_erase_one();
    }
    moy_store_unlock();
    return did;
}
