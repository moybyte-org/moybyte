// The volume seam: one interface over the file-system libraries an image
// already links -- oofatfs on a card, littlefs2 on internal flash, POSIX on the
// host and in the browser -- called at the library level, never through the
// VFS's object API (which allocates on the gc heap and raises through nlr).
//
// A volume is a library and the ONE instance of it that owns a path, never a
// second copy (2026-10-06). The kernel owns the consoles' instances: the card's
// FATFS over a native block device (moy_card.c), which Python mounts at /sd so
// VfsFat and the store share it, and the internal flash's lfs2_t (moy_kvfs.c),
// which Python reaches through the kernel's KVfs. A volume the kernel does not
// own, the store BORROWS for the length of a call: moy_vol_at resolves a path
// through MicroPython's mount table to a VfsFat's FATFS, a VfsLfs2's lfs2_t
// (its private layout checked on every resolve, the guard on a tag bump) or
// VfsPosix, and the pointer never outlives the call. The store's code is the
// same over a borrowed and an owned instance.
//
// ESP-IDF's VFS was REJECTED (2026-10-06): its fatfs and oofatfs both define a
// global f_open, so one image cannot link both; IDF builds fatfs without
// relative paths (FF_FS_RPATH 0), which the scan's moy_vol_enter stands on; and
// IDF ships no littlefs.
//
// Every call returns 0 or an errno value (MicroPython's numbering, which is
// Linux's; a POSIX volume hands back its C library's own, as VfsPosix does).
// Paths are NUL-terminated and relative to the volume: what moy_vol_at hands
// back as `rest`. A file is allocated from moy_store_alloc and freed by
// moy_vol_close; nothing is open when a store call returns, and
// moy_vol_unwind closes whatever a raised call left open.

#ifndef MOY_VOL_H
#define MOY_VOL_H

#include <stddef.h>
#include <stdint.h>

#ifdef MOY_STORE_MICROPYTHON
#include "py/mpconfig.h"
#ifndef MOY_VOL_FAT
#define MOY_VOL_FAT MICROPY_VFS_FAT
#endif
#ifndef MOY_VOL_LFS2
#define MOY_VOL_LFS2 MICROPY_VFS_LFS2
#endif
#ifndef MOY_VOL_POSIX
#define MOY_VOL_POSIX MICROPY_VFS_POSIX
#endif
#endif
#ifndef MOY_VOL_FAT
#define MOY_VOL_FAT 0
#endif
#ifndef MOY_VOL_LFS2
#define MOY_VOL_LFS2 0
#endif
#ifndef MOY_VOL_POSIX
#define MOY_VOL_POSIX 1
#endif

// The errno values the store returns itself.
#define MOY_EIO 5
#define MOY_EBADF 9
#define MOY_ENOMEM 12
#define MOY_EACCES 13
#define MOY_EBUSY 16
#define MOY_EEXIST 17
#define MOY_EXDEV 18
#define MOY_ENODEV 19
#define MOY_EISDIR 21
#define MOY_EINVAL 22
#define MOY_EMFILE 24
#define MOY_EFBIG 27
#define MOY_ENOSPC 28
#define MOY_EROFS 30

enum {
    MOY_VOL_KIND_POSIX = 1,
    MOY_VOL_KIND_FAT = 2,       // fs is a FATFS *
    MOY_VOL_KIND_LFS2 = 3,      // fs is an lfs2_t *
    MOY_VOL_KIND_VM = 4,        // the volume table's: a mount only the VM's task works
};

typedef struct {
    uint8_t kind;
    void *fs;
} moy_vol_t;

enum {
    MOY_VOL_READ = 1,
    MOY_VOL_WRITE = 2,          // created, or truncated
    MOY_VOL_APPEND = 3,         // created, or written at its end
    MOY_VOL_HELD = 0x10,        // or'd in: the file outlives the call (below)
};

typedef struct moy_vol_file moy_vol_file_t;

// `mtime` is seconds since 1970 where the medium keeps one (FAT's stamp,
// littlefs's attribute 1 in nanoseconds, POSIX's st_mtime), else 0: what
// os.stat()[8] reads, for ordering a listing newest first.
typedef struct {
    uint8_t is_dir;
    uint32_t size;
    int64_t mtime;
} moy_vol_stat_t;

// One entry of a listing: its name (`len` bytes, NUL-terminated), whether it
// is a folder, and its size. A nonzero return stops the walk and is returned.
typedef int (*moy_vol_ent_fn)(void *ctx, const char *name, size_t len,
                              int is_dir, uint32_t size);

