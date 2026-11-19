// The ESP-NOW-over-ESP-Hosted wire protocol -- ONE body, both processors.
//
// The P4 has no radio; its ESP-NOW is the C6's, reached over ESP-Hosted's
// custom-RPC seam (esp_hosted_send_custom_data / register_custom_callback,
// present on both halves since hosted 2.8.1 -- this board builds 2.12.12,
// build.sh step 2c'). The host side is modmoy_c6.c, which implements the
// esp_now_* C API for MicroPython's stock modespnow.c; the slave side is
// slave_espnow_shim.c, added to the stock esp-hosted slave build. The slave
// build COPIES this header from here (its build script does; see
// docs/espnow_p4_2026-08.md), so a drifted copy is a build to re-run, not a
// second definition.
//
// Wire format: packed little-endian structs, both ends RISC-V. ESP-NOW's own
// payload cap is 1470 bytes (v2; 250 below shim 4) and the seam's is 8166, so
// nothing here ever fragments: a SEND_BATCH holds whole frames up to
// MOYC6_BATCH_BYTES.

#ifndef MOYBYTE_ESPNOW_SHIM_PROTO_H
#define MOYBYTE_ESPNOW_SHIM_PROTO_H

#include <stdint.h>
#include <string.h>

// EXACTLY ONE hosted msg id per direction, verbs multiplexed inside the
// envelope: the seam's handler table is CONFIG_ESP_HOSTED_MAX_CUSTOM_MSG_
// HANDLERS deep and DEFAULTS TO 3 on each processor, so a design that
// registered one id per verb would silently need a Kconfig bump on the slave
// -- and a Kconfig dependency the stock slave build does not carry is exactly
// the kind of half-set this repo keeps finding. One slot per side, forever.
#define MOYC6_BASE            0x4D4F5900u          // "MOY\0"
#define MOYC6_H2S             (MOYC6_BASE + 0x01)  // every host->slave message
#define MOYC6_S2H             (MOYC6_BASE + 0x02)  // every slave->host message

// Verbs, in the envelope's `verb` byte.
// host -> slave
#define MOYC6_V_INIT          0x01  // moyc6_hdr_t
#define MOYC6_V_DEINIT        0x02  // moyc6_hdr_t
#define MOYC6_V_SEND          0x03  // moyc6_send_t + payload
#define MOYC6_V_ADD_PEER      0x04  // moyc6_peer_t (upsert)
#define MOYC6_V_DEL_PEER      0x05  // moyc6_mac_t
#define MOYC6_V_SET_RATE      0x06  // moyc6_rate_t
#define MOYC6_V_SET_PMK       0x07  // moyc6_pmk_t
#define MOYC6_V_PING          0x08  // moyc6_hdr_t
#define MOYC6_V_VERSION       0x09  // moyc6_hdr_t; ACK's err carries MOYC6_SHIM_VERSION
#define MOYC6_V_SEND_BATCH    0x0A  // moyc6_batch_t + frames (shim 4)
#define MOYC6_V_DIAG          0x0B  // moyc6_hdr_t; answered by DIAG_REPLY (shim 4)
// slave -> host
#define MOYC6_V_ACK           0x81  // moyc6_ack_t
#define MOYC6_V_RECV          0x82  // moyc6_recv_t + payload
#define MOYC6_V_SEND_STATUS   0x83  // moyc6_send_status_t
#define MOYC6_V_BATCH_STATUS  0x84  // moyc6_batch_status_t (shim 4)
#define MOYC6_V_DIAG_REPLY    0x85  // moyc6_diag_t (shim 4)

#define MOYC6_PROTO_VERSION   1

// The SHIM's build identity, self-reported over MOYC6_V_VERSION so the C6
// updater (device/moy_c6_update.py) needs no local bookkeeping: the slave IS
// the record of what it runs. BUMP THIS whenever the slave image changes for
// any reason -- a shim edit, a hosted component bump, an sdkconfig change --
// exactly the FIRMWARE_VERSION discipline, because the published manifest's
// c6.version is read from here and a stale number is an update nobody is
// offered. 1 is reserved for the first shim, which predates the verb and
// answers nothing (the updater reads a VERSION timeout as "older than
// everything").
// MUST stay below 0x100: the version travels in the ACK's err field, and
// ESP-IDF's own error space starts at 0x100 (ESP_ERR_TIMEOUT is 0x107) --
// the host filters on that boundary.
#define MOYC6_SHIM_VERSION    4

// What a host may use from shim 4 on: SEND_BATCH, and frames (sent and
// received) up to ESP-NOW v2's 1470 bytes. A host asks VERSION first and
// keeps one SEND per frame of at most 250 bytes below it.
#define MOYC6_SHIM_BATCH      4
#define MOYC6_FRAME_V1        250   // ESP_NOW_MAX_DATA_LEN
#define MOYC6_FRAME_V2        1470  // ESP_NOW_MAX_DATA_LEN_V2

