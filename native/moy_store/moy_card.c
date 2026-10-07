// The card volume, owned (docs/kernel_store_2026-10.md section 1, slice 8):
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
// moy_cache.h.

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

// The card driver on a board, where the image takes one: moy_sd's
// moy_sd_card_io, over SPI on the S3s and the SDMMC host on the P4s.
extern int moy_sd_card_io(uint32_t start, uint8_t *buf, uint32_t count, int write)
    __attribute__((weak));

typedef struct {
    fs_user_mount_t *vfs;
    uint32_t sectors;
    moy_cache_t cache;
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

static void need_card(void) {
    if (card_p == NULL) {
        card_p = moy_store_raw_alloc(sizeof(card_t));
        if (card_p == NULL) {
            mp_raise_type(&mp_type_MemoryError);
        }
        memset(card_p, 0, sizeof(card_t));
        card.cache.io = io;
        moy_cache_drop_all(&card.cache);
    }
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

// card(sectors, driver=None) -> the volume, a VfsFat to vfs.mount: the store's
// FATFS mounted over the card. A second call drops the cache and mounts the
// same instance again (a card may have changed).
static mp_obj_t mod_card(size_t n_args, const mp_obj_t *args) {
    uint32_t sectors = (uint32_t)mp_obj_get_int(args[0]);
    need_card();
    MP_STATE_VM(moy_store_card_driver) = n_args > 1 ? args[1] : mp_const_none;
    if (card.vfs == NULL) {
        card.vfs = moy_store_raw_alloc(sizeof(fs_user_mount_t));
        card.cache.slots = moy_store_raw_alloc((size_t)SLOTS * SECTOR);
        if (card.vfs == NULL || card.cache.slots == NULL) {
            mp_raise_type(&mp_type_MemoryError);
        }
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
    FRESULT res = f_mount(&v->fatfs);
    if (res != FR_OK) {
        mp_raise_OSError(fresult_to_errno_table[res]);
    }
    return MP_OBJ_FROM_PTR(v);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_store_card_obj, 1, 2, mod_card);

// card_stats(check=None, skip_drop=None) -> (reads, hits, handed, stale):
// what the card was asked for, what the cache answered, the sectors FatFS
// was handed and the hits a check found stale; the switches are the tests'.
static mp_obj_t mod_card_stats(size_t n_args, const mp_obj_t *args) {
    need_card();
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
