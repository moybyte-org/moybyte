// moy_spine's MicroPython binding: the kernel's spine (moy_htab.h, moy_route.h,
// moy_settings.h) as the module runtime/moy_spine.py is, name for name -- Table,
// AppRegistry, BackStack, Returns, Leases, Settings, StaleHandle and the
// constants. The same binding serves the interface suite and the spine trace;
// which build takes it is MOY_SPINE_IMPL (tools/moy_index_spike.py's header).
//
// It registers EXTENSIBLE, so a moy_spine.py on the import path wins over it. A
// build that takes the native module stages no such file
// (tools/board_config.py), and the desktop MicroPython's suites reach this one
// by importing it with the path emptied (tests/test_moy_spine_twins.py).
//
// Every table, record and row the components keep is allocated by spine_alloc:
// PSRAM on a board (docs/native_kernel_2026-09.md section 4.6: kernel data is
// MALLOC_CAP_SPIRAM unless it is latency-bound), malloc elsewhere. What a
// component hands Python is a small int, a str or a list of them; a kind is an
// interned str (a qstr, made once per kind), so top(), has() and index() on the
// frame path allocate nothing. Each object frees its C state in __del__, so the
// collector reclaims what the Python twin's garbage would.
//
// A handle is an int; one outside 1 .. 2**30 - 1 names no row. A Table keeps its
// rows' objects in a gc array beside the C table (a row of the C table is empty:
// the objects must stay visible to the collector).

#include <string.h>

#include "py/mperrno.h"
#include "py/objexcept.h"
#include "py/objlist.h"
#include "py/objstr.h"
#include "py/objtuple.h"
#include "py/qstr.h"
#include "py/runtime.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#else
#include <stdlib.h>
#endif

#include "moy_htab.h"
#include "moy_route.h"
#include "moy_settings.h"

// -- the allocator -------------------------------------------------------------

#ifdef ESP_PLATFORM
// PSRAM, zeroed. A board with no PSRAM at all takes the default heap; one whose
// PSRAM is merely full refuses, rather than spend internal SRAM.
static void *spine_alloc(size_t n) {
    n = n ? n : 1u;
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0) {
        p = heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
    }
    return p;
}

static void spine_release(void *p, size_t n) {
    (void)n;
    heap_caps_free(p);
}
#else
static void *spine_alloc(size_t n) {
    return calloc(1, n ? n : 1u);
}

static void spine_release(void *p, size_t n) {
    (void)n;
    free(p);
}
#endif

static const moy_htab_mem_t spine_mem = { spine_alloc, spine_release };

// -- errors and arguments --------------------------------------------------------

static MP_DEFINE_CONST_OBJ_TYPE(
    stale_type, MP_QSTR_StaleHandle, MP_TYPE_FLAG_NONE,
    make_new, mp_obj_exception_make_new,
    print, mp_obj_exception_print,
    attr, mp_obj_exception_attr,
    parent, &mp_type_ValueError
    );

static MP_NORETURN void stale(const char *name, uint32_t h) {
    mp_raise_msg_varg(&stale_type, MP_ERROR_TEXT("stale %s handle %d"), name,
                      (int)h);
}

static MP_NORETURN void no_memory(void) {
    mp_raise_type(&mp_type_MemoryError);
}

static MP_NORETURN void no_space(void) {
    mp_raise_OSError(MP_ENOSPC);
}

static MP_NORETURN void raise_alloc(int rc) {
    if (rc == MOY_HTAB_FULL) {
        no_space();
    }
    no_memory();
}

// The handle an int names, 0 for an int no row can have, TypeError for anything
// else. A bool is the int it is, as it is to the Python twin.
static bool handle_of(mp_obj_t h, uint32_t *out) {
    if (mp_obj_is_small_int(h)) {
        mp_int_t v = MP_OBJ_SMALL_INT_VALUE(h);
        *out = (v > 0 && v < ((mp_int_t)1 << 30)) ? (uint32_t)v : 0u;
        return true;
    }
    if (mp_obj_is_bool(h)) {
        *out = h == mp_const_true ? 1u : 0u;
        return true;
    }
    if (mp_obj_is_int(h)) {
        *out = 0u;
        return true;
    }
    return false;
}

static uint32_t handle_arg(mp_obj_t h) {
    uint32_t v;
    if (!handle_of(h, &v)) {
        mp_raise_TypeError(MP_ERROR_TEXT("a handle is an int"));
    }
    return v;
}

static const char *str_arg(mp_obj_t o, size_t *len, mp_rom_error_text_t why) {
    if (!mp_obj_is_str(o)) {
        mp_raise_TypeError(why);
    }
    return mp_obj_str_get_data(o, len);
}

// A kind: a str of 1 .. MOY_ID_MAX bytes.
static const char *kind_arg(mp_obj_t o, size_t *len) {
    const char *s = str_arg(o, len, MP_ERROR_TEXT("a kind is a str"));
    if (*len < 1u || *len > MOY_ID_MAX) {
        mp_raise_ValueError(MP_ERROR_TEXT("a kind is 1..15 bytes"));
    }
    return s;
}

static mp_obj_t kind_obj(const moy_kind_t *k) {
    return MP_OBJ_NEW_QSTR(qstr_from_strn(k->s, k->len));
}

