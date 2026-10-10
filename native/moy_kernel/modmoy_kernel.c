// The kernel's two small bindings (moy_kernel.c has the design):
//
//   moy_kernel.boot_ok()           the console painted its first frame
//   moy_kernel.boot_failed(text)   what the console's failed boot said
//   moy_kernel.mode()              'start', 'safe' or 'repl'
//   moy_kernel.test(kind)          DEV: 'vm_start' or 'heap' on the next boot; restarts
//   moy_kernel.feed()              the console's frame: feeds the task watchdog (#160)
//   moy_kernel.rest()              the frame loop ended: nothing feeds it now
//   moy_kernel.watchdog([reset])   (armed, timeout_ms, max_gap_ms, frames)
//   moy_kernel.kstop(n)            DEV: n soft resets of the VM, heaps printed
//   moy_kernel.lit()               ms after power-on the kernel lit the logo, or None
//   moy_kernel.start()             'boot', or 'return' after a VM stop
//   moy_kernel.stop()              the loop ends at this frame and the VM stops
//                                  for the run the Player launched
//   moy_kernel.resume([text])      the resume record: set it, or read it (None)
//   moy_kernel.stamp(part)         the console's part of the start: 2 IMPORTS,
//                                  3 WS, 4 WIRED (moy_kernel.h's MOY_STAMP_*)
//   moy_kernel.stamps()            the start's six stamps, ms after power-on
//                                  (None: not reached): exit, vm, imports, ws,
//                                  wired, frame
//   moy_kernel.kpool()             (high-water bytes, misses) of the kernel's
//                                  small-block pool (moy_kernel.h)
//   moy_kernel.time_imports(rows)  DEV: the memory census's import timer
//                                  (device/mem_census.py), installed as
//                                  builtins.__import__ by the census
//
//   moy_crash.arm(role, id)        the ledger's OPEN id (None clears), into RTC
//   moy_crash.last()               the last record this board made, or None
//   moy_crash.take()               this boot's fresh record, once, or None
//   moy_crash.panic(how)           DEV: 'fault' (a store to 0) or 'abort', now

#include "py/runtime.h"
#include "py/objstr.h"
#include "py/builtin.h"
#include "py/mphal.h"

#include "moy_crash.h"
#include "moy_kernel.h"

// ---- moy_kernel ---------------------------------------------------------------

static mp_obj_t mod_boot_ok(void) {
    moy_kernel_boot_ok();
    if (moy_kernel_kstop_next()) {
        mp_raise_type(&mp_type_SystemExit);     // kstop: the next reset
    }
    return mp_const_none;
}

// kstop(n): leave the console now, and again each time it proves itself,
// until n soft resets have run.
static mp_obj_t mod_kstop(mp_obj_t n) {
    moy_kernel_kstop(mp_obj_get_int(n));
    mp_raise_type(&mp_type_SystemExit);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_kstop_obj, mod_kstop);

static mp_obj_t mod_lit(void) {
    uint32_t ms = moy_kernel_lit_ms();
    return ms ? mp_obj_new_int_from_uint(ms) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lit_obj, mod_lit);
static MP_DEFINE_CONST_FUN_OBJ_0(mod_boot_ok_obj, mod_boot_ok);

static mp_obj_t mod_start(void) {
    return MP_OBJ_NEW_QSTR(moy_kernel_start() == MOY_START_RETURN ? MP_QSTR_return
                                                                 : MP_QSTR_boot);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_start_obj, mod_start);

static mp_obj_t mod_stop(void) {
    moy_kernel_stop(MOY_STOP_RUN);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_stop_obj, mod_stop);

static mp_obj_t mod_resume(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0) {
        size_t n;
        const char *t = mp_obj_str_get_data(args[0], &n);
        moy_kernel_resume_set(t, n);
        return mp_const_none;
    }
    const char *t;
    size_t n = moy_kernel_resume(&t);
    return t == NULL ? mp_const_none : mp_obj_new_str(t, n);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_resume_obj, 0, 1, mod_resume);

