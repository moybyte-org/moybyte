// moy_media's MicroPython binding: the media service (moy_media.h) as the
// module `moy_media`, the names runtime/host/moy_media.py gives CPython. A
// refusal is a negative errno, which the face (runtime/system/dev_media.py)
// raises. `rate` in open() is the host's: a board decodes to its output's rate
// and channels (moy_aud_out_rate and _channels).
//
//   present() share() open(rate) close() pid()
//   last() -> (runs, pid, client, state, err)
//   take(client, who, label) -> the client taken over from, 0 none
//   tell(was) -> bool  stamp() -> int | None
//   play(src, held) -> entries  page(on)  seek(ms)
//   clear() add(src, title) playlist(text, base)
//   go(i) stop() pause(on) volume(v)
//   feed(gen, buf) -> n  feed_end(gen)  feed_fail(gen, err)  feed_size(gen, size)
//   feed_icy(gen, metaint, station)
//   status() -> tuple (FIELDS' order) | None   text(which) -> str
//   entry(i) -> (src, title) | None   run() -> bool   pump()
//   probe(path) -> (seconds, title, artist) | None
//   TASK: 1 where a task runs the service's loop (a board), else 0 (the
//   browser: the frame's job runs it)
//   icy_title(meta) -> str | None   list_kind(ctype, path, text) -> int

#include <string.h>

#include "py/runtime.h"

#include "moy_media.h"

#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define MEDIA_TASK 1
#endif
#endif
#ifndef MEDIA_TASK
#define MEDIA_TASK 0
#endif

static uint32_t u32(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int_truncated(o);
}

static mp_obj_t rc(int r) {
    return MP_OBJ_NEW_SMALL_INT(r);
}

static const char *opt_str(mp_obj_t o, size_t *n) {
    if (o == mp_const_none) {
        *n = 0;
        return NULL;
    }
    return mp_obj_str_get_data(o, n);
}

static mp_obj_t media_present(void) {
    return mp_obj_new_bool(moy_media_present());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_present_obj, media_present);

static mp_obj_t media_share(void) {
    return mp_obj_new_int_from_uint(moy_media_share());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_share_obj, media_share);

static mp_obj_t media_open(mp_obj_t rate) {
    return rc(moy_media_open(mp_obj_get_int(rate), 1));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_open_obj, media_open);

static mp_obj_t media_close(void) {
    return rc(moy_media_close());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_close_obj, media_close);

static mp_obj_t media_pid(void) {
    return mp_obj_new_int_from_uint(moy_media_pid());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_pid_obj, media_pid);

static mp_obj_t media_last(void) {
    moy_media_end_t e;
    moy_media_last(&e);
    mp_obj_t t[5] = { mp_obj_new_int_from_uint(e.runs), mp_obj_new_int_from_uint(e.pid),
                      mp_obj_new_int_from_uint(e.client), MP_OBJ_NEW_SMALL_INT(e.state),
                      MP_OBJ_NEW_SMALL_INT(e.err) };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_last_obj, media_last);

static mp_obj_t media_take(mp_obj_t client, mp_obj_t who, mp_obj_t label) {
    size_t n;
    const char *w = opt_str(who, &n), *l = opt_str(label, &n);
    return mp_obj_new_int_from_uint(moy_media_take(u32(client), w, l));
}
static MP_DEFINE_CONST_FUN_OBJ_3(media_take_obj, media_take);

