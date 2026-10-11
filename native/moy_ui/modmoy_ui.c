// The module `moy_ui` (moy_ui.h): what runtime/ui.py's face calls. Names in,
// ids out once; after that the face passes ints, and caches what it gets
// back, so a widget draw reaches here only on a cache miss.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"

#include "moy_ui.h"
#include "moy_str.h"
#include "moy_ui_wasm.h"

static mp_obj_t names_tuple(const char *const *names, int n) {
    mp_obj_t items[MOY_UI_KINDS > MOY_UI_TOKENS ? MOY_UI_KINDS : MOY_UI_TOKENS];
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

// -- the strings C draws (native/moy_spine/moy_str.h) ------------------------------

static int str_id(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    int id = moy_str_id(s, n);
    if (id < 0) {
        mp_raise_type_arg(&mp_type_KeyError, name);
    }
    return id;
}

// s(id) -> the id's text: the pack's entry, else English.
static mp_obj_t ui_s(mp_obj_t name) {
    const char *t = moy_str(str_id(name));
    return mp_obj_new_str(t, strlen(t));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_s_obj, ui_s);

// str_set(id, text) -> True, or False when the pack's entry is refused (its
// conversions differ from English's: the id reads English). None removes it.
static mp_obj_t ui_str_set(mp_obj_t name, mp_obj_t text) {
    int id = str_id(name);
    int rc;
    if (text == mp_const_none) {
        rc = moy_str_set(id, NULL, 0);
    } else {
        size_t n;
        const char *t = mp_obj_str_get_data(text, &n);
        rc = moy_str_set(id, t, n);
    }
    if (rc == MOY_STR_NOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    return mp_obj_new_bool(rc == MOY_STR_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_str_set_obj, ui_str_set);

// str_reset(): every pack entry removed; the table reads English.
static mp_obj_t ui_str_reset(void) {
    moy_str_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_str_reset_obj, ui_str_reset);

// -- the canvas, the box, the text and the colours as the faces pass them --------------
//
// A face passes the canvas object itself, its rect as it has it and its
// text and colours raw: the conversions are here, in C, because on a board
// every Python call a face makes costs more than the widget it draws.

// The sink of canvas `cv` (its `_ui`: the CANVAS handle and the sprite
// queue), once the pending sprite batch has landed (#63's order rule).
static void sink_of(moy_ui_sink_t *s, mp_obj_t cv, mp_obj_t look) {
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(mp_load_attr(cv, MP_QSTR__ui), &n, &it);
    if (n < 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: not a canvas"));
    }
    mp_buffer_info_t b;
    mp_get_buffer_raise(it[1], &b, MP_BUFFER_READ);
    if (b.len >= 2 && ((const int16_t *)b.buf)[0] > 4) {
        mp_obj_t dest[2];
        mp_load_method(cv, MP_QSTR_flush_batch, dest);
        mp_call_method_n_kw(0, 0, dest);
    }
    int rc = moy_ui_sink_row(s, (uint32_t)mp_obj_get_int(it[0]),
                             look == mp_const_none ? 0 : mp_obj_get_int(look));
    if (rc == MOY_UI_STALE) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: stale canvas"));
    }
    check(rc);
}

// A coordinate, converted toward zero as the print gate's gate_num does.
static int32_t num(mp_obj_t o) {
    if (mp_obj_is_small_int(o)) {
        return (int32_t)MP_OBJ_SMALL_INT_VALUE(o);
    }
#if MICROPY_PY_BUILTINS_FLOAT
    if (mp_obj_is_float(o)) {
        return (int32_t)mp_obj_get_float(o);
    }
#endif
    return (int32_t)mp_obj_get_int(o);
}

static void rect_of(mp_obj_t o, int32_t r[4]) {
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(o, &n, &it);
    if (n < 4) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: a rect is (x, y, w, h)"));
    }
    for (int i = 0; i < 4; i++) {
        r[i] = num(it[i]);
    }
}

static moy_ui_box_t box_of(mp_obj_t o) {
    int32_t r[4];
    rect_of(o, r);
    moy_ui_box_t b = {(int16_t)r[0], (int16_t)r[1], (int16_t)r[2], (int16_t)r[3]};
    return b;
}

static int16_t i16(mp_obj_t o) {
    return (int16_t)num(o);
}

static int16_t opt(mp_obj_t o, int16_t none) {
    return o == mp_const_none ? none : (int16_t)num(o);
}

// A colour: the style's (None), else the index the canvas would mask it to.
static int16_t col(mp_obj_t o, int16_t none) {
    return o == mp_const_none ? none : (int16_t)(mp_obj_get_int(o) & 63);
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
        out[i] = i < n ? col(it[i], -1) : -1;
    }
}

