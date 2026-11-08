// moy_evq's MicroPython binding: the service model's kernel half -- the
// event queues (moy_evq.h), the supervisor (moy_super.h) and the queue of
// calls into the VM (moy_vmq.h) -- as the module `moy_evq`, the same names
// runtime/host/moy_evq.py gives CPython.
//
//   open(pid, app, receives="", vm=False)   a process's queue (receives: one
//                      name a line)
//   close(pid), has(pid), app(pid)
//   listen(pid, name, on)                    OK, STALE or FULL
//   post(to, frm, name, args)                one message (args a JSON array
//                      text): OK, STALE, DEAF, DENIED, NOMEM
//   send(frm, app, name, args) -> (took, denied)   -1 took: no queue
//   route(name, args) -> took                an OS event, from the system
//   take(pid) -> (frm, app, name, args) or None
//   queued(pid), ready()
//   charge(pid, cost_us, aborted) -> rung    the supervisor
//   held(pid), resume(pid), rungs(pid) -> (overruns, strikes), forget(pid)
//   serve() -> calls waiting                 the VM's queue: hops run
//   next_call() -> (ticket, pid, name, args) or None
//   answer(ticket, ans)                      ans a JSON text, or None: refused
//   drop(pid)
//   reset()            every queue, row and slot (tests)
//   OK .. RANGE, FINE .. SUSPENDED, SLICE_US, ABORT_US, STRIKES, SYSTEM

#include <string.h>

#include "py/runtime.h"
#include "py/objstr.h"

#include "moy_evq.h"
#include "moy_super.h"
#include "moy_vmq.h"

#define ARGS_MAX 4096

static mp_obj_t ev_open(size_t n, const mp_obj_t *a) {
    const char *recv = n > 2 && a[2] != mp_const_none ? mp_obj_str_get_str(a[2]) : NULL;
    bool vm = n > 3 && mp_obj_is_true(a[3]);
    int rc = moy_evq_open(mp_obj_get_int(a[0]), mp_obj_str_get_str(a[1]), recv,
                          vm ? MOY_EVQ_VM : 0u);
    return MP_OBJ_NEW_SMALL_INT(rc);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_open_obj, 2, 4, ev_open);

static mp_obj_t ev_close(mp_obj_t pid) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_close(mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_close_obj, ev_close);

