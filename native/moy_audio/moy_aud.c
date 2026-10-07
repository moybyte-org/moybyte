// moy_aud: sessions, the mix and the sample voice. moy_aud.h has the contract.
//
// Who touches what: the VM's thread makes every session call; the feeder task
// (moy_aud_out.c) calls moy_aud_render; a compiled cart's thread writes the
// stream (moy_audio_snd.h). The table's rows are reserved at init, so a row
// never moves; only the VM adds or releases one, so the VM reads rows without
// the lock. What the feeder reads -- the focused row's synth and sample voices,
// the console level, the stream -- changes only under the lock.

#include <stdlib.h>
#include <string.h>

#include "moy_aud.h"
#include "moy_audio.h"
#include "moy_audio_snd.h"
#include "moy_htab.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#endif

typedef struct {
    const int16_t *pcm;         // a clip's frames, or NULL: idle
    uint32_t frames, pos;
    uint32_t clip;              // the clip's handle, for free to find it
} aud_voice_t;

typedef struct {
    uint32_t owner;
    int level;                  // the session's own, 0..7
    moy_bank *bank;
    moy_audio *a;
    aud_voice_t sv[MOY_AUD_SAMPLE_VOICES];
} aud_sess_t;

typedef struct {
    int16_t *pcm;
    uint32_t frames;
} aud_clip_t;

typedef struct {
    moy_htab_t *sess;
    moy_htab_t *clips;
    aud_sess_t *focus;
    uint32_t focus_h;
    int rate;
    int console;                // 0..7
    int hush;                   // the next render clears the stream first
    // a compiled cart's stream
    moy_stream pcm;
    int16_t *pcm_ring;
    int pcm_on;
    // counters
    moy_aud_stats_t st;
    uint64_t trig_at;           // a verb waiting for its first mixed chunk, us
    // the trace
    int32_t *trace;
    uint32_t trace_n;
    int trace_on;
} aud_t;

static aud_t A = {.rate = 22050, .console = 7};

// -- memory: PSRAM on a board, the C heap elsewhere ----------------------------

static void *aud_alloc(size_t n) {
#ifdef ESP_PLATFORM
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL) {
        p = heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
    }
    return p;
#else
    return calloc(1, n);
#endif
}

static void aud_release(void *p, size_t n) {
    (void)n;
#ifdef ESP_PLATFORM
    heap_caps_free(p);
#else
    free(p);
#endif
}

static const moy_htab_mem_t aud_mem = {aud_alloc, aud_release};

static int aud_init(void) {
    if (A.sess != NULL) {
        return MOY_AUD_OK;
    }
    moy_htab_t *s = moy_htab_new(&aud_mem, MOY_KIND_AUDIO, MOY_AUD_SESSIONS,
                                 sizeof(aud_sess_t));
    moy_htab_t *c = moy_htab_new(&aud_mem, MOY_KIND_CLIP, MOY_AUD_CLIPS,
                                 sizeof(aud_clip_t));
    if (s == NULL || c == NULL || moy_htab_reserve(s) != MOY_HTAB_OK
        || moy_htab_reserve(c) != MOY_HTAB_OK) {
        moy_htab_free(s);
        moy_htab_free(c);
        return MOY_AUD_NOMEM;
    }
    A.sess = s;
    A.clips = c;
    return MOY_AUD_OK;
}

static aud_sess_t *sess_of(uint32_t h) {
    void *row;
    if (A.sess == NULL || moy_htab_get(A.sess, h, &row) != MOY_HTAB_OK) {
        return NULL;
    }
    return (aud_sess_t *)row;
}

static aud_clip_t *clip_of(uint32_t h) {
    void *row;
    if (A.clips == NULL || moy_htab_get(A.clips, h, &row) != MOY_HTAB_OK) {
        return NULL;
    }
    return (aud_clip_t *)row;
}

