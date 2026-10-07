// moy_aud_out: the board's speaker -- the I2S channel, the codec where the board
// has one, and the feeder task that pulls the mix (moy_aud.h has the contract).
//
// THE FEEDER (#41's crackle fix) is a FreeRTOS task pinned to core 1 that
// renders blocks out of moy_aud_render and writes them to I2S, blocking on the
// DMA drain -- which is what paces it to the audio clock whatever the frame
// loop is doing. It never touches the VM. A compiled cart's thread shares core
// 1, so its work per block stays small: it renders in 32-frame chunks, taking
// the lock per chunk, so a verb waits tens of microseconds, not a block.
//
// LAZY: nothing here runs, and no internal SRAM is spent (the task's stack, the
// DMA ring, the mutex), until the first session is focused. A board whose
// output cannot start has no audio: the state says FAILED and why, and nothing
// else feeds the speaker.
//
// While a compiled cart streams, the task runs a SHALLOW pipeline: 128-frame
// blocks into a ring of 4 x 128 (about 29 ms from mix to speaker) where the
// synth's own is 256-frame blocks into 6 x 512 (about 150 ms): a cart's samples
// arrive with the picture that caused them. The task swaps the channel itself,
// between blocks.
//
// The board names its output in mpconfigboard.h:
//   MOY_AUDIO_I2S_BCK, _WS, _DOUT       the I2S pins (required)
//   MOY_AUDIO_I2S_MCLK                  the master clock pin, where a codec needs one
//   MOY_AUDIO_CODEC_ES8311              the codec on the kernel's I2C bus (moy_codec_es8311.c)
//   MOY_AUDIO_PA_GPIO                   the amplifier's enable, driven high once the codec is up
//   MOY_AUDIO_RATE                      the output rate, 22050 unless named

#include <stdio.h>
#include <string.h>

#include "moy_aud.h"

#if MOY_AUD_BOARD
#include "py/mpconfig.h"        // the board's MOY_AUDIO_* (mpconfigboard.h)
#endif

#if MOY_AUD_BOARD && defined(MOY_AUDIO_I2S_BCK)

#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#if MOY_AUDIO_CODEC_ES8311
#include "moy_codec_es8311.h"
#endif

#ifndef MOY_AUDIO_RATE
#define MOY_AUDIO_RATE 22050
#endif
#ifndef MOY_AUDIO_I2S_MCLK
#define MOY_AUDIO_I2S_MCLK (-1)
#endif

#define BLOCK_FRAMES 256
#define STREAM_BLOCK 128
#define STREAM_DESC 4
#define MIX_CHUNK 32
#define DMA_DESC 6
#define DMA_FRAMES 512
#define WRITE_TIMEOUT_MS 100

static i2s_chan_handle_t s_tx;
static TaskHandle_t s_task;
static SemaphoreHandle_t s_mutex;
static volatile int s_running;
static volatile uint32_t s_ovf;         // the ring ran a whole ring behind (ISR)
static volatile int s_shallow_want;
static int s_shallow;
static int s_state = MOY_AUD_OUT_NONE;
static const char *s_why = "";

void moy_aud_lock(void) {
    if (s_mutex != NULL) {
        xSemaphoreTake(s_mutex, portMAX_DELAY);
    }
}

int moy_aud_trylock(void) {
    return s_mutex == NULL || xSemaphoreTake(s_mutex, 0) == pdTRUE;
}

void moy_aud_unlock(void) {
    if (s_mutex != NULL) {
        xSemaphoreGive(s_mutex);
    }
}

uint64_t moy_aud_now_us(void) {
    return (uint64_t)esp_timer_get_time();
}

int moy_aud_out_plays(void) {
    return s_task != NULL;
}

void moy_aud_out_shallow(int on) {
    s_shallow_want = on;
}

static bool on_ovf(i2s_chan_handle_t h, i2s_event_data_t *e, void *ctx) {
    (void)h;
    (void)e;
    (void)ctx;
    s_ovf++;
    return false;
}

// The channel on the board's pins with a ring of `desc` x `frames`, enabled:
// 0, or -1 with nothing left open.
static int chan_open(int desc, int frames) {
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    cc.dma_desc_num = desc;
    cc.dma_frame_num = frames;
    cc.auto_clear = true;       // silence, not stale DMA, on an under-run
    if (i2s_new_channel(&cc, &s_tx, NULL) != ESP_OK) {
        s_tx = NULL;
        return -1;
    }
    i2s_std_config_t sc = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG((uint32_t)MOY_AUDIO_RATE),
#if MOY_AUDIO_CODEC_ES8311
        // Philips I2S, the sample on both slots: the codec's DAC reads the left.
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_MONO),
#else
        // MSB-justified mono on the left slot: the MAX98357's mono input.
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                    I2S_SLOT_MODE_MONO),
#endif
        .gpio_cfg = {
            .mclk = (gpio_num_t)MOY_AUDIO_I2S_MCLK,
            .bclk = (gpio_num_t)MOY_AUDIO_I2S_BCK,
            .ws = (gpio_num_t)MOY_AUDIO_I2S_WS,
            .dout = (gpio_num_t)MOY_AUDIO_I2S_DOUT,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
        },
    };
