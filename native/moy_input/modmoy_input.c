// moy_input's MicroPython binding: the input table (moy_input.h) as the module
// `moy_input`, the same names the host's ctypes binding (runtime/moy_input.py)
// gives CPython.
//
//   NAMES, HOST_NAMES             the buttons, libmoy's order then the console's
//   InputTable(), HostInputTable() a table over all fifteen names, or the host's
//                                 eight; kernel() is the drivers' table
//   InputSource                   table.source(name, player=0): one producer
//   Pointer(w, h, idle_ms=...)    a screen-space cursor
//   pointer_state(inp, out)       the pointer a cart sees: [x, y, P_*, 0]
//
// A table carries a dict for the attributes the shell hangs on its input
// (game_pointer, netplay_live, players, ...). Nothing a frame calls allocates:
// the reads answer small ints and bools, button_masks fills a caller's list.

#include <string.h>

#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/objlist.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_ble.h"
#include "moy_drivers.h"
#include "moy_input.h"

static const qstr NAME_Q[MOY_INPUT_BUTTONS] = {
    MP_QSTR_left, MP_QSTR_right, MP_QSTR_up, MP_QSTR_down, MP_QSTR_a, MP_QSTR_b,
    MP_QSTR_run, MP_QSTR_home, MP_QSTR_x, MP_QSTR_y, MP_QSTR_stop, MP_QSTR_save,
    MP_QSTR_share, MP_QSTR_select, MP_QSTR_start,
};

static const mp_rom_obj_tuple_t names_obj = {
    {&mp_type_tuple}, MOY_INPUT_BUTTONS, {
        MP_ROM_QSTR(MP_QSTR_left), MP_ROM_QSTR(MP_QSTR_right), MP_ROM_QSTR(MP_QSTR_up),
        MP_ROM_QSTR(MP_QSTR_down), MP_ROM_QSTR(MP_QSTR_a), MP_ROM_QSTR(MP_QSTR_b),
        MP_ROM_QSTR(MP_QSTR_run), MP_ROM_QSTR(MP_QSTR_home), MP_ROM_QSTR(MP_QSTR_x),
        MP_ROM_QSTR(MP_QSTR_y), MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_QSTR(MP_QSTR_save),
        MP_ROM_QSTR(MP_QSTR_share), MP_ROM_QSTR(MP_QSTR_select), MP_ROM_QSTR(MP_QSTR_start),
    }
};

static const mp_rom_obj_tuple_t host_names_obj = {
    {&mp_type_tuple}, MOY_INPUT_HOST_BUTTONS, {
        MP_ROM_QSTR(MP_QSTR_left), MP_ROM_QSTR(MP_QSTR_right), MP_ROM_QSTR(MP_QSTR_up),
        MP_ROM_QSTR(MP_QSTR_down), MP_ROM_QSTR(MP_QSTR_a), MP_ROM_QSTR(MP_QSTR_b),
        MP_ROM_QSTR(MP_QSTR_run), MP_ROM_QSTR(MP_QSTR_home),
    }
};

// A coordinate: an int, or a float truncated toward zero.
static int32_t coord(mp_obj_t o) {
    if (mp_obj_is_float(o)) {
        return (int32_t)mp_obj_get_float(o);
    }
    return (int32_t)mp_obj_get_int(o);
}

static uint32_t now_ms(void) {
    return (uint32_t)mp_hal_ticks_ms() & (MOY_INPUT_TICKS_PERIOD - 1u);
}

// A button name's bit, or -1. A qstr is compared by number, any other str by
// its bytes.
static int name_bit(mp_obj_t name) {
    if (mp_obj_is_qstr(name)) {
        qstr q = MP_OBJ_QSTR_VALUE(name);
        for (int i = 0; i < (int)MOY_INPUT_BUTTONS; i++) {
            if (NAME_Q[i] == q) {
                return i;
            }
        }
        return -1;
    }
    if (!mp_obj_is_str(name)) {
        return -1;
    }
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    for (int i = 0; i < (int)MOY_INPUT_BUTTONS; i++) {
        if (strlen(MOY_INPUT_NAMES[i]) == n && memcmp(MOY_INPUT_NAMES[i], s, n) == 0) {
            return i;
        }
    }
    return -1;
}

static MP_NORETURN void check_fail(int rc) {
    if (rc == MOY_INPUT_FULL) {
        mp_raise_OSError(MP_ENOSPC);
    }
    if (rc == MOY_INPUT_NOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (rc == MOY_INPUT_STALE) {
        mp_raise_ValueError(MP_ERROR_TEXT("stale input handle"));
    }
    mp_raise_ValueError(MP_ERROR_TEXT("input: refused"));
}

static void check(int rc) {
    if (rc != MOY_INPUT_OK) {
        check_fail(rc);
    }
}

// -- the table ---------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_input_t *t;
    bool kernel;
    mp_obj_t pointer;
    mp_obj_t dict;                  // the shell's own attributes
    mp_obj_t srcs[MOY_INPUT_SOURCES];
    mp_obj_t order;                 // the last order button_masks packed for
    uint8_t perm[MOY_INPUT_BUTTONS];   // its name i's bit, 0xFF for none
    uint8_t order_n;
    bool order_prefix;              // order is NAMES[:order_n]
} input_table_obj_t;

typedef struct {
    mp_obj_base_t base;
    input_table_obj_t *table;
    uint32_t h;
    mp_obj_t name;
} input_source_obj_t;

typedef struct {
    mp_obj_base_t base;
    moy_input_ptr_t p;
} input_pointer_obj_t;

static const mp_obj_type_t input_table_type;
static const mp_obj_type_t host_table_type;
static const mp_obj_type_t input_source_type;
static const mp_obj_type_t input_pointer_type;

static input_table_obj_t *table_of(mp_obj_t o) {
    input_table_obj_t *s = MP_OBJ_TO_PTR(o);
    if (s->t == NULL) {
        mp_raise_ValueError(MP_ERROR_TEXT("input table released"));
    }
    return s;
}

static input_table_obj_t *table_new(const mp_obj_type_t *type, moy_input_t *t, bool kernel) {
    input_table_obj_t *s = mp_obj_malloc_with_finaliser(input_table_obj_t, type);
    s->t = t;
    s->kernel = kernel;
    s->pointer = mp_const_none;
    s->dict = mp_obj_new_dict(0);
    for (size_t i = 0; i < MOY_INPUT_SOURCES; i++) {
        s->srcs[i] = MP_OBJ_NULL;
    }
    s->order = MP_OBJ_NULL;
    return s;
}

static mp_obj_t table_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                               const mp_obj_t *args) {
    (void)args;
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    uint8_t n = type == &host_table_type ? MOY_INPUT_HOST_BUTTONS : MOY_INPUT_BUTTONS;
    moy_input_t *t = moy_input_new(n);
    if (t == NULL) {
        mp_raise_type(&mp_type_MemoryError);
    }
    return MP_OBJ_FROM_PTR(table_new(type, t, false));
}

static mp_obj_t table_del(mp_obj_t self_in) {
    input_table_obj_t *s = MP_OBJ_TO_PTR(self_in);
    if (s->t != NULL && !s->kernel) {
        moy_input_free(s->t);
    }
    s->t = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_del_obj, table_del);

static mp_obj_t source_of(input_table_obj_t *s, uint32_t h, mp_obj_t name) {
    uint32_t slot = h & 0xFFu;
    if (s->srcs[slot] == MP_OBJ_NULL) {
        input_source_obj_t *o = mp_obj_malloc(input_source_obj_t, &input_source_type);
        o->table = s;
        o->h = h;
        o->name = name;
        s->srcs[slot] = MP_OBJ_FROM_PTR(o);
    }
    return s->srcs[slot];
}

