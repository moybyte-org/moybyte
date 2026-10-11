// The module `moy_ui` (moy_ui.h): what runtime/ui.py's face calls. Names in,
// ids out once; after that the face passes ints, and caches what it gets
// back, so a widget draw reaches here only on a cache miss.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"

#include "moy_ui.h"

static mp_obj_t names_tuple(const char *const *names, int n) {
    mp_obj_t items[MOY_UI_KINDS > MOY_UI_STRINGS ? MOY_UI_KINDS : MOY_UI_STRINGS];
    for (int i = 0; i < n; i++) {
        items[i] = mp_obj_new_str(names[i], strlen(names[i]));
    }
    return mp_obj_new_tuple((size_t)n, items);
}

static mp_obj_t ui_tokens(void) {
    return names_tuple(moy_ui_token_names, MOY_UI_TOKENS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_tokens_obj, ui_tokens);

static mp_obj_t ui_states(void) {
    return names_tuple(moy_ui_state_names, MOY_UI_STATES);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_states_obj, ui_states);

// kinds(): the kinds a style entry colours; metric_kinds(): those with slots.
static mp_obj_t ui_kinds(void) {
    return names_tuple(moy_ui_kind_names, MOY_UI_COLOR_KINDS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_kinds_obj, ui_kinds);

static mp_obj_t ui_metric_kinds(void) {
    mp_obj_t items[MOY_UI_KINDS];
    size_t n = 0;
    for (int k = 0; k < MOY_UI_KINDS; k++) {
        if (moy_ui_metric_n[k]) {
            items[n++] = mp_obj_new_str(moy_ui_kind_names[k], strlen(moy_ui_kind_names[k]));
        }
    }
    return mp_obj_new_tuple(n, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_metric_kinds_obj, ui_metric_kinds);

static mp_obj_t ui_kind(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_kind(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_kind_obj, ui_kind);

static mp_obj_t ui_button_kind(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_button_kind(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_button_kind_obj, ui_button_kind);

static mp_obj_t ui_skins(void) {
    return names_tuple(moy_ui_skin_names, MOY_UI_SKINS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_skins_obj, ui_skins);

// skin([name]): install `name` (an unknown one installs the default); the
// installed skin's name either way.
static mp_obj_t ui_skin(size_t n_args, const mp_obj_t *args) {
    int s;
    if (n_args > 0) {
        size_t n;
        const char *name = mp_obj_str_get_data(args[0], &n);
        s = moy_ui_skin_use(name, n);
    } else {
        s = moy_ui_skin();
    }
    const char *name = moy_ui_skin_names[s];
    return mp_obj_new_str(name, strlen(name));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_skin_obj, 0, 1, ui_skin);

static mp_obj_t ui_gen(void) {
    return mp_obj_new_int_from_uint(moy_ui_gen());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_gen_obj, ui_gen);

static void check(int rc) {
    if (rc == MOY_UI_NOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (rc != MOY_UI_OK) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: bad argument"));
    }
}

// look(slot, tokens): point a look at a token row, an array('i') of
// MOY_UI_TOKENS values.
static mp_obj_t ui_look(mp_obj_t slot, mp_obj_t tokens) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(tokens, &b, MP_BUFFER_READ);
    if (b.len != MOY_UI_TOKENS * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: a token row is 28 int32"));
    }
    int32_t row[MOY_UI_TOKENS];
    memcpy(row, b.buf, sizeof(row));
    check(moy_ui_look_set(mp_obj_get_int(slot), row));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_look_obj, ui_look);

static mp_obj_t colour(int16_t v) {
    return v < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(v);
}

// colors(look, kind, state) -> (field, ink, edge), None for nothing.
static mp_obj_t ui_colors(mp_obj_t look, mp_obj_t kind, mp_obj_t state) {
    int16_t c[3];
    check(moy_ui_colors(mp_obj_get_int(look), mp_obj_get_int(kind),
                        mp_obj_get_int(state), c));
    mp_obj_t t[3] = {colour(c[0]), colour(c[1]), colour(c[2])};
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_colors_obj, ui_colors);

// metrics(kind) -> the active skin's tuple for a kind's name, or None.
static mp_obj_t ui_metrics(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    int k = moy_ui_kind_known(s, n);
    int count = 0;
    const int16_t *v = moy_ui_metrics(k, &count);
    if (v == NULL) {
        return mp_const_none;
    }
    mp_obj_t items[MOY_UI_SLOTS];
    for (int i = 0; i < count; i++) {
        items[i] = MP_OBJ_NEW_SMALL_INT(v[i]);
    }
    return mp_obj_new_tuple((size_t)count, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_metrics_obj, ui_metrics);

// text_width(s, scale=1): the pixels `s` draws at `scale` (moy_text.h: bytes).
static mp_obj_t ui_text_width(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *s = mp_obj_str_get_data(args[0], &n);
    int scale = n_args > 1 ? (int)mp_obj_get_int(args[1]) : 1;
    return MP_OBJ_NEW_SMALL_INT(moy_text_width(MOY_FONT_PETME128, (const uint8_t *)s, n, scale));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_width_obj, 1, 2, ui_text_width);

// wrap(s, cols) -> [line, ...]: the toolkit's word wrap (moy_text_wrap's
// words mode), each line its words joined by single spaces.
#define WRAP_LINES 32
static mp_obj_t ui_wrap(mp_obj_t text, mp_obj_t cols_in) {
    size_t n;
    const uint8_t *s = (const uint8_t *)mp_obj_str_get_data(text, &n);
    int cols = (int)mp_obj_get_int(cols_in);
    moy_text_line_t stack[WRAP_LINES];
    moy_text_line_t *lines = stack;
    int count = moy_text_wrap(MOY_FONT_PETME128, s, n, cols, MOY_WRAP_WORDS, lines,
                              WRAP_LINES);
    if (count > WRAP_LINES) {
        lines = m_new(moy_text_line_t, count);
        moy_text_wrap(MOY_FONT_PETME128, s, n, cols, MOY_WRAP_WORDS, lines, count);
    }
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < count; i++) {
        const moy_text_line_t *l = &lines[i];
        if (!l->collapse) {
            mp_obj_list_append(out, mp_obj_new_str((const char *)s + l->off, l->len));
            continue;
        }
        vstr_t v;
        vstr_init_len(&v, l->len);
        v.len = moy_text_collapse(s, l, (uint8_t *)v.buf);
        mp_obj_list_append(out, mp_obj_new_str_from_vstr(&v));
    }
    if (lines != stack) {
        m_del(moy_text_line_t, lines, count);
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_wrap_obj, ui_wrap);

static mp_obj_t ui_glyph_names(void) {
    mp_obj_t items[MOY_UI_GLYPHS];
    for (int i = 0; i < MOY_UI_GLYPHS; i++) {
        items[i] = mp_obj_new_str(moy_ui_glyph_names[i], strlen(moy_ui_glyph_names[i]));
    }
    return mp_obj_new_tuple(MOY_UI_GLYPHS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_glyph_names_obj, ui_glyph_names);

// glyph_id(name) -> its id, or -1.
static mp_obj_t ui_glyph_id(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_glyph_id(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_glyph_id_obj, ui_glyph_id);

// glyph(canvas, g, x, y, w, h, ci, scale): one glyph into a canvas row.
static mp_obj_t ui_glyph(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int rc = moy_ui_row_glyph((uint32_t)mp_obj_get_int(a[0]), mp_obj_get_int(a[1]),
                              mp_obj_get_int(a[2]), mp_obj_get_int(a[3]),
                              mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                              mp_obj_get_int(a[6]), mp_obj_get_int(a[7]));
    if (rc == MOY_UI_STALE) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: stale canvas"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_glyph_obj, 8, 8, ui_glyph);

// -- the primitives -----------------------------------------------------------------

static void sink_of(moy_ui_sink_t *s, mp_obj_t canvas, mp_obj_t look) {
    int rc = moy_ui_sink_row(s, (uint32_t)mp_obj_get_int(canvas), mp_obj_get_int(look));
    if (rc == MOY_UI_STALE) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: stale canvas"));
    }
    check(rc);
}

static int16_t i16(mp_obj_t o) {
    return (int16_t)mp_obj_get_int(o);
}

static int16_t opt(mp_obj_t o, int16_t none) {
    return o == mp_const_none ? none : (int16_t)mp_obj_get_int(o);
}

static moy_ui_span_t span(mp_obj_t o) {
    moy_ui_span_t sp = {NULL, 0};
    if (o != mp_const_none) {
        size_t n;
        sp.p = (const uint8_t *)mp_obj_str_get_data(o, &n);
        sp.n = (uint16_t)(n > 0xFFFF ? 0xFFFF : n);
    }
    return sp;
}

static void colors3(mp_obj_t o, int16_t out[3]) {
    if (o == mp_const_none) {
        out[0] = out[1] = out[2] = MOY_UI_FROM_STYLE;
        return;
    }
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(o, &n, &it);
    for (size_t i = 0; i < 3; i++) {
        out[i] = i < n ? opt(it[i], -1) : -1;
    }
}

static moy_ui_box_t box4(const mp_obj_t *a) {
    moy_ui_box_t b = {i16(a[0]), i16(a[1]), i16(a[2]), i16(a[3])};
    return b;
}

static mp_obj_t box_tuple(moy_ui_box_t b) {
    mp_obj_t t[4] = {MP_OBJ_NEW_SMALL_INT(b.x), MP_OBJ_NEW_SMALL_INT(b.y),
                     MP_OBJ_NEW_SMALL_INT(b.w), MP_OBJ_NEW_SMALL_INT(b.h)};
    return mp_obj_new_tuple(4, t);
}

// row(canvas, look, x, y, w, h, kind, flags, interact, label, value, glyph,
//     glyph_size, glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs,
//     colors, phase)
static mp_obj_t ui_row(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_row_t r;
    r.label = span(a[9]);
    r.value = span(a[10]);
    r.glyph = i16(a[11]);
    r.glyph_size = opt(a[12], MOY_UI_DEFAULT);
    r.glyph_ink = opt(a[13], MOY_UI_FROM_STYLE);
    r.value_ink = opt(a[14], MOY_UI_FROM_STYLE);
    r.pad = opt(a[15], MOY_UI_DEFAULT);
    r.pad_right = opt(a[16], MOY_UI_DEFAULT);
    r.text_dy = opt(a[17], MOY_UI_DEFAULT);
    r.scale = i16(a[18]);
    r.fs = i16(a[19]);
    colors3(a[20], r.colors);
    check(moy_ui_row(&s, box4(a + 2), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
                     mp_obj_get_int(a[8]), &r, mp_obj_get_int(a[21])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_row_obj, 22, 22, ui_row);

// cell(canvas, look, x, y, w, h, kind, flags, interact, label, glyph, pad,
//      caption_h, band_fill, band_ink, text_dx, text_dy, fs, colors, phase)
//      -> the art box
static mp_obj_t ui_cell(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_cell_t k;
    k.label = span(a[9]);
    k.glyph = i16(a[10]);
    k.pad = opt(a[11], MOY_UI_DEFAULT);
    k.caption_h = opt(a[12], MOY_UI_DEFAULT);
    k.band_fill = opt(a[13], MOY_UI_FROM_STYLE);
    k.band_ink = opt(a[14], MOY_UI_FROM_STYLE);
    k.text_dx = opt(a[15], MOY_UI_DEFAULT);
    k.text_dy = opt(a[16], MOY_UI_DEFAULT);
    k.fs = i16(a[17]);
    colors3(a[18], k.colors);
    moy_ui_box_t art;
    check(moy_ui_cell(&s, box4(a + 2), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
                      mp_obj_get_int(a[8]), &k, mp_obj_get_int(a[19]), &art));
    return box_tuple(art);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_cell_obj, 20, 20, ui_cell);

// label(canvas, look, x, y, w, h, text, ink, align) -> the drawn width
static mp_obj_t ui_label(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_label(&s, box4(a + 2), span(a[6]),
                                             mp_obj_get_int(a[7]), mp_obj_get_int(a[8])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_label_obj, 9, 9, ui_label);

// bar_rect(x, y, w, h, content, offset, horizontal, fs, bar_w) -> the thumb, or None
static mp_obj_t ui_bar_rect(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_box_t r;
    if (!moy_ui_bar_rect(box4(a), mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                         mp_obj_is_true(a[6]), mp_obj_get_int(a[7]), mp_obj_get_int(a[8]),
                         &r)) {
        return mp_const_none;
    }
    return box_tuple(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_bar_rect_obj, 9, 9, ui_bar_rect);

// scrollbar(canvas, look, x, y, w, h, content, offset, horizontal, fs, bar_w)
static mp_obj_t ui_scrollbar(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_scrollbar(&s, box4(a + 2), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
                           mp_obj_is_true(a[8]), mp_obj_get_int(a[9]),
                           mp_obj_get_int(a[10])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scrollbar_obj, 11, 11, ui_scrollbar);

// scroll_cues(canvas, ux, uy, dx, dy, can_up, can_dn, ci)
static mp_obj_t ui_scroll_cues(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], MP_OBJ_NEW_SMALL_INT(0));
    check(moy_ui_scroll_cues(&s, mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                             mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                             mp_obj_is_true(a[5]), mp_obj_is_true(a[6]),
                             mp_obj_get_int(a[7])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_cues_obj, 8, 8, ui_scroll_cues);

// fill_uncovered(canvas, ix, iy, iw, ih, ox, oy, ow, oh, ci)
static mp_obj_t ui_fill_uncovered(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], MP_OBJ_NEW_SMALL_INT(0));
    check(moy_ui_fill_uncovered(&s, box4(a + 1), box4(a + 5), mp_obj_get_int(a[9])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_fill_uncovered_obj, 10, 10, ui_fill_uncovered);

// list(canvas, look, x, y, w, h, kind, items, count, row_h, offset, sel, fs, bar_w)
static mp_obj_t ui_list(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[7], &b, MP_BUFFER_READ);
    check(moy_ui_list(&s, box4(a + 2), mp_obj_get_int(a[6]), (const uint8_t *)b.buf, b.len,
                      mp_obj_get_int(a[8]), mp_obj_get_int(a[9]), mp_obj_get_int(a[10]),
                      mp_obj_get_int(a[11]), mp_obj_get_int(a[12]), mp_obj_get_int(a[13])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_list_obj, 14, 14, ui_list);

static mp_obj_t ui_calls(void) {
    return mp_obj_new_int_from_uint(moy_ui_calls());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_calls_obj, ui_calls);

static const mp_rom_map_elem_t moy_ui_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ui) },
    { MP_ROM_QSTR(MP_QSTR_tokens), MP_ROM_PTR(&ui_tokens_obj) },
    { MP_ROM_QSTR(MP_QSTR_states), MP_ROM_PTR(&ui_states_obj) },
    { MP_ROM_QSTR(MP_QSTR_kinds), MP_ROM_PTR(&ui_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR_metric_kinds), MP_ROM_PTR(&ui_metric_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR_kind), MP_ROM_PTR(&ui_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_button_kind), MP_ROM_PTR(&ui_button_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_skins), MP_ROM_PTR(&ui_skins_obj) },
    { MP_ROM_QSTR(MP_QSTR_skin), MP_ROM_PTR(&ui_skin_obj) },
    { MP_ROM_QSTR(MP_QSTR_gen), MP_ROM_PTR(&ui_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_look), MP_ROM_PTR(&ui_look_obj) },
    { MP_ROM_QSTR(MP_QSTR_colors), MP_ROM_PTR(&ui_colors_obj) },
    { MP_ROM_QSTR(MP_QSTR_metrics), MP_ROM_PTR(&ui_metrics_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_width), MP_ROM_PTR(&ui_text_width_obj) },
    { MP_ROM_QSTR(MP_QSTR_wrap), MP_ROM_PTR(&ui_wrap_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph_names), MP_ROM_PTR(&ui_glyph_names_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph_id), MP_ROM_PTR(&ui_glyph_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph), MP_ROM_PTR(&ui_glyph_obj) },
    { MP_ROM_QSTR(MP_QSTR_row), MP_ROM_PTR(&ui_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_cell), MP_ROM_PTR(&ui_cell_obj) },
    { MP_ROM_QSTR(MP_QSTR_label), MP_ROM_PTR(&ui_label_obj) },
    { MP_ROM_QSTR(MP_QSTR_bar_rect), MP_ROM_PTR(&ui_bar_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_scrollbar), MP_ROM_PTR(&ui_scrollbar_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_cues), MP_ROM_PTR(&ui_scroll_cues_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_uncovered), MP_ROM_PTR(&ui_fill_uncovered_obj) },
    { MP_ROM_QSTR(MP_QSTR_list), MP_ROM_PTR(&ui_list_obj) },
    { MP_ROM_QSTR(MP_QSTR_calls), MP_ROM_PTR(&ui_calls_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ui_globals, moy_ui_globals_table);

const mp_obj_module_t moy_ui_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ui_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ui, moy_ui_user_cmodule);
