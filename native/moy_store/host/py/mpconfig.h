// The few MicroPython settings oofatfs's ffconf.h reads, for the host builds
// of native/moy_store (the power-cut matrix and the fuzz): the boards' FAT
// configuration, with RAM-card sectors of 512 bytes.
#ifndef MOY_STORE_HOST_MPCONFIG_H
#define MOY_STORE_HOST_MPCONFIG_H
#define MICROPY_FATFS_ENABLE_LFN (1)
#define MICROPY_FATFS_RPATH (2)
#define MICROPY_FATFS_MAX_SS (512)
#define MICROPY_FATFS_LFN_CODE_PAGE 437
#endif