static void trace(uint32_t h, int verb, int32_t a, int32_t b) {
    if (!A.trace_on || A.trace == NULL || A.trace_n >= MOY_AUD_TRACE_ROWS) {
        return;
    }
    int32_t *r = A.trace + 4u * A.trace_n++;
    r[0] = h ? (int32_t)(h & 0xffu) : -1;
    r[1] = verb;
    r[2] = a;
    r[3] = b;
}

static int effective(const aud_sess_t *s) {
    return s->level < A.console ? s->level : A.console;
}

// The lock, timed: the longest any caller held it is the bound on how long the
// feeder could have waited for it.
static uint64_t lock_timed(void) {
    moy_aud_lock();
    return moy_aud_now_us();
}

static void unlock_timed(uint64_t t0) {
    uint64_t held = moy_aud_now_us() - t0;
    if (held > A.st.lock_hold_us_max) {
        A.st.lock_hold_us_max = (uint32_t)held;
    }
    moy_aud_unlock();
}

// A bank parsed from `json` into fresh memory, outside the lock. *rc is OK or
// BANK (a refused text leaves a valid, silent bank).
static moy_bank *bank_new(const char *json, size_t n, int *rc) {
    moy_bank *b = aud_alloc(sizeof(moy_bank));
    if (b == NULL) {
        *rc = MOY_AUD_NOMEM;
        return NULL;
    }
    char *text = NULL;
    if (json != NULL && n > 0) {
        text = aud_alloc(n + 1);
        if (text == NULL) {
            aud_release(b, sizeof(moy_bank));
            *rc = MOY_AUD_NOMEM;
            return NULL;
        }
        memcpy(text, json, n);
    }
    uint64_t t0 = moy_aud_now_us();
    *rc = moy_bank_parse(b, text) ? MOY_AUD_BANK : MOY_AUD_OK;
    uint64_t took = moy_aud_now_us() - t0;
    if (took > A.st.bank_parse_us_max) {
        A.st.bank_parse_us_max = (uint32_t)took;
    }
    if (text != NULL) {
        aud_release(text, n + 1);
    }
    return b;
}

// -- sessions ---------------------------------------------------------------------

int moy_aud_open(uint32_t *s, uint32_t owner, const char *bank_json, size_t n) {
    if (owner == 0u) {
        return MOY_AUD_BAD;
    }
    int rc = aud_init();
    if (rc != MOY_AUD_OK) {
        return rc;
    }
    for (uint32_t i = 0; i < moy_htab_slots(A.sess); i++) {
        uint32_t h = moy_htab_at(A.sess, i);
        if (h && ((aud_sess_t *)moy_htab_row(A.sess, i))->owner == owner) {
            moy_aud_close(h);
        }
    }
    if (moy_htab_full(A.sess)) {
        return MOY_AUD_FULL;
    }
    moy_audio *a = aud_alloc(sizeof(moy_audio));
    moy_bank *b = a ? bank_new(bank_json, n, &rc) : NULL;
    if (a == NULL || b == NULL) {
        if (a != NULL) {
            aud_release(a, sizeof(moy_audio));
        }
        return MOY_AUD_NOMEM;
    }
    uint32_t h;
    void *row;
    if (moy_htab_add(A.sess, &h, &row) != MOY_HTAB_OK) {
        aud_release(a, sizeof(moy_audio));
        aud_release(b, sizeof(moy_bank));
        return MOY_AUD_NOMEM;
    }
    aud_sess_t *r = row;
    r->owner = owner;
    r->level = 7;
    r->bank = b;
    r->a = a;
    moy_audio_init(a, b, A.rate);
    a->master = effective(r);
    *s = h;
    trace(h, MOY_AUD_T_OPEN, (int32_t)owner, rc);
    return rc;
}

int moy_aud_bank(uint32_t s, const char *bank_json, size_t n) {
    aud_sess_t *r = sess_of(s);
    if (r == NULL) {
        return MOY_AUD_STALE;
    }
    int rc;
    moy_bank *b = bank_new(bank_json, n, &rc);
    if (b == NULL) {
        return rc;
    }
    uint64_t t0 = lock_timed();
    moy_bank *old = r->bank;
    r->bank = b;
    moy_audio_init(r->a, b, A.rate);
    r->a->master = effective(r);
    unlock_timed(t0);
    aud_release(old, sizeof(moy_bank));
    trace(s, MOY_AUD_T_BANK, rc, 0);
    return rc;
}

