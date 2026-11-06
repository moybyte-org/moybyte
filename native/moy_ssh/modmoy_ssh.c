// The module `moy_ssh`: the server's task, started and stopped from the
// console (moy_ssh.h).
//
//   moy_ssh.start(hash, key=None, port=22) -> the bytes of a new host key, or None
//   moy_ssh.stop()
//   moy_ssh.stats() -> (sessions, signed_in, refused, heap_now, heap_peak,
//                       session_peak, to_shell_ms, listening, stack_free)
//   moy_ssh.hash(password, salt) -> the stored form
//
// The key and the hash are the store's, read and written by the caller: a
// file in the store is the VM's to touch. The server task's stack is PSRAM;
// the setup (the key made or read) runs on the caller's task.

#include <string.h>
#include <unistd.h>

#include "py/runtime.h"
#include "py/objstr.h"
#include "py/mperrno.h"
#include "py/mphal.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "esp_heap_caps.h"

#include "moy_ssh.h"
#include "moy_ssh_face.h"

#define SSH_STACK (16 * 1024)
#define SSH_PRIORITY 1
#define SSH_CORE 0

extern const moy_ssh_shell_t moy_ssh_moy_sh;

static TaskHandle_t s_task;
static volatile int s_stop, s_done, s_lfd = -1;

static void ssh_task(void *arg) {
    (void)arg;
    while (!s_stop) {
        if (moy_ssh_serve_one(s_lfd, 500) < 0) {
            break;
        }
    }
    close(s_lfd);
    s_lfd = -1;
    s_done = 1;
    vTaskSuspend(NULL);
}

static mp_obj_t ssh_start(size_t n_args, const mp_obj_t *args) {
    const char *hash = mp_obj_str_get_str(args[0]);
    mp_buffer_info_t key = {0};
    bool have_key = n_args > 1 && args[1] != mp_const_none;
    if (have_key) {
        mp_get_buffer_raise(args[1], &key, MP_BUFFER_READ);
    }
    int port = n_args > 2 ? mp_obj_get_int(args[2]) : 22;
    uint8_t *made = NULL;
    size_t made_n = 0;
    if (moy_ssh_setup(have_key ? key.buf : NULL, key.len, hash, &made, &made_n) != 0) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("ssh setup failed"));
    }
    mp_obj_t out = mp_const_none;
    if (made != NULL) {
        out = mp_obj_new_bytes(made, made_n);
        moy_ssh_free(made);
    }
    if (s_task != NULL) {
        return out;
    }
    moy_ssh_face_set_shell(&moy_ssh_moy_sh);
    s_lfd = moy_ssh_listen(port);
    if (s_lfd < 0) {
        mp_raise_OSError(MP_EADDRINUSE);
    }
    s_stop = s_done = 0;
    if (xTaskCreatePinnedToCoreWithCaps(ssh_task, "moy_ssh", SSH_STACK, NULL, SSH_PRIORITY,
                                        &s_task, SSH_CORE,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        close(s_lfd);
        s_lfd = -1;
        s_task = NULL;
        mp_raise_OSError(MP_ENOMEM);
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ssh_start_obj, 1, 3, ssh_start);

// The task ends after the session it serves (the listener's next wait).
static mp_obj_t ssh_stop(void) {
    if (s_task == NULL) {
        return mp_const_none;
    }
    s_stop = 1;
    while (!s_done) {
        mp_hal_delay_ms(20);
    }
    vTaskDeleteWithCaps(s_task);
    s_task = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ssh_stop_obj, ssh_stop);

static mp_obj_t ssh_stats(void) {
    moy_ssh_stats_t s;
    moy_ssh_stats(&s);
    mp_obj_t t[9] = {
        mp_obj_new_int_from_uint(s.sessions), mp_obj_new_int_from_uint(s.signed_in),
        mp_obj_new_int_from_uint(s.refused), mp_obj_new_int_from_uint(s.heap_now),
        mp_obj_new_int_from_uint(s.heap_peak), mp_obj_new_int_from_uint(s.session_peak),
        mp_obj_new_int_from_uint(s.handshake_ms), mp_obj_new_bool(s_task != NULL),
        s_task != NULL ? mp_obj_new_int_from_uint(uxTaskGetStackHighWaterMark(s_task)
                                                  * sizeof(StackType_t))
                       : mp_const_none,
    };
    return mp_obj_new_tuple(9, t);
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
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&ssh_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_hash), MP_ROM_PTR(&ssh_hash_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ssh_globals, moy_ssh_globals_table);

const mp_obj_module_t moy_ssh_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ssh_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ssh, moy_ssh_module);