static mp_obj_t table_source(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    enum { ARG_name, ARG_player };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_name, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_player, MP_ARG_INT, {.u_int = 0} },
    };
    mp_arg_val_t a[2];
    mp_arg_parse_all(n_args - 1, pos + 1, kw, 2, allowed, a);
    input_table_obj_t *s = table_of(pos[0]);
    mp_int_t player = a[ARG_player].u_int;
    if (player < 0 || player >= (mp_int_t)MOY_INPUT_PLAYERS) {
        mp_raise_ValueError(MP_ERROR_TEXT("player out of range"));
    }
    uint32_t h;
    check(moy_input_source(s->t, mp_obj_str_get_str(a[ARG_name].u_obj), (uint8_t)player, &h));
    return source_of(s, h, a[ARG_name].u_obj);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(table_source_obj, 2, table_source);

#define TABLE_VERB(fn, call) \
    static mp_obj_t fn(mp_obj_t self_in) { \
        call(table_of(self_in)->t); \
        return mp_const_none; \
    } \
    static MP_DEFINE_CONST_FUN_OBJ_1(fn##_obj, fn)

TABLE_VERB(table_begin_frame, moy_input_begin_frame);
TABLE_VERB(table_release_all, moy_input_release_all);
TABLE_VERB(table_clear_edges, moy_input_clear_edges);
TABLE_VERB(table_keep_edges, moy_input_keep_edges);
TABLE_VERB(table_tick_edges, moy_input_tick_edges);
TABLE_VERB(table_drop_edges, moy_input_drop_edges);

static uint8_t player_arg(size_t n_args, const mp_obj_t *args, size_t i) {
    if (n_args <= i || args[i] == mp_const_none) {
        return MOY_INPUT_UNION;
    }
    mp_int_t p = mp_obj_get_int(args[i]);
    return (p < 0 || p >= (mp_int_t)MOY_INPUT_PLAYERS) ? MOY_INPUT_PLAYERS : (uint8_t)p;
}

static mp_obj_t table_held(size_t n_args, const mp_obj_t *args) {
    input_table_obj_t *s = table_of(args[0]);
    int b = name_bit(args[1]);
    uint32_t h, p;
    moy_input_masks(s->t, player_arg(n_args, args, 2), &h, &p);
    return mp_obj_new_bool(b >= 0 && (h >> b) & 1u);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(table_held_obj, 2, 3, table_held);

static mp_obj_t table_pressed(size_t n_args, const mp_obj_t *args) {
    input_table_obj_t *s = table_of(args[0]);
    int b = name_bit(args[1]);
    uint32_t h, p;
    moy_input_masks(s->t, player_arg(n_args, args, 2), &h, &p);
    return mp_obj_new_bool(b >= 0 && (p >> b) & 1u);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(table_pressed_obj, 2, 3, table_pressed);

static mp_obj_t table_released(mp_obj_t self_in, mp_obj_t name) {
    int b = name_bit(name);
    return mp_obj_new_bool(b >= 0 && (moy_input_released(table_of(self_in)->t) >> b) & 1u);
}
static MP_DEFINE_CONST_FUN_OBJ_2(table_released_obj, table_released);

static mp_obj_t table_any_held(mp_obj_t self_in) {
    uint32_t h, p;
    moy_input_masks(table_of(self_in)->t, MOY_INPUT_UNION, &h, &p);
    return mp_obj_new_bool(h != 0);
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_any_held_obj, table_any_held);

static mp_obj_t table_any_pressed(mp_obj_t self_in) {
    uint32_t h, p;
    moy_input_masks(table_of(self_in)->t, MOY_INPUT_UNION, &h, &p);
    return mp_obj_new_bool(p != 0);
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_any_pressed_obj, table_any_pressed);

static mp_obj_t names_set(uint32_t bits) {
    mp_obj_t set = mp_obj_new_set(0, NULL);
    for (size_t i = 0; i < MOY_INPUT_BUTTONS; i++) {
        if (bits & (1u << i)) {
            mp_obj_set_store(set, MP_OBJ_NEW_QSTR(NAME_Q[i]));
        }
    }
    return set;
}

static mp_obj_t table_held_names(mp_obj_t self_in) {
    uint32_t h, p;
    moy_input_masks(table_of(self_in)->t, MOY_INPUT_UNION, &h, &p);
    return names_set(h);
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_held_names_obj, table_held_names);

static mp_obj_t table_kept_names(mp_obj_t self_in) {
    return names_set(moy_input_kept(table_of(self_in)->t));
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_kept_names_obj, table_kept_names);

static void order_bind(input_table_obj_t *s, mp_obj_t order) {
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(order, &n, &items);
    if (n > MOY_INPUT_BUTTONS) {
        mp_raise_ValueError(MP_ERROR_TEXT("order: too many names"));
    }
    bool prefix = true;
    for (size_t i = 0; i < n; i++) {
        int b = name_bit(items[i]);
        s->perm[i] = b < 0 ? 0xFFu : (uint8_t)b;
        prefix = prefix && b == (int)i;
    }
    s->order = order;
    s->order_n = (uint8_t)n;
    s->order_prefix = prefix;
}

static uint32_t order_pack(const input_table_obj_t *s, uint32_t bits) {
    if (s->order_prefix) {
        return bits & ((1u << s->order_n) - 1u);
    }
    uint32_t out = 0;
    for (uint8_t i = 0; i < s->order_n; i++) {
        uint8_t b = s->perm[i];
        if (b != 0xFFu && (bits >> b) & 1u) {
            out |= 1u << i;
        }
    }
    return out;
}

// button_masks(order, player=None, out=None) -> (held, pressed), or `out` filled.
static mp_obj_t table_button_masks(size_t n_args, const mp_obj_t *args) {
    input_table_obj_t *s = table_of(args[0]);
    if (s->order != args[1]) {
        order_bind(s, args[1]);
    }
    uint32_t h, p;
    moy_input_masks(s->t, player_arg(n_args, args, 2), &h, &p);
    mp_obj_t hv = MP_OBJ_NEW_SMALL_INT(order_pack(s, h));
    mp_obj_t pv = MP_OBJ_NEW_SMALL_INT(order_pack(s, p));
    if (n_args > 3 && args[3] != mp_const_none) {
        mp_obj_subscr(args[3], MP_OBJ_NEW_SMALL_INT(0), hv);
        mp_obj_subscr(args[3], MP_OBJ_NEW_SMALL_INT(1), pv);
        return args[3];
    }
    mp_obj_t t[2] = {hv, pv};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(table_button_masks_obj, 2, 4, table_button_masks);

static mp_obj_t table_set_button(mp_obj_t self_in, mp_obj_t name, mp_obj_t held);

static mp_obj_t table_source_players(mp_obj_t self_in) {
    uint8_t out[MOY_INPUT_SOURCES];
    uint8_t n = moy_input_players(table_of(self_in)->t, out);
    mp_obj_t items[MOY_INPUT_SOURCES];
    for (uint8_t i = 0; i < n; i++) {
        items[i] = MP_OBJ_NEW_SMALL_INT(out[i]);
    }
    return mp_obj_new_tuple(n, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_source_players_obj, table_source_players);

static mp_obj_t table_player_count(mp_obj_t self_in) {
    uint8_t out[MOY_INPUT_SOURCES];
    return MP_OBJ_NEW_SMALL_INT(moy_input_players(table_of(self_in)->t, out));
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_player_count_obj, table_player_count);

static mp_obj_t table_multi(mp_obj_t self_in) {
    return mp_obj_new_bool(moy_input_multi(table_of(self_in)->t));
}
static MP_DEFINE_CONST_FUN_OBJ_1(table_multi_obj, table_multi);

static void set_held(input_table_obj_t *s, uint32_t h, mp_obj_t name, mp_obj_t held) {
    int b = name_bit(name);
    if (b < 0 || b >= moy_input_nbuttons(s->t)) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("unknown button: %s"),
                          mp_obj_is_str(name) ? mp_obj_str_get_str(name) : "?");
    }
    check(moy_input_set_held(s->t, h, (uint8_t)b, mp_obj_is_true(held)));
}

static mp_obj_t table_set_button(mp_obj_t self_in, mp_obj_t name, mp_obj_t held) {
    input_table_obj_t *s = table_of(self_in);
    uint32_t h;
    check(moy_input_source(s->t, "local", 0, &h));
    set_held(s, h, name, held);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(table_set_button_obj, table_set_button);

static mp_obj_t table_apply_pointer(mp_obj_t self_in, mp_obj_t ptr);
static MP_DEFINE_CONST_FUN_OBJ_2(table_apply_pointer_obj, table_apply_pointer);

#define TABLE_LOCALS(buttons) \
    { MP_ROM_QSTR(MP_QSTR_BUTTONS), MP_ROM_PTR(buttons) }, \
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&table_del_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_source), MP_ROM_PTR(&table_source_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_begin_frame), MP_ROM_PTR(&table_begin_frame_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_release_all), MP_ROM_PTR(&table_release_all_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_clear_edges), MP_ROM_PTR(&table_clear_edges_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_keep_edges), MP_ROM_PTR(&table_keep_edges_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_tick_edges), MP_ROM_PTR(&table_tick_edges_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_drop_edges), MP_ROM_PTR(&table_drop_edges_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_held), MP_ROM_PTR(&table_held_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_pressed), MP_ROM_PTR(&table_pressed_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_released), MP_ROM_PTR(&table_released_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_any_held), MP_ROM_PTR(&table_any_held_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_any_pressed), MP_ROM_PTR(&table_any_pressed_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_held_names), MP_ROM_PTR(&table_held_names_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_kept_names), MP_ROM_PTR(&table_kept_names_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_button_masks), MP_ROM_PTR(&table_button_masks_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_source_players), MP_ROM_PTR(&table_source_players_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_player_count), MP_ROM_PTR(&table_player_count_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_multi), MP_ROM_PTR(&table_multi_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_apply_pointer), MP_ROM_PTR(&table_apply_pointer_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_set_button), MP_ROM_PTR(&table_set_button_obj) }, \
    { MP_ROM_QSTR(MP_QSTR_set_held), MP_ROM_PTR(&table_set_button_obj) }

static const mp_rom_map_elem_t table_locals_table[] = { TABLE_LOCALS(&names_obj) };
static MP_DEFINE_CONST_DICT(table_locals, table_locals_table);

static const mp_rom_map_elem_t host_locals_table[] = { TABLE_LOCALS(&host_names_obj) };
static MP_DEFINE_CONST_DICT(host_locals, host_locals_table);

static void table_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    input_table_obj_t *s = MP_OBJ_TO_PTR(self_in);
    if (dest[0] == MP_OBJ_NULL) {
        if (attr == MP_QSTR_last_key) {
            dest[0] = MP_OBJ_NEW_SMALL_INT(s->t ? moy_input_last_key(s->t) : 0);
            return;
        }
        if (attr == MP_QSTR_text_mode) {
            dest[0] = mp_obj_new_bool(s->t && moy_input_text_mode(s->t));
            return;
        }
        if (attr == MP_QSTR_pointer) {
            dest[0] = s->pointer;
            return;
        }
        if (mp_map_lookup((mp_map_t *)&table_locals.map, MP_OBJ_NEW_QSTR(attr),
                          MP_MAP_LOOKUP) != NULL) {
            dest[1] = MP_OBJ_SENTINEL;      // a method, or BUTTONS: the type's locals
            return;
        }
        mp_map_elem_t *d = mp_map_lookup(mp_obj_dict_get_map(s->dict), MP_OBJ_NEW_QSTR(attr),
                                         MP_MAP_LOOKUP);
        if (d != NULL) {
            dest[0] = d->value;
        }
        return;
    }
    if (dest[0] != MP_OBJ_SENTINEL) {
        return;
    }
    mp_obj_t v = dest[1];
    if (attr == MP_QSTR_last_key && v != MP_OBJ_NULL) {
        moy_input_set_last_key(table_of(self_in)->t, v == mp_const_none ? 0 : mp_obj_get_int(v));
    } else if (attr == MP_QSTR_text_mode && v != MP_OBJ_NULL) {
        moy_input_set_text_mode(table_of(self_in)->t, mp_obj_is_true(v));
    } else if (attr == MP_QSTR_pointer && v != MP_OBJ_NULL) {
        s->pointer = v;
    } else if (v == MP_OBJ_NULL) {
        mp_map_t *m = mp_obj_dict_get_map(s->dict);
        if (mp_map_lookup(m, MP_OBJ_NEW_QSTR(attr), MP_MAP_LOOKUP_REMOVE_IF_FOUND) == NULL) {
            return;
        }
    } else {
        mp_obj_dict_store(s->dict, MP_OBJ_NEW_QSTR(attr), v);
    }
    dest[0] = MP_OBJ_NULL;
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_table_type, MP_QSTR_InputTable, MP_TYPE_FLAG_NONE,
    make_new, table_make_new,
    attr, table_attr,
    locals_dict, &table_locals
    );

static MP_DEFINE_CONST_OBJ_TYPE(
    host_table_type, MP_QSTR_HostInputTable, MP_TYPE_FLAG_NONE,
    make_new, table_make_new,
    attr, table_attr,
    locals_dict, &host_locals
    );

// The drivers' table, one wrapper for the VM's life.
static mp_obj_t input_kernel(void) {
    mp_obj_t o = MP_STATE_VM(moy_input_kernel_obj);
    if (o == MP_OBJ_NULL) {
        moy_input_t *t = moy_input_kernel();
        if (t == NULL) {
            mp_raise_type(&mp_type_MemoryError);
        }
        o = MP_OBJ_FROM_PTR(table_new(&input_table_type, t, true));
        MP_STATE_VM(moy_input_kernel_obj) = o;
    }
    return o;
}
static MP_DEFINE_CONST_FUN_OBJ_0(input_kernel_obj, input_kernel);

MP_REGISTER_ROOT_POINTER(mp_obj_t moy_input_kernel_obj);

// -- a source ----------------------------------------------------------------------

static mp_obj_t source_release_all(mp_obj_t self_in) {
    input_source_obj_t *o = MP_OBJ_TO_PTR(self_in);
    check(moy_input_release(table_of(MP_OBJ_FROM_PTR(o->table))->t, o->h));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(source_release_all_obj, source_release_all);

static mp_obj_t source_set_button(mp_obj_t self_in, mp_obj_t name, mp_obj_t held) {
    input_source_obj_t *o = MP_OBJ_TO_PTR(self_in);
    set_held(table_of(MP_OBJ_FROM_PTR(o->table)), o->h, name, held);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(source_set_button_obj, source_set_button);

static mp_obj_t source_key(mp_obj_t self_in, mp_obj_t key) {
    input_source_obj_t *o = MP_OBJ_TO_PTR(self_in);
    check(moy_input_key(table_of(MP_OBJ_FROM_PTR(o->table))->t, o->h, mp_obj_get_int(key)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(source_key_obj, source_key);

// point(x, y, down, edge=False, fresh=True): this source's pointer sample.
static mp_obj_t source_point(size_t n_args, const mp_obj_t *args) {
    input_source_obj_t *o = MP_OBJ_TO_PTR(args[0]);
    bool edge = n_args > 4 && mp_obj_is_true(args[4]);
    bool fresh = n_args <= 5 || mp_obj_is_true(args[5]);
    check(moy_input_point(table_of(MP_OBJ_FROM_PTR(o->table))->t, o->h, coord(args[1]),
                          coord(args[2]), mp_obj_is_true(args[3]), edge, fresh));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(source_point_obj, 4, 6, source_point);

static mp_obj_t source_held_names(mp_obj_t self_in) {
    input_source_obj_t *o = MP_OBJ_TO_PTR(self_in);
    return names_set(moy_input_source_held(table_of(MP_OBJ_FROM_PTR(o->table))->t, o->h));
}
static MP_DEFINE_CONST_FUN_OBJ_1(source_held_names_obj, source_held_names);

static const mp_rom_map_elem_t source_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_release_all), MP_ROM_PTR(&source_release_all_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_button), MP_ROM_PTR(&source_set_button_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_held), MP_ROM_PTR(&source_set_button_obj) },
    { MP_ROM_QSTR(MP_QSTR_key), MP_ROM_PTR(&source_key_obj) },
    { MP_ROM_QSTR(MP_QSTR_held_names), MP_ROM_PTR(&source_held_names_obj) },
    { MP_ROM_QSTR(MP_QSTR_point), MP_ROM_PTR(&source_point_obj) },
};
static MP_DEFINE_CONST_DICT(source_locals, source_locals_table);

static void source_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    input_source_obj_t *o = MP_OBJ_TO_PTR(self_in);
    if (dest[0] == MP_OBJ_NULL) {
        if (attr == MP_QSTR_state) {
            dest[0] = MP_OBJ_FROM_PTR(o->table);
        } else if (attr == MP_QSTR_name) {
            dest[0] = o->name;
        } else if (attr == MP_QSTR_h) {
            dest[0] = mp_obj_new_int_from_uint(o->h);
        } else if (attr == MP_QSTR_player) {
            uint8_t p;
            check(moy_input_source_player(table_of(MP_OBJ_FROM_PTR(o->table))->t, o->h, &p));
            dest[0] = MP_OBJ_NEW_SMALL_INT(p);
        } else if (attr == MP_QSTR_last_key) {
            int32_t k;
            check(moy_input_source_key(table_of(MP_OBJ_FROM_PTR(o->table))->t, o->h, &k));
            dest[0] = MP_OBJ_NEW_SMALL_INT(k);
        } else {
            dest[1] = MP_OBJ_SENTINEL;
        }
        return;
    }
    if (dest[0] != MP_OBJ_SENTINEL || dest[1] == MP_OBJ_NULL) {
        return;
    }
    moy_input_t *t = table_of(MP_OBJ_FROM_PTR(o->table))->t;
    if (attr == MP_QSTR_player) {
        mp_int_t p = mp_obj_get_int(dest[1]);
        if (p < 0 || p >= (mp_int_t)MOY_INPUT_PLAYERS) {
            mp_raise_ValueError(MP_ERROR_TEXT("player out of range"));
        }
        check(moy_input_set_player(t, o->h, (uint8_t)p));
        dest[0] = MP_OBJ_NULL;
    } else if (attr == MP_QSTR_last_key) {
        check(moy_input_set_key(t, o->h, dest[1] == mp_const_none ? 0 : mp_obj_get_int(dest[1])));
        dest[0] = MP_OBJ_NULL;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_source_type, MP_QSTR_InputSource, MP_TYPE_FLAG_NONE,
    attr, source_attr,
    locals_dict, &source_locals
    );

// -- the pointer -------------------------------------------------------------------

static mp_obj_t pointer_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                                 const mp_obj_t *args) {
    enum { ARG_w, ARG_h, ARG_idle_ms };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_w, MP_ARG_REQUIRED | MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_h, MP_ARG_REQUIRED | MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_idle_ms, MP_ARG_INT, {.u_int = MOY_INPUT_CURSOR_IDLE_MS} },
    };
    mp_arg_val_t a[3];
    mp_arg_parse_all_kw_array(n_args, n_kw, args, 3, allowed, a);
    input_pointer_obj_t *o = mp_obj_malloc(input_pointer_obj_t, type);
    moy_input_ptr_init(&o->p, a[ARG_w].u_int, a[ARG_h].u_int, a[ARG_idle_ms].u_int, now_ms());
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t pointer_move(mp_obj_t self_in, mp_obj_t dx, mp_obj_t dy) {
    input_pointer_obj_t *o = MP_OBJ_TO_PTR(self_in);
    moy_input_ptr_move(&o->p, coord(dx), coord(dy), now_ms());
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(pointer_move_obj, pointer_move);

static mp_obj_t pointer_place(mp_obj_t self_in, mp_obj_t x, mp_obj_t y) {
    input_pointer_obj_t *o = MP_OBJ_TO_PTR(self_in);
    moy_input_ptr_place(&o->p, coord(x), coord(y), now_ms());
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(pointer_place_obj, pointer_place);

static mp_obj_t pointer_live(mp_obj_t self_in) {
    input_pointer_obj_t *o = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(moy_input_ptr_live(&o->p, now_ms()));
}
static MP_DEFINE_CONST_FUN_OBJ_1(pointer_live_obj, pointer_live);

static mp_obj_t pointer_tick(mp_obj_t self_in, mp_obj_t now) {
    input_pointer_obj_t *o = MP_OBJ_TO_PTR(self_in);
    moy_input_ptr_tick(&o->p, (uint32_t)mp_obj_get_int_truncated(now) & (MOY_INPUT_TICKS_PERIOD - 1u));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(pointer_tick_obj, pointer_tick);

static const mp_rom_map_elem_t pointer_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_move), MP_ROM_PTR(&pointer_move_obj) },
    { MP_ROM_QSTR(MP_QSTR_place), MP_ROM_PTR(&pointer_place_obj) },
    { MP_ROM_QSTR(MP_QSTR_live), MP_ROM_PTR(&pointer_live_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick), MP_ROM_PTR(&pointer_tick_obj) },
};
static MP_DEFINE_CONST_DICT(pointer_locals, pointer_locals_table);

static int32_t *pointer_int(moy_input_ptr_t *p, qstr attr) {
    switch (attr) {
        case MP_QSTR_w: return &p->w;
        case MP_QSTR_h: return &p->h;
        case MP_QSTR_x: return &p->x;
        case MP_QSTR_y: return &p->y;
        case MP_QSTR_idle_ms: return &p->idle_ms;
        case MP_QSTR__sampled: return (int32_t *)&p->sampled;
        case MP_QSTR__last_move: return (int32_t *)&p->last_move;
        default: return NULL;
    }
}

static bool *pointer_flag(moy_input_ptr_t *p, qstr attr) {
    switch (attr) {
        case MP_QSTR_click: return &p->click;
        case MP_QSTR_down: return &p->down;
        case MP_QSTR_hovers: return &p->hovers;
        case MP_QSTR_fresh: return &p->fresh;
        case MP_QSTR_visible: return &p->visible;
        default: return NULL;
    }
}

static void pointer_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    input_pointer_obj_t *o = MP_OBJ_TO_PTR(self_in);
    int32_t *i = pointer_int(&o->p, attr);
    bool *f = i ? NULL : pointer_flag(&o->p, attr);
    if (dest[0] == MP_OBJ_NULL) {
        if (i) {
            dest[0] = MP_OBJ_NEW_SMALL_INT(*i);
        } else if (f) {
            dest[0] = mp_obj_new_bool(*f);
        } else {
            dest[1] = MP_OBJ_SENTINEL;
        }
        return;
    }
    if (dest[0] != MP_OBJ_SENTINEL || dest[1] == MP_OBJ_NULL) {
        return;
    }
    if (i) {
        *i = mp_obj_is_float(dest[1]) ? coord(dest[1]) : (int32_t)mp_obj_get_int_truncated(dest[1]);
        if (attr == MP_QSTR__sampled || attr == MP_QSTR__last_move) {
            *i &= (int32_t)(MOY_INPUT_TICKS_PERIOD - 1u);
        }
        dest[0] = MP_OBJ_NULL;
    } else if (f) {
        *f = mp_obj_is_true(dest[1]);
        dest[0] = MP_OBJ_NULL;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_pointer_type, MP_QSTR_Pointer, MP_TYPE_FLAG_NONE,
    make_new, pointer_make_new,
    attr, pointer_attr,
    locals_dict, &pointer_locals
    );

// apply_pointer(pointer) -> P_HELD | P_CLICK: the frame's sample into it.
static mp_obj_t table_apply_pointer(mp_obj_t self_in, mp_obj_t ptr) {
    if (!mp_obj_is_type(ptr, &input_pointer_type)) {
        mp_raise_TypeError(MP_ERROR_TEXT("apply_pointer: not a Pointer"));
    }
    input_pointer_obj_t *po = MP_OBJ_TO_PTR(ptr);
    return MP_OBJ_NEW_SMALL_INT(moy_input_ptr_apply(table_of(self_in)->t, &po->p, now_ms()));
}

// -- the pointer a cart sees ---------------------------------------------------------

static mp_obj_t attr_or(mp_obj_t o, qstr attr, mp_obj_t dflt) {
    mp_obj_t dest[2];
    mp_load_method_maybe(o, attr, dest);
    if (dest[0] == MP_OBJ_NULL) {
        return dflt;
    }
    return dest[1] == MP_OBJ_NULL ? dest[0] : mp_call_method_n_kw(0, 0, dest);
}

// pointer_state(inp, out) -> out, filled [x, y, P_*, 0]. A linked match has no
// pointer; liveness is the pointer's own; the game-space publication
// (`inp.game_pointer`, #39) wins the coordinates where the console makes one.
static mp_obj_t input_pointer_state(mp_obj_t inp, mp_obj_t out) {
    mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(2), MP_OBJ_NEW_SMALL_INT(MOY_INPUT_P_NONE));
    if (mp_obj_is_true(attr_or(inp, MP_QSTR_netplay_live, mp_const_false))) {
        return out;
    }
    mp_obj_t p = attr_or(inp, MP_QSTR_pointer, mp_const_none);
    if (p == mp_const_none) {
        return out;
    }
    int x, y, st;
    if (mp_obj_is_type(p, &input_pointer_type)) {
        input_pointer_obj_t *po = MP_OBJ_TO_PTR(p);
        if (!moy_input_ptr_live(&po->p, now_ms())) {
            return out;
        }
        x = po->p.x;
        y = po->p.y;
        st = MOY_INPUT_P_LIVE | (po->p.down ? MOY_INPUT_P_HELD : 0)
             | (po->p.click ? MOY_INPUT_P_CLICK : 0);
    } else {
        mp_obj_t live[2];
        mp_load_method_maybe(p, MP_QSTR_live, live);
        if (live[0] != MP_OBJ_NULL && live[1] != MP_OBJ_NULL
            && !mp_obj_is_true(mp_call_method_n_kw(0, 0, live))) {
            return out;
        }
        x = mp_obj_get_int(attr_or(p, MP_QSTR_x, MP_OBJ_NEW_SMALL_INT(0)));
        y = mp_obj_get_int(attr_or(p, MP_QSTR_y, MP_OBJ_NEW_SMALL_INT(0)));
        st = MOY_INPUT_P_LIVE
             | (mp_obj_is_true(attr_or(p, MP_QSTR_down, mp_const_false)) ? MOY_INPUT_P_HELD : 0)
             | (mp_obj_is_true(attr_or(p, MP_QSTR_click, mp_const_false)) ? MOY_INPUT_P_CLICK : 0);
    }
    mp_obj_t gp = attr_or(inp, MP_QSTR_game_pointer, mp_const_none);
    if (gp != mp_const_none) {
        mp_obj_t gx = mp_obj_subscr(gp, MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_SENTINEL);
        mp_obj_t gy = mp_obj_subscr(gp, MP_OBJ_NEW_SMALL_INT(1), MP_OBJ_SENTINEL);
        st = MOY_INPUT_P_LIVE
             | (mp_obj_is_true(mp_obj_subscr(gp, MP_OBJ_NEW_SMALL_INT(2), MP_OBJ_SENTINEL))
                ? MOY_INPUT_P_CLICK : 0);
        if (mp_obj_get_int(mp_obj_len(gp)) > 3
            && mp_obj_is_true(mp_obj_subscr(gp, MP_OBJ_NEW_SMALL_INT(3), MP_OBJ_SENTINEL))) {
            st |= MOY_INPUT_P_HELD;
        }
        mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(0), gx);
        mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(1), gy);
    } else {
        mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_NEW_SMALL_INT(x));
        mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(1), MP_OBJ_NEW_SMALL_INT(y));
    }
    mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(2), MP_OBJ_NEW_SMALL_INT(st));
    mp_obj_subscr(out, MP_OBJ_NEW_SMALL_INT(3), MP_OBJ_NEW_SMALL_INT(0));
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_2(input_pointer_state_obj, input_pointer_state);