int moy_aud_focus(uint32_t s) {
    aud_sess_t *r = NULL;
    if (s != 0u) {
        r = sess_of(s);
        if (r == NULL) {
            return MOY_AUD_STALE;
        }
    }
    trace(s, MOY_AUD_T_FOCUS, 0, 0);
    if (r != NULL) {
        moy_aud_out_start();        // the lazy start: the first audible session
    }
    uint64_t t0 = lock_timed();
    A.focus = r;
    A.focus_h = s;
    unlock_timed(t0);
    return MOY_AUD_OK;
}

uint32_t moy_aud_focused(void) {
    return A.focus_h;
}

int moy_aud_close(uint32_t s) {
    aud_sess_t *r = sess_of(s);
    if (r == NULL) {
        return MOY_AUD_STALE;
    }
    trace(s, MOY_AUD_T_CLOSE, 0, 0);
    uint64_t t0 = lock_timed();
    if (A.focus == r) {
        A.focus = NULL;
        A.focus_h = 0u;
    }
    unlock_timed(t0);
    aud_release(r->a, sizeof(moy_audio));
    aud_release(r->bank, sizeof(moy_bank));
    moy_htab_release(A.sess, s);
    return MOY_AUD_OK;
}

// One verb on a live session, under the lock.
#define VERB(s, r)                                  \
    aud_sess_t *r = sess_of(s);                     \
    if (r == NULL) {                                \
        return MOY_AUD_STALE;                       \
    }                                               \
    A.st.verbs++;                                   \
    uint64_t t0_ = lock_timed()

#define VERB_END() unlock_timed(t0_); return MOY_AUD_OK

static void trig_mark(const aud_sess_t *r) {
    if (r == A.focus && A.trig_at == 0u) {
        A.trig_at = moy_aud_now_us();
    }
}

int moy_aud_sfx(uint32_t s, int n, int chan) {
    trace(s, MOY_AUD_T_SFX, n, chan);
    VERB(s, r);
    moy_audio_sfx(r->a, n, chan);
    trig_mark(r);
    VERB_END();
}

int moy_aud_beep(uint32_t s, float freq_hz, float dur_s) {
    trace(s, MOY_AUD_T_BEEP, (int32_t)(freq_hz * 1000.0f), (int32_t)(dur_s * 1000.0f));
    VERB(s, r);
    moy_audio_beep(r->a, freq_hz, dur_s);
    VERB_END();
}

int moy_aud_music(uint32_t s, int track, int loop) {
    trace(s, MOY_AUD_T_MUSIC, track, loop);
    VERB(s, r);
    moy_audio_music(r->a, track, loop);
    trig_mark(r);
    VERB_END();
}

int moy_aud_music_stop(uint32_t s) {
    trace(s, MOY_AUD_T_MUSIC_STOP, 0, 0);
    VERB(s, r);
    moy_audio_music_stop(r->a);
    VERB_END();
}

int moy_aud_stop(uint32_t s, int chan) {
    trace(s, MOY_AUD_T_STOP, chan, 0);
    VERB(s, r);
    moy_audio_sound_stop(r->a, chan);
    for (int i = 0; i < MOY_AUD_SAMPLE_VOICES; i++) {
        if (chan < 0 || chan == i) {
            r->sv[i].pcm = NULL;
        }
    }
    VERB_END();
}

int moy_aud_level(uint32_t s, int level) {
    trace(s, MOY_AUD_T_LEVEL, level, 0);
    VERB(s, r);
    r->level = level < 0 ? 0 : (level > 7 ? 7 : level);
    r->a->master = effective(r);
    VERB_END();
}

