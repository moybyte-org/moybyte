// Moybyte moy_audio: the MicroPython binding for libmoy's SPEC.md 8 synth.
//
// WHAT THIS IS
// A thin shim. The synthesizer is libmoy/moy_audio.c -- moy-spec's own C
// implementation of SPEC.md 8, vendored verbatim (see libmoy/UPSTREAM.md) --
// and this file does three things and no more: it hands that library a place to
// put its bank, forwards the six 8.2 verbs from Python, and owns the I2S plumbing
// libmoy deliberately has none of.
//
// WHY THE BINDING GOT SMALLER (#97)
// It used to be a REIMPLEMENTATION: a hand-ported copy of runtime/audio.py's
// per-sample loop, plus the machinery to keep the two halves in step. Python owned
// the bank, the voices and the music scheduler; every frame it pushed all four
// voices' entire state across the boundary (voice_set: up to 64 steps x 4 fields
// per voice, through mp_obj_get_int), C advanced them, and Python read the
// advanced cursor back (voice_read). The core-1 task then had to snapshot that
// shared array, mix from the copy, and fold the result back ONLY where core 0
// had not re-triggered underneath it -- which needed a per-voice commit counter,
// because the obvious "did anything change?" proxy aliased on a same-sfx
// retrigger and silently dropped overlapping sound effects.
//
// All of that existed to keep two copies of one state consistent. libmoy owns
// the state now, so there is one copy, and the entire class of bug goes with it:
// no voice_set, no voice_read, no snapshot, no fold-back, no commit counter, and
// nothing marshalled per frame. The bank crosses the boundary ONCE per cart, as
// the sounds.json text the store already has.
//
// THE CORE SPLIT (unchanged in shape -- #41's crackle fix)
//   core 0 (MP VM): calls the verbs. That is all. No per-sample work, no I2S.
//   core 1 (C task): renders blocks straight out of libmoy and writes them to
//                    I2S, blocking on the DMA drain -- which is what paces it to
//                    the audio clock, on a core the VM is not on. It never
//                    touches the MicroPython heap or the GIL.
// A mutex guards the one moy_audio struct. The task renders in small CHUNKS and
// releases between them, so a verb called from core 0 waits tens of microseconds,
// not a whole block.
//
// FALLBACK: if the task or the I2S channel can't be created, audio_start()
// returns False and DeviceAudio drives render() itself from the frame loop
// (machine.I2S), with no rebuild needed.
//
// NEEDS ON-DEVICE VERIFICATION: the synth half is checked against libmoy under
// the desktop VM (tests/test_audio_parity.py), but I2S, the core-1 task and the
// PSRAM bank placement cannot be exercised off-hardware. Do not claim this plays
// on a board until a board has played it.

#include <stdlib.h>
#include <string.h>
#include "py/obj.h"
#include "py/runtime.h"

// libmoy, vendored. This is the whole synthesizer.
#include "moy_audio.h"

// ESP-IDF I2S + FreeRTOS exist only in the firmware build. Everything device-only
// hides behind MOY_AUDIO_HAVE_IDF so the module still compiles (synth + render
// entry, no core-1 task) for ports/unix and the webassembly runner -- the same
// guard moy_sd uses.
#ifdef ESP_IDF_VERSION
#include "driver/i2s_std.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
// xTaskCreatePinnedToCore (the SMP core-affinity API) is in the IDF additions
// header, not vanilla FreeRTOS task.h -- include it explicitly.
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "esp_heap_caps.h"
#define MOY_AUDIO_HAVE_IDF 1
#else
#define MOY_AUDIO_HAVE_IDF 0
#endif

