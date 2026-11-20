// The module `moy_uitree`: the UI engine (moy_uitree.h) as one object a
// front end drives, `Engine()`, its methods those of the host's binding
// (runtime/host/moy_uitree_binding.py's Engine), name for name. A call
// answers what the C answers; a negative answer's text is `error()`.
// `frame(ctx, cv, look, ...)` reaches the toolkit's context and sink through
// the board module's moy_ui_mp_ctx / moy_ui_mp_sink.
#include <string.h>

#include "py/obj.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_uitree.h"

moy_ui_ctx_t *moy_ui_mp_ctx(mp_obj_t ctx);
void moy_ui_mp_sink(moy_ui_sink_t *s, mp_obj_t cv, mp_obj_t look);

typedef struct {
    mp_obj_base_t base;
    uit_t *e;
} eng_obj_t;

static uit_t *eng(mp_obj_t self)
{
    eng_obj_t *o = MP_OBJ_TO_PTR(self);
    if (o->e == NULL)
        mp_raise_ValueError(MP_ERROR_TEXT("moy_uitree: engine freed"));
    return o->e;
}

static const char *str_or_null(mp_obj_t v, size_t *n)
{
    if (v == mp_const_none) {
        if (n)
            *n = 0;
        return NULL;
    }
    size_t m;
    const char *s = mp_obj_is_str(v) ? mp_obj_str_get_data(v, &m) : NULL;
    if (s == NULL) {
        mp_buffer_info_t bi;
        mp_get_buffer_raise(v, &bi, MP_BUFFER_READ);
        s = bi.buf;
        m = bi.len;
    }
    if (n)
        *n = m;
    return s;
}

// A NUL-terminated copy for the C calls that take a const char *: a str's
// data is NUL-terminated in MicroPython, a bytes object's too.
#define CSTR(v) str_or_null((v), NULL)