int moy_aud_active(uint32_t s, uint32_t *mask) {
    aud_sess_t *r;
    if (s == 0u) {
        r = A.focus;
    } else {
        r = sess_of(s);
        if (r == NULL) {
            return MOY_AUD_STALE;
        }
    }
    uint32_t m = 0;
    moy_aud_lock();
    if (r != NULL) {
        for (int i = 0; i < MOY_A_CHANNELS; i++) {
            if (r->a->v[i].owner) {
                m |= 1u << i;
            }
        }
        if (r->a->track != NULL) {
            m |= 1u << 4;
        }
        if (r->a->bleft > 0) {
            m |= 1u << 5;
        }
        for (int i = 0; i < MOY_AUD_SAMPLE_VOICES; i++) {
            if (r->sv[i].pcm != NULL) {
                m |= 1u << 7;
            }
        }
    }
    if (A.pcm_on && A.pcm.count > 0) {
        m |= 1u << 6;
    }
    moy_aud_unlock();
    *mask = m;
    return MOY_AUD_OK;
}

void moy_aud_volume(int level) {
    trace(0, MOY_AUD_T_VOLUME, level, 0);
    level = level < 0 ? 0 : (level > 7 ? 7 : level);
    uint64_t t0 = lock_timed();
    A.console = level;
    if (A.sess != NULL) {
        for (uint32_t i = 0; i < moy_htab_slots(A.sess); i++) {
            if (moy_htab_live(A.sess, i)) {
                aud_sess_t *r = moy_htab_row(A.sess, i);
                r->a->master = effective(r);
            }
        }
    }
    unlock_timed(t0);
}

int moy_aud_console_level(void) {
    return A.console;
}

void moy_aud_hush(void) {
    trace(0, MOY_AUD_T_HUSH, 0, 0);
    uint64_t t0 = lock_timed();
    if (A.sess != NULL) {
        for (uint32_t i = 0; i < moy_htab_slots(A.sess); i++) {
            if (moy_htab_live(A.sess, i)) {
                aud_sess_t *r = moy_htab_row(A.sess, i);
                moy_audio_sound_stop(r->a, -1);
                for (int v = 0; v < MOY_AUD_SAMPLE_VOICES; v++) {
                    r->sv[v].pcm = NULL;
                }
            }
        }
    }
    if (A.pcm_on) {
        moy_stream_clear(&A.pcm);
    }
    A.trig_at = 0u;
    A.st.hush_at = A.st.rendered;
    unlock_timed(t0);
}

// -- the sample voice ---------------------------------------------------------------

int moy_aud_sample_load(uint32_t *h, const int16_t *pcm, size_t frames, int rate) {
    if (pcm == NULL || frames == 0u || rate <= 0 || frames > 0x7fffffffu / (size_t)A.rate) {
        return MOY_AUD_BAD;
    }
    int rc = aud_init();
    if (rc != MOY_AUD_OK) {
        return rc;
    }
    if (moy_htab_full(A.clips)) {
        return MOY_AUD_FULL;
    }
    // Resampled once, here, so the mix adds a clip frame for frame.
    uint32_t out = (uint32_t)(((uint64_t)frames * (uint64_t)A.rate) / (uint64_t)rate);
    if (out == 0u) {
        out = 1u;
    }
    int16_t *buf = aud_alloc((size_t)out * sizeof(int16_t));
    if (buf == NULL) {
        return MOY_AUD_NOMEM;
    }
    for (uint32_t i = 0; i < out; i++) {
        uint64_t p = ((uint64_t)i * (uint64_t)rate << 16) / (uint64_t)A.rate;
        uint32_t k = (uint32_t)(p >> 16);
        uint32_t f = (uint32_t)(p & 0xffffu);
        int32_t x0 = pcm[k < frames ? k : frames - 1u];
        int32_t x1 = pcm[k + 1u < frames ? k + 1u : frames - 1u];
        buf[i] = (int16_t)(x0 + (((x1 - x0) * (int32_t)f) >> 16));
    }
    void *row;
    if (moy_htab_add(A.clips, h, &row) != MOY_HTAB_OK) {
        aud_release(buf, (size_t)out * sizeof(int16_t));
        return MOY_AUD_NOMEM;
    }
    ((aud_clip_t *)row)->pcm = buf;
    ((aud_clip_t *)row)->frames = out;
    return MOY_AUD_OK;
}

