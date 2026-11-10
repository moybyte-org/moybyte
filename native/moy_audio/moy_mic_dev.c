// The `mic` device API by handle (native/moy_kernel/moy_dev.h's mic row): the
// microphone's ring for a compiled program, on its own task, over this
// module's driver (moy_mic.h), whose open and close take the lease table's
// lock and whose read takes the ring's.

#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_mic.h"

#define CHUNK 128u              // samples a read takes from the ring at a time

static int32_t mic_open(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg,
                        size_t cn) {
    (void)slot, (void)perms;
    words_t it = { cfg, cfg + cn };
    word_t w;
    if (moy_dev_word(&it, &w) != 0) {
        return MOY_DEV_EBAD;                    // the microphone takes no cfg
    }
    return moy_dev_errno(moy_mic_open(pid));
}

static void mic_close(uint32_t slot, uint32_t pid) {
    (void)slot;
    moy_mic_close(pid);
}

static int32_t mic_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    (void)slot;
    int16_t buf[CHUNK];
    size_t want = cap / 2u, got = 0;
    while (got < want) {
        uint32_t ask = want - got < CHUNK ? (uint32_t)(want - got) : CHUNK;
        int rc = moy_mic_read(pid, buf, ask);
        if (rc < 0) {
            return got != 0 ? (int32_t)(2u * got) : moy_dev_errno(rc);
        }
        memcpy(out + 2u * got, buf, 2u * (size_t)rc);     // PCM16 little-endian, as held
        got += (size_t)rc;
        if ((uint32_t)rc < ask) {
            break;
        }
    }
    return (int32_t)(2u * got);
}

static int32_t mic_get(uint32_t slot, uint32_t pid, const char *what, size_t n) {
    (void)slot, (void)pid;
    if (moy_dev_is(what, n, "rate")) {
        return (int32_t)moy_mic_rate();
    }
    if (moy_dev_is(what, n, "drops")) {
        return (int32_t)moy_mic_drops();
    }
    return MOY_DEV_EBAD;
}

const moy_dev_ops_t moy_dev_mic = {
    .open = mic_open, .close = mic_close, .read = mic_read, .get = mic_get,
};
