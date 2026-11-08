// The host's native-module loader (native/moy_elf/README.md): a module the
// SDK built for the host (tools/elf_cart.py --chip host) is a shared object
// the dynamic loader maps, its entry found by the ELF header, and everything
// above the load -- the verdict, the process, the rows, the session's calls --
// is the boards' C (native/moy_elf/moy_os.c, moy_elf_check.c).
//
// A signature that is present must verify (moy_ota_verify_module), as on a
// board; an unsigned module runs.
//
// A host module links its C library dynamically (memcpy and the like come from
// the host's), so its verdict lets it import symbols; the table's rows it calls
// are its own hidden stubs, bound inside it at its link. Each load maps its
// own copy (a memfd), so two processes of one module do not share its data.

#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "moy_elf.h"
#include "moy_ota.h"
#include "moy_os.h"
#include "moy_sh.h"

#if defined(__x86_64__)
#define HOST_EM MOY_ELF_EM_X86_64
#elif defined(__aarch64__)
#define HOST_EM MOY_ELF_EM_AARCH64
#else
#define HOST_EM 0
#endif

static void put(char *err, int errlen, const char *a, const char *b) {
    snprintf(err, (size_t)errlen, "%s%s%s", a, b != NULL && *b ? ": " : "", b != NULL ? b : "");
}

static void unload(void *h) {
    dlclose(h);
}

// The module at `path` checked and mapped into *m: 0, or -3 with the reason.
static int load(const char *path, moy_os_module_t *m, char *err, int errlen) {
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        put(err, errlen, "no module", path);
        return -3;
    }
    uint8_t *b = NULL;
    long n = 0;
    if (fseek(f, 0, SEEK_END) == 0 && (n = ftell(f)) > 0 && fseek(f, 0, SEEK_SET) == 0
            && (b = malloc((size_t)n)) != NULL && fread(b, 1, (size_t)n, f) != (size_t)n) {
        free(b);
        b = NULL;
    }
    fclose(f);
    if (b == NULL) {
        put(err, errlen, "the module would not read", path);
        return -3;
    }
    size_t len = (size_t)n;
    const char *bad = moy_ota_verify_module(b, (size_t)n, MOY_ELF_SIG_SCHEME, "host", &len);
    if (bad != NULL && strcmp(bad, MOY_OTA_MODULE_UNSIGNED) != 0) {
        free(b);
        put(err, errlen, "refused", bad);
        return -3;
    }
    moy_elf_info_t info;
    char why[160];
    if (moy_elf_check(b, len, HOST_EM, MOY_OS_ROWS, 1, &info, why, sizeof why) != 0) {
        free(b);
        put(err, errlen, "refused", why);
        return -3;
    }
    uint64_t entry = 0;
    memcpy(&entry, b + 24, 8);
    int fd = memfd_create("moy_elf", 0);
    char where[64];
    if (fd < 0 || write(fd, b, len) != (ssize_t)len) {
        free(b);
        if (fd >= 0) {
            close(fd);
        }
        put(err, errlen, "the module would not map", NULL);
        return -3;
    }
    free(b);
    snprintf(where, sizeof where, "/proc/self/fd/%d", fd);
    void *h = dlopen(where, RTLD_NOW | RTLD_LOCAL);
    close(fd);
    if (h == NULL) {
        put(err, errlen, "load", dlerror());
        return -3;
    }
    struct link_map *lm = NULL;
    if (dlinfo(h, RTLD_DI_LINKMAP, &lm) != 0 || lm == NULL) {
        dlclose(h);
        put(err, errlen, "load", "no base");
        return -3;
    }
    m->entry = (moy_elf_entry_fn)(uintptr_t)(lm->l_addr + entry);
    m->loaded = h;
    m->unload = unload;
    return 0;
}

int hl_elf_command(const char *path, int argc, const char **argv, const char *roots,
                   uint32_t pid, const char *id, uint32_t bg_pid, uint32_t budget_ms,
                   char *err, int errlen) {
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        put(err, errlen, "needs a shell session", NULL);
        return -3;
    }
    moy_os_module_t m;
    if (load(path, &m, err, errlen) != 0) {
        return -3;
    }
    return moy_os_command(s, &m, NULL, argc, argv, roots, pid, id, bg_pid, budget_ms, err,
                          (size_t)errlen);
}

int hl_elf_service(const char *path, uint32_t pid, const char *id, char *err, int errlen) {
    moy_os_module_t m;
    if (load(path, &m, err, errlen) != 0) {
        return -3;
    }
    return moy_os_service(&m, NULL, pid, id, err, (size_t)errlen) != NULL ? 0 : -3;
}

int hl_elf_check(const char *path, char *err, int errlen) {
    moy_os_module_t m;
    if (load(path, &m, err, errlen) != 0) {
        return -3;
    }
    dlclose(m.loaded);
    return 0;
}