// -- the board's drivers (moy_input_task.c) ------------------------------------------
//
// keyboard() -> Keyboard or None: the T-Deck's C3, writing the kernel table's
//   "kbd" source from the input task. set_game_mode(on) queues the mode flip.
// touch(w, h) -> Touch or None: the board's controller; poll() answers the
//   pointer sample the way every touch driver did, [x, y, press_edge] (one list,
//   the driver's own, rewritten by the next poll) or None, `fresh` beside it.
// trackball() -> Trackball or None: poll() -> [[up, down, left, right], click].
// kick(): one pass of the board's drivers (on the input task where the board
//   has one); the frame's input stage calls it once.

static mp_obj_t stat_first_big(const moy_touchdev_t *d) {
    if (!d->fb_set) {
        return mp_const_none;
    }
    static const qstr PHASE[3] = {MP_QSTR_status, MP_QSTR_point, MP_QSTR_clear};
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(d->fb_ms), MP_OBJ_NEW_QSTR(PHASE[d->fb_phase % 3]),
        d->fb_status < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(d->fb_status),
        mp_obj_new_int_from_uint(d->fb_n),
    };
    return mp_obj_new_tuple(4, t);
}

typedef struct {
    mp_obj_base_t base;
    moy_kbd_t *k;
} input_kbd_obj_t;

