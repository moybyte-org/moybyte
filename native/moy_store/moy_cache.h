// The card volume's read cache, with no VM below or above it: moy_card.c puts
// it under every board's card, and fuzz_fs.c under its RAM card, cut at every
// block write.
//
// FatFS reads directory and FAT sectors one at a time and again on every path
// lookup that crosses them, so a one-sector read is served from the last
// MOY_CACHE_SLOTS such reads (clock eviction), a longer one goes to the card,
// and every write drops the sectors it covers BEFORE it is issued, so neither
// a write nor a failed write leaves a stale sector behind.
//
// FatFS clears a new directory's cluster one zero sector at a time (oofatfs'
// dir_clear without FF_USE_LFN 3): 64 single-block writes for a 32 KB
// cluster, 140 ms on the T-Deck's card and 270 ms on the Guition P4's. With
// `zeros` (MOY_CACHE_RUN zero sectors) and the volume's clusters (`base`, the
// first data sector, and `every`, its sectors per cluster), a one-sector zero
// write at a cluster's start is held as a RUN that the next contiguous zero
// sectors extend, and the run goes to the card as one multi-block write before
// any other read or write reaches it -- so the card sees the writes in the
// order FatFS made them, and a cut loses no more than it would have between
// those writes.

#ifndef MOY_CACHE_H
#define MOY_CACHE_H

#include <stdint.h>

#define MOY_CACHE_SECTOR 512u
#define MOY_CACHE_SLOTS 32
#define MOY_CACHE_RUN 32            // the zero sectors one run holds at most
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
    const uint8_t *zeros;           // MOY_CACHE_RUN zero sectors, the owner's; NULL: no runs
    uint32_t base, every;           // the volume's first data sector, sectors per cluster
    uint32_t run_at, run_n;         // the zero sectors held, not yet on the card
    uint32_t reads, hits, handed, stale, runs;
} moy_cache_t;

// Every slot empty: a card that may have changed, or a reboot. A run still
// held goes with them, as a cut would take it.
void moy_cache_drop_all(moy_cache_t *c);
// The held run to the card now: 0, or the driver's error.
int moy_cache_flush(moy_cache_t *c);
int moy_cache_read(moy_cache_t *c, uint8_t *buf, uint32_t block, uint32_t n);
int moy_cache_write(moy_cache_t *c, const uint8_t *buf, uint32_t block, uint32_t n);

#endif
