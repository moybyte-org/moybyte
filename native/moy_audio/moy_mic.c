// moy_mic: the `mic` device API's driver (moy_mic.h).

#include "moy_tasks.h"

#include "moy_kmem.h"

#include "moy_mic.h"

#include <stdlib.h>
#include <string.h>

#include "moy_aud.h"
#include "moy_lease.h"

#if MOY_ON_BOARD
#include "py/mpconfig.h"        // the board's MOY_AUDIO_* (mpconfigboard.h)
#endif

#if MOY_ON_BOARD && defined(MOY_API_MIC) && defined(MOY_HW_MIC_DIN)
#define MIC_BOARD 1
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
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

// What the driver keeps, taken at the first open (a board's PSRAM): a console
// whose apps never record pays a pointer for it.
typedef struct {
    uint32_t head, count;
    uint32_t drops;
    volatile uint32_t owner;
    #ifdef MIC_BOARD
    uint8_t dev;                        // the ES7210 on the kernel's bus, 0xff before
    volatile int run;
    TaskHandle_t task;
    #ifdef MIC_OWN_CHANNEL
    i2s_chan_handle_t rx;
    #endif
    int16_t frame[256 * 2];             // one read, both slots
    int16_t mono[256];
    #endif
    int16_t ring[MOY_MIC_RING];
} mic_t;
static mic_t *M;
static uint32_t s_sim_rate;

void moy_mic_push(const int16_t *s, uint32_t n) {
    if (M == NULL || M->owner == 0) {
        return;
    }
    LOCK();
    for (uint32_t i = 0; i < n; i++) {
        uint32_t at = (M->head + M->count) % MOY_MIC_RING;
        M->ring[at] = s[i];
        if (M->count == MOY_MIC_RING) {
            M->head = (M->head + 1u) % MOY_MIC_RING;
            M->drops++;
        } else {
            M->count++;
        }
    }
    UNLOCK();
}

int moy_mic_read(uint32_t owner, int16_t *out, uint32_t n) {
    if (owner == 0 || M == NULL || owner != M->owner) {
        return -MOY_MIC_EBADF;
    }
    LOCK();
    uint32_t k = M->count < n ? M->count : n;
    for (uint32_t i = 0; i < k; i++) {
        out[i] = M->ring[(M->head + i) % MOY_MIC_RING];
    }
    M->head = (M->head + k) % MOY_MIC_RING;
    M->count -= k;
    UNLOCK();
    return (int)k;
}

// -- the board's producer -------------------------------------------------------------

#ifdef MIC_BOARD

static int reg(uint8_t r, uint8_t v) {
    uint8_t b[2] = { r, v };
    return moy_bus_write(M->dev, b, 2) == MOY_BUS_OK ? 0 : -1;
}

