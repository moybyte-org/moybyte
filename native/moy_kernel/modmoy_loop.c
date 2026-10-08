// moy_loop's MicroPython binding: the kernel's frame as the module `moy_loop`,
// the same names runtime/moy_loop.py gives CPython.
//
//   register(handle_input, handle_pointer, frame, words=None, service=None)
//                      the console's upcalls, held as ROOT POINTERS (never in
//                      kernel state: docs/native_kernel_2026-09.md §4.3);
//                      refused while no VM runs. `frame(dt)` answers the
//                      console's frames drawn; `words(line)` the console's
//                      dev words, truthy when the line asked for the REPL
//   unregister()       the upcalls dropped
//   step(), run()      one frame / until QUIT, INTERRUPT or STOPPED
//   upcalls()          ((console, app, driver, service, refused) of the last frame,
//                       the same in total)
//   tick(ms), capture([on]), meters(), meters_reset(), pump(), lit(on),
//   health(on), frames(), drawn()
//   idle([rung, secs]) the ladder: (state, dim_s, saver_s, blank_s) / set one
//   power(word)        `power on|off` from Python: wake / blank
//   perf_due(), perf(name, value), perf_cart(title)   the console's PERF half
//   tap(x, y), swipe(x0, y0, x1, y1, n), drag(cx, cy, n, step)   gestures
//   devch(line), devch_budget(n), devch_stats(), devch_armed([on])
//   trace_*            the trace tier (moy_loop_host.c)
//
// An upcall that raises is reported where the board reports a frame error
// (the message, the traceback, a collect) and the loop goes on; a
// KeyboardInterrupt ends the loop (INTERRUPT), which is the Ctrl-C -> REPL
// contract, and a SystemExit ends it too (EXIT), which a board's VM service
// turns into its soft reset (`kstop`).

#include <string.h>

#include "py/gc.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/smallint.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_devch.h"
#include "moy_loop.h"

MP_REGISTER_ROOT_POINTER(mp_obj_t moy_loop_up[5]);
// The driver tier hands an upcall's exception back to its harness: kept here
// until the step returns, and every later upcall of that step skipped.
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_loop_exc);

static bool s_driving;

#define UPS (MP_STATE_VM(moy_loop_up))

// The trace tier (moy_loop_host.c) is the desktop MicroPython's alone: a board
// image carries none of it, and its trace_* words raise there.
void moy_loop_host_init(int fps_cap, bool can_dim, uint32_t clock_ms) __attribute__((weak));
void moy_loop_host_clock(uint32_t ms) __attribute__((weak));
void moy_loop_host_advance(uint32_t us) __attribute__((weak));
void moy_loop_host_cost(int stage, uint32_t us) __attribute__((weak));
void moy_loop_host_input(bool click, bool active) __attribute__((weak));
void moy_loop_host_feed(const uint8_t *bytes, size_t n) __attribute__((weak));
const char *moy_loop_host_log(void) __attribute__((weak));
void moy_loop_host_clear(void) __attribute__((weak));
void moy_loop_host_note(const char *token) __attribute__((weak));
int moy_loop_driver_step(uint32_t dt_us) __attribute__((weak));

static void need_trace(void) {
    if (moy_loop_host_init == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no trace tier in this image"));
    }
}

// The VM-side stages (moy_loop_board.c): a board's VM service installs them
// at every VM start; a desktop MicroPython that registers a console with no
// stages given yet takes them at registration.
void moy_loop_board_vm_start(void);

// The board's frame-error report beside the message; weak: absent elsewhere.
void moy_loop_board_frame_error(void) __attribute__((weak));

static void report(mp_obj_t exc, int which) {
    mp_printf(&mp_plat_print, "Moybyte %s error: ",
              which == MOY_UP_WORD ? "REMOTE" : which == MOY_UP_SERVICE ? "service" : "frame");
    mp_obj_print_helper(&mp_plat_print, exc, PRINT_STR);
    mp_printf(&mp_plat_print, "\n");
    mp_obj_print_exception(&mp_plat_print, exc);
    if (moy_loop_board_frame_error != NULL) {
        moy_loop_board_frame_error();
    }
    gc_collect();
}