int moy_vol_open(const moy_vol_t *v, const char *path, int mode,
                 moy_vol_file_t **out);
int moy_vol_read(moy_vol_file_t *f, void *buf, size_t n, size_t *got);
int moy_vol_write(moy_vol_file_t *f, const void *buf, size_t n);
int moy_vol_size(moy_vol_file_t *f, uint32_t *n);
int moy_vol_seek(moy_vol_file_t *f, uint32_t at);   // the next read's offset
int moy_vol_close(moy_vol_file_t *f);           // frees f; the first error wins
void moy_vol_unwind(void);                      // close every file still open
// A file opened with MOY_VOL_HELD is its opener's: made from moy_store_keep and
// off the list moy_vol_unwind walks, so a raised call elsewhere leaves it, and
// only its own moy_vol_close closes it. A compiled cart's session holds the
// file it streams this way (moy_files.h).

// THE STORE'S LOCK. Any task may make a store op: the VM's, a compiled or Lua
// program's own, a service's, the kernel's frame while no VM runs. One
// mutex (a FreeRTOS mutex on a board, a pthread one on the host and the
// desktop, nothing in the threadless browser) keeps them apart, recursive
// for its holder:
//
//   * a whole op holds it, from moy_vol_gate_enter to moy_vol_gate_leave
//     (the bus gate inside it), and a working folder holds it from
//     moy_vol_enter to moy_vol_leave, so a FAT volume's working folder moves
//     only while one op owns the volume;
//   * every call of the seam below takes it for itself, so the library's
//     instance is never worked by two tasks at once;
//   * VfsFat's calls take it through FatFS's own grant (oofatfs is built
//     FF_FS_REENTRANT, each FATFS's sync object this lock where the volume is
//     one a task can reach, moy_vol_fat_lockable), and KVfs's around each
//     littlefs call (moy_kvfs.c), so the VM's Python waits for a program's op
//     at a library call's edge and never inside one;
//   * on the T-Deck the panel's kick takes it, so no frame starts while a
//     task holds the card's bus (modmoy_lcd.c).
//
// moy_store_unlock_to drops the calling task's holds down to `depth`, what a
// raised call's unwind does with the depth it entered at. moy_store_lock_stats
// is the meter: how many outermost holds, the longest, and the longest any
// task and the VM's task waited (moy_store_on_vm, which the binding defines,
// says which task that is; weak). `reset` zeroes it after reading.
void moy_store_lock(void);
void moy_store_unlock(void);
int moy_store_lock_depth(void);         // the calling task's holds
void moy_store_unlock_to(int depth);
uintptr_t moy_store_me(void);           // the calling task, never 0
int moy_store_on_vm(void);
typedef struct {
    uint32_t holds;
    uint32_t hold_us;                   // the longest outermost hold
    uint32_t wait_us;                   // the longest wait, any task
    uint32_t vm_wait_us;                // the longest wait of the VM's task
    uint32_t waits;                     // the locks that found it held
} moy_store_lock_stats_t;
void moy_store_lock_stats(moy_store_lock_stats_t *out, int reset);

// THE VOLUME TABLE: the volumes a task other than the VM's resolves a path
// through, each a mount point and the instance under it, longest match first.
// Only an instance whose sectors never enter the VM is listed: the kernel's
// card over its native driver, the kernel's internal flash (KVfs), and POSIX
// at "/". The kernel lists its own at their mounts (the card at /sd, the
// flash at /), and the VM's binding lists what its mount table holds of them
// at each change of that table (modmoy_store.c), so a path a task resolves
// is the one the VM would. Every other mount of the VM's is listed too, as
// MOY_VOL_KIND_VM, so a path under it is never taken for one under the volume
// mounted above it. moy_vol_table_drop takes every entry over `fs` out (the
// card lent out whole); moy_vol_table_at answers ENODEV for a path no entry
// holds and for one under a mount of the VM's alone.
#define MOY_VOL_TABLE_N 6u
#define MOY_VOL_TABLE_AT 16u                 // a mount point, its NUL counted
int moy_vol_table_set(const char *at, uint8_t kind, void *fs);
void moy_vol_table_drop(void *fs);
void moy_vol_table_clear(void);
int moy_vol_table_at(const char *path, moy_vol_t *v, const char **rest);
// Each mount point's first folder under "/" ("sd" for /sd), as moy_vol_mounts.
int moy_vol_table_mounts(int (*fn)(void *arg, const char *name), void *arg);
// The FAT instance whose FatFS calls take the lock (one: the card's, over its
// native driver), or NULL. Set before its f_mount.
void moy_vol_fat_lockable(void *fs);

