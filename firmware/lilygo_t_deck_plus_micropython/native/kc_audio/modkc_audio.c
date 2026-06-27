// KidCode kc_audio: a focused native PCM mixer for the v0.4 console (#16).
//
// WHY THIS EXISTS
// On hardware the per-frame software mixer in runtime/audio.py (AudioEngine.
// render_into) is the bottleneck: a 320x240 cart at 30 FPS leaves only a few ms
// per frame for audio, and the MicroPython inner loop -- one Python-level
// iteration per output sample, per voice -- is far too slow (the beeper runs
// ~12 FPS and crackles). This module moves ONLY that hot inner loop into C while
// leaving the data model, control surface and music scheduler in Python.
//
// SCOPE (deliberately small -- NOT TulipCC/AMY)
// The KidCode sound model is tiny: 4 voices, each a short list of [pitch, wave,
// vol] steps stepped at a fixed rate, 4 waveforms (square/tri/saw/noise), a
// per-voice noise LCG, a master volume and a fixed /CHANNELS mixdown. That whole
// per-sample model lives here in C and is a byte-for-byte port of
// AudioEngine.render_into, so the SAME .kcart sounds identical on host and device.
//
// THE SPLIT (mirror of runtime/audio.py)
//   Python keeps: the WHOLE model + control surface + music scheduler, and stays
//                 the single source of truth for every voice's state (its _Voice
//                 objects). audio.py is untouched: the host runs it unchanged.
//   C (here) is a PURE PER-BLOCK KERNEL: before each block DeviceAudio pushes every
//                 voice's exact state in (voice_set), renders the heavy per-sample
//                 loop (render), then reads the advanced state back out (voice_read)
//                 into the Python _Voice. C holds no authoritative cross-block
//                 state, so it can never drift from the Python mixer -- it is just
//                 a fast, exact re-implementation of render_into's inner loop.
//
// VM-NEUTRAL HOT PATH: render() takes a plain writable buffer + an int count and
// touches no Python objects per sample, the same discipline as native/kc_gfx.
//
// NEEDS ON-DEVICE VERIFICATION: built + unit-checked against the Python mixer, but
// the audible result on the MAX98357 amp is unproven in this environment.

#include <math.h>
#include "py/obj.h"
#include "py/runtime.h"

// --- model constants: MUST match runtime/audio.py -------------------------
#define KC_CHANNELS     4       // AudioEngine CHANNELS
#define KC_MAX_STEPS    64      // cap a voice's step list (SFX are short blips)
#define KC_A4_PITCH     57      // semitone index of A4
#define KC_A4_FREQ      440.0

#define KC_WAVE_SQUARE   0
#define KC_WAVE_TRIANGLE 1
#define KC_WAVE_SAW      2
#define KC_WAVE_NOISE    3

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

// The C voice mirror. Single instance -- there is one audio device. This holds only
// the per-voice render state pushed in by voice_set each block; rate + master volume
// are passed to render() per call, so the module keeps no authoritative cross-block
// engine state (Python's AudioEngine remains the single source of truth).
static kc_voice_t kc_voices[KC_CHANNELS];

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

// --- Python-facing API ----------------------------------------------------

// voice_set(chan, active, steps, step_dur, loop, idx, t, phase, noise) -- push the
// EXACT state of a Python _Voice into the C mirror before a render block. Unlike a
// "play" trigger this sets every field verbatim (no idx/t/phase reset), so C is a
// pure function of the pushed state and reproduces render_into bit-for-bit. `steps`
// is any iterable of 3-element [pitch, wave, vol] sequences (>KC_MAX_STEPS dropped).
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

// render(out, nframes, rate, master) -> nframes_written. Mix `nframes` of signed-
// 16-bit little-endian mono PCM into `out` (a writable buffer of >= nframes*2
// bytes), advancing every voice's phase + step cursor. This is the heavy inner
// loop -- a byte-for-byte port of AudioEngine.render_into's per-sample body (minus
// the music scheduler, which Python runs between blocks). `rate`/`master` are passed
// in each call so C keeps no engine state.
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
    uint8_t *out = (uint8_t *)bi.buf;

    mp_int_t rate = mp_obj_get_int(a[2]);
    if (rate <= 0) {
        rate = 8000;
    }
    double master = mp_obj_get_float(a[3]);
    if (master < 0.0) {
        master = 0.0;
    } else if (master > 1.0) {
        master = 1.0;
    }
    const double inv_rate = 1.0 / (double)rate;

    for (mp_int_t i = 0; i < nframes; i++) {
        double acc = 0.0;
        for (int c = 0; c < KC_CHANNELS; c++) {
            kc_voice_t *v = &kc_voices[c];
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
    return MP_OBJ_NEW_SMALL_INT(nframes);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_audio_render_obj, 4, 4, kc_audio_render);

static const mp_rom_map_elem_t kc_audio_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),    MP_OBJ_NEW_QSTR(MP_QSTR_kc_audio) },
    { MP_ROM_QSTR(MP_QSTR_CHANNELS),    MP_ROM_INT(KC_CHANNELS) },
    { MP_ROM_QSTR(MP_QSTR_voice_set),   MP_ROM_PTR(&kc_audio_voice_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_voice_read),  MP_ROM_PTR(&kc_audio_voice_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_render),      MP_ROM_PTR(&kc_audio_render_obj) },
};
static MP_DEFINE_CONST_DICT(kc_audio_globals, kc_audio_globals_table);

const mp_obj_module_t mp_module_kc_audio = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&kc_audio_globals,
};

MP_REGISTER_MODULE(MP_QSTR_kc_audio, mp_module_kc_audio);
