// The card volume's read cache, with no VM below or above it: moy_card.c puts
// it under every board's card, and fuzz_fs.c under its RAM card, cut at every
// block write.
//
// FatFS reads directory and FAT sectors one at a time and again on every path
// lookup that crosses them, so a one-sector read is served from the last
// MOY_CACHE_SLOTS such reads (clock eviction), a longer one goes to the card,
// and every write drops the sectors it covers BEFORE it is issued, so neither
// a write nor a failed write leaves a stale sector behind.

#ifndef MOY_CACHE_H
#define MOY_CACHE_H

#include <stdint.h>

#define MOY_CACHE_SECTOR 512u
#define MOY_CACHE_SLOTS 32
// What a read returns when `check` finds a hit that differs from the card.
#define MOY_CACHE_ESTALE (-1)

// The card's sectors moved: 0, or the driver's error (never MOY_CACHE_ESTALE).
typedef int (*moy_cache_io_t)(void *ctx, uint32_t start, uint8_t *buf, uint32_t n,
                              int write);

typedef struct {
    moy_cache_io_t io;
    void *ctx;
    uint8_t *slots;                 // MOY_CACHE_SLOTS sectors, the owner's memory
    int32_t held[MOY_CACHE_SLOTS];
    uint8_t ref[MOY_CACHE_SLOTS];
    int hand;
    int check;                      // every hit compared with the card (tests)
    int skip_drop;                  // a write that leaves its sectors cached (tests)
    uint32_t reads, hits, handed, stale;
} moy_cache_t;

// Every slot empty: a card that may have changed, or a reboot.
void moy_cache_drop_all(moy_cache_t *c);
int moy_cache_read(moy_cache_t *c, uint8_t *buf, uint32_t block, uint32_t n);
int moy_cache_write(moy_cache_t *c, const uint8_t *buf, uint32_t block, uint32_t n);

#endif
