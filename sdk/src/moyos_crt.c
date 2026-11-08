// The module's entry (sdk/README.md): the one address the loader calls, with
// the OS table and what to run. It keeps the table for the stubs, answers
// which hooks the module defines, and calls them. A hook the module does not
// define is a hidden weak reference the link resolves to 0, so the module
// imports nothing by name.
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
extern int moy_start(void) HOOK;
extern void moy_stop(void) HOOK;
extern void moy_timer(uint32_t t_us) HOOK;
extern void moy_on(const moy_event_t *e) HOOK;
// The SDK's C library's exit path (sdk/libc/), when the module links it.
extern void __moy_exit(void) HOOK;

// What the loader asks (native/moy_elf/moy_os.h's MOY_ELF_OP_*).
enum { OP_BIND = 0, OP_MAIN, OP_START, OP_STOP, OP_TIMER, OP_ON };
// OP_BIND's answer: a bit per hook defined.
enum { H_MAIN = 1, H_START = 2, H_STOP = 4, H_TIMER = 8, H_ON = 16 };

int _moy_entry(const moy_os_table_t *os, int op, int argc, char **argv, const void *arg)
{
    // The loader's own call, which runs the module's constructors first
    // (elf_loader's esp_elf_request): nothing more to do.
    if (os == NULL) {
        return 0;
    }
    switch (op) {
    case OP_BIND:
        __moy_os = os;
        return (main ? H_MAIN : 0) | (moy_start ? H_START : 0) | (moy_stop ? H_STOP : 0)
               | (moy_timer ? H_TIMER : 0) | (moy_on ? H_ON : 0);
    case OP_MAIN: {
        int code = main ? main(argc, argv) : -1;
        if (__moy_exit) {
            __moy_exit();
        }
        return code;
    }
    case OP_START:
        return moy_start ? moy_start() : 0;
    case OP_STOP:
        if (moy_stop) {
            moy_stop();
        }
        return 0;
    case OP_TIMER:
        if (moy_timer) {
            moy_timer((uint32_t)argc);
        }
        return 0;
    case OP_ON:
        if (moy_on) {
            moy_on((const moy_event_t *)arg);
        }
        return 0;
    }
    return -1;
}
