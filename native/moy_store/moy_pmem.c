// A cart's pmem record (moy_pmem.h has the format and the contract).

#include <string.h>

#include "moy_fs.h"
#include "moy_pmem.h"
#include "moy_vol.h"

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

int moy_pmem_read(const char *path, int32_t cells[MOY_PMEM_CELLS]) {
    memset(cells, 0, BODY);
    moy_vol_t v;
    const char *rest;
    if (moy_vol_at(path, &v, &rest) != 0) {
        return 0;
    }
    uint8_t *buf = moy_store_alloc(2u * MOY_PMEM_SLOT);
    if (buf == NULL) {
        return 0;
    }
    int at = newest(buf, load(&v, rest, buf));
    if (at >= 0) {
        const uint8_t *p = buf + (size_t)at * MOY_PMEM_SLOT + HEAD;
        for (int i = 0; i < MOY_PMEM_CELLS; i++) {
            cells[i] = (int32_t)get32(p + 4 * i);
        }
    }
    moy_store_free(buf, 2u * MOY_PMEM_SLOT);
    return at >= 0;
}

int moy_pmem_write(const char *path, const int32_t cells[MOY_PMEM_CELLS]) {
    moy_vol_t v;
    const char *rest;
    int rc = moy_vol_at(path, &v, &rest);
    if (rc != 0) {
        return rc;
    }
    uint8_t *buf = moy_store_alloc(2u * MOY_PMEM_SLOT);
    if (buf == NULL) {
        return MOY_ENOMEM;
    }
    moy_store_lock();
    int at = newest(buf, load(&v, rest, buf));
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
    rc = moy_vol_open(&v, rest, MOY_VOL_UPDATE, &f);
    if (rc == 0) {
        rc = moy_vol_seek(f, slot == buf ? 0u : MOY_PMEM_SLOT);
        if (rc == 0) {
            rc = moy_vol_write(f, slot, MOY_PMEM_SLOT);
        }
        int crc = moy_vol_close(f);
        rc = rc ? rc : crc;
    }
    moy_store_unlock();
    moy_store_free(buf, 2u * MOY_PMEM_SLOT);
    return rc;
}
