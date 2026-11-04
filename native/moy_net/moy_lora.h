// moy_lora: the `lora` device API's driver (docs/os_2026-11/hardware.md
// section 1; owner, 2026-11-03: the T-Deck's radio is device-API only). One
// process holds the radio at a time and gets raw LoRa packets, sent and
// received with RSSI and SNR, and LoRaWAN over the OS-side MAC
// (moy_lorawan.h): a join, uplinks, and the downlinks the receive windows
// catch, the windows timed here in C.
//
// THE RADIO is the board's `[hardware]` lora row (MOY_API_LORA,
// MOY_HW_LORA_RST/_BUSY/_IRQ): the T-Deck's SX1262 on SPI2 with CS
// MOY_HW_SPI2_RADIO_CS, the bus the panel's flush holds for most of every
// frame. Every radio transaction runs in the flush's band gaps
// (moy_flush_gate_take/_give, native/moy_flush/moy_flush.h): the feeder
// stops queuing bands while a transaction waits, lets the bands in flight
// finish, steps aside, and takes the bus back. The SX1262 runs off its TCXO
// (DIO3, 1.8 V), DIO2 switching its RF path, DC-DC, and DIO1 raising its
// interrupts, which wake the driver's task on core 0.
//
// THE STATE MACHINE is moy_lora_step(now_ms): the board's task calls it on
// each interrupt and at each window's deadline; the host's stand-in calls it
// from a test's clock. Every event posts a LORA record on the ring source
// "lora" (moy_input.h): `id` the event (MOY_LORA_EV_*), `a` a length, `b`
// the packet's RSSI (dBm, low 16 bits signed) and SNR (dB x 4, high 16 bits
// signed) where it has them.
//
// THE STAND-IN (the host): the radio is a record of what it was told, its
// transmissions an outbox the "air" reads (moy_lora_sim_sent) and its
// receiver hearing what the air delivers on the frequency and spreading factor
// it is tuned to (moy_lora_sim_air); a transmission ends at the next step.

#ifndef MOY_LORA_H
#define MOY_LORA_H

#include <stddef.h>
#include <stdint.h>

#define MOY_LORA_PKT 255
#define MOY_LORA_QUEUE 8                // packets waiting for the holder
#define MOY_LORA_WIN_EARLY_MS 20        // a window opens this much before its time
#define MOY_LORA_WIN_MS 60              // and stays open this long past it (plus 12 symbols)

#define MOY_LORA_EBADF 9
#define MOY_LORA_ENOMEM 12
#define MOY_LORA_EBUSY 16
#define MOY_LORA_ENODEV 19
#define MOY_LORA_EINVAL 22
#define MOY_LORA_EIO 5
#define MOY_LORA_ENOTCONN 107

enum {
    MOY_LORA_EV_RX = 1,                 // a raw packet heard
    MOY_LORA_EV_TX_DONE,                // a raw packet sent
    MOY_LORA_EV_JOINED,
    MOY_LORA_EV_JOIN_FAILED,            // neither window brought an accept
    MOY_LORA_EV_DOWN,                   // a downlink (queued, its port)
    MOY_LORA_EV_UP_DONE,                // an uplink's windows closed; a = 1 when acked
};

enum {
    MOY_LORA_IDLE = 0,
    MOY_LORA_LISTEN,                    // raw receive, continuous
    MOY_LORA_TX,                        // a raw packet on the air
    MOY_LORA_LW_TX,                     // an uplink or a join request on the air
    MOY_LORA_LW_WAIT1,
    MOY_LORA_LW_RX1,
    MOY_LORA_LW_WAIT2,
    MOY_LORA_LW_RX2,
};

// What a raw packet goes out and is heard with.
typedef struct {
    uint32_t freq;                      // Hz
    uint32_t bw;                        // Hz: 62500, 125000, 250000, 500000
    uint8_t sf;                         // 7..12
    uint8_t cr;                         // 5..8: 4/5 .. 4/8
    int8_t power;                       // dBm, -9..22
    uint8_t sync;                       // 0x12 private, 0x34 LoRaWAN's
    uint16_t preamble;
} moy_lora_cfg_t;

// The radio for `owner`: 0, or -EBUSY (another holds it), -ENODEV (none
// here), -ENOMEM, -EIO (it did not answer). Its raw settings start at
// 868.1 MHz, SF9, 125 kHz, 4/5, 14 dBm, private sync word.
int moy_lora_open(uint32_t owner);
int moy_lora_close(uint32_t owner);
uint32_t moy_lora_sweep(uint32_t owner);
uint32_t moy_lora_owner(void);
// The raw settings: 0, or -EINVAL for a value outside the radio's range.
int moy_lora_config(uint32_t owner, const moy_lora_cfg_t *cfg);
// Raw receive on (continuous, between transmissions) or off.
int moy_lora_listen(uint32_t owner, int on);
// A raw packet sent: 0, or -EBUSY while the radio is transmitting or in a
// LoRaWAN exchange.
int moy_lora_send(uint32_t owner, const uint8_t *data, size_t n);
// The next waiting packet: its length, 0 for none; its RSSI, SNR (x4) and
// port (0 for a raw packet). -EBADF for a reader that does not hold it.
int moy_lora_recv(uint32_t owner, uint8_t *out, size_t cap, int16_t *rssi, int8_t *snr,
                  uint8_t *port);
// LoRaWAN: an OTAA join at data rate `dr`, then uplinks; each answers at once
// and reports through its event. -ENOTCONN for an uplink before a join.
int moy_lora_join(uint32_t owner, const uint8_t dev_eui[8], const uint8_t join_eui[8],
                  const uint8_t app_key[16], uint8_t dr);
int moy_lora_uplink(uint32_t owner, uint8_t port, const uint8_t *data, size_t n, int confirmed,
                    uint8_t dr);
// Whether the session is joined, its DevAddr and uplink count.
int moy_lora_joined(uint32_t *dev_addr, uint32_t *fcnt_up);
uint8_t moy_lora_mode(void);
// The state machine at `now_ms` (the board's task; a test's clock): the
// deadline of the next step it wants, 0 for none.
uint32_t moy_lora_step(uint32_t now_ms);
uint32_t moy_lora_source(void);
int moy_lora_present(void);
// The radio's interrupt line rose (its ISR; the stand-in's air).
void moy_lora_irq(void);

// -- the stand-in --
void moy_lora_sim(int on);
// The oldest transmission on the air: its length (0: none), its frequency,
// spreading factor, bandwidth and sync word, and whether its IQ is inverted.
int moy_lora_sim_sent(uint8_t *out, size_t cap, uint32_t *freq, uint8_t *sf, uint32_t *bw,
                      uint8_t *sync, uint8_t *iq);
// A packet on the air: heard (1) when the receiver is on `freq` at `sf` now.
int moy_lora_sim_air(const uint8_t *data, size_t n, uint32_t freq, uint8_t sf, int16_t rssi,
                     int8_t snr);
void moy_lora_reset(void);

#endif // MOY_LORA_H
