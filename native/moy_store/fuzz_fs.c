// The crash-safe write cut at every block write: oofatfs on a RAM card and
// littlefs2 on a RAM flash, under the sanitizers.
//
// tools/moy_index_spike.py --component fs builds it (`sanitize`), and so does
// tests/test_moy_store.py. `fuzz_fs --matrix` is the power-cut matrix: every
// scripted write sequence, on both media, cut at every block write it makes;
// `fuzz_fs SEED RUNS` (MOY_FS_FUZZ_MAIN) runs seeded random sequences with a
// random cut; under libFuzzer the input is the program.
//
// A cut at step k lands the first k block writes (a sector, a prog or an
// erase) and drops every one after it -- the medium as the power left it,
// while the code runs on against a device that no longer listens. The volume
// is then mounted afresh from the RAM image, the store's marker cache dropped
// as a reboot drops it, and every file the sequence touched must read as the
// version before the sequence or the version it was writing, whole.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FFCONF_H "lib/oofatfs/ffconf.h"
#include "lib/littlefs/lfs2.h"
#include "lib/oofatfs/ff.h"
#include "lib/oofatfs/diskio.h"
#include "moy_fs.h"

#define CHECK(c) do { \
        if (!(c)) { \
            fprintf(stderr, "fuzz_fs: %s:%d: %s (medium %d, case %d, cut %ld)\n", \
                    __FILE__, __LINE__, #c, medium, scase, last_cut); \
            abort(); \
        } \
} while (0)

static int medium, scase;
static long cut_at = -1;        // -1: no cut
static long writes;
static long last_cut = -1;   // the cut the sequence under check was made with             // block writes made since the medium was armed

// -- the imports, counted ----------------------------------------------------------

static long scratch, kept;

void *moy_store_alloc(size_t n) {
    size_t *p = calloc(1, n + 2 * sizeof(size_t));
    if (p == NULL) {
        return NULL;
    }
    p[0] = n;
    p[1] = 0;
    scratch += (long)n;
    return p + 2;
}

void *moy_store_keep(size_t n) {
    size_t *p = moy_store_alloc(n);
    if (p != NULL) {
        p[-1] = 1;
        scratch -= (long)n;
        kept += (long)n;
    }
    return p;
}

void moy_store_free(void *q, size_t n) {
    if (q == NULL) {
        return;
    }
    size_t *p = (size_t *)q - 2;
    if (p[0] != n) {
        fprintf(stderr, "fuzz_fs: freed %zu bytes of %zu\n", n, p[0]);
        abort();
    }
    if (p[1]) {
        kept -= (long)n;
    } else {
        scratch -= (long)n;
    }
    free(p);
}

// -- the media -------------------------------------------------------------------------

#define SECTOR 512u
#define SECTORS 512u
#define LBLOCK 512u
#define LBLOCKS 256u

static uint8_t disk[SECTORS * SECTOR];
static uint8_t flash[LBLOCKS * LBLOCK];

static int lands(void) {
    long k = writes++;
    return cut_at < 0 || k < cut_at;
}

DRESULT disk_read(void *drv, BYTE *buff, DWORD sector, UINT count) {
    (void)drv;
    if ((size_t)sector + count > SECTORS) {
        return RES_PARERR;
    }
    memcpy(buff, disk + (size_t)sector * SECTOR, (size_t)count * SECTOR);
    return RES_OK;
}

DRESULT disk_write(void *drv, const BYTE *buff, DWORD sector, UINT count) {
    (void)drv;
    if ((size_t)sector + count > SECTORS) {
        return RES_PARERR;
    }
    for (UINT i = 0; i < count; i++) {
        if (lands()) {
            memcpy(disk + ((size_t)sector + i) * SECTOR, buff + (size_t)i * SECTOR,
                   SECTOR);
        }
    }
    return RES_OK;
}