static mp_obj_t ev_has(mp_obj_t pid) {
    return mp_obj_new_bool(moy_evq_has(mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_has_obj, ev_has);

static mp_obj_t ev_app(mp_obj_t pid) {
    const char *app = moy_evq_app(mp_obj_get_int(pid));
    return app != NULL ? mp_obj_new_str(app, strlen(app)) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_app_obj, ev_app);

static mp_obj_t ev_listen(mp_obj_t pid, mp_obj_t name, mp_obj_t on) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_listen(mp_obj_get_int(pid), mp_obj_str_get_str(name),
                                               mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ev_listen_obj, ev_listen);

static mp_obj_t ev_post(size_t n, const mp_obj_t *a) {
    (void)n;
    size_t len;
    const char *args = mp_obj_str_get_data(a[3], &len);
    return MP_OBJ_NEW_SMALL_INT(moy_evq_post(mp_obj_get_int(a[0]), mp_obj_get_int(a[1]),
                                             mp_obj_str_get_str(a[2]), args, len));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_post_obj, 4, 4, ev_post);

static mp_obj_t ev_send(size_t n, const mp_obj_t *a) {
    (void)n;
    size_t len;
    const char *args = mp_obj_str_get_data(a[3], &len);
    uint32_t denied = 0;
    int took = moy_evq_send(mp_obj_get_int(a[0]), mp_obj_str_get_str(a[1]),
                            mp_obj_str_get_str(a[2]), args, len, &denied);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(took < 0 ? -1 : took),
                      MP_OBJ_NEW_SMALL_INT(denied) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_send_obj, 4, 4, ev_send);

static mp_obj_t ev_route(mp_obj_t name, mp_obj_t args) {
    size_t len;
    const char *a = mp_obj_str_get_data(args, &len);
    return MP_OBJ_NEW_SMALL_INT(moy_evq_route(mp_obj_str_get_str(name), a, len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_route_obj, ev_route);

static mp_obj_t ev_take(mp_obj_t pid_in) {
    uint32_t pid = mp_obj_get_int(pid_in);
    moy_evq_ev_t ev;
    char small[256];
    int rc = moy_evq_take(pid, &ev, small, sizeof small);
    char *buf = small;
    if (rc == MOY_EVQ_RANGE && ev.n < ARGS_MAX) {
        buf = m_new(char, ev.n + 1u);
        rc = moy_evq_take(pid, &ev, buf, ev.n + 1u);
    }
    if (rc != MOY_EVQ_OK) {
        if (buf != small) {
            m_del(char, buf, ev.n + 1u);
        }
        return mp_const_none;
    }
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(ev.from), mp_obj_new_str(ev.app, strlen(ev.app)),
        mp_obj_new_str(ev.name, strlen(ev.name)), mp_obj_new_str(buf, ev.n),
    };
    if (buf != small) {
        m_del(char, buf, ev.n + 1u);
    }
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_take_obj, ev_take);

static mp_obj_t ev_queued(mp_obj_t pid) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_queued(mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_queued_obj, ev_queued);

static mp_obj_t ev_ready(void) {
    return mp_obj_new_int_from_uint(moy_evq_ready());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_ready_obj, ev_ready);

static mp_obj_t ev_charge(mp_obj_t pid, mp_obj_t cost, mp_obj_t aborted) {
    return MP_OBJ_NEW_SMALL_INT(moy_super_charge(mp_obj_get_int(pid), mp_obj_get_int(cost),
                                                 mp_obj_is_true(aborted)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ev_charge_obj, ev_charge);

static mp_obj_t ev_held(mp_obj_t pid) {
    return mp_obj_new_bool(moy_super_held(mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_held_obj, ev_held);

static mp_obj_t ev_resume(mp_obj_t pid) {
    uint32_t p = mp_obj_get_int(pid);
    bool was = moy_super_resume(p);
    moy_evq_kick(p);
    return mp_obj_new_bool(was);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_resume_obj, ev_resume);

static mp_obj_t ev_rungs(mp_obj_t pid) {
    uint32_t o, s;
    moy_super_info(mp_obj_get_int(pid), &o, &s);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(o), MP_OBJ_NEW_SMALL_INT(s) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_rungs_obj, ev_rungs);

static mp_obj_t ev_forget(mp_obj_t pid) {
    moy_super_forget(mp_obj_get_int(pid));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_forget_obj, ev_forget);

static mp_obj_t ev_serve(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_vmq_serve());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_serve_obj, ev_serve);

static mp_obj_t ev_next_call(void) {
    uint32_t ticket, pid;
    const char *name, *args;
    size_t n;
    if (moy_vmq_next(&ticket, &pid, &name, &args, &n) != MOY_VMQ_OK) {
        return mp_const_none;
    }
    mp_obj_t t[4] = { mp_obj_new_int_from_uint(ticket), mp_obj_new_int_from_uint(pid),
                      mp_obj_new_str(name, strlen(name)), mp_obj_new_str(args, n) };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_next_call_obj, ev_next_call);

static mp_obj_t ev_answer(mp_obj_t ticket, mp_obj_t ans) {
    size_t n = 0;
    const char *a = ans != mp_const_none ? mp_obj_str_get_data(ans, &n) : NULL;
    return MP_OBJ_NEW_SMALL_INT(moy_vmq_answer(mp_obj_get_int_truncated(ticket), a, n));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_answer_obj, ev_answer);

static mp_obj_t ev_drop(mp_obj_t pid) {
    moy_vmq_drop(mp_obj_get_int(pid));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_drop_obj, ev_drop);

static mp_obj_t ev_reset(void) {
    moy_evq_reset();
    moy_super_reset();
    moy_vmq_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_reset_obj, ev_reset);

static const mp_rom_map_elem_t ev_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_evq) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&ev_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&ev_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_has), MP_ROM_PTR(&ev_has_obj) },
    { MP_ROM_QSTR(MP_QSTR_app), MP_ROM_PTR(&ev_app_obj) },
    { MP_ROM_QSTR(MP_QSTR_listen), MP_ROM_PTR(&ev_listen_obj) },
    { MP_ROM_QSTR(MP_QSTR_post), MP_ROM_PTR(&ev_post_obj) },
    { MP_ROM_QSTR(MP_QSTR_send), MP_ROM_PTR(&ev_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_route), MP_ROM_PTR(&ev_route_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&ev_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_queued), MP_ROM_PTR(&ev_queued_obj) },
    { MP_ROM_QSTR(MP_QSTR_ready), MP_ROM_PTR(&ev_ready_obj) },
    { MP_ROM_QSTR(MP_QSTR_charge), MP_ROM_PTR(&ev_charge_obj) },
    { MP_ROM_QSTR(MP_QSTR_held), MP_ROM_PTR(&ev_held_obj) },
    { MP_ROM_QSTR(MP_QSTR_resume), MP_ROM_PTR(&ev_resume_obj) },
    { MP_ROM_QSTR(MP_QSTR_rungs), MP_ROM_PTR(&ev_rungs_obj) },
    { MP_ROM_QSTR(MP_QSTR_forget), MP_ROM_PTR(&ev_forget_obj) },
    { MP_ROM_QSTR(MP_QSTR_serve), MP_ROM_PTR(&ev_serve_obj) },
    { MP_ROM_QSTR(MP_QSTR_next_call), MP_ROM_PTR(&ev_next_call_obj) },
    { MP_ROM_QSTR(MP_QSTR_answer), MP_ROM_PTR(&ev_answer_obj) },
    { MP_ROM_QSTR(MP_QSTR_drop), MP_ROM_PTR(&ev_drop_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&ev_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_OK), MP_ROM_INT(MOY_EVQ_OK) },
    { MP_ROM_QSTR(MP_QSTR_STALE), MP_ROM_INT(MOY_EVQ_STALE) },
    { MP_ROM_QSTR(MP_QSTR_FULL), MP_ROM_INT(MOY_EVQ_FULL) },
    { MP_ROM_QSTR(MP_QSTR_NOMEM), MP_ROM_INT(MOY_EVQ_NOMEM) },
    { MP_ROM_QSTR(MP_QSTR_DENIED), MP_ROM_INT(MOY_EVQ_DENIED) },
    { MP_ROM_QSTR(MP_QSTR_DEAF), MP_ROM_INT(MOY_EVQ_DEAF) },
    { MP_ROM_QSTR(MP_QSTR_EMPTY), MP_ROM_INT(MOY_EVQ_EMPTY) },
    { MP_ROM_QSTR(MP_QSTR_RANGE), MP_ROM_INT(MOY_EVQ_RANGE) },
    { MP_ROM_QSTR(MP_QSTR_FINE), MP_ROM_INT(MOY_SUPER_FINE) },
    { MP_ROM_QSTR(MP_QSTR_WARNED), MP_ROM_INT(MOY_SUPER_WARNED) },
    { MP_ROM_QSTR(MP_QSTR_STRUCK), MP_ROM_INT(MOY_SUPER_STRUCK) },
    { MP_ROM_QSTR(MP_QSTR_SUSPENDED), MP_ROM_INT(MOY_SUPER_SUSPENDED) },
    { MP_ROM_QSTR(MP_QSTR_SLICE_US), MP_ROM_INT(MOY_SUPER_SLICE_US) },
    { MP_ROM_QSTR(MP_QSTR_ABORT_US), MP_ROM_INT(MOY_SUPER_ABORT_US) },
    { MP_ROM_QSTR(MP_QSTR_STRIKES), MP_ROM_INT(MOY_SUPER_STRIKES) },
    { MP_ROM_QSTR(MP_QSTR_DEPTH), MP_ROM_INT(MOY_EVQ_DEPTH) },
    { MP_ROM_QSTR(MP_QSTR_SYSTEM), MP_ROM_QSTR(MP_QSTR_system) },
};
static MP_DEFINE_CONST_DICT(ev_globals, ev_globals_table);

const mp_obj_module_t moy_evq_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&ev_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_evq, moy_evq_module);
