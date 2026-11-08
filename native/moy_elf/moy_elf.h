// moy_elf: a native module's file (docs/os_2026-11/tiers.md section 1;
// README.md beside this file): what the OS checks before a byte of it is
// loaded, and its signature.
//
// A MODULE FILE is a relocatable ELF (ET_DYN) for one chip, built by the SDK
// (sdk/README.md) against the OS table (moy_os_table.h): its entry is
// sdk/src/moyos_crt.c's, the section .moy.table names the table version it was
// built against, and .moy.rows lists every row it calls. It imports no
// symbol: everything it calls in the OS is a row, by ordinal.
//
// THE VERDICT refuses, before the loader sees it, a file that is not an ELF
// for this console's chip, one not built by the SDK, one that imports a
// symbol, and one that calls a row this console's table does not have: that
// module needs a newer console ("needs a console update"). A module built
// against a newer table that calls only rows this one has runs.
//
// THE SIGNATURE is the compiled tier's (native/moy_wasm/moy_wasm_key.h's
// layout and MOY_WASM_SIG_MAGIC), over MOY_ELF_SIG_SCHEME's text naming the
// chip, the module's length and its sha256 (tools/elf_cart.py signs). A file
// that carries one must verify; one that carries none runs on the consent
// its install asked for ("full access to the console").

#ifndef MOY_ELF_H
#define MOY_ELF_H

#include <stddef.h>
#include <stdint.h>

#define MOY_ELF_SIG_SCHEME "moybyte-elf-sig 1"
#define MOY_ELF_TABLE_MAGIC "MOYT"

// The chips, by the ELF's e_machine.
#define MOY_ELF_EM_XTENSA 94
#define MOY_ELF_EM_RISCV 243
#define MOY_ELF_EM_X86_64 62
#define MOY_ELF_EM_AARCH64 183

typedef struct {
    uint32_t built;             // the table version it was built against
    uint32_t highest;           // the highest row it calls, or UINT32_MAX for none
    uint32_t rows;              // how many rows it calls
} moy_elf_info_t;

// The verdict on the module file's `n` bytes (its signature trailer cut off),
// for a console of `machine` whose table has `rows` rows: 0 with *info, or -1
// with the reason in err. `imports_ok` lets a module import symbols (the
// host's, whose modules link its C library dynamically).
int moy_elf_check(const uint8_t *file, size_t n, uint16_t machine, uint32_t rows,
                  int imports_ok, moy_elf_info_t *info, char *err, size_t errlen);

#endif // MOY_ELF_H
