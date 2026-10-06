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
#include "moy_cat.h"
#include "moy_fs.h"
#include "moy_journal.h"
#include "moy_json.h"
#include "moy_load.h"
#include "moy_pack.h"
#include "moy_seed.h"

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

void moy_store_tick(void) {
}

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

// -- the catalogue over whatever the medium holds ---------------------------------------

static long scanned;

static int count_entry(void *ctx, const moy_cat_entry_t *e) {
    (void)ctx;
    CHECK(e->folder_n > 0 && e->main != NULL && e->man != NULL);
    CHECK(moy_json_valid(e->man, e->man_n) == MOY_JSON_OK);
    for (size_t i = 0; i < e->scenes_n; i++) {
        CHECK(e->scenes[i] != NULL);
    }
    scanned++;
    return 0;
}

static int count_cart(void *ctx, const moy_cart_t *c) {
    (void)ctx;
    CHECK(c->src != NULL && c->e.main != NULL);
    for (size_t i = 0; i < c->scenes_n; i++) {
        CHECK(c->scenes[i].text != NULL);
    }
    scanned++;
    return 0;
}

// A scan of the root and an entry of each folder in it: whatever a cut left,
// it reads or refuses, leaves the working folder as it found it and holds no
// scratch after.
static void scan_all(const char *root) {
    DWORD cdir = fatfs.cdir;
    moy_cat_scan(root, count_entry, NULL, NULL);
    moy_cat_entry(root, count_entry, NULL, NULL);
    moy_cat_entry("/carts/a.moy", count_entry, NULL, NULL);
    moy_cat_load("/carts/a.moy", count_cart, NULL, NULL);
    moy_cat_load("/carts/moybyte.hop.moy", count_cart, NULL, NULL);
    for (int i = 0; i < 6; i++) {
        char p[32];
        snprintf(p, sizeof p, "/carts/c%d.moy", i);
        moy_cat_load(p, count_cart, NULL, NULL);
    }
    if (medium == 0) {
        CHECK(fatfs.cdir == cdir);
    }
    CHECK(scratch == 0);
}

// moy_json over arbitrary bytes: whatever it accepts it can write again as
// text it accepts, with the same kinds, and nothing it refuses is read.
static void json_bytes(const uint8_t *data, size_t size) {
    moy_buf_t inf;
    if (moy_seed_inflate(data, size, &inf) == 0) {     // any stream: refused or whole
        moy_buf_free(&inf);
    }
    const char *t = (const char *)data, *end = t + size;
    int ok = moy_json_valid(t, size) == MOY_JSON_OK;
    const char *s = moy_json_ws(t, end);
    const char *e = moy_json_value(s, end, 1u);
    CHECK(ok == (e != NULL && moy_json_ws(e, end) == end));
    if (e == NULL) {
        return;
    }
    size_t n = moy_json_canon(s, e, NULL, 0);
    char *out = malloc(n + 1u);
    CHECK(out != NULL);
    CHECK(moy_json_canon(s, e, out, n) == n);
    CHECK(moy_json_valid(out, n) == MOY_JSON_OK);
    CHECK(moy_json_kind(out, out + n) == moy_json_kind(s, e)
          || (moy_json_kind(s, e) == MOY_JSON_INT));
    int64_t v;
    moy_json_int(s, e, &v);
    moy_json_truthy(s, e);
    if (*s == '{' || *s == '[') {
        moy_json_iter_t it;
        const char *k, *ke, *x, *xe;
        moy_json_iter(&it, s, e);
        while (moy_json_next(&it, &k, &ke, &x, &xe)) {
            if (k != NULL) {
                char *d = malloc(moy_json_strlen(k, ke) + 1u);
                CHECK(d != NULL);
                CHECK(moy_json_str(k, ke, d) == moy_json_strlen(k, ke));
                moy_json_str_eq(k, ke, k, ke);
                free(d);
            }
        }
        const char *g, *ge;
        moy_json_get(s, e, "title", &g, &ge);
    }
    free(out);
}

