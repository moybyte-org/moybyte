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

// A board says it has the link in its mpconfigboard.h (MOY_NET_LINK).
#if defined(ESP_PLATFORM) && __has_include("mpconfigboard.h")
#include "mpconfigboard.h"
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

void moy_link_ring_init(moy_link_ring_t *r, uint8_t *buf, uint32_t cap);
// 1 when the record went in, 0 when it was dropped.
int moy_link_ring_put(moy_link_ring_t *r, const uint8_t mac[6],
                      const uint8_t *data, uint32_t len);
// The oldest record's payload length, its MAC into `mac` and its payload into
// `data` (at most `cap` bytes; the rest is discarded with it), or -1 when the
// ring is empty.
int moy_link_ring_get(moy_link_ring_t *r, uint8_t mac[6], uint8_t *data,
                      uint32_t cap);

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
