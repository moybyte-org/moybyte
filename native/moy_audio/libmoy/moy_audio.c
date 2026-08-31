/* moy_audio -- SPEC.md 8: the parser, the sequencers, the eight waves.
 *
 * Everything here follows from two sentences of the spec. 8.3: "waveforms are
 * generated, not sampled, and mixed to signed 16-bit mono; voices sum with
 * each note scaled by vol / 7" -- that is render(). And 8: "music claims
 * channels from the top ... sound effects round-robin across whatever music
 * leaves free" -- that is the whole allocation policy, there is no mixer
 * cleverness beyond it.
 *
 * The number parser is hand-rolled, not strtod: strtod honours the process
 * locale, and a cart that plays correctly until the host runs under a
 * comma-decimal locale is exactly the kind of bug this library exists to make
 * impossible. */

#include <string.h>

#include "moy_audio.h"

/* ------------------------------------------------------------ parsing --- */

typedef struct {
    const char *p;
    int err;
} jp_t;

static void jp_ws(jp_t *j)
{
    while (*j->p == ' ' || *j->p == '\t' || *j->p == '\n' || *j->p == '\r')
        j->p++;
}

static int jp_lit(jp_t *j, char c)
{
    jp_ws(j);
    if (*j->p != c) return 0;
    j->p++;
    return 1;
}

/* A number, locale-proof. Good for every value 8.1 can carry. */
static float jp_num(jp_t *j)
{
    float v = 0.0f, frac = 0.1f;
    int neg = 0, any = 0;
    jp_ws(j);
    if (*j->p == '-') { neg = 1; j->p++; }
    while (*j->p >= '0' && *j->p <= '9') {
        v = v * 10.0f + (float)(*j->p - '0');
        j->p++; any = 1;
    }
    if (*j->p == '.') {
        j->p++;
        while (*j->p >= '0' && *j->p <= '9') {
            v += (float)(*j->p - '0') * frac;
            frac *= 0.1f;
            j->p++; any = 1;
        }
    }
    if (!any) j->err = 1;
    return neg ? -v : v;
}

static int jp_bool(jp_t *j)
{
    jp_ws(j);
    if (!strncmp(j->p, "true", 4))  { j->p += 4; return 1; }
    if (!strncmp(j->p, "false", 5)) { j->p += 5; return 0; }
    j->err = 1;
    return 0;
}

/* A key string; returns its start and length without copying. */
static const char *jp_key(jp_t *j, int *len)
{
    const char *s;
    jp_ws(j);
    if (*j->p != '"') { j->err = 1; return NULL; }
    s = ++j->p;
    while (*j->p && *j->p != '"') j->p++;
    if (!*j->p) { j->err = 1; return NULL; }
    *len = (int)(j->p - s);
    j->p++;
    return s;
}

static int jp_key_is(const char *s, int len, const char *want)
{
    return s && (int)strlen(want) == len && !strncmp(s, want, (size_t)len);
}

/* Skip any value -- unknown keys are legal and ignored, like every other
 * reader of cart JSON in this repository. */
static void jp_skip(jp_t *j)
{
    jp_ws(j);
    if (*j->p == '"') {
        int n;
        (void)jp_key(j, &n);
    } else if (*j->p == '[') {
        j->p++;
        jp_ws(j);
        while (!j->err && *j->p && *j->p != ']') {
            jp_skip(j);
            jp_ws(j);
            if (*j->p == ',') j->p++;
            jp_ws(j);
        }
        if (*j->p == ']') j->p++; else j->err = 1;
    } else if (*j->p == '{') {
        j->p++;
        jp_ws(j);
        while (!j->err && *j->p && *j->p != '}') {
            int n;
            (void)jp_key(j, &n);
            if (!jp_lit(j, ':')) { j->err = 1; break; }
            jp_skip(j);
            jp_ws(j);
            if (*j->p == ',') j->p++;
            jp_ws(j);
        }
        if (*j->p == '}') j->p++; else j->err = 1;
    } else if (!strncmp(j->p, "null", 4)) {
        j->p += 4;
    } else if (*j->p == 't' || *j->p == 'f') {
        (void)jp_bool(j);
    } else {
        (void)jp_num(j);
    }
}

/* [pitch, wave, vol] or [pitch, wave, vol, eff] */
static void jp_note(jp_t *j, moy_note *n)
{
    float f[4] = {0, 0, 6, 0};
    int i = 0;
    if (!jp_lit(j, '[')) { j->err = 1; return; }
    jp_ws(j);
    while (!j->err && *j->p != ']') {
        float v = jp_num(j);
        if (i < 4) f[i] = v;
        i++;
        jp_ws(j);
        if (*j->p == ',') j->p++;
        jp_ws(j);
    }
    if (!jp_lit(j, ']')) j->err = 1;
    if (i < 3) j->err = 1;
    n->pitch = (int8_t)(f[0] < 0 ? -1 : (f[0] > 95 ? 95 : f[0]));
    n->wave  = (uint8_t)((int)f[1] & 7);
    n->vol   = (uint8_t)(f[2] < 0 ? 0 : (f[2] > 7 ? 7 : f[2]));
    n->eff   = (uint8_t)((int)f[3] & 7);
}