// A random tree for the catalogue: cart folders whose manifests, sheets and
// scenes come from the input, scanned on the medium.
static void run_cat(const uint8_t *data, size_t size) {
    size_t i = 1;
#define NEXT() (i < size ? data[i++] : 0u)
    medium = NEXT() & 1u;
    format();
    mount();
    CHECK(moy_fs_mkdir("/carts") == 0);
    unsigned carts = NEXT() % 6u;
    char path[96];
    static const char *const manifests[] = {
        "{\"title\": \"A\", \"main\": \"main.py\", \"icon\": [3, 2, 2]}",
        "{\"format\": \"moy-1\", \"assets\": {\"scenes\": [\"b\", 3, \"a\"]}}",
        "{ broken", "[1]", "{\"runtime\": \"wasm\", \"writable\": [\"x\"], \"memory\": 2}",
        "{\"canvas\": {\"width\": 160, \"height\": 120}, \"input\": [\"touch\"]}",
        "{\"assets\": {\"scenes\": [[1]]}}", "{\"main\": 7}",
    };
    for (unsigned c = 0; c < carts; c++) {
        snprintf(path, sizeof path, "/carts/c%u.moy", c);
        moy_fs_mkdir(path);
        unsigned roll = NEXT();
        char file[128];
        if (roll & 1u) {
            snprintf(file, sizeof file, "%s/manifest.json", path);
            size_t take = NEXT() % 48u;
            if (roll & 2u && i + take <= size) {
                moy_fs_write(file, data + i, take);     // bytes from the input
                i += take;
            } else {
                const char *m = manifests[NEXT() % (sizeof manifests / sizeof manifests[0])];
                moy_fs_write(file, m, strlen(m));
            }
        }
        if (roll & 4u) {
            snprintf(file, sizeof file, "%s/main.py", path);
            moy_fs_write(file, "x", 1);
        }
        if (roll & 8u) {
            snprintf(file, sizeof file, "%s/sprites.moygfx", path);
            char sheet[600];
            size_t n = 0;
            unsigned lines = NEXT() % 20u;
            for (unsigned l = 0; l < lines && n + 40 < sizeof sheet; l++) {
                unsigned w = NEXT() % 36u;
                for (unsigned k = 0; k < w; k++) {
                    sheet[n++] = "0123456789abcdef \t"[NEXT() % 18u];
                }
                sheet[n++] = (NEXT() & 1u) ? '\n' : '\r';
            }
            moy_fs_write(file, sheet, n);
        }
        if (roll & 16u) {
            snprintf(file, sizeof file, "%s/scenes", path);
            if (roll & 32u) {
                moy_fs_write(file, "f", 1);
            } else {
                moy_fs_mkdir(file);
                snprintf(file, sizeof file, "%s/scenes/a.moyscene", path);
                moy_fs_write(file, "[]", 2);
                snprintf(file, sizeof file, "%s/scenes/b.moyscene", path);
                moy_fs_write(file, "[]", 2);
            }
        }
    }
    // the archive: a cart packed and unpacked again, then the archive with
    // input bytes written over part of it, unpacked
    char top[64];
    if (moy_pack("/carts/c0.moy", "c0.moy", "/carts/p.zip", NEXT() & 1u) >= 0) {
        CHECK(moy_unpack("/carts/p.zip", "/carts/u", top, sizeof top) >= 0);
        moy_buf_t z;
        if (moy_fs_read_file("/carts/p.zip", (size_t)-1, &z) == 0) {
            for (size_t k = 0; k < z.n && i < size; k += 1u + NEXT() % 32u) {
                z.p[k] = (char)NEXT();
            }
            moy_fs_write("/carts/q.zip", z.p, z.n);
            moy_buf_free(&z);
            moy_unpack("/carts/q.zip", "/carts/v", top, sizeof top);
        }
        moy_adopt("/carts/u", "/carts/c1.moy");
    }
#undef NEXT
    scanned = 0;
    scan_all("/carts");
    unmount_cold();
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

// -- the seed --------------------------------------------------------------------------------
//
// A cart of the roster: its JSON (src the version's text), as one stored
// deflate block, which is what moy_seed_inflate reads first.

#define SEED_DIR ROOT "/moybyte.hop.moy"

static char cart_json[8000];
static uint8_t blob[8100];

static size_t seed_blob(const char *src, size_t n, int version) {
    size_t k = (size_t)snprintf(cart_json, sizeof cart_json,
        "{\"title\": \"Hop\", \"type\": \"game\", \"version\": %d, "
        "\"cfg\": {\"speed\": 3}, \"sprites\": \"0123\\n4567\", "
        "\"scenes\": {\"b\": \"[]\", \"a\": \"[1]\"}, \"src\": \"", version);
    memcpy(cart_json + k, src, n);
    k += n;
    memcpy(cart_json + k, "\"}", 2);
    k += 2;
    blob[0] = 1;                            // BFINAL, stored
    blob[1] = (uint8_t)k;
    blob[2] = (uint8_t)(k >> 8);
    blob[3] = (uint8_t)~k;
    blob[4] = (uint8_t)(~k >> 8);
    memcpy(blob + 5, cart_json, k);
    return k + 5u;
}

// One boot's seed of the cart: inflated, then written if the folder lacks it.
static int seed_once(const char *src, size_t n, int version) {
    size_t bn = seed_blob(src, n, version);
    moy_buf_t text;
    if (moy_seed_inflate(blob, bn, &text) != 0) {
        return -1;
    }
    CHECK(text.n == bn - 5u && memcmp(text.p, cart_json, text.n) == 0);
    int rc = moy_seed_write(ROOT, "moybyte.hop.moy", text.p, text.n);
    moy_buf_free(&text);
    return rc;
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
    if (scase == 9 || scase == 10) {        // a cart with v1 committed (and v2)
        uint32_t seq;
        CHECK(moy_fs_publish(FILE_A, v1, n1) == 0);
        CHECK(moy_journal_append("/carts/a.moy", "main.py", v1, n1, -1, NULL, 0, 1, &seq) == 0);
        if (scase == 10) {
            CHECK(moy_fs_publish(FILE_A, v2, n2) == 0);
            CHECK(moy_journal_append("/carts/a.moy", "main.py", v2, n2, 1, "[1]", 3, 2,
                                     &seq) == 0);
        }
    }
    if (scase == 11) {                      // a staged copy of the cart, v2
        CHECK(moy_fs_mkdir("/carts/stage") == 0);
        CHECK(moy_fs_write("/carts/stage/main.py", v2, n2) == 0);
        CHECK(moy_fs_write(FILE_A, v1, n1) == 0);
    }
    if (scase == 8) {                       // a seeded cart a kid has played
        CHECK(seed_once(v1, n1, 1) == 1);
        CHECK(moy_fs_write(SEED_DIR "/pmem.json", "[7]", 3) == 0);
        CHECK(moy_fs_write(SEED_DIR "/config.json", "{\"kid\": 1}", 10) == 0);
    }
    unmount_cold();
}

// One sequence, run on the mounted medium. The cases:
//   0  publish v2 over v1 under a root           3  publish v2 where nothing was
//   1  publish v2 over v1 with no root           4  publish v2, then claim it
//   2  write_bytes v2 over v1                    5  publish v2 twice in a row
//   6  publish v2, then claim it into its own folder
//   7  seed the cart (v2) where there is none
//   8  seed v2 over the seeded v1, a kid's saves and config in it
static void sequence(void) {
    if (scase != 1) {
        moy_fs_root(ROOT);
    }
    switch (scase) {
        case 7: case 8:
            seed_once(v2, n2, 2);
            break;
        case 9: {                           // a commit: published, then journaled
            uint32_t seq;
            if (moy_fs_publish(FILE_A, v2, n2) == 0) {
                moy_journal_append("/carts/a.moy", "main.py", v2, n2, 0, "[[2]]", 5, 3, &seq);
            }
            break;
        }
        case 11:
            moy_adopt("/carts/stage", "/carts/a.moy");
            break;
        case 10: {
            char f[32];
            moy_journal_undo("/carts/a.moy", NULL, 0, f, sizeof f);
            break;
        }
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
    if (scase == 11) {
        // The cart is the old one or the new one whole, or the old one aside
        // where cart_index.recover puts it back.
        if (moy_fs_read_file(FILE_A, (size_t)-1, &b) == 0) {
            CHECK(same(&b, v1, n1) || same(&b, v2, n2));
            moy_buf_free(&b);
        } else {
            CHECK(moy_fs_read_file("/carts/stage.old/main.py", (size_t)-1, &b) == 0);
            CHECK(same(&b, v1, n1));
            moy_buf_free(&b);
        }
    } else if (scase == 9 || scase == 10) {
        // The journal reads whatever the cut left, a walk restores whole
        // snapshots or refuses, and the next commit lands.
        char f[32];
        int pass;
        CHECK(moy_fs_read(FILE_A, NULL, &b) == 0);
        CHECK(same(&b, v1, n1) || same(&b, v2, n2));
        moy_buf_free(&b);
        for (pass = 0; pass < 2; pass++) {
            int rc = moy_journal_undo("/carts/a.moy", NULL, 0, f, sizeof f);
            CHECK(rc == 0 || rc == 1);
            CHECK(moy_fs_read(FILE_A, NULL, &b) == 0);
            CHECK(same(&b, v1, n1) || same(&b, v2, n2));
            moy_buf_free(&b);
            rc = moy_journal_redo("/carts/a.moy", NULL, 0, f, sizeof f);
            CHECK(rc == 0 || rc == 1);
            CHECK(moy_fs_read(FILE_A, NULL, &b) == 0);
            CHECK(same(&b, v1, n1) || same(&b, v2, n2));
            moy_buf_free(&b);
        }
        uint32_t seq = 0;
        CHECK(moy_fs_publish(FILE_A, v1, n1) == 0);
        CHECK(moy_journal_append("/carts/a.moy", "main.py", v1, n1, -1, NULL, 0, 4, &seq) == 0);
        CHECK(moy_journal_compact("/carts/a.moy") >= 0);
        moy_buf_t snap;
        if (seq != 0 && moy_journal_snap("/carts/a.moy", seq, &snap) == 0) {
            CHECK(same(&snap, v1, n1));
            moy_buf_free(&snap);
        }
    } else if (scase == 7 || scase == 8) {
        // The next boot seeds again: whatever the cut left, the cart is whole.
        int rc = seed_once(v2, n2, 2);
        CHECK(rc == 0 || rc == 1);
        CHECK(moy_fs_read_file(SEED_DIR "/main.py", (size_t)-1, &b) == 0);
        CHECK(same(&b, v2, n2));
        moy_buf_free(&b);
        CHECK(moy_fs_read_file(SEED_DIR "/manifest.json", (size_t)-1, &b) == 0);
        CHECK(strstr(b.p, "\"version\": 2") != NULL);
        moy_buf_free(&b);
        CHECK(moy_fs_read_file(SEED_DIR "/config.json", (size_t)-1, &b) == 0);
        CHECK(strcmp(b.p, "{\"speed\": 3}") == 0
              || (scase == 8 && strcmp(b.p, "{\"kid\": 1}") == 0));
        moy_buf_free(&b);
        CHECK(moy_fs_read_file(SEED_DIR "/scenes/a.moyscene", (size_t)-1, &b) == 0);
        CHECK(strcmp(b.p, "[1]") == 0);
        moy_buf_free(&b);
    } else if (scase == 2) {
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
    scan_all(ROOT);
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
    int had_v1 = scase != 3 && scase != 7;
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
        for (scase = 0; scase < 12; scase++) {
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
    if (size && (data[0] & 0xc0u) == 0xc0u) {
        json_bytes(data + 1, size - 1u);
        return;
    }
    if (size && (data[0] & 0xc0u) == 0x80u) {
        run_cat(data, size);
        return;
    }
    size_t i = 0;
#define NEXT() (i < size ? data[i++] : 0u)
    medium = NEXT() & 1u;
    scase = NEXT() % 12u;
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
    int had_v1 = scase != 3 && scase != 7;
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
    uint8_t buf[160];
    static const char *const texts[] = {
        "{\"a\": [1, 2.5e3, -0, \"\\u00e9\\ud83d\\ude00\"], \"a\": {}}",
        "[true, false, null, NaN, -Infinity, 1e400, \"\\/\\b\"]",
        "{\"t\": \"x\", \"\": 0.1}", "  \"\\u0000\" ",
    };
    for (unsigned long r = 0; r < runs; r++) {
        for (size_t k = 0; k < sizeof buf; k++) {
            rng ^= rng << 13;
            rng ^= rng >> 17;
            rng ^= rng << 5;
            buf[k] = (uint8_t)rng;
        }
        size_t n = 16;
        if (r % 4u == 1u) {
            buf[0] |= 0xc0u;            // moy_json over a mutated text
            const char *t = texts[r % (sizeof texts / sizeof texts[0])];
            n = strlen(t) + 1u;
            memcpy(buf + 1, t, n - 1u);
            buf[1 + (rng % (n - 1u))] = (uint8_t)(rng >> 8);
        } else if (r % 4u == 2u) {
            buf[0] = (uint8_t)((buf[0] & 0x3fu) | 0x80u);   // a catalogue
            n = sizeof buf;
        } else {
            buf[0] &= 0x3fu;
        }
        run(buf, n);
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