static mp_obj_t mod_stamp(mp_obj_t part) {
    int k = mp_obj_get_int(part);
    if (k > MOY_STAMP_VM && k < MOY_STAMP_FRAME) {
        moy_kernel_stamp(k);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_stamp_obj, mod_stamp);

static mp_obj_t mod_stamps(void) {
    const uint32_t *s = moy_kernel_stamps();
    mp_obj_t t[MOY_STAMPS];
    for (int k = 0; k < MOY_STAMPS; k++) {
        t[k] = s[k] || k == MOY_STAMP_EXIT ? mp_obj_new_int_from_uint(s[k]) : mp_const_none;
    }
    return mp_obj_new_tuple(MOY_STAMPS, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_stamps_obj, mod_stamps);

static mp_obj_t mod_boot_failed(mp_obj_t what) {
    moy_kernel_boot_failed(mp_obj_str_get_str(what));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_boot_failed_obj, mod_boot_failed);

static mp_obj_t mod_mode(void) {
    switch (moy_kernel_mode()) {
        case MOY_BOOT_SAFE: return MP_OBJ_NEW_QSTR(MP_QSTR_safe);
        case MOY_BOOT_REPL: return MP_OBJ_NEW_QSTR(MP_QSTR_repl);
        default: return MP_OBJ_NEW_QSTR(MP_QSTR_start);
    }
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_mode_obj, mod_mode);

static mp_obj_t mod_test(mp_obj_t kind) {
    const char *k = mp_obj_str_get_str(kind);
    if (strcmp(k, "vm_start") == 0) {
        moy_kernel_test_restart(MOY_TEST_VM_START);
    } else if (strcmp(k, "heap") == 0) {
        moy_kernel_test_restart(MOY_TEST_HEAP);
    }
    mp_raise_ValueError(MP_ERROR_TEXT("test: 'vm_start' or 'heap'"));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_test_obj, mod_test);

static mp_obj_t mod_feed(void) {
    moy_kernel_feed();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_feed_obj, mod_feed);

static mp_obj_t mod_rest(void) {
    moy_kernel_rest();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_rest_obj, mod_rest);

static mp_obj_t mod_watchdog(size_t n_args, const mp_obj_t *args) {
    uint32_t timeout_ms, max_gap_ms, frames;
    bool armed = moy_kernel_watchdog(&timeout_ms, &max_gap_ms, &frames,
                                     n_args > 0 && mp_obj_is_true(args[0]));
    mp_obj_t t[4] = {
        mp_obj_new_bool(armed),
        mp_obj_new_int_from_uint(timeout_ms),
        mp_obj_new_int_from_uint(max_gap_ms),
        mp_obj_new_int_from_uint(frames),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_watchdog_obj, 0, 1, mod_watchdog);

// THE CENSUS'S IMPORT TIMER. Every module's first import, timed inclusive of
// what it imports, as (name, depth, us) rows appended to the census's list
// when it finishes. It is C so that each import level it wraps costs this
// frame and nothing more: a Python hook added a bytecode frame per level, and
// an armed start's imports, eight deep, left 88 bytes of the VM task's stack
// (#224). A name already in sys.modules, or timed once (a built-in module
// never enters sys.modules), goes straight through. The rows and the seen
// names are one root pointer, so a VM stop's root sweep drops them with the
// VM; the census installs the timer again at the next start.
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_kernel_imports);
static int s_import_depth;

static void import_row(mp_obj_t rows, mp_obj_t name, int depth, uint32_t t0) {
    mp_obj_t t[3] = {
        name, MP_OBJ_NEW_SMALL_INT(depth),
        mp_obj_new_int_from_uint(mp_hal_ticks_us() - t0),
    };
    mp_obj_list_append(rows, mp_obj_new_tuple(3, t));
}

static mp_obj_t mod_timed_import(size_t n_args, const mp_obj_t *args) {
    mp_obj_t st = MP_STATE_VM(moy_kernel_imports);
    if (st == MP_OBJ_NULL || st == mp_const_none) {
        return mp_builtin___import__(n_args, args);
    }
    mp_obj_t *it;
    mp_obj_get_array_fixed_n(st, 2, &it);
    mp_map_t *seen = mp_obj_dict_get_map(it[1]);
    if (mp_map_lookup(&MP_STATE_VM(mp_loaded_modules_dict).map, args[0], MP_MAP_LOOKUP) != NULL
        || mp_map_lookup(seen, args[0], MP_MAP_LOOKUP) != NULL) {
        return mp_builtin___import__(n_args, args);
    }
    mp_obj_dict_store(it[1], args[0], mp_const_true);
    int depth = s_import_depth++;
    uint32_t t0 = mp_hal_ticks_us();
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t m = mp_builtin___import__(n_args, args);
        nlr_pop();
        s_import_depth = depth;
        import_row(it[0], args[0], depth, t0);
        return m;
    }
    s_import_depth = depth;
    import_row(it[0], args[0], depth, t0);
    nlr_jump(nlr.ret_val);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_timed_import_obj, 1, 5, mod_timed_import);

// time_imports(rows) -> the timer to install as builtins.__import__; `rows`
// the list it appends to.
static mp_obj_t mod_time_imports(mp_obj_t rows) {
    mp_obj_t t[2] = { rows, mp_obj_new_dict(0) };
    MP_STATE_VM(moy_kernel_imports) = mp_obj_new_tuple(2, t);
    s_import_depth = 0;
    return MP_OBJ_FROM_PTR(&mod_timed_import_obj);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_time_imports_obj, mod_time_imports);

static mp_obj_t mod_kpool(void) {
    size_t high;
    uint32_t misses;
    moy_kpool_stats(&high, &misses);
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(high), mp_obj_new_int_from_uint(misses) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_kpool_obj, mod_kpool);

static const mp_rom_map_elem_t moy_kernel_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_kpool), MP_ROM_PTR(&mod_kpool_obj) },
    { MP_ROM_QSTR(MP_QSTR_time_imports), MP_ROM_PTR(&mod_time_imports_obj) },
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_kernel) },
    { MP_ROM_QSTR(MP_QSTR_boot_ok), MP_ROM_PTR(&mod_boot_ok_obj) },
    { MP_ROM_QSTR(MP_QSTR_kstop), MP_ROM_PTR(&mod_kstop_obj) },
    { MP_ROM_QSTR(MP_QSTR_lit), MP_ROM_PTR(&mod_lit_obj) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&mod_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&mod_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_resume), MP_ROM_PTR(&mod_resume_obj) },
    { MP_ROM_QSTR(MP_QSTR_stamp), MP_ROM_PTR(&mod_stamp_obj) },
    { MP_ROM_QSTR(MP_QSTR_stamps), MP_ROM_PTR(&mod_stamps_obj) },
    { MP_ROM_QSTR(MP_QSTR_boot_failed), MP_ROM_PTR(&mod_boot_failed_obj) },
    { MP_ROM_QSTR(MP_QSTR_mode), MP_ROM_PTR(&mod_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_test), MP_ROM_PTR(&mod_test_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed), MP_ROM_PTR(&mod_feed_obj) },
    { MP_ROM_QSTR(MP_QSTR_rest), MP_ROM_PTR(&mod_rest_obj) },
    { MP_ROM_QSTR(MP_QSTR_watchdog), MP_ROM_PTR(&mod_watchdog_obj) },
};
static MP_DEFINE_CONST_DICT(moy_kernel_globals, moy_kernel_globals_table);

