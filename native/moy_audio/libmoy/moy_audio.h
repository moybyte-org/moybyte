/* moy_audio -- SPEC.md 8 as a freestanding synthesizer.
 *
 * OPTIONAL. libmoy's core never references this file; a host that wants
 * silence simply does not use it, and SPEC.md 8.3 blesses that. A host that
 * wants sound gets the whole of SPEC.md 8 -- the eight waveforms, the seven
 * effects, the sfx step sequencer and the music row sequencer with its
 * channel-claiming rules -- by doing three things:
 *
 *   1. moy_bank_parse(&bank, sounds_json_text)     once, at cart load
 *   2. moy_audio_init(&audio, &bank, sample_rate)  once, when audio opens
 *   3. moy_audio_render(&audio, buf, nframes)      from the audio callback
 *
 * and routing the six moy_host audio hooks to the moy_audio_* calls below.
 * The SDL2 port does exactly this in ~50 lines; an ESP32 host renders into
 * an I2S DMA buffer instead and nothing else changes.
 *
 * Same contract as the rest of the library: no allocation, no I/O, no clock.
 * The parser reads a text buffer the HOST slurped; the synth is a pure
 * function of (bank, verb calls, sample count). Not thread-safe by itself --
 * verbs mutate what render reads, so a host whose audio runs on another
 * thread brackets the verb calls with its own lock (SDL_LockAudioDevice in
 * the port).
 *
 * Fixed capacities, like the sheet and the map: the bank is a static ~34 KB.
 * SPEC.md puts no ceiling on sounds.json; these are THIS library's, chosen to
 * hold every PICO-8 cart (64 sfx of 64 steps; PICO-8 tops out at 64 x 32),
 * and moy_bank_parse reports the cart that exceeds them rather than playing
 * half of it. */

#ifndef MOY_AUDIO_H
#define MOY_AUDIO_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MOY_A_SFX_MAX    64     /* sfx entries in the bank */
#define MOY_A_STEPS_MAX  64     /* steps per sfx */
#define MOY_A_MUSIC_MAX  32     /* music tracks */
#define MOY_A_ROWS_MAX   64     /* pattern rows per track */
#define MOY_A_CHANNELS   4      /* SPEC.md 8: fixed */

/* SPEC.md 8.1: the atom. pitch -1 is a rest; 57 = A4 = 440 Hz. */
typedef struct {
    int8_t  pitch;              /* 0..95, or -1 */
    uint8_t wave;               /* 0..7 */
    uint8_t vol;                /* 0..7 */
    uint8_t eff;                /* 0..7 */
} moy_note;

/* PICO-8's per-sfx FILTER byte, carried verbatim the way `eff` is (SPEC.md
 * 8.1). Five independent settings packed into one byte, and a cart from 0.2.4
 * on leans on them hard -- one measured cart used reverb on 17 of its 23
 * sounds, dampen on 14, detune on 13. Absent or 0 is the dry sound every
 * earlier cart has, so this is additive. */
#define MOY_A_F_NOIZ(f)    (((f) & 0x2) != 0)   /* noise x sawtooth */
#define MOY_A_F_BUZZ(f)    (((f) & 0x4) != 0)   /* the harsher wave variants */
#define MOY_A_F_DETUNE(f)  (((f) / 8) % 3)      /* 0 none, 1 or 2 */
#define MOY_A_F_REVERB(f)  (((f) / 24) % 3)
#define MOY_A_F_DAMPEN(f)  (((f) / 72) % 3)

typedef struct {
    float    speed;             /* steps per second, default 8 */
    uint8_t  loop;
    uint8_t  loop_start;
    uint8_t  nsteps;
    uint8_t  filters;           /* PICO-8's filter byte; 0 = dry */
    moy_note steps[MOY_A_STEPS_MAX];
} moy_sfx_def;

typedef struct {
    float   speed;              /* rows per second, default 4; fractions legal */
    uint8_t loop;               /* track default; the verb's arg overrides */
    uint8_t width;              /* channels claimed: widest row */
    uint8_t nrows;
    uint8_t has_row_secs;
    int8_t  rows[MOY_A_ROWS_MAX][MOY_A_CHANNELS];   /* sfx id, -1 silent */
    float   row_secs[MOY_A_ROWS_MAX];               /* 0 = hold forever */
} moy_music_def;