// The ES7210 as an I2S slave, 16-bit Philips with all four inputs in TDM at
// MOY_MIC_GAIN_DB, its clocks from MCLK = 256 x the rate. With 16 bit clocks a
// slot the two slots carry ADC1 and ADC3. Inputs 1 and 2 alone, without TDM,
// read silence on the T-Deck (2026-11-04), where LilyGo's own example runs
// all four in TDM; which input holds its microphone was not told apart on
// glass, so the reader keeps the two slots' mean.
static int es7210_init(void) {
    if (M->dev == 0xff && moy_bus_add(MOY_MIC_ES7210_ADDR, 50, &M->dev) != MOY_BUS_OK) {
        M->dev = 0xff;
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
        {0x11, 0x60}, {0x12, 0x02},             // 16-bit I2S, TDM
        {0x47, 0x08}, {0x48, 0x08}, {0x49, 0x08}, {0x4a, 0x08},   // mic power
        {0x4b, 0x00}, {0x4c, 0x00},             // MIC1/2 and MIC3/4 on
        {0x43, 0x10 | (MOY_MIC_GAIN_DB / 3)}, {0x44, 0x10 | (MOY_MIC_GAIN_DB / 3)},
        {0x45, 0x10 | (MOY_MIC_GAIN_DB / 3)}, {0x46, 0x10 | (MOY_MIC_GAIN_DB / 3)},
        {0x01, 0x00},                           // every ADC clock on
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
    if (i2s_new_channel(&cc, NULL, &M->rx) != ESP_OK) {
        M->rx = NULL;
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
    if (i2s_channel_init_std_mode(M->rx, &sc) != ESP_OK || i2s_channel_enable(M->rx) != ESP_OK) {
        i2s_del_channel(M->rx);
        M->rx = NULL;
        return -1;
    }
    return 0;
}

static int rx_read(void *buf, size_t bytes, size_t *got) {
    return i2s_channel_read(M->rx, buf, bytes, got, pdMS_TO_TICKS(50)) == ESP_OK ? 0 : -1;
}
#else
static int rx_read(void *buf, size_t bytes, size_t *got) {
    return moy_aud_out_rx_read(buf, bytes, got, 50);
}
#endif

static void reader(void *arg) {
    (void)arg;
    int16_t *frame = M->frame;
    int16_t *mono = M->mono;
    while (M->run) {
        size_t got = 0;
        if (rx_read(frame, sizeof(M->frame), &got) != 0 || got == 0) {
            vTaskDelay(1);
            continue;
        }
        uint32_t n = (uint32_t)(got / 4u);
        for (uint32_t i = 0; i < n; i++) {
            mono[i] = (int16_t)(((int32_t)frame[2 * i] + frame[2 * i + 1]) / 2);
        }
        moy_mic_push(mono, n);
    }
    M->task = NULL;
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
    M->run = 1;
    if (!moy_task_spawn(MOY_TASK_MIC, reader, NULL, &M->task)) {
        M->run = 0;
        M->task = NULL;
        return -MOY_MIC_ENOMEM;
    }
    return 0;
}

static void stop(void) {
    M->run = 0;
    for (int i = 0; M->task != NULL && i < 20; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    #ifdef MIC_OWN_CHANNEL
    if (M->rx != NULL) {
        i2s_channel_disable(M->rx);
        i2s_del_channel(M->rx);
        M->rx = NULL;
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

static int open_(uint32_t owner) {
    if (owner == 0 || !moy_mic_present()) {
        return -MOY_MIC_ENODEV;
    }
    if (M == NULL) {
        M = moy_kalloc(sizeof(*M), MOY_KMEM_PSRAM | MOY_KMEM_ZERO);
        if (M == NULL) {
            return -MOY_MIC_ENOMEM;
        }
        #ifdef MIC_BOARD
        M->dev = 0xff;
        #endif
    }
    // The holder is the lease table's (moy_lease.h, MOY_LEASE_MIC); M->owner
    // is its copy for the producer, which reads it per buffer.
    int lr = moy_lease_hold(owner, MOY_LEASE_MIC);
    if (lr != MOY_LEASE_OK) {
        return lr == MOY_LEASE_BUSY ? -MOY_MIC_EBUSY : -MOY_MIC_ENOMEM;
    }
    if (M->owner == owner) {
        return 0;
    }
    M->head = M->count = M->drops = 0;
    M->owner = owner;
    #ifdef MIC_BOARD
    int rc = start();
    if (rc != 0) {
        M->owner = 0;
        stop();
        moy_lease_let(owner, MOY_LEASE_MIC);
        return rc;
    }
    #endif
    return 0;
}

static int close_(uint32_t owner) {
    if (owner == 0 || M == NULL || owner != M->owner) {
        return -MOY_MIC_EBADF;
    }
    M->owner = 0;
    #ifdef MIC_BOARD
    stop();
    #endif
    M->head = M->count = 0;
    moy_lease_let(owner, MOY_LEASE_MIC);
    return 0;
}

// Open and close hold the lease table's lock (the lease they take or let
// inside it), so a compiled program's handle (moy_mic_dev.c, on its own task)
// and the VM's face never race them, and the lease lock is the outer one.
int moy_mic_open(uint32_t owner) {
    moy_lease_lock();
    int r = open_(owner);
    moy_lease_unlock();
    return r;
}

int moy_mic_close(uint32_t owner) {
    moy_lease_lock();
    int r = close_(owner);
    moy_lease_unlock();
    return r;
}

void moy_mic_lease_end(uint32_t owner) {
    moy_mic_close(owner);
}

uint32_t moy_mic_sweep(uint32_t owner) {
    return moy_mic_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_mic_owner(void) {
    return moy_lease_holder(MOY_LEASE_MIC);
}

uint32_t moy_mic_drops(void) {
    return M != NULL ? M->drops : 0;
}

uint32_t moy_mic_pending(void) {
    return M != NULL ? M->count : 0;
}

void moy_mic_sim(uint32_t rate) {
    s_sim_rate = rate;
}

void moy_mic_reset(void) {
    s_sim_rate = 0;
    if (M == NULL) {
        return;
    }
    if (M->owner != 0) {
        moy_mic_close(M->owner);
    }
    M->drops = 0;
}
