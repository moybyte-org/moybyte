// moy_mic: the `mic` device API's driver (moy_mic.h).

#include "moy_mic.h"

#include <stdlib.h>
#include <string.h>

#include "moy_aud.h"

#if MOY_AUD_BOARD
#include "py/mpconfig.h"        // the board's MOY_AUDIO_* (mpconfigboard.h)
#endif

#if MOY_AUD_BOARD && defined(MOY_API_MIC) && defined(MOY_HW_MIC_DIN)
#define MIC_BOARD 1
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "moy_bus.h"
#ifndef MOY_AUDIO_RATE
#define MOY_AUDIO_RATE 22050
#endif
#if defined(MOY_HW_MIC_SCK)
#define MIC_OWN_CHANNEL 1       // the T-Deck's: its own pins, I2S1 as master
#endif
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
#define LOCK() portENTER_CRITICAL(&s_mux)
#define UNLOCK() portEXIT_CRITICAL(&s_mux)
#else
#define LOCK()
#define UNLOCK()
#endif

static int16_t *s_ring;                 // MOY_MIC_RING samples, kernel memory
static uint32_t s_head, s_count;
static uint32_t s_drops;
static volatile uint32_t s_owner;
static uint32_t s_sim_rate;

void moy_mic_push(const int16_t *s, uint32_t n) {
    if (s_owner == 0 || s_ring == NULL) {
        return;
    }
    LOCK();
    for (uint32_t i = 0; i < n; i++) {
        uint32_t at = (s_head + s_count) % MOY_MIC_RING;
        s_ring[at] = s[i];
        if (s_count == MOY_MIC_RING) {
            s_head = (s_head + 1u) % MOY_MIC_RING;
            s_drops++;
        } else {
            s_count++;
        }
    }
    UNLOCK();
}

int moy_mic_read(uint32_t owner, int16_t *out, uint32_t n) {
    if (owner == 0 || owner != s_owner) {
        return -MOY_MIC_EBADF;
    }
    LOCK();
    uint32_t k = s_count < n ? s_count : n;
    for (uint32_t i = 0; i < k; i++) {
        out[i] = s_ring[(s_head + i) % MOY_MIC_RING];
    }
    s_head = (s_head + k) % MOY_MIC_RING;
    s_count -= k;
    UNLOCK();
    return (int)k;
}

// -- the board's producer -------------------------------------------------------------

#ifdef MIC_BOARD
static uint8_t s_dev = 0xff;
static volatile int s_run;
static TaskHandle_t s_task;
#ifdef MIC_OWN_CHANNEL
static i2s_chan_handle_t s_rx;
#endif

static int reg(uint8_t r, uint8_t v) {
    uint8_t b[2] = { r, v };
    return moy_bus_write(s_dev, b, 2) == MOY_BUS_OK ? 0 : -1;
}

// The ES7210 as an I2S slave, 16-bit Philips, MIC1 and MIC2 on the two slots
// at MOY_MIC_GAIN_DB, its clocks from MCLK = 256 x the rate (esp_codec_dev's
// es7210_open, _config_sample and _start).
static int es7210_init(void) {
    if (s_dev == 0xff && moy_bus_add(MOY_MIC_ES7210_ADDR, 50, &s_dev) != MOY_BUS_OK) {
        s_dev = 0xff;
        return -1;
    }
    static const uint8_t seq[][2] = {
        {0x00, 0xff}, {0x00, 0x41},             // reset
        {0x01, 0x3f},                           // clocks off
        {0x09, 0x30}, {0x0a, 0x30},             // power-up timing
        {0x23, 0x2a}, {0x22, 0x0a}, {0x20, 0x0a}, {0x21, 0x2a},   // high-pass filters
        {0x08, 0x10},                           // slave
        {0x40, 0x43},                           // analog: VMID
        {0x41, 0x70}, {0x42, 0x70},             // mic bias 2.87 V
        {0x07, 0x20},                           // OSR
        {0x02, 0x81}, {0x04, 0x01}, {0x05, 0x00},   // MCLK 256 x LRCK
        {0x11, 0x60}, {0x12, 0x00},             // 16-bit I2S, no TDM
        {0x47, 0x08}, {0x48, 0x08}, {0x49, 0x08}, {0x4a, 0x08},   // mic power
        {0x4b, 0x00}, {0x4c, 0xff},             // MIC1/2 on, MIC3/4 off
        {0x43, 0x10 | (MOY_MIC_GAIN_DB / 3)}, {0x44, 0x10 | (MOY_MIC_GAIN_DB / 3)},
        {0x45, 0x00}, {0x46, 0x00},
        {0x01, 0x14},                           // ADC1/2 clocks on
        {0x06, 0x00},                           // nothing powered down
        {0x00, 0x71}, {0x00, 0x41},             // start
    };
    for (size_t i = 0; i < sizeof(seq) / sizeof(seq[0]); i++) {
        if (reg(seq[i][0], seq[i][1]) != 0) {
            return -1;
        }
    }
    return 0;
}

