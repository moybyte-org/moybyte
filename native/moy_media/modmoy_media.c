// moy_media's MicroPython binding: the media service (moy_media.h) as the
// module `moy_media`, the names runtime/host/moy_media.py gives CPython. A
// refusal is a negative errno, which the face (runtime/system/dev_media.py)
// raises. `rate` in open() is the host's: a board decodes to its output's rate
// and channels (moy_aud_out_rate and _channels).
//
//   present() share() open(owner, rate) close(owner) owner()
//   clear(owner) add(owner, src, title) playlist(owner, text, base)
//   go(owner, i) stop(owner) pause(owner, on) volume(owner, v)
//   feed(owner, gen, buf) -> n  feed_end(owner, gen)
//   status() -> tuple (FIELDS' order) | None   text(which) -> str
//   entry(i) -> (src, title) | None   run() -> bool
//   icy_title(meta) -> str | None   list_kind(ctype, path, text) -> int

#include <string.h>

#include "py/runtime.h"

#include "moy_media.h"

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

static mp_obj_t media_open(mp_obj_t owner, mp_obj_t rate) {
    return rc(moy_media_open(u32(owner), mp_obj_get_int(rate), 1));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_open_obj, media_open);

static mp_obj_t media_close(mp_obj_t owner) {
    return rc(moy_media_close(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_close_obj, media_close);

static mp_obj_t media_owner(void) {
    return mp_obj_new_int_from_uint(moy_media_owner());
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_owner_obj, media_owner);

static mp_obj_t media_clear(mp_obj_t owner) {
    return rc(moy_media_clear(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_clear_obj, media_clear);

static mp_obj_t media_add(mp_obj_t owner, mp_obj_t src, mp_obj_t title) {
    size_t n;
    const char *t = opt_str(title, &n);
    return rc(moy_media_add(u32(owner), mp_obj_str_get_str(src), t));
}
static MP_DEFINE_CONST_FUN_OBJ_3(media_add_obj, media_add);

static mp_obj_t media_playlist(mp_obj_t owner, mp_obj_t text, mp_obj_t base) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(text, &b, MP_BUFFER_READ);
    size_t n;
    const char *bs = opt_str(base, &n);
    return rc(moy_media_playlist(u32(owner), b.buf, b.len, bs));
}
static MP_DEFINE_CONST_FUN_OBJ_3(media_playlist_obj, media_playlist);

static mp_obj_t media_go(mp_obj_t owner, mp_obj_t i) {
    return rc(moy_media_go(u32(owner), mp_obj_get_int(i)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_go_obj, media_go);

static mp_obj_t media_stop(mp_obj_t owner) {
    return rc(moy_media_stop(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(media_stop_obj, media_stop);

static mp_obj_t media_pause(mp_obj_t owner, mp_obj_t on) {
    return rc(moy_media_pause(u32(owner), mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_pause_obj, media_pause);

static mp_obj_t media_volume(mp_obj_t owner, mp_obj_t v) {
    return rc(moy_media_volume(u32(owner), mp_obj_get_int(v)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_volume_obj, media_volume);

static mp_obj_t media_feed(mp_obj_t owner, mp_obj_t gen, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return rc(moy_media_feed(u32(owner), u32(gen), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(media_feed_obj, media_feed);

static mp_obj_t media_feed_end(mp_obj_t owner, mp_obj_t gen) {
    return rc(moy_media_feed_end(u32(owner), u32(gen)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(media_feed_end_obj, media_feed_end);

static mp_obj_t media_status(void) {
    moy_media_status_t s;
    if (!moy_media_status(&s)) {
        return mp_const_none;
    }
    mp_obj_t t[21] = {
        MP_OBJ_NEW_SMALL_INT(s.state), MP_OBJ_NEW_SMALL_INT(s.paused),
        mp_obj_new_int_from_uint(s.owner), mp_obj_new_int_from_uint(s.gen),
        MP_OBJ_NEW_SMALL_INT(s.entry), MP_OBJ_NEW_SMALL_INT(s.entries),
        MP_OBJ_NEW_SMALL_INT(s.kind), MP_OBJ_NEW_SMALL_INT(s.armed),
        MP_OBJ_NEW_SMALL_INT(s.rate), MP_OBJ_NEW_SMALL_INT(s.channels),
        MP_OBJ_NEW_SMALL_INT(s.kbps), MP_OBJ_NEW_SMALL_INT(s.volume),
        mp_obj_new_int_from_uint(s.in_bytes), mp_obj_new_int_from_uint(s.in_room),
        mp_obj_new_int_from_uint(s.pcm_frames), mp_obj_new_int_from_uint(s.decoded),
        mp_obj_new_int_from_uint(s.played), mp_obj_new_int_from_uint(s.starved),
        MP_OBJ_NEW_SMALL_INT(s.err), mp_obj_new_int_from_uint(s.text_gen),
        MP_OBJ_NEW_SMALL_INT(s.idle),
    };
    return mp_obj_new_tuple(21, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(media_status_obj, media_status);

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
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&media_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear), MP_ROM_PTR(&media_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_add), MP_ROM_PTR(&media_add_obj) },
    { MP_ROM_QSTR(MP_QSTR_playlist), MP_ROM_PTR(&media_playlist_obj) },
    { MP_ROM_QSTR(MP_QSTR_go), MP_ROM_PTR(&media_go_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&media_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_pause), MP_ROM_PTR(&media_pause_obj) },
    { MP_ROM_QSTR(MP_QSTR_volume), MP_ROM_PTR(&media_volume_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed), MP_ROM_PTR(&media_feed_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed_end), MP_ROM_PTR(&media_feed_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&media_status_obj) },
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
    { MP_ROM_QSTR(MP_QSTR_T_SRC), MP_ROM_INT(MOY_MEDIA_T_SRC) },
    { MP_ROM_QSTR(MP_QSTR_T_TITLE), MP_ROM_INT(MOY_MEDIA_T_TITLE) },
    { MP_ROM_QSTR(MP_QSTR_T_STATION), MP_ROM_INT(MOY_MEDIA_T_STATION) },
    { MP_ROM_QSTR(MP_QSTR_T_ENTRY), MP_ROM_INT(MOY_MEDIA_T_ENTRY) },
};
static MP_DEFINE_CONST_DICT(media_globals, media_globals_table);

const mp_obj_module_t moy_media_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&media_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_media, moy_media_module);