static void jp_sfx(jp_t *j, moy_sfx_def *s)
{
    memset(s, 0, sizeof *s);
    s->speed = 8.0f;                    /* SPEC.md 8.1 defaults */
    if (!jp_lit(j, '{')) { j->err = 1; return; }
    jp_ws(j);
    while (!j->err && *j->p != '}') {
        int klen;
        const char *k = jp_key(j, &klen);
        if (!jp_lit(j, ':')) { j->err = 1; return; }
        if (jp_key_is(k, klen, "speed")) {
            s->speed = jp_num(j);
            if (s->speed <= 0.0f) s->speed = 8.0f;
        } else if (jp_key_is(k, klen, "loop")) {
            s->loop = (uint8_t)jp_bool(j);
        } else if (jp_key_is(k, klen, "filters")) {
            s->filters = (uint8_t)jp_num(j);
        } else if (jp_key_is(k, klen, "loop_start")) {
            s->loop_start = (uint8_t)jp_num(j);
        } else if (jp_key_is(k, klen, "steps")) {
            if (!jp_lit(j, '[')) { j->err = 1; return; }
            jp_ws(j);
            while (!j->err && *j->p != ']') {
                moy_note n;
                jp_note(j, &n);
                if (s->nsteps >= MOY_A_STEPS_MAX) { j->err = 1; return; }
                s->steps[s->nsteps++] = n;
                jp_ws(j);
                if (*j->p == ',') j->p++;
                jp_ws(j);
            }
            if (!jp_lit(j, ']')) j->err = 1;
        } else {
            jp_skip(j);
        }
        jp_ws(j);
        if (*j->p == ',') j->p++;
        jp_ws(j);
    }
    if (!jp_lit(j, '}')) j->err = 1;
    if (s->loop_start >= s->nsteps) s->loop_start = 0;
}

/* A row: one sfx id, or a list of up to 4, -1 for silent. */
static void jp_row(jp_t *j, int8_t row[MOY_A_CHANNELS], uint8_t *width)
{
    int i;
    for (i = 0; i < MOY_A_CHANNELS; i++) row[i] = -1;
    jp_ws(j);
    if (*j->p == '[') {
        int n = 0;
        j->p++;
        jp_ws(j);
        while (!j->err && *j->p != ']') {
            float v = jp_num(j);
            if (n < MOY_A_CHANNELS) row[n] = (int8_t)(v < 0 ? -1 : v);
            n++;
            jp_ws(j);
            if (*j->p == ',') j->p++;
            jp_ws(j);
        }
        if (!jp_lit(j, ']')) j->err = 1;
        if (n > MOY_A_CHANNELS) { j->err = 1; return; }
        if ((uint8_t)n > *width) *width = (uint8_t)n;
    } else {
        float v = jp_num(j);
        row[0] = (int8_t)(v < 0 ? -1 : v);
        if (*width < 1) *width = 1;
    }
}

static void jp_music(jp_t *j, moy_music_def *m)
{
    memset(m, 0, sizeof *m);
    m->speed = 4.0f;                    /* SPEC.md 8.1 defaults */
    m->loop = 1;
    if (!jp_lit(j, '{')) { j->err = 1; return; }
    jp_ws(j);
    while (!j->err && *j->p != '}') {
        int klen;
        const char *k = jp_key(j, &klen);
        if (!jp_lit(j, ':')) { j->err = 1; return; }
        if (jp_key_is(k, klen, "speed")) {
            m->speed = jp_num(j);
            if (m->speed <= 0.0f) m->speed = 4.0f;
        } else if (jp_key_is(k, klen, "loop")) {
            m->loop = (uint8_t)jp_bool(j);
        } else if (jp_key_is(k, klen, "pattern")) {
            if (!jp_lit(j, '[')) { j->err = 1; return; }
            jp_ws(j);
            while (!j->err && *j->p != ']') {
                if (m->nrows >= MOY_A_ROWS_MAX) { j->err = 1; return; }
                jp_row(j, m->rows[m->nrows], &m->width);
                m->nrows++;
                jp_ws(j);
                if (*j->p == ',') j->p++;
                jp_ws(j);
            }
            if (!jp_lit(j, ']')) j->err = 1;
        } else if (jp_key_is(k, klen, "row_secs")) {
            int n = 0;
            if (!jp_lit(j, '[')) { j->err = 1; return; }
            jp_ws(j);
            while (!j->err && *j->p != ']') {
                float v = jp_num(j);
                if (n < MOY_A_ROWS_MAX) m->row_secs[n] = v < 0.0f ? 0.0f : v;
                n++;
                jp_ws(j);
                if (*j->p == ',') j->p++;
                jp_ws(j);
            }
            if (!jp_lit(j, ']')) j->err = 1;
            m->has_row_secs = 1;
        } else {
            jp_skip(j);
        }
        jp_ws(j);
        if (*j->p == ',') j->p++;
        jp_ws(j);
    }
    if (!jp_lit(j, '}')) j->err = 1;
}

