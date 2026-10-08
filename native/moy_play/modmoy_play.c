// The module `moy_play`: the kernel's Player as a VM sees it
// (docs/kernel_cartpath_2026-10.md §3). runtime/moy_play.py is the same
// module on CPython, by ctypes, name for name:
//
//   Tick()            the tick model (moy_tick.h), one per run:
//     start(rate[, steady]), steady_mode(on), uncap_mode(on), note_tick(s),
//     plan(dt) -> draw, cycle(div), fits(div); its state as attributes --
//     rate, period, tick_ms, steady, uncapped, div, n, draw, ticks, draws,
//     misses, tick_cost, draw_frame, tick_frame, late, probing
//   MAX_CATCHUP, MAX_DIV  the model's integer bounds (the CPython module
//                         adds its real-valued constants)

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_tick.h"

typedef struct {
    mp_obj_base_t base;
    moy_tick_t t;
} tick_obj_t;

static const mp_obj_type_t tick_type;

static mp_obj_t tick_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                              const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    tick_obj_t *o = mp_obj_malloc(tick_obj_t, type);
    memset(&o->t, 0, sizeof(o->t));
    moy_tick_start(&o->t, 30, true);
    o->t.rate = 0;
    o->t.period = 0;
    o->t.tick_ms = 0;
    return MP_OBJ_FROM_PTR(o);
}

static moy_tick_t *T(mp_obj_t self) {
    return &((tick_obj_t *)MP_OBJ_TO_PTR(self))->t;
}

// `rate` 60 opts in; anything else is the 30 the console guarantees.
static mp_obj_t tick_start(size_t n_args, const mp_obj_t *args) {
    moy_tick_start(T(args[0]), mp_obj_equal(args[1], MP_OBJ_NEW_SMALL_INT(60)) ? 60 : 30,
                   n_args > 2 ? mp_obj_is_true(args[2]) : true);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tick_start_obj, 2, 3, tick_start);

static mp_obj_t tick_steady_mode(mp_obj_t self, mp_obj_t on) {
    moy_tick_mode(T(self), mp_obj_is_true(on), T(self)->uncapped);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tick_steady_mode_obj, tick_steady_mode);

static mp_obj_t tick_uncap_mode(mp_obj_t self, mp_obj_t on) {
    moy_tick_mode(T(self), T(self)->steady, mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tick_uncap_mode_obj, tick_uncap_mode);

static mp_obj_t tick_note_tick(mp_obj_t self, mp_obj_t s) {
    moy_tick_note(T(self), (moy_tick_real_t)mp_obj_get_float(s));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tick_note_tick_obj, tick_note_tick);

static mp_obj_t tick_plan(mp_obj_t self, mp_obj_t dt) {
    return mp_obj_new_bool(moy_tick_plan(T(self), (moy_tick_real_t)mp_obj_get_float(dt), NULL));
}
static MP_DEFINE_CONST_FUN_OBJ_2(tick_plan_obj, tick_plan);

static mp_obj_t tick_cycle(mp_obj_t self, mp_obj_t div) {
    return mp_obj_new_float((mp_float_t)moy_tick_cycle(T(self), mp_obj_get_int(div)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(tick_cycle_obj, tick_cycle);

static mp_obj_t tick_fits(mp_obj_t self, mp_obj_t div) {
    return mp_obj_new_bool(moy_tick_fits(T(self), mp_obj_get_int(div)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(tick_fits_obj, tick_fits);

static void tick_attr(mp_obj_t self, qstr attr, mp_obj_t *dest) {
    if (dest[0] != MP_OBJ_NULL) {
        return;                         // read-only
    }
    const moy_tick_t *t = T(self);
    switch (attr) {
        case MP_QSTR_rate: dest[0] = MP_OBJ_NEW_SMALL_INT(t->rate); return;
        case MP_QSTR_period: dest[0] = mp_obj_new_float((mp_float_t)t->period); return;
        case MP_QSTR_tick_ms: dest[0] = MP_OBJ_NEW_SMALL_INT(t->tick_ms); return;
        case MP_QSTR_steady: dest[0] = mp_obj_new_bool(t->steady); return;
        case MP_QSTR_uncapped: dest[0] = mp_obj_new_bool(t->uncapped); return;
        case MP_QSTR_div: dest[0] = MP_OBJ_NEW_SMALL_INT(t->div); return;
        case MP_QSTR_n: dest[0] = MP_OBJ_NEW_SMALL_INT(t->n); return;
        case MP_QSTR_draw: dest[0] = mp_obj_new_bool(t->draw); return;
        case MP_QSTR_ticks: dest[0] = mp_obj_new_int_from_uint(t->ticks); return;
        case MP_QSTR_draws: dest[0] = mp_obj_new_int_from_uint(t->draws); return;
        case MP_QSTR_misses: dest[0] = mp_obj_new_int_from_uint(t->misses); return;
        case MP_QSTR_tick_cost: dest[0] = mp_obj_new_float((mp_float_t)t->tick_cost); return;
        case MP_QSTR_draw_frame: dest[0] = mp_obj_new_float((mp_float_t)t->draw_frame); return;
        case MP_QSTR_tick_frame: dest[0] = mp_obj_new_float((mp_float_t)t->tick_frame); return;
        case MP_QSTR_late: dest[0] = mp_obj_new_float((mp_float_t)t->late); return;
        case MP_QSTR_probing: dest[0] = mp_obj_new_bool(t->probing); return;
        default:
            dest[1] = MP_OBJ_SENTINEL;  // the methods
            return;
    }
}

static const mp_rom_map_elem_t tick_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&tick_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_steady_mode), MP_ROM_PTR(&tick_steady_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_uncap_mode), MP_ROM_PTR(&tick_uncap_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_note_tick), MP_ROM_PTR(&tick_note_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_plan), MP_ROM_PTR(&tick_plan_obj) },
    { MP_ROM_QSTR(MP_QSTR_cycle), MP_ROM_PTR(&tick_cycle_obj) },
    { MP_ROM_QSTR(MP_QSTR_fits), MP_ROM_PTR(&tick_fits_obj) },
};
static MP_DEFINE_CONST_DICT(tick_locals, tick_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    tick_type, MP_QSTR_Tick, MP_TYPE_FLAG_NONE,
    make_new, tick_make_new,
    attr, tick_attr,
    locals_dict, &tick_locals
    );

static const mp_rom_map_elem_t moy_play_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_play) },
    { MP_ROM_QSTR(MP_QSTR_Tick), MP_ROM_PTR(&tick_type) },
    { MP_ROM_QSTR(MP_QSTR_MAX_CATCHUP), MP_ROM_INT(MOY_TICK_MAX_CATCHUP) },
    { MP_ROM_QSTR(MP_QSTR_MAX_DIV), MP_ROM_INT(MOY_TICK_MAX_DIV) },
};
static MP_DEFINE_CONST_DICT(moy_play_globals, moy_play_globals_table);

const mp_obj_module_t moy_play_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_play_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_play, moy_play_module);