// Text: a str as it is; anything else as str() makes it (None reads "").
static moy_ui_span_t span_of(mp_obj_t o) {
    moy_ui_span_t sp = {NULL, 0};
    if (o == mp_const_none) {
        return sp;
    }
    if (!mp_obj_is_str(o)) {
        o = mp_obj_str_make_new(&mp_type_str, 1, 0, &o);
    }
    size_t n;
    sp.p = (const uint8_t *)mp_obj_str_get_data(o, &n);
    sp.n = (uint16_t)(n > 0xFFFF ? 0xFFFF : n);
    return sp;
}

// A label a falsy value hides (row's `if label:`).
static moy_ui_span_t span_if(mp_obj_t o) {
    if (!mp_obj_is_true(o)) {
        moy_ui_span_t sp = {(const uint8_t *)"", 0};
        return sp;
    }
    return span_of(o);
}

static mp_obj_t box_tuple(moy_ui_box_t b) {
    mp_obj_t t[4] = {MP_OBJ_NEW_SMALL_INT(b.x), MP_OBJ_NEW_SMALL_INT(b.y),
                     MP_OBJ_NEW_SMALL_INT(b.w), MP_OBJ_NEW_SMALL_INT(b.h)};
    return mp_obj_new_tuple(4, t);
}

static mp_obj_t rect_tuple(const int32_t r[4]) {
    mp_obj_t t[4] = {mp_obj_new_int(r[0]), mp_obj_new_int(r[1]), mp_obj_new_int(r[2]),
                     mp_obj_new_int(r[3])};
    return mp_obj_new_tuple(4, t);
}

static int fs_or(mp_obj_t o, const moy_ui_sink_t *s) {
    return o == mp_const_none ? s->fs : (int)num(o);
}

// -- glyphs ---------------------------------------------------------------------------