static mp_obj_t kbd_set_game_mode(mp_obj_t self_in, mp_obj_t on) {
    input_kbd_obj_t *o = MP_OBJ_TO_PTR(self_in);
    moy_kbd_game_mode(o->k, mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(kbd_set_game_mode_obj, kbd_set_game_mode);

static const mp_rom_map_elem_t kbd_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_set_game_mode), MP_ROM_PTR(&kbd_set_game_mode_obj) },
};
static MP_DEFINE_CONST_DICT(kbd_locals, kbd_locals_table);

static void kbd_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    moy_kbd_t *k = ((input_kbd_obj_t *)MP_OBJ_TO_PTR(self_in))->k;
    if (dest[0] == MP_OBJ_SENTINEL) {
        if (attr == MP_QSTR_RAW_GAME_MODE && dest[1] != MP_OBJ_NULL) {
            k->raw_game = mp_obj_is_true(dest[1]);
            dest[0] = MP_OBJ_NULL;
        }
        return;
    }
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    switch (attr) {
        case MP_QSTR_available: dest[0] = mp_obj_new_bool(k->available); break;
        case MP_QSTR_raw_mode: dest[0] = mp_obj_new_bool(k->raw_mode); break;
        case MP_QSTR_RAW_GAME_MODE: dest[0] = mp_obj_new_bool(k->raw_game); break;
        case MP_QSTR_raw_unsupported: dest[0] = mp_obj_new_bool(k->raw_unsupported); break;
        case MP_QSTR_stat_n: dest[0] = mp_obj_new_int_from_uint(k->stat_n); break;
        case MP_QSTR_stat_max_us: dest[0] = mp_obj_new_int_from_uint(k->stat_max_us); break;
        case MP_QSTR_stat_max_raw: dest[0] = mp_obj_new_bool(k->stat_max_raw); break;
        case MP_QSTR_stat_over5: dest[0] = mp_obj_new_int_from_uint(k->stat_over5); break;
        case MP_QSTR_stat_over20: dest[0] = mp_obj_new_int_from_uint(k->stat_over20); break;
        case MP_QSTR_stat_timeouts: dest[0] = mp_obj_new_int_from_uint(k->stat_timeouts); break;
        default: dest[1] = MP_OBJ_SENTINEL; break;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_kbd_type, MP_QSTR_Keyboard, MP_TYPE_FLAG_NONE,
    attr, kbd_attr,
    locals_dict, &kbd_locals
    );