DRESULT disk_ioctl(void *drv, BYTE cmd, void *buff) {
    (void)drv;
    switch (cmd) {
        case CTRL_SYNC:
            return RES_OK;
        case GET_SECTOR_COUNT:
            *(DWORD *)buff = SECTORS;
            return RES_OK;
        case GET_SECTOR_SIZE:
            *(WORD *)buff = SECTOR;
            return RES_OK;
        case GET_BLOCK_SIZE:
            *(DWORD *)buff = 1;
            return RES_OK;
        case IOCTL_INIT:
        case IOCTL_STATUS:
            *(DSTATUS *)buff = 0;
            return RES_OK;
        default:
            return RES_PARERR;
    }
}

DWORD get_fattime(void) {
    return ((DWORD)(2026 - 1980) << 25) | (10u << 21) | (6u << 16);
}

static int l_read(const struct lfs2_config *c, lfs2_block_t b, lfs2_off_t off,
                  void *buf, lfs2_size_t n) {
    (void)c;
    memcpy(buf, flash + (size_t)b * LBLOCK + off, n);
    return 0;
}

static int l_prog(const struct lfs2_config *c, lfs2_block_t b, lfs2_off_t off,
                  const void *buf, lfs2_size_t n) {
    (void)c;
    if (lands()) {
        memcpy(flash + (size_t)b * LBLOCK + off, buf, n);
    }
    return 0;
}

static int l_erase(const struct lfs2_config *c, lfs2_block_t b) {
    (void)c;
    if (lands()) {
        memset(flash + (size_t)b * LBLOCK, 0xff, LBLOCK);
    }
    return 0;
}

static int l_sync(const struct lfs2_config *c) {
    (void)c;
    return 0;
}

static uint8_t l_rbuf[64], l_pbuf[64], l_look[16];
static const struct lfs2_config lcfg = {
    .read = l_read, .prog = l_prog, .erase = l_erase, .sync = l_sync,
    .read_size = 16, .prog_size = 16, .block_size = LBLOCK,
    .block_count = LBLOCKS, .block_cycles = 100, .cache_size = 64,
    .lookahead_size = 16, .read_buffer = l_rbuf, .prog_buffer = l_pbuf,
    .lookahead_buffer = l_look,
};

static FATFS fatfs;
static lfs2_t lfs;
static int mounted;

int moy_vol_at(const char *path, moy_vol_t *v, const char **rest) {
    if (!mounted) {
        return MOY_ENODEV;
    }
    v->kind = medium == 0 ? MOY_VOL_KIND_FAT : MOY_VOL_KIND_LFS2;
    v->fs = medium == 0 ? (void *)&fatfs : (void *)&lfs;
    *rest = path;
    return 0;
}

static void format(void) {
    cut_at = -1;
    if (medium == 0) {
        static uint8_t work[SECTOR];
        memset(disk, 0, sizeof disk);
        memset(&fatfs, 0, sizeof fatfs);
        fatfs.drv = NULL;
        CHECK(f_mkfs(&fatfs, FM_FAT | FM_SFD, 0, work, sizeof work) == FR_OK);
    } else {
        memset(flash, 0xff, sizeof flash);
        CHECK(lfs2_format(&lfs, &lcfg) == 0);
    }
}

static void mount(void) {
    if (medium == 0) {
        memset(&fatfs, 0, sizeof fatfs);
        CHECK(f_mount(&fatfs) == FR_OK);
    } else {
        memset(&lfs, 0, sizeof lfs);
        CHECK(lfs2_mount(&lfs, &lcfg) == 0);
    }
    mounted = 1;
}

// The power goes: the instance is dropped as it stands, the store's marker
// cache with it, and nothing it holds reaches the medium again.
static void unmount_cold(void) {
    moy_vol_unwind();
    moy_fs_roots_clear();
    mounted = 0;
    CHECK(scratch == 0 && kept == 0);
}

// -- the sequences -------------------------------------------------------------------------