#ifdef MIC_OWN_CHANNEL
static int chan_open(void) {
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    cc.dma_desc_num = 4;
    cc.dma_frame_num = 256;
    if (i2s_new_channel(&cc, NULL, &s_rx) != ESP_OK) {
        s_rx = NULL;
        return -1;
    }
    i2s_std_config_t sc = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG((uint32_t)MOY_AUDIO_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = (gpio_num_t)MOY_HW_MIC_MCLK,
            .bclk = (gpio_num_t)MOY_HW_MIC_SCK,
            .ws = (gpio_num_t)MOY_HW_MIC_LRCK,
            .dout = I2S_GPIO_UNUSED,
            .din = (gpio_num_t)MOY_HW_MIC_DIN,
        },
    };
    sc.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    if (i2s_channel_init_std_mode(s_rx, &sc) != ESP_OK || i2s_channel_enable(s_rx) != ESP_OK) {
        i2s_del_channel(s_rx);
        s_rx = NULL;
        return -1;
    }
    return 0;
}

static int rx_read(void *buf, size_t bytes, size_t *got) {
    return i2s_channel_read(s_rx, buf, bytes, got, pdMS_TO_TICKS(50)) == ESP_OK ? 0 : -1;
}
#else
static int rx_read(void *buf, size_t bytes, size_t *got) {
    return moy_aud_out_rx_read(buf, bytes, got, 50);
}
#endif

static void reader(void *arg) {
    (void)arg;
    static int16_t frame[256 * 2];
    static int16_t mono[256];
    while (s_run) {
        size_t got = 0;
        if (rx_read(frame, sizeof(frame), &got) != 0 || got == 0) {
            vTaskDelay(1);
            continue;
        }
        uint32_t n = (uint32_t)(got / 4u);
        for (uint32_t i = 0; i < n; i++) {
            mono[i] = frame[2 * i];
        }
        moy_mic_push(mono, n);
    }
    s_task = NULL;
    vTaskDelete(NULL);
}

static int start(void) {
    #ifndef MIC_OWN_CHANNEL
    int st = moy_aud_out_start();
    if (st != MOY_AUD_OUT_RUNNING) {
        return -MOY_MIC_EIO;
    }
    #endif
    if (es7210_init() != 0) {
        return -MOY_MIC_EIO;
    }
    #ifdef MIC_OWN_CHANNEL
    if (chan_open() != 0) {
        return -MOY_MIC_EIO;
    }
    #else
    if (moy_aud_out_rx_enable(1) != 0) {
        return -MOY_MIC_EIO;
    }
    #endif
    s_run = 1;
    if (xTaskCreatePinnedToCore(reader, "moy_mic", 3072, NULL, 6, &s_task, 0) != pdPASS) {
        s_run = 0;
        s_task = NULL;
        return -MOY_MIC_ENOMEM;
    }
    return 0;
}

static void stop(void) {
    s_run = 0;
    for (int i = 0; s_task != NULL && i < 20; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    #ifdef MIC_OWN_CHANNEL
    if (s_rx != NULL) {
        i2s_channel_disable(s_rx);
        i2s_del_channel(s_rx);
        s_rx = NULL;
    }
    #else
    moy_aud_out_rx_enable(0);
    #endif
    reg(0x06, 0xff);                            // the ADC powered down
}
#endif

// -- the holder ---------------------------------------------------------------------

int moy_mic_present(void) {
    #ifdef MIC_BOARD
    return 1;
    #else
    return s_sim_rate != 0;
    #endif
}

uint32_t moy_mic_rate(void) {
    #ifdef MIC_BOARD
    return MOY_AUDIO_RATE;
    #else
    return s_sim_rate;
    #endif
}

int moy_mic_open(uint32_t owner) {
    if (owner == 0 || !moy_mic_present()) {
        return -MOY_MIC_ENODEV;
    }
    if (s_owner == owner) {
        return 0;
    }
    if (s_owner != 0) {
        return -MOY_MIC_EBUSY;
    }
    if (s_ring == NULL) {
        #ifdef MIC_BOARD
        s_ring = heap_caps_malloc(MOY_MIC_RING * sizeof(int16_t), MALLOC_CAP_SPIRAM);
        #else
        s_ring = malloc(MOY_MIC_RING * sizeof(int16_t));
        #endif
        if (s_ring == NULL) {
            return -MOY_MIC_ENOMEM;
        }
    }
    s_head = s_count = s_drops = 0;
    s_owner = owner;
    #ifdef MIC_BOARD
    int rc = start();
    if (rc != 0) {
        s_owner = 0;
        stop();
        return rc;
    }
    #endif
    return 0;
}

int moy_mic_close(uint32_t owner) {
    if (owner == 0 || owner != s_owner) {
        return -MOY_MIC_EBADF;
    }
    s_owner = 0;
    #ifdef MIC_BOARD
    stop();
    #endif
    s_head = s_count = 0;
    return 0;
}

uint32_t moy_mic_sweep(uint32_t owner) {
    return moy_mic_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_mic_owner(void) {
    return s_owner;
}

uint32_t moy_mic_drops(void) {
    return s_drops;
}

uint32_t moy_mic_pending(void) {
    return s_count;
}

void moy_mic_sim(uint32_t rate) {
    s_sim_rate = rate;
}

void moy_mic_reset(void) {
    if (s_owner != 0) {
        moy_mic_close(s_owner);
    }
    s_sim_rate = 0;
    s_drops = 0;
}
