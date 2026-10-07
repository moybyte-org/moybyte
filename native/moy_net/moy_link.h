// The radio link's owner (docs/kernel_survival_2026-10.md section 6.1): the one
// ESP-NOW receive callback the firmware has, feeding a receive ring the kernel
// owns, so a VM stop or a soft reset neither orphans the callback nor frees
// what it writes into.
//
// The ring is pure and builds on every tier (fuzz_net.c drives it); the radio
// half compiles where a board defines MOY_NET_LINK: on-die on the S3 consoles,
// and on the P4s through native/p4/moy_c6's esp_now_* shim over the C6.

#ifndef MOY_LINK_H
#define MOY_LINK_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// A board says it has the link in its mpconfigboard.h (MOY_NET_LINK), which
// the port's configuration header includes; nothing else of the VM's is read.
#ifdef ESP_PLATFORM
#include "py/mpconfig.h"
#endif

// The most a frame carries (ESP_NOW_MAX_DATA_LEN, v1 frames).
#define MOY_LINK_MAX 250u
// The bytes a ring record costs beyond its payload: its length and its MAC.
#define MOY_LINK_REC 7u

typedef struct {
    uint8_t *buf;
    uint32_t cap;       // bytes
    uint32_t head;      // the next record's first byte, mod cap
    uint32_t used;      // bytes held
    uint32_t rx;        // records taken in
    uint32_t drops;     // records refused for want of room or for size
    uint32_t peak;      // the most bytes ever held
} moy_link_ring_t;

static inline void moy_link_ring_init(moy_link_ring_t *r, uint8_t *buf, uint32_t cap) {
    memset(r, 0, sizeof(*r));
    r->buf = buf;
    r->cap = cap;
}

static inline void moy_link_copy_in(moy_link_ring_t *r, uint32_t at, const uint8_t *p,
                    uint32_t n) {
    at %= r->cap;
    uint32_t first = r->cap - at < n ? r->cap - at : n;
    memcpy(r->buf + at, p, first);
    memcpy(r->buf, p + first, n - first);
}

static inline void moy_link_copy_out(const moy_link_ring_t *r, uint32_t at, uint8_t *p,
                     uint32_t n) {
    at %= r->cap;
    uint32_t first = r->cap - at < n ? r->cap - at : n;
    memcpy(p, r->buf + at, first);
    memcpy(p + first, r->buf, n - first);
}

// 1 when the record went in, 0 when it was dropped (and counted).
static inline int moy_link_ring_put(moy_link_ring_t *r, const uint8_t mac[6],
                      const uint8_t *data, uint32_t len) {
    uint32_t need = len + MOY_LINK_REC;
    if (r->buf == NULL || len > MOY_LINK_MAX || need > r->cap - r->used) {
        r->drops++;
        return 0;
    }
    uint32_t tail = r->head + r->used;
    uint8_t n = (uint8_t)len;
    moy_link_copy_in(r, tail, &n, 1);
    moy_link_copy_in(r, tail + 1, mac, 6);
    moy_link_copy_in(r, tail + MOY_LINK_REC, data, len);
    r->used += need;
    r->rx++;
    if (r->used > r->peak) {
        r->peak = r->used;
    }
    return 1;
}

// The oldest record's payload length, its MAC into `mac` and its payload into
// `data` (at most `cap` bytes; the rest goes with it), or -1 when empty.
static inline int moy_link_ring_get(moy_link_ring_t *r, uint8_t mac[6], uint8_t *data,
                      uint32_t cap) {
    if (r->used == 0) {
        return -1;
    }
    uint8_t n;
    moy_link_copy_out(r, r->head, &n, 1);
    moy_link_copy_out(r, r->head + 1, mac, 6);
    moy_link_copy_out(r, r->head + MOY_LINK_REC, data, n < cap ? n : cap);
    r->head = (r->head + MOY_LINK_REC + n) % r->cap;
    r->used -= MOY_LINK_REC + n;
    return n;
}

#if defined(MOY_NET_LINK) && MOY_NET_LINK

typedef struct {
    uint32_t rx, drops, peak, cap, used;
    int up;
} moy_link_stats_t;

// Bring ESP-NOW up over the running WiFi with a ring of `rxbuf` bytes (kept
// across a stop; resized only while the link is down). Idempotent.
int moy_link_start(uint32_t rxbuf);
void moy_link_stop(void);
int moy_link_rate(int rate);
int moy_link_add_peer(const uint8_t mac[6]);
int moy_link_send(const uint8_t mac[6], const uint8_t *data, size_t len);
int moy_link_recv(uint8_t mac[6], uint8_t *data, uint32_t cap);
void moy_link_stats(moy_link_stats_t *s);

#endif

#endif // MOY_LINK_H
