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
};
static MP_DEFINE_CONST_DICT(moy_ui_globals, moy_ui_globals_table);

const mp_obj_module_t moy_ui_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ui_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ui, moy_ui_user_cmodule);
