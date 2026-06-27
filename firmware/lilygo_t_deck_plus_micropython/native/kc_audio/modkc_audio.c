// KidCode kc_audio: a focused native PCM mixer for the v0.4 console (#16, #41).
//
// WHY THIS EXISTS
// On hardware the per-frame software mixer in runtime/audio.py (AudioEngine.
// render_into) is the bottleneck: a 320x240 cart at 30 FPS leaves only a few ms
// per frame for audio, and the MicroPython inner loop -- one Python-level
// iteration per output sample, per voice -- is far too slow (the beeper runs
// ~12 FPS and crackles). This module moves ONLY that hot inner loop into C while
// leaving the data model, control surface and music scheduler in Python.
//
// WHY A CORE-1 TASK (#41 -- the crackle fix)
// Even with the C mixer, the I2S feed used to be COUPLED to the render loop:
// DeviceAudio.tick() ran once per frame on core 0 (the MicroPython VM core), and a
// render frame is ~50-80 ms (12-14 fps). During a long draw the I2S DMA ring drains
// and under-runs -> crackle. A bigger ring only helped "a bit" because the feed
// cadence is still the slow, jittery frame rate. The fix (matches PixelRoot / most
// MCU games): feed I2S from a DEDICATED native FreeRTOS task PINNED TO CORE 1,
// decoupled from rendering, so the DMA is topped up continuously no matter how slow
// core 0's frame is. The ESP32-S3 is dual-core; MicroPython's VM is single-core
// (core 0) so we cannot run Python on core 1, but a pure-C task can run there fully
// independently. I2S is on its OWN pins (BCK=7 WS=5 DOUT=6), separate from the
// shared SPI display/SD bus, so core 1 owning I2S never touches the panel/SD path.
//
// THE CORE SPLIT
//   core 0 (MP VM): DeviceAudio (Python) owns the model + control surface + music
//                   scheduler. Each frame it commits every voice's exact state into
//                   the shared kc_voices[] array (voice_commit, under a mutex) and
//                   reads back which voices are still active (active_mask) so the
//                   scheduler can advance phrases. It does NO per-sample work and NO
//                   I2S write -- the task does both.
//   core 1 (C task): owns the IDF i2s_std channel + the write loop. Each block it
//                   takes the mutex, snapshots kc_voices[] into a task-local copy,
//                   releases the mutex, mixes the block from the snapshot (the heavy
//                   per-sample loop), writes it to I2S (finite timeout), then copies
//                   the advanced snapshot state back under the mutex so the active
//                   flags / cursor stay live for core 0's next scheduler tick. The
//                   task NEVER calls into the MicroPython runtime (no MP heap, no GIL)
//                   -- it touches only plain C state guarded by the mutex.
//
// FALLBACK: if the core-1 task / I2S channel can't be created, audio_start() returns
// False and the Python DeviceAudio falls back to the legacy single-core per-frame
// feed (machine.I2S + the per-block voice_set/render/voice_read kernel below), so a
// bad result is fully revert-able from Python (KC_AUDIO_CORE1 flag) with no rebuild.
//
// SCOPE (deliberately small -- NOT TulipCC/AMY)
// The KidCode sound model is tiny: 4 voices, each a short list of [pitch, wave,
// vol] steps stepped at a fixed rate, 4 waveforms (square/tri/saw/noise), a
// per-voice noise LCG, a master volume and a fixed /CHANNELS mixdown. That whole
// per-sample model lives here in C and is a byte-for-byte port of
// AudioEngine.render_into, so the SAME .kcart sounds identical on host and device.
//
// VM-NEUTRAL HOT PATH: the mix kernel touches no Python objects per sample, the
// same discipline as native/kc_gfx; the core-1 task is doubly so (no MP at all).
//
// NEEDS ON-DEVICE VERIFICATION: built + unit-checked against the Python mixer, but
// the audible result on the MAX98357 amp is unproven in this environment.

#include <math.h>
#include <string.h>
#include "py/obj.h"
#include "py/runtime.h"

