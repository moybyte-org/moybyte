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
//   census(path)      the VM-free rule (moy_play_vm_free) over the cart folder
//                     at `path`, as the store's C reads its catalogue entry:
//                     (runtime, vm_free, why), or None when it is no cart.
//                     This image's runtime map has a "lua" row where moycore is
//                     built (MOY_WITH_LUA) and a "wasm" row where the engine is
//                     (MOY_WASM).
//
// The Player (moy_play.h), the VM's alone -- on CPython the host's runs are
// runtime/lua_host.py's and runtime/wasm_host.py's:
//
//   launch(path, paced) -> run   the cart's verdict and its runtime's row
//   bind(run, input, audio, tick) its input table, audio session, Tick
//   open(run)                    the row's check that its runtime is open
//   frame(run, ticks, dt, render, x, y, touch) -> QUIT | VIEW
//   end(run[, why]), info([run]), current(), stack()
//
// The map's "python" row is registered with the Lua and wasm ones: a VM is
// what imports this module.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_cat.h"
#include "moy_play.h"
#include "moy_rt.h"
#include "moy_tick.h"
#include "moy_input.h"

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

// -- the runtime map's rows and the census ------------------------------------

static void rows_once(void) {
    static bool done;
    if (done) {
        return;
    }
    done = true;
    #ifdef MOY_WITH_LUA
    bool lua = true;
    #else
    bool lua = false;
    #endif
    #if defined(MOY_WASM) && MOY_WASM
    bool wasm = true;
    #else
    bool wasm = false;
    #endif
    moy_play_rows(lua, wasm, true);
}

static int census_one(void *ctx, const moy_cat_entry_t *e) {
    moy_play_census_line(e, (char *)ctx, 96);
    return 0;
}

static mp_obj_t mod_census(mp_obj_t path_obj) {
    char line[96] = "";
    rows_once();
    if (moy_cat_entry(mp_obj_str_get_str(path_obj), census_one, NULL, line) != 0
        || line[0] == 0) {
        return mp_const_none;
    }
    char *a = strchr(line, ' ');
    char *b = a ? strchr(a + 1, ' ') : NULL;
    if (b == NULL) {
        return mp_const_none;
    }
    mp_obj_t t[3] = {
        mp_obj_new_str(line, (size_t)(a - line)),
        mp_obj_new_bool(strncmp(a + 1, "free", 4) == 0),
        mp_obj_new_str(b + 1, strlen(b + 1)),
    };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_census_obj, mod_census);

// -- the Player -----------------------------------------------------------------

// The objects a bound run reads from C: the input table and the tick model.
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_play_input_obj);
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_play_tick_obj);

extern moy_input_t *moy_input_table_of(mp_obj_t o);

static uint32_t run_arg(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int_truncated(o);
}

static mp_obj_t raise_rc(int rc) {
    static const char *const WHAT[] = {
        "ok", "stale run", "full", "no memory", "no such cart", "runtime not in this image",
        "newer", "does not fit", "raised", "ended", "needs the VM",
    };
    mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("moy_play: %s"),
                      rc >= 0 && rc <= MOY_PLAY_NEEDS_VM ? WHAT[rc] : "?");
}

// launch(path, paced) -> the run's handle; RuntimeError when the cart will
// not read or its runtime is not in this image.
static mp_obj_t mod_launch(mp_obj_t path_obj, mp_obj_t paced_obj) {
    rows_once();
    uint32_t run = 0;
    int rc = moy_play_launch(mp_obj_str_get_str(path_obj), NULL,
                             mp_obj_is_true(paced_obj) ? MOY_PLAY_PACED : 0u, &run);
    if (rc != MOY_PLAY_OK) {
        raise_rc(rc);
    }
    MP_STATE_VM(moy_play_input_obj) = MP_OBJ_NULL;
    MP_STATE_VM(moy_play_tick_obj) = MP_OBJ_NULL;
    return mp_obj_new_int_from_uint(run);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_launch_obj, mod_launch);

// bind(run, input, audio, tick) -- the run's input table (an InputTable or a
// HostInputTable), its audio session's handle (0: silent) and the Tick a
// paced run notes its costs into (None: not paced). TypeError for an input
// that is no table.
static mp_obj_t mod_bind(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_input_t *in = moy_input_table_of(a[1]);
    if (in == NULL) {
        mp_raise_TypeError(MP_ERROR_TEXT("bind: not an input table"));
    }
    moy_tick_t *t = NULL;
    if (a[3] != mp_const_none) {
        if (!mp_obj_is_type(a[3], &tick_type)) {
            mp_raise_TypeError(MP_ERROR_TEXT("bind: not a Tick"));
        }
        t = T(a[3]);
    }
    int rc = moy_play_bind(run_arg(a[0]), in, (uint32_t)mp_obj_get_int_truncated(a[2]), t);
    if (rc != MOY_PLAY_OK) {
        raise_rc(rc);
    }
    MP_STATE_VM(moy_play_input_obj) = a[1];
    MP_STATE_VM(moy_play_tick_obj) = a[3] == mp_const_none ? MP_OBJ_NULL : a[3];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_bind_obj, 4, 4, mod_bind);

// open(run) -- the row's check that its runtime is open; RuntimeError naming
// what it found otherwise.
static mp_obj_t mod_open(mp_obj_t run_obj) {
    uint32_t run = run_arg(run_obj);
    int rc = moy_play_open(run);
    if (rc == MOY_PLAY_RAISED) {
        moy_play_info_t info;
        moy_play_info(run, &info);
        mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("%s"), info.error);
    }
    if (rc != MOY_PLAY_OK) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_open_obj, mod_open);

