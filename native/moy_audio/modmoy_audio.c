// moy_audio: the MicroPython binding for the kernel's audio (moy_aud.h). One
// call per verb, nothing held between calls: a session is an integer handle.
//
// The synth is libmoy/ (vendored, libmoy/UPSTREAM.md), the sessions and the mix
// moy_aud.c, the speaker moy_aud_out.c. This file converts arguments. A stale
// handle raises ValueError; a full table MemoryError.
//
// The same binding builds for the desktop MicroPython (tests/test_audio_parity.py
// drives it against libmoy sample for sample) and the browser, where nothing
// feeds a speaker and the page pulls render() once a frame.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_aud.h"
#include "moy_audio.h"

#if MOY_AUD_BOARD && MOY_AUDIO_CODEC_ES8311
#include "moy_codec_es8311.h"
#endif

static mp_int_t check(int rc) {
    switch (rc) {
        case MOY_AUD_OK:
        case MOY_AUD_BANK:
            return rc;
        case MOY_AUD_STALE:
            mp_raise_ValueError(MP_ERROR_TEXT("stale audio handle"));
        case MOY_AUD_FULL:
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("the audio table is full"));
        case MOY_AUD_NOMEM:
            mp_raise_type(&mp_type_MemoryError);
        default:
            mp_raise_ValueError(MP_ERROR_TEXT("audio: bad argument"));
    }
}

static uint32_t h_of(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int_truncated(o);
}

static int opt_int(size_t n_args, const mp_obj_t *a, size_t i, int dflt) {
    return (n_args > i && a[i] != mp_const_none) ? (int)mp_obj_get_int(a[i]) : dflt;
}

// open(owner, bank=None) -> handle. The bank is sounds.json text; a refused
// one leaves the session open on a silent bank (bank() says which).
static mp_obj_t mod_open(size_t n_args, const mp_obj_t *a) {
    uint32_t h = 0;
    const char *text = NULL;
    size_t n = 0;
    if (n_args > 1 && a[1] != mp_const_none) {
        text = mp_obj_str_get_data(a[1], &n);
    }
    check(moy_aud_open(&h, (uint32_t)mp_obj_get_int(a[0]), text, n));
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_open_obj, 1, 2, mod_open);