int moy_bank_parse(moy_bank *b, const char *json)
{
    jp_t j;
    memset(b, 0, sizeof *b);
    if (!json) return 0;                /* no sounds.json: a silent cart */
    j.p = json;
    j.err = 0;
    jp_ws(&j);
    if (!*j.p) return 0;
    if (!jp_lit(&j, '{')) return 1;
    jp_ws(&j);
    while (!j.err && *j.p && *j.p != '}') {
        int klen;
        const char *k = jp_key(&j, &klen);
        if (!jp_lit(&j, ':')) { j.err = 1; break; }
        if (jp_key_is(k, klen, "sfx")) {
            if (!jp_lit(&j, '[')) { j.err = 1; break; }
            jp_ws(&j);
            while (!j.err && *j.p != ']') {
                if (b->nsfx >= MOY_A_SFX_MAX) { j.err = 1; break; }
                jp_sfx(&j, &b->sfx[b->nsfx]);
                b->nsfx++;
                jp_ws(&j);
                if (*j.p == ',') j.p++;
                jp_ws(&j);
            }
            if (!j.err && !jp_lit(&j, ']')) j.err = 1;
        } else if (jp_key_is(k, klen, "music")) {
            if (!jp_lit(&j, '[')) { j.err = 1; break; }
            jp_ws(&j);
            while (!j.err && *j.p != ']') {
                if (b->nmusic >= MOY_A_MUSIC_MAX) { j.err = 1; break; }
                jp_music(&j, &b->music[b->nmusic]);
                b->nmusic++;
                jp_ws(&j);
                if (*j.p == ',') j.p++;
                jp_ws(&j);
            }
            if (!j.err && !jp_lit(&j, ']')) j.err = 1;
        } else {
            jp_skip(&j);
        }
        jp_ws(&j);
        if (*j.p == ',') j.p++;
        jp_ws(&j);
    }
    if (!j.err && !jp_lit(&j, '}')) j.err = 1;
    if (j.err) memset(b, 0, sizeof *b);
    return j.err;
}

/* ---------------------------------------------------------- the synth --- */

/* Pitch -> Hz without a libm dependency: 440 * 2^((n-57)/12), split into
 * octave doublings, a 12-entry semitone table, and a quadratic for the
 * FRACTIONAL semitone (vibrato, slide) -- 2^(f/12), within a hundredth of a
 * cent for f in [0,1). The exponent's /12 belongs to the fraction too: scale
 * the fraction by 2^f instead and a quarter-semitone wobble comes out three
 * semitones wide. */
static float pitch_hz(float semitone)
{
    static const float SEMI[12] = {
        1.0f, 1.059463f, 1.122462f, 1.189207f, 1.259921f, 1.334840f,
        1.414214f, 1.498307f, 1.587401f, 1.681793f, 1.781797f, 1.887749f
    };
    float n = semitone - 57.0f;
    int oct = 0, idx;
    float frac, base;
    while (n < 0.0f)  { n += 12.0f; oct--; }
    while (n >= 12.0f) { n -= 12.0f; oct++; }
    idx = (int)n;
    frac = n - (float)idx;
    base = SEMI[idx] * (1.0f + frac * (0.0577623f + frac * 0.0016682f));
    while (oct > 0) { base *= 2.0f; oct--; }
    while (oct < 0) { base *= 0.5f; oct++; }
    return 440.0f * base;
}

static float tri_wave(float p)
{
    float d = p - 0.5f;
    if (d < 0.0f) d = -d;
    return 4.0f * d - 1.0f;
}

static float fabs_f(float x)
{
    return x < 0.0f ? -x : x;
}

static int16_t clamp_i16(float x)
{
    if (x >= 0.999f) return 32734;
    if (x <= -0.999f) return -32734;
    return (int16_t)(x * 32768.0f);
}

/* SPEC.md 8.3's eight shapes, phase in [0,1). This is PICO-8's synthesis
 * arithmetic, taken from zepto8/fake-08's reverse engineering (their
 * synth.cpp, non-"buzz" variants), because a ported cart's music is composed
 * against exactly these -- including the deliberately UNEQUAL loudness per
 * instrument (the square family peaks at 0.25, the triangle family at 0.5;
 * play them equal and every square lead shouts down its own accompaniment).
 * Noise is the one stateful shape and lives in voice_sample, where the
 * note's frequency is known. */
/* A tilted saw with its corner at `a` -- the shape itself, before the 0.5.
 * The triangle's buzz variant averages one in, so it is worth a name. */
static float tilted(float p, float a)
{
    return p < a ? 2.0f * p / a - 1.0f
                 : 2.0f * (1.0f - p) / (1.0f - a) - 1.0f;
}

