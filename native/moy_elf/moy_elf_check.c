// moy_elf: the verdict on a native module's file (moy_elf.h).

// A host build (a board is told by its heap header, as moy_os.c is).
#if !__has_include("esp_heap_caps.h")
#define _POSIX_C_SOURCE 200809L
#endif

#include <stdio.h>
#include <string.h>

#include "moy_elf.h"

#define ET_DYN_ 3
#define SHT_DYNSYM_ 11
#define SHN_UNDEF_ 0
#define STB_WEAK_ 2

static uint16_t u16(const uint8_t *p) {
    return (uint16_t)(p[0] | p[1] << 8);
}

static uint32_t u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint64_t u64(const uint8_t *p) {
    return (uint64_t)u32(p) | (uint64_t)u32(p + 4) << 32;
}

typedef struct {
    uint32_t type;
    uint64_t off;
    uint64_t size;
    uint32_t name;
    uint32_t link;
    uint64_t entsize;
} shdr_t;

static int no(char *err, size_t errlen, const char *why) {
    snprintf(err, errlen, "%s", why);
    return -1;
}

int moy_elf_check(const uint8_t *f, size_t n, uint16_t machine, uint32_t rows,
                  int imports_ok, moy_elf_info_t *info, char *err, size_t errlen) {
    memset(info, 0, sizeof *info);
    info->highest = UINT32_MAX;
    if (n < 52 || memcmp(f, "\177ELF", 4) != 0) {
        return no(err, errlen, "not an ELF module");
    }
    int wide = f[4] == 2;
    if ((f[4] != 1 && f[4] != 2) || f[5] != 1 || (wide && n < 64)) {
        return no(err, errlen, "not a little-endian ELF module");
    }
    if (u16(f + 16) != ET_DYN_) {
        return no(err, errlen, "not a module the SDK built (it is not relocatable)");
    }
    if (u16(f + 18) != machine) {
        return no(err, errlen, "built for another chip");
    }
    uint64_t shoff = wide ? u64(f + 40) : u32(f + 32);
    uint16_t shentsize = u16(f + (wide ? 58 : 46));
    uint16_t shnum = u16(f + (wide ? 60 : 48));
    uint16_t shstrndx = u16(f + (wide ? 62 : 50));
    if (shentsize < (wide ? 64 : 40) || shstrndx >= shnum || shoff > n
            || (uint64_t)shnum * shentsize > n - shoff) {
        return no(err, errlen, "a malformed ELF module");
    }
    shdr_t sh[2] = { { 0 } };
#define SH(i, out) do { \
        const uint8_t *s_ = f + shoff + (uint64_t)(i) * shentsize; \
        (out).name = u32(s_); \
        (out).type = u32(s_ + 4); \
        (out).off = wide ? u64(s_ + 24) : u32(s_ + 16); \
        (out).size = wide ? u64(s_ + 32) : u32(s_ + 20); \
        (out).link = u32(s_ + (wide ? 40 : 24)); \
        (out).entsize = wide ? u64(s_ + 56) : u32(s_ + 36); \
    } while (0)
    SH(shstrndx, sh[0]);
    shdr_t strs = sh[0];
    if (strs.off > n || strs.size > n - strs.off) {
        return no(err, errlen, "a malformed ELF module");
    }
    int have_table = 0;
    for (uint16_t i = 0; i < shnum; i++) {
        shdr_t s;
        SH(i, s);
        if (s.off > n || s.size > n - s.off || s.name >= strs.size) {
            if (s.type != 8) {           // SHT_NOBITS has no bytes in the file
                return no(err, errlen, "a malformed ELF module");
            }
            continue;
        }
        const char *name = (const char *)f + strs.off + s.name;
        size_t room = strs.size - s.name;
        if (strnlen(name, room) == room) {
            return no(err, errlen, "a malformed ELF module");
        }
        if (strcmp(name, ".moy.table") == 0) {
            if (s.size < 8 || memcmp(f + s.off, MOY_ELF_TABLE_MAGIC, 4) != 0) {
                return no(err, errlen, "a malformed .moy.table");
            }
            info->built = u32(f + s.off + 4);
            have_table = 1;
        } else if (strcmp(name, ".moy.rows") == 0) {
            if (s.size % 4) {
                return no(err, errlen, "a malformed .moy.rows");
            }
            for (uint64_t k = 0; k < s.size; k += 4) {
                uint32_t r = u32(f + s.off + k);
                if (info->highest == UINT32_MAX || r > info->highest) {
                    info->highest = r;
                }
                info->rows++;
            }
        } else if (s.type == SHT_DYNSYM_ && !imports_ok) {
            shdr_t str;
            if (s.link >= shnum) {
                return no(err, errlen, "a malformed ELF module");
            }
            SH(s.link, str);
            uint64_t es = s.entsize ? s.entsize : (wide ? 24 : 16);
            for (uint64_t k = es; k + es <= s.size; k += es) {
                const uint8_t *y = f + s.off + k;
                uint32_t nm = u32(y);
                uint16_t shndx = u16(y + (wide ? 6 : 14));
                uint8_t bind = (uint8_t)(y[wide ? 4 : 12] >> 4);
                if (shndx == SHN_UNDEF_ && nm != 0 && bind != STB_WEAK_) {
                    char why[96];
                    const char *sym = nm < str.size && str.off + str.size <= n
                                      ? (const char *)f + str.off + nm : "?";
                    snprintf(why, sizeof why, "it imports %.40s, which no console exports", sym);
                    return no(err, errlen, why);
                }
            }
        }
    }
#undef SH
    if (!have_table) {
        return no(err, errlen, "not a module the SDK built (it names no OS table)");
    }
    if (info->highest != UINT32_MAX && info->highest >= rows) {
        snprintf(err, errlen, "needs a console update: it calls row %u of the OS table, "
                 "which has %u here", (unsigned)info->highest, (unsigned)rows);
        return -1;
    }
    return 0;
}