typedef struct {
    moy_sfx_def   sfx[MOY_A_SFX_MAX];
    moy_music_def music[MOY_A_MUSIC_MAX];
    int nsfx, nmusic;
} moy_bank;

/* A biquad, Direct Form I. Only the two "dampen" high shelves use it, and
 * their coefficients come from the W3C audio-EQ cookbook. */
typedef struct {
    float c1, c2, c3, c4, c5;
    float li, lli, lo, llo;
} moy_biquad;

/* The reverb delay lines, in samples AT 22050 Hz -- PICO-8's own rate, and
 * the device's. 16.6 ms and 33.2 ms. They are int16_t rather than float
 * because they are the single biggest thing in this struct and it lives in a
 * board's .bss: float would cost 17.6 KB of SRAM across four voices, and the
 * ring already holds what is about to become an int16 sample anyway. */
#define MOY_A_REVERB1  366
#define MOY_A_REVERB2  732

/* One playing sfx. Internal, but in the header so the whole state is a
 * plain struct a host (or a test) can place and inspect. */
typedef struct {
    uint8_t  owner;             /* 0 free, 1 sfx verb, 2 music */
    const moy_sfx_def *s;
    int      step;
    int      samp;              /* samples into the current step: integer so
                                 * step boundaries stay exact at any length */
    float    phase, phase2;     /* phase2: the phaser's detuned partner */
    uint32_t rng;               /* noise LCG state (wave 3) */
    float    nfrom, nto;        /* noise: low-pass state / shaped output */
    float    amp;               /* de-click amplitude slew (current gain) */
    float    prev_pitch;        /* slide origin: the channel's previous
                                 * SOUNDING note. Survives retriggers on
                                 * purpose -- 8.1 says a slide carries across
                                 * a row boundary. -1 = no previous note yet */
    float    prev_vol;
    /* --- the filter set (SPEC.md 8.1's `filters`), all per-CHANNEL ------ */
    float    dphase, dphase2;   /* detune: the second oscillator's own phase,
                                 * accumulated at freq*factor rather than
                                 * scaled off `phase` -- a wrapped phase
                                 * cannot be multiplied by a non-integer */
    uint8_t  cyc;               /* which of the saw buzz's two cycles we are
                                 * in; its dip loops on a 2x period */
    int16_t  rv1[MOY_A_REVERB1];
    int16_t  rv2[MOY_A_REVERB2];
    int      rvi;
    int      rvn1, rvn2;        /* the rings' live length at THIS rate */
    uint8_t  fxlast;            /* the filters of the last sfx that played --
                                 * a reverb tail has to ring out AFTER the
                                 * note that made it has ended */
    int      fxtail;            /* frames of tail still owed */
    moy_biquad damp1, damp2;
} moy_voice;

typedef struct {
    const moy_bank *bank;       /* may be NULL: every verb no-ops, render is silence */
    int   rate;                 /* output sample rate, Hz */
    int   master;               /* volume(level), 0..7, default 7 */
    moy_voice v[MOY_A_CHANNELS];
    int   rr;                   /* sfx round-robin cursor */
    /* music sequencer */
    const moy_music_def *track;
    int   mrow;
    int   msamp;                /* samples into the row, same integer rule */
    int   mloop;
    /* beep: engine-native, outside the four channels */
    float bfreq, bleft, bphase;
} moy_audio;

/* Fill `b` from sounds.json text. Returns 0 on success; nonzero (and a
 * zeroed, silent bank) on malformed JSON or a cart past the capacities
 * above. NULL or empty text is a valid silent bank, not an error -- carts
 * without sound exist. */
int moy_bank_parse(moy_bank *b, const char *json);

void moy_audio_init(moy_audio *a, const moy_bank *bank, int sample_rate);

/* SPEC.md 8.2, one entry per verb. All of them safe on a NULL bank. */
void moy_audio_sfx(moy_audio *a, int n, int chan);        /* chan < 0: round-robin */
void moy_audio_beep(moy_audio *a, float freq_hz, float dur_s);
void moy_audio_music(moy_audio *a, int track, int loop);
void moy_audio_music_stop(moy_audio *a);
void moy_audio_sound_stop(moy_audio *a, int chan);        /* chan < 0: all */
void moy_audio_volume(moy_audio *a, int level);

/* Mix `nframes` samples of signed 16-bit mono into `out`. */
void moy_audio_render(moy_audio *a, int16_t *out, int nframes);

#ifdef __cplusplus
}
#endif

#endif /* MOY_AUDIO_H */