#if MOY_AUDIO_CODEC_ES8311
    sc.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    sc.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
#endif
    i2s_event_callbacks_t cbs = {.on_send_q_ovf = on_ovf};
    if (i2s_channel_init_std_mode(s_tx, &sc) != ESP_OK
        || i2s_channel_register_event_callback(s_tx, &cbs, NULL) != ESP_OK
        || i2s_channel_enable(s_tx) != ESP_OK) {
        i2s_del_channel(s_tx);
        s_tx = NULL;
        return -1;
    }
    return 0;
}

static void chan_swap(void) {
    int want = s_shallow_want;
    i2s_channel_disable(s_tx);
    i2s_del_channel(s_tx);
    s_tx = NULL;
    if (want && chan_open(STREAM_DESC, STREAM_BLOCK) == 0) {
        s_shallow = 1;
        return;
    }
    s_shallow = 0;
    if (chan_open(DMA_DESC, DMA_FRAMES) != 0) {
        s_running = 0;
        s_state = MOY_AUD_OUT_FAILED;
        s_why = "the I2S channel would not re-open";
    }
}

static void feeder(void *arg) {
    (void)arg;
    int16_t block[BLOCK_FRAMES];    // on the task's stack: nothing static until a start
    uint32_t ovf_seen = s_ovf;
    while (s_running) {
        if (s_shallow_want != s_shallow) {
            chan_swap();
            if (!s_running) {
                break;
            }
            ovf_seen = s_ovf;       // a fresh ring starts empty
        }
        int frames = s_shallow ? STREAM_BLOCK : BLOCK_FRAMES;
        for (int off = 0; off < frames; off += MIX_CHUNK) {
            moy_aud_render(block + off, MIX_CHUNK);
        }
        // Blocks here while the DMA drains: that IS the pacing. NEVER abandon
        // rendered frames (2026-08-10, the celeste 1.6x): the driver may accept
        // part of a block per call, and an abandoned tail advances the mix
        // without reaching the speaker -- the seam measured it at the audible
        // tempo error while both per-side clocks read 1.000. A timeout loops.
        size_t done = 0, bytes = (size_t)frames * sizeof(int16_t);
        while (done < bytes && s_running) {
            size_t w = 0;
            i2s_channel_write(s_tx, (const char *)block + done, bytes - done, &w,
                              pdMS_TO_TICKS(WRITE_TIMEOUT_MS));
            done += w;
        }
        uint32_t ovf = s_ovf;
        moy_aud_count_out((uint32_t)(done / sizeof(int16_t)), ovf - ovf_seen);
        ovf_seen = ovf;
    }
    if (s_tx != NULL) {
        i2s_channel_disable(s_tx);
    }
    s_task = NULL;
    vTaskDelete(NULL);
}

int moy_aud_out_start(void) {
    if (s_state != MOY_AUD_OUT_NONE) {
        return s_state;
    }
    s_state = MOY_AUD_OUT_FAILED;
    moy_aud_set_rate(MOY_AUDIO_RATE);
    if (chan_open(DMA_DESC, DMA_FRAMES) != 0) {
        s_why = "the I2S channel would not open";
        return s_state;
    }
#if MOY_AUDIO_CODEC_ES8311
    // The codec takes its clocks from the channel's MCLK, so it is set up
    // with the channel running (silence) and the amplifier still off.
    const char *why = moy_es8311_init(MOY_AUDIO_RATE);
    if (why != NULL) {
        s_why = why;
        i2s_channel_disable(s_tx);
        i2s_del_channel(s_tx);
        s_tx = NULL;
        return s_state;
    }
#endif
#ifdef MOY_AUDIO_PA_GPIO
    gpio_config_t pa = {.pin_bit_mask = 1ULL << MOY_AUDIO_PA_GPIO, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&pa);
    gpio_set_level(MOY_AUDIO_PA_GPIO, 1);
#endif
    s_mutex = xSemaphoreCreateMutex();
    if (s_mutex == NULL) {
        s_why = "no memory for the lock";
        i2s_channel_disable(s_tx);
        i2s_del_channel(s_tx);
        s_tx = NULL;
        return s_state;
    }
    s_running = 1;
    if (xTaskCreatePinnedToCore(feeder, "moy_audio", 4096 + BLOCK_FRAMES * 2, NULL,
                                configMAX_PRIORITIES - 3,
                                &s_task, 1) != pdPASS) {
        s_running = 0;
        s_task = NULL;
        s_why = "no memory for the feeder task";
        i2s_channel_disable(s_tx);
        i2s_del_channel(s_tx);
        s_tx = NULL;
        return s_state;
    }
    s_state = MOY_AUD_OUT_RUNNING;
    s_why = "";
    return s_state;
}

