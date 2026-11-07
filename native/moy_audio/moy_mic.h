// moy_mic: the `mic` device API's driver (docs/os_2026-11/hardware.md section
// 1). One process at a time holds the microphone; while it does, a producer
// pushes PCM16 mono at the board's rate (moy_mic_rate: the speaker's where
// the two share clocks, so 44,100 Hz on a P4, 22,050 on the T-Deck) into a ring of kernel
// memory the holder reads, the oldest samples dropped (and counted) when it
// falls behind.
//
// The producer is the board's: an I2S receive channel and the ES7210 ADC on the
// kernel's I2C bus (moy_bus.h) where its `[hardware]` mic row names one
// (MOY_API_MIC, MOY_HW_MIC_*; board_config.hw_defines). The T-Deck's ES7210
// has its own I2S pins (MCLK, SCK, LRCK, DIN), so its channel is I2S1 as
// master; a P4's shares the speaker's clocks and adds only DIN, so its
// channel is the speaker's I2S0 opened full duplex (moy_aud_out.c's
// moy_aud_out_rx). A task reads it and pushes the mean of its two slots
// (moy_mic.c says why).
//
// The host has no microphone: moy_mic_sim(rate) makes one, and a test or the
// simulator pushes its samples (runtime/host/mic_sim.py).

#ifndef MOY_MIC_H
#define MOY_MIC_H

#include <stdint.h>

#define MOY_MIC_RING 16384u             // samples: ~0.37 s at 44.1 kHz, ~0.74 s at 22.05
#define MOY_MIC_ES7210_ADDR 0x40
#define MOY_MIC_GAIN_DB 30

#define MOY_MIC_EBADF 9
#define MOY_MIC_ENOMEM 12
#define MOY_MIC_EBUSY 16
#define MOY_MIC_ENODEV 19
#define MOY_MIC_EIO 5

// The microphone for `owner`: 0, or -EBUSY (another holds it), -ENODEV (none
// here), -ENOMEM, -EIO (the ADC or the channel refused). Opening it again
// for its holder is 0.
int moy_mic_open(uint32_t owner);
// `owner` lets it go: 0, or -EBADF when it does not hold it.
int moy_mic_close(uint32_t owner);
// Whatever `owner` holds let go: 1 when it held the microphone, else 0.
uint32_t moy_mic_sweep(uint32_t owner);
uint32_t moy_mic_owner(void);           // the MOY_LEASE_MIC lease's holder, 0: no one
// Its lease dropped from outside (moy_lease.h's DEVICES): `owner` lets it go.
void moy_mic_lease_end(uint32_t owner);
// Up to `n` samples, oldest first: their count, or -EBADF for a reader that
// does not hold it.
int moy_mic_read(uint32_t owner, int16_t *out, uint32_t n);
uint32_t moy_mic_rate(void);            // 0 where there is no microphone
uint32_t moy_mic_drops(void);           // samples dropped since the open
uint32_t moy_mic_pending(void);
int moy_mic_present(void);
// The producer's push: dropped unless someone holds the microphone.
void moy_mic_push(const int16_t *s, uint32_t n);
// The host's stand-in microphone at `rate` (0: none).
void moy_mic_sim(uint32_t rate);
void moy_mic_reset(void);

#endif // MOY_MIC_H