static mp_obj_t input_keyboard(void) {
    moy_kbd_t *k;
    if (!moy_input_board_kbd(&k)) {
        return mp_const_none;
    }
    input_kbd_obj_t *o = mp_obj_malloc(input_kbd_obj_t, &input_kbd_type);
    o->k = k;
    return MP_OBJ_FROM_PTR(o);
}
static MP_DEFINE_CONST_FUN_OBJ_0(input_keyboard_obj, input_keyboard);

typedef struct {
    mp_obj_base_t base;
    moy_touchdev_t *d;
    uint32_t src;                   // the kernel table's "touch" source
    mp_obj_t out;                   // [x, y, edge], rewritten by every poll
} input_touch_obj_t;

static mp_obj_t touch_poll(mp_obj_t self_in) {
    input_touch_obj_t *o = MP_OBJ_TO_PTR(self_in);
    moy_touch_pt_t pt;
    moy_touchdev_poll(o->d, now_ms(), &pt);
    moy_input_point(moy_input_kernel(), o->src, pt.x, pt.y, pt.down, pt.edge, o->d->held.fresh);
    if (!pt.down) {
        return mp_const_none;
    }
    mp_obj_list_t *l = MP_OBJ_TO_PTR(o->out);
    l->items[0] = MP_OBJ_NEW_SMALL_INT(pt.x);
    l->items[1] = MP_OBJ_NEW_SMALL_INT(pt.y);
    l->items[2] = mp_obj_new_bool(pt.edge);
    return o->out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(touch_poll_obj, touch_poll);

static const mp_rom_map_elem_t touch_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&touch_poll_obj) },
};
static MP_DEFINE_CONST_DICT(touch_locals, touch_locals_table);