// The board's bus rule around an op on a card: on the T-Deck the card shares
// the panel's SPI host, and the panel's driver registers a gate that drains
// the flush and holds the next one off while `on` (moy_lcd's sd_guard, which
// nests). NULL, the default, where a card has a bus of its own.
// moy_vol_gate_enter takes the store's lock, and the gate as well when `path`
// is on a FAT volume and a gate is registered, and answers what it took for
// moy_vol_gate_leave; or -1, nothing taken, when `path` is on no volume the
// calling task can reach (a task other than the VM's, a path the volume table
// does not hold): the op belongs on the VM's task. A caller that touches the
// store from C brackets every op with the two, as the Python store's session
// does.
typedef void (*moy_vol_gate_fn)(int on);
void moy_vol_set_gate(moy_vol_gate_fn fn);
int moy_vol_gate_enter(const char *path);
void moy_vol_gate_leave(int entered);

int moy_vol_stat(const moy_vol_t *v, const char *path, moy_vol_stat_t *st);
int moy_vol_list(const moy_vol_t *v, const char *dir, moy_vol_ent_fn fn,
                 void *ctx);
int moy_vol_mkdir(const moy_vol_t *v, const char *path);
int moy_vol_remove(const moy_vol_t *v, const char *path);
// `dst` must not exist, on every backend, as FAT has it: EEXIST.
int moy_vol_rename(const moy_vol_t *v, const char *src, const char *dst);
// `src` over `dst`: the medium's own atomic rename where it has one (littlefs,
// POSIX), and on FAT the rename after `dst` is removed.
int moy_vol_replace(const moy_vol_t *v, const char *src, const char *dst);

// A working folder. moy_vol_enter makes `dir` the place a relative name
// starts; moy_vol_here_name gives the name to open a file of it by, and
// moy_vol_leave puts the volume's working folder back as it was. On FAT it is
// the volume's own: f_chdir on the FATFS, which then looks a relative name up
// in that folder alone, where a full path walks every folder above it again;
// leave restores every working-folder field (cdir, and exFAT's cdc_*) as one,
// so the VM's working folder is untouched. The store's lock is held from enter
// to leave, so no other task's call sees the folder moved. On littlefs and
// POSIX it is a path prefix. A raised call leaves through moy_vol_unwind,
// which leaves too.
typedef struct moy_vol_here {
    struct moy_vol_here *next;      // what moy_vol_unwind leaves
    uintptr_t who;                  // the task that entered it
    moy_vol_t v;
    char *dir;                      // the folder, NUL-terminated
    size_t dir_n;
    uint32_t cdir, cdc_scl, cdc_size, cdc_ofs;
    char *name;                     // moy_vol_here_name's scratch
    size_t name_cap;
} moy_vol_here_t;

int moy_vol_enter(const moy_vol_t *v, const char *dir, moy_vol_here_t *h);
// The name of `name` in the working folder: `name` itself where the volume
// keeps the folder (FAT), else the folder joined with it. NULL with no memory.
// The pointer is good until the next call on `h`.
const char *moy_vol_here_name(moy_vol_here_t *h, const char *name);
void moy_vol_leave(moy_vol_here_t *h);

// Imported from the host. moy_vol_at resolves `path` to the volume that owns
// it and the path within it (off the VM's task through the volume table, and
// never raising there); moy_store_alloc is a call's scratch (zeroed), the
// calling task's (what a raised call's unwind frees is its own task's alone),
// moy_store_keep what outlives the call (the marker cache); both are freed by
// moy_store_free with the size they were made with.
int moy_vol_at(const char *path, moy_vol_t *v, const char **rest);
// The folders mounted under the root beside the root's own volume ("sd" for
// /sd), each once to `fn` until it answers non-zero (its answer returned).
// Optional: a host with one volume over the whole tree defines none, and a
// caller takes it weak.
int moy_vol_mounts(int (*fn)(void *arg, const char *name), void *arg);
//
// moy_store_tick is called at every open and every listing: on a board it
// feeds the kernel's task watchdog when the frame loop has armed it, so a
// long store call (a seed, a scan, a pack, a big load) is never a hang.
void moy_store_tick(void);
void *moy_store_alloc(size_t n);
void *moy_store_keep(size_t n);
void moy_store_free(void *p, size_t n);

#endif // MOY_VOL_H
