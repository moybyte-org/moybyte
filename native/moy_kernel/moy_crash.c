// moy_crash: the record's seal and the boot decision. The header has the
// design; this file stays portable (tests/test_moy_kernel.py builds it on the
// host). Whatever the panic wrapper reaches is MOY_CRASH_IRAM and touches only
// its arguments: no tables, no library calls, so it runs with the flash cache
// off.

#include <string.h>

#include "moy_crash.h"

#if defined(ESP_PLATFORM)
#include "esp_attr.h"
#define MOY_CRASH_IRAM IRAM_ATTR
#else
#define MOY_CRASH_IRAM
#endif

// CRC-32 (IEEE, reflected, the zlib value), bit by bit: no table in flash.
MOY_CRASH_IRAM uint32_t moy_crash_crc32(uint32_t crc, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    crc = ~crc;
    while (len--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

#define REC_TAIL(r) ((const uint8_t *)(r) + offsetof(moy_crash_rec_t, crc) + sizeof(uint32_t))
#define REC_TAIL_LEN (sizeof(moy_crash_rec_t) - offsetof(moy_crash_rec_t, crc) - sizeof(uint32_t))

MOY_CRASH_IRAM void moy_crash_seal(moy_crash_rec_t *r) {
    r->magic = MOY_CRASH_MAGIC;
    r->version = MOY_CRASH_VERSION;
    r->size = (uint16_t)sizeof(moy_crash_rec_t);
    r->crc = moy_crash_crc32(0, REC_TAIL(r), REC_TAIL_LEN);
}

int moy_crash_valid(const moy_crash_rec_t *r) {
    return r->magic == MOY_CRASH_MAGIC
           && r->version == MOY_CRASH_VERSION
           && r->size == sizeof(moy_crash_rec_t)
           && r->kind > MOY_CRASH_NONE && r->kind < MOY_CRASH_KINDS
           && r->crc == moy_crash_crc32(0, REC_TAIL(r), REC_TAIL_LEN);
}

// Always terminated, never reads past `cap - 1` bytes of `src`.
MOY_CRASH_IRAM void moy_crash_strcpy(char *dst, size_t cap, const char *src) {
    size_t i = 0;
    if (src != NULL) {
        for (; i + 1 < cap && src[i] != '\0'; i++) {
            dst[i] = src[i];
        }
    }
    for (; i < cap; i++) {
        dst[i] = '\0';
    }
}

const char *moy_crash_kind_name(int kind) {
    static const char *const names[MOY_CRASH_KINDS] = {
        "none", "fault", "abort", "int_wdt", "task_wdt", "debug", "reset", "vm",
    };
    return (kind >= 0 && kind < MOY_CRASH_KINDS) ? names[kind] : "?";
}

const char *moy_crash_why_name(int why) {
    switch (why) {
        case MOY_WHY_VM_START: return "vm_start";
        case MOY_WHY_BOOT_LOOP: return "boot_loop";
        case MOY_WHY_HEAP: return "heap";
        default: return "none";
    }
}

const char *moy_crash_role_name(int role) {
    switch (role) {
        case MOY_ROLE_APP: return "app";
        case MOY_ROLE_WALLPAPER: return "wallpaper";
        default: return "none";
    }
}

void moy_kstate_open(moy_kstate_t *st) {
    if (st->magic == MOY_KSTATE_MAGIC
            && st->next <= MOY_BOOT_RECOVERY && st->reason <= MOY_WHY_HEAP
            && st->test <= MOY_TEST_HEAP) {
        // The OPEN ids were the last boot's VM's; the record already took
        // them, and this boot's ledger arms its own.
        st->boot++;
        memset(st->open_app, 0, sizeof(st->open_app));
        memset(st->open_wallpaper, 0, sizeof(st->open_wallpaper));
        return;
    }
    memset(st, 0, sizeof(*st));
    st->magic = MOY_KSTATE_MAGIC;
    st->boot = 1;
}

moy_boot_decision_t moy_boot_decide(moy_kstate_t *st) {
    moy_boot_decision_t d = { MOY_BOOT_START, MOY_WHY_NONE, st->test };
    uint8_t next = st->next, reason = st->reason;
    st->next = MOY_BOOT_NONE;
    st->reason = MOY_WHY_NONE;
    st->test = MOY_TEST_NONE;
    if (next == MOY_BOOT_START || next == MOY_BOOT_SAFE || next == MOY_BOOT_REPL) {
        // A choice on the recovery screen: somebody is watching, so the
        // boot-loop count starts over.
        st->unproven = 0;
        d.action = next;
    } else if (next == MOY_BOOT_RECOVERY) {
        d.action = MOY_BOOT_RECOVERY;
        d.reason = reason != MOY_WHY_NONE ? reason : MOY_WHY_VM_START;
    } else if (st->unproven >= MOY_BOOT_LOOP_STARTS) {
        d.action = MOY_BOOT_RECOVERY;
        d.reason = MOY_WHY_BOOT_LOOP;
    }
    if (d.action == MOY_BOOT_RECOVERY) {
        d.test = MOY_TEST_NONE;
    } else if (d.action != MOY_BOOT_REPL && st->unproven < 255) {
        // A REPL start has no console to prove, so it is not counted.
        st->unproven++;
    }
    return d;
}

void moy_boot_proven(moy_kstate_t *st) {
    st->unproven = 0;
}

void moy_kstate_arm(moy_kstate_t *st, int role, const char *id) {
    if (role == MOY_ROLE_APP) {
        moy_crash_strcpy(st->open_app, MOY_CRASH_ID_LEN, id);
    } else if (role == MOY_ROLE_WALLPAPER) {
        moy_crash_strcpy(st->open_wallpaper, MOY_CRASH_ID_LEN, id);
    }
}

MOY_CRASH_IRAM int moy_kstate_open_id(const moy_kstate_t *st, const char **id) {
    if (st->open_app[0] != '\0') {
        *id = st->open_app;
        return MOY_ROLE_APP;
    }
    if (st->open_wallpaper[0] != '\0') {
        *id = st->open_wallpaper;
        return MOY_ROLE_WALLPAPER;
    }
    *id = "";
    return MOY_ROLE_NONE;
}