static int32_t *touch_knob(moy_touchdev_t *d, qstr attr) {
    switch (attr) {
        case MP_QSTR_w: return &d->map.w;
        case MP_QSTR_h: return &d->map.h;
        case MP_QSTR_raw_w: return &d->map.raw_w;
        case MP_QSTR_raw_h: return &d->map.raw_h;
        case MP_QSTR_raw_x0: return &d->map.raw_x0;
        case MP_QSTR_raw_y0: return &d->map.raw_y0;
        default: return NULL;
    }
}

static bool *touch_flag(moy_touchdev_t *d, qstr attr) {
    switch (attr) {
        case MP_QSTR_swap_xy: return &d->map.swap;
        case MP_QSTR_flip_x: return &d->map.flip_x;
        case MP_QSTR_flip_y: return &d->map.flip_y;
        default: return NULL;
    }
}

static void touch_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    moy_touchdev_t *d = ((input_touch_obj_t *)MP_OBJ_TO_PTR(self_in))->d;
    int32_t *knob = touch_knob(d, attr);
    bool *flag = knob ? NULL : touch_flag(d, attr);
    if (dest[0] == MP_OBJ_SENTINEL) {
        if (dest[1] != MP_OBJ_NULL && (knob || flag)) {
            if (knob) {
                *knob = mp_obj_get_int(dest[1]);
            } else {
                *flag = mp_obj_is_true(dest[1]);
            }
            dest[0] = MP_OBJ_NULL;
        }
        return;
    }
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    if (knob) {
        dest[0] = MP_OBJ_NEW_SMALL_INT(*knob);
        return;
    }
    if (flag) {
        dest[0] = mp_obj_new_bool(*flag);
        return;
    }
    switch (attr) {
        case MP_QSTR_available: dest[0] = mp_obj_new_bool(d->available); break;
        case MP_QSTR_fresh: dest[0] = mp_obj_new_bool(d->held.fresh); break;
        case MP_QSTR_fingers: dest[0] = MP_OBJ_NEW_SMALL_INT(d->fingers); break;
        case MP_QSTR_loaded: dest[0] = mp_obj_new_int_from_uint(d->loaded); break;
        case MP_QSTR_addr: dest[0] = MP_OBJ_NEW_SMALL_INT(d->addr); break;
        case MP_QSTR_raw: {
            if (!d->has_raw) {
                dest[0] = mp_const_none;
            } else {
                mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(d->raw_x), MP_OBJ_NEW_SMALL_INT(d->raw_y)};
                dest[0] = mp_obj_new_tuple(2, t);
            }
            break;
        }
        case MP_QSTR_stat_n: dest[0] = mp_obj_new_int_from_uint(d->stat_n); break;
        case MP_QSTR_stat_max_us: dest[0] = mp_obj_new_int_from_uint(d->stat_max_us); break;
        case MP_QSTR_stat_over5: dest[0] = mp_obj_new_int_from_uint(d->stat_over5); break;
        case MP_QSTR_stat_over20: dest[0] = mp_obj_new_int_from_uint(d->stat_over20); break;
        case MP_QSTR_stat_skipped: dest[0] = mp_obj_new_int_from_uint(d->stat_skipped); break;
        case MP_QSTR_stat_int_edges: dest[0] = mp_obj_new_int_from_uint(d->int_count); break;
        case MP_QSTR_stat_first_big: dest[0] = stat_first_big(d); break;
        case MP_QSTR_gate: dest[0] = mp_obj_new_bool(d->gate); break;
        default: dest[1] = MP_OBJ_SENTINEL; break;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_touch_type, MP_QSTR_Touch, MP_TYPE_FLAG_NONE,
    attr, touch_attr,
    locals_dict, &touch_locals
    );

static mp_obj_t input_touch(mp_obj_t w, mp_obj_t h) {
    moy_touchdev_t *d;
    if (!moy_input_board_touch(&d, mp_obj_get_int(w), mp_obj_get_int(h))) {
        return mp_const_none;
    }
    uint32_t src;
    moy_input_t *t = moy_input_kernel();
    if (t == NULL || moy_input_source(t, "touch", 0, &src) != MOY_INPUT_OK) {
        return mp_const_none;
    }
    input_touch_obj_t *o = mp_obj_malloc(input_touch_obj_t, &input_touch_type);
    o->d = d;
    o->src = src;
    mp_obj_t items[3] = {MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_NEW_SMALL_INT(0), mp_const_false};
    o->out = mp_obj_new_list(3, items);
    return MP_OBJ_FROM_PTR(o);
}
static MP_DEFINE_CONST_FUN_OBJ_2(input_touch_obj, input_touch);

typedef struct {
    mp_obj_base_t base;
    moy_ball_t *b;
    bool prev;                      // the click was down at the last poll
    mp_obj_t counts;                // [up, down, left, right]
    mp_obj_t out;                   // [counts, click]
} input_ball_obj_t;

static mp_obj_t ball_poll(mp_obj_t self_in) {
    input_ball_obj_t *o = MP_OBJ_TO_PTR(self_in);
    uint32_t c[4];
    bool down;
    moy_ball_t *b;
    moy_input_board_ball(&b, &down);
    moy_ball_take(o->b, c);
    mp_obj_list_t *l = MP_OBJ_TO_PTR(o->counts);
    for (int i = 0; i < 4; i++) {
        l->items[i] = MP_OBJ_NEW_SMALL_INT(c[i]);
    }
    mp_obj_list_t *out = MP_OBJ_TO_PTR(o->out);
    out->items[1] = mp_obj_new_bool(down && !o->prev);
    o->prev = down;
    return o->out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ball_poll_obj, ball_poll);

static const mp_rom_map_elem_t ball_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&ball_poll_obj) },
};
static MP_DEFINE_CONST_DICT(ball_locals, ball_locals_table);

static void ball_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    (void)self_in;
    if (dest[0] == MP_OBJ_NULL && attr == MP_QSTR_available) {
        dest[0] = mp_const_true;
    } else if (dest[0] == MP_OBJ_NULL) {
        dest[1] = MP_OBJ_SENTINEL;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_ball_type, MP_QSTR_Trackball, MP_TYPE_FLAG_NONE,
    attr, ball_attr,
    locals_dict, &ball_locals
    );