// --- core-1 task tuning ---------------------------------------------------
// Mix/write block: small enough that the task tops the DMA up continuously,
// large enough that per-block overhead is negligible. 256 frames @ 8 kHz = 32 ms.
#define MOY_BLOCK_FRAMES  256
// How much the task renders per mutex acquisition. libmoy's render is a pure
// function of its state, so a block can be produced in pieces with the lock
// dropped between them; this bounds how long a core-0 verb call can be made to
// wait. 32 frames is ~4 ms of audio and well under 100 us of mixing.
#define MOY_MIX_CHUNK     32
// I2S DMA ring: dma_desc_num * dma_frame_num frames buffered in hardware.
// 6 * 256 = 1536 frames ~= 0.19 s @ 8 kHz -- a deep cushion the task keeps
// topped, independent of core 0's frame jitter.
#define MOY_DMA_DESC_NUM  6
#define MOY_DMA_FRAME_NUM 512   /* 6x512 = ~140ms of hardware cushion at 22050
                                   (was 256: ~190ms at the old 8000 rate) */
// A finite (not portMAX_DELAY) write timeout means a stuck channel cannot wedge
// the task forever -- it loops and retries.
#define MOY_WRITE_TIMEOUT_MS 100

// --- the one copy of the state --------------------------------------------
// The bank is ~33 KB, so it is allocated rather than declared: on the S3 that
// belongs in PSRAM (internal SRAM is contended -- the Lua allocator alone wants
// a 48 KB floor there), and everywhere else a plain malloc. NOT m_malloc: this
// must not live on the MicroPython GC heap, where a collection could move it
// while the core-1 task is reading it through libmoy's `const moy_bank *`.
static moy_bank  *s_bank = NULL;
static moy_audio  s_audio;
static int        s_rate = 8000;
static int        s_inited = 0;

#if MOY_AUDIO_HAVE_IDF
static i2s_chan_handle_t s_tx_chan = NULL;
static TaskHandle_t      s_task = NULL;
static SemaphoreHandle_t s_mutex = NULL;
static volatile int      s_running = 0;
// Frames the I2S peripheral has actually ACCEPTED. The feeder blocks on the DMA
// drain, so this counter advances at the hardware's true consumption rate --
// divide it by wall-clock and you get the rate the speaker is really running at,
// which is the one number that says whether playback speed matches the rate
// libmoy synthesised for. Written only by the core-1 task, read by anyone.
static volatile uint32_t s_frames_out = 0;
// The two sides of the render->speaker seam (2026-08-10 tempo hunt): the
// engine timeline advances by RENDERED frames; the speaker plays WRITTEN
// frames. If rendered outruns written, something consumes engine time
// whose output never reaches the DMA -- audibly fast playback that every
// per-side counter calls correct.
static volatile uint32_t s_frames_rendered = 0;   // by the core-1 task

#endif

// The Python-side half of that seam, and it lives OUTSIDE the IDF guard because
// mod_render is what the host, the unix test build and the wasm runner call --
// they have no core-1 task, so this is the only frame counter they have. It was
// declared inside the guard when the tempo hunt added it (2026-08-10), which
// left every non-IDF build referencing an undeclared symbol: a compile error
// nobody saw until the web runner was next rebuilt (moycore stage 4).
static volatile uint32_t s_frames_pyrender = 0;   // by mod_render (Python)

// The lock is a no-op until the core-1 task exists; in the fallback and host
// builds the MP thread is the only accessor.
static inline void moy_lock(void) {
#if MOY_AUDIO_HAVE_IDF
    if (s_mutex != NULL) {
        xSemaphoreTake(s_mutex, portMAX_DELAY);
    }
#endif
}

static inline void moy_unlock(void) {
#if MOY_AUDIO_HAVE_IDF
    if (s_mutex != NULL) {
        xSemaphoreGive(s_mutex);
    }
#endif
}

// Bring the engine up on the (possibly empty) bank. Safe to call repeatedly.
static void moy_engine_init_locked(void) {
    int master = s_inited ? s_audio.master : 7;   // keep the user's volume
    moy_audio_init(&s_audio, s_bank, s_rate);
    s_audio.master = master;
    s_inited = 1;
}

static int moy_ensure_bank(void) {
    if (s_bank != NULL) {
        return 1;
    }
#if MOY_AUDIO_HAVE_IDF
    s_bank = heap_caps_malloc(sizeof(moy_bank), MALLOC_CAP_SPIRAM);
    if (s_bank == NULL) {
        s_bank = heap_caps_malloc(sizeof(moy_bank), MALLOC_CAP_DEFAULT);
    }
#else
    s_bank = malloc(sizeof(moy_bank));
#endif
    if (s_bank == NULL) {
        return 0;
    }
    memset(s_bank, 0, sizeof(moy_bank));
    moy_engine_init_locked();
    return 1;
}