static int vm_up(int which, uint32_t arg, const char *line) {
    if (which < 0 || which >= MOY_UP_COUNT) {
        return MOY_UP_ABSENT;
    }
    mp_obj_t fn = UPS[which];
    if (fn == MP_OBJ_NULL || fn == mp_const_none) {
        return MOY_UP_ABSENT;
    }
    if (s_driving && MP_STATE_VM(moy_loop_exc) != MP_OBJ_NULL) {
        return MOY_UP_ABSENT;
    }
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t r;
        switch (which) {
            case MOY_UP_FRAME:
                r = mp_call_function_1(fn, mp_obj_new_float((mp_float_t)arg / (mp_float_t)1000000));
                break;
            case MOY_UP_WORD:
                r = mp_call_function_1(fn, mp_obj_new_str(line, strlen(line)));
                break;
            case MOY_UP_SERVICE:
                r = mp_call_function_1(fn, MP_OBJ_NEW_SMALL_INT(arg));
                break;
            default:
                r = mp_call_function_0(fn);
                break;
        }
        int v = 0;
        if (mp_obj_is_small_int(r)) {
            v = MP_OBJ_SMALL_INT_VALUE(r);
        } else if (r == mp_const_true) {
            v = 1;
        }
        nlr_pop();
        return v < 0 ? 0 : v;
    }
    mp_obj_t exc = MP_OBJ_FROM_PTR(nlr.ret_val);
    if (s_driving) {
        MP_STATE_VM(moy_loop_exc) = exc;
        return MOY_UP_RAISED;
    }
    if (mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(mp_obj_get_type(exc)),
                                MP_OBJ_FROM_PTR(&mp_type_KeyboardInterrupt))) {
        return MOY_UP_INTERRUPTED;
    }
    if (mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(mp_obj_get_type(exc)),
                                MP_OBJ_FROM_PTR(&mp_type_SystemExit))) {
        return MOY_UP_EXIT;
    }
    report(exc, which);
    return MOY_UP_RAISED;
}

static void set_registered(void) {
    uint32_t bits = 0;
    for (int i = 0; i <= MOY_UP_FRAME; i++) {
        if (UPS[i] != MP_OBJ_NULL && UPS[i] != mp_const_none) {
            bits |= 1u << i;
        }
    }
    moy_loop_set_registered(bits);
}

// The VM is going: the upcalls belonged to its heap.
void moy_loop_vm_clear(void) {
    for (int i = 0; i < MOY_UP_COUNT; i++) {
        UPS[i] = MP_OBJ_NULL;
    }
    MP_STATE_VM(moy_loop_exc) = MP_OBJ_NULL;
    moy_loop_set_registered(0);
}

static mp_obj_t loop_register(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    enum { ARG_hi, ARG_hp, ARG_frame, ARG_words, ARG_service };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_handle_input, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_handle_pointer, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_frame, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_words, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_service, MP_ARG_OBJ, {.u_obj = mp_const_none} },
    };
    mp_arg_val_t a[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos, kw, MP_ARRAY_SIZE(allowed), allowed, a);
    if (!moy_loop_vm()) {
        mp_raise_OSError(MP_EPERM);
    }
    if (!moy_loop_has_ops()) {
        moy_loop_board_vm_start();
    }
    for (int i = 0; i < MOY_UP_COUNT; i++) {
        UPS[i] = a[i].u_obj;
    }
    moy_loop_set_upcall(vm_up);
    set_registered();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(loop_register_obj, 3, loop_register);

static mp_obj_t loop_unregister(void) {
    moy_loop_vm_clear();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_unregister_obj, loop_unregister);