const mp_obj_module_t moy_kernel_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_kernel_globals,
};
MP_REGISTER_MODULE(MP_QSTR_moy_kernel, moy_kernel_module);

// ---- moy_crash ----------------------------------------------------------------

static void put(mp_obj_t d, qstr k, mp_obj_t v) {
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(k), v);
}

static mp_obj_t str_of(const char *s) {
    return mp_obj_new_str(s, strlen(s));
}

static mp_obj_t rec_dict(const moy_crash_rec_t *r) {
    if (r == NULL) {
        return mp_const_none;
    }
    mp_obj_t d = mp_obj_new_dict(14);
    put(d, MP_QSTR_kind, str_of(moy_crash_kind_name(r->kind)));
    put(d, MP_QSTR_task, str_of(r->task));
    put(d, MP_QSTR_id, r->id[0] ? str_of(r->id) : mp_const_none);
    put(d, MP_QSTR_role, str_of(moy_crash_role_name(r->role)));
    put(d, MP_QSTR_what, str_of(r->what));
    put(d, MP_QSTR_reset, str_of(moy_kernel_reset_name(r->reset)));
    put(d, MP_QSTR_pc, mp_obj_new_int_from_uint(r->pc));
    put(d, MP_QSTR_cause, mp_obj_new_int_from_uint(r->cause));
    put(d, MP_QSTR_addr, mp_obj_new_int_from_uint(r->addr));
    mp_obj_t bt[4];
    for (int i = 0; i < 4; i++) {
        bt[i] = mp_obj_new_int_from_uint(r->bt[i]);
    }
    put(d, MP_QSTR_bt, mp_obj_new_list(4, bt));
    put(d, MP_QSTR_core, MP_OBJ_NEW_SMALL_INT(r->core));
    put(d, MP_QSTR_boot, mp_obj_new_int_from_uint(r->boot));
    put(d, MP_QSTR_uptime_ms, mp_obj_new_int_from_uint(r->uptime_ms));
    put(d, MP_QSTR_build, mp_obj_new_int_from_uint(r->build));
    return d;
}

