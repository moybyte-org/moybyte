// moy_elf_maps: the loaded modules' table a core dump carries (moy_elf_maps.h).

#include "moy_platform.h"

#include <string.h>

#ifdef MOY_ON_BOARD
#include "esp_attr.h"
#define MOY_ELF_MAPS_ATTR COREDUMP_DRAM_ATTR
#endif

#include "moy_elf_maps.h"

static MOY_ELF_MAPS_ATTR moy_elf_maps_t s_moy_elf_maps = {
    .magic = {'M', 'O', 'Y', 'M', 'A', 'P', 'S', '1'},
    .slots = MOY_ELF_MAPS_SLOTS,
};

// The loader object each slot was recorded under. Not part of the table: a
// host pointer is wider than the board's, and the dump needs none of it.
static const void *g_keys[MOY_ELF_MAPS_SLOTS];

void moy_elf_maps_add(const void *key, uint32_t pid, uint32_t vaddr, uint32_t addr,
                      uint32_t size, const uint8_t sha[32]) {
    unsigned at = 0;
    for (unsigned i = 0; i < MOY_ELF_MAPS_SLOTS; i++) {
        if (s_moy_elf_maps.map[i].seq == 0) {
            at = i;
            break;
        }
        if (s_moy_elf_maps.map[i].seq < s_moy_elf_maps.map[at].seq) {
            at = i;
        }
    }
    moy_elf_map_t *m = &s_moy_elf_maps.map[at];
    m->seq = 0;
    m->pid = pid;
    m->vaddr = vaddr;
    m->addr = addr;
    m->size = size;
    memcpy(m->sha, sha, sizeof m->sha);
    g_keys[at] = key;
    if (++s_moy_elf_maps.seq == 0) {
        s_moy_elf_maps.seq = 1;
    }
    m->seq = s_moy_elf_maps.seq;
}

void moy_elf_maps_drop(const void *key) {
    for (unsigned i = 0; i < MOY_ELF_MAPS_SLOTS; i++) {
        if (s_moy_elf_maps.map[i].seq != 0 && g_keys[i] == key) {
            memset(&s_moy_elf_maps.map[i], 0, sizeof s_moy_elf_maps.map[i]);
            g_keys[i] = NULL;
        }
    }
}

const moy_elf_maps_t *moy_elf_maps(void) {
    return &s_moy_elf_maps;
}
