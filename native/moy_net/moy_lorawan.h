// moy_lorawan: the OS-side LoRaWAN MAC (docs/os_2026-11/hardware.md section
// 2: "LoRaWAN's receive windows are timed by the OS-side MAC in C"), the pure
// half the `lora` device API's driver (moy_lora.h) runs: frames built and
// read, keys derived, the receive windows placed. It touches no radio and no
// clock, so the host runs it against a network server of its own
// (tests/test_dev_lora.py).
//
// LoRaWAN 1.0.x, class A, OTAA, the EU868 plan: an uplink on 868.1, 868.3 or
// 868.5 MHz (and the join accept's CFList channels), RX1 on the uplink's
// frequency at its data rate less the RX1 offset, RX2 on 869.525 MHz at DR0
// until the join accept names another. A downlink's MAC commands (FOpts, port
// 0) are read past and not answered; the DevNonce is random, as 1.0.2 allows.
//
// EUIs and keys cross this API in the order a network console prints them
// (most significant byte first); the frames carry the EUIs and the DevAddr
// little-endian.

#ifndef MOY_LORAWAN_H
#define MOY_LORAWAN_H

#include <stddef.h>
#include <stdint.h>

#define MOY_LW_JOIN_REQ 23              // a join request's length
#define MOY_LW_MAX 255                  // the largest frame
#define MOY_LW_CHANNELS 8
#define MOY_LW_JOIN_DELAY1_MS 5000
#define MOY_LW_JOIN_DELAY2_MS 6000
#define MOY_LW_RX2_FREQ 869525000u

enum {
    MOY_LW_OK = 0,
    MOY_LW_BAD = -1,                    // malformed, or not for this device
    MOY_LW_MIC = -2,                    // its MIC does not verify
    MOY_LW_STATE = -3,                  // not joined (or joined, for a join)
};

typedef struct {
    uint8_t dev_eui[8], join_eui[8], app_key[16];
    uint8_t nwk_skey[16], app_skey[16];
    uint32_t dev_addr;
    uint32_t fcnt_up, fcnt_down;
    uint16_t dev_nonce;
    uint8_t joined;
    uint8_t rx1_dr_offset, rx2_dr, rx_delay_s;
    uint32_t rx2_freq;
    uint32_t chan[MOY_LW_CHANNELS];     // Hz, 0 for none
    uint8_t ack_due;                    // a confirmed downlink waits for our ACK
    uint8_t acked;                      // the last downlink acked our confirmed uplink
} moy_lw_t;

typedef struct {
    uint32_t freq;                      // Hz
    uint8_t dr;
    uint32_t delay_ms;                  // from the uplink's end
} moy_lw_win_t;

// A fresh device with its identity; its channels the EU868 three.
void moy_lw_init(moy_lw_t *s, const uint8_t dev_eui[8], const uint8_t join_eui[8],
                 const uint8_t app_key[16]);
// The join request with DevNonce `nonce` into `out` (MOY_LW_JOIN_REQ bytes).
int moy_lw_join_request(moy_lw_t *s, uint16_t nonce, uint8_t *out);
// A join accept read: decrypted, its MIC checked, the session keys derived,
// the settings and channels taken. MOY_LW_OK and joined, or an error.
int moy_lw_join_accept(moy_lw_t *s, const uint8_t *in, size_t n);
// An uplink of `n` bytes on `port` (1..223) into `out`: its length, or an
// error. It carries the ACK a confirmed downlink is owed, and counts FCnt up.
int moy_lw_uplink(moy_lw_t *s, uint8_t port, const uint8_t *data, size_t n, int confirmed,
                  uint8_t *out, size_t cap);
// A downlink read: its FRMPayload decrypted into `out` and its port (0 for
// none), its length; MOY_LW_BAD for another device's or a stale FCnt,
// MOY_LW_MIC for a bad MIC.
int moy_lw_downlink(moy_lw_t *s, const uint8_t *in, size_t n, uint8_t *port, uint8_t *out,
                    size_t cap);
// The two receive windows after an uplink (or a join request) on `freq` at `dr`.
void moy_lw_windows(const moy_lw_t *s, int join, uint32_t freq, uint8_t dr, moy_lw_win_t w[2]);
// A data rate as its spreading factor and bandwidth (EU868: DR0 SF12 ..
// DR5 SF7 at 125 kHz, DR6 SF7 at 250 kHz); 0 for one outside the plan.
uint8_t moy_lw_sf(uint8_t dr);
uint32_t moy_lw_bw(uint8_t dr);
// The uplink channel for draw `r`.
uint32_t moy_lw_channel(const moy_lw_t *s, uint32_t r);

#endif // MOY_LORAWAN_H