int moy_aud_out_state(const char **why) {
    if (why != NULL) {
        *why = s_why;
    }
    return s_state;
}

int moy_aud_out_attach(void) {
#if MOY_AUDIO_CODEC_ES8311
    return moy_es8311_attach() ? MOY_AUD_BAD : MOY_AUD_OK;
#else
    return MOY_AUD_OK;
#endif
}

#else   // no output in this build: the host, the browser, a board with no pins

static int s_state = MOY_AUD_OUT_ABSENT;

void moy_aud_lock(void) {
}

int moy_aud_trylock(void) {
    return 1;
}

void moy_aud_unlock(void) {
}

#if MOY_AUD_BOARD
#include "esp_timer.h"
uint64_t moy_aud_now_us(void) {
    return (uint64_t)esp_timer_get_time();
}
#elif defined(__EMSCRIPTEN__)
uint64_t moy_aud_now_us(void) {
    return 0;
}
#else
#include <time.h>
uint64_t moy_aud_now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}
#endif

// What pulls the mix on a host or in the browser is the per-frame render,
// which plays a compiled cart's stream as it mixes it; a board without pins
// has nothing that would.
int moy_aud_out_plays(void) {
#if MOY_AUD_BOARD
    return 0;
#else
    return 1;
#endif
}

void moy_aud_out_shallow(int on) {
    (void)on;
}

int moy_aud_out_start(void) {
    return s_state;
}

int moy_aud_out_attach(void) {
    return MOY_AUD_OK;
}

int moy_aud_out_state(const char **why) {
    if (why != NULL) {
        *why = "this build has no speaker";
    }
    return s_state;
}

#endif

// -- the probe: the speaker's rate against the mix's --------------------------------

static void fixed(char *out, size_t cap, uint32_t num, uint32_t den, int places) {
    uint32_t scale = places == 3 ? 1000u : 10000u;
    uint64_t v = den ? ((uint64_t)num * scale + den / 2u) / den : 0u;
    snprintf(out, cap, places == 3 ? "%u.%03u" : "%u.%04u",
             (unsigned)(v / scale), (unsigned)(v % scale));
}

size_t moy_aud_out_probe(char *buf, size_t cap) {
    static uint32_t last_w, w0, st0_rendered, st0_written;
    static uint64_t last_t, t0;
    moy_aud_stats_t st;
    moy_aud_stats(&st);
    uint64_t now = moy_aud_now_us();
    if (st.written == last_w || now - last_t < 1000000u) {
        if (st.written == last_w) {
            last_t = now;
        }
        return 0;
    }
    if (last_t == 0u || t0 == 0u) {
        // The first sample after sound starts is the base of every window.
        last_w = w0 = st.written;
        last_t = t0 = now;
        st0_rendered = st.rendered_out;
        st0_written = st.written;
        return 0;
    }
    uint32_t rate = (uint32_t)moy_aud_rate();
    uint32_t win_us = (uint32_t)(now - last_t);
    uint32_t eff = (uint32_t)(((uint64_t)(st.written - last_w) * 1000000u) / win_us);
    uint32_t ceff = (uint32_t)(((uint64_t)(st.written - w0) * 1000000u) / (uint32_t)(now - t0));
    char ratio[16], cum[16], seam[16];
    fixed(ratio, sizeof ratio, eff, rate, 3);
    fixed(cum, sizeof cum, ceff, rate, 4);
    fixed(seam, sizeof seam, st.rendered_out - st0_rendered, st.written - st0_written, 4);
    last_w = st.written;
    last_t = now;
    uint32_t sc[4];
    int open = 0;
    char snd[48] = "";
    if (moy_aud_snd_counts(sc, &open)) {
        snprintf(snd, sizeof snd, " snd=%u/%u/%u/%u", (unsigned)sc[0],
                 (unsigned)sc[1], (unsigned)sc[2], (unsigned)sc[3]);
    }
    int n = snprintf(buf, cap,
                     "AUDIORATE eff=%u want=%u ratio=%s cum=%s seam=%s under=%u "
                     "lockw=%u lockh=%u parse=%u trig=%u lat=%u/%u%s",
                     (unsigned)eff, (unsigned)rate, ratio, cum, seam,
                     (unsigned)st.underruns, (unsigned)st.lock_wait_us_max,
                     (unsigned)st.lock_hold_us_max, (unsigned)st.bank_parse_us_max,
                     (unsigned)st.trig, (unsigned)st.trig_us_last,
                     (unsigned)st.trig_us_max, snd);
    return n > 0 ? ((size_t)n < cap ? (size_t)n : cap - 1u) : 0u;
}