// drive(handle_input, handle_pointer, frame, dt): one frame of a harness that
// owns the clock and the input (the host's and the browser's ConsoleDriver):
// the loop over no stages but the clock, the three upcalls this harness's,
// and an exception one of them raised raised here, as a direct call would.
static mp_obj_t loop_drive(size_t n_args, const mp_obj_t *a) {
    if (moy_loop_driver_step == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no driver tier in this image"));
    }
    for (int i = 0; i < 3; i++) {
        UPS[i] = a[i];
    }
    UPS[MOY_UP_WORD] = MP_OBJ_NULL;
    UPS[MOY_UP_SERVICE] = MP_OBJ_NULL;
    moy_loop_set_upcall(vm_up);
    set_registered();
    mp_float_t dt = mp_obj_get_float(a[3]);
    uint32_t us = dt > 0 ? (uint32_t)(dt * (mp_float_t)1000000 + (mp_float_t)0.5) : 0;
    MP_STATE_VM(moy_loop_exc) = MP_OBJ_NULL;
    s_driving = true;
    int r = moy_loop_driver_step(us);
    s_driving = false;
    mp_obj_t exc = MP_STATE_VM(moy_loop_exc);
    if (exc != MP_OBJ_NULL) {
        MP_STATE_VM(moy_loop_exc) = MP_OBJ_NULL;
        nlr_raise(exc);
    }
    return MP_OBJ_NEW_SMALL_INT(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_drive_obj, 4, 4, loop_drive);

static mp_obj_t loop_step(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_loop_step());
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_step_obj, loop_step);

static mp_obj_t loop_run(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_loop_run());
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_run_obj, loop_run);

static mp_obj_t tuple_classes(const uint32_t v[MOY_UPC_CLASSES]) {
    mp_obj_t t[MOY_UPC_CLASSES];
    for (int i = 0; i < MOY_UPC_CLASSES; i++) {
        t[i] = mp_obj_new_int_from_uint(v[i]);
    }
    return mp_obj_new_tuple(MOY_UPC_CLASSES, t);
}

static mp_obj_t loop_upcalls(void) {
    uint32_t f[MOY_UPC_CLASSES], t[MOY_UPC_CLASSES];
    moy_loop_upcalls(f, t);
    mp_obj_t r[2] = {tuple_classes(f), tuple_classes(t)};
    return mp_obj_new_tuple(2, r);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_upcalls_obj, loop_upcalls);

static mp_obj_t loop_tick(mp_obj_t ms) {
    moy_loop_set_tick((uint32_t)mp_obj_get_int(ms));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_tick_obj, loop_tick);

static mp_obj_t loop_fps(mp_obj_t fps) {
    moy_loop_set_fps(mp_obj_get_int(fps));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_fps_obj, loop_fps);

static mp_obj_t loop_services(size_t n_args, const mp_obj_t *a) {
    if (n_args) {
        moy_loop_set_services((uint32_t)mp_obj_get_int(a[0]));
    }
    return mp_obj_new_int_from_uint(moy_loop_services());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_services_obj, 0, 1, loop_services);

static mp_obj_t loop_devch_unread(mp_obj_t data) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(data, &b, MP_BUFFER_READ);
    moy_devch_unread(b.buf, b.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_devch_unread_obj, loop_devch_unread);

static mp_obj_t loop_capture(size_t n_args, const mp_obj_t *a) {
    if (n_args) {
        moy_loop_set_capture(mp_obj_is_true(a[0]));
    }
    return mp_obj_new_bool(moy_loop_capture());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_capture_obj, 0, 1, loop_capture);

static mp_obj_t opt_u(bool have, uint32_t v) {
    return have ? mp_obj_new_int_from_uint(v) : mp_const_none;
}