/* `buzz` and `noiz` are two of SPEC.md 8.1's five per-sfx filters. They do not
 * post-process the wave, they SELECT A DIFFERENT ONE -- every instrument has a
 * harsher twin, and the header of this file used to say it implemented "the
 * non-buzz variants", which was true and was silently half the timbre of any
 * cart written since PICO-8 0.2.4.
 *
 * `p2` is the partner phase (the phaser's 109/110 beat, or the detuned
 * second oscillator's), and `cyc` says which of two cycles the saw's buzz dip
 * is in -- that one shape loops on a 2x period, so a wrapped phase alone
 * cannot place it. */
static float wave_sample(moy_voice *v, int wave, float p, float p2,
                         int buzz, int noiz, uint8_t cyc)
{
    float w;
    switch (wave) {
    case 0:                                                  /* square */
        return p < (buzz ? 0.4f : 0.5f) ? 0.25f : -0.25f;
    case 1:                                                  /* triangle */
        w = 1.0f - fabs_f(4.0f * p - 2.0f);
        if (buzz) w = w * 0.75f + tilted(p, 0.875f) * 0.25f;
        return w * 0.5f;
    case 2:                                                  /* saw */
        w = p < 0.5f ? p : p - 1.0f;
        if (buzz) {
            /* the dip rides a 2x period: `|fmod(phase, 2) - 1| < 0.5` */
            int dip = cyc ? (p < 0.5f) : (p > 0.5f);
            w = w * 0.83f - (dip ? 0.085f : 0.0f);
        }
        return 0.653f * w;
    case 3:                                                  /* noise (held) */
        w = v->nto;
        if (noiz) w *= 2.0f * (p < 0.5f ? p : p - 1.0f);
        return w;
    case 4:                                                  /* pulse */
        return p < (buzz ? 0.255f : (1.0f / 3.0f)) ? 0.25f : -0.25f;
    case 5:                                                  /* organ */
        w = p < 0.5f ? 3.0f - fabs_f(24.0f * p - 6.0f)
                     : 1.0f - fabs_f(16.0f * p - 12.0f);
        if (buzz) {
            w = p < 0.5f ? w * 2.0f + 3.0f : w;
            w = (p < 0.5f && w > -1.875f) ? w * 0.2f - 1.0f : w + 0.5f;
        }
        return w / 9.0f;
    case 6:                                                  /* tilted saw */
        return tilted(p, buzz ? 0.975f : 0.875f) * 0.5f;
    default:                                                 /* phaser */
        w = 2.0f - fabs_f(8.0f * p - 4.0f)
            + 1.0f - fabs_f(4.0f * p2 - 2.0f);
        if (buzz) {
            /* the original triangle carries harmonics 1,3,5,7...; buzz adds
             * the even ones at 2,6,10 and 4,12,20. Multiplying a WRAPPED
             * phase by an integer is exact -- the integer part it dropped
             * would have contributed an integer here too. */
            float q2 = p * 2.0f + 0.5f, q4 = p * 4.0f;
            q2 -= (float)(int)q2;
            q4 -= (float)(int)q4;
            w += 0.25f - fabs_f(1.0f * q2 - 0.5f);
            w += 0.125f - fabs_f(0.5f * q4 - 0.25f);
        }
        return w / 6.0f;
    }
}

/* The detuned partner's frequency ratio. PICO-8 picks it per instrument:
 * a triangle gets a real interval (a fourth or a fifth), everything else a
 * near-unison beat or a plain octave. Instrument numbers here are MOY's
 * (0 square, 1 triangle, ... 7 phaser), not PICO-8's. */
/* Detune skips NOISE: it has no phase to offset, and PICO-8 skips it too. */
static int n_wave_is_pitched(const moy_sfx_def *s, int step)
{
    return s->steps[step].wave != 3;
}

static float detune_factor(int wave, int d)
{
    if (wave == 1) return d == 1 ? 3.0f / 4.0f : 3.0f / 2.0f;
    if (wave == 5) return d == 1 ? 200.0f / 199.0f : 800.0f / 199.0f;
    if (wave == 7) return d == 1 ? 49.0f / 50.0f : 400.0f / 199.0f;
    return d == 1 ? 200.0f / 199.0f : 400.0f / 199.0f;
}

/* sin/cos for the shelf coefficients, computed ONCE per init and never in the
 * sample loop. This file links no libm on purpose -- pitch_hz is a table for
 * the same reason -- and two filter setups do not justify pulling it in. The
 * argument is w0 = 2*pi*freq/rate, clamped below Nyquist, so a Taylor series
 * to the 11th order is exact to well past single precision here. */
static float sin_f(float x)
{
    float x2 = x * x;
    return x * (1.0f + x2 * (-1.0f / 6.0f + x2 * (1.0f / 120.0f
             + x2 * (-1.0f / 5040.0f + x2 * (1.0f / 362880.0f
             + x2 * (-1.0f / 39916800.0f))))));
}

static float cos_f(float x)
{
    float x2 = x * x;
    return 1.0f + x2 * (-0.5f + x2 * (1.0f / 24.0f + x2 * (-1.0f / 720.0f
             + x2 * (1.0f / 40320.0f + x2 * (-1.0f / 3628800.0f)))));
}