// ESP-IDF I2S + FreeRTOS are only available in the firmware build. Guard everything
// device-only behind KC_AUDIO_HAVE_IDF so the module still compiles (kernel only,
// no core-1 task) under a host unix build, exactly like kc_sd guards on ESP_IDF.
#ifdef ESP_IDF_VERSION
#include "driver/i2s_std.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
// xTaskCreatePinnedToCore (the SMP core-affinity task API) is declared in the
// ESP-IDF additions header, not the vanilla FreeRTOS task.h -- include it explicitly.
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "esp_heap_caps.h"
#define KC_AUDIO_HAVE_IDF 1
#else
#define KC_AUDIO_HAVE_IDF 0
#endif

// --- model constants: MUST match runtime/audio.py -------------------------
#define KC_CHANNELS     4       // AudioEngine CHANNELS
#define KC_MAX_STEPS    64      // cap a voice's step list (SFX are short blips)
#define KC_A4_PITCH     57      // semitone index of A4
#define KC_A4_FREQ      440.0

#define KC_WAVE_SQUARE   0
#define KC_WAVE_TRIANGLE 1
#define KC_WAVE_SAW      2
#define KC_WAVE_NOISE    3

// --- core-1 task tuning ---------------------------------------------------
// Mix/write block: small enough that the task tops the DMA up continuously, large
// enough that per-block overhead is negligible. 256 frames @ 8 kHz = 32 ms.
#define KC_BLOCK_FRAMES  256
// I2S DMA: the channel's own ring. dma_desc_num * dma_frame_num frames buffered in
// hardware. 6 * 256 = 1536 frames ~= 0.19 s @ 8 kHz -- a deep cushion the task keeps
// topped, independent of core 0's frame jitter.
#define KC_DMA_DESC_NUM  6
#define KC_DMA_FRAME_NUM 256
// The core-1 write timeout: the task blocks here while the DMA drains, which is the
// whole point (it paces the task to the audio clock). A finite (not portMAX_DELAY)
// timeout means a stuck channel can't wedge the task forever -- it loops and retries.
#define KC_WRITE_TIMEOUT_MS 100

// One voice's render state -- the C mirror of audio._Voice. `steps` is the voice's
// step list flattened to (pitch, wave, vol) triples; only the render-time fields
// live here (the bank/data model stays in Python).
typedef struct _kc_voice_t {
    int      active;            // bool: producing sound
    int      nsteps;            // number of valid triples in `steps`
    int16_t  steps[KC_MAX_STEPS][3];   // [pitch, wave, vol] per step
    double   step_dur;         // seconds per step
    int      loop;             // bool: wrap idx at the end vs. deactivate
    int      idx;              // current step index
    double   t;                // seconds into the current step
    double   phase;            // oscillator phase 0..1
    uint32_t noise;            // per-voice noise LCG state
} kc_voice_t;

// The SHARED voice mirror -- the single handoff between core 0 (writer) and core 1
// (reader). Single instance: there is one audio device. In core-1 mode it is guarded
// by s_voice_mutex (commit/snapshot/readback hold it briefly); in legacy per-block
// mode the MP thread is the only accessor so the mutex is unused. Rate + master
// volume are passed/published separately (see s_rate / s_master), so the module keeps
// no authoritative cross-block ENGINE state -- Python's AudioEngine stays the source
// of truth for triggering decisions.
static kc_voice_t kc_voices[KC_CHANNELS];

#if KC_AUDIO_HAVE_IDF
// --- core-1 task + I2S channel state (all C, never touched by the MP runtime) ---
static i2s_chan_handle_t s_tx_chan = NULL;     // IDF i2s_std TX channel (task-owned)
static TaskHandle_t      s_task = NULL;        // the core-1 feeder task
static SemaphoreHandle_t s_voice_mutex = NULL; // guards kc_voices[] across cores
static volatile int      s_running = 0;        // task should keep looping
static volatile int      s_rate = 8000;        // sample rate (set before start)
static volatile double   s_master = 1.0;       // master volume 0..1 (live, core 0 sets)
// active_mask: bit c set if voice c is producing sound, published by the task after
// each block so core 0's music scheduler can tell when a phrase slot finished. A
// plain volatile word is a safe lock-free cross-core read of an aligned 32-bit value.
static volatile uint32_t s_active_mask = 0;
#endif