static mp_obj_t kind_or_none(const moy_kind_t *k) {
    return k == NULL ? mp_const_none : kind_obj(k);
}

static mp_obj_t bool_obj(int v) {
    return mp_obj_new_bool(v != 0);
}

// -- Table -----------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_htab_t *t;
    mp_obj_t name;
    mp_obj_t *rows;         // one object per slot
} table_obj_t;

static const mp_obj_type_t table_type;

static table_obj_t *table_of(mp_obj_t self) {
    return MP_OBJ_TO_PTR(self);
}

static const char *table_name(const table_obj_t *self) {
    return mp_obj_str_get_str(self->name);
}

static mp_obj_t table_make_new(const mp_obj_type_t *type, size_t n_args,
                               size_t n_kw, const mp_obj_t *all_args) {
    enum { ARG_kind, ARG_name, ARG_slots };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_kind, MP_ARG_REQUIRED | MP_ARG_INT, { .u_int = 0 } },
        { MP_QSTR_name, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_slots, MP_ARG_INT, { .u_int = MOY_HTAB_SLOTS } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all_kw_array(n_args, n_kw, all_args, MP_ARRAY_SIZE(allowed),
                              allowed, args);
    if (!mp_obj_is_str(args[ARG_name].u_obj)) {
        mp_raise_TypeError(MP_ERROR_TEXT("a table name is a str"));
    }
    mp_int_t kind = args[ARG_kind].u_int, slots = args[ARG_slots].u_int;
    if (kind < 1 || kind > 15 || slots < 1 || slots > (mp_int_t)MOY_HTAB_SLOTS) {
        mp_raise_ValueError(MP_ERROR_TEXT("table kind 1..15, slots 1..256"));
    }
    table_obj_t *o = mp_obj_malloc_with_finaliser(table_obj_t, type);
    o->name = args[ARG_name].u_obj;
    o->t = moy_htab_new(&spine_mem, (uint8_t)kind, (uint32_t)slots, 0u);
    if (o->t == NULL) {
        no_memory();
    }
    o->rows = m_new0(mp_obj_t, slots);
    return MP_OBJ_FROM_PTR(o);
}

static uint32_t table_slot(table_obj_t *self, mp_obj_t h) {
    uint32_t v = handle_arg(h);
    uint32_t s = moy_htab_slot_of(self->t, v);
    if (s == MOY_HTAB_NOSLOT) {
        stale(table_name(self), v);
    }
    return s;
}

static mp_obj_t table_new(mp_obj_t self_in, mp_obj_t row) {
    table_obj_t *self = table_of(self_in);
    uint32_t h;
    void *unused;
    int rc = moy_htab_add(self->t, &h, &unused);
    if (rc != MOY_HTAB_OK) {
        raise_alloc(rc);
    }
    self->rows[h & self->t->slot_mask] = row;
    return MP_OBJ_NEW_SMALL_INT(h);
}
static MP_DEFINE_CONST_FUN_OBJ_2(table_new_obj, table_new);

static mp_obj_t table_get(mp_obj_t self_in, mp_obj_t h) {
    table_obj_t *self = table_of(self_in);
    return self->rows[table_slot(self, h)];
}
static MP_DEFINE_CONST_FUN_OBJ_2(table_get_obj, table_get);

static mp_obj_t table_put(mp_obj_t self_in, mp_obj_t h, mp_obj_t row) {
    table_obj_t *self = table_of(self_in);
    self->rows[table_slot(self, h)] = row;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(table_put_obj, table_put);

static mp_obj_t table_valid(mp_obj_t self_in, mp_obj_t h) {
    uint32_t v;
    return bool_obj(handle_of(h, &v)
                    && moy_htab_slot_of(table_of(self_in)->t, v) != MOY_HTAB_NOSLOT);
}
static MP_DEFINE_CONST_FUN_OBJ_2(table_valid_obj, table_valid);

static mp_obj_t table_release(mp_obj_t self_in, mp_obj_t h) {
    table_obj_t *self = table_of(self_in);
    uint32_t s = table_slot(self, h);
    mp_obj_t row = self->rows[s];
    self->rows[s] = MP_OBJ_NULL;
    moy_htab_release(self->t, moy_htab_handle(self->t, s));
    return row;
}
static MP_DEFINE_CONST_FUN_OBJ_2(table_release_obj, table_release);

static mp_obj_t table_handles(mp_obj_t self_in) {
    moy_htab_t *t = table_of(self_in)->t;
    mp_obj_list_t *out = MP_OBJ_TO_PTR(mp_obj_new_list(moy_htab_count(t), NULL));
    size_t k = 0;
    for (uint32_t s = 0, n = moy_htab_slots(t); s < n && k < out->len; s++) {
        if (moy_htab_live(t, s)) {
            out->items[k++] = MP_OBJ_NEW_SMALL_INT(moy_htab_handle(t, s));
        }
    }
    return MP_OBJ_FROM_PTR(out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_handles_obj, table_handles);

static mp_obj_t table_count(mp_obj_t self_in) {
    return MP_OBJ_NEW_SMALL_INT(moy_htab_count(table_of(self_in)->t));
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_count_obj, table_count);

static mp_obj_t table_del(mp_obj_t self_in) {
    table_obj_t *self = table_of(self_in);
    moy_htab_free(self->t);
    self->t = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_del_obj, table_del);

static const mp_rom_map_elem_t table_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_new), MP_ROM_PTR(&table_new_obj) },
    { MP_ROM_QSTR(MP_QSTR_get), MP_ROM_PTR(&table_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_put), MP_ROM_PTR(&table_put_obj) },
    { MP_ROM_QSTR(MP_QSTR_valid), MP_ROM_PTR(&table_valid_obj) },
    { MP_ROM_QSTR(MP_QSTR_release), MP_ROM_PTR(&table_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_handles), MP_ROM_PTR(&table_handles_obj) },
    { MP_ROM_QSTR(MP_QSTR_count), MP_ROM_PTR(&table_count_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&table_del_obj) },
};
static MP_DEFINE_CONST_DICT(table_locals, table_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    table_type, MP_QSTR_Table, MP_TYPE_FLAG_NONE,
    make_new, table_make_new,
    locals_dict, &table_locals
    );