static mp_obj_t input_trackball(void) {
    moy_ball_t *b;
    bool down;
    if (!moy_input_board_ball(&b, &down)) {
        return mp_const_none;
    }
    input_ball_obj_t *o = mp_obj_malloc(input_ball_obj_t, &input_ball_type);
    o->b = b;
    o->prev = down;
    mp_obj_t z[4] = {MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_NEW_SMALL_INT(0),
                     MP_OBJ_NEW_SMALL_INT(0)};
    o->counts = mp_obj_new_list(4, z);
    mp_obj_t out[2] = {o->counts, mp_const_false};
    o->out = mp_obj_new_list(2, out);
    return MP_OBJ_FROM_PTR(o);
}
static MP_DEFINE_CONST_FUN_OBJ_0(input_trackball_obj, input_trackball);


// ble() -> the BLE HID keyboard, or None where the board takes no BLE: the
//   kernel's central (moy_ble_task.c over moy_hid.c), writing the kernel
//   table's "ble" source. start() brings the radio up; poll() takes the
//   reports since the last frame into the source (before begin_frame); the
//   Settings panel's verbs are queued for NimBLE's host task.

typedef struct {
    mp_obj_base_t base;
    moy_hid_t *h;
    bool left;                      // the mouse's left button at the last apply
} input_ble_obj_t;

static mp_obj_t ble_str_or_none(const char *s) {
    return s[0] ? mp_obj_new_str(s, strlen(s)) : mp_const_none;
}

static mp_obj_t ble_addr(const moy_hid_addr_t *a) {
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(a->type), mp_obj_new_bytes(a->a, 6)};
    return mp_obj_new_tuple(2, t);
}

static mp_obj_t ble_state(const moy_hid_t *h) {
    return mp_obj_new_str(MOY_HID_STATES[h->state], strlen(MOY_HID_STATES[h->state]));
}

static moy_hid_t *ble_of(mp_obj_t self_in) {
    return ((input_ble_obj_t *)MP_OBJ_TO_PTR(self_in))->h;
}

static mp_obj_t ble_start(mp_obj_t self_in) {
    (void)self_in;
    return mp_obj_new_bool(moy_ble_start());
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_start_obj, ble_start);

static mp_obj_t ble_stop(mp_obj_t self_in) {
    bool was = moy_ble_up();
    moy_ble_stop();
    (void)self_in;
    return mp_obj_new_bool(was);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_stop_obj, ble_stop);

static mp_obj_t ble_poll(mp_obj_t self_in) {
    moy_hid_frame(ble_of(self_in));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_poll_obj, ble_poll);

static mp_obj_t ble_status(mp_obj_t self_in) {
    moy_hid_t *h = ble_of(self_in);
    mp_obj_t t[3] = {ble_state(h), ble_str_or_none(h->name), mp_const_none};
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_status_obj, ble_status);

static mp_obj_t ble_settings_status(mp_obj_t self_in) {
    moy_hid_t *h = ble_of(self_in);
    mp_obj_t t[5] = {
        mp_obj_new_bool(h->enabled), ble_state(h), ble_str_or_none(h->name),
        h->has_pref ? ble_addr(&h->pref) : mp_const_none, ble_str_or_none(h->error),
    };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_settings_status_obj, ble_settings_status);

// ((address, name, rssi, preferred, connected), ...) in display order; the
// address is opaque to the shell, which hands it back to connect_device.
static mp_obj_t ble_settings_devices(mp_obj_t self_in) {
    moy_hid_t *h = ble_of(self_in);
    moy_hid_dev_t devs[MOY_HID_DEVICES];
    MOY_DRV_LOCK(&h->lock);
    uint8_t n = h->ndev;
    memcpy(devs, h->devs, sizeof(devs));
    MOY_DRV_UNLOCK(&h->lock);
    mp_obj_t rows[MOY_HID_DEVICES];
    for (uint8_t i = 0; i < n; i++) {
        moy_hid_dev_t *d = &devs[i];
        bool pref = h->has_pref && d->addr.type == h->pref.type
                    && memcmp(d->addr.a, h->pref.a, 6) == 0;
        bool conn = h->conn >= 0 && h->has_cand && d->addr.type == h->cand.type
                    && memcmp(d->addr.a, h->cand.a, 6) == 0;
        mp_obj_t row[5] = {ble_addr(&d->addr), mp_obj_new_str(d->name, strlen(d->name)),
                           MP_OBJ_NEW_SMALL_INT(d->rssi), mp_obj_new_bool(pref),
                           mp_obj_new_bool(conn)};
        rows[i] = mp_obj_new_tuple(5, row);
    }
    return mp_obj_new_tuple(n, rows);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_settings_devices_obj, ble_settings_devices);

static mp_obj_t ble_set_enabled(mp_obj_t self_in, mp_obj_t on) {
    bool want = mp_obj_is_true(on);
    if (want && !moy_ble_up()) {
        ble_of(self_in)->enabled = true;
        moy_ble_verb(MOY_BLE_ENABLE, NULL);
        moy_ble_start();
    } else {
        moy_ble_verb(want ? MOY_BLE_ENABLE : MOY_BLE_DISABLE, NULL);
    }
    return mp_obj_new_bool(want);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ble_set_enabled_obj, ble_set_enabled);

static mp_obj_t ble_discover(mp_obj_t self_in) {
    return mp_obj_new_bool(ble_of(self_in)->enabled && moy_ble_verb(MOY_BLE_DISCOVER, NULL));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_discover_obj, ble_discover);

static mp_obj_t ble_connect_device(mp_obj_t self_in, mp_obj_t address) {
    moy_hid_t *h = ble_of(self_in);
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(address, &n, &items);
    if (n != 2) {
        return mp_const_false;
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(items[1], &bi, MP_BUFFER_READ);
    if (bi.len != 6) {
        return mp_const_false;
    }
    moy_hid_addr_t a = {.type = (uint8_t)mp_obj_get_int(items[0])};
    memcpy(a.a, bi.buf, 6);
    bool known = false;
    MOY_DRV_LOCK(&h->lock);
    for (uint8_t i = 0; i < h->ndev; i++) {
        known = known || (h->devs[i].addr.type == a.type && memcmp(h->devs[i].addr.a, a.a, 6) == 0);
    }
    MOY_DRV_UNLOCK(&h->lock);
    return mp_obj_new_bool(known && moy_ble_verb(MOY_BLE_PICK, &a));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ble_connect_device_obj, ble_connect_device);

static mp_obj_t ble_forget(mp_obj_t self_in) {
    (void)self_in;
    moy_ble_verb(MOY_BLE_FORGET, NULL);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_forget_obj, ble_forget);

static mp_obj_t ble_scan(mp_obj_t self_in) {
    moy_hid_t *h = ble_of(self_in);
    return mp_obj_new_bool(h->available && h->enabled && moy_ble_verb(MOY_BLE_SCAN, NULL));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ble_scan_obj, ble_scan);

static mp_obj_t ble_set_player(mp_obj_t self_in, mp_obj_t slot) {
    moy_hid_set_player(ble_of(self_in), (int8_t)mp_obj_get_int(slot));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ble_set_player_obj, ble_set_player);

// apply_mouse(pointer) -> True while a mouse is connected: its motion since the
// last frame moves the pointer, which a mouse never lets expire (hovers), and
// its left button is the "ble" source's pointer sample -- down, and the press
// edge -- for the merge. Before begin_frame, like poll().
static mp_obj_t ble_apply_mouse(mp_obj_t self_in, mp_obj_t ptr) {
    input_ble_obj_t *o = MP_OBJ_TO_PTR(self_in);
    int32_t dx, dy;
    uint8_t buttons;
    if (!moy_hid_take_mouse(o->h, &dx, &dy, &buttons) || !mp_obj_is_type(ptr, &input_pointer_type)) {
        o->left = false;
        return mp_const_false;
    }
    moy_input_ptr_t *p = &((input_pointer_obj_t *)MP_OBJ_TO_PTR(ptr))->p;
    uint32_t now = now_ms();
    if (dx || dy) {
        moy_input_ptr_move(p, dx, dy, now);
    }
    p->hovers = true;
    bool left = (buttons & 1) != 0;
    moy_input_point(o->h->table, o->h->src, p->x, p->y, left, left && !o->left, true);
    o->left = left;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ble_apply_mouse_obj, ble_apply_mouse);

// BLE reports carry text and make/break state: no mode to flip.
static mp_obj_t ble_set_game_mode(mp_obj_t self_in, mp_obj_t on) {
    (void)self_in;
    (void)on;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ble_set_game_mode_obj, ble_set_game_mode);

static mp_obj_t ble_none_1(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    (void)args;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ble_fast_status_obj, 1, 1, ble_none_1);

// The raw-notification trace is not carried: it declines.
static mp_obj_t ble_trace(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    (void)args;
    return mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ble_trace_obj, 1, 2, ble_trace);

static const mp_rom_map_elem_t ble_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&ble_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&ble_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&ble_poll_obj) },
    { MP_ROM_QSTR(MP_QSTR_apply_mouse), MP_ROM_PTR(&ble_apply_mouse_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&ble_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_settings_status), MP_ROM_PTR(&ble_settings_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_settings_devices), MP_ROM_PTR(&ble_settings_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_enabled), MP_ROM_PTR(&ble_set_enabled_obj) },
    { MP_ROM_QSTR(MP_QSTR_discover_devices), MP_ROM_PTR(&ble_discover_obj) },
    { MP_ROM_QSTR(MP_QSTR_connect_device), MP_ROM_PTR(&ble_connect_device_obj) },
    { MP_ROM_QSTR(MP_QSTR_forget), MP_ROM_PTR(&ble_forget_obj) },
    { MP_ROM_QSTR(MP_QSTR_scan), MP_ROM_PTR(&ble_scan_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_player), MP_ROM_PTR(&ble_set_player_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_game_mode), MP_ROM_PTR(&ble_set_game_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_fast_status), MP_ROM_PTR(&ble_fast_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace), MP_ROM_PTR(&ble_trace_obj) },
};
static MP_DEFINE_CONST_DICT(ble_locals, ble_locals_table);

