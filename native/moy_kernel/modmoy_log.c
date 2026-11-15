// moy_log's MicroPython binding: the OS log (moy_log.h) as the module
// `moy_log`, its names those face.json lists: the rows here are the hand
// ones, the rest are generated into modmoy_log_face.c.
//
//   write(level, pid, *parts)   one record: the parts' str() joined by spaces,
//                      under `pid` (None: current). A single str part is
//                      written from its own bytes; nothing is allocated
//   spawn(name)        a new process's pid, named
//   name(pid)          its name ("" once reused)
//   current([pid])     the pid a write that names none goes under
//   read(seq=0, n=32)  up to n records from seq on, oldest first:
//                      [(seq, t_us, level, pid, text), ...]
//   next()             the seq the next write takes
//   stats()            (first, next, used, cap)
//   level_name(level), level_of(name)
//   init(bytes)        the ring made again `bytes` large and emptied (tests)
//   pin_clock(us)      the clock pinned (tests; 0: the real one)
//   EMERG .. DEBUG, KERNEL, SHELL, TEXT_MAX

#include <string.h>

#include "py/runtime.h"
#include "py/objstr.h"

#include "moy_sb.h"
#include "moy_log.h"
#include "moy_proc_exit.h"

#include "modmoy_log_face.c"

#define JOIN_MAX 384

static void join_strn(void *env, const char *s, size_t n) {
    moy_sb_put(env, s, n);
}

static uint32_t pid_of(mp_obj_t o) {
    return o == mp_const_none ? MOY_LOG_CURRENT : (uint32_t)mp_obj_get_int_truncated(o);
}

static mp_obj_t log_write(size_t n_args, const mp_obj_t *args) {
    int level = mp_obj_get_int(args[0]);
    uint32_t pid = pid_of(args[1]);
    if (n_args == 3 && (mp_obj_is_str(args[2]) || mp_obj_is_type(args[2], &mp_type_bytes))) {
        size_t n;
        const char *s = mp_obj_str_get_data(args[2], &n);
        moy_log_write(level, pid, s, n);
        return mp_const_none;
    }
    char buf[JOIN_MAX];
    moy_sb_t j = moy_sb_fixed(buf, sizeof(buf));
    mp_print_t p = { &j, join_strn };
    for (size_t i = 2; i < n_args; i++) {
        if (i > 2) {
            join_strn(&j, " ", 1);
        }
        mp_obj_print_helper(&p, args[i], PRINT_STR);
    }
    moy_log_write(level, pid, buf, j.n < sizeof(buf) ? j.n : sizeof(buf));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(log_write_obj, 2, MP_OBJ_FUN_ARGS_MAX, log_write);

static mp_obj_t log_current(size_t n_args, const mp_obj_t *args) {
    if (n_args) {
        moy_log_set_current((uint32_t)mp_obj_get_int_truncated(args[0]));
        return mp_const_none;
    }
    return mp_obj_new_int_from_uint(moy_log_current());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(log_current_obj, 0, 1, log_current);

static mp_obj_t log_read(size_t n_args, const mp_obj_t *args) {
    moy_log_cursor_t c = { n_args > 0 ? (uint32_t)mp_obj_get_int_truncated(args[0]) : 0u,
                           UINT32_MAX };
    mp_int_t most = n_args > 1 ? mp_obj_get_int(args[1]) : 32;
    mp_obj_t out = mp_obj_new_list(0, NULL);
    char *text = m_new(char, MOY_LOG_TEXT_MAX);
    moy_log_rec_t r;
    while (most-- > 0 && moy_log_read(&c, &r, text, MOY_LOG_TEXT_MAX)) {
        mp_obj_t t[5] = {
            mp_obj_new_int_from_uint(r.seq),
            mp_obj_new_int_from_ull(r.t_us),
            MP_OBJ_NEW_SMALL_INT(r.level),
            mp_obj_new_int_from_uint(r.pid),
            mp_obj_new_str(text, r.len < MOY_LOG_TEXT_MAX ? r.len : MOY_LOG_TEXT_MAX),
        };
        mp_obj_list_append(out, mp_obj_new_tuple(5, t));
    }
    m_del(char, text, MOY_LOG_TEXT_MAX);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(log_read_obj, 0, 2, log_read);

static mp_obj_t log_next(void) {
    moy_log_stats_t s;
    moy_log_stats(&s);
    return mp_obj_new_int_from_uint(s.next);
}
static MP_DEFINE_CONST_FUN_OBJ_0(log_next_obj, log_next);

static const mp_rom_map_elem_t log_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_log) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&log_write_obj) },
    MOY_FACE_MOY_LOG_ROWS
    { MP_ROM_QSTR(MP_QSTR_current), MP_ROM_PTR(&log_current_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&log_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_next), MP_ROM_PTR(&log_next_obj) },
};
static MP_DEFINE_CONST_DICT(log_globals, log_globals_table);

const mp_obj_module_t moy_log_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&log_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_log, moy_log_module);