// -- AppRegistry -------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_apps_t *a;
} apps_obj_t;

static const mp_obj_type_t apps_type;

static moy_apps_t *apps_of(mp_obj_t self) {
    return ((apps_obj_t *)MP_OBJ_TO_PTR(self))->a;
}

static mp_obj_t apps_make_new(const mp_obj_type_t *type, size_t n_args,
                              size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    apps_obj_t *o = mp_obj_malloc_with_finaliser(apps_obj_t, type);
    o->a = moy_apps_new(&spine_mem);
    if (o->a == NULL) {
        no_memory();
    }
    return MP_OBJ_FROM_PTR(o);
}

static const moy_app_t *app_row(mp_obj_t self, mp_obj_t h) {
    uint32_t v = handle_arg(h);
    const moy_app_t *app;
    if (moy_apps_get(apps_of(self), v, &app) != MOY_ROUTE_OK) {
        stale("app", v);
    }
    return app;
}

// An int that fits 32 bits, ValueError for one that does not (the compare is
// made on the objects: a long int is no machine word on the boards' VM).
static int32_t int32_arg(mp_obj_t o) {
    if (mp_obj_is_true(mp_binary_op(MP_BINARY_OP_LESS, o, mp_obj_new_int(INT32_MIN)))
        || mp_obj_is_true(mp_binary_op(MP_BINARY_OP_MORE, o, mp_obj_new_int(INT32_MAX)))) {
        mp_raise_ValueError(MP_ERROR_TEXT("min_size must fit 32 bits"));
    }
    return (int32_t)mp_obj_get_int(o);
}

static mp_obj_t apps_register(size_t n_args, const mp_obj_t *pos_args,
                              mp_map_t *kw_args) {
    enum { ARG_app_id, ARG_title, ARG_text_mode, ARG_min_size };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_app_id, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_title, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_text_mode, MP_ARG_OBJ, { .u_obj = mp_const_false } },
        { MP_QSTR_min_size, MP_ARG_OBJ, { .u_obj = mp_const_none } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed),
                     allowed, args);
    moy_apps_t *a = apps_of(pos_args[0]);
    size_t id_len;
    const char *id = kind_arg(args[ARG_app_id].u_obj, &id_len);
    if (moy_apps_find(a, id, id_len)) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("duplicate app id: %s"),
                          id);
    }
    int has_min = 0;
    int32_t w = 0, h = 0;
    mp_obj_t size = args[ARG_min_size].u_obj;
    if (size != mp_const_none) {
        mp_obj_t *items;
        mp_obj_get_array_fixed_n(size, 2, &items);
        w = int32_arg(items[0]);
        h = int32_arg(items[1]);
        has_min = 1;
    }
    mp_obj_t title = args[ARG_title].u_obj;
    if (!mp_obj_is_str(title)) {
        title = mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_str), title);
    }
    size_t title_len;
    const char *t = mp_obj_str_get_data(title, &title_len);
    uint32_t handle;
    int rc = moy_apps_register(a, id, id_len, t, title_len,
                               mp_obj_is_true(args[ARG_text_mode].u_obj), has_min,
                               w, h, &handle);
    if (rc != MOY_ROUTE_OK) {
        raise_alloc(rc);
    }
    return MP_OBJ_NEW_SMALL_INT(handle);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(apps_register_obj, 3, apps_register);