// {stage: (budget_us, avg_us, last_us, max_us, misses, n)}: a stage never
// sampled reads None in every measured field, and a stage with no deadline
// None in budget and misses.
static mp_obj_t loop_meters(void) {
    mp_obj_t d = mp_obj_new_dict(MOY_ST_COUNT);
    for (int i = 0; i < MOY_ST_COUNT; i++) {
        moy_loop_meter_t m;
        bool seen = moy_loop_meter(i, &m);
        bool dl = m.budget_us >= 0;
        mp_obj_t t[6] = {
            dl ? mp_obj_new_int(m.budget_us) : mp_const_none,
            opt_u(seen, m.avg_us), opt_u(seen, m.last_us), opt_u(seen, m.max_us),
            opt_u(seen && dl, m.misses), mp_obj_new_int_from_uint(m.n),
        };
        const char *name = moy_loop_stage_name(i);
        mp_obj_dict_store(d, mp_obj_new_str(name, strlen(name)), mp_obj_new_tuple(6, t));
    }
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_meters_obj, loop_meters);

static mp_obj_t loop_meters_reset(void) {
    moy_loop_meters_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_meters_reset_obj, loop_meters_reset);

static mp_obj_t loop_stages(void) {
    mp_obj_t t[MOY_ST_COUNT];
    for (int i = 0; i < MOY_ST_COUNT; i++) {
        const char *name = moy_loop_stage_name(i);
        t[i] = mp_obj_new_str(name, strlen(name));
    }
    return mp_obj_new_tuple(MOY_ST_COUNT, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_stages_obj, loop_stages);

static mp_obj_t loop_pump(void) {
    moy_loop_pump_t p;
    moy_loop_pump(&p);
    mp_obj_t t[5] = {
        mp_obj_new_int_from_uint(p.frame_ms), mp_obj_new_int_from_uint(p.slot),
        mp_obj_new_int_from_uint(p.debt), mp_obj_new_int_from_uint(p.slack),
        mp_obj_new_int_from_uint(p.tick_ms),
    };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_pump_obj, loop_pump);

static mp_obj_t loop_pace(mp_obj_t elapsed) {
    return mp_obj_new_int_from_uint(moy_loop_pace((uint32_t)mp_obj_get_int(elapsed)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_pace_obj, loop_pace);

static mp_obj_t loop_lit(mp_obj_t on) {
    moy_loop_set_lit(mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_lit_obj, loop_lit);

static mp_obj_t loop_health(mp_obj_t on) {
    moy_loop_arm_health(mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_health_obj, loop_health);

static mp_obj_t loop_frames(void) {
    return mp_obj_new_int_from_uint(moy_loop_frames());
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_frames_obj, loop_frames);

// last() -> elapsed * 65536 + sleep of the last finished frame, in ms: one
// small int, so a per-frame reader allocates nothing.
static mp_obj_t loop_last(void) {
    uint32_t e, sl;
    moy_loop_last(&e, &sl);
    if (e > 0x3FFF) {
        e = 0x3FFF;
    }
    if (sl > 0xFFFF) {
        sl = 0xFFFF;
    }
    return MP_OBJ_NEW_SMALL_INT((mp_int_t)(e * 65536u + sl));
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_last_obj, loop_last);

// diag_take() -> (hitch, loop): the HITCH and LOOP lines the kernel said
// since the last call, each None when there was none. A board that rings its
// diag lines takes them; the period is seconds, so this allocates rarely.
static mp_obj_t loop_diag_take(void) {
    char hitch[MOY_PERF_LINE_MAX], loop[MOY_PERF_LINE_MAX];     // the stack: no .bss
    int got = moy_loop_diag_take(hitch, loop, sizeof(hitch));
    mp_obj_t t[2] = {
        (got & 1) ? mp_obj_new_str(hitch, strlen(hitch)) : mp_const_none,
        (got & 2) ? mp_obj_new_str(loop, strlen(loop)) : mp_const_none,
    };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_diag_take_obj, loop_diag_take);

// frame_at() -> the board's clock (ms, the port's ticks) at the top of the
// frame a word runs in: what a tool times the drawn-frame counter against.
static mp_obj_t loop_frame_at(void) {
    return mp_obj_new_int_from_uint(moy_loop_frame_at() & (MICROPY_PY_TIME_TICKS_PERIOD - 1));
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_frame_at_obj, loop_frame_at);

static mp_obj_t loop_drawn(void) {
    return mp_obj_new_int_from_uint(moy_loop_drawn());
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_drawn_obj, loop_drawn);

// idle() -> (state, dim_s, saver_s, blank_s); idle(rung, secs) sets one.
static mp_obj_t loop_idle(size_t n_args, const mp_obj_t *a) {
    moy_idle_t *d = moy_loop_idle();
    if (n_args == 2) {
        moy_idle_set(d, mp_obj_get_int(a[0]), (uint32_t)mp_obj_get_int(a[1]));
    }
    mp_obj_t t[4] = {
        MP_OBJ_NEW_SMALL_INT(d->state),
        mp_obj_new_int_from_uint(moy_idle_get(d, MOY_IDLE_DIM)),
        mp_obj_new_int_from_uint(moy_idle_get(d, MOY_IDLE_SAVER)),
        mp_obj_new_int_from_uint(moy_idle_get(d, MOY_IDLE_BLANK)),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_idle_obj, 0, 2, loop_idle);

// idle_state() -> the ladder's rung now, a small int (no allocation: the
// console's frame reads it every frame).
static mp_obj_t loop_idle_state(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_loop_idle()->state);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_idle_state_obj, loop_idle_state);

static mp_obj_t loop_idle_can_dim(void) {
    return mp_obj_new_bool(moy_loop_idle()->can_dim);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_idle_can_dim_obj, loop_idle_can_dim);

static mp_obj_t loop_power(mp_obj_t on) {
    if (mp_obj_is_true(on)) {
        moy_idle_wake(moy_loop_idle());
    } else {
        moy_idle_blank(moy_loop_idle());
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_power_obj, loop_power);

static mp_obj_t loop_perf_due(void) {
    return mp_obj_new_bool(moy_loop_perf_due());
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_perf_due_obj, loop_perf_due);

static void perf_part(moy_perf_values_t *v, int f, int part, mp_obj_t x) {
    if (x != mp_const_none) {
        moy_perf_set(v, f, part, (double)mp_obj_get_float(x));
    }
}

// perf(name, value): one field of the console's half. None is absent; a
// tuple sets each part, a None part absent on its own.
static mp_obj_t loop_perf(mp_obj_t name, mp_obj_t value) {
    moy_perf_values_t *v = moy_loop_perf_console();
    if (v == NULL) {
        return mp_const_false;
    }
    const char *s = mp_obj_str_get_str(name);
    for (int f = 0; f < MOY_PF_FIELDS; f++) {
        if (strcmp(s, moy_perf_field_name(f)) != 0) {
            continue;
        }
        v->has[f] = 0;
        if (value == mp_const_none) {
            return mp_const_true;
        }
        if (mp_obj_is_type(value, &mp_type_tuple) || mp_obj_is_type(value, &mp_type_list)) {
            size_t n;
            mp_obj_t *items;
            mp_obj_get_array(value, &n, &items);
            for (size_t i = 0; i < n && i < MOY_PF_PARTS; i++) {
                perf_part(v, f, (int)i, items[i]);
            }
        } else {
            perf_part(v, f, 0, value);
        }
        return mp_const_true;
    }
    return mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_2(loop_perf_obj, loop_perf);

static mp_obj_t loop_perf_cart(mp_obj_t title) {
    moy_perf_values_t *v = moy_loop_perf_console();
    if (v != NULL) {
        moy_perf_set_cart(v, title == mp_const_none ? NULL : mp_obj_str_get_str(title));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_perf_cart_obj, loop_perf_cart);

// perf_format({name: value}) -> the line, for the format's own tests.
static mp_obj_t loop_perf_format(mp_obj_t values) {
    moy_perf_values_t v;
    moy_perf_clear(&v);
    mp_map_t *map = mp_obj_dict_get_map(values);
    for (size_t i = 0; i < map->alloc; i++) {
        if (!mp_map_slot_is_filled(map, i)) {
            continue;
        }
        const char *s = mp_obj_str_get_str(map->table[i].key);
        mp_obj_t value = map->table[i].value;
        for (int f = 0; f < MOY_PF_FIELDS; f++) {
            if (strcmp(s, moy_perf_field_name(f)) != 0 || value == mp_const_none) {
                continue;
            }
            if (f == MOY_PF_CART) {
                moy_perf_set_cart(&v, mp_obj_str_get_str(value));
            } else if (mp_obj_is_type(value, &mp_type_tuple) || mp_obj_is_type(value, &mp_type_list)) {
                size_t n;
                mp_obj_t *items;
                mp_obj_get_array(value, &n, &items);
                for (size_t k = 0; k < n && k < MOY_PF_PARTS; k++) {
                    perf_part(&v, f, (int)k, items[k]);
                }
            } else {
                perf_part(&v, f, 0, value);
            }
        }
    }
    char line[MOY_PERF_LINE_MAX];
    size_t n = moy_perf_format(&v, line, sizeof(line));
    return mp_obj_new_str(line, n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_perf_format_obj, loop_perf_format);

static mp_obj_t loop_tap(mp_obj_t x, mp_obj_t y) {
    moy_devch_tap(mp_obj_get_int(x), mp_obj_get_int(y));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(loop_tap_obj, loop_tap);

static mp_obj_t loop_swipe(size_t n_args, const mp_obj_t *a) {
    moy_devch_swipe(mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                    mp_obj_get_int(a[3]), n_args > 4 ? mp_obj_get_int(a[4]) : 20);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_swipe_obj, 4, 5, loop_swipe);

static mp_obj_t loop_drag(size_t n_args, const mp_obj_t *a) {
    moy_devch_drag(mp_obj_get_int(a[0]), mp_obj_get_int(a[1]),
                   n_args > 2 ? mp_obj_get_int(a[2]) : 120, n_args > 3 ? mp_obj_get_int(a[3]) : 6);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_drag_obj, 2, 4, loop_drag);

static mp_obj_t loop_devch(mp_obj_t line) {
    bool quit = false;
    moy_devch_line(mp_obj_str_get_str(line), &quit);
    return mp_obj_new_bool(quit);
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_devch_obj, loop_devch);

static mp_obj_t loop_devch_budget(mp_obj_t n) {
    moy_devch_set_budget((uint32_t)mp_obj_get_int(n));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_devch_budget_obj, loop_devch_budget);

static mp_obj_t loop_devch_stats(void) {
    moy_devch_stats_t s;
    moy_devch_stats(&s);
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(s.rx), mp_obj_new_int_from_uint(s.lines),
        mp_obj_new_int_from_uint(s.dropped), mp_obj_new_bool(s.armed),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_devch_stats_obj, loop_devch_stats);

static mp_obj_t loop_devch_armed(size_t n_args, const mp_obj_t *a) {
    if (n_args) {
        moy_devch_set_armed(mp_obj_is_true(a[0]));
    }
    return mp_obj_new_bool(moy_devch_armed());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(loop_devch_armed_obj, 0, 1, loop_devch_armed);

// -- the trace tier -------------------------------------------------------------------

static mp_obj_t loop_trace_init(mp_obj_t fps, mp_obj_t can_dim, mp_obj_t clock) {
    need_trace();
    moy_loop_host_init(mp_obj_get_int(fps), mp_obj_is_true(can_dim), (uint32_t)mp_obj_get_int(clock));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(loop_trace_init_obj, loop_trace_init);

static mp_obj_t loop_trace_clock(mp_obj_t ms) {
    need_trace();
    moy_loop_host_clock((uint32_t)mp_obj_get_int(ms));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_trace_clock_obj, loop_trace_clock);

static mp_obj_t loop_trace_advance(mp_obj_t us) {
    need_trace();
    moy_loop_host_advance((uint32_t)mp_obj_get_int(us));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_trace_advance_obj, loop_trace_advance);

static mp_obj_t loop_trace_cost(mp_obj_t stage, mp_obj_t us) {
    need_trace();
    moy_loop_host_cost(mp_obj_get_int(stage), (uint32_t)mp_obj_get_int(us));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(loop_trace_cost_obj, loop_trace_cost);

static mp_obj_t loop_trace_input(mp_obj_t click, mp_obj_t active) {
    need_trace();
    moy_loop_host_input(mp_obj_is_true(click), mp_obj_is_true(active));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(loop_trace_input_obj, loop_trace_input);

static mp_obj_t loop_trace_feed(mp_obj_t data) {
    need_trace();
    mp_buffer_info_t b;
    mp_get_buffer_raise(data, &b, MP_BUFFER_READ);
    moy_loop_host_feed(b.buf, b.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_trace_feed_obj, loop_trace_feed);

static mp_obj_t loop_trace_log(void) {
    need_trace();
    const char *s = moy_loop_host_log();
    mp_obj_t r = mp_obj_new_str(s, strlen(s));
    moy_loop_host_clear();
    return r;
}
static MP_DEFINE_CONST_FUN_OBJ_0(loop_trace_log_obj, loop_trace_log);

static mp_obj_t loop_trace_note(mp_obj_t token) {
    need_trace();
    moy_loop_host_note(mp_obj_str_get_str(token));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(loop_trace_note_obj, loop_trace_note);

// -- the module ----------------------------------------------------------------------

static const mp_rom_map_elem_t loop_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_loop) },
    { MP_ROM_QSTR(MP_QSTR_register), MP_ROM_PTR(&loop_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_unregister), MP_ROM_PTR(&loop_unregister_obj) },
    { MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&loop_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_diag_take), MP_ROM_PTR(&loop_diag_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_drive), MP_ROM_PTR(&loop_drive_obj) },
    { MP_ROM_QSTR(MP_QSTR_run), MP_ROM_PTR(&loop_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_upcalls), MP_ROM_PTR(&loop_upcalls_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick), MP_ROM_PTR(&loop_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_capture), MP_ROM_PTR(&loop_capture_obj) },
    { MP_ROM_QSTR(MP_QSTR_fps), MP_ROM_PTR(&loop_fps_obj) },
    { MP_ROM_QSTR(MP_QSTR_services), MP_ROM_PTR(&loop_services_obj) },
    { MP_ROM_QSTR(MP_QSTR_devch_unread), MP_ROM_PTR(&loop_devch_unread_obj) },
    { MP_ROM_QSTR(MP_QSTR_meters), MP_ROM_PTR(&loop_meters_obj) },
    { MP_ROM_QSTR(MP_QSTR_meters_reset), MP_ROM_PTR(&loop_meters_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_stages), MP_ROM_PTR(&loop_stages_obj) },
    { MP_ROM_QSTR(MP_QSTR_pump), MP_ROM_PTR(&loop_pump_obj) },
    { MP_ROM_QSTR(MP_QSTR_pace), MP_ROM_PTR(&loop_pace_obj) },
    { MP_ROM_QSTR(MP_QSTR_lit), MP_ROM_PTR(&loop_lit_obj) },
    { MP_ROM_QSTR(MP_QSTR_health), MP_ROM_PTR(&loop_health_obj) },
    { MP_ROM_QSTR(MP_QSTR_frames), MP_ROM_PTR(&loop_frames_obj) },
    { MP_ROM_QSTR(MP_QSTR_drawn), MP_ROM_PTR(&loop_drawn_obj) },
    { MP_ROM_QSTR(MP_QSTR_last), MP_ROM_PTR(&loop_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_at), MP_ROM_PTR(&loop_frame_at_obj) },
    { MP_ROM_QSTR(MP_QSTR_idle), MP_ROM_PTR(&loop_idle_obj) },
    { MP_ROM_QSTR(MP_QSTR_power), MP_ROM_PTR(&loop_power_obj) },
    { MP_ROM_QSTR(MP_QSTR_idle_state), MP_ROM_PTR(&loop_idle_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_idle_can_dim), MP_ROM_PTR(&loop_idle_can_dim_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_due), MP_ROM_PTR(&loop_perf_due_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf), MP_ROM_PTR(&loop_perf_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_cart), MP_ROM_PTR(&loop_perf_cart_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_format), MP_ROM_PTR(&loop_perf_format_obj) },
    { MP_ROM_QSTR(MP_QSTR_tap), MP_ROM_PTR(&loop_tap_obj) },
    { MP_ROM_QSTR(MP_QSTR_swipe), MP_ROM_PTR(&loop_swipe_obj) },
    { MP_ROM_QSTR(MP_QSTR_drag), MP_ROM_PTR(&loop_drag_obj) },
    { MP_ROM_QSTR(MP_QSTR_devch), MP_ROM_PTR(&loop_devch_obj) },
    { MP_ROM_QSTR(MP_QSTR_devch_budget), MP_ROM_PTR(&loop_devch_budget_obj) },
    { MP_ROM_QSTR(MP_QSTR_devch_stats), MP_ROM_PTR(&loop_devch_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_devch_armed), MP_ROM_PTR(&loop_devch_armed_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_init), MP_ROM_PTR(&loop_trace_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_clock), MP_ROM_PTR(&loop_trace_clock_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_input), MP_ROM_PTR(&loop_trace_input_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_advance), MP_ROM_PTR(&loop_trace_advance_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_cost), MP_ROM_PTR(&loop_trace_cost_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_feed), MP_ROM_PTR(&loop_trace_feed_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_log), MP_ROM_PTR(&loop_trace_log_obj) },
    { MP_ROM_QSTR(MP_QSTR_trace_note), MP_ROM_PTR(&loop_trace_note_obj) },
    { MP_ROM_QSTR(MP_QSTR_OK), MP_ROM_INT(MOY_LOOP_OK) },
    { MP_ROM_QSTR(MP_QSTR_QUIT), MP_ROM_INT(MOY_LOOP_QUIT) },
    { MP_ROM_QSTR(MP_QSTR_INTERRUPT), MP_ROM_INT(MOY_LOOP_INTERRUPT) },
    { MP_ROM_QSTR(MP_QSTR_STOPPED), MP_ROM_INT(MOY_LOOP_STOPPED) },
    { MP_ROM_QSTR(MP_QSTR_EXIT), MP_ROM_INT(MOY_LOOP_EXIT) },
    { MP_ROM_QSTR(MP_QSTR_DIM), MP_ROM_INT(MOY_IDLE_DIM) },
    { MP_ROM_QSTR(MP_QSTR_SAVER), MP_ROM_INT(MOY_IDLE_SAVER) },
    { MP_ROM_QSTR(MP_QSTR_BLANK), MP_ROM_INT(MOY_IDLE_BLANK) },
    { MP_ROM_QSTR(MP_QSTR_SVC_WEB), MP_ROM_INT(MOY_SVC_WEB) },
    { MP_ROM_QSTR(MP_QSTR_SVC_LINK), MP_ROM_INT(MOY_SVC_LINK) },
    { MP_ROM_QSTR(MP_QSTR_SVC_UPDATE), MP_ROM_INT(MOY_SVC_UPDATE) },
    { MP_ROM_QSTR(MP_QSTR_SVC_HEALTHY), MP_ROM_INT(MOY_SVC_HEALTHY) },
};
static MP_DEFINE_CONST_DICT(loop_globals, loop_globals_table);

const mp_obj_module_t moy_loop_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&loop_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_loop, moy_loop_module);
