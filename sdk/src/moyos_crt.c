// The module's entry (sdk/README.md): the one address the loader calls, with
// the OS table and what to run. It keeps the table for the stubs, answers
// whether the module defines main, and calls it: a command's run and a
// service's loop alike. main is a hidden weak reference the link resolves to
// 0 where the module lacks it, so the module imports nothing by name.
//
// The section .moy.table, which the loader reads before anything is
// relocated, names the table version this module was built against.

#include <stddef.h>
#include <stdint.h>

#include "moyos_rt.h"

#define MOY_OS_STR_(x) #x
#define MOY_OS_STR(x) MOY_OS_STR_(x)

__asm__(".pushsection .moy.table,\"\",@progbits\n"
        ".ascii \"MOYT\"\n"
        ".4byte " MOY_OS_STR(MOY_OS_VERSION) "\n"
        ".popsection");

const moy_os_table_t *__moy_os __attribute__((visibility("hidden")));

#define HOOK __attribute__((weak, visibility("hidden")))
extern int main(int argc, char **argv) HOOK;
// The SDK's C library's exit path (sdk/libc/), when the module links it.
extern void __moy_exit(void) HOOK;

// What the loader asks (native/moy_elf/moy_os.h's MOY_ELF_OP_*).
enum { OP_BIND = 0, OP_MAIN };
// OP_BIND's answer: a bit per hook defined.
enum { H_MAIN = 1 };

int _moy_entry(const moy_os_table_t *os, int op, int argc, char **argv, const void *arg)
{
    (void)arg;
    // The loader's own call, which runs the module's constructors first
    // (elf_loader's esp_elf_request): nothing more to do.
    if (os == NULL) {
        return 0;
    }
    switch (op) {
    case OP_BIND:
        __moy_os = os;
        return main ? H_MAIN : 0;
    case OP_MAIN: {
        int code = main ? main(argc, argv) : -1;
        if (__moy_exit) {
            __moy_exit();
        }
        return code;
    }
    }
    return -1;
}
