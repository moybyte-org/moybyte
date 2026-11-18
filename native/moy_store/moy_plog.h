// moy_plog: THE PMEM LOG. A raw flash area beside the internal store (the
// "pmem" partition) that a cart's pmem save on internal flash appends to,
// instead of rewriting `<cart>/pmem.bin` through littlefs: one program of a
// few hundred bytes into flash erased ahead of it, where the littlefs write
// erased and programmed a block. moy_pmem.c is its only caller, under the
// store's lock; moy_pmem.h has the contract a cart sees.
//
// THE AREA is whole 4 KB sectors. Each holds records back to back from its
// start, then erased flash:
//   0  "MOYR"
//   4  sequence number, u32 little-endian, one counter for the whole area
//   8  crc32 (moy_fs_crc32) of bytes 4..8, 12..15 and 16..len
//   12 len, u16: the record's bytes, a multiple of 4
//   14 the key's length, u8 (1..MOY_PLOG_KEY)
//   15 0xFF while the record is alive, anything else once it is dead
//   16 the key (the pmem.bin path inside the volume), zero-padded to 4
//   .. the cells up to the last one not zero, i32 little-endian
// A cart's pmem is its key's ALIVE record of the highest sequence number;
// with none alive it is the file's. The dead mark is one byte programmed
// over the record, outside its checksum, so killing a record never makes the
// sector unreadable.
//
// THE SCAN walks each sector from its start: a valid record, then the next;
// erased flash ("FFFF" where a record would start) ends the sector's records;
// anything else closes the sector, which takes no more records until it is
// erased. A record is only ever appended into flash that reads erased, so a
// cut append is the last thing in its sector and its checksum fails: a read
// after it answers the cells before (the key's previous alive record, or the
// file), never a mix.
//
// ERASES happen ahead. A sector is erased only when none of its records is
// its key's newest alive one (a cut erase can only bring back records older
// than one that stays alive). moy_pmem_tidy erases at a quiet moment; an
// append finds a sector with erased room first, and erases one on the save
// path only when no sector has room (moy_plog_stats_t.erases_now counts
// them).
//
// fuzz_fs.c's power-cut matrix cuts every program and erase of it (a
// program in 16-byte pieces, an erase in two halves).

#ifndef MOY_PLOG_H
#define MOY_PLOG_H

#include <stdint.h>

#include "moy_pmem.h"
#include "moy_vol.h"

#define MOY_PLOG_SECTOR 4096u
#define MOY_PLOG_SECTORS 64u        // the most sectors an area is used for
#define MOY_PLOG_KEY 223u           // the longest key (moy_save.h's MOY_SAVE_PATH - 1)
#define MOY_PLOG_PART "pmem"        // the board's partition label

typedef struct {
    const uint8_t *map;         // the area as read (a board's flash mapping)
    uint32_t size;              // bytes, whole sectors
    void *ctx;
    int (*prog)(void *ctx, uint32_t off, const void *buf, uint32_t n);   // 0 or MOY_EIO
    int (*erase)(void *ctx, uint32_t off);      // the sector at `off`; 0 or MOY_EIO
} moy_plog_medium_t;

typedef struct {
    uint32_t sectors;           // the area's
    uint32_t blank;             // sectors erased and holding no record
    uint32_t alive;             // records alive: saves not yet in their files
    uint32_t appends;           // records written
    uint32_t erases;            // sectors erased at a quiet moment
    uint32_t erases_now;        // sectors erased on the save path (no sector had room)
    uint32_t settles;           // keys written back to their files
    uint32_t refused;           // appends that found no room: the file was written
} moy_plog_stats_t;

// The log on `m` for the volume whose fs is `owner`, scanned. NULL `m`
// detaches it, what a power cut does to its RAM state.
void moy_plog_attach(const moy_plog_medium_t *m, const void *owner);
// A board: the "pmem" partition mapped and attached for `owner`, when the
// table has one; nothing otherwise (pmem stays in its files).
void moy_plog_board(const void *owner);
// Whether the log keeps the pmem of volume `v`, and the volume it keeps.
int moy_plog_owns(const moy_vol_t *v);
const void *moy_plog_owner(void);

// 1: `cells` is the key's newest alive record (cells past it zero); 0: none.
int moy_plog_read(const char *key, int32_t cells[MOY_PMEM_CELLS]);
// 0, MOY_ENOSPC (no sector has room, none can be erased), MOY_EIO.
int moy_plog_append(const char *key, const int32_t cells[MOY_PMEM_CELLS]);
// Every alive record of `key` marked dead, oldest first: 0, or MOY_EIO when
// one would not die (its key's pmem stays the log's).
int moy_plog_kill(const char *key);
void moy_plog_settled(void);        // counts a key written back
// 1 and the key of an alive record at or under `prefix` ("" any) in `key`.
int moy_plog_next(const char *prefix, char key[MOY_PLOG_KEY + 1]);
// One sector worth erasing erased: 1, or 0 when none is.
int moy_plog_erase_one(void);

void moy_plog_stats(moy_plog_stats_t *out, int reset);

#endif // MOY_PLOG_H
