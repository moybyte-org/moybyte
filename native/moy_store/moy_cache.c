// The card volume's read cache: see moy_cache.h.

#include <string.h>

#include "moy_cache.h"

// The held run to the card, MOY_CACHE_RUN sectors a command at most: 0, or the
// driver's error (the run is let go either way).
static int issue(moy_cache_t *c) {
    int rc = 0;
    while (c->run_n > 0 && rc == 0) {
        uint32_t n = c->run_n < MOY_CACHE_RUN ? c->run_n : MOY_CACHE_RUN;
        rc = c->io(c->ctx, c->run_at, (uint8_t *)c->zeros, n, 1);
        c->run_at += n;
        c->run_n -= n;
        c->runs++;
    }
    c->run_n = 0;
    return rc;
}

static int io(moy_cache_t *c, uint32_t start, uint8_t *buf, uint32_t n, int write) {
    int rc = issue(c);
    if (rc != 0) {
        return rc;
    }
    c->reads += !write;
    return c->io(c->ctx, start, buf, n, write);
}

static int zero(const uint8_t *b) {
    uint8_t any = 0;
    for (uint32_t i = 0; i < MOY_CACHE_SECTOR; i++) {
        any |= b[i];
    }
    return any == 0;
}

// Whether a run takes the one-sector write of `buf` at `block`: 1 when it is
// held, 0 when the write goes on to the card (its io() issues any run first).
// A full run that the write continues is issued here, its error in `*rc`.
static int run_takes(moy_cache_t *c, const uint8_t *buf, uint32_t block, int *rc) {
    if (c->zeros == NULL || c->every == 0 || !zero(buf)) {
        return 0;
    }
    if (c->run_n > 0 && block == c->run_at + c->run_n) {
        if (c->run_n < MOY_CACHE_RUN) {
            c->run_n++;
            return 1;
        }
        *rc = issue(c);
        if (*rc != 0) {
            return 1;
        }
    } else if (c->run_n > 0 || block < c->base || (block - c->base) % c->every != 0) {
        return 0;
    }
    c->run_at = block;
    c->run_n = 1;
    return 1;
}

static void drop(moy_cache_t *c, int s) {
    c->held[s] = -1;
    c->ref[s] = 0;
}

int moy_cache_flush(moy_cache_t *c) {
    return issue(c);
}

void moy_cache_drop_all(moy_cache_t *c) {
    for (int s = 0; s < MOY_CACHE_SLOTS; s++) {
        drop(c, s);
    }
    c->hand = 0;
    c->run_n = 0;
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
    int rc = 0;
    if (n == 1 && run_takes(c, buf, block, &rc)) {
        return rc;
    }
    return io(c, block, (uint8_t *)buf, n, 1);
}