// glyph(cv, g, rect, ci, scale): scale None is the canvas's font scale
static mp_obj_t ui_glyph(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    moy_ui_box_t b = box_of(a[2]);
    check(moy_ui_sink_glyph(&s, mp_obj_get_int(a[1]), b.x, b.y, b.w, b.h,
                            mp_obj_get_int(a[3]), fs_or(a[4], &s)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_glyph_obj, 5, 5, ui_glyph);

// -- rows, cells and lists ---------------------------------------------------------------

// row(cv, look, rect, kind, flags, interact, label, value, glyph, glyph_size,
//     glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs, colors, phase)
static mp_obj_t ui_row(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_row_t r;
    r.label = span_if(a[6]);
    r.value = span_of(a[7]);
    r.glyph = i16(a[8]);
    r.glyph_size = opt(a[9], MOY_UI_DEFAULT);
    r.glyph_ink = col(a[10], MOY_UI_FROM_STYLE);
    r.value_ink = col(a[11], MOY_UI_FROM_STYLE);
    r.pad = opt(a[12], MOY_UI_DEFAULT);
    r.pad_right = opt(a[13], MOY_UI_DEFAULT);
    r.text_dy = opt(a[14], MOY_UI_DEFAULT);
    r.scale = i16(a[15]);
    r.fs = (int16_t)fs_or(a[16], &s);
    colors3(a[17], r.colors);
    check(moy_ui_row(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                     mp_obj_get_int(a[5]), &r, mp_obj_get_int(a[18])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_row_obj, 19, 19, ui_row);

// cell(cv, look, rect, kind, flags, interact, label, glyph, pad, caption_h,
//      band_fill, band_ink, text_dx, text_dy, fs, colors, phase) -> the art box
static mp_obj_t ui_cell(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_cell_t k;
    k.label = span_of(a[6]);
    k.glyph = i16(a[7]);
    k.pad = opt(a[8], MOY_UI_DEFAULT);
    k.caption_h = opt(a[9], MOY_UI_DEFAULT);
    k.band_fill = col(a[10], MOY_UI_FROM_STYLE);
    k.band_ink = col(a[11], MOY_UI_FROM_STYLE);
    k.text_dx = opt(a[12], MOY_UI_DEFAULT);
    k.text_dy = opt(a[13], MOY_UI_DEFAULT);
    k.fs = (int16_t)fs_or(a[14], &s);
    colors3(a[15], k.colors);
    moy_ui_box_t art;
    check(moy_ui_cell(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                      mp_obj_get_int(a[5]), &k, mp_obj_get_int(a[16]), &art));
    return box_tuple(art);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_cell_obj, 17, 17, ui_cell);

// label(cv, rect, text, ink, align) -> the drawn width
static mp_obj_t ui_label(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_label(&s, box_of(a[1]), span_if(a[2]),
                                             mp_obj_get_int(a[3]), mp_obj_get_int(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_label_obj, 5, 5, ui_label);

// bar_rect(view, content, offset, horizontal, fs, bar_w) -> the thumb, or None
static mp_obj_t ui_bar_rect(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_box_t r;
    if (!moy_ui_bar_rect(box_of(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                         mp_obj_is_true(a[3]), mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                         &r)) {
        return mp_const_none;
    }
    return box_tuple(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_bar_rect_obj, 6, 6, ui_bar_rect);

// scrollbar(cv, look, view, content, offset, horizontal, bar_w)
static mp_obj_t ui_scrollbar(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_scrollbar(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                           mp_obj_is_true(a[5]), s.fs, mp_obj_get_int(a[6])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scrollbar_obj, 7, 7, ui_scrollbar);

// scroll_cues(cv, up_xy, dn_xy, can_up, can_dn, ci)
static mp_obj_t ui_scroll_cues(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int up = mp_obj_is_true(a[3]), dn = mp_obj_is_true(a[4]);
    if (!up && !dn) {
        return mp_const_none;
    }
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    size_t n;
    mp_obj_t *u, *d;
    mp_obj_get_array(a[1], &n, &u);
    mp_obj_get_array(a[2], &n, &d);
    check(moy_ui_scroll_cues(&s, num(u[0]), num(u[1]), num(d[0]), num(d[1]), up, dn,
                             mp_obj_get_int(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_cues_obj, 6, 6, ui_scroll_cues);

// fill_uncovered(cv, inner, outer, ci)
static mp_obj_t ui_fill_uncovered(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_fill_uncovered(&s, box_of(a[1]), box_of(a[2]), mp_obj_get_int(a[3])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_fill_uncovered_obj, 4, 4, ui_fill_uncovered);

// list(cv, look, rect, kind, items, count, row_h, offset, sel, bar_w)
static mp_obj_t ui_list(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[4], &b, MP_BUFFER_READ);
    check(moy_ui_list(&s, box_of(a[2]), mp_obj_get_int(a[3]), (const uint8_t *)b.buf, b.len,
                      mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
                      mp_obj_get_int(a[8]), s.fs, mp_obj_get_int(a[9])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_list_obj, 10, 10, ui_list);

// -- buttons and frames --------------------------------------------------------------------

// button(cv, look, rect, kind, flags, interact, label, glyph, phase)
static mp_obj_t ui_button(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_button_t k;
    k.label = span_of(a[6]);
    k.glyph = i16(a[7]);
    k.fs = s.fs;
    k.colors[0] = k.colors[1] = k.colors[2] = MOY_UI_FROM_STYLE;
    check(moy_ui_button(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                        mp_obj_get_int(a[5]), &k, mp_obj_get_int(a[8])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_button_obj, 9, 9, ui_button);

// button_x(rect, flags, label, fs): where its icon or glyph lands
static mp_obj_t ui_button_x(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return MP_OBJ_NEW_SMALL_INT(moy_ui_button_x(box_of(a[0]), mp_obj_get_int(a[1]),
                                                span_of(a[2]), mp_obj_get_int(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_button_x_obj, 4, 4, ui_button_x);

// chip(cv, look, rect, kind, flags, interact, label, glyph, fs, colors, phase)
static mp_obj_t ui_chip(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_button_t k;
    k.label = span_of(a[6]);
    k.glyph = i16(a[7]);
    k.fs = (int16_t)fs_or(a[8], &s);
    colors3(a[9], k.colors);
    check(moy_ui_chip(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                      mp_obj_get_int(a[5]), &k, mp_obj_get_int(a[10])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_chip_obj, 11, 11, ui_chip);

#define TABS 32
// tab_rects(rect, labels, count, fs, gap) -> (labels_on, [box, ...])
static mp_obj_t ui_tab_rects(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[1], &b, MP_BUFFER_READ);
    moy_ui_box_t out[TABS];
    int on = 0;
    int count = mp_obj_get_int(a[2]);
    int n = moy_ui_tab_rects(box_of(a[0]), (const uint8_t *)b.buf, b.len,
                             count > TABS ? TABS : count, mp_obj_get_int(a[3]),
                             opt(a[4], MOY_UI_DEFAULT), out, &on);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        mp_obj_list_append(list, box_tuple(out[i]));
    }
    mp_obj_t t[2] = {mp_obj_new_bool(on), list};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_tab_rects_obj, 5, 5, ui_tab_rects);

// tab(cv, look, rect, active, labels_on, label, ink, phase)
static mp_obj_t ui_tab(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_tab(&s, box_of(a[2]), mp_obj_is_true(a[3]), mp_obj_is_true(a[4]),
                     span_of(a[5]), col(a[6], MOY_UI_FROM_STYLE), s.fs,
                     mp_obj_get_int(a[7])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_tab_obj, 8, 8, ui_tab);

// status(cv, look, rect, items, count)
static mp_obj_t ui_status(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[3], &b, MP_BUFFER_READ);
    check(moy_ui_status(&s, box_of(a[2]), (const uint8_t *)b.buf, b.len, mp_obj_get_int(a[4]),
                        s.fs));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_status_obj, 5, 5, ui_status);

// game_btn(cv, rect, label, fill)
static mp_obj_t ui_game_btn(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_game_btn(&s, box_of(a[1]), span_of(a[2]), mp_obj_get_int(a[3]), s.fs));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_game_btn_obj, 4, 4, ui_game_btn);

// game_icon_btn(cv, rect, glyph, label, fill, phase)
static mp_obj_t ui_game_icon_btn(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_game_icon_btn(&s, box_of(a[1]), mp_obj_get_int(a[2]), span_if(a[3]),
                               mp_obj_get_int(a[4]), s.fs, mp_obj_get_int(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_game_icon_btn_obj, 6, 6, ui_game_icon_btn);

// mini_btn(cv, rect, label, fill, label_rect)
static mp_obj_t ui_mini_btn(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    moy_ui_box_t b = box_of(a[1]);
    moy_ui_box_t l = a[4] == mp_const_none ? b : box_of(a[4]);
    check(moy_ui_mini_btn(&s, b, span_of(a[2]), mp_obj_get_int(a[3]), l.x, l.y));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_mini_btn_obj, 5, 5, ui_mini_btn);

// toolbar(cv, look, rect)
static mp_obj_t ui_toolbar(mp_obj_t cv, mp_obj_t look, mp_obj_t rect) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    check(moy_ui_toolbar(&s, box_of(rect)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_toolbar_obj, ui_toolbar);

// dialog(cv, rect, ring, fill)
static mp_obj_t ui_dialog(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_dialog(&s, box_of(a[1]), col(a[2], MOY_UI_FROM_STYLE),
                        col(a[3], MOY_UI_FROM_STYLE)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_dialog_obj, 4, 4, ui_dialog);

// text_field(cv, rect, text, hint, colors, fs): fs None is 1 (the quirk)
static mp_obj_t ui_text_field(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    int16_t c[5];
    const int16_t *cp = NULL;
    if (a[4] != mp_const_none) {
        size_t n;
        mp_obj_t *it;
        mp_obj_get_array(a[4], &n, &it);
        for (size_t i = 0; i < 5; i++) {
            c[i] = i < n ? col(it[i], -1) : -1;
        }
        cp = c;
    }
    check(moy_ui_text_field(&s, box_of(a[1]), span_if(a[2]), span_if(a[3]), cp,
                            a[5] == mp_const_none ? 1 : (int)num(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_field_obj, 6, 6, ui_text_field);

// panel(cv, look, rect, title, fs) -> the content box
static mp_obj_t ui_panel(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_box_t ct;
    check(moy_ui_panel(&s, box_of(a[2]), span_if(a[3]), fs_or(a[4], &s), &ct));
    return box_tuple(ct);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_panel_obj, 5, 5, ui_panel);

// focus_ring(cv, look, rect, fs)
static mp_obj_t ui_focus_ring(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_focus_ring(&s, box_of(a[2]), fs_or(a[3], &s)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_focus_ring_obj, 4, 4, ui_focus_ring);

// -- rect algebra ---------------------------------------------------------------------------

// inset(rect, dx, dy)
static mp_obj_t ui_inset(mp_obj_t rect, mp_obj_t dx, mp_obj_t dy) {
    int32_t r[4], o[4];
    rect_of(rect, r);
    moy_ui_inset32(r, num(dx), num(dy), o);
    return rect_tuple(o);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_inset_obj, ui_inset);

// cut(rect, side, n) -> (band, rest)
static mp_obj_t ui_cut(mp_obj_t rect, mp_obj_t side, mp_obj_t n) {
    int32_t r[4], band[4], rest[4];
    rect_of(rect, r);
    moy_ui_cut(r, mp_obj_get_int(side), num(n), band, rest);
    mp_obj_t t[2] = {rect_tuple(band), rect_tuple(rest)};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_cut_obj, ui_cut);

#define SPLITS 64
// split(rect, n, gap, horizontal) -> [rect, ...]
static mp_obj_t ui_split(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t r[4];
    rect_of(a[0], r);
    int n = mp_obj_get_int(a[1]);
    int32_t out[SPLITS][4];
    int got = moy_ui_split(r, n > SPLITS ? SPLITS : n, num(a[2]), mp_obj_is_true(a[3]),
                           out, SPLITS);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (int i = 0; i < got && i < SPLITS; i++) {
        mp_obj_list_append(list, rect_tuple(out[i]));
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_split_obj, 4, 4, ui_split);

// row_content(rect, fs, pad, pad_right, icon, glyph, glyph_size)
static mp_obj_t ui_row_content(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t r[4], o[4];
    rect_of(a[0], r);
    moy_ui_row_content(r, mp_obj_get_int(a[1]), opt(a[2], MOY_UI_DEFAULT),
                       opt(a[3], MOY_UI_DEFAULT), mp_obj_is_true(a[4]), mp_obj_is_true(a[5]),
                       opt(a[6], MOY_UI_DEFAULT), o);
    return rect_tuple(o);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_row_content_obj, 7, 7, ui_row_content);

// cell_art(rect, fs, pad, caption_h)
static mp_obj_t ui_cell_art(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t r[4], o[4];
    rect_of(a[0], r);
    moy_ui_cell_art(r, mp_obj_get_int(a[1]), opt(a[2], MOY_UI_DEFAULT), num(a[3]), o);
    return rect_tuple(o);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_cell_art_obj, 4, 4, ui_cell_art);


// -- interaction over caller-owned buffers ------------------------------------------------

static void *rw(mp_obj_t o, size_t need, size_t *len) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_RW);
    if (b.len < need) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: state buffer too small"));
    }
    if (len != NULL) {
        *len = b.len;
    }
    return b.buf;
}

static mp_obj_t ui_hits_cap(void) {
    return MP_OBJ_NEW_SMALL_INT(MOY_UI_HITS_CAP);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_hits_cap_obj, ui_hits_cap);

static mp_obj_t ui_hits_clear(mp_obj_t buf) {
    moy_ui_hits_clear(rw(buf, MOY_UI_HITS_HDR * 4, NULL));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_hits_clear_obj, ui_hits_clear);

// hits_add(buf, rect, id) -> False when full
static mp_obj_t ui_hits_add(mp_obj_t buf, mp_obj_t rect, mp_obj_t id) {
    size_t len;
    int32_t *b = rw(buf, MOY_UI_HITS_HDR * 4, &len);
    int32_t r[4];
    rect_of(rect, r);
    return mp_obj_new_bool(moy_ui_hits_add(b, len / 4, r[0], r[1], r[2], r[3],
                                           mp_obj_get_int(id)) == MOY_UI_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_hits_add_obj, ui_hits_add);

static mp_obj_t ui_hits_at(mp_obj_t buf, mp_obj_t px, mp_obj_t py) {
    return MP_OBJ_NEW_SMALL_INT(moy_ui_hits_at(rw(buf, MOY_UI_HITS_HDR * 4, NULL), num(px),
                                               num(py)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_hits_at_obj, ui_hits_at);

// hits_pump(buf, px, py, down, pointing, pump): pump is an array('i') of
// (hover, pressed, armed, down), read and written in place.
static mp_obj_t ui_hits_pump(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_pump_t *p = rw(a[5], sizeof(moy_ui_pump_t), NULL);
    moy_ui_hits_pump(rw(a[0], MOY_UI_HITS_HDR * 4, NULL), num(a[1]), num(a[2]),
                     mp_obj_is_true(a[3]), mp_obj_is_true(a[4]), p);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_hits_pump_obj, 6, 6, ui_hits_pump);

static float dt_of(mp_obj_t o) {
    return o == mp_const_none ? 0.0f : (float)mp_obj_get_float(o);
}

static moy_ui_scroll_t *scroll_of(mp_obj_t o) {
    return rw(o, sizeof(moy_ui_scroll_t), NULL);
}

static mp_obj_t ui_scroll_stop(mp_obj_t st) {
    moy_ui_scroll_stop(scroll_of(st));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_scroll_stop_obj, ui_scroll_stop);

static mp_obj_t ui_scroll_drag_start(mp_obj_t st, mp_obj_t p) {
    moy_ui_scroll_drag_start(scroll_of(st), num(p));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_scroll_drag_start_obj, ui_scroll_drag_start);

// scroll_drag_move(st, p, dt, offset, content, extent) -> the new offset, or
// -1 when no drag is live
static mp_obj_t ui_scroll_drag_move(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t off = num(a[3]);
    if (!moy_ui_scroll_drag_move(scroll_of(a[0]), num(a[1]), dt_of(a[2]), &off,
                                 num(a[4]), num(a[5]))) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    return mp_obj_new_int(off);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_drag_move_obj, 6, 6, ui_scroll_drag_move);

static mp_obj_t ui_scroll_drag_end(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_scroll_drag_end(scroll_of(a[0]), num(a[1]), num(a[2]), num(a[3]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_drag_end_obj, 4, 4, ui_scroll_drag_end);

// scroll_tick(st, dt, offset, content, extent) -> the new offset, or -1 when
// nothing coasts
static mp_obj_t ui_scroll_tick(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t off = num(a[2]);
    if (!moy_ui_scroll_tick(scroll_of(a[0]), dt_of(a[1]), &off, num(a[3]), num(a[4]))) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    return mp_obj_new_int(off);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_tick_obj, 5, 5, ui_scroll_tick);

// dragtap(tap, st, view, horizontal, px, py, click, down, slop, dt, offset,
//         content, out) -> True on a clean tap; out (array('i')) gets the
// offset and the tap's x and y
static mp_obj_t ui_dragtap(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_dragtap_t *t = rw(a[0], sizeof(moy_ui_dragtap_t), NULL);
    int32_t view[4];
    rect_of(a[2], view);
    int32_t *out = rw(a[12], 3 * sizeof(int32_t), NULL);
    out[0] = num(a[10]);
    int tapped = moy_ui_dragtap_frame(t, scroll_of(a[1]), view, mp_obj_is_true(a[3]),
                                      num(a[4]), num(a[5]), mp_obj_is_true(a[6]),
                                      mp_obj_is_true(a[7]), num(a[8]), dt_of(a[9]),
                                      &out[0], num(a[11]), &out[1], &out[2]);
    return mp_obj_new_bool(tapped);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_dragtap_obj, 13, 13, ui_dragtap);

// wasm_rows() -> the toolkit's rows a compiled app imports ("ui_button", ...),
// in roles.json's order: what this image serves (moy_ui_wasm.h).
static mp_obj_t ui_wasm_rows(void) {
    mp_obj_t items[MOY_UI_WASM_ROWS];
    for (int i = 0; i < MOY_UI_WASM_ROWS; i++) {
        const char *n = moy_ui_wasm_name(i);
        items[i] = mp_obj_new_str(n, strlen(n));
    }
    return mp_obj_new_tuple(MOY_UI_WASM_ROWS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_wasm_rows_obj, ui_wasm_rows);

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
    { MP_ROM_QSTR(MP_QSTR_s), MP_ROM_PTR(&ui_s_obj) },
    { MP_ROM_QSTR(MP_QSTR_wasm_rows), MP_ROM_PTR(&ui_wasm_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_str_set), MP_ROM_PTR(&ui_str_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_str_reset), MP_ROM_PTR(&ui_str_reset_obj) },
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
    { MP_ROM_QSTR(MP_QSTR_hits_cap), MP_ROM_PTR(&ui_hits_cap_obj) },
    { MP_ROM_QSTR(MP_QSTR_hits_clear), MP_ROM_PTR(&ui_hits_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_hits_add), MP_ROM_PTR(&ui_hits_add_obj) },
    { MP_ROM_QSTR(MP_QSTR_hits_at), MP_ROM_PTR(&ui_hits_at_obj) },
    { MP_ROM_QSTR(MP_QSTR_hits_pump), MP_ROM_PTR(&ui_hits_pump_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_stop), MP_ROM_PTR(&ui_scroll_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_drag_start), MP_ROM_PTR(&ui_scroll_drag_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_drag_move), MP_ROM_PTR(&ui_scroll_drag_move_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_drag_end), MP_ROM_PTR(&ui_scroll_drag_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_tick), MP_ROM_PTR(&ui_scroll_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_dragtap), MP_ROM_PTR(&ui_dragtap_obj) },
    { MP_ROM_QSTR(MP_QSTR_button), MP_ROM_PTR(&ui_button_obj) },
    { MP_ROM_QSTR(MP_QSTR_button_x), MP_ROM_PTR(&ui_button_x_obj) },
    { MP_ROM_QSTR(MP_QSTR_chip), MP_ROM_PTR(&ui_chip_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab_rects), MP_ROM_PTR(&ui_tab_rects_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab), MP_ROM_PTR(&ui_tab_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&ui_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_game_btn), MP_ROM_PTR(&ui_game_btn_obj) },
    { MP_ROM_QSTR(MP_QSTR_game_icon_btn), MP_ROM_PTR(&ui_game_icon_btn_obj) },
    { MP_ROM_QSTR(MP_QSTR_mini_btn), MP_ROM_PTR(&ui_mini_btn_obj) },
    { MP_ROM_QSTR(MP_QSTR_toolbar), MP_ROM_PTR(&ui_toolbar_obj) },
    { MP_ROM_QSTR(MP_QSTR_dialog), MP_ROM_PTR(&ui_dialog_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_field), MP_ROM_PTR(&ui_text_field_obj) },
    { MP_ROM_QSTR(MP_QSTR_panel), MP_ROM_PTR(&ui_panel_obj) },
    { MP_ROM_QSTR(MP_QSTR_focus_ring), MP_ROM_PTR(&ui_focus_ring_obj) },
    { MP_ROM_QSTR(MP_QSTR_inset), MP_ROM_PTR(&ui_inset_obj) },
    { MP_ROM_QSTR(MP_QSTR_cut), MP_ROM_PTR(&ui_cut_obj) },
    { MP_ROM_QSTR(MP_QSTR_split), MP_ROM_PTR(&ui_split_obj) },
    { MP_ROM_QSTR(MP_QSTR_row_content), MP_ROM_PTR(&ui_row_content_obj) },
    { MP_ROM_QSTR(MP_QSTR_cell_art), MP_ROM_PTR(&ui_cell_art_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ui_globals, moy_ui_globals_table);

const mp_obj_module_t moy_ui_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ui_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ui, moy_ui_user_cmodule);