static mp_obj_t mod_arm(mp_obj_t role, mp_obj_t id) {
    mp_int_t r = mp_obj_get_int(role);
    if (r != MOY_ROLE_APP && r != MOY_ROLE_WALLPAPER && r != MOY_ROLE_GAME) {
        mp_raise_ValueError(MP_ERROR_TEXT("role: 1 (app), 2 (wallpaper) or 3 (game)"));
    }
    moy_kernel_arm((int)r, id == mp_const_none ? NULL : mp_obj_str_get_str(id));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_arm_obj, mod_arm);

static mp_obj_t mod_last(void) {
    return rec_dict(moy_kernel_last_crash());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_last_obj, mod_last);

static mp_obj_t mod_take(void) {
    return rec_dict(moy_kernel_take_crash());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_take_obj, mod_take);

static mp_obj_t mod_panic(mp_obj_t how) {
    const char *h = mp_obj_str_get_str(how);
    if (strcmp(h, "fault") != 0 && strcmp(h, "abort") != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("panic: 'fault' or 'abort'"));
    }
    moy_kernel_test_crash(strcmp(h, "abort") == 0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_panic_obj, mod_panic);

static const mp_rom_map_elem_t moy_crash_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_crash) },
    { MP_ROM_QSTR(MP_QSTR_arm), MP_ROM_PTR(&mod_arm_obj) },
    { MP_ROM_QSTR(MP_QSTR_last), MP_ROM_PTR(&mod_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&mod_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_panic), MP_ROM_PTR(&mod_panic_obj) },
    { MP_ROM_QSTR(MP_QSTR_APP), MP_ROM_INT(MOY_ROLE_APP) },
    { MP_ROM_QSTR(MP_QSTR_WALLPAPER), MP_ROM_INT(MOY_ROLE_WALLPAPER) },
    { MP_ROM_QSTR(MP_QSTR_GAME), MP_ROM_INT(MOY_ROLE_GAME) },
};
static MP_DEFINE_CONST_DICT(moy_crash_globals, moy_crash_globals_table);

const mp_obj_module_t moy_crash_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_crash_globals,
};
MP_REGISTER_MODULE(MP_QSTR_moy_crash, moy_crash_module);