#define ROOT "/carts"
#define FILE_A "/carts/a.moy/main.py"
#define FILE_B "/carts/a.moy/sprites.moygfx"
#define DEST "/carts/a.moy/.journal/0001.snap"
#define DEST_HERE "/carts/a.moy/0001.snap"

static char v1[3000], v2[3000];
static size_t n1, n2;

static void fill(char *p, size_t n, unsigned seed, int utf8) {
    for (size_t i = 0; i < n; i++) {
        seed = seed * 1103515245u + 12345u;
        p[i] = (char)('a' + (seed >> 16) % 26u);
        if (utf8 && i + 1 < n && (seed >> 8) % 11u == 0) {
            p[i++] = (char)0xc3;            // a two-byte code point
            p[i] = (char)0xa9;
        }
    }
}

static int same(const moy_buf_t *b, const char *p, size_t n) {
    return b->n == n && memcmp(b->p, p, n) == 0;
}

// The state before the sequence: the folders, and v1 published at A and B.
static void before(int with_v1) {
    format();
    mount();
    moy_fs_root(ROOT);
    CHECK(moy_fs_mkdir(ROOT) == 0);
    CHECK(moy_fs_mkdir("/carts/a.moy") == 0);
    CHECK(moy_fs_mkdir("/carts/a.moy/.journal") == 0);
    if (with_v1) {
        CHECK(moy_fs_publish(FILE_A, v1, n1) == 0);
        CHECK(moy_fs_write_bytes(FILE_B, v1, n1) == 0);
    }
    unmount_cold();
}

// One sequence, run on the mounted medium. The cases:
//   0  publish v2 over v1 under a root           3  publish v2 where nothing was
//   1  publish v2 over v1 with no root           4  publish v2, then claim it
//   2  write_bytes v2 over v1                    5  publish v2 twice in a row
//   6  publish v2, then claim it into its own folder
static void sequence(void) {
    if (scase != 1) {
        moy_fs_root(ROOT);
    }
    switch (scase) {
        case 0: case 1: case 3:
            moy_fs_publish(FILE_A, v2, n2);
            break;
        case 2:
            moy_fs_write_bytes(FILE_B, v2, n2);
            break;
        case 4: case 6: {
            uint32_t chars, crc;
            if (moy_fs_publish(FILE_A, v2, n2) == 0) {
                moy_fs_stamp(v2, n2, &chars, &crc);
                moy_fs_claim(FILE_A, scase == 4 ? DEST : DEST_HERE, chars, crc);
            }
            break;
        }
        default:
            moy_fs_publish(FILE_A, v2, n2);
            moy_fs_publish(FILE_A, v2, n2);
            break;
    }
}

// After the power came back: every file reads as before or as written.
static void verify(int had_v1) {
    mount();
    if (scase != 1) {
        moy_fs_root(ROOT);
    }
    moy_buf_t b;
    if (scase == 2) {
        int rc = moy_fs_read_file(FILE_B, (size_t)-1, &b);
        if (rc == 0) {
            CHECK(same(&b, v1, n1) || same(&b, v2, n2));
            moy_buf_free(&b);
        } else {
            // FAT's rename does not replace: between the remove and the rename
            // the new bytes are whole beside it, and nothing else is lost.
            CHECK(medium == 0);
            CHECK(moy_fs_read_file(FILE_B ".tmp", (size_t)-1, &b) == 0);
            CHECK(same(&b, v2, n2));
            moy_buf_free(&b);
        }
    } else {
        for (int pass = 0; pass < 2; pass++) {      // the second read: healed
            int rc = moy_fs_read(FILE_A, NULL, &b);
            if (rc == 0) {
                CHECK(same(&b, v2, n2) || (had_v1 && same(&b, v1, n1)));
                moy_buf_free(&b);
            } else {
                CHECK(!had_v1);
            }
        }
        if ((scase == 4 || scase == 6)
            && moy_fs_read_stamped(scase == 4 ? DEST : DEST_HERE, &b) == 0) {
            CHECK(same(&b, v2, n2));
            moy_buf_free(&b);
        }
    }
    unmount_cold();
}

