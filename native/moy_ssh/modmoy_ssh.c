// The module `moy_ssh`: the server's two tasks, started and stopped from the
// console (moy_ssh.h).
//
//   moy_ssh.start(system_dir, port=22)
//   moy_ssh.stop()
//   moy_ssh.stats() -> (sessions, signed_in, refused, heap_now, heap_peak,
//                       session_peak, to_shell_ms, listening, stack_free,
//                       face_stack_free, arena_peak)
//   moy_ssh.hash(password, salt) -> the stored form
//
// `system_dir` is the store's system folder: the password, `ssh`, the
// authorized keys and the host key are read there (moy_ssh.h's SIGN-IN). The
// setup runs on the caller's task, the console's; the server task and the
// face task (moy_ssh_face.h) have PSRAM stacks and records.

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
#define FACE_STACK (24 * 1024)
#define SSH_PRIORITY 1
#define SSH_CORE 0

extern const moy_ssh_shell_t moy_ssh_moy_sh;

typedef struct {
    TaskHandle_t task, face;
    volatile int stop, done, face_done;
    int lfd;
} ssh_tasks_t;

static ssh_tasks_t *s_t;

static void ssh_task(void *arg) {
    ssh_tasks_t *t = arg;
    while (!t->stop) {
        if (moy_ssh_serve_one(t->lfd, 500) < 0) {
            break;
        }
    }
    close(t->lfd);
    t->lfd = -1;
    t->done = 1;
    vTaskSuspend(NULL);
}

static void face_task(void *arg) {
    ssh_tasks_t *t = arg;
    moy_ssh_face_task(&t->stop);
    t->face_done = 1;
    vTaskSuspend(NULL);
}

static TaskHandle_t spawn(TaskFunction_t fn, const char *name, uint32_t stack) {
    TaskHandle_t h = NULL;
    if (xTaskCreatePinnedToCoreWithCaps(fn, name, stack, s_t, SSH_PRIORITY, &h, SSH_CORE,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        return NULL;
    }
    return h;
}

static void reap(void);

static mp_obj_t ssh_start(size_t n_args, const mp_obj_t *args) {
    const char *sys = mp_obj_str_get_str(args[0]);
    int port = n_args > 1 ? mp_obj_get_int(args[1]) : 22;
    if (moy_ssh_setup(sys) != 0) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("ssh setup failed"));
    }
    if (s_t != NULL && s_t->stop && !moy_ssh_serving()) {
        reap();
    }
    if (s_t != NULL) {
        return mp_const_none;
    }
    s_t = heap_caps_calloc(1, sizeof *s_t, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_t == NULL) {
        mp_raise_OSError(MP_ENOMEM);
    }
    moy_ssh_face_set_shell(&moy_ssh_moy_sh);
    s_t->lfd = moy_ssh_listen(port);
    if (s_t->lfd < 0) {
        heap_caps_free(s_t);
        s_t = NULL;
        mp_raise_OSError(MP_EADDRINUSE);
    }
    s_t->face = spawn(face_task, "moy_ssh_face", FACE_STACK);
    s_t->task = s_t->face != NULL ? spawn(ssh_task, "moy_ssh", SSH_STACK) : NULL;
    if (s_t->task == NULL) {
        if (s_t->face != NULL) {
            s_t->stop = 1;
            while (!s_t->face_done) {
                mp_hal_delay_ms(20);
            }
            vTaskDeleteWithCaps(s_t->face);
        }
        close(s_t->lfd);
        heap_caps_free(s_t);
        s_t = NULL;
        mp_raise_OSError(MP_ENOMEM);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ssh_start_obj, 1, 2, ssh_start);

// The tasks end after the session they serve (the listener's next wait),
// the hops they make served here meanwhile. Asked from a session's own
// command (`passwd -d` over SSH: inside a hop) they are told to end and
// reaped by the next start or stop.
static void reap(void) {
    while (!s_t->done || !s_t->face_done) {
        moy_ssh_tail();
        mp_hal_delay_ms(20);
    }
    vTaskDeleteWithCaps(s_t->task);
    vTaskDeleteWithCaps(s_t->face);
    heap_caps_free(s_t);
    s_t = NULL;
}

static mp_obj_t ssh_stop(void) {
    if (s_t == NULL) {
        return mp_const_none;
    }
    s_t->stop = 1;
    if (!moy_ssh_serving()) {
        reap();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ssh_stop_obj, ssh_stop);

static mp_obj_t stack_free(TaskHandle_t h) {
    return h != NULL ? mp_obj_new_int_from_uint(uxTaskGetStackHighWaterMark(h)
                                                * sizeof(StackType_t))
                     : mp_const_none;
}

static mp_obj_t ssh_stats(void) {
    moy_ssh_stats_t s;
    moy_ssh_stats(&s);
    mp_obj_t t[11] = {
        mp_obj_new_int_from_uint(s.sessions), mp_obj_new_int_from_uint(s.signed_in),
        mp_obj_new_int_from_uint(s.refused), mp_obj_new_int_from_uint(s.heap_now),
        mp_obj_new_int_from_uint(s.heap_peak), mp_obj_new_int_from_uint(s.session_peak),
        mp_obj_new_int_from_uint(s.handshake_ms), mp_obj_new_bool(s_t != NULL),
        stack_free(s_t != NULL ? s_t->task : NULL), stack_free(s_t != NULL ? s_t->face : NULL),
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
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&ssh_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_hash), MP_ROM_PTR(&ssh_hash_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ssh_globals, moy_ssh_globals_table);

const mp_obj_module_t moy_ssh_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ssh_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ssh, moy_ssh_module);
