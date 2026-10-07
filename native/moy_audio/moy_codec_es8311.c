// moy_codec_es8311: moy_codec_es8311.h has the contract. The sequence is the
// one Espressif's es8311 component writes for a slave codec on MCLK at 256 fs,
// 16-bit Philips I2S, DAC to the output driver (the factory firmware's and
// Waveshare's demo's).

#include "moy_codec_es8311.h"

#ifdef ESP_PLATFORM
#include "py/mpconfig.h"        // the board's MOY_AUDIO_CODEC_ES8311
#endif

#if defined(ESP_PLATFORM) && MOY_AUDIO_CODEC_ES8311

#include <stddef.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "moy_bus.h"

static uint16_t s_id;
static int s_mismatch = -1;

static int wr(uint8_t dev, uint8_t reg, uint8_t v) {
    uint8_t b[2] = {reg, v};
    return moy_bus_write(dev, b, 2);
}

static int rd(uint8_t dev, uint8_t reg, uint8_t *v) {
    return moy_bus_write_read(dev, &reg, 1, v, 1);
}

// What the sequence leaves in each register it sets, in the order it writes
// them: the clock tree for 256 fs (pre-divider 1, multiplier 1, ADC and DAC
// dividers 1, single speed, LRCK 256, the 0x10 oversampling), the serial port
// as a 16-bit I2S slave, then the power-up, DAC on, output to the driver, the
// equaliser bypassed, 0 dB, unmuted.
static const uint8_t SEQ[][2] = {
    {0x01, 0x3F}, {0x02, 0x00}, {0x05, 0x00}, {0x03, 0x10}, {0x04, 0x10},
    {0x07, 0x00}, {0x08, 0xFF}, {0x06, 0x03},
    {0x09, 0x0C}, {0x0A, 0x0C},
    {0x0D, 0x01}, {0x0E, 0x02}, {0x12, 0x00}, {0x13, 0x10}, {0x1C, 0x6A},
    {0x37, 0x08}, {0x32, 0xBF}, {0x31, 0x00},
};

int moy_es8311_attach(void) {
    uint8_t dev;
    return moy_bus_add(MOY_ES8311_ADDR, 50, &dev) == MOY_BUS_OK ? 0 : -1;
}

const char *moy_es8311_init(int rate) {
    (void)rate;                 // MCLK is 256 x whatever the channel runs at
    uint8_t dev, hi = 0, lo = 0;
    if (moy_bus_add(MOY_ES8311_ADDR, 50, &dev) != MOY_BUS_OK) {
        return "the I2C bus would not take the ES8311";
    }
    if (rd(dev, 0xFD, &hi) != MOY_BUS_OK || rd(dev, 0xFE, &lo) != MOY_BUS_OK) {
        return "the ES8311 does not answer on the bus";
    }
    s_id = (uint16_t)((hi << 8) | lo);
    if (s_id != 0x8311) {
        return "the chip at 0x18 is not an ES8311";
    }
    if (wr(dev, 0x00, 0x1F) != MOY_BUS_OK) {            // reset
        return "the ES8311 refused its reset";
    }
    vTaskDelay(pdMS_TO_TICKS(20));
    wr(dev, 0x00, 0x00);
    wr(dev, 0x00, 0x80);                                // power on, slave
    for (size_t i = 0; i < sizeof(SEQ) / sizeof(SEQ[0]); i++) {
        if (wr(dev, SEQ[i][0], SEQ[i][1]) != MOY_BUS_OK) {
            return "an ES8311 register write failed";
        }
    }
    s_mismatch = 0;
    for (size_t i = 0; i < sizeof(SEQ) / sizeof(SEQ[0]); i++) {
        uint8_t v = 0;
        if (rd(dev, SEQ[i][0], &v) != MOY_BUS_OK || v != SEQ[i][1]) {
            s_mismatch++;
        }
    }
    return NULL;
}

void moy_es8311_check(uint16_t *chip_id, int *mismatches) {
    *chip_id = s_id;
    *mismatches = s_mismatch;
}

#endif