static void ble_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    moy_hid_t *h = ble_of(self_in);
    switch (attr) {
        case MP_QSTR_available: dest[0] = mp_obj_new_bool(h->available); break;
        case MP_QSTR_state: dest[0] = ble_state(h); break;
        case MP_QSTR_name: dest[0] = ble_str_or_none(h->name); break;
        case MP_QSTR_error: dest[0] = ble_str_or_none(h->error); break;
        case MP_QSTR_passkey: dest[0] = mp_const_none; break;
        case MP_QSTR_settings_capable: dest[0] = mp_const_true; break;
        case MP_QSTR_notify_count: dest[0] = mp_obj_new_int_from_uint(h->notify_count); break;
        case MP_QSTR_protocol:
            dest[0] = h->protocol == 1 ? MP_OBJ_NEW_QSTR(MP_QSTR_boot)
                      : h->protocol == 2 ? MP_OBJ_NEW_QSTR(MP_QSTR_report) : mp_const_none;
            break;
        case MP_QSTR_conn_interval_ms:
            dest[0] = h->interval ? mp_obj_new_float((mp_float_t)h->interval * (mp_float_t)1.25) : mp_const_none;
            break;
        default: dest[1] = MP_OBJ_SENTINEL; break;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    input_ble_type, MP_QSTR_BleKeyboard, MP_TYPE_FLAG_NONE,
    attr, ble_attr,
    locals_dict, &ble_locals
    );

static mp_obj_t input_ble(void) {
    moy_hid_t *h = moy_ble_hid();
    if (h == NULL) {
        return mp_const_none;
    }
    mp_obj_t o = MP_STATE_VM(moy_input_ble_obj);
    if (o == MP_OBJ_NULL) {
        input_ble_obj_t *b = mp_obj_malloc(input_ble_obj_t, &input_ble_type);
        b->h = h;
        o = MP_OBJ_FROM_PTR(b);
        MP_STATE_VM(moy_input_ble_obj) = o;
    }
    return o;
}
static MP_DEFINE_CONST_FUN_OBJ_0(input_ble_obj, input_ble);

MP_REGISTER_ROOT_POINTER(mp_obj_t moy_input_ble_obj);

static mp_obj_t input_kick(void) {
    moy_input_board_kick();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(input_kick_obj, input_kick);

static mp_obj_t input_task_stack_free(void) {
    return mp_obj_new_int_from_uint(moy_input_board_stack_free());
}
static MP_DEFINE_CONST_FUN_OBJ_0(input_task_stack_free_obj, input_task_stack_free);

// -- the module ----------------------------------------------------------------------

static const mp_rom_map_elem_t moy_input_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_input) },
    { MP_ROM_QSTR(MP_QSTR_NAMES), MP_ROM_PTR(&names_obj) },
    { MP_ROM_QSTR(MP_QSTR_HOST_NAMES), MP_ROM_PTR(&host_names_obj) },
    { MP_ROM_QSTR(MP_QSTR_InputTable), MP_ROM_PTR(&input_table_type) },
    { MP_ROM_QSTR(MP_QSTR_HostInputTable), MP_ROM_PTR(&host_table_type) },
    { MP_ROM_QSTR(MP_QSTR_InputSource), MP_ROM_PTR(&input_source_type) },
    { MP_ROM_QSTR(MP_QSTR_Pointer), MP_ROM_PTR(&input_pointer_type) },
    { MP_ROM_QSTR(MP_QSTR_kernel), MP_ROM_PTR(&input_kernel_obj) },
    { MP_ROM_QSTR(MP_QSTR_pointer_state), MP_ROM_PTR(&input_pointer_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_keyboard), MP_ROM_PTR(&input_keyboard_obj) },
    { MP_ROM_QSTR(MP_QSTR_touch), MP_ROM_PTR(&input_touch_obj) },
    { MP_ROM_QSTR(MP_QSTR_trackball), MP_ROM_PTR(&input_trackball_obj) },
    { MP_ROM_QSTR(MP_QSTR_kick), MP_ROM_PTR(&input_kick_obj) },
    { MP_ROM_QSTR(MP_QSTR_ble), MP_ROM_PTR(&input_ble_obj) },
    { MP_ROM_QSTR(MP_QSTR_task_stack_free), MP_ROM_PTR(&input_task_stack_free_obj) },
    { MP_ROM_QSTR(MP_QSTR_SOURCES), MP_ROM_INT(MOY_INPUT_SOURCES) },
    { MP_ROM_QSTR(MP_QSTR_PLAYERS), MP_ROM_INT(MOY_INPUT_PLAYERS) },
    { MP_ROM_QSTR(MP_QSTR_UNION), MP_ROM_INT(MOY_INPUT_UNION) },
    { MP_ROM_QSTR(MP_QSTR_CURSOR_IDLE_MS), MP_ROM_INT(MOY_INPUT_CURSOR_IDLE_MS) },
    { MP_ROM_QSTR(MP_QSTR_POINTER_LINGER_MS), MP_ROM_INT(MOY_INPUT_POINTER_LINGER_MS) },
    { MP_ROM_QSTR(MP_QSTR_P_NONE), MP_ROM_INT(MOY_INPUT_P_NONE) },
    { MP_ROM_QSTR(MP_QSTR_P_LIVE), MP_ROM_INT(MOY_INPUT_P_LIVE) },
    { MP_ROM_QSTR(MP_QSTR_P_HELD), MP_ROM_INT(MOY_INPUT_P_HELD) },
    { MP_ROM_QSTR(MP_QSTR_P_CLICK), MP_ROM_INT(MOY_INPUT_P_CLICK) },
};
static MP_DEFINE_CONST_DICT(moy_input_globals, moy_input_globals_table);

const mp_obj_module_t moy_input_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_input_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_input, moy_input_module);
