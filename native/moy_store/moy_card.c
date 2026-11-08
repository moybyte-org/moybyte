// The card volume, owned (native/moy_store/moy_vol.h):
// the store's own FATFS over a native block device with a read cache, which
// Python mounts at /sd, so VfsFat and the store share one instance and every
// sector FatFS is handed comes through here.
//
// The instance (an fs_user_mount_t, the VfsFat object) and the cache live in
// PSRAM, outside the gc heap: a VM stop leaves them. The block device is
// NATIVE (MP_BLOCKDEV_FLAG_NATIVE): FatFS's reads and writes reach the C below
// with no VM call. Its sectors come from the card driver: moy_sd's
// moy_sd_card_io on a board (the T-Deck's card attached to the panel's host,
// the Guition S3's on its own SPI3, the P4s' on SDMMC slot 0), or, on the
// host, a Python object with moy_sd's read/write verbs, which is how
// tests/test_store_on_vfs.py drives it.
//
// The cache is the T-Deck's (dev 638661e), in C under every board's card:
// moy_cache.h, with its zero runs over the mounted volume's clusters.

#include <string.h>

#include "py/mperrno.h"
#include "py/objarray.h"
#include "py/runtime.h"

#if MICROPY_VFS_FAT
#ifndef FFCONF_H
#define FFCONF_H "lib/oofatfs/ffconf.h"
#endif
#include "extmod/vfs.h"
#include "extmod/vfs_fat.h"

#include "moy_cache.h"
#include "moy_vol.h"

#if MOY_VOL_FAT_WEAK
extern __typeof__(f_mount) f_mount __attribute__((weak));
#endif

#define SECTOR MOY_CACHE_SECTOR
#define SLOTS MOY_CACHE_SLOTS

// The card driver on a board, where the image takes one (native/moy_sd/
// moy_sd.h): its bring-up and its sectors, over SPI on the S3s and the SDMMC
// host on the P4s.
extern int moy_sd_card_io(uint32_t start, uint8_t *buf, uint32_t count, int write)
    __attribute__((weak));
extern int moy_sd_card_up(uint32_t *sectors, const char **what) __attribute__((weak));

typedef struct {
    fs_user_mount_t *vfs;
    uint32_t sectors;
    moy_cache_t cache;
    int held;                   // lent out whole (USB mass storage): no mount
} card_t;

// In PSRAM with the rest of the volume, made at the first call: the card adds
// nothing to internal SRAM.
static card_t *card_p;
#define card (*card_p)

void *moy_store_raw_alloc(size_t n);

MP_REGISTER_ROOT_POINTER(mp_obj_t moy_store_card_driver);

static int io(void *ctx, uint32_t start, uint8_t *buf, uint32_t n, int write) {
    (void)ctx;
    mp_obj_t drv = MP_STATE_VM(moy_store_card_driver);
    if (drv == MP_OBJ_NULL || drv == mp_const_none) {
        if (moy_sd_card_io == NULL) {
            return MP_ENODEV;
        }
        return moy_sd_card_io(start, buf, n, write) == 0 ? 0 : MP_EIO;
    }
    mp_obj_t args[3] = {
        mp_obj_new_int_from_uint(start),
        mp_obj_new_memoryview('B' | MP_OBJ_ARRAY_TYPECODE_FLAG_RW, n * SECTOR, buf),
        mp_obj_new_int_from_uint(n),
    };
    mp_obj_t fn = mp_load_attr(drv, write ? MP_QSTR_write : MP_QSTR_read);
    mp_call_function_n_kw(fn, 3, 0, args);
    return 0;
}

static int need_card(void) {
    if (card_p == NULL) {
        card_p = moy_store_raw_alloc(sizeof(card_t));
        if (card_p == NULL) {
            return 0;
        }
        memset(card_p, 0, sizeof(card_t));
        card.cache.io = io;
        moy_cache_drop_all(&card.cache);
    }
    return 1;
}

static mp_uint_t card_read(uint8_t *buf, uint32_t block, uint32_t n) {
    moy_store_tick();
    int rc = moy_cache_read(&card.cache, buf, block, n);
    return rc == MOY_CACHE_ESTALE ? MP_EIO : rc;
}