static mp_obj_t apps_find(mp_obj_t self, mp_obj_t app_id) {
    size_t n;
    const char *s = str_arg(app_id, &n, MP_ERROR_TEXT("an app id is a str"));
    return MP_OBJ_NEW_SMALL_INT(moy_apps_find(apps_of(self), s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_2(apps_find_obj, apps_find);

static mp_obj_t apps_app_id(mp_obj_t self, mp_obj_t h) {
    return kind_obj(&app_row(self, h)->id);
}
static MP_DEFINE_CONST_FUN_OBJ_2(apps_app_id_obj, apps_app_id);

static mp_obj_t apps_title(mp_obj_t self, mp_obj_t h) {
    const moy_app_t *app = app_row(self, h);
    return mp_obj_new_str_copy(&mp_type_str, (const byte *)app->title,
                               app->title_len);
}
static MP_DEFINE_CONST_FUN_OBJ_2(apps_title_obj, apps_title);

static mp_obj_t apps_text_mode(mp_obj_t self, mp_obj_t h) {
    return bool_obj(app_row(self, h)->text_mode);
}
static MP_DEFINE_CONST_FUN_OBJ_2(apps_text_mode_obj, apps_text_mode);

static mp_obj_t apps_min_size(mp_obj_t self, mp_obj_t h) {
    const moy_app_t *app = app_row(self, h);
    if (!app->has_min) {
        return mp_const_none;
    }
    mp_obj_t pair[2] = { mp_obj_new_int(app->min_w), mp_obj_new_int(app->min_h) };
    return mp_obj_new_tuple(2, pair);
}
static MP_DEFINE_CONST_FUN_OBJ_2(apps_min_size_obj, apps_min_size);

static mp_obj_t apps_valid(mp_obj_t self, mp_obj_t h) {
    uint32_t v;
    return bool_obj(handle_of(h, &v) && moy_apps_valid(apps_of(self), v));
}
static MP_DEFINE_CONST_FUN_OBJ_2(apps_valid_obj, apps_valid);

static mp_obj_t apps_handles(mp_obj_t self) {
    moy_apps_t *a = apps_of(self);
    mp_obj_list_t *out = MP_OBJ_TO_PTR(mp_obj_new_list(moy_apps_count(a), NULL));
    size_t k = 0;
    for (uint32_t s = 0, n = moy_apps_slots(a); s < n && k < out->len; s++) {
        uint32_t h = moy_apps_at(a, s);
        if (h) {
            out->items[k++] = MP_OBJ_NEW_SMALL_INT(h);
        }
    }
    return MP_OBJ_FROM_PTR(out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(apps_handles_obj, apps_handles);

static mp_obj_t apps_count(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_apps_count(apps_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(apps_count_obj, apps_count);

static mp_obj_t apps_del(mp_obj_t self_in) {
    apps_obj_t *self = MP_OBJ_TO_PTR(self_in);
    moy_apps_free(self->a);
    self->a = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(apps_del_obj, apps_del);

static const mp_rom_map_elem_t apps_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_SLOTS), MP_ROM_INT(MOY_APP_SLOTS) },
    { MP_ROM_QSTR(MP_QSTR_register), MP_ROM_PTR(&apps_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_find), MP_ROM_PTR(&apps_find_obj) },
    { MP_ROM_QSTR(MP_QSTR_app_id), MP_ROM_PTR(&apps_app_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_title), MP_ROM_PTR(&apps_title_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_mode), MP_ROM_PTR(&apps_text_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_min_size), MP_ROM_PTR(&apps_min_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_valid), MP_ROM_PTR(&apps_valid_obj) },
    { MP_ROM_QSTR(MP_QSTR_handles), MP_ROM_PTR(&apps_handles_obj) },
    { MP_ROM_QSTR(MP_QSTR_count), MP_ROM_PTR(&apps_count_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&apps_del_obj) },
};
static MP_DEFINE_CONST_DICT(apps_locals, apps_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    apps_type, MP_QSTR_AppRegistry, MP_TYPE_FLAG_NONE,
    make_new, apps_make_new,
    locals_dict, &apps_locals
    );

// -- BackStack ---------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_back_t *b;
} back_obj_t;

static moy_back_t *back_of(mp_obj_t self) {
    return ((back_obj_t *)MP_OBJ_TO_PTR(self))->b;
}

static mp_obj_t back_make_new(const mp_obj_type_t *type, size_t n_args,
                              size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    back_obj_t *o = mp_obj_malloc_with_finaliser(back_obj_t, type);
    o->b = moy_back_new(&spine_mem);
    if (o->b == NULL) {
        no_memory();
    }
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t back_goto(mp_obj_t self, mp_obj_t kind) {
    size_t n;
    const char *s = kind_arg(kind, &n);
    int answer = 0;
    int rc = moy_back_goto(back_of(self), s, n, &answer);
    if (rc != MOY_ROUTE_OK) {
        raise_alloc(rc);
    }
    return MP_OBJ_NEW_SMALL_INT(answer);
}
static MP_DEFINE_CONST_FUN_OBJ_2(back_goto_obj, back_goto);

static mp_obj_t back_remove(mp_obj_t self, mp_obj_t kind) {
    size_t n;
    const char *s = kind_arg(kind, &n);
    int removed = 0;
    moy_back_remove(back_of(self), s, n, &removed);
    return bool_obj(removed);
}
static MP_DEFINE_CONST_FUN_OBJ_2(back_remove_obj, back_remove);

static mp_obj_t back_top(mp_obj_t self) {
    return kind_obj(moy_back_top(back_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(back_top_obj, back_top);

// The depth of `kind` from the root, or -1 for an object that is no kind on it.
static int back_find(mp_obj_t self, mp_obj_t kind) {
    if (!mp_obj_is_str(kind)) {
        return -1;
    }
    size_t n;
    const char *s = mp_obj_str_get_data(kind, &n);
    return moy_back_index(back_of(self), s, n);
}

static mp_obj_t back_has(mp_obj_t self, mp_obj_t kind) {
    return bool_obj(back_find(self, kind) >= 0);
}
static MP_DEFINE_CONST_FUN_OBJ_2(back_has_obj, back_has);

static mp_obj_t back_index(mp_obj_t self, mp_obj_t kind) {
    return MP_OBJ_NEW_SMALL_INT(back_find(self, kind));
}
static MP_DEFINE_CONST_FUN_OBJ_2(back_index_obj, back_index);

static mp_obj_t back_depth(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_back_depth(back_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(back_depth_obj, back_depth);

static mp_obj_t back_kinds(mp_obj_t self) {
    moy_back_t *b = back_of(self);
    uint32_t n = moy_back_depth(b);
    mp_obj_list_t *out = MP_OBJ_TO_PTR(mp_obj_new_list(n, NULL));
    for (uint32_t i = 0; i < n && i < out->len; i++) {
        out->items[i] = kind_obj(moy_back_at(b, i));
    }
    return MP_OBJ_FROM_PTR(out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(back_kinds_obj, back_kinds);

static mp_obj_t back_del(mp_obj_t self_in) {
    back_obj_t *self = MP_OBJ_TO_PTR(self_in);
    moy_back_free(self->b);
    self->b = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(back_del_obj, back_del);

static const mp_rom_map_elem_t back_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_DEPTH), MP_ROM_INT(MOY_BACK_DEPTH) },
    { MP_ROM_QSTR(MP_QSTR_goto), MP_ROM_PTR(&back_goto_obj) },
    { MP_ROM_QSTR(MP_QSTR_remove), MP_ROM_PTR(&back_remove_obj) },
    { MP_ROM_QSTR(MP_QSTR_top), MP_ROM_PTR(&back_top_obj) },
    { MP_ROM_QSTR(MP_QSTR_has), MP_ROM_PTR(&back_has_obj) },
    { MP_ROM_QSTR(MP_QSTR_index), MP_ROM_PTR(&back_index_obj) },
    { MP_ROM_QSTR(MP_QSTR_depth), MP_ROM_PTR(&back_depth_obj) },
    { MP_ROM_QSTR(MP_QSTR_kinds), MP_ROM_PTR(&back_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&back_del_obj) },
};
static MP_DEFINE_CONST_DICT(back_locals, back_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    back_type, MP_QSTR_BackStack, MP_TYPE_FLAG_NONE,
    make_new, back_make_new,
    locals_dict, &back_locals
    );

// -- Returns -----------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_returns_t *r;
    mp_obj_t apps;          // the registry it reads, kept alive with it
} returns_obj_t;

static moy_returns_t *returns_of(mp_obj_t self) {
    return ((returns_obj_t *)MP_OBJ_TO_PTR(self))->r;
}

static mp_obj_t returns_make_new(const mp_obj_type_t *type, size_t n_args,
                                 size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 1, 1, false);
    if (!mp_obj_is_type(args[0], &apps_type)) {
        mp_raise_TypeError(MP_ERROR_TEXT("Returns takes an AppRegistry"));
    }
    returns_obj_t *o = mp_obj_malloc_with_finaliser(returns_obj_t, type);
    o->apps = args[0];
    o->r = moy_returns_new(&spine_mem, apps_of(args[0]));
    if (o->r == NULL) {
        no_memory();
    }
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t returns_run(mp_obj_t self, mp_obj_t caller) {
    if (caller == mp_const_none) {
        moy_returns_run(returns_of(self), NULL, 0);
        return mp_const_none;
    }
    size_t n;
    const char *s = kind_arg(caller, &n);
    moy_returns_run(returns_of(self), s, n);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(returns_run_obj, returns_run);

static mp_obj_t returns_caller(mp_obj_t self) {
    return kind_or_none(moy_returns_caller(returns_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(returns_caller_obj, returns_caller);

static mp_obj_t returns_spend(mp_obj_t self) {
    moy_kind_t k;
    return moy_returns_spend(returns_of(self), &k) ? kind_obj(&k) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(returns_spend_obj, returns_spend);

static mp_obj_t returns_route(mp_obj_t self, mp_obj_t windowed) {
    return MP_OBJ_NEW_SMALL_INT(
        moy_returns_route(returns_of(self), mp_obj_is_true(windowed)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(returns_route_obj, returns_route);

static mp_obj_t returns_note(mp_obj_t self, mp_obj_t kind) {
    size_t n;
    const char *s = kind_arg(kind, &n);
    int set = 0;
    moy_returns_note(returns_of(self), s, n, &set);
    return bool_obj(set);
}
static MP_DEFINE_CONST_FUN_OBJ_2(returns_note_obj, returns_note);

static mp_obj_t returns_back(mp_obj_t self) {
    return kind_or_none(moy_returns_back(returns_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(returns_back_obj, returns_back);

static mp_obj_t returns_take_back(mp_obj_t self) {
    moy_kind_t k;
    return moy_returns_take_back(returns_of(self), &k) ? kind_obj(&k)
                                                       : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(returns_take_back_obj, returns_take_back);

static mp_obj_t returns_del(mp_obj_t self_in) {
    returns_obj_t *self = MP_OBJ_TO_PTR(self_in);
    moy_returns_free(self->r);
    self->r = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(returns_del_obj, returns_del);

static const mp_rom_map_elem_t returns_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_run), MP_ROM_PTR(&returns_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_caller), MP_ROM_PTR(&returns_caller_obj) },
    { MP_ROM_QSTR(MP_QSTR_spend), MP_ROM_PTR(&returns_spend_obj) },
    { MP_ROM_QSTR(MP_QSTR_route), MP_ROM_PTR(&returns_route_obj) },
    { MP_ROM_QSTR(MP_QSTR_note), MP_ROM_PTR(&returns_note_obj) },
    { MP_ROM_QSTR(MP_QSTR_back), MP_ROM_PTR(&returns_back_obj) },
    { MP_ROM_QSTR(MP_QSTR_take_back), MP_ROM_PTR(&returns_take_back_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&returns_del_obj) },
};
static MP_DEFINE_CONST_DICT(returns_locals, returns_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    returns_type, MP_QSTR_Returns, MP_TYPE_FLAG_NONE,
    make_new, returns_make_new,
    locals_dict, &returns_locals
    );

// -- Leases ------------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_leases_t *l;
} leases_obj_t;

static moy_leases_t *leases_of(mp_obj_t self) {
    return ((leases_obj_t *)MP_OBJ_TO_PTR(self))->l;
}

static mp_obj_t leases_make_new(const mp_obj_type_t *type, size_t n_args,
                                size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    leases_obj_t *o = mp_obj_malloc_with_finaliser(leases_obj_t, type);
    o->l = moy_leases_new(&spine_mem);
    if (o->l == NULL) {
        no_memory();
    }
    return MP_OBJ_FROM_PTR(o);
}

// The bit of a lease tag: TypeError for a non-str, ValueError for one outside
// the table.
static uint32_t lease_arg(mp_obj_t tag) {
    size_t n;
    const char *s = str_arg(tag, &n, MP_ERROR_TEXT("a lease tag is a str"));
    uint32_t bit = moy_lease_bit(s, n);
    if (bit == 0u) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("unknown lease tag %s"),
                          s);
    }
    return bit;
}

static mp_obj_t leases_hold(mp_obj_t self, mp_obj_t tag) {
    size_t n;
    lease_arg(tag);
    const char *s = mp_obj_str_get_data(tag, &n);
    uint32_t mask;
    moy_leases_hold(leases_of(self), s, n, &mask);
    return MP_OBJ_NEW_SMALL_INT(mask);
}
static MP_DEFINE_CONST_FUN_OBJ_2(leases_hold_obj, leases_hold);

static mp_obj_t leases_release(mp_obj_t self, mp_obj_t tag) {
    size_t n;
    lease_arg(tag);
    const char *s = mp_obj_str_get_data(tag, &n);
    uint32_t mask;
    moy_leases_release(leases_of(self), s, n, &mask);
    return MP_OBJ_NEW_SMALL_INT(mask);
}
static MP_DEFINE_CONST_FUN_OBJ_2(leases_release_obj, leases_release);

static mp_obj_t leases_mask(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_leases_mask(leases_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(leases_mask_obj, leases_mask);

static mp_obj_t leases_held(mp_obj_t self, mp_obj_t tag) {
    return bool_obj(moy_leases_mask(leases_of(self)) & lease_arg(tag));
}
static MP_DEFINE_CONST_FUN_OBJ_2(leases_held_obj, leases_held);

static mp_obj_t leases_holders(mp_obj_t self) {
    uint32_t mask = moy_leases_mask(leases_of(self));
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (uint32_t i = 0; i < MOY_LEASE_TAGS; i++) {
        if (mask & (1u << i)) {
            const char *tag = moy_lease_tag(i);
            mp_obj_list_append(out, MP_OBJ_NEW_QSTR(qstr_from_strn(tag, strlen(tag))));
        }
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(leases_holders_obj, leases_holders);

static mp_obj_t leases_del(mp_obj_t self_in) {
    leases_obj_t *self = MP_OBJ_TO_PTR(self_in);
    moy_leases_free(self->l);
    self->l = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(leases_del_obj, leases_del);

static const mp_rom_map_elem_t leases_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_hold), MP_ROM_PTR(&leases_hold_obj) },
    { MP_ROM_QSTR(MP_QSTR_release), MP_ROM_PTR(&leases_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_mask), MP_ROM_PTR(&leases_mask_obj) },
    { MP_ROM_QSTR(MP_QSTR_held), MP_ROM_PTR(&leases_held_obj) },
    { MP_ROM_QSTR(MP_QSTR_holders), MP_ROM_PTR(&leases_holders_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&leases_del_obj) },
};
static MP_DEFINE_CONST_DICT(leases_locals, leases_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    leases_type, MP_QSTR_Leases, MP_TYPE_FLAG_NONE,
    make_new, leases_make_new,
    locals_dict, &leases_locals
    );

// -- Settings ----------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_settings_t *s;
} settings_obj_t;

static moy_settings_t *settings_of(mp_obj_t self) {
    return ((settings_obj_t *)MP_OBJ_TO_PTR(self))->s;
}

static mp_obj_t settings_make_new(const mp_obj_type_t *type, size_t n_args,
                                  size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    settings_obj_t *o = mp_obj_malloc_with_finaliser(settings_obj_t, type);
    o->s = moy_settings_new(&spine_mem);
    if (o->s == NULL) {
        no_memory();
    }
    return MP_OBJ_FROM_PTR(o);
}

static MP_NORETURN void raise_settings(int rc) {
    if (rc == MOY_SETTINGS_NOMEM) {
        no_memory();
    }
    if (rc == MOY_SETTINGS_BADKEY) {
        mp_raise_ValueError(MP_ERROR_TEXT("an empty settings key"));
    }
    mp_raise_ValueError(MP_ERROR_TEXT("not JSON the settings store holds"));
}

static const char *settings_key(mp_obj_t key, size_t *len) {
    const char *s = str_arg(key, len, MP_ERROR_TEXT("a settings key is a str"));
    if (*len == 0u) {
        mp_raise_ValueError(MP_ERROR_TEXT("an empty settings key"));
    }
    return s;
}

static mp_obj_t settings_load(mp_obj_t self, mp_obj_t text) {
    size_t n;
    const char *s = str_arg(text, &n, MP_ERROR_TEXT("system.json is a str"));
    uint32_t rows;
    int rc = moy_settings_load(settings_of(self), s, n, &rows);
    if (rc != MOY_SETTINGS_OK) {
        raise_settings(rc);
    }
    return MP_OBJ_NEW_SMALL_INT(rows);
}
static MP_DEFINE_CONST_FUN_OBJ_2(settings_load_obj, settings_load);

// Replace every row with `d`'s items, each value encoded by json.dumps: the
// object's text is what load reads, so a value that cannot be encoded raises
// before any row changes.
static mp_obj_t settings_adopt(mp_obj_t self, mp_obj_t d) {
    if (!mp_obj_is_dict_or_ordereddict(d)) {
        mp_raise_TypeError(MP_ERROR_TEXT("adopt takes a dict"));
    }
    mp_obj_t it = mp_getiter(d, NULL), k;
    while ((k = mp_iternext(it)) != MP_OBJ_STOP_ITERATION) {
        size_t n;
        settings_key(k, &n);
    }
    mp_obj_t json = mp_import_name(MP_QSTR_json, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
    mp_obj_t text = mp_call_function_1(mp_load_attr(json, MP_QSTR_dumps), d);
    return settings_load(self, text);
}
static MP_DEFINE_CONST_FUN_OBJ_2(settings_adopt_obj, settings_adopt);

static mp_obj_t settings_get(mp_obj_t self, mp_obj_t key) {
    size_t kn, jn;
    const char *k = settings_key(key, &kn), *j;
    if (!moy_settings_get(settings_of(self), k, kn, &j, &jn)) {
        return mp_const_none;
    }
    return mp_obj_new_str_copy(&mp_type_str, (const byte *)j, jn);
}
static MP_DEFINE_CONST_FUN_OBJ_2(settings_get_obj, settings_get);

static mp_obj_t settings_set(mp_obj_t self, mp_obj_t key, mp_obj_t text) {
    size_t jn, kn;
    const char *j = str_arg(text, &jn, MP_ERROR_TEXT("a settings value is JSON text"));
    if (moy_settings_validate(j, jn) != MOY_SETTINGS_OK) {
        raise_settings(MOY_SETTINGS_BADJSON);
    }
    const char *k = settings_key(key, &kn);
    int rc = moy_settings_set(settings_of(self), k, kn, j, jn);
    if (rc != MOY_SETTINGS_OK) {
        raise_settings(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(settings_set_obj, settings_set);

static mp_obj_t settings_delete(mp_obj_t self, mp_obj_t key) {
    size_t kn;
    const char *k = settings_key(key, &kn);
    return bool_obj(moy_settings_delete(settings_of(self), k, kn));
}
static MP_DEFINE_CONST_FUN_OBJ_2(settings_delete_obj, settings_delete);

static mp_obj_t settings_keys(mp_obj_t self) {
    moy_settings_t *s = settings_of(self);
    uint32_t n = moy_settings_count(s);
    mp_obj_list_t *out = MP_OBJ_TO_PTR(mp_obj_new_list(n, NULL));
    for (uint32_t i = 0; i < n && i < out->len; i++) {
        const char *k, *j;
        size_t kn, jn;
        moy_settings_at(s, i, &k, &kn, &j, &jn);
        out->items[i] = mp_obj_new_str_copy(&mp_type_str, (const byte *)k, kn);
    }
    return MP_OBJ_FROM_PTR(out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(settings_keys_obj, settings_keys);

static mp_obj_t settings_dump(mp_obj_t self) {
    moy_settings_t *s = settings_of(self);
    vstr_t vstr;
    vstr_init_len(&vstr, moy_settings_dump(s, NULL, 0));
    moy_settings_dump(s, vstr.buf, vstr.len);
    return mp_obj_new_str_from_vstr(&vstr);
}
static MP_DEFINE_CONST_FUN_OBJ_1(settings_dump_obj, settings_dump);

static mp_obj_t settings_del(mp_obj_t self_in) {
    settings_obj_t *self = MP_OBJ_TO_PTR(self_in);
    moy_settings_free(self->s);
    self->s = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(settings_del_obj, settings_del);

static const mp_rom_map_elem_t settings_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_load), MP_ROM_PTR(&settings_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_adopt), MP_ROM_PTR(&settings_adopt_obj) },
    { MP_ROM_QSTR(MP_QSTR_get), MP_ROM_PTR(&settings_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_set), MP_ROM_PTR(&settings_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_delete), MP_ROM_PTR(&settings_delete_obj) },
    { MP_ROM_QSTR(MP_QSTR_keys), MP_ROM_PTR(&settings_keys_obj) },
    { MP_ROM_QSTR(MP_QSTR_dump), MP_ROM_PTR(&settings_dump_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&settings_del_obj) },
};
static MP_DEFINE_CONST_DICT(settings_locals, settings_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    settings_type, MP_QSTR_Settings, MP_TYPE_FLAG_NONE,
    make_new, settings_make_new,
    locals_dict, &settings_locals
    );

// -- the module --------------------------------------------------------------------

static const mp_rom_obj_tuple_t lease_tags_tuple = {
    { &mp_type_tuple }, MOY_LEASE_TAGS,
    {
        MP_ROM_QSTR(MP_QSTR_web), MP_ROM_QSTR(MP_QSTR_update),
        MP_ROM_QSTR(MP_QSTR_settings), MP_ROM_QSTR(MP_QSTR_cart),
        MP_ROM_QSTR(MP_QSTR_link), MP_ROM_QSTR(MP_QSTR_carts),
        MP_ROM_QSTR(MP_QSTR_dev),
    },
};

static const mp_rom_map_elem_t moy_spine_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_spine) },
    { MP_ROM_QSTR(MP_QSTR_Table), MP_ROM_PTR(&table_type) },
    { MP_ROM_QSTR(MP_QSTR_AppRegistry), MP_ROM_PTR(&apps_type) },
    { MP_ROM_QSTR(MP_QSTR_BackStack), MP_ROM_PTR(&back_type) },
    { MP_ROM_QSTR(MP_QSTR_Returns), MP_ROM_PTR(&returns_type) },
    { MP_ROM_QSTR(MP_QSTR_Leases), MP_ROM_PTR(&leases_type) },
    { MP_ROM_QSTR(MP_QSTR_Settings), MP_ROM_PTR(&settings_type) },
    { MP_ROM_QSTR(MP_QSTR_StaleHandle), MP_ROM_PTR(&stale_type) },
    { MP_ROM_QSTR(MP_QSTR_SLOT_BITS), MP_ROM_INT(8) },
    { MP_ROM_QSTR(MP_QSTR_KIND_SHIFT), MP_ROM_INT(MOY_HTAB_KIND_SHIFT) },
    { MP_ROM_QSTR(MP_QSTR_GEN_SHIFT), MP_ROM_INT(MOY_HTAB_GEN_SHIFT) },
    { MP_ROM_QSTR(MP_QSTR_GEN_MAX), MP_ROM_INT(MOY_HTAB_GEN_MAX) },
    { MP_ROM_QSTR(MP_QSTR_SLOTS), MP_ROM_INT(MOY_HTAB_SLOTS) },
    { MP_ROM_QSTR(MP_QSTR_ID_MAX), MP_ROM_INT(MOY_ID_MAX) },
    { MP_ROM_QSTR(MP_QSTR_KIND_APP), MP_ROM_INT(MOY_KIND_APP) },
    { MP_ROM_QSTR(MP_QSTR_STAYED), MP_ROM_INT(MOY_GOTO_STAYED) },
    { MP_ROM_QSTR(MP_QSTR_PUSHED), MP_ROM_INT(MOY_GOTO_PUSHED) },
    { MP_ROM_QSTR(MP_QSTR_RETURNED), MP_ROM_INT(MOY_GOTO_RETURNED) },
    { MP_ROM_QSTR(MP_QSTR_ROOT), MP_ROM_QSTR(MP_QSTR_launcher) },
    { MP_ROM_QSTR(MP_QSTR_EDITOR), MP_ROM_QSTR(MP_QSTR_menu) },
    { MP_ROM_QSTR(MP_QSTR_ROUTE_HOME), MP_ROM_INT(MOY_ROUTE_HOME) },
    { MP_ROM_QSTR(MP_QSTR_ROUTE_EDITOR), MP_ROM_INT(MOY_ROUTE_EDITOR) },
    { MP_ROM_QSTR(MP_QSTR_ROUTE_APP), MP_ROM_INT(MOY_ROUTE_APP) },
    { MP_ROM_QSTR(MP_QSTR_ROUTE_WINDOW), MP_ROM_INT(MOY_ROUTE_WINDOW) },
    { MP_ROM_QSTR(MP_QSTR_LEASE_TAGS), MP_ROM_PTR(&lease_tags_tuple) },
};
static MP_DEFINE_CONST_DICT(moy_spine_globals, moy_spine_globals_table);

const mp_obj_module_t moy_spine_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_spine_globals,
};

MP_REGISTER_EXTENSIBLE_MODULE(MP_QSTR_moy_spine, moy_spine_module);
