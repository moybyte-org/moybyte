// The device APIs of the handle table (moy_dev.h has the contract,
// moy_dev_kinds.h the split): their names and permissions, and each call
// handed to its driver module's verbs, reached through a weak symbol, so an
// image without the driver answers ABSENT for its kind. Every call runs under
// the lease table's lock (moy_dev.c), which each driver's verbs that take or
// let a lease take first too, so the lease lock is always the outer one.

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99 (moy_sync.h)
#endif

#include "moy_dev.h"

#include <string.h>

#include "moy_dev_kinds.h"
#include "moy_lease.h"
#include "moy_sync.h"

extern const moy_dev_ops_t moy_dev_lora __attribute__((weak));
extern const moy_dev_ops_t moy_dev_mic __attribute__((weak));
extern const moy_dev_ops_t moy_dev_midi __attribute__((weak));
extern const moy_dev_ops_t moy_dev_http __attribute__((weak));
extern const moy_dev_ops_t moy_dev_usb __attribute__((weak));
extern const moy_dev_ops_t moy_dev_ble __attribute__((weak));

static const struct {
    const char *name;
    uint32_t perm;
    uint8_t cls;                // the lease class its driver holds, 0: none
    bool one;                   // one handle a process
    const moy_dev_ops_t *ops;
} KINDS[] = {
    { NULL, 0, 0, false, NULL },
    { "lora", MOY_DEV_P_LORA, MOY_LEASE_LORA, true, &moy_dev_lora },
    { "mic", MOY_DEV_P_MIC, MOY_LEASE_MIC, true, &moy_dev_mic },
    { "midi", MOY_DEV_P_MIDI, 0, false, &moy_dev_midi },
    { "http", MOY_DEV_P_HTTP, 0, false, &moy_dev_http },
    { "usb", MOY_DEV_P_USB, 0, true, &moy_dev_usb },
    { "ble", MOY_DEV_P_BLE, MOY_LEASE_BLE, true, &moy_dev_ble },
};
#define NKINDS (sizeof KINDS / sizeof KINDS[0])

typedef struct {
    uint32_t pid;               // 0: free
    uint8_t kind;
} kind_t;

static kind_t *s_kind;                  // MOY_DEV_HANDLES rows, made at the first open

void *moy_dev_rows(void **t, size_t row) {
    return moy_sync_table(t, MOY_DEV_HANDLES * row);
}

int32_t moy_dev_errno(int rc) {
    if (rc >= 0) {
        return rc;
    }
    switch (-rc) {
        case 1: case 13: return MOY_DEV_EDENIED;                    // EPERM, EACCES
        case 16: return MOY_DEV_EBUSY;
        case 22: case 93: return MOY_DEV_EBAD;                      // EINVAL, EPROTONOSUPPORT
        case 2: case 9: case 19: case 107: return MOY_DEV_EABSENT;  // ENOENT, EBADF, ENODEV,
                                                                    // ENOTCONN
        case 12: return MOY_DEV_ENOMEM;
        case 24: case 28: return MOY_DEV_EFULL;                     // EMFILE, ENOSPC
        default: return MOY_DEV_EIO;
    }
}

bool moy_dev_hex(const char *v, size_t n, uint8_t *out, size_t len) {
    if (n >= 2 && v[0] == '0' && (v[1] == 'x' || v[1] == 'X')) {
        v += 2;
        n -= 2;
    }
    if (n != 2 * len) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        char c = v[i];
        int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
                : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (d < 0) {
            return false;
        }
        out[i / 2] = (uint8_t)(i % 2 ? out[i / 2] | d : d << 4);
    }
    return true;
}

int moy_dev_kind(const char *name, size_t n) {
    for (int k = 1; k < (int)NKINDS; k++) {
        if (moy_dev_is(name, n, KINDS[k].name)) {
            return k;
        }
    }
    return 0;
}

static const moy_dev_ops_t *ops_of(uint32_t slot) {
    return KINDS[s_kind[slot].kind].ops;
}

int32_t moy_dev_kind_open(uint32_t slot, int kind, uint32_t pid, uint32_t perms,
                          const char *cfg, size_t cn) {
    if (!(perms & KINDS[kind].perm)) {
        return MOY_DEV_EDENIED;
    }
    if (MOY_DEV_ROWS(s_kind) == NULL) {
        return MOY_DEV_ENOMEM;
    }
    for (uint32_t i = 0; KINDS[kind].one && i < MOY_DEV_HANDLES; i++) {
        if (s_kind[i].pid == pid && s_kind[i].kind == kind) {
            return MOY_DEV_EBUSY;               // one handle a process on the one device
        }
    }
    const moy_dev_ops_t *o = KINDS[kind].ops;
    if (o == NULL) {
        return MOY_DEV_EABSENT;
    }
    int32_t r = o->open(slot, pid, perms, cfg, cn);
    if (r < 0) {
        return r;
    }
    s_kind[slot] = (kind_t){ .pid = pid, .kind = (uint8_t)kind };
    return 0;
}

void moy_dev_kind_close(uint32_t slot) {
    if (s_kind == NULL) {
        return;
    }
    kind_t *k = &s_kind[slot];
    if (k->kind != 0) {
        ops_of(slot)->close(slot, k->pid);
    }
    *k = (kind_t){ 0 };
}

int32_t moy_dev_kind_read(uint32_t slot, uint8_t *out, size_t cap) {
    const moy_dev_ops_t *o = ops_of(slot);
    return o->read != NULL ? o->read(slot, s_kind[slot].pid, out, cap) : MOY_DEV_EBAD;
}

int32_t moy_dev_kind_write(uint32_t slot, const uint8_t *data, size_t n) {
    const moy_dev_ops_t *o = ops_of(slot);
    return o->write != NULL ? o->write(slot, s_kind[slot].pid, data, n) : MOY_DEV_EBAD;
}

int32_t moy_dev_kind_xfer(uint32_t slot, const uint8_t *data, size_t n, uint8_t *out,
                          size_t cap) {
    const moy_dev_ops_t *o = ops_of(slot);
    return o->xfer != NULL ? o->xfer(slot, s_kind[slot].pid, data, n, out, cap) : MOY_DEV_EBAD;
}

int32_t moy_dev_kind_set(uint32_t slot, const char *cfg, size_t n) {
    const moy_dev_ops_t *o = ops_of(slot);
    return o->set != NULL ? o->set(slot, s_kind[slot].pid, cfg, n) : MOY_DEV_EBAD;
}

int32_t moy_dev_kind_get(uint32_t slot, const char *what, size_t n) {
    const moy_dev_ops_t *o = ops_of(slot);
    return o->get != NULL ? o->get(slot, s_kind[slot].pid, what, n) : MOY_DEV_EBAD;
}

bool moy_dev_holds(uint32_t pid, uint8_t cls) {
    bool r = false;
    moy_lease_lock();
    for (uint32_t i = 0; s_kind != NULL && i < MOY_DEV_HANDLES && !r; i++) {
        const kind_t *k = &s_kind[i];
        const moy_dev_ops_t *o = KINDS[k->kind].ops;
        r = pid != 0 && cls != 0 && k->pid == pid && k->kind != 0
            && (KINDS[k->kind].cls == cls || (o->holds != NULL && o->holds(i) == cls));
    }
    moy_lease_unlock();
    return r;
}