static mp_uint_t card_write(const uint8_t *buf, uint32_t block, uint32_t n) {
    moy_store_tick();
    return moy_cache_write(&card.cache, buf, block, n);
}

static mp_obj_t card_count(mp_obj_t self) {
    (void)self;
    return mp_obj_new_int_from_uint(card.sectors);
}
static MP_DEFINE_CONST_FUN_OBJ_1(card_count_obj, card_count);

// How the running VM started (native/moy_kernel): weak, so an image without
// the kernel (the host, the unix port) never returns.
extern int moy_kernel_start(void) __attribute__((weak));

// The card's FATFS while it is mounted, or NULL: what the store resolves
// "/sd" to while no VM runs (modmoy_store.c's moy_vol_at), and what the next
// VM mounts again.
void *moy_store_card_fatfs(void) {
    if (card_p == NULL || card.vfs == NULL || card.vfs->fatfs.fs_type == 0) {
        return NULL;
    }
    return &card.vfs->fatfs;
}

// The store's FATFS mounted over the card's `sectors`, with no VM: 0, or an
// errno value. A second call drops the cache and mounts the same instance
// again (a card may have changed).
int moy_store_card_mount(uint32_t sectors) {
    if (!need_card()) {
        return MP_ENOMEM;
    }
    if (card.held) {
        return MP_EBUSY;
    }
    if (card.vfs == NULL) {
        card.vfs = moy_store_raw_alloc(sizeof(fs_user_mount_t));
        card.cache.slots = moy_store_raw_alloc((size_t)SLOTS * SECTOR);
        uint8_t *zeros = moy_store_raw_alloc((size_t)MOY_CACHE_RUN * SECTOR);
        if (card.vfs == NULL || card.cache.slots == NULL || zeros == NULL) {
            return MP_ENOMEM;
        }
        memset(zeros, 0, (size_t)MOY_CACHE_RUN * SECTOR);
        card.cache.zeros = zeros;
    }
    fs_user_mount_t *v = card.vfs;
    memset(v, 0, sizeof *v);
    v->base.type = &mp_fat_vfs_type;
    v->blockdev.flags = MP_BLOCKDEV_FLAG_NATIVE;
    v->blockdev.block_size = SECTOR;
    v->blockdev.readblocks[0] = MP_OBJ_FROM_PTR(&card_count_obj);     // present
    v->blockdev.readblocks[2] = (mp_obj_t)(uintptr_t)card_read;
    v->blockdev.writeblocks[0] = MP_OBJ_FROM_PTR(&card_count_obj);    // writable
    v->blockdev.writeblocks[2] = (mp_obj_t)(uintptr_t)card_write;
    v->blockdev.u.old.count[0] = MP_OBJ_FROM_PTR(&card_count_obj);
    v->blockdev.u.old.count[1] = MP_OBJ_FROM_PTR(v);
    v->fatfs.drv = v;
    card.sectors = sectors;
    moy_cache_drop_all(&card.cache);
    card.cache.every = 0;
    FRESULT res = f_mount(&v->fatfs);
    if (res != FR_OK) {
        return fresult_to_errno_table[res];
    }
    card.cache.base = (uint32_t)v->fatfs.database;
    card.cache.every = v->fatfs.csize;
    return 0;
}

// THE HOLD: the card lent out whole, to a computer over USB mass storage
// (native/moy_usbdisk), with the store closed. Holding answers EBUSY while
// the volume is still in the VFS mount table (VfsFat's umount keeps its FATFS
// mounted, so Python unmounts it first: runtime/system/usb_drive.py), then
// puts the cache's held zero run on the card and closes the FATFS (every
// operation FatFS finishes has synced its window). While held every mount
// answers EBUSY, the kernel's (a VM start, the recovery floor) and Python's
// alike, and the store resolves /sd to nothing (moy_store_card_fatfs).
// Letting go drops the cache: the computer may have written any sector.
int moy_store_card_hold(int on) {
    if (!need_card()) {
        return MP_ENOMEM;
    }
    if (on && card.vfs != NULL) {
        for (mp_vfs_mount_t *m = MP_STATE_VM(vfs_mount_table); m != NULL; m = m->next) {
            if (m->obj == MP_OBJ_FROM_PTR(card.vfs)) {
                return MP_EBUSY;
            }
        }
        if (card.vfs->fatfs.fs_type != 0) {
            if (moy_cache_flush(&card.cache) != 0) {
                return MP_EIO;
            }
            card.vfs->fatfs.fs_type = 0;
        }
    }
    card.held = on != 0;
    moy_cache_drop_all(&card.cache);
    return 0;
}

