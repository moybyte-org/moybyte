// moy_elf_maps: where each loaded native module sits, for a core dump.
//
// A module is relocated into PSRAM (modmoy_elf.c), so a pc inside it means
// nothing to gdb until it knows the module's file and its load address. This
// table holds both for every module loaded now: the sha256 of the module's
// bytes (its signature trailer cut off, the bytes the board loaded) and the
// mapping of its code, `vaddr` in the file to `addr` where the CPU fetches it,
// over `size` bytes. On a P4 the mapping is the loader's one segment, every
// section at one offset; on an S3 it is the text alone, fetched through the
// instruction bus's alias of PSRAM, and the data sections sit elsewhere.
//
// The board build places the table in internal RAM the core dump always
// carries (MOY_ELF_MAPS_ATTR: COREDUMP_DRAM_ATTR, the `.dram2.coredump`
// section), so a dump written by a crash names the modules that were loaded
// when it struck. tools/coredump.py finds the table by its magic, matches each
// sha to the module's unstripped twin (tools/elf_cart.py keeps it under
// .build/sdk/symbols/) and loads its symbols at the recorded address.
//
// The layout is little-endian and fixed: `tests/test_coredump.py` compiles
// this file on the host and reads the bytes back through tools/coredump.py's
// parser. Portable C: nothing here names the IDF.

#ifndef MOY_ELF_MAPS_H
#define MOY_ELF_MAPS_H

#include <stdint.h>

#define MOY_ELF_MAPS_MAGIC "MOYMAPS1"
#define MOY_ELF_MAPS_SLOTS 4u        // moy_os.h's MOY_OS_PROCS, a module each

#ifndef MOY_ELF_MAPS_ATTR
#define MOY_ELF_MAPS_ATTR
#endif

typedef struct {
    uint32_t seq;               // the load's number, 0 for an empty slot
    uint32_t pid;               // the process it was loaded for
    uint32_t vaddr;             // the first byte mapped, as the file links it
    uint32_t addr;              // where the CPU fetches that byte
    uint32_t size;              // bytes mapped from vaddr
    uint8_t sha[32];            // sha256 of the module's bytes, trailer cut off
} moy_elf_map_t;

typedef struct {
    char magic[8];              // MOY_ELF_MAPS_MAGIC, unterminated
    uint32_t slots;             // MOY_ELF_MAPS_SLOTS
    uint32_t seq;               // the loads so far
    moy_elf_map_t map[MOY_ELF_MAPS_SLOTS];
} moy_elf_maps_t;

// Record a module loaded under `key` (its loader object). With every slot
// taken, the oldest load's is reused.
void moy_elf_maps_add(const void *key, uint32_t pid, uint32_t vaddr, uint32_t addr,
                      uint32_t size, const uint8_t sha[32]);

// Forget the module loaded under `key`; nothing for a key not recorded.
void moy_elf_maps_drop(const void *key);

const moy_elf_maps_t *moy_elf_maps(void);

#endif // MOY_ELF_MAPS_H