// frame(run, ticks, dt, render, x, y, touch) -> QUIT | VIEW bits. The
// pointer is the console's, in the cart's coordinates. A cart that raised
// raises RuntimeError with its own text, which is how the Player has always
// captured a Lua cart's crash.
static mp_obj_t mod_frame(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    uint32_t run = run_arg(a[0]);
    moy_play_in_t in = {
        (int32_t)mp_obj_get_int(a[4]), (int32_t)mp_obj_get_int(a[5]),
        (int32_t)mp_obj_get_int(a[6]),
    };
    moy_play_input(run, &in);
    uint32_t out = 0;
    mp_int_t n = mp_obj_get_int(a[1]);
    int rc = moy_play_frame(run, (uint8_t)(n < 0 ? 0 : n > 255 ? 255 : n),
                            (float)mp_obj_get_float(a[2]), mp_obj_is_true(a[3]), &out);
    if (rc == MOY_PLAY_RAISED) {
        moy_play_info_t info;
        moy_play_info(run, &info);
        mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("%s"), info.error);
    }
    if (rc != MOY_PLAY_OK) {
        raise_rc(rc);
    }
    return MP_OBJ_NEW_SMALL_INT(out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_frame_obj, 7, 7, mod_frame);

// end(run[, why]) -- the run's books closed; a stale handle is a no-op.
static mp_obj_t mod_end(size_t n_args, const mp_obj_t *a) {
    moy_play_end(run_arg(a[0]), n_args > 1 ? mp_obj_get_int(a[1]) : MOY_PLAY_END_QUIT);
    MP_STATE_VM(moy_play_input_obj) = MP_OBJ_NULL;
    MP_STATE_VM(moy_play_tick_obj) = MP_OBJ_NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_end_obj, 1, 2, mod_end);

static mp_obj_t stack_obj(uint32_t v) {
    return v == MOY_PLAY_NO_STACK ? mp_const_none : mp_obj_new_int_from_uint(v);
}

// info([run]) -> (runtime, vm_free, why, frames, ticks, upcalls, ended,
// error, stack_open, stack_frame), the live run's or the last one's; None when
// there is none. `upcalls` is a tuple by class: CONSOLE, APP, DRIVER,
// SERVICE, REFUSED; the two stack readings are moy_play.stack()'s, taken
// after the open and after the last frame, None off a board.
static mp_obj_t mod_info(size_t n_args, const mp_obj_t *a) {
    uint32_t run = n_args > 0 ? run_arg(a[0]) : moy_play_last();
    moy_play_info_t i;
    if (moy_play_info(run, &i) != MOY_PLAY_OK) {
        return mp_const_none;
    }
    mp_obj_t up[MOY_PLAY_UPC];
    for (int k = 0; k < MOY_PLAY_UPC; k++) {
        up[k] = mp_obj_new_int_from_uint(i.upcalls[k]);
    }
    const char *why = moy_play_why_name(i.why);
    mp_obj_t t[10] = {
        mp_obj_new_str(i.runtime, strlen(i.runtime)),
        mp_obj_new_bool(i.vm_free),
        mp_obj_new_str(why, strlen(why)),
        mp_obj_new_int_from_uint(i.frames),
        mp_obj_new_int_from_uint(i.ticks),
        mp_obj_new_tuple(MOY_PLAY_UPC, up),
        mp_obj_new_bool(i.ended),
        i.raised ? mp_obj_new_str(i.error, strlen(i.error)) : mp_const_none,
        stack_obj(i.stack_open),
        stack_obj(i.stack_frame),
    };
    return mp_obj_new_tuple(10, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_info_obj, 0, 1, mod_info);

// stack() -> the calling task's stack high-water mark in bytes (the least
// it has had free since it started), or None off a board.
static mp_obj_t mod_stack(void) {
    return stack_obj(moy_play_stack_free());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_stack_obj, mod_stack);

static mp_obj_t mod_current(void) {
    return mp_obj_new_int_from_uint(moy_play_current());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_current_obj, mod_current);

static const mp_rom_map_elem_t moy_play_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_play) },
    { MP_ROM_QSTR(MP_QSTR_Tick), MP_ROM_PTR(&tick_type) },
    { MP_ROM_QSTR(MP_QSTR_census), MP_ROM_PTR(&mod_census_obj) },
    { MP_ROM_QSTR(MP_QSTR_launch), MP_ROM_PTR(&mod_launch_obj) },
    { MP_ROM_QSTR(MP_QSTR_bind), MP_ROM_PTR(&mod_bind_obj) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&mod_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame), MP_ROM_PTR(&mod_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_end), MP_ROM_PTR(&mod_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_info), MP_ROM_PTR(&mod_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_current), MP_ROM_PTR(&mod_current_obj) },
    { MP_ROM_QSTR(MP_QSTR_stack), MP_ROM_PTR(&mod_stack_obj) },
    { MP_ROM_QSTR(MP_QSTR_QUIT), MP_ROM_INT(MOY_PLAY_QUIT) },
    { MP_ROM_QSTR(MP_QSTR_VIEW), MP_ROM_INT(MOY_PLAY_VIEW) },
    { MP_ROM_QSTR(MP_QSTR_MAX_CATCHUP), MP_ROM_INT(MOY_TICK_MAX_CATCHUP) },
    { MP_ROM_QSTR(MP_QSTR_MAX_DIV), MP_ROM_INT(MOY_TICK_MAX_DIV) },
};
static MP_DEFINE_CONST_DICT(moy_play_globals, moy_play_globals_table);

const mp_obj_module_t moy_play_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_play_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_play, moy_play_module);