int moy_aud_sample_play(uint32_t s, uint32_t h, int chan) {
    aud_clip_t *c = clip_of(h);
    if (c == NULL) {
        return MOY_AUD_STALE;
    }
    trace(s, MOY_AUD_T_SAMPLE, (int32_t)(h & 0xffu), chan);
    VERB(s, r);
    if (chan < 0 || chan >= MOY_AUD_SAMPLE_VOICES) {
        chan = 0;
        for (int i = 0; i < MOY_AUD_SAMPLE_VOICES; i++) {
            if (r->sv[i].pcm == NULL) {
                chan = i;
                break;
            }
        }
    }
    r->sv[chan].pcm = c->pcm;
    r->sv[chan].frames = c->frames;
    r->sv[chan].pos = 0;
    r->sv[chan].clip = h;
    trig_mark(r);
    VERB_END();
}

int moy_aud_sample_free(uint32_t h) {
    aud_clip_t *c = clip_of(h);
    if (c == NULL) {
        return MOY_AUD_STALE;
    }
    uint64_t t0 = lock_timed();
    for (uint32_t i = 0; A.sess != NULL && i < moy_htab_slots(A.sess); i++) {
        if (moy_htab_live(A.sess, i)) {
            aud_sess_t *r = moy_htab_row(A.sess, i);
            for (int v = 0; v < MOY_AUD_SAMPLE_VOICES; v++) {
                if (r->sv[v].clip == h) {
                    r->sv[v].pcm = NULL;
                    r->sv[v].clip = 0u;
                }
            }
        }
    }
    unlock_timed(t0);
    aud_release(c->pcm, (size_t)c->frames * sizeof(int16_t));
    moy_htab_release(A.clips, h);
    return MOY_AUD_OK;
}

static void mix_samples(aud_sess_t *r, int16_t *out, int n) {
    int master = effective(r);
    int32_t gain = master <= 0 ? 0 : master >= 7 ? 32768 : master * 32768 / 7;
    for (int v = 0; v < MOY_AUD_SAMPLE_VOICES; v++) {
        aud_voice_t *sv = &r->sv[v];
        if (sv->pcm == NULL) {
            continue;
        }
        for (int f = 0; f < n && sv->pos < sv->frames; f++) {
            int32_t o = out[f] + (int32_t)(((int64_t)sv->pcm[sv->pos++] * gain) >> 15);
            out[f] = (int16_t)(o > 32767 ? 32767 : (o < -32768 ? -32768 : o));
        }
        if (sv->pos >= sv->frames) {
            sv->pcm = NULL;
        }
    }
}

// -- the mix ----------------------------------------------------------------------

void moy_aud_set_rate(int rate) {
    if (rate <= 0) {
        return;
    }
    moy_aud_lock();
    A.rate = rate;
    for (uint32_t i = 0; A.sess != NULL && i < moy_htab_slots(A.sess); i++) {
        if (moy_htab_live(A.sess, i)) {
            ((aud_sess_t *)moy_htab_row(A.sess, i))->a->rate = rate;
        }
    }
    moy_aud_unlock();
}

int moy_aud_rate(void) {
    return A.rate;
}

void moy_aud_render(int16_t *out, int n) {
    if (n <= 0) {
        return;
    }
    uint64_t t0 = moy_aud_now_us();
    moy_aud_lock();
    uint64_t wait = moy_aud_now_us() - t0;
    if (wait > A.st.lock_wait_us_max) {
        A.st.lock_wait_us_max = (uint32_t)wait;
    }
    aud_sess_t *r = A.focus;
    if (r != NULL) {
        moy_audio_render(r->a, out, n);
        mix_samples(r, out, n);
    } else {
        memset(out, 0, (size_t)n * sizeof(int16_t));
    }
    if (A.pcm_on) {
        moy_stream_mix(&A.pcm, out, n, A.rate, r != NULL ? effective(r) : A.console);
    }
    A.st.rendered += (uint32_t)n;
    for (int i = 0; i < n; i++) {
        if (out[i] != 0) {
            A.st.loud_at = A.st.rendered;
            break;
        }
    }
    if (A.trig_at != 0u) {
        uint64_t lat = moy_aud_now_us() - A.trig_at;
        A.trig_at = 0u;
        A.st.trig++;
        A.st.trig_us_last = (uint32_t)lat;
        if (lat > A.st.trig_us_max) {
            A.st.trig_us_max = (uint32_t)lat;
        }
    }
    moy_aud_unlock();
}

