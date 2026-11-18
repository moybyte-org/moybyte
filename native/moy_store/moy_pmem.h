// moy_pmem: a cart's pmem on the medium, `<cart>/pmem.bin` -- 256 signed
// 32-bit cells, the record every tier reads and writes (the Python Player's
// widgets.Pmem, a Lua or compiled run's image in native/moycore, the VM-stop
// path in native/moy_play).
//
// The file is two slots of MOY_PMEM_SLOT bytes, written alternately in place:
//   0  "MOYP"
//   4  sequence number, u32 little-endian
//   8  crc32 (moy_fs_crc32) of bytes 4..8 then of the cells
//   12 the cells, 256 x i32 little-endian
//   1036.. zeros to the slot's end
// A read takes the valid slot of the newer sequence number (compared mod
// 2^32); with neither valid the cells are all zero. A write reads both slots,
// then writes the one that does NOT hold the newest valid record, whole, at
// its offset, with the next sequence number: the file is opened without
// truncating, so on a card a save is the slot's three sectors and the
// directory entry. A cut anywhere leaves the newest valid record untouched
// and the slot being written either whole or failing its checksum: a read
// after it answers the previous cells or the new ones, never a mix
// (native/moy_store/fuzz_fs.c's cases 16 and 17 cut it at every block write).
//
// ON INTERNAL FLASH WITH A PMEM LOG (moy_plog.h: the board's "pmem"
// partition, beside the internal store) a save is a record appended to the
// log instead, and a read is the path's newest record there, else the file.
// The file stays the record at rest: moy_pmem_tidy, at a quiet moment, writes
// each logged path back into its file and marks its records dead, then erases
// a sector of them, one step a call. Whatever hands a cart's files on or
// takes its folder away goes through the two verbs below: the export
// (moy_pack) and the sync wire's read settle first; a removal (the shelf's,
// moy_cat_rmtree) and the sync wire's write of pmem.bin forget, so a cart
// installed again in that folder starts from its file, or from none.

#ifndef MOY_PMEM_H
#define MOY_PMEM_H

#include <stdint.h>

#define MOY_PMEM_FILE "pmem.bin"
#define MOY_PMEM_CELLS 256
#define MOY_PMEM_SLOT 1536

// 1: `cells` holds the newest valid record; 0: none is (no file, both slots
// torn), `cells` is all zero. A medium error answers 0 too.
int moy_pmem_read(const char *path, int32_t cells[MOY_PMEM_CELLS]);
// 0, or the medium's code (MOY_ENOMEM where the scratch is short).
int moy_pmem_write(const char *path, const int32_t cells[MOY_PMEM_CELLS]);
// The log's records at or under `path` (NULL: all) written back to their
// files now: 0, or the first write's error.
int moy_pmem_settle(const char *path);
// The log's records at or under `path` dropped, unwritten.
void moy_pmem_forget(const char *path);
// One step of the log's quiet-moment work (a path written back, else a
// sector erased): 1 when it did one, 0 when nothing is left. A write-back
// the medium refuses is tried again after the next save.
int moy_pmem_tidy(void);

#endif // MOY_PMEM_H
