// The module `moy_tty`: the stream face's figures (moy_tty.h), for the
// console's diagnostics and the on-glass suites, and its tail for a board
// with no frame.
//
//   moy_tty.stats() -> (faces, busy, arena_peak, sends,
//                       web_sessions, web_refused, web_in, web_out, web_frames, web_live)
//   moy_tty.tail()     serve a face's hop on this task: the console's task
//                      where no frame loop calls moy_tty_tail (the Zero's
//                      poll loop, zero_host.serve)

#include "py/runtime.h"

#include "moy_tty.h"

static mp_obj_t tty_stats(void) {
    moy_tty_stats_t s;
    moy_tty_ws_stats_t w;
    moy_tty_stats(&s);
    moy_tty_ws_stats(&w);
    mp_obj_t t[10] = {
        mp_obj_new_int_from_uint(s.faces), mp_obj_new_int_from_uint(s.busy),
        mp_obj_new_int_from_uint(s.arena_peak), mp_obj_new_int_from_uint(s.sends),
        mp_obj_new_int_from_uint(w.sessions), mp_obj_new_int_from_uint(w.refused),
        mp_obj_new_int_from_uint(w.in_bytes), mp_obj_new_int_from_uint(w.out_bytes),
        mp_obj_new_int_from_uint(w.frames_out), mp_obj_new_bool(w.live),
    };
    return mp_obj_new_tuple(10, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tty_stats_obj, tty_stats);

static mp_obj_t tty_tail(void) {
    moy_tty_tail();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tty_tail_obj, tty_tail);

static const mp_rom_map_elem_t moy_tty_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_tty) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&tty_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_tail), MP_ROM_PTR(&tty_tail_obj) },
};
static MP_DEFINE_CONST_DICT(moy_tty_globals, moy_tty_globals_table);

const mp_obj_module_t moy_tty_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_tty_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_tty, moy_tty_module);
