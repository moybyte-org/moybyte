// moy_save: THE SAVER. A running cart's pmem (moy_pmem.h's record) saved
// without the frame waiting for the medium where a task can take it -- what
// widgets.Pmem, a Lua or compiled run's image in native/moycore and the
// VM-stop path in native/moy_play save through.
//
// moy_save_pmem hands over the cells. Where the saver writes the path it
// copies them into the path's slot and returns: newer cells for a path whose
// save has not started replace the queued ones (the latest wins, many writes
// collapse into one save), and cells that come while that path is being
// written wait in the slot for the next. On a board one task (moy_tasks.h's
// SAVE row) writes them, under the store's lock and the bus gate
// (moy_vol_gate_enter), so the VM waits for one record's write at most, and
// only when it reaches the store at the same time.
//
// The saver writes only to a volume a task other than the VM's reaches, whose
// writes leave the cache on: a card, or POSIX. On a card that shares the
// panel's bus (the T-Deck's) the record's one write holds the gate once,
// which delays one panel kick, never a frame by the write. Any other path is
// written at once, on the caller's task (as is one when all four slots hold
// other paths): internal flash, whose writes turn the cache off on both cores
// whichever task makes them, so no task takes one off the frame, and whose
// writer needs a stack in internal RAM, which the SAVE task's is not (the
// T-Deck reset at its first save from that task, 2026-11-18). There a save
// is one record appended to the pmem log (moy_plog.h) where the board's
// table has one, else the file's slot. The callers make that write at a
// frame boundary, at most every widgets.Pmem.SAVE_MS. Where there is no task (the browser, the unix port,
// the CPython host, a board image without the kernel's task table) every save
// is written at once.
//
// moy_save_wait blocks until no save of `path` (NULL: of any path) is queued
// or being written: what an exit, a pause or a crash does before it reads the
// file. Never call it while holding the store's lock: the saver needs it.

#ifndef MOY_SAVE_H
#define MOY_SAVE_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SAVE_SLOTS 4
#define MOY_SAVE_PATH 224

typedef struct {
    uint32_t saves;         // records written
    uint32_t errors;        // writes the medium refused
    uint32_t replaced;      // queued records newer ones replaced before their write
    uint32_t last_us;       // the last save, queue (or call) to written
    uint32_t max_us;        // the longest
    uint32_t now;           // of `saves`, those written on the caller's task
    uint32_t busy;          // slots queued or being written now
} moy_save_stats_t;

// 0: queued, or written; else the write's code (MOY_EINVAL: a path of
// MOY_SAVE_PATH bytes or more).
int moy_save_pmem(const char *path, const int32_t *cells);
// Whether a save of `path` (NULL: any) is queued or being written.
int moy_save_busy(const char *path);
void moy_save_wait(const char *path);
void moy_save_stats(moy_save_stats_t *out, int reset);

#endif // MOY_SAVE_H