/* W3C audio-EQ-cookbook high shelf, with q FIXED at 1 -- which is the only q
 * PICO-8's dampen uses, and it collapses the cookbook's alpha to
 * sin(w0)/sqrt(2). `A` and `sqrtA` come in as constants for the same reason:
 * the two gains are -6 dB and -12 dB and never vary, so there is no pow() or
 * sqrt() to do. (Pleasingly, sqrt(A) for -12 dB IS A for -6 dB.) */
static void biquad_highshelf(moy_biquad *b, float freq, float A, float sqrtA,
                             float rate)
{
    float w0 = 6.283185307179586f * freq / rate;
    float cw, alpha, sq, a0, a1, a2, b0, b1, b2;
    if (w0 > 2.5f) w0 = 2.5f;           /* freq at/over Nyquist: clamp, do
                                         * not let the series run wild */
    cw = cos_f(w0);
    alpha = sin_f(w0) * 0.70710678f;
    sq = 2.0f * sqrtA * alpha;
    a0 = (A + 1.0f) - (A - 1.0f) * cw + sq;
    a1 = 2.0f * ((A - 1.0f) - (A + 1.0f) * cw);
    a2 = (A + 1.0f) - (A - 1.0f) * cw - sq;
    b0 = A * ((A + 1.0f) + (A - 1.0f) * cw + sq);
    b1 = -2.0f * A * ((A - 1.0f) + (A + 1.0f) * cw);
    b2 = A * ((A + 1.0f) + (A - 1.0f) * cw - sq);
    b->c1 = b0 / a0; b->c2 = b1 / a0; b->c3 = b2 / a0;
    b->c4 = a1 / a0; b->c5 = a2 / a0;
    b->li = b->lli = b->lo = b->llo = 0.0f;
}

static float biquad_run(moy_biquad *b, float x)
{
    float y = b->c1 * x + b->c2 * b->li + b->c3 * b->lli
            - b->c4 * b->lo - b->c5 * b->llo;
    b->lli = b->li; b->li = x;
    b->llo = b->lo; b->lo = y;
    return y;
}

/* PICO-8's `reverb` and `dampen`, per CHANNEL and AFTER the note's volume --
 * they are the only two of the five that are post-processing rather than a
 * different oscillator.
 *
 * Reverb is a plain delay line fed back at half, 16.6 ms or 33.2 ms. Both
 * rings are written every sample whichever setting is on, so switching
 * between them mid-note does not start from silence -- that is PICO-8's own
 * behaviour and it is audible on a cart that alternates.
 *
 * Both shelves RUN every sample even when off, so their state is warm the
 * instant one turns on; only the mix is gated. A biquad started cold on a
 * signal already in flight thumps. */
static float voice_post(moy_voice *v, float dry, uint8_t filters)
{
    int rb = MOY_A_F_REVERB(filters), dp = MOY_A_F_DAMPEN(filters);
    float wet = dry, d1, d2;
    if (rb == 1) wet += (float)v->rv1[v->rvi % v->rvn1] * (0.5f / 32768.0f);
    else if (rb == 2) wet += (float)v->rv2[v->rvi % v->rvn2] * (0.5f / 32768.0f);
    v->rv1[v->rvi % v->rvn1] = clamp_i16(wet);
    v->rv2[v->rvi % v->rvn2] = clamp_i16(wet);
    v->rvi++;
    if (v->rvi >= v->rvn1 * v->rvn2) v->rvi = 0;   /* both moduli, no drift */
    d1 = biquad_run(&v->damp1, wet);
    if (dp == 1) wet = d1;
    d2 = biquad_run(&v->damp2, wet);
    if (dp == 2) wet = d2;
    return wet;
}

static float lcg_unit(uint32_t *rng)
{
    *rng = *rng * 1664525u + 1013904223u;
    return (float)(*rng >> 16 & 0x7FFF) / 16384.0f - 1.0f;
}

static void voice_start(moy_voice *v, const moy_sfx_def *s, uint8_t owner)
{
    /* prev_pitch/prev_vol deliberately survive: SPEC.md 8.1 says a slide
     * carries across a row boundary, so the glide's origin is whatever this
     * CHANNEL last played, not the new sfx's first step. The amplitude slew
     * restarts from 0 -- a retrigger resets the oscillator phase, and the
     * short ramp is what keeps that from clicking. */
    v->owner = owner;
    v->s = s;
    v->step = 0;
    v->samp = 0;
    v->phase = v->phase2 = 0.0f;
    v->dphase = v->dphase2 = 0.0f;
    v->cyc = 0;
    v->amp = 0.0f;
    /* The noise filter state (nfrom/nto) deliberately survives, like the
     * slide origin: PICO-8 keeps its per-channel noise walk running. */
    if (!v->rng) v->rng = 0x2F9E2B1u;
}

static void voice_stop(moy_voice *v)
{
    v->owner = 0;
    v->s = NULL;
}