static mp_obj_t eng_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args)
{
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    (void)args;
    eng_obj_t *o = mp_obj_malloc_with_finaliser(eng_obj_t, type);
    o->e = uit_new();
    if (o->e == NULL)
        mp_raise_type(&mp_type_MemoryError);
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t eng_del(mp_obj_t self)
{
    eng_obj_t *o = MP_OBJ_TO_PTR(self);
    uit_free(o->e);
    o->e = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_del_obj, eng_del);

static mp_obj_t eng_error(mp_obj_t self)
{
    const char *s = uit_error(eng(self));
    return mp_obj_new_str(s, strlen(s));
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_error_obj, eng_error);

static mp_obj_t eng_error_code(mp_obj_t self)
{
    const char *s = uit_error_code(eng(self));
    return mp_obj_new_str(s, strlen(s));
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_error_code_obj, eng_error_code);

static mp_obj_t eng_state(size_t n, const mp_obj_t *a)
{
    (void)n;
    size_t vn;
    const char *v = str_or_null(a[3], &vn);
    return MP_OBJ_NEW_SMALL_INT(uit_state(eng(a[0]), CSTR(a[1]), mp_obj_get_int(a[2]), v, vn));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_state_obj, 4, 4, eng_state);

static mp_obj_t eng_model(size_t n, const mp_obj_t *a)
{
    (void)n;
    size_t nn, tn;
    const char *names = str_or_null(a[2], &nn);
    const char *types = str_or_null(a[3], &tn);
    return MP_OBJ_NEW_SMALL_INT(uit_model(eng(a[0]), mp_obj_get_int(a[1]), names, nn, (const uint8_t *)types,
                                          (int)tn, mp_obj_get_int(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_model_obj, 5, 5, eng_model);

static mp_obj_t eng_row_of(mp_obj_t self, mp_obj_t slot, mp_obj_t model)
{
    return MP_OBJ_NEW_SMALL_INT(uit_row_of(eng(self), mp_obj_get_int(slot), mp_obj_get_int(model)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_row_of_obj, eng_row_of);

static mp_obj_t eng_labels(size_t n, const mp_obj_t *a)
{
    (void)n;
    size_t kn, wn;
    const char *k = str_or_null(a[2], &kn);
    const char *w = str_or_null(a[3], &wn);
    return MP_OBJ_NEW_SMALL_INT(uit_labels(eng(a[0]), CSTR(a[1]), k, kn, w, wn));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_labels_obj, 4, 4, eng_labels);

static mp_obj_t eng_action(size_t n, const mp_obj_t *a)
{
    (void)n;
    return MP_OBJ_NEW_SMALL_INT(uit_action(eng(a[0]), CSTR(a[1]), CSTR(a[2]), CSTR(a[3]), CSTR(a[4]),
                                           mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), CSTR(a[7])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_action_obj, 8, 8, eng_action);

static mp_obj_t eng_set_int(mp_obj_t self, mp_obj_t slot, mp_obj_t v)
{
    return MP_OBJ_NEW_SMALL_INT(uit_set_int(eng(self), mp_obj_get_int(slot), mp_obj_get_int(v)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_set_int_obj, eng_set_int);

static mp_obj_t eng_set_num(mp_obj_t self, mp_obj_t slot, mp_obj_t v)
{
#if MICROPY_PY_BUILTINS_FLOAT
    return MP_OBJ_NEW_SMALL_INT(uit_set_num(eng(self), mp_obj_get_int(slot), mp_obj_get_float(v)));
#else
    return MP_OBJ_NEW_SMALL_INT(uit_set_num(eng(self), mp_obj_get_int(slot), mp_obj_get_int(v)));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_set_num_obj, eng_set_num);

static mp_obj_t eng_set_text(mp_obj_t self, mp_obj_t slot, mp_obj_t v)
{
    size_t vn;
    const char *s = str_or_null(v, &vn);
    return MP_OBJ_NEW_SMALL_INT(uit_set_text(eng(self), mp_obj_get_int(slot), s ? s : "", vn));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_set_text_obj, eng_set_text);

static mp_obj_t eng_set_model(size_t n, const mp_obj_t *a)
{
    (void)n;
    size_t rn;
    const char *raw = str_or_null(a[3], &rn);
    return MP_OBJ_NEW_SMALL_INT(uit_set_model(eng(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                                              raw ? raw : "", rn));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_set_model_obj, 4, 4, eng_set_model);

static mp_obj_t eng_get_int(mp_obj_t self, mp_obj_t slot)
{
    return mp_obj_new_int(uit_get_int(eng(self), mp_obj_get_int(slot)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_get_int_obj, eng_get_int);

static mp_obj_t eng_get_text(mp_obj_t self, mp_obj_t slot)
{
    size_t n = 0;
    const char *s = uit_get_text(eng(self), mp_obj_get_int(slot), &n);
    return s ? mp_obj_new_str(s, n) : MP_OBJ_NEW_QSTR(MP_QSTR_);
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_get_text_obj, eng_get_text);

static mp_obj_t eng_get_cell(size_t n_args, const mp_obj_t *a)
{
    const char *s = uit_get_cell(eng(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]), mp_obj_get_int(a[3]));
    return s ? mp_obj_new_str(s, strlen(s)) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_get_cell_obj, 4, 4, eng_get_cell);

static mp_obj_t eng_build_begin(mp_obj_t self, mp_obj_t pid)
{
    uit_build_begin(eng(self), CSTR(pid));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_build_begin_obj, eng_build_begin);

static mp_obj_t eng_node(size_t n, const mp_obj_t *a)
{
    (void)n;
    return MP_OBJ_NEW_SMALL_INT(uit_node(eng(a[0]), mp_obj_get_int(a[1]), CSTR(a[2]), mp_obj_get_int(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_node_obj, 4, 4, eng_node);

static mp_obj_t eng_prop(size_t n, const mp_obj_t *a)
{
    (void)n;
    return MP_OBJ_NEW_SMALL_INT(uit_prop(eng(a[0]), mp_obj_get_int(a[1]), CSTR(a[2]), CSTR(a[3]), CSTR(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_prop_obj, 5, 5, eng_prop);

static mp_obj_t eng_node_action(mp_obj_t self, mp_obj_t node, mp_obj_t act)
{
    return MP_OBJ_NEW_SMALL_INT(uit_node_action(eng(self), mp_obj_get_int(node), mp_obj_get_int(act)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_node_action_obj, eng_node_action);

static mp_obj_t eng_build_end(mp_obj_t self)
{
    return MP_OBJ_NEW_SMALL_INT(uit_build_end(eng(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_build_end_obj, eng_build_end);

static mp_obj_t eng_open(mp_obj_t self)
{
    uit_open(eng(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_open_obj, eng_open);

static mp_obj_t eng_want_focus(mp_obj_t self, mp_obj_t key)
{
    uit_want_focus(eng(self), CSTR(key));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_want_focus_obj, eng_want_focus);

// frame(ctx, cv, look, x, y, w, h, flags, fs, input) -> events | owe << 30
static mp_obj_t eng_frame(size_t n, const mp_obj_t *a)
{
    (void)n;
    uit_t *e = eng(a[0]);
    moy_ui_ctx_t *c = moy_ui_mp_ctx(a[1]);
    moy_ui_sink_t s;
    moy_ui_mp_sink(&s, a[2], a[3]);
    int r = uit_frame(e, c, &s, mp_obj_get_int(a[4]), mp_obj_get_int(a[5]), mp_obj_get_int(a[6]),
                      mp_obj_get_int(a[7]), mp_obj_get_int(a[8]), mp_obj_get_int(a[9]), mp_obj_get_int(a[10]));
    return mp_obj_new_int(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_frame_obj, 11, 11, eng_frame);

static char evbuf[512];

// next() -> (kind, a, b, c, text), or None
static mp_obj_t eng_next(mp_obj_t self)
{
    int32_t ev[3];
    int k = uit_next(eng(self), ev, evbuf, sizeof evbuf);
    if (k == UIT_EV_NONE)
        return mp_const_none;
    mp_obj_t t[5] = {MP_OBJ_NEW_SMALL_INT(k), MP_OBJ_NEW_SMALL_INT(ev[0]), MP_OBJ_NEW_SMALL_INT(ev[1]),
                     MP_OBJ_NEW_SMALL_INT(ev[2]), mp_obj_new_str(evbuf, strlen(evbuf))};
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_next_obj, eng_next);

static mp_obj_t eng_trace_next(mp_obj_t self)
{
    int n = uit_trace_next(eng(self), evbuf, sizeof evbuf);
    return n < 0 ? mp_const_none : mp_obj_new_str(evbuf, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_trace_next_obj, eng_trace_next);

static mp_obj_t eng_can(mp_obj_t self, mp_obj_t act, mp_obj_t row)
{
    return MP_OBJ_NEW_SMALL_INT(uit_can(eng(self), mp_obj_get_int(act), mp_obj_get_int(row)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_can_obj, eng_can);

static mp_obj_t eng_letter(mp_obj_t self, mp_obj_t ch)
{
    return MP_OBJ_NEW_SMALL_INT(uit_letter(eng(self), mp_obj_get_int(ch)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_letter_obj, eng_letter);

static mp_obj_t eng_back_dialog(mp_obj_t self)
{
    int a = uit_back_dialog(eng(self), evbuf, sizeof evbuf);
    if (a < 0)
        return MP_OBJ_NEW_SMALL_INT(a);
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(a), mp_obj_new_str(evbuf, strlen(evbuf))};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_back_dialog_obj, eng_back_dialog);

static mp_obj_t eng_dialog(mp_obj_t self)
{
    return MP_OBJ_NEW_SMALL_INT(uit_dialog(eng(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_dialog_obj, eng_dialog);

static mp_obj_t eng_unfocused(mp_obj_t self)
{
    return MP_OBJ_NEW_SMALL_INT(uit_unfocused(eng(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_unfocused_obj, eng_unfocused);

static mp_obj_t eng_focused(mp_obj_t self)
{
    int n = uit_focused(eng(self), evbuf, sizeof evbuf);
    return n ? mp_obj_new_str(evbuf, (size_t)n) : MP_OBJ_NEW_QSTR(MP_QSTR_);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_focused_obj, eng_focused);

static mp_obj_t eng_stop(mp_obj_t self, mp_obj_t i)
{
    int k = uit_stop(eng(self), mp_obj_get_int(i), evbuf, sizeof evbuf);
    if (k < 0)
        return mp_const_none;
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(k), mp_obj_new_str(evbuf, strlen(evbuf))};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_stop_obj, eng_stop);

static mp_obj_t eng_draft(mp_obj_t self, mp_obj_t key)
{
    size_t n = 0;
    const char *s = uit_draft(eng(self), CSTR(key), &n);
    return s ? mp_obj_new_str(s, n) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_draft_obj, eng_draft);

static mp_obj_t eng_leave(mp_obj_t self)
{
    return MP_OBJ_NEW_SMALL_INT(uit_leave(eng(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_leave_obj, eng_leave);

static mp_obj_t eng_dump(mp_obj_t self)
{
    uit_t *e = eng(self);
    int n = uit_dump(e, NULL, 0);
    vstr_t v;
    vstr_init_len(&v, (size_t)n);
    uit_dump(e, v.buf, (size_t)n + 1);
    return mp_obj_new_str_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_dump_obj, eng_dump);

static mp_obj_t eng_stats(mp_obj_t self)
{
    int32_t out[8];
    uit_stats(eng(self), out);
    mp_obj_t t[8];
    for (int i = 0; i < 8; i++)
        t[i] = mp_obj_new_int(out[i]);
    return mp_obj_new_tuple(8, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_stats_obj, eng_stats);

static const mp_rom_map_elem_t eng_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&eng_del_obj) },
    { MP_ROM_QSTR(MP_QSTR_error), MP_ROM_PTR(&eng_error_obj) },
    { MP_ROM_QSTR(MP_QSTR_error_code), MP_ROM_PTR(&eng_error_code_obj) },
    { MP_ROM_QSTR(MP_QSTR_state), MP_ROM_PTR(&eng_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_model), MP_ROM_PTR(&eng_model_obj) },
    { MP_ROM_QSTR(MP_QSTR_row_of), MP_ROM_PTR(&eng_row_of_obj) },
    { MP_ROM_QSTR(MP_QSTR_action), MP_ROM_PTR(&eng_action_obj) },
    { MP_ROM_QSTR(MP_QSTR_labels), MP_ROM_PTR(&eng_labels_obj) },
    { MP_ROM_QSTR(MP_QSTR_focused), MP_ROM_PTR(&eng_focused_obj) },
    { MP_ROM_QSTR(MP_QSTR_draft), MP_ROM_PTR(&eng_draft_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&eng_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_leave), MP_ROM_PTR(&eng_leave_obj) },
    { MP_ROM_QSTR(MP_QSTR_letter), MP_ROM_PTR(&eng_letter_obj) },
    { MP_ROM_QSTR(MP_QSTR_back_dialog), MP_ROM_PTR(&eng_back_dialog_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_int), MP_ROM_PTR(&eng_set_int_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_num), MP_ROM_PTR(&eng_set_num_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_text), MP_ROM_PTR(&eng_set_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_model), MP_ROM_PTR(&eng_set_model_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_int), MP_ROM_PTR(&eng_get_int_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_text), MP_ROM_PTR(&eng_get_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_cell), MP_ROM_PTR(&eng_get_cell_obj) },
    { MP_ROM_QSTR(MP_QSTR_build_begin), MP_ROM_PTR(&eng_build_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_node), MP_ROM_PTR(&eng_node_obj) },
    { MP_ROM_QSTR(MP_QSTR_prop), MP_ROM_PTR(&eng_prop_obj) },
    { MP_ROM_QSTR(MP_QSTR_node_action), MP_ROM_PTR(&eng_node_action_obj) },
    { MP_ROM_QSTR(MP_QSTR_build_end), MP_ROM_PTR(&eng_build_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&eng_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_want_focus), MP_ROM_PTR(&eng_want_focus_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame), MP_ROM_PTR(&eng_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_next), MP_ROM_PTR(&eng_next_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_next), MP_ROM_PTR(&eng_trace_next_obj) },
    { MP_ROM_QSTR(MP_QSTR_can), MP_ROM_PTR(&eng_can_obj) },
    { MP_ROM_QSTR(MP_QSTR_dialog), MP_ROM_PTR(&eng_dialog_obj) },
    { MP_ROM_QSTR(MP_QSTR_unfocused), MP_ROM_PTR(&eng_unfocused_obj) },
    { MP_ROM_QSTR(MP_QSTR_dump), MP_ROM_PTR(&eng_dump_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&eng_stats_obj) },
};
static MP_DEFINE_CONST_DICT(eng_locals, eng_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    eng_type, MP_QSTR_Engine, MP_TYPE_FLAG_NONE,
    make_new, eng_make_new,
    locals_dict, &eng_locals
    );

static const mp_rom_map_elem_t uit_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_uitree) },
    { MP_ROM_QSTR(MP_QSTR_Engine), MP_ROM_PTR(&eng_type) },
};
static MP_DEFINE_CONST_DICT(uit_globals, uit_globals_table);

const mp_obj_module_t moy_uitree_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&uit_globals,
};
MP_REGISTER_MODULE(MP_QSTR_moy_uitree, moy_uitree_module);
