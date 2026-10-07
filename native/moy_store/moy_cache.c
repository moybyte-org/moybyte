// The card volume's read cache: see moy_cache.h.

#include <string.h>

#include "moy_cache.h"

static int io(moy_cache_t *c, uint32_t start, uint8_t *buf, uint32_t n, int write) {
    c->reads += !write;
    return c->io(c->ctx, start, buf, n, write);
}

static void drop(moy_cache_t *c, int s) {
    c->held[s] = -1;
    c->ref[s] = 0;
}

void moy_cache_drop_all(moy_cache_t *c) {
    for (int s = 0; s < MOY_CACHE_SLOTS; s++) {
        drop(c, s);
    }
    c->hand = 0;
}

static int slot_of(const moy_cache_t *c, uint32_t block) {
    for (int s = 0; s < MOY_CACHE_SLOTS; s++) {
        if (c->held[s] == (int32_t)block) {
            return s;
        }
    }
    return -1;
}

// An empty slot: the clock hand's next one not used since it last passed.
static int free_slot(moy_cache_t *c) {
    int h = c->hand;
    while (c->ref[h]) {
        c->ref[h] = 0;
        h = (h + 1) % MOY_CACHE_SLOTS;
    }
    c->hand = (h + 1) % MOY_CACHE_SLOTS;
    drop(c, h);
    return h;
}

int moy_cache_read(moy_cache_t *c, uint8_t *buf, uint32_t block, uint32_t n) {
    c->handed += n;
    if (n != 1) {
        return io(c, block, buf, n, 0);
    }
    uint8_t *at;
    int s = slot_of(c, block);
    if (s < 0) {
        s = free_slot(c);
        at = c->slots + (size_t)s * MOY_CACHE_SECTOR;
        int rc = io(c, block, at, 1, 0);
        if (rc != 0) {
            return rc;
        }
        c->held[s] = (int32_t)block;
    } else {
        c->hits++;
        at = c->slots + (size_t)s * MOY_CACHE_SECTOR;
        if (c->check) {
            uint8_t real[MOY_CACHE_SECTOR];
            int rc = io(c, block, real, 1, 0);
            if (rc != 0) {
                return rc;
            }
            if (memcmp(real, at, MOY_CACHE_SECTOR) != 0) {
                c->stale++;
                return MOY_CACHE_ESTALE;
            }
        }
    }
    c->ref[s] = 1;
    memcpy(buf, at, MOY_CACHE_SECTOR);
    return 0;
}

int moy_cache_write(moy_cache_t *c, const uint8_t *buf, uint32_t block, uint32_t n) {
    if (!c->skip_drop) {
        for (int s = 0; s < MOY_CACHE_SLOTS; s++) {
            if (c->held[s] >= (int32_t)block && c->held[s] < (int32_t)(block + n)) {
                drop(c, s);
            }
        }
    }
    return c->io(c->ctx, block, (uint8_t *)buf, n, 1);
}