/* The current step's frequency and amplitude at time t into the step --
 * i.e. SPEC.md 8.1's effects table, evaluated. Time is counted in integer
 * samples and converted by one multiply, so a step boundary lands within a
 * sample of where the speed says it should.
 *
 * The last stage is a ~1.5 ms amplitude slew toward the note's level. It is
 * not in the spec and needs to not be: it is de-clicking, the same smoothing
 * PICO-8 applies -- a retriggered oscillator restarts at phase 0, and
 * without the ramp every fast sfx chain carries a click per step. */
static float voice_sample(moy_voice *v, float dt, float rate)
{
    const moy_sfx_def *s = v->s;
    const moy_note *n;
    float step_dur, t, u, pitch, vol, g, w, slew;
    float slide_from = -1.0f;           /* origin frequency when eff 1 */
    int idx = v->step, pitch_i;
    int buzz, noiz, detune;
    float dt2;                          /* the detune partner's freq ratio */

    if (!s || !s->nsteps) return 0.0f;
    buzz = MOY_A_F_BUZZ(s->filters);
    noiz = MOY_A_F_NOIZ(s->filters);
    detune = MOY_A_F_DETUNE(s->filters);
    dt2 = (detune && n_wave_is_pitched(s, v->step))
        ? detune_factor(s->steps[v->step].wave, detune) : 0.0f;
    step_dur = 1.0f / s->speed;
    t = (float)v->samp * dt;
    n = &s->steps[idx];
    pitch_i = n->pitch;
    pitch = (float)pitch_i;
    vol = (float)n->vol;

    /* Arpeggio cycles the step's group of four -- the PITCH only; volume and
     * wave stay the step's own. 30/15 notes/s, doubled on a fast sfx (15+
     * steps/s; PICO-8 doubles at speed <= 8 ticks/note, same thing). */
    if (n->eff == 6 || n->eff == 7) {
        float nps = (n->eff == 6 ? 30.0f : 15.0f)
                  * (s->speed >= 15.0f ? 2.0f : 1.0f);
        int k = (idx / 4) * 4 + (int)(t * nps) % 4;
        if (k < s->nsteps) {
            pitch_i = s->steps[k].pitch;
            pitch = (float)pitch_i;
        }
    }

    u = t / step_dur;                   /* 0..1 through the step */
    if (u > 1.0f) u = 1.0f;

    switch (n->eff) {
    case 1:                             /* slide from the channel's previous
                                         * note; with none yet, from itself.
                                         * The glide is linear in FREQUENCY,
                                         * not semitones (PICO-8/zepto8) --
                                         * on a wide slide the curves differ
                                         * audibly. */
        if (v->prev_pitch >= 0.0f) {
            slide_from = pitch_hz(v->prev_pitch);
            vol = v->prev_vol + (vol - v->prev_vol) * u;
        }
        break;
    case 2:                             /* vibrato: +-0.25 semitone, 7.5 Hz */
        {
            float ph = t * 7.5f;
            ph -= (float)(int)ph;
            pitch += 0.25f * tri_wave(ph);
        }
        break;
    case 4: vol *= u; break;            /* fade in */
    case 5: vol *= 1.0f - u; break;     /* fade out */
    default: break;
    }

    g = (pitch_i < 0 || vol <= 0.0f) ? 0.0f : vol / 7.0f;
    if (g > 0.0f) {
        float freq = pitch_hz(pitch);
        if (slide_from > 0.0f) freq = slide_from + (freq - slide_from) * u;
        if (n->eff == 3) freq *= 1.0f - u;      /* drop: falls linearly to 0 */
        v->phase += freq * dt;
        if (v->phase >= 1.0f) v->cyc ^= 1;      /* the saw buzz's 2x period */
        v->phase -= (float)(int)v->phase;
        if (n->wave == 7) {                     /* the detuned partner: PICO-8
                                                 * beats at ~109/110, not the
                                                 * folkloric 127/128 */
            v->phase2 += freq * (109.0f / 110.0f) * dt;
            v->phase2 -= (float)(int)v->phase2;
        }
        if (dt2 > 0.0f) {                       /* the `detune` filter's own
                                                 * oscillator, at freq*factor */
            v->dphase += freq * dt2 * dt;
            v->dphase -= (float)(int)v->dphase;
            v->dphase2 += freq * dt2 * (109.0f / 110.0f) * dt;
            v->dphase2 -= (float)(int)v->dphase2;
        }
        if (n->wave == 3) {
            /* PICO-8's noise: an LCG random walk through a one-pole low-pass
             * whose cutoff tracks the note (zepto8's constant), then a bass
             * lift -- low keys up to 3x. nfrom is the filter state, nto the
             * shaped output wave_sample holds between updates. */
            float scale = freq * dt * 8.858923f;
            float p8key = pitch - 24.0f;        /* moy 57=A4 <-> p8 33=A4 */
            float factor;
            if (p8key < 0.0f) p8key = 0.0f;
            if (p8key > 63.0f) p8key = 63.0f;
            factor = 1.0f - p8key / 63.0f;
            v->nfrom = (v->nfrom + scale * lcg_unit(&v->rng)) / (1.0f + scale);
            v->nto = v->nfrom * 1.5f * (1.0f + factor * factor);
        }
    }
    /* On a rest the phase holds and the slew below rides the held level to
     * zero -- that IS the release de-click. */
    w = wave_sample(v, n->wave, v->phase, v->phase2, buzz, noiz, v->cyc);
    if (dt2 > 0.0f) {
        /* `detune` is a SECOND oscillator mixed in at half level, not a pitch
         * offset -- the note keeps its own pitch and gains a partner. Noise is
         * exempt: it has no phase to detune. Organ's second voice at detune 2
         * simplifies to a triangle, which is PICO-8's own shortcut. */
        int dw = (n->wave == 5 && detune == 2) ? 1 : n->wave;
        w += wave_sample(v, dw, v->dphase, v->dphase2, buzz, noiz, v->cyc)
           * 0.5f;
    }

    slew = dt / 0.0015f;
    if (v->amp < g) { v->amp += slew; if (v->amp > g) v->amp = g; }
    else            { v->amp -= slew; if (v->amp < g) v->amp = g; }

    /* advance the sequencer; any KEYED step -- volume 0 included -- becomes
     * the channel's previous note. In PICO-8 every tracker slot has a key,
     * so a rest is still a slide origin; only moy's pitch -1 records
     * nothing. */
    v->samp++;
    if ((float)v->samp >= step_dur * rate) {
        const moy_note *fin = &s->steps[v->step];
        if (fin->pitch >= 0) {
            v->prev_pitch = (float)fin->pitch;
            v->prev_vol = (float)fin->vol;
        }
        v->samp = 0;
        v->step++;
        if (v->step >= s->nsteps) {
            if (s->loop) v->step = s->loop_start;
            else voice_stop(v);
        }
    }
    return w * v->amp;
}