static uint8_t image_before[sizeof disk > sizeof flash ? sizeof disk : sizeof flash];

static void save_image(void) {
    memcpy(image_before, medium == 0 ? disk : flash, medium == 0 ? sizeof disk
                                                                  : sizeof flash);
}

static void load_image(void) {
    memcpy(medium == 0 ? disk : flash, image_before, medium == 0 ? sizeof disk
                                                                  : sizeof flash);
}

// Every cut of one sequence: the number of cuts made.
static long every_cut(void) {
    int had_v1 = scase != 3;
    before(had_v1);
    save_image();
    mount();                    // uncut, to count its writes
    writes = 0;
    cut_at = -1;
    sequence();
    long total = writes;
    last_cut = -1;
    unmount_cold();
    verify(had_v1);
    for (long k = 0; k <= total; k++) {
        load_image();
        mount();
        writes = 0;
        cut_at = last_cut = k;
        sequence();
        cut_at = -1;
        unmount_cold();
        verify(had_v1);
    }
    return total + 1;
}

static long matrix(void) {
    static const size_t sizes[][2] = { {700, 1900}, {1900, 700}, {1200, 1200},
                                       {0, 300} };
    long cuts = 0;
    for (medium = 0; medium < 2; medium++) {
        for (scase = 0; scase < 7; scase++) {
            for (size_t s = 0; s < sizeof sizes / sizeof sizes[0]; s++) {
                n1 = sizes[s][0];
                n2 = sizes[s][1];
                fill(v1, n1, 7u + (unsigned)s, 1);
                fill(v2, n2, 99u + (unsigned)s, 1);
                cuts += every_cut();
            }
        }
    }
    medium = scase = 0;
    return cuts;
}

// A seeded program: the medium, the case, the sizes, the bytes, the cut.
static void run(const uint8_t *data, size_t size) {
    size_t i = 0;
#define NEXT() (i < size ? data[i++] : 0u)
    medium = NEXT() & 1u;
    scase = NEXT() % 7u;
    n1 = ((size_t)NEXT() << 4) % sizeof v1;
    n2 = ((size_t)NEXT() << 4) % sizeof v2;
    unsigned s1 = NEXT(), s2 = NEXT(), same_head = NEXT() & 1u;
    fill(v1, n1, s1, 1);
    fill(v2, n2, s2, 1);
    if (same_head && n2 >= n1) {
        memcpy(v2, v1, n1);         // v1 a prefix of v2: the case recovery reads
    }
    long cut = (long)NEXT() << 8;
    cut |= (long)NEXT();
#undef NEXT
    int had_v1 = scase != 3;
    before(had_v1);
    mount();
    writes = 0;
    cut_at = last_cut = cut % 64;
    sequence();
    cut_at = -1;
    unmount_cold();
    verify(had_v1);
}

#if defined(MOY_FS_FUZZ_MAIN)
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--matrix") == 0) {
        long cuts = matrix();
        printf("fuzz_fs: matrix of %ld cuts, ok\n", cuts);
        return 0;
    }
    uint32_t seed = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 1u;
    unsigned long runs = argc > 2 ? strtoul(argv[2], NULL, 0) : 300ul;
    uint32_t rng = seed ? seed : 1u;
    uint8_t buf[16];
    for (unsigned long r = 0; r < runs; r++) {
        for (size_t k = 0; k < sizeof buf; k++) {
            rng ^= rng << 13;
            rng ^= rng >> 17;
            rng ^= rng << 5;
            buf[k] = (uint8_t)rng;
        }
        run(buf, sizeof buf);
    }
    printf("fuzz_fs: %lu programs, seed %u, ok\n", runs, seed);
    return 0;
}
#else
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    run(data, size);
    return 0;
}
#endif