// Every message begins with this envelope; every struct below embeds it.
typedef struct __attribute__((packed)) {
    uint8_t proto;                 // MOYC6_PROTO_VERSION
    uint8_t verb;                  // MOYC6_V_*
} moyc6_hdr_t;

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t dst_valid;             // 0: esp_now_send(NULL, ...) = all peers
    uint8_t dst[6];
    uint16_t len;
} moyc6_send_t;                    // followed by len payload bytes

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t mac[6];
    uint8_t channel;
    uint8_t ifidx;                 // wifi_interface_t
    uint8_t encrypt;
    uint8_t lmk[16];               // valid only when encrypt
} moyc6_peer_t;

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t mac[6];
} moyc6_mac_t;

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t ifidx;
    uint32_t rate;                 // wifi_phy_rate_t
} moyc6_rate_t;

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t pmk[16];
} moyc6_pmk_t;

// The slave ACKs INIT/DEINIT/PING always (they are the handshakes) and every
// other verb ONLY on failure -- the host's peer mirror is authoritative for
// the happy path, and a per-packet ack on SEND would put an RPC round-trip
// inside the 30Hz lockstep budget for nothing (the REAL delivery report is
// SEND_STATUS, from the radio's own send callback).
typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t verb;                  // the MOYC6_V_* being answered
    int32_t err;                   // esp_err_t
} moyc6_ack_t;

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t src[6];
    uint8_t dst[6];
    int8_t rssi;
    uint16_t len;
} moyc6_recv_t;                    // followed by len payload bytes

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t mac[6];
    uint8_t status;                // esp_now_send_status_t
} moyc6_send_status_t;

// SEND_BATCH: several frames in one host->slave message, which costs one
// RPC round trip (the seam's request/response is the P4's per-message cost,
// not the bytes). `count` frames follow, each a moyc6_batch_frame_t and its
// payload, in send order. The slave sends each and answers only when one or
// more failed: one BATCH_STATUS for the whole batch.
#define MOYC6_BATCH_BYTES     7600  // the whole message's ceiling, under the seam's 8166
#define MOYC6_BATCH_FRAMES    32

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t count;
} moyc6_batch_t;

typedef struct __attribute__((packed)) {
    uint8_t dst_valid;             // 0: all peers
    uint8_t dst[6];
    uint16_t len;
} moyc6_batch_frame_t;             // followed by len payload bytes

typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint8_t count;                 // the batch's frames
    uint8_t failed;                // how many of them the radio refused
    int32_t err;                   // the last refusal's esp_err_t
} moyc6_batch_status_t;

// What the slave says of itself, which nothing else carries off the C6: its
// heap, and what its forwarder and its batches dropped or waited for.
typedef struct __attribute__((packed)) {
    moyc6_hdr_t hdr;
    uint32_t heap_free;            // esp_get_free_heap_size
    uint32_t heap_min;             // esp_get_minimum_free_heap_size
    uint32_t heap_largest;         // the largest 8-bit-capable block
    uint32_t rx_dropped;           // received frames the forwarder had no room for
    uint32_t ctl_dropped;          // ACKs and statuses the same
    uint32_t batch_waits;          // ticks a batch waited on ESP_ERR_ESPNOW_NO_MEM
    uint32_t batch_failed;         // frames a batch's radio refused
} moyc6_diag_t;

// Room for one more frame of `len` bytes in a batch `used` bytes long.
static inline int moyc6_batch_fits(uint32_t used, uint32_t len) {
    return used + sizeof(moyc6_batch_frame_t) + len <= MOYC6_BATCH_BYTES;
}

// One frame appended to the batch at `buf`, `used` bytes long so far (a fresh
// batch: 0); the new length, or 0 where it does not fit. The caller has
// checked moyc6_batch_fits and the frame cap.
static inline uint32_t moyc6_batch_put(uint8_t *buf, uint32_t used,
    const uint8_t *dst, const uint8_t *data, uint16_t len) {
    if (used == 0) {
        moyc6_batch_t b = { { MOYC6_PROTO_VERSION, MOYC6_V_SEND_BATCH }, 0 };
        memcpy(buf, &b, sizeof(b));
        used = sizeof(b);
    }
    if (buf[2] >= MOYC6_BATCH_FRAMES || !moyc6_batch_fits(used, len)) {
        return 0;
    }
    moyc6_batch_frame_t f = { 0 };
    f.dst_valid = dst != NULL;
    if (dst != NULL) {
        memcpy(f.dst, dst, 6);
    }
    f.len = len;
    memcpy(buf + used, &f, sizeof(f));
    memcpy(buf + used + sizeof(f), data, len);
    buf[2]++;
    return used + sizeof(f) + len;
}

// The batch's frame at `*at` (start at sizeof(moyc6_batch_t)): its header
// into `f`, its payload's offset returned and `*at` moved past it; -1 where
// the message ends before the frame does.
static inline int32_t moyc6_batch_next(const uint8_t *msg, uint32_t len,
    uint32_t *at, moyc6_batch_frame_t *f) {
    if (*at + sizeof(*f) > len) {
        return -1;
    }
    memcpy(f, msg + *at, sizeof(*f));
    uint32_t data = *at + sizeof(*f);
    if (f->len > MOYC6_FRAME_V2 || data + f->len > len) {
        return -1;
    }
    *at = data + f->len;
    return (int32_t)data;
}

#endif // MOYBYTE_ESPNOW_SHIM_PROTO_H