int moy_store_card_held(void) {
    return card_p != NULL && card.held;
}

// The board's card brought up and mounted with no VM, for the kernel (the
// recovery floor's shell, docs/os_2026-11/system.md §2), over the same C the
// console's Python calls (`moy_sd.up()`, `moy_store.card(sectors)`): 0 when
// the volume is mounted (already, or now), else an error with the step in
// `*what`. On the T-Deck the panel must be up first: the card rides its bus.
int moy_store_card_up(const char **what) {
    *what = "";
    if (moy_store_card_fatfs() != NULL) {
        return 0;
    }
    if (moy_sd_card_up == NULL) {
        *what = "no card";
        return MP_ENODEV;
    }
    uint32_t sectors;
    int err = moy_sd_card_up(&sectors, what);
    if (err != 0) {
        return err;
    }
    *what = "mount";
    return moy_store_card_mount(sectors);
}

// card(sectors, driver=None) -> the volume, a VfsFat to vfs.mount: the store's
// FATFS mounted over the card (moy_store_card_mount). Except at a RETURN start
// (docs/history/kernel_cartpath_2026-10.md section 5.4): the volume the kernel
// held open through the VM's stop is handed over as it is, the card
// untouched, because a failed remount on the Guition S3 stays failed until a
// reboot.
static mp_obj_t mod_card(size_t n_args, const mp_obj_t *args) {
    uint32_t sectors = (uint32_t)mp_obj_get_int(args[0]);
    if (!need_card()) {
        mp_raise_type(&mp_type_MemoryError);
    }
    MP_STATE_VM(moy_store_card_driver) = n_args > 1 ? args[1] : mp_const_none;
    if (moy_kernel_start != NULL && moy_kernel_start() == 1 && n_args < 2
        && moy_store_card_fatfs() != NULL && card.sectors == sectors) {
        return MP_OBJ_FROM_PTR(card.vfs);
    }
    int rc = moy_store_card_mount(sectors);
    if (rc == MP_ENOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (rc != 0) {
        mp_raise_OSError(rc);
    }
    return MP_OBJ_FROM_PTR(card.vfs);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_store_card_obj, 1, 2, mod_card);

// card_hold(on) -> None: the hold (moy_store_card_hold), raising
// OSError(EBUSY) while the volume is mounted; card_hold() -> whether held.
static mp_obj_t mod_card_hold(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        return mp_obj_new_bool(moy_store_card_held());
    }
    int rc = moy_store_card_hold(mp_obj_is_true(args[0]));
    if (rc == MP_ENOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (rc != 0) {
        mp_raise_OSError(rc);
    }
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_store_card_hold_obj, 0, 1, mod_card_hold);

// card_stats(check=None, skip_drop=None) -> (reads, hits, handed, stale):
// what the card was asked for, what the cache answered, the sectors FatFS
// was handed and the hits a check found stale; the switches are the tests'.
static mp_obj_t mod_card_stats(size_t n_args, const mp_obj_t *args) {
    if (!need_card()) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (n_args > 0 && args[0] != mp_const_none) {
        card.cache.check = mp_obj_is_true(args[0]);
    }
    if (n_args > 1 && args[1] != mp_const_none) {
        card.cache.skip_drop = mp_obj_is_true(args[1]);
    }
    mp_obj_t t[4] = { mp_obj_new_int_from_uint(card.cache.reads), mp_obj_new_int_from_uint(card.cache.hits),
                      mp_obj_new_int_from_uint(card.cache.handed), mp_obj_new_int_from_uint(card.cache.stale) };
    return mp_obj_new_tuple(4, t);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_store_card_stats_obj, 0, 2, mod_card_stats);

#endif // MICROPY_VFS_FAT
