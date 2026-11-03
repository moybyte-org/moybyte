// moy_log's MicroPython binding: the OS log (moy_log.h) as the module
// `moy_log`, the same names runtime/host/moy_log.py gives CPython.
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

#include "moy_log.h"

#define JOIN_MAX 384

typedef struct {
    char *buf;
    size_t n, cap;
} join_t;

static void join_strn(void *env, const char *s, size_t n) {
    join_t *j = env;
    size_t room = j->cap - j->n;
    if (n > room) {
        n = room;
    }
    memcpy(j->buf + j->n, s, n);
    j->n += n;
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
    join_t j = { buf, 0, sizeof(buf) };
    mp_print_t p = { &j, join_strn };
    for (size_t i = 2; i < n_args; i++) {
        if (i > 2) {
            join_strn(&j, " ", 1);
        }
        mp_obj_print_helper(&p, args[i], PRINT_STR);
    }
    moy_log_write(level, pid, buf, j.n);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(log_write_obj, 2, MP_OBJ_FUN_ARGS_MAX, log_write);

static mp_obj_t log_spawn(mp_obj_t name) {
    return mp_obj_new_int_from_uint(moy_log_spawn(mp_obj_str_get_str(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(log_spawn_obj, log_spawn);

static mp_obj_t log_name(mp_obj_t pid) {
    return mp_obj_new_str_from_cstr(moy_log_name((uint32_t)mp_obj_get_int_truncated(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(log_name_obj, log_name);

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

static mp_obj_t log_stats(void) {
    moy_log_stats_t s;
    moy_log_stats(&s);
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(s.first), mp_obj_new_int_from_uint(s.next),
        mp_obj_new_int_from_uint(s.used), mp_obj_new_int_from_uint(s.cap),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(log_stats_obj, log_stats);

static mp_obj_t log_level_name(mp_obj_t level) {
    return mp_obj_new_str_from_cstr(moy_log_level_name(mp_obj_get_int(level)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(log_level_name_obj, log_level_name);

static mp_obj_t log_level_of(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_log_level_of(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(log_level_of_obj, log_level_of);

static mp_obj_t log_init(mp_obj_t bytes) {
    return mp_obj_new_bool(moy_log_init((size_t)mp_obj_get_int(bytes)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(log_init_obj, log_init);

static mp_obj_t log_pin_clock(mp_obj_t us) {
    moy_log_pin_clock((uint64_t)mp_obj_get_int(us));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(log_pin_clock_obj, log_pin_clock);

static const mp_rom_map_elem_t log_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_log) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&log_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_spawn), MP_ROM_PTR(&log_spawn_obj) },
    { MP_ROM_QSTR(MP_QSTR_name), MP_ROM_PTR(&log_name_obj) },
    { MP_ROM_QSTR(MP_QSTR_current), MP_ROM_PTR(&log_current_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&log_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_next), MP_ROM_PTR(&log_next_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&log_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_level_name), MP_ROM_PTR(&log_level_name_obj) },
    { MP_ROM_QSTR(MP_QSTR_level_of), MP_ROM_PTR(&log_level_of_obj) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&log_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_pin_clock), MP_ROM_PTR(&log_pin_clock_obj) },
    { MP_ROM_QSTR(MP_QSTR_EMERG), MP_ROM_INT(MOY_LOG_EMERG) },
    { MP_ROM_QSTR(MP_QSTR_ALERT), MP_ROM_INT(MOY_LOG_ALERT) },
    { MP_ROM_QSTR(MP_QSTR_CRIT), MP_ROM_INT(MOY_LOG_CRIT) },
    { MP_ROM_QSTR(MP_QSTR_ERR), MP_ROM_INT(MOY_LOG_ERR) },
    { MP_ROM_QSTR(MP_QSTR_WARNING), MP_ROM_INT(MOY_LOG_WARNING) },
    { MP_ROM_QSTR(MP_QSTR_NOTICE), MP_ROM_INT(MOY_LOG_NOTICE) },
    { MP_ROM_QSTR(MP_QSTR_INFO), MP_ROM_INT(MOY_LOG_INFO) },
    { MP_ROM_QSTR(MP_QSTR_DEBUG), MP_ROM_INT(MOY_LOG_DEBUG) },
    { MP_ROM_QSTR(MP_QSTR_KERNEL), MP_ROM_INT(MOY_LOG_KERNEL) },
    { MP_ROM_QSTR(MP_QSTR_SHELL), MP_ROM_INT(MOY_LOG_SHELL) },
    { MP_ROM_QSTR(MP_QSTR_TEXT_MAX), MP_ROM_INT(MOY_LOG_TEXT_MAX) },
};
static MP_DEFINE_CONST_DICT(log_globals, log_globals_table);

const mp_obj_module_t moy_log_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&log_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_log, moy_log_module);
