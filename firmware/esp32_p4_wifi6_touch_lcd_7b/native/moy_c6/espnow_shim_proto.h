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
// payload cap is 250 bytes and the seam's is 8166, so nothing here ever
// fragments.

#ifndef MOYBYTE_ESPNOW_SHIM_PROTO_H
#define MOYBYTE_ESPNOW_SHIM_PROTO_H

#include <stdint.h>

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
// slave -> host
#define MOYC6_V_ACK           0x81  // moyc6_ack_t
#define MOYC6_V_RECV          0x82  // moyc6_recv_t + payload
#define MOYC6_V_SEND_STATUS   0x83  // moyc6_send_status_t

#define MOYC6_PROTO_VERSION   1

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

#endif // MOYBYTE_ESPNOW_SHIM_PROTO_H