int moy_aud_dump(uint32_t s, int16_t *out, int n) {
    aud_sess_t *r = sess_of(s);
    if (r == NULL) {
        return MOY_AUD_STALE;
    }
    if (r == A.focus || n <= 0) {
        return MOY_AUD_BAD;
    }
    moy_aud_lock();
    r->a->rate = A.rate;
    moy_audio_render(r->a, out, n);
    moy_aud_unlock();
    return MOY_AUD_OK;
}

void moy_aud_count_out(uint32_t written, uint32_t underruns) {
    A.st.written += written;
    A.st.underruns += underruns;
}

void moy_aud_stats(moy_aud_stats_t *st) {
    *st = A.st;
}

void moy_aud_stats_reset_max(void) {
    A.st.trig_us_max = 0;
    A.st.lock_wait_us_max = 0;
    A.st.lock_hold_us_max = 0;
    A.st.bank_parse_us_max = 0;
}

// -- the trace ----------------------------------------------------------------------

void moy_aud_trace_on(int on) {
    if (on && A.trace == NULL) {
        A.trace = aud_alloc(MOY_AUD_TRACE_ROWS * 4u * sizeof(int32_t));
    }
    A.trace_on = on && A.trace != NULL;
    A.trace_n = 0;
}

size_t moy_aud_trace_read(int32_t *rows, size_t max) {
    size_t n = A.trace_n < max ? A.trace_n : max;
    if (n) {
        memcpy(rows, A.trace, n * 4u * sizeof(int32_t));
    }
    A.trace_n = 0;
    return n;
}

// -- a compiled cart's stream (moy_audio_snd.h) ------------------------------------

int moy_audio_snd_open(void) {
    size_t bytes = MOY_AUDIO_SND_DEPTH * sizeof(int16_t);
    if (!moy_aud_out_plays()) {
        return 0;
    }
    if (A.pcm_ring == NULL) {
        A.pcm_ring = aud_alloc(bytes);
        if (A.pcm_ring == NULL) {
            return 0;
        }
    }
    moy_aud_lock();
    moy_stream_init(&A.pcm, A.pcm_ring, MOY_AUDIO_SND_DEPTH, MOY_AUDIO_SND_RATE);
    A.pcm_on = 1;
    moy_aud_unlock();
    return 1;
}

uint32_t moy_audio_snd(const uint8_t *pcm, uint32_t n) {
    uint32_t r = 0;
    moy_aud_lock();
    if (A.pcm_on) {
        r = n ? moy_stream_write(&A.pcm, pcm, n) : moy_stream_room(&A.pcm);
    }
    moy_aud_unlock();
    if (r && n) {
        moy_aud_out_shallow(1);
    }
    return r;
}

void moy_audio_snd_close(void) {
    moy_aud_lock();
    if (A.pcm_on) {
        moy_stream_clear(&A.pcm);
    }
    A.pcm_on = 0;
    moy_aud_unlock();
    moy_aud_out_shallow(0);
}

int moy_aud_snd_counts(uint32_t c[4], int *open) {
    if (A.pcm_ring == NULL) {
        return 0;
    }
    moy_aud_lock();
    c[0] = A.pcm.in;
    c[1] = A.pcm.out;
    c[2] = A.pcm.starved;
    c[3] = moy_stream_room(&A.pcm);
    *open = A.pcm_on;
    moy_aud_unlock();
    return 1;
}