// note_to_freq: equal-temperament Hz for a semitone index (A4=440). Negative
// pitch (REST) -> 0. Identical to audio.note_to_freq.
static inline double kc_note_to_freq(int pitch) {
    if (pitch < 0) {
        return 0.0;
    }
    return KC_A4_FREQ * pow(2.0, (pitch - KC_A4_PITCH) / 12.0);
}

// One waveform sample in [-1, 1] at `phase` in [0, 1). Advances *noise for the
// noise voice. Byte-for-byte the same arithmetic as audio._sample_wave.
static inline double kc_sample_wave(int wave, double phase, uint32_t *noise) {
    if (wave == KC_WAVE_SQUARE) {
        return (phase < 0.5) ? 1.0 : -1.0;
    }
    if (wave == KC_WAVE_TRIANGLE) {
        return 4.0 * fabs(phase - 0.5) - 1.0;
    }
    if (wave == KC_WAVE_SAW) {
        return 2.0 * phase - 1.0;
    }
    // noise: tiny LCG (matches the Python `& 0x7FFFFFFF` masked LCG)
    *noise = (*noise * 1103515245u + 12345u) & 0x7FFFFFFFu;
    return ((double)(*noise) / (double)0x3FFFFFFF) - 1.0;
}

// advance_step: move a voice to its next step; deactivate (or loop) at the end.
// Mirror of audio._Voice.advance_step.
static inline void kc_advance_step(kc_voice_t *v) {
    v->idx += 1;
    v->t = 0.0;
    if (v->idx >= v->nsteps) {
        if (v->loop) {
            v->idx = 0;
        } else {
            v->active = 0;
        }
    }
}

// kc_mix_block: THE heavy per-sample kernel, shared by the per-block render() entry
// (legacy core-0 path) and the core-1 task. Mixes `nframes` of signed-16-bit
// little-endian mono PCM into `out`, advancing the given `voices` array's phase +
// step cursors. Byte-for-byte the per-sample body of AudioEngine.render_into (minus
// the music scheduler, which Python runs between blocks). Pure C, no Python objects.
static void kc_mix_block(kc_voice_t *voices, uint8_t *out, int nframes,
                         int rate, double master) {
    if (rate <= 0) {
        rate = 8000;
    }
    if (master < 0.0) {
        master = 0.0;
    } else if (master > 1.0) {
        master = 1.0;
    }
    const double inv_rate = 1.0 / (double)rate;

    for (int i = 0; i < nframes; i++) {
        double acc = 0.0;
        for (int c = 0; c < KC_CHANNELS; c++) {
            kc_voice_t *v = &voices[c];
            if (!v->active || v->nsteps <= 0) {
                continue;
            }
            int16_t *step = v->steps[v->idx];
            int pitch = step[0];
            int wave  = step[1];
            int vol   = step[2];
            if (pitch >= 0 && vol > 0) {
                double freq = kc_note_to_freq(pitch);
                double s = kc_sample_wave(wave, v->phase, &v->noise);
                acc += s * ((double)vol / 7.0);
                v->phase += freq * inv_rate;
                if (v->phase >= 1.0) {
                    // Python: v.phase -= int(v.phase)  (drop the integer part)
                    v->phase -= (double)((int)v->phase);
                }
            }
            // advance time within the step
            v->t += inv_rate;
            if (v->t >= v->step_dur) {
                kc_advance_step(v);
            }
        }
        // mixdown: /CHANNELS to avoid clipping, then master volume; clamp; pack LE.
        acc = acc * master / (double)KC_CHANNELS;
        if (acc > 1.0) {
            acc = 1.0;
        } else if (acc < -1.0) {
            acc = -1.0;
        }
        int val = (int)(acc * 32767.0);
        out[2 * i]     = (uint8_t)(val & 0xFF);
        out[2 * i + 1] = (uint8_t)((val >> 8) & 0xFF);
    }
}

// --- Python-facing API ----------------------------------------------------

