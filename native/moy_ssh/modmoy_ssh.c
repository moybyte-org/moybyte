// The module `moy_ssh`: the server's two tasks, started and stopped from the
// console (moy_ssh.h).
//
//   moy_ssh.start(system_dir, port=22)
//   moy_ssh.stop()
//   moy_ssh.parked() -> whether both tasks have parked after a stop
//   moy_ssh.stats() -> (sessions, signed_in, refused, heap_now, heap_peak,
//                       session_peak, to_shell_ms, listening, stack_free,
//                       face_stack_free, arena_peak)
//   moy_ssh.hash(password, salt) -> the stored form
//
// `system_dir` is the store's system folder: the password, `ssh`, the
// authorized keys and the host key are read there (moy_ssh.h's SIGN-IN). The
// setup runs on the caller's task, the console's; the tasks are the run's
// (moy_ssh.h, THE RUN; moy_ssh_run.c).

#include <string.h>

#include "py/runtime.h"
#include "py/objstr.h"
#include "py/mperrno.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "moy_ssh.h"
#include "moy_tty.h"

extern const moy_tty_shell_t moy_tty_moy_sh;

static mp_obj_t ssh_start(size_t n_args, const mp_obj_t *args) {
    const char *sys = mp_obj_str_get_str(args[0]);
    int port = n_args > 1 ? mp_obj_get_int(args[1]) : 22;
    if (moy_ssh_setup(sys) != 0) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("ssh setup failed"));
    }
    moy_tty_set_shell(&moy_tty_moy_sh);
    int r = moy_ssh_run_start(port);
    if (r == MOY_SSH_EADDRINUSE) {
        mp_raise_OSError(MP_EADDRINUSE);
    }
    if (r != 0) {
        mp_raise_OSError(MP_ENOMEM);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ssh_start_obj, 1, 2, ssh_start);

static mp_obj_t ssh_stop(void) {
    moy_ssh_run_stop();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ssh_stop_obj, ssh_stop);

static mp_obj_t ssh_parked(void) {
    return mp_obj_new_bool(moy_ssh_run_parked());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ssh_parked_obj, ssh_parked);

static mp_obj_t stack_free(uintptr_t h) {
    return h != 0 ? mp_obj_new_int_from_uint(uxTaskGetStackHighWaterMark((TaskHandle_t)h)
                                                * sizeof(StackType_t))
                     : mp_const_none;
}

static mp_obj_t ssh_stats(void) {
    moy_ssh_stats_t s;
    moy_ssh_stats(&s);
    uintptr_t server, face;
    moy_ssh_run_tasks(&server, &face);
    mp_obj_t t[11] = {
        mp_obj_new_int_from_uint(s.sessions), mp_obj_new_int_from_uint(s.signed_in),
        mp_obj_new_int_from_uint(s.refused), mp_obj_new_int_from_uint(s.heap_now),
        mp_obj_new_int_from_uint(s.heap_peak), mp_obj_new_int_from_uint(s.session_peak),
        mp_obj_new_int_from_uint(s.handshake_ms), mp_obj_new_bool(moy_ssh_run_listening()),
        stack_free(server), stack_free(face),
        mp_obj_new_int_from_uint(s.arena_peak),
    };
    return mp_obj_new_tuple(11, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ssh_stats_obj, ssh_stats);

static mp_obj_t ssh_hash(mp_obj_t pw, mp_obj_t salt) {
    char out[MOY_SSH_HASH_MAX];
    if (moy_ssh_hash(mp_obj_str_get_str(pw), mp_obj_str_get_str(salt), out, sizeof out) != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("salt: 1 to 32 hex digits"));
    }
    return mp_obj_new_str(out, strlen(out));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ssh_hash_obj, ssh_hash);

static const mp_rom_map_elem_t moy_ssh_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ssh) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&ssh_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&ssh_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_parked), MP_ROM_PTR(&ssh_parked_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&ssh_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_hash), MP_ROM_PTR(&ssh_hash_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ssh_globals, moy_ssh_globals_table);

const mp_obj_module_t moy_ssh_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ssh_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ssh, moy_ssh_module);
