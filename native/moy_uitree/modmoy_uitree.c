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
    mp_obj_t draw;              // on_draw's function: (node, x, y, w, h)
    mp_obj_t raised;            // what it raised inside a frame, raised again after it
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
    o->draw = o->raised = MP_OBJ_NULL;
    if (o->e == NULL)
        mp_raise_type(&mp_type_MemoryError);
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t eng_del(mp_obj_t self)
{
    eng_obj_t *o = MP_OBJ_TO_PTR(self);
    uit_free(o->e);
    o->e = NULL;
    o->draw = o->raised = MP_OBJ_NULL;
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

static mp_obj_t eng_action_row(mp_obj_t self, mp_obj_t a, mp_obj_t slot)
{
    return MP_OBJ_NEW_SMALL_INT(uit_action_row(eng(self), mp_obj_get_int(a), mp_obj_get_int(slot)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_action_row_obj, eng_action_row);

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
                                           mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), CSTR(a[7]), CSTR(a[8])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_action_obj, 9, 9, eng_action);

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

static mp_obj_t eng_set_absent(mp_obj_t self, mp_obj_t slot)
{
    return MP_OBJ_NEW_SMALL_INT(uit_set_absent(eng(self), mp_obj_get_int(slot)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_set_absent_obj, eng_set_absent);

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

static mp_obj_t eng_close(mp_obj_t self)
{
    uit_close(eng(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_close_obj, eng_close);

static mp_obj_t eng_want_focus(mp_obj_t self, mp_obj_t key)
{
    uit_want_focus(eng(self), CSTR(key));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_want_focus_obj, eng_want_focus);

static mp_obj_t eng_set_placed(mp_obj_t self, mp_obj_t on)
{
    uit_set_placed(eng(self), mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_set_placed_obj, eng_set_placed);

// ground(x, y, w, h): the box the page's ground covers (w 0: none)
static mp_obj_t eng_ground(size_t n, const mp_obj_t *a)
{
    (void)n;
    uit_set_ground(eng(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]), mp_obj_get_int(a[3]),
                   mp_obj_get_int(a[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_ground_obj, 5, 5, eng_ground);

// place(x, y, w, h, flags, fs): the layout alone, nothing drawn
static mp_obj_t eng_place(size_t n, const mp_obj_t *a)
{
    (void)n;
    return MP_OBJ_NEW_SMALL_INT(uit_place(eng(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                                          mp_obj_get_int(a[3]), mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                                          mp_obj_get_int(a[6])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_place_obj, 7, 7, eng_place);

// rect(key) -> (x, y, w, h) where it was placed last, else None
static mp_obj_t eng_rect(mp_obj_t self, mp_obj_t key)
{
    int32_t r[4];
    if (!uit_rect(eng(self), CSTR(key), r))
        return mp_const_none;
    mp_obj_t t[4] = {MP_OBJ_NEW_SMALL_INT(r[0]), MP_OBJ_NEW_SMALL_INT(r[1]), MP_OBJ_NEW_SMALL_INT(r[2]),
                     MP_OBJ_NEW_SMALL_INT(r[3])};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_rect_obj, eng_rect);

// cond_of(node, name) -> 1, 0, or -1 where the node has no such condition
static mp_obj_t eng_cond_of(mp_obj_t self, mp_obj_t node, mp_obj_t name)
{
    return MP_OBJ_NEW_SMALL_INT(uit_cond_of(eng(self), mp_obj_get_int(node), CSTR(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(eng_cond_of_obj, eng_cond_of);

#define LENT_MAX 32
#define LENT_TEXT 4096

// lent(site) -> [(kind, node, value index, flags, action, key, value, label, icon)], None where
// none is lent, a negative answer a refusal
static mp_obj_t eng_lent(mp_obj_t self, mp_obj_t site)
{
    int32_t *cells = m_new(int32_t, UIT_LC_INTS * LENT_MAX);
    char *text = m_new(char, LENT_TEXT);
    int n = uit_lent(eng(self), CSTR(site), cells, LENT_MAX, text, LENT_TEXT);
    mp_obj_t out = n == -1 ? mp_const_none : n < 0 ? MP_OBJ_NEW_SMALL_INT(n) : mp_obj_new_list(0, NULL);
    const char *t = text;
    for (int i = 0; i < n; i++) {
        mp_obj_t c[9];
        for (int j = 0; j < UIT_LC_INTS; j++)
            c[j] = MP_OBJ_NEW_SMALL_INT(cells[UIT_LC_INTS * i + j]);
        for (int j = 0; j < 4; j++) {
            size_t len = strlen(t);
            c[5 + j] = mp_obj_new_str(t, len);
            t += len + 1;
        }
        mp_obj_list_append(out, mp_obj_new_tuple(9, c));
    }
    m_del(int32_t, cells, UIT_LC_INTS * LENT_MAX);
    m_del(char, text, LENT_TEXT);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_lent_obj, eng_lent);

// lent_place(cells, x0, w, h, fs, pad) -> (density, [(x, w, labelled)], hairlines, need);
// cells (kind, flags, width named) each
static mp_obj_t eng_lent_place(size_t na, const mp_obj_t *a)
{
    (void)na;
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(a[1], &n, &items);
    int32_t *io = m_new(int32_t, UIT_LP_INTS * n + 1), *hair = m_new(int32_t, n + 1), need = 0;
    for (size_t i = 0; i < n; i++) {
        mp_obj_t *f;
        mp_obj_get_array_fixed_n(items[i], 3, &f);
        for (int j = 0; j < 3; j++)
            io[UIT_LP_INTS * i + j] = mp_obj_get_int(f[j]);
    }
    int nh = 0;
    int d = uit_lent_place(io, (int)n, mp_obj_get_int(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                           mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), hair, &nh, &need);
    mp_obj_t places = mp_obj_new_list(0, NULL), hairs = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < n; i++) {
        mp_obj_t t[3];
        for (int j = 0; j < 3; j++)
            t[j] = MP_OBJ_NEW_SMALL_INT(io[UIT_LP_INTS * i + 3 + j]);
        mp_obj_list_append(places, mp_obj_new_tuple(3, t));
    }
    for (int i = 0; i < nh; i++)
        mp_obj_list_append(hairs, MP_OBJ_NEW_SMALL_INT(hair[i]));
    m_del(int32_t, io, UIT_LP_INTS * n + 1);
    m_del(int32_t, hair, n + 1);
    mp_obj_t r[4] = {MP_OBJ_NEW_SMALL_INT(d), places, hairs, MP_OBJ_NEW_SMALL_INT(need)};
    return mp_obj_new_tuple(4, r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_lent_place_obj, 7, 7, eng_lent_place);

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
    eng_obj_t *o = MP_OBJ_TO_PTR(a[0]);
    if (o->raised != MP_OBJ_NULL) {
        mp_obj_t x = o->raised;
        o->raised = MP_OBJ_NULL;
        nlr_raise(x);
    }
    return mp_obj_new_int(r);
}

// A canvas's draw, called inside the frame: what it raises is kept and raised once the frame
// is out of the engine's C.
static void draw_tramp(void *ud, int node, int x, int y, int w, int h)
{
    eng_obj_t *o = ud;
    if (o->draw == MP_OBJ_NULL || o->raised != MP_OBJ_NULL)
        return;
    mp_obj_t args[5] = {MP_OBJ_NEW_SMALL_INT(node), MP_OBJ_NEW_SMALL_INT(x), MP_OBJ_NEW_SMALL_INT(y),
                        MP_OBJ_NEW_SMALL_INT(w), MP_OBJ_NEW_SMALL_INT(h)};
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_call_function_n_kw(o->draw, 5, 0, args);
        nlr_pop();
    } else {
        o->raised = MP_OBJ_FROM_PTR(nlr.ret_val);
    }
}

static mp_obj_t eng_on_draw(mp_obj_t self, mp_obj_t fn)
{
    eng_obj_t *o = MP_OBJ_TO_PTR(self);
    o->draw = fn == mp_const_none ? MP_OBJ_NULL : fn;
    uit_on_draw(eng(self), o->draw == MP_OBJ_NULL ? NULL : draw_tramp, o);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(eng_on_draw_obj, eng_on_draw);
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_frame_obj, 11, 11, eng_frame);

// still(ctx, x, y, w, h, flags, fs, input) -> whether the frame drawn last is what a frame now draws
static mp_obj_t eng_still(size_t n, const mp_obj_t *a)
{
    (void)n;
    return mp_obj_new_bool(uit_still(eng(a[0]), moy_ui_mp_ctx(a[1]), mp_obj_get_int(a[2]), mp_obj_get_int(a[3]),
                                     mp_obj_get_int(a[4]), mp_obj_get_int(a[5]), mp_obj_get_int(a[6]),
                                     mp_obj_get_int(a[7]), mp_obj_get_int(a[8])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(eng_still_obj, 9, 9, eng_still);

// An event's, a trace line's or an id's text: on the VM task's stack for the
// call, so the module keeps no static RAM.
#define EVBUF 512

// next() -> (kind, a, b, c, text), or None
static mp_obj_t eng_next(mp_obj_t self)
{
    char evbuf[EVBUF];
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
    char evbuf[EVBUF];
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
    char evbuf[EVBUF];
    int a = uit_back_dialog(eng(self), evbuf, sizeof evbuf);
    if (a < 0)
        return MP_OBJ_NEW_SMALL_INT(a);
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(a), mp_obj_new_str(evbuf, strlen(evbuf))};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_back_dialog_obj, eng_back_dialog);

static mp_obj_t eng_back_popover(mp_obj_t self)
{
    char evbuf[EVBUF];
    int a = uit_back_popover(eng(self), evbuf, sizeof evbuf);
    if (a < 0)
        return MP_OBJ_NEW_SMALL_INT(a);
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(a), mp_obj_new_str(evbuf, strlen(evbuf))};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_back_popover_obj, eng_back_popover);

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
    char evbuf[EVBUF];
    int n = uit_focused(eng(self), evbuf, sizeof evbuf);
    return n ? mp_obj_new_str(evbuf, (size_t)n) : MP_OBJ_NEW_QSTR(MP_QSTR_);
}
static MP_DEFINE_CONST_FUN_OBJ_1(eng_focused_obj, eng_focused);

static mp_obj_t eng_stop(mp_obj_t self, mp_obj_t i)
{
    char evbuf[EVBUF];
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
    { MP_ROM_QSTR(MP_QSTR_action_row), MP_ROM_PTR(&eng_action_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_action), MP_ROM_PTR(&eng_action_obj) },
    { MP_ROM_QSTR(MP_QSTR_labels), MP_ROM_PTR(&eng_labels_obj) },
    { MP_ROM_QSTR(MP_QSTR_focused), MP_ROM_PTR(&eng_focused_obj) },
    { MP_ROM_QSTR(MP_QSTR_draft), MP_ROM_PTR(&eng_draft_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&eng_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_leave), MP_ROM_PTR(&eng_leave_obj) },
    { MP_ROM_QSTR(MP_QSTR_letter), MP_ROM_PTR(&eng_letter_obj) },
    { MP_ROM_QSTR(MP_QSTR_back_dialog), MP_ROM_PTR(&eng_back_dialog_obj) },
    { MP_ROM_QSTR(MP_QSTR_back_popover), MP_ROM_PTR(&eng_back_popover_obj) },
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
    { MP_ROM_QSTR(MP_QSTR_on_draw), MP_ROM_PTR(&eng_on_draw_obj) },
    { MP_ROM_QSTR(MP_QSTR_build_end), MP_ROM_PTR(&eng_build_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&eng_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&eng_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_free), MP_ROM_PTR(&eng_del_obj) },
    { MP_ROM_QSTR(MP_QSTR_want_focus), MP_ROM_PTR(&eng_want_focus_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_placed), MP_ROM_PTR(&eng_set_placed_obj) },
    { MP_ROM_QSTR(MP_QSTR_ground), MP_ROM_PTR(&eng_ground_obj) },
    { MP_ROM_QSTR(MP_QSTR_place), MP_ROM_PTR(&eng_place_obj) },
    { MP_ROM_QSTR(MP_QSTR_rect), MP_ROM_PTR(&eng_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_lent), MP_ROM_PTR(&eng_lent_obj) },
    { MP_ROM_QSTR(MP_QSTR_cond_of), MP_ROM_PTR(&eng_cond_of_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_absent), MP_ROM_PTR(&eng_set_absent_obj) },
    { MP_ROM_QSTR(MP_QSTR_lent_place), MP_ROM_PTR(&eng_lent_place_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame), MP_ROM_PTR(&eng_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_still), MP_ROM_PTR(&eng_still_obj) },
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

// mem() -> (bytes live, the most live since peak_reset(), blocks live): every
// engine's together (uit_mem).
static mp_obj_t uit_mod_mem(void)
{
    uint32_t m[3];
    uit_mem(m);
    mp_obj_t t[3] = {mp_obj_new_int_from_uint(m[0]), mp_obj_new_int_from_uint(m[1]),
                     mp_obj_new_int_from_uint(m[2])};
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(uit_mod_mem_obj, uit_mod_mem);

static mp_obj_t uit_mod_peak_reset(void)
{
    uit_mem_peak_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(uit_mod_peak_reset_obj, uit_mod_peak_reset);

static const mp_rom_map_elem_t uit_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_uitree) },
    { MP_ROM_QSTR(MP_QSTR_Engine), MP_ROM_PTR(&eng_type) },
    { MP_ROM_QSTR(MP_QSTR_mem), MP_ROM_PTR(&uit_mod_mem_obj) },
    { MP_ROM_QSTR(MP_QSTR_peak_reset), MP_ROM_PTR(&uit_mod_peak_reset_obj) },
};
static MP_DEFINE_CONST_DICT(uit_globals, uit_globals_table);

const mp_obj_module_t moy_uitree_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&uit_globals,
};
MP_REGISTER_MODULE(MP_QSTR_moy_uitree, moy_uitree_module);