static mp_obj_t media_play(mp_obj_t src, mp_obj_t held) {
    return rc(moy_media_play(mp_obj_str_get_str(src), mp_obj_is_true(held)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_play_obj, media_play);

static mp_obj_t media_page(mp_obj_t on) {
    return rc(moy_media_page(mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_page_obj, media_page);

static mp_obj_t media_seek(mp_obj_t ms) {
    mp_int_t v = mp_obj_get_int(ms);
    return rc(moy_media_seek(v < 0 ? 0u : (uint32_t)v));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_seek_obj, media_seek);

static mp_obj_t media_pump(void) {
    moy_media_pump();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_pump_obj, media_pump);

static mp_obj_t media_probe(mp_obj_t path) {
    moy_media_probe_t p;
    if (moy_media_probe(mp_obj_str_get_str(path), &p) != 0) {
        return mp_const_none;
    }
    mp_obj_t t[3] = { mp_obj_new_int_from_uint(p.seconds), mp_obj_new_str(p.title, strlen(p.title)),
                      mp_obj_new_str(p.artist, strlen(p.artist)) };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_probe_obj, media_probe);

static mp_obj_t media_clear(void) {
    return rc(moy_media_clear());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_clear_obj, media_clear);

static mp_obj_t media_add(mp_obj_t src, mp_obj_t title) {
    size_t n;
    const char *t = opt_str(title, &n);
    return rc(moy_media_add(mp_obj_str_get_str(src), t));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_add_obj, media_add);

static mp_obj_t media_playlist(mp_obj_t text, mp_obj_t base) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(text, &b, MP_BUFFER_READ);
    size_t n;
    const char *bs = opt_str(base, &n);
    return rc(moy_media_playlist(b.buf, b.len, bs));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_playlist_obj, media_playlist);

static mp_obj_t media_go(mp_obj_t i) {
    return rc(moy_media_go(mp_obj_get_int(i)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_go_obj, media_go);

static mp_obj_t media_stop(void) {
    return rc(moy_media_stop());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_stop_obj, media_stop);

static mp_obj_t media_pause(mp_obj_t on) {
    return rc(moy_media_pause(mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_pause_obj, media_pause);

static mp_obj_t media_volume(mp_obj_t v) {
    return rc(moy_media_volume(mp_obj_get_int(v)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_volume_obj, media_volume);

static mp_obj_t media_feed(mp_obj_t gen, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return rc(moy_media_feed(u32(gen), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_feed_obj, media_feed);

static mp_obj_t media_feed_end(mp_obj_t gen) {
    return rc(moy_media_feed_end(u32(gen)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_feed_end_obj, media_feed_end);

static mp_obj_t media_feed_fail(mp_obj_t gen, mp_obj_t err) {
    return rc(moy_media_feed_fail(u32(gen), mp_obj_get_int(err)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_feed_fail_obj, media_feed_fail);

static mp_obj_t media_feed_size(mp_obj_t gen, mp_obj_t size) {
    return rc(moy_media_feed_size(u32(gen), u32(size)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_feed_size_obj, media_feed_size);

static mp_obj_t media_feed_icy(mp_obj_t gen, mp_obj_t metaint, mp_obj_t station) {
    return rc(moy_media_feed_icy(u32(gen), u32(metaint), mp_obj_str_get_str(station)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(media_feed_icy_obj, media_feed_icy);

static mp_obj_t media_status(void) {
    moy_media_status_t s;
    if (!moy_media_status(&s)) {
        return mp_const_none;
    }
    mp_obj_t t[27] = {
        MP_OBJ_NEW_SMALL_INT(s.state), MP_OBJ_NEW_SMALL_INT(s.paused),
        mp_obj_new_int_from_uint(s.pid), mp_obj_new_int_from_uint(s.gen),
        MP_OBJ_NEW_SMALL_INT(s.entry), MP_OBJ_NEW_SMALL_INT(s.entries),
        MP_OBJ_NEW_SMALL_INT(s.kind), MP_OBJ_NEW_SMALL_INT(s.armed),
        MP_OBJ_NEW_SMALL_INT(s.rate), MP_OBJ_NEW_SMALL_INT(s.channels),
        MP_OBJ_NEW_SMALL_INT(s.kbps), MP_OBJ_NEW_SMALL_INT(s.volume),
        mp_obj_new_int_from_uint(s.in_bytes), mp_obj_new_int_from_uint(s.in_room),
        mp_obj_new_int_from_uint(s.pcm_frames), mp_obj_new_int_from_uint(s.decoded),
        mp_obj_new_int_from_uint(s.played), mp_obj_new_int_from_uint(s.starved),
        MP_OBJ_NEW_SMALL_INT(s.err), mp_obj_new_int_from_uint(s.text_gen),
        MP_OBJ_NEW_SMALL_INT(s.idle), mp_obj_new_int_from_uint(s.at_ms),
        mp_obj_new_int_from_uint(s.len_ms), mp_obj_new_int_from_uint(s.feed_at),
        mp_obj_new_int_from_uint(s.client), mp_obj_new_int_from_uint(s.sram),
        mp_obj_new_int_from_uint(s.dec_us),
    };
    return mp_obj_new_tuple(27, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_status_obj, media_status);

// moy_media_stamp: None with no service.
static mp_obj_t media_stamp(void) {
    int32_t h = moy_media_stamp();
    return h < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(h);
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_stamp_obj, media_stamp);

// moy_media_tell: whether a notification went out.
static mp_obj_t media_tell(mp_obj_t was) {
    return mp_obj_new_bool(moy_media_tell(u32(was)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_tell_obj, media_tell);

// A media key's consumer usage going down, answered: whether it acted (the
// frame's job answers the keyboard's own).
static mp_obj_t media_key(mp_obj_t usage) {
    return mp_obj_new_bool(moy_media_key((uint16_t)mp_obj_get_int(usage)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_key_obj, media_key);

static mp_obj_t media_text(mp_obj_t which) {
    char buf[MOY_MEDIA_SRC_MAX];
    int n = moy_media_text(mp_obj_get_int(which), buf, sizeof(buf));
    return mp_obj_new_str(buf, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_text_obj, media_text);

static mp_obj_t media_entry(mp_obj_t i) {
    char src[MOY_MEDIA_SRC_MAX], title[MOY_MEDIA_TEXT_MAX];
    if (moy_media_entry(mp_obj_get_int(i), src, sizeof(src), title, sizeof(title)) < 0) {
        return mp_const_none;
    }
    mp_obj_t t[2] = { mp_obj_new_str(src, strlen(src)), mp_obj_new_str(title, strlen(title)) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_entry_obj, media_entry);

static mp_obj_t media_run(void) {
    return mp_obj_new_bool(moy_media_run());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_run_obj, media_run);

static mp_obj_t media_icy_title(mp_obj_t meta) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(meta, &b, MP_BUFFER_READ);
    char out[MOY_MEDIA_TEXT_MAX];
    int n = moy_media_icy_title(b.buf, b.len, out, sizeof(out));
    return n < 0 ? mp_const_none : mp_obj_new_str(out, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_icy_title_obj, media_icy_title);

static mp_obj_t media_list_kind(mp_obj_t ctype, mp_obj_t path, mp_obj_t text) {
    size_t cn, pn, tn;
    const char *c = opt_str(ctype, &cn), *p = opt_str(path, &pn), *t = NULL;
    tn = 0;
    if (text != mp_const_none) {
        mp_buffer_info_t b;
        mp_get_buffer_raise(text, &b, MP_BUFFER_READ);
        t = b.buf;
        tn = b.len;
    }
    return rc(moy_media_list_kind(c, p, t, tn));
}
static MP_DEFINE_CONST_FUN_OBJ_3(media_list_kind_obj, media_list_kind);

static const mp_rom_map_elem_t media_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_media) },
    { MP_ROM_QSTR(MP_QSTR_present), MP_ROM_PTR(&media_present_obj) },
    { MP_ROM_QSTR(MP_QSTR_share), MP_ROM_PTR(&media_share_obj) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&media_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&media_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_pid), MP_ROM_PTR(&media_pid_obj) },
    { MP_ROM_QSTR(MP_QSTR_last), MP_ROM_PTR(&media_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&media_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_play), MP_ROM_PTR(&media_play_obj) },
    { MP_ROM_QSTR(MP_QSTR_page), MP_ROM_PTR(&media_page_obj) },
    { MP_ROM_QSTR(MP_QSTR_seek), MP_ROM_PTR(&media_seek_obj) },
    { MP_ROM_QSTR(MP_QSTR_pump), MP_ROM_PTR(&media_pump_obj) },
    { MP_ROM_QSTR(MP_QSTR_probe), MP_ROM_PTR(&media_probe_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear), MP_ROM_PTR(&media_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_add), MP_ROM_PTR(&media_add_obj) },
    { MP_ROM_QSTR(MP_QSTR_playlist), MP_ROM_PTR(&media_playlist_obj) },
    { MP_ROM_QSTR(MP_QSTR_go), MP_ROM_PTR(&media_go_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&media_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_pause), MP_ROM_PTR(&media_pause_obj) },
    { MP_ROM_QSTR(MP_QSTR_volume), MP_ROM_PTR(&media_volume_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed), MP_ROM_PTR(&media_feed_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed_end), MP_ROM_PTR(&media_feed_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed_fail), MP_ROM_PTR(&media_feed_fail_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed_size), MP_ROM_PTR(&media_feed_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed_icy), MP_ROM_PTR(&media_feed_icy_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&media_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_stamp), MP_ROM_PTR(&media_stamp_obj) },
    { MP_ROM_QSTR(MP_QSTR_tell), MP_ROM_PTR(&media_tell_obj) },
    { MP_ROM_QSTR(MP_QSTR_key), MP_ROM_PTR(&media_key_obj) },
    { MP_ROM_QSTR(MP_QSTR_text), MP_ROM_PTR(&media_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_entry), MP_ROM_PTR(&media_entry_obj) },
    { MP_ROM_QSTR(MP_QSTR_run), MP_ROM_PTR(&media_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_icy_title), MP_ROM_PTR(&media_icy_title_obj) },
    { MP_ROM_QSTR(MP_QSTR_list_kind), MP_ROM_PTR(&media_list_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_IDLE), MP_ROM_INT(MOY_MEDIA_IDLE) },
    { MP_ROM_QSTR(MP_QSTR_OPENING), MP_ROM_INT(MOY_MEDIA_OPENING) },
    { MP_ROM_QSTR(MP_QSTR_PLAYING), MP_ROM_INT(MOY_MEDIA_PLAYING) },
    { MP_ROM_QSTR(MP_QSTR_ENDED), MP_ROM_INT(MOY_MEDIA_ENDED) },
    { MP_ROM_QSTR(MP_QSTR_FAILED), MP_ROM_INT(MOY_MEDIA_FAILED) },
    { MP_ROM_QSTR(MP_QSTR_URL), MP_ROM_INT(MOY_MEDIA_URL) },
    { MP_ROM_QSTR(MP_QSTR_FEED), MP_ROM_INT(MOY_MEDIA_FEED) },
    { MP_ROM_QSTR(MP_QSTR_FILE), MP_ROM_INT(MOY_MEDIA_FILE) },
    { MP_ROM_QSTR(MP_QSTR_T_SRC), MP_ROM_INT(MOY_MEDIA_T_SRC) },
    { MP_ROM_QSTR(MP_QSTR_T_TITLE), MP_ROM_INT(MOY_MEDIA_T_TITLE) },
    { MP_ROM_QSTR(MP_QSTR_T_STATION), MP_ROM_INT(MOY_MEDIA_T_STATION) },
    { MP_ROM_QSTR(MP_QSTR_T_ENTRY), MP_ROM_INT(MOY_MEDIA_T_ENTRY) },
    { MP_ROM_QSTR(MP_QSTR_T_SONG), MP_ROM_INT(MOY_MEDIA_T_SONG) },
    { MP_ROM_QSTR(MP_QSTR_T_ARTIST), MP_ROM_INT(MOY_MEDIA_T_ARTIST) },
    { MP_ROM_QSTR(MP_QSTR_T_WHO), MP_ROM_INT(MOY_MEDIA_T_WHO) },
    { MP_ROM_QSTR(MP_QSTR_T_LABEL), MP_ROM_INT(MOY_MEDIA_T_LABEL) },
    { MP_ROM_QSTR(MP_QSTR_TASK), MP_ROM_INT(MEDIA_TASK) },
};
static MP_DEFINE_CONST_DICT(media_globals, media_globals_table);

const mp_obj_module_t moy_media_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&media_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_media, moy_media_module);