/* -------------------------------------------------------------- verbs --- */

void moy_audio_init(moy_audio *a, const moy_bank *bank, int sample_rate)
{
    int i;
    memset(a, 0, sizeof *a);
    a->bank = bank;
    a->rate = sample_rate > 0 ? sample_rate : 44100;
    a->master = 7;
    for (i = 0; i < MOY_A_CHANNELS; i++) {
        moy_voice *v = &a->v[i];
        v->prev_pitch = -1.0f;          /* no previous note yet (slide) */
        /* The delay lines are quoted in samples at 22050 -- PICO-8's rate --
         * so at any other rate they are that many SECONDS, re-counted. */
        v->rvn1 = (MOY_A_REVERB1 * a->rate + 11025) / 22050;
        v->rvn2 = (MOY_A_REVERB2 * a->rate + 11025) / 22050;
        if (v->rvn1 < 1) v->rvn1 = 1;
        if (v->rvn2 < 1) v->rvn2 = 1;
        if (v->rvn1 > MOY_A_REVERB1) v->rvn1 = MOY_A_REVERB1;
        if (v->rvn2 > MOY_A_REVERB2) v->rvn2 = MOY_A_REVERB2;
        biquad_highshelf(&v->damp1, 2400.0f, 0.70794578f, 0.84139514f,
                         (float)a->rate);
        biquad_highshelf(&v->damp2, 1000.0f, 0.50118723f, 0.70794578f,
                         (float)a->rate);
    }
}

/* Row channel j plays on voice 3 - j (SPEC.md 8.1). */
static void music_row_start(moy_audio *a)
{
    const moy_music_def *m = a->track;
    int j;
    for (j = 0; j < m->width; j++) {
        moy_voice *v = &a->v[MOY_A_CHANNELS - 1 - j];
        int id = m->rows[a->mrow][j];
        if (id < 0 || id >= a->bank->nsfx) {
            voice_stop(v);
        } else {
            voice_start(v, &a->bank->sfx[id], 2);
        }
    }
}

void moy_audio_sfx(moy_audio *a, int n, int chan)
{
    int free_top, i;
    if (!a->bank || n < 0 || n >= a->bank->nsfx) return;
    if (chan >= 0 && chan < MOY_A_CHANNELS) {
        voice_start(&a->v[chan], &a->bank->sfx[n], 1);
        return;
    }
    /* Round-robin what music leaves free: a playing track of width W owns
     * voices 3 .. 4-W, so the pool is 0 .. 3-W. When a 4-channel track owns
     * every voice, steal voice 0 -- the track's last, typically least
     * melodic, channel -- rather than dropping the effect (the reference
     * console does the same). */
    free_top = MOY_A_CHANNELS - (a->track ? a->track->width : 0);
    if (free_top <= 0) {
        voice_start(&a->v[0], &a->bank->sfx[n], 1);
        return;
    }
    /* Prefer an idle voice, scanned in order; otherwise steal the cursor's. */
    for (i = 0; i < free_top; i++) {
        if (!a->v[i].owner) {
            voice_start(&a->v[i], &a->bank->sfx[n], 1);
            return;
        }
    }
    voice_start(&a->v[a->rr % free_top], &a->bank->sfx[n], 1);
    a->rr = (a->rr % free_top + 1) % free_top;
}