// --- Python-facing API ----------------------------------------------------

// bank_load(json) -> bool. Parse a cart's sounds.json into the engine's bank.
// ONE crossing per cart -- the text the store already holds, handed to libmoy's
// own parser, so the device reads exactly what every other libmoy host reads.
// False means the JSON was malformed or exceeded libmoy's fixed capacities (64
// sfx x 64 steps, 32 tracks x 64 rows); the bank is then zeroed and silent
// rather than half-loaded, and the caller can say so.
static mp_obj_t mod_bank_load(mp_obj_t json_obj) {
    const char *json = mp_obj_str_get_str(json_obj);
    int err;
    if (!moy_ensure_bank()) {
        return mp_const_false;
    }
    moy_lock();
    err = moy_bank_parse(s_bank, json);
    moy_engine_init_locked();       // rebind + reset; a new cart starts silent
    moy_unlock();
    return err ? mp_const_false : mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_bank_load_obj, mod_bank_load);

// set_rate(hz). Before audio_start; the task reads it when it opens the channel.
static mp_obj_t mod_set_rate(mp_obj_t rate_obj) {
    int rate = (int)mp_obj_get_int(rate_obj);
    if (rate <= 0) {
        rate = 8000;
    }
    moy_lock();
    s_rate = rate;
    if (s_inited) {
        s_audio.rate = rate;
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_rate_obj, mod_set_rate);

// --- SPEC.md 8.2, one entry per verb --------------------------------------

static mp_obj_t mod_sfx(size_t n_args, const mp_obj_t *a) {
    int n = (int)mp_obj_get_int(a[0]);
    int chan = (n_args > 1 && a[1] != mp_const_none)
             ? (int)mp_obj_get_int(a[1]) : -1;
    moy_lock();
    if (s_inited) {
        moy_audio_sfx(&s_audio, n, chan);
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_sfx_obj, 1, 2, mod_sfx);

static mp_obj_t mod_beep(size_t n_args, const mp_obj_t *a) {
    float freq = (float)mp_obj_get_float(a[0]);
    float dur = (n_args > 1) ? (float)mp_obj_get_float(a[1]) : 0.15f;
    moy_lock();
    if (s_inited) {
        moy_audio_beep(&s_audio, freq, dur);
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_beep_obj, 1, 2, mod_beep);

static mp_obj_t mod_music(size_t n_args, const mp_obj_t *a) {
    int track = (int)mp_obj_get_int(a[0]);
    int loop = (n_args > 1) ? (mp_obj_is_true(a[1]) ? 1 : 0) : 1;
    moy_lock();
    if (s_inited) {
        moy_audio_music(&s_audio, track, loop);
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_music_obj, 1, 2, mod_music);

static mp_obj_t mod_music_stop(void) {
    moy_lock();
    if (s_inited) {
        moy_audio_music_stop(&s_audio);
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_music_stop_obj, mod_music_stop);

static mp_obj_t mod_sound_stop(size_t n_args, const mp_obj_t *a) {
    int chan = (n_args > 0 && a[0] != mp_const_none)
             ? (int)mp_obj_get_int(a[0]) : -1;
    moy_lock();
    if (s_inited) {
        moy_audio_sound_stop(&s_audio, chan);
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_sound_stop_obj, 0, 1,
                                           mod_sound_stop);

static mp_obj_t mod_volume(mp_obj_t level_obj) {
    int level = (int)mp_obj_get_int(level_obj);
    moy_lock();
    if (s_inited) {
        moy_audio_volume(&s_audio, level);
    }
    moy_unlock();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_volume_obj, mod_volume);

// --- render + state -------------------------------------------------------

// render(buf, nframes) -> frames written. Mixes straight into the caller's
// buffer. Used by the fallback per-frame feed and by the web runner, which pulls
// finished PCM each frame and hands it to the page.
//
// libmoy writes native-endian int16; every target here (Xtensa, RISC-V, wasm,
// x86) is little-endian, which is what machine.I2S and the page's PCM path both
// expect. A bytearray's buffer comes from malloc, so the int16 alignment holds.
static mp_obj_t mod_render(size_t n_args, const mp_obj_t *a) {
    mp_buffer_info_t bi;
    mp_int_t nframes = mp_obj_get_int(a[1]);
    if (nframes <= 0) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    mp_get_buffer_raise(a[0], &bi, MP_BUFFER_WRITE);
    if ((size_t)nframes * 2u > bi.len) {    // never overrun a short buffer
        nframes = (mp_int_t)(bi.len / 2u);
    }
    if (nframes <= 0) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    if (n_args > 2) {                       // optional live rate override
        mp_int_t rate = mp_obj_get_int(a[2]);
        if (rate > 0) {
            s_rate = (int)rate;
        }
    }
    moy_lock();
    if (!s_inited) {
        memset(bi.buf, 0, (size_t)nframes * 2u);
    } else {
        s_audio.rate = s_rate;
        moy_audio_render(&s_audio, (int16_t *)bi.buf, (int)nframes);
        s_frames_pyrender += (uint32_t)nframes;
    }
    moy_unlock();
    return MP_OBJ_NEW_SMALL_INT(nframes);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_render_obj, 2, 3,
                                           mod_render);

// active() -> int. Bit c per sounding voice, bit 4 for a running music track,
// bit 5 for the beep. Non-zero is "something is audible" -- what the Music
// editor's preview and the console's redraw gate ask.
static mp_obj_t mod_active(void) {
    uint32_t mask = 0;
    int i;
    moy_lock();
    if (s_inited) {
        for (i = 0; i < MOY_A_CHANNELS; i++) {
            if (s_audio.v[i].owner) {
                mask |= (uint32_t)1 << i;
            }
        }
        if (s_audio.track != NULL) {
            mask |= (uint32_t)1 << 4;
        }
        if (s_audio.bleft > 0.0f) {
            mask |= (uint32_t)1 << 5;
        }
    }
    moy_unlock();
    return mp_obj_new_int_from_uint(mask);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_active_obj, mod_active);

#if MOY_AUDIO_HAVE_IDF
// --- the core-1 feeder task ----------------------------------------------
static void moy_audio_task(void *arg) {
    (void)arg;
    static int16_t block[MOY_BLOCK_FRAMES];
    int off;

    while (s_running) {
        // Render a block in chunks, dropping the lock between them so a verb
        // from core 0 is never held up for a whole block.
        for (off = 0; off < MOY_BLOCK_FRAMES; off += MOY_MIX_CHUNK) {
            moy_lock();
            if (s_inited) {
                moy_audio_render(&s_audio, block + off, MOY_MIX_CHUNK);
                s_frames_rendered += MOY_MIX_CHUNK;
            } else {
                memset(block + off, 0, sizeof(int16_t) * MOY_MIX_CHUNK);
            }
            moy_unlock();
        }

        // Blocks here while the DMA drains -- that IS the pacing, and it happens
        // on core 1, so the VM never stalls on it.
        //
        // Write the WHOLE block, retrying the remainder after a timeout
        // (2026-08-10): the old single write dropped whatever a timeout left
        // unwritten and moved on to render the NEXT block -- every drop skips
        // the synth timeline forward, which the ear hears as SPED-UP audio on
        // every cart, and which frames_out cannot see (it counts writes, so
        // AUDIORATE read ~1.0 while the music ran fast). The sustained >1.0
        // AUDIORATE means (1.01-1.08 steady, 1.1+ during sfx bursts) were this
        // hole measured from the other side: rendered-minus-played.
        // NEVER abandon rendered frames (2026-08-10, the celeste 1.6x): the
        // driver chronically accepts only part of a block per call here, and
        // every abandoned tail advances the synth timeline without reaching
        // the speaker -- the seam counters measured rendered/written at the
        // exact audible tempo error (1.61-1.64) while both per-side clocks
        // read 1.000. Retry until the WHOLE block is consumed; a timeout just
        // loops (s_running is the only exit), so a wedged channel parks the
        // task at 100ms polls instead of silently eating the music.
        size_t done = 0;
        while (done < sizeof(block) && s_running) {
            size_t written = 0;
            i2s_channel_write(s_tx_chan, (const char *)block + done,
                              sizeof(block) - done, &written,
                              pdMS_TO_TICKS(MOY_WRITE_TIMEOUT_MS));
            done += written;
        }
        s_frames_out += (uint32_t)(done / sizeof(int16_t));
    }

    if (s_tx_chan != NULL) {
        i2s_channel_disable(s_tx_chan);
    }
    s_task = NULL;
    vTaskDelete(NULL);
}
#endif  // MOY_AUDIO_HAVE_IDF

// audio_start(bck, ws, dout, rate) -> True if the core-1 feeder is running.
// On ANY failure it tears everything back down and returns False, so DeviceAudio
// falls back to the per-frame feed. Idempotent while running.
static mp_obj_t mod_audio_start(size_t n_args, const mp_obj_t *a) {
#if MOY_AUDIO_HAVE_IDF
    int bck  = (n_args > 0) ? (int)mp_obj_get_int(a[0]) : 7;
    int ws   = (n_args > 1) ? (int)mp_obj_get_int(a[1]) : 5;
    int dout = (n_args > 2) ? (int)mp_obj_get_int(a[2]) : 6;
    int rate = (n_args > 3) ? (int)mp_obj_get_int(a[3]) : 8000;
    if (rate <= 0) {
        rate = 8000;
    }
    if (s_task != NULL) {
        return mp_const_true;
    }
    if (!moy_ensure_bank()) {
        return mp_const_false;
    }
    s_rate = rate;
    s_audio.rate = rate;

    if (s_mutex == NULL) {
        s_mutex = xSemaphoreCreateMutex();
        if (s_mutex == NULL) {
            return mp_const_false;
        }
    }

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = MOY_DMA_DESC_NUM;
    chan_cfg.dma_frame_num = MOY_DMA_FRAME_NUM;
    chan_cfg.auto_clear = true;   // emit silence, not stale DMA, on under-run
    if (i2s_new_channel(&chan_cfg, &s_tx_chan, NULL) != ESP_OK) {
        s_tx_chan = NULL;
        return mp_const_false;
    }

    // Standard Philips I2S, 16-bit mono on the T-Deck amp pins. MONO puts the
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

    s_running = 1;
    BaseType_t ok = xTaskCreatePinnedToCore(
        moy_audio_task, "moy_audio", 4096, NULL,
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
    return mp_const_false;   // no IDF -> no core-1 task; Python uses render()
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_audio_start_obj, 0, 4,
                                           mod_audio_start);

// audio_stop() -> None. Signal the task to exit and release the I2S channel.
// Safe when not running.
static mp_obj_t mod_audio_stop(void) {
#if MOY_AUDIO_HAVE_IDF
    if (s_task == NULL) {
        if (s_tx_chan != NULL) {
            i2s_del_channel(s_tx_chan);
            s_tx_chan = NULL;
        }
        return mp_const_none;
    }
    s_running = 0;
    // Bounded wait for the task to observe it and self-delete; never forever.
    for (int i = 0; i < 40 && s_task != NULL; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (s_tx_chan != NULL) {
        i2s_del_channel(s_tx_chan);   // the task already disabled it
        s_tx_chan = NULL;
    }
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_audio_stop_obj, mod_audio_stop);

// running() -> bool. True while the core-1 feeder is alive.
static mp_obj_t mod_running(void) {
#if MOY_AUDIO_HAVE_IDF
    return mp_obj_new_bool(s_task != NULL);
#else
    return mp_const_false;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_running_obj, mod_running);

// engine_sig() -> (rate, nsfx, sfx10_speed, music4_speed) read from the C
// structs themselves (2026-08-10, the celeste tempo hunt): BANKSIG certifies
// the PYTHON bank at push time; this reads back what libmoy actually HOLDS
// after bank_load, closing the last unverified hop of the json crossing.
static mp_obj_t mod_engine_sig(void) {
    mp_obj_t t[4];
    moy_lock();
    int rate = s_inited ? (int)s_audio.rate : s_rate;
    int nsfx = (s_bank != NULL) ? s_bank->nsfx : -1;
    float s10 = (s_bank != NULL && s_bank->nsfx > 10)
                ? s_bank->sfx[10].speed : -1.0f;
    float m4 = (s_bank != NULL && s_bank->nmusic > 4)
               ? s_bank->music[4].speed : -1.0f;
    moy_unlock();
    t[0] = mp_obj_new_int(rate);
    t[1] = mp_obj_new_int(nsfx);
    t[2] = mp_obj_new_float((mp_float_t)s10);
    t[3] = mp_obj_new_float((mp_float_t)m4);
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_engine_sig_obj, mod_engine_sig);

// frames_out() -> (frames, rate). Frames the I2S peripheral has accepted since
// boot, and the rate libmoy is synthesising for. Sampling this against wall
// clock answers the one question the synth cannot: is the speaker consuming
// samples at the rate they were MADE for? A ratio other than 1.0 is playback
// speed error -- pitch and tempo together -- and no amount of staring at the
// mixer will show it, because the mixer is right.
static mp_obj_t mod_frames_out(void) {
    mp_obj_t t[4];
#if MOY_AUDIO_HAVE_IDF
    t[0] = mp_obj_new_int_from_uint(s_frames_out);
#else
    t[0] = mp_obj_new_int_from_uint(0);
#endif
    t[1] = mp_obj_new_int(s_inited ? (int)s_audio.rate : s_rate);
#if MOY_AUDIO_HAVE_IDF
    t[2] = mp_obj_new_int_from_uint(s_frames_rendered);
#else
    t[2] = mp_obj_new_int_from_uint(0);
#endif
    t[3] = mp_obj_new_int_from_uint(s_frames_pyrender);
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_frames_out_obj, mod_frames_out);

static const mp_rom_map_elem_t moy_audio_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),     MP_OBJ_NEW_QSTR(MP_QSTR_moy_audio) },
    { MP_ROM_QSTR(MP_QSTR_CHANNELS),     MP_ROM_INT(MOY_A_CHANNELS) },
    // the bank + the SPEC.md 8.2 verbs
    { MP_ROM_QSTR(MP_QSTR_bank_load),    MP_ROM_PTR(&mod_bank_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_rate),     MP_ROM_PTR(&mod_set_rate_obj) },
    { MP_ROM_QSTR(MP_QSTR_sfx),          MP_ROM_PTR(&mod_sfx_obj) },
    { MP_ROM_QSTR(MP_QSTR_beep),         MP_ROM_PTR(&mod_beep_obj) },
    { MP_ROM_QSTR(MP_QSTR_music),        MP_ROM_PTR(&mod_music_obj) },
    { MP_ROM_QSTR(MP_QSTR_music_stop),   MP_ROM_PTR(&mod_music_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_sound_stop),   MP_ROM_PTR(&mod_sound_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_volume),       MP_ROM_PTR(&mod_volume_obj) },
    // rendering + state
    { MP_ROM_QSTR(MP_QSTR_render),       MP_ROM_PTR(&mod_render_obj) },
    { MP_ROM_QSTR(MP_QSTR_active),       MP_ROM_PTR(&mod_active_obj) },
    // core-1 feeder task (#41)
    { MP_ROM_QSTR(MP_QSTR_audio_start),  MP_ROM_PTR(&mod_audio_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_audio_stop),   MP_ROM_PTR(&mod_audio_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_frames_out),   MP_ROM_PTR(&mod_frames_out_obj) },
    { MP_ROM_QSTR(MP_QSTR_engine_sig),   MP_ROM_PTR(&mod_engine_sig_obj) },
    { MP_ROM_QSTR(MP_QSTR_running),      MP_ROM_PTR(&mod_running_obj) },
};
static MP_DEFINE_CONST_DICT(moy_audio_globals, moy_audio_globals_table);

const mp_obj_module_t mp_module_moy_audio = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_audio_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_audio, mp_module_moy_audio);