// voice_set(chan, active, steps, step_dur, loop, idx, t, phase, noise) -- push the
// EXACT state of a Python _Voice into the C mirror. Unlike a "play" trigger this
// sets every field verbatim (no idx/t/phase reset), so C is a pure function of the
// pushed state and reproduces render_into bit-for-bit. `steps` is any iterable of
// 3-element [pitch, wave, vol] sequences (>KC_MAX_STEPS dropped).
//
// THREADING: in legacy per-block mode (core 0 only) this is the lone writer. In
// core-1 mode DeviceAudio calls voice_set for all voices BETWEEN voice_lock() and
// voice_unlock(), so the whole commit is atomic versus the task's snapshot -- a
// half-written kc_voices[] is never visible to core 1.
static mp_obj_t kc_audio_voice_set(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_int_t c = mp_obj_get_int(a[0]);
    if (c < 0 || c >= KC_CHANNELS) {
        return mp_const_none;
    }
    kc_voice_t *v = &kc_voices[c];

    v->active = mp_obj_is_true(a[1]) ? 1 : 0;

    // Copy the step triples out of the Python sequence into the C voice.
    size_t nsteps = 0;
    mp_obj_t iterable = mp_getiter(a[2], NULL);
    mp_obj_t step;
    while ((step = mp_iternext(iterable)) != MP_OBJ_STOP_ITERATION) {
        if (nsteps >= KC_MAX_STEPS) {
            break;
        }
        mp_obj_t *fields;
        size_t nfields;
        mp_obj_get_array(step, &nfields, &fields);
        int pitch = (nfields > 0) ? (int)mp_obj_get_int(fields[0]) : -1;
        int wave  = (nfields > 1) ? (int)mp_obj_get_int(fields[1]) : KC_WAVE_SQUARE;
        int vol   = (nfields > 2) ? (int)mp_obj_get_int(fields[2]) : 6;
        v->steps[nsteps][0] = (int16_t)pitch;
        v->steps[nsteps][1] = (int16_t)wave;
        v->steps[nsteps][2] = (int16_t)vol;
        nsteps++;
    }
    v->nsteps   = (int)nsteps;
    v->step_dur = mp_obj_get_float(a[3]);
    v->loop     = mp_obj_is_true(a[4]) ? 1 : 0;
    v->idx      = (int)mp_obj_get_int(a[5]);
    v->t        = mp_obj_get_float(a[6]);
    v->phase    = mp_obj_get_float(a[7]);
    v->noise    = (uint32_t)mp_obj_get_int_truncated(a[8]);
    if (v->idx < 0) {
        v->idx = 0;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_audio_voice_set_obj, 9, 9, kc_audio_voice_set);

// voice_read(chan) -> (active, idx, t, phase, noise). After a render block C owns
// the advanced render state; the Python AudioEngine reads it back into its _Voice
// so it stays the single source of truth for the NEXT block's triggering decisions
// (is_active, the music scheduler's free-channel pick). Returns None for a bad chan.
// (Legacy per-block path only -- the core-1 task advances voices itself and publishes
// activity via active_mask().)
static mp_obj_t kc_audio_voice_read(mp_obj_t chan_obj) {
    mp_int_t c = mp_obj_get_int(chan_obj);
    if (c < 0 || c >= KC_CHANNELS) {
        return mp_const_none;
    }
    kc_voice_t *v = &kc_voices[c];
    mp_obj_t tup[5] = {
        mp_obj_new_bool(v->active),
        MP_OBJ_NEW_SMALL_INT(v->idx),
        mp_obj_new_float(v->t),
        mp_obj_new_float(v->phase),
        mp_obj_new_int_from_uint(v->noise),
    };
    return mp_obj_new_tuple(5, tup);
}
static MP_DEFINE_CONST_FUN_OBJ_1(kc_audio_voice_read_obj, kc_audio_voice_read);

// render(out, nframes, rate, master) -> nframes_written. Legacy per-block entry: mix
// `nframes` into `out` from the shared kc_voices[] using the shared kernel. This is
// the core-0 fallback feed (DeviceAudio.tick when the core-1 task is off/unavailable).
static mp_obj_t kc_audio_render(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_int_t nframes = mp_obj_get_int(a[1]);
    if (nframes <= 0) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(a[0], &bi, MP_BUFFER_WRITE);
    // Clamp to the caller's buffer (2 bytes/frame) so a short buffer can't overrun.
    if ((size_t)nframes * 2u > bi.len) {
        nframes = (mp_int_t)(bi.len / 2u);
    }
    if (nframes <= 0) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    mp_int_t rate = mp_obj_get_int(a[2]);
    double master = mp_obj_get_float(a[3]);
    kc_mix_block(kc_voices, (uint8_t *)bi.buf, (int)nframes, (int)rate, master);
    return MP_OBJ_NEW_SMALL_INT(nframes);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_audio_render_obj, 4, 4, kc_audio_render);

#if KC_AUDIO_HAVE_IDF
// --- the core-1 feeder task ----------------------------------------------
// Pure C. Owns s_tx_chan. Snapshots kc_voices[] under the mutex, mixes a block,
// writes it to I2S (this blocks while the DMA drains -- that is what paces us to the
// audio clock), then copies the advanced cursor state back under the mutex and
// publishes the active mask. Never touches the MP heap / GIL.
static void kc_audio_task(void *arg) {
    (void)arg;
    static kc_voice_t snap[KC_CHANNELS];   // task-local snapshot (in task stack-adjacent BSS)
    static uint8_t block[KC_BLOCK_FRAMES * 2];

    while (s_running) {
        // 1. snapshot the shared voices under the mutex (fast memcpy, then release).
        if (s_voice_mutex != NULL) {
            xSemaphoreTake(s_voice_mutex, portMAX_DELAY);
        }
        memcpy(snap, kc_voices, sizeof(snap));
        double master = s_master;
        int rate = s_rate;
        if (s_voice_mutex != NULL) {
            xSemaphoreGive(s_voice_mutex);
        }

        // 2. mix the block from the snapshot (heavy per-sample loop, mutex-free).
        kc_mix_block(snap, block, KC_BLOCK_FRAMES, rate, master);

        // 3. write to I2S. Blocks here while the DMA ring drains -- this is the
        //    audio-clock pacing, and it runs on core 1 so it never stalls the VM.
        size_t written = 0;
        i2s_channel_write(s_tx_chan, block, sizeof(block), &written,
                          pdMS_TO_TICKS(KC_WRITE_TIMEOUT_MS));

        // 4. publish advanced cursor state back to the shared voices so core 0's
        //    music scheduler sees activity changes, and update the active mask.
        //    Copy back ONLY the advancing cursor fields (idx/t/phase/noise/active),
        //    NOT the step list -- core 0 owns triggering (voice_commit overwrites
        //    steps), and a concurrent retrigger must win, not be clobbered here.
        uint32_t mask = 0;
        if (s_voice_mutex != NULL) {
            xSemaphoreTake(s_voice_mutex, portMAX_DELAY);
        }
        for (int c = 0; c < KC_CHANNELS; c++) {
            // Only fold back voices that core 0 didn't re-trigger underneath us. We
            // detect a retrigger by the step pointer identity is not available, so be
            // conservative: only fold back when the shared voice still matches the
            // snapshot's step list length + first step (a cheap "unchanged" proxy).
            kc_voice_t *shared = &kc_voices[c];
            kc_voice_t *s = &snap[c];
            int unchanged = (shared->nsteps == s->nsteps);
            if (unchanged && s->nsteps > 0) {
                unchanged = (shared->steps[0][0] == s->steps[0][0] &&
                             shared->steps[0][1] == s->steps[0][1] &&
                             shared->steps[0][2] == s->steps[0][2] &&
                             shared->step_dur == s->step_dur);
            }
            if (unchanged) {
                shared->active = s->active;
                shared->idx    = s->idx;
                shared->t      = s->t;
                shared->phase  = s->phase;
                shared->noise  = s->noise;
            }
            if (kc_voices[c].active && kc_voices[c].nsteps > 0) {
                mask |= (1u << c);
            }
        }
        if (s_voice_mutex != NULL) {
            xSemaphoreGive(s_voice_mutex);
        }
        s_active_mask = mask;
    }

    // Drain + stop the channel before the task exits (audio_stop path).
    if (s_tx_chan != NULL) {
        i2s_channel_disable(s_tx_chan);
    }
    s_task = NULL;
    vTaskDelete(NULL);
}
#endif  // KC_AUDIO_HAVE_IDF

// audio_start(bck, ws, dout, rate) -> True if the core-1 I2S feeder task is running.
// Creates the IDF i2s_std TX channel + the mutex + the task pinned to core 1. On ANY
// failure it tears everything back down and returns False, so DeviceAudio falls back
// to the legacy machine.I2S per-frame feed. Idempotent: a second call while running
// just returns True.
static mp_obj_t kc_audio_audio_start(size_t n_args, const mp_obj_t *a) {
#if KC_AUDIO_HAVE_IDF
    int bck  = (n_args > 0) ? mp_obj_get_int(a[0]) : 7;
    int ws   = (n_args > 1) ? mp_obj_get_int(a[1]) : 5;
    int dout = (n_args > 2) ? mp_obj_get_int(a[2]) : 6;
    int rate = (n_args > 3) ? mp_obj_get_int(a[3]) : 8000;
    if (rate <= 0) {
        rate = 8000;
    }

    if (s_task != NULL) {
        return mp_const_true;  // already running
    }
    s_rate = rate;

    // mutex guarding the kc_voices[] handoff across cores.
    if (s_voice_mutex == NULL) {
        s_voice_mutex = xSemaphoreCreateMutex();
        if (s_voice_mutex == NULL) {
            return mp_const_false;
        }
    }

    // Create the IDF i2s_std TX channel on I2S_NUM_0. Its own DMA ring (KC_DMA_*)
    // is the deep cushion the task keeps topped, independent of core 0's frames.
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = KC_DMA_DESC_NUM;
    chan_cfg.dma_frame_num = KC_DMA_FRAME_NUM;
    chan_cfg.auto_clear = true;   // emit silence (not stale DMA) on under-run
    if (i2s_new_channel(&chan_cfg, &s_tx_chan, NULL) != ESP_OK) {
        s_tx_chan = NULL;
        return mp_const_false;
    }

    // Standard Philips I2S, 16-bit mono, on the T-Deck amp pins. MONO puts the
    // sample on the left slot, which is the MAX98357's mono input.
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG((uint32_t)rate),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                    I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = (gpio_num_t)bck,
            .ws   = (gpio_num_t)ws,
            .dout = (gpio_num_t)dout,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    if (i2s_channel_init_std_mode(s_tx_chan, &std_cfg) != ESP_OK) {
        i2s_del_channel(s_tx_chan);
        s_tx_chan = NULL;
        return mp_const_false;
    }
    if (i2s_channel_enable(s_tx_chan) != ESP_OK) {
        i2s_del_channel(s_tx_chan);
        s_tx_chan = NULL;
        return mp_const_false;
    }

    // Launch the feeder PINNED TO CORE 1 (APP_CPU). Core 0 runs the MP VM.
    s_running = 1;
    s_active_mask = 0;
    BaseType_t ok = xTaskCreatePinnedToCore(
        kc_audio_task, "kc_audio", 4096, NULL,
        configMAX_PRIORITIES - 3,   // above idle, below the IDF I2S/system tasks
        &s_task, 1 /* core 1 */);
    if (ok != pdPASS) {
        s_running = 0;
        s_task = NULL;
        i2s_channel_disable(s_tx_chan);
        i2s_del_channel(s_tx_chan);
        s_tx_chan = NULL;
        return mp_const_false;
    }
    return mp_const_true;
#else
    (void)n_args; (void)a;
    return mp_const_false;   // no IDF -> no core-1 task; Python uses the fallback feed
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_audio_audio_start_obj, 0, 4, kc_audio_audio_start);

// audio_stop() -> None. Signal the core-1 task to exit and tear the I2S channel down.
// Lets DeviceAudio revert to the fallback feed cleanly (also handy for a clean
// shutdown). Safe to call when not running.
static mp_obj_t kc_audio_audio_stop(void) {
#if KC_AUDIO_HAVE_IDF
    if (s_task == NULL) {
        // task already gone; just make sure the channel is released
        if (s_tx_chan != NULL) {
            i2s_del_channel(s_tx_chan);
            s_tx_chan = NULL;
        }
        return mp_const_none;
    }
    s_running = 0;
    // Wait for the task to observe s_running==0 and self-delete (it clears s_task).
    // Bounded spin: the task loops every block (~32 ms) plus a write timeout, so a
    // few hundred ms is ample; never block forever.
    for (int i = 0; i < 40 && s_task != NULL; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (s_tx_chan != NULL) {
        i2s_del_channel(s_tx_chan);   // task already disabled it
        s_tx_chan = NULL;
    }
    s_active_mask = 0;
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(kc_audio_audio_stop_obj, kc_audio_audio_stop);

// voice_lock() / voice_unlock() -- DeviceAudio brackets its per-frame voice_commit
// (all voice_set calls) between these in core-1 mode so the whole commit is atomic
// versus the task's snapshot. No-ops when the mutex isn't created (legacy mode).
static mp_obj_t kc_audio_voice_lock(void) {
#if KC_AUDIO_HAVE_IDF
    if (s_voice_mutex != NULL) {
        xSemaphoreTake(s_voice_mutex, portMAX_DELAY);
    }
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(kc_audio_voice_lock_obj, kc_audio_voice_lock);

static mp_obj_t kc_audio_voice_unlock(void) {
#if KC_AUDIO_HAVE_IDF
    if (s_voice_mutex != NULL) {
        xSemaphoreGive(s_voice_mutex);
    }
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(kc_audio_voice_unlock_obj, kc_audio_voice_unlock);

// set_master(vol) -- core 0 publishes the live master volume the task reads each
// block. Plain double store of an aligned word; the mutex isn't needed for it.
static mp_obj_t kc_audio_set_master(mp_obj_t vol_obj) {
#if KC_AUDIO_HAVE_IDF
    double v = mp_obj_get_float(vol_obj);
    if (v < 0.0) { v = 0.0; } else if (v > 1.0) { v = 1.0; }
    s_master = v;
#else
    (void)vol_obj;
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(kc_audio_set_master_obj, kc_audio_set_master);

// active_mask() -> int. Bit c set if voice c is currently producing sound, as the
// core-1 task last published it. Core 0's music scheduler reads this (instead of
// voice_read) to know when a phrase slot finished so it can advance. Returns 0 when
// the core-1 task isn't running.
static mp_obj_t kc_audio_active_mask(void) {
#if KC_AUDIO_HAVE_IDF
    return mp_obj_new_int_from_uint(s_active_mask);
#else
    return MP_OBJ_NEW_SMALL_INT(0);
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(kc_audio_active_mask_obj, kc_audio_active_mask);

// running() -> bool. True while the core-1 feeder task is alive.
static mp_obj_t kc_audio_running(void) {
#if KC_AUDIO_HAVE_IDF
    return mp_obj_new_bool(s_task != NULL);
#else
    return mp_const_false;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(kc_audio_running_obj, kc_audio_running);

static const mp_rom_map_elem_t kc_audio_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),     MP_OBJ_NEW_QSTR(MP_QSTR_kc_audio) },
    { MP_ROM_QSTR(MP_QSTR_CHANNELS),     MP_ROM_INT(KC_CHANNELS) },
    // per-block kernel (legacy core-0 feed + host build)
    { MP_ROM_QSTR(MP_QSTR_voice_set),    MP_ROM_PTR(&kc_audio_voice_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_voice_read),   MP_ROM_PTR(&kc_audio_voice_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_render),       MP_ROM_PTR(&kc_audio_render_obj) },
    // core-1 feeder task (#41): I2S ownership + the shared-voice handoff
    { MP_ROM_QSTR(MP_QSTR_audio_start),  MP_ROM_PTR(&kc_audio_audio_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_audio_stop),   MP_ROM_PTR(&kc_audio_audio_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_voice_lock),   MP_ROM_PTR(&kc_audio_voice_lock_obj) },
    { MP_ROM_QSTR(MP_QSTR_voice_unlock), MP_ROM_PTR(&kc_audio_voice_unlock_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_master),   MP_ROM_PTR(&kc_audio_set_master_obj) },
    { MP_ROM_QSTR(MP_QSTR_active_mask),  MP_ROM_PTR(&kc_audio_active_mask_obj) },
    { MP_ROM_QSTR(MP_QSTR_running),      MP_ROM_PTR(&kc_audio_running_obj) },
};
static MP_DEFINE_CONST_DICT(kc_audio_globals, kc_audio_globals_table);

const mp_obj_module_t mp_module_kc_audio = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&kc_audio_globals,
};

MP_REGISTER_MODULE(MP_QSTR_kc_audio, mp_module_kc_audio);