void moy_audio_beep(moy_audio *a, float freq_hz, float dur_s)
{
    if (freq_hz <= 0.0f || dur_s <= 0.0f) return;
    a->bfreq = freq_hz;
    a->bleft = dur_s;
    a->bphase = 0.0f;
}

void moy_audio_music(moy_audio *a, int track, int loop)
{
    int i;
    if (!a->bank || track < 0 || track >= a->bank->nmusic) return;
    /* A new track releases the old one's voices before claiming its own. */
    for (i = 0; i < MOY_A_CHANNELS; i++)
        if (a->v[i].owner == 2) voice_stop(&a->v[i]);
    a->track = &a->bank->music[track];
    a->mrow = 0;
    a->msamp = 0;
    a->mloop = loop;
    if (!a->track->nrows) { a->track = NULL; return; }
    music_row_start(a);
}

void moy_audio_music_stop(moy_audio *a)
{
    int i;
    a->track = NULL;
    for (i = 0; i < MOY_A_CHANNELS; i++)
        if (a->v[i].owner == 2) voice_stop(&a->v[i]);
}

void moy_audio_sound_stop(moy_audio *a, int chan)
{
    int i;
    if (chan >= 0) {
        if (chan < MOY_A_CHANNELS) voice_stop(&a->v[chan]);
        return;
    }
    for (i = 0; i < MOY_A_CHANNELS; i++) voice_stop(&a->v[i]);
    a->bleft = 0.0f;
    a->track = NULL;
}

void moy_audio_volume(moy_audio *a, int level)
{
    a->master = level < 0 ? 0 : (level > 7 ? 7 : level);
}

/* ------------------------------------------------------------- render --- */

void moy_audio_render(moy_audio *a, int16_t *out, int nframes)
{
    float dt, rate, row_dur, master;
    int i, f;

    if (!a->rate) {                     /* uninitialised: silence, not UB */
        memset(out, 0, (size_t)nframes * sizeof *out);
        return;
    }
    rate = (float)a->rate;
    dt = 1.0f / rate;
    master = (float)a->master / 7.0f;

    for (f = 0; f < nframes; f++) {
        float mix = 0.0f;
        int s;

        /* the music clock */
        if (a->track) {
            const moy_music_def *m = a->track;
            row_dur = m->has_row_secs ? m->row_secs[a->mrow]
                                      : 1.0f / m->speed;
            if (row_dur > 0.0f) {       /* 0 holds this row forever (8.1) */
                a->msamp++;
                if ((float)a->msamp >= row_dur * rate) {
                    a->msamp = 0;
                    a->mrow++;
                    if (a->mrow >= m->nrows) {
                        if (a->mloop) { a->mrow = 0; music_row_start(a); }
                        else moy_audio_music_stop(a);
                    } else {
                        music_row_start(a);
                    }
                }
            }
        }

        for (i = 0; i < MOY_A_CHANNELS; i++) {
            moy_voice *v = &a->v[i];
            float dry = v->owner ? voice_sample(v, dt, rate) : 0.0f;
            uint8_t fx = (v->owner && v->s) ? v->s->filters : 0;
            if (MOY_A_F_REVERB(fx) || MOY_A_F_DAMPEN(fx)) {
                v->fxlast = fx;
                /* EIGHT passes of the long ring, not three: the line feeds
                 * back at half, so three passes still leaves 12% -- plainly
                 * audible, and cutting it there chopped the tail off mid-
                 * decay. Eight lands at -48 dB, under the 16-bit floor. */
                v->fxtail = v->rvn2 * 8;
            } else if (v->fxtail > 0) {
                v->fxtail--;
            }
            /* Costs nothing for the carts that use none of this: `fxtail` is
             * only ever nonzero after a filtered sfx has actually played. */
            mix += v->fxtail > 0 ? voice_post(v, dry, v->fxlast) : dry;
        }

        if (a->bleft > 0.0f) {          /* beep: square at vol 6 (8.2) */
            float bg = 6.0f / 7.0f;
            if (a->bleft < 0.0015f)     /* ...with its own release de-click */
                bg *= a->bleft / 0.0015f;
            a->bphase += a->bfreq * dt;
            a->bphase -= (float)(int)a->bphase;
            mix += (a->bphase < 0.5f ? 0.25f : -0.25f) * bg;
            a->bleft -= dt;
        }

        /* Sum, scale, saturate. The instruments themselves peak at 0.25-0.5
         * (PICO-8's mix, above), so 0.5 here is the headroom for four
         * simultaneous voices. */
        s = (int)(mix * master * 0.5f * 32767.0f);
        if (s > 32767) s = 32767;
        if (s < -32768) s = -32768;
        out[f] = (int16_t)s;
    }
}
