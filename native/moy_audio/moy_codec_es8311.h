// moy_codec_es8311: the ES8311 codec both P4 boards carry, on the kernel's I2C
// bus at 0x18 (native/moy_kernel/moy_bus.h), as an I2S slave clocked from the
// channel's MCLK at 256 x the rate, DAC on, ADC idle. A board takes it with
// MOY_AUDIO_CODEC_ES8311 in mpconfigboard.h (native/moy_audio/moy_aud_out.c).

#ifndef MOY_CODEC_ES8311_H
#define MOY_CODEC_ES8311_H

#include <stdint.h>

#define MOY_ES8311_ADDR 0x18

// The register sequence, each register read back after it: NULL when the codec
// answered and took it, else what went wrong. What the read-back found is
// moy_es8311_check's.
const char *moy_es8311_init(int rate);

// The codec's chip id (0x8311 when it answers) and how many of the sequence's
// registers read back other than written, as the last init found them.
void moy_es8311_check(uint16_t *chip_id, int *mismatches);

#endif // MOY_CODEC_ES8311_H