// bank(h, json) -> bool: True when the bank took the text.
static mp_obj_t mod_bank(mp_obj_t h, mp_obj_t text) {
    size_t n;
    const char *s = mp_obj_str_get_data(text, &n);
    return mp_obj_new_bool(check(moy_aud_bank(h_of(h), s, n)) == MOY_AUD_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_bank_obj, mod_bank);

static mp_obj_t mod_focus(mp_obj_t h) {
    check(moy_aud_focus(h_of(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_focus_obj, mod_focus);

static mp_obj_t mod_focused(void) {
    return mp_obj_new_int_from_uint(moy_aud_focused());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_focused_obj, mod_focused);

static mp_obj_t mod_close(mp_obj_t h) {
    check(moy_aud_close(h_of(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_close_obj, mod_close);

// -- SPEC.md 8.2, one entry per verb -----------------------------------------------

static mp_obj_t mod_sfx(size_t n_args, const mp_obj_t *a) {
    check(moy_aud_sfx(h_of(a[0]), (int)mp_obj_get_int(a[1]), opt_int(n_args, a, 2, -1)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_sfx_obj, 2, 3, mod_sfx);

static mp_obj_t mod_beep(size_t n_args, const mp_obj_t *a) {
    float dur = n_args > 2 ? (float)mp_obj_get_float(a[2]) : 0.15f;
    check(moy_aud_beep(h_of(a[0]), (float)mp_obj_get_float(a[1]), dur));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_beep_obj, 2, 3, mod_beep);

static mp_obj_t mod_music(size_t n_args, const mp_obj_t *a) {
    int loop = n_args > 2 ? (mp_obj_is_true(a[2]) ? 1 : 0) : 1;
    check(moy_aud_music(h_of(a[0]), (int)mp_obj_get_int(a[1]), loop));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_music_obj, 2, 3, mod_music);

static mp_obj_t mod_music_stop(mp_obj_t h) {
    check(moy_aud_music_stop(h_of(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_music_stop_obj, mod_music_stop);

static mp_obj_t mod_stop(size_t n_args, const mp_obj_t *a) {
    check(moy_aud_stop(h_of(a[0]), opt_int(n_args, a, 1, -1)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_stop_obj, 1, 2, mod_stop);

static mp_obj_t mod_level(mp_obj_t h, mp_obj_t level) {
    check(moy_aud_level(h_of(h), (int)mp_obj_get_int(level)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_level_obj, mod_level);

// active(h=0) -> mask (moy_aud_active's bits); 0, the focused session.
static mp_obj_t mod_active(size_t n_args, const mp_obj_t *a) {
    uint32_t m = 0;
    check(moy_aud_active(n_args > 0 ? h_of(a[0]) : 0u, &m));
    return mp_obj_new_int_from_uint(m);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_active_obj, 0, 1, mod_active);

// -- the console's -------------------------------------------------------------------

static mp_obj_t mod_volume(size_t n_args, const mp_obj_t *a) {
    if (n_args > 0) {
        moy_aud_volume((int)mp_obj_get_int(a[0]));
    }
    return MP_OBJ_NEW_SMALL_INT(moy_aud_console_level());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_volume_obj, 0, 1, mod_volume);

static mp_obj_t mod_hush(void) {
    moy_aud_hush();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_hush_obj, mod_hush);

// -- the sample voice ------------------------------------------------------------------

// sample_load(pcm, rate) -> clip: signed 16-bit little-endian mono.
static mp_obj_t mod_sample_load(mp_obj_t pcm, mp_obj_t rate) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(pcm, &bi, MP_BUFFER_READ);
    uint32_t h = 0;
    check(moy_aud_sample_load(&h, (const int16_t *)bi.buf, bi.len / 2u,
                              (int)mp_obj_get_int(rate)));
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_sample_load_obj, mod_sample_load);

static mp_obj_t mod_sample_play(size_t n_args, const mp_obj_t *a) {
    check(moy_aud_sample_play(h_of(a[0]), h_of(a[1]), opt_int(n_args, a, 2, -1)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_sample_play_obj, 2, 3, mod_sample_play);

static mp_obj_t mod_sample_free(mp_obj_t h) {
    check(moy_aud_sample_free(h_of(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_sample_free_obj, mod_sample_free);

// -- the mix --------------------------------------------------------------------------

static mp_obj_t mod_set_rate(mp_obj_t rate) {
    moy_aud_set_rate((int)mp_obj_get_int(rate));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_rate_obj, mod_set_rate);

static mp_obj_t mod_rate(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_aud_rate());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_rate_obj, mod_rate);

// render(buf, n) -> frames: the next `n` frames of the mix, as the speaker
// would take them. What the browser and the desktop pull; on a board the
// feeder task is the only caller, and a second one shows as seam= above 1.
static mp_obj_t mod_render(mp_obj_t buf, mp_obj_t nobj) {
    mp_buffer_info_t bi;
    mp_int_t n = mp_obj_get_int(nobj);
    mp_get_buffer_raise(buf, &bi, MP_BUFFER_WRITE);
    if ((size_t)n * 2u > bi.len) {
        n = (mp_int_t)(bi.len / 2u);
    }
    if (n <= 0) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    moy_aud_render((int16_t *)bi.buf, (int)n);
    return MP_OBJ_NEW_SMALL_INT(n);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_render_obj, mod_render);

// dump(h, buf, n) -> frames: the self-dump, `n` frames of an unfocused
// session's synth alone into `buf` (signed 16-bit mono), no speaker involved.
static mp_obj_t mod_dump(mp_obj_t h, mp_obj_t buf, mp_obj_t nobj) {
    mp_buffer_info_t bi;
    mp_int_t n = mp_obj_get_int(nobj);
    mp_get_buffer_raise(buf, &bi, MP_BUFFER_WRITE);
    if ((size_t)n * 2u > bi.len) {
        n = (mp_int_t)(bi.len / 2u);
    }
    check(moy_aud_dump(h_of(h), (int16_t *)bi.buf, (int)n));
    return MP_OBJ_NEW_SMALL_INT(n);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_dump_obj, mod_dump);

// -- the output and its meters -----------------------------------------------------------

// start() -> (state, why): start the speaker now (the first focus does).
static mp_obj_t out_tuple(int st) {
    const char *why = "";
    st = moy_aud_out_state(&why);
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(st), mp_obj_new_str(why, strlen(why))};
    return mp_obj_new_tuple(2, t);
}

static mp_obj_t mod_start(void) {
    return out_tuple(moy_aud_out_start());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_start_obj, mod_start);

// out() -> (state, why): OUT_NONE, OUT_RUNNING, OUT_FAILED or OUT_ABSENT.
static mp_obj_t mod_out(void) {
    return out_tuple(0);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_out_obj, mod_out);

// stats(reset_max=False) -> (rendered, written, underruns, verbs, trig,
// trig_us_last, trig_us_max, lock_wait_us_max, lock_hold_us_max,
// bank_parse_us_max, hush_at, loud_at, rendered_out).
static mp_obj_t mod_stats(size_t n_args, const mp_obj_t *a) {
    moy_aud_stats_t st;
    moy_aud_stats(&st);
    if (n_args > 0 && mp_obj_is_true(a[0])) {
        moy_aud_stats_reset_max();
    }
    uint32_t v[13] = {st.rendered, st.written, st.underruns, st.verbs, st.trig,
                      st.trig_us_last, st.trig_us_max, st.lock_wait_us_max,
                      st.lock_hold_us_max, st.bank_parse_us_max, st.hush_at,
                      st.loud_at, st.rendered_out};
    mp_obj_t t[13];
    for (int i = 0; i < 13; i++) {
        t[i] = mp_obj_new_int_from_uint(v[i]);
    }
    return mp_obj_new_tuple(13, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_stats_obj, 0, 1, mod_stats);

// probe() -> the AUDIORATE line, or None when there is nothing new.
static mp_obj_t mod_probe(void) {
    char buf[200];
    size_t n = moy_aud_out_probe(buf, sizeof buf);
    return n ? mp_obj_new_str(buf, n) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_probe_obj, mod_probe);

// trace(on) starts or stops the session trace; trace() reads and clears it:
// a list of (slot, verb, a, b), slot -1 for a console-wide call.
static mp_obj_t mod_trace(size_t n_args, const mp_obj_t *a) {
    if (n_args > 0) {
        moy_aud_trace_on(mp_obj_is_true(a[0]));
        return mp_const_none;
    }
    int32_t rows[MOY_AUD_TRACE_ROWS * 4u];
    size_t n = moy_aud_trace_read(rows, MOY_AUD_TRACE_ROWS);
    mp_obj_t l = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < n; i++) {
        mp_obj_t t[4];
        for (int k = 0; k < 4; k++) {
            t[k] = mp_obj_new_int(rows[4 * i + k]);
        }
        mp_obj_list_append(l, mp_obj_new_tuple(4, t));
    }
    return l;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_trace_obj, 0, 1, mod_trace);

// codec() -> (chip_id, mismatches) on a board with a codec, else None.
static mp_obj_t mod_codec(void) {
#if MOY_AUD_BOARD && MOY_AUDIO_CODEC_ES8311
    uint16_t id = 0;
    int bad = -1;
    moy_es8311_check(&id, &bad);
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(id), MP_OBJ_NEW_SMALL_INT(bad)};
    return mp_obj_new_tuple(2, t);
#else
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_codec_obj, mod_codec);

// snd_counts() -> (queued, played, starved, room, open), or None before a
// compiled cart opened its stream.
static mp_obj_t mod_snd_counts(void) {
    uint32_t c[4];
    int open = 0;
    if (!moy_aud_snd_counts(c, &open)) {
        return mp_const_none;
    }
    mp_obj_t t[5];
    for (int i = 0; i < 4; i++) {
        t[i] = mp_obj_new_int_from_uint(c[i]);
    }
    t[4] = mp_obj_new_bool(open);
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_snd_counts_obj, mod_snd_counts);

static const mp_rom_map_elem_t moy_audio_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),     MP_ROM_QSTR(MP_QSTR_moy_audio) },
    { MP_ROM_QSTR(MP_QSTR_CHANNELS),     MP_ROM_INT(MOY_A_CHANNELS) },
    { MP_ROM_QSTR(MP_QSTR_OUT_NONE),     MP_ROM_INT(MOY_AUD_OUT_NONE) },
    { MP_ROM_QSTR(MP_QSTR_OUT_RUNNING),  MP_ROM_INT(MOY_AUD_OUT_RUNNING) },
    { MP_ROM_QSTR(MP_QSTR_OUT_FAILED),   MP_ROM_INT(MOY_AUD_OUT_FAILED) },
    { MP_ROM_QSTR(MP_QSTR_OUT_ABSENT),   MP_ROM_INT(MOY_AUD_OUT_ABSENT) },
    // sessions
    { MP_ROM_QSTR(MP_QSTR_open),         MP_ROM_PTR(&mod_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_bank),         MP_ROM_PTR(&mod_bank_obj) },
    { MP_ROM_QSTR(MP_QSTR_focus),        MP_ROM_PTR(&mod_focus_obj) },
    { MP_ROM_QSTR(MP_QSTR_focused),      MP_ROM_PTR(&mod_focused_obj) },
    { MP_ROM_QSTR(MP_QSTR_close),        MP_ROM_PTR(&mod_close_obj) },
    // SPEC.md 8.2
    { MP_ROM_QSTR(MP_QSTR_sfx),          MP_ROM_PTR(&mod_sfx_obj) },
    { MP_ROM_QSTR(MP_QSTR_beep),         MP_ROM_PTR(&mod_beep_obj) },
    { MP_ROM_QSTR(MP_QSTR_music),        MP_ROM_PTR(&mod_music_obj) },
    { MP_ROM_QSTR(MP_QSTR_music_stop),   MP_ROM_PTR(&mod_music_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop),         MP_ROM_PTR(&mod_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_level),        MP_ROM_PTR(&mod_level_obj) },
    { MP_ROM_QSTR(MP_QSTR_active),       MP_ROM_PTR(&mod_active_obj) },
    // the console's
    { MP_ROM_QSTR(MP_QSTR_volume),       MP_ROM_PTR(&mod_volume_obj) },
    { MP_ROM_QSTR(MP_QSTR_hush),         MP_ROM_PTR(&mod_hush_obj) },
    // the sample voice (#70)
    { MP_ROM_QSTR(MP_QSTR_sample_load),  MP_ROM_PTR(&mod_sample_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_sample_play),  MP_ROM_PTR(&mod_sample_play_obj) },
    { MP_ROM_QSTR(MP_QSTR_sample_free),  MP_ROM_PTR(&mod_sample_free_obj) },
    // the mix, the output, the meters
    { MP_ROM_QSTR(MP_QSTR_set_rate),     MP_ROM_PTR(&mod_set_rate_obj) },
    { MP_ROM_QSTR(MP_QSTR_rate),         MP_ROM_PTR(&mod_rate_obj) },
    { MP_ROM_QSTR(MP_QSTR_render),       MP_ROM_PTR(&mod_render_obj) },
    { MP_ROM_QSTR(MP_QSTR_dump),         MP_ROM_PTR(&mod_dump_obj) },
    { MP_ROM_QSTR(MP_QSTR_start),        MP_ROM_PTR(&mod_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_out),          MP_ROM_PTR(&mod_out_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats),        MP_ROM_PTR(&mod_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_probe),        MP_ROM_PTR(&mod_probe_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace),        MP_ROM_PTR(&mod_trace_obj) },
    { MP_ROM_QSTR(MP_QSTR_codec),        MP_ROM_PTR(&mod_codec_obj) },
    { MP_ROM_QSTR(MP_QSTR_snd_counts),   MP_ROM_PTR(&mod_snd_counts_obj) },
};
static MP_DEFINE_CONST_DICT(moy_audio_globals, moy_audio_globals_table);

const mp_obj_module_t mp_module_moy_audio = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_audio_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_audio, mp_module_moy_audio);
