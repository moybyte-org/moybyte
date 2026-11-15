// moy_evq's MicroPython binding: the service model's kernel half -- the
// event queues (moy_evq.h), the supervisor (moy_super.h) and the queue of
// calls into the VM (moy_vmq.h) -- as the module `moy_evq`, its names those
// face.json lists: the rows here are the hand ones, the rest are generated
// into modmoy_evq_face.c.
//
//   open(pid, app, receives="", vm=False, alarms=False, notify=False)
//                      a process's queue (receives: one name a line;
//                      alarms: MOY_EVQ_ALARMS, notify: MOY_EVQ_NOTIFY)
//   close(pid), has(pid), app(pid)
//   listen(pid, name, on)                    OK, STALE or FULL
//   hears(pid, name)                         whether its queue listens on it
//   post(to, frm, name, args)                one message (args a JSON array
//                      text): OK, STALE, DEAF, DENIED, FULL, NOMEM
//   send(frm, app, name, args) -> (took, denied)   took -STALE: no queue,
//                      -FULL: none took it, a listener full (BACKPRESSURE)
//   route(name, args) -> took                an OS event, from the system
//   take(pid) -> (frm, app, name, args) or None
//   queued(pid), ready(), step(pid) -> held  a step's batch afresh (0: the VM's)
//   charge(pid, cost_us, aborted) -> rung    the supervisor
//   held(pid), resume(pid), rungs(pid) -> (overruns, strikes), forget(pid)
//   serve() -> calls waiting                 the VM's queue: hops run
//   next_call() -> (ticket, pid, name, args) or None
//   answer(ticket, ans)                      ans a JSON text, or None: refused
//   drop(pid)
//   vm(up=None) -> up  the VM up or down for the queue (moy_vmq.h, WITH NO VM),
//                      set when `up` is given
//   vm_stop() -> closed  the VM's queues closed, as its teardown does
//   live()             the queues open
//   timer(pid, ms, every=0) -> (rc, id)   a timer of its queue (TIMERS)
//   alarm(pid, ms) -> (rc, id), woke() -> pid or 0      ALARMS
//   termed() -> pid or 0               THE STOPS ASKED
//   status_add, status_show, status_remove, status_sweep, status_gen,
//   status_get, status_reset           the status items (moy_status.h)
//   notes_id, notes_waiting, notes_take, notes_goal, notes_award,
//   notes_progress, notes_run, notes_end, notes_reset   the notes (moy_notes.h)
//   items_hand, items_end, items_reset the items handed to processes (moy_items.h)
//   timer_cancel(pid, id) -> bool, timers(pid) -> n
//   timers_run() -> ms to the next      the due ones posted
//   want(bits), wanted(mask) -> bits    THE VM'S WAKE: bits wanted, and those
//                      of `mask` taken; wants() what is wanted, left set
//   listening(name)    the queues that are not the VM's listening on `name`
//   reset()            every queue, row and slot (tests)
//   OK .. RANGE, FINE .. SUSPENDED, SLICE_US, ABORT_US, STRIKES, DEPTH, SENDER,
//   BATCH, SYSTEM

#include <string.h>

#include "py/runtime.h"
#include "py/objstr.h"

#include "moy_evq.h"
#include "moy_screen_slot.h"
#include "moy_items.h"
#include "moy_notes.h"
#include "moy_status.h"
#include "moy_super.h"
#include "moy_vmq.h"

#include "modmoy_evq_face.c"

#define ARGS_MAX 4096

static mp_obj_t ev_open(size_t n, const mp_obj_t *a) {
    const char *recv = n > 2 && a[2] != mp_const_none ? mp_obj_str_get_str(a[2]) : NULL;
    bool vm = n > 3 && mp_obj_is_true(a[3]);
    bool alarms = n > 4 && mp_obj_is_true(a[4]);
    bool notify = n > 5 && mp_obj_is_true(a[5]);
    int rc = moy_evq_open(mp_obj_get_int(a[0]), mp_obj_str_get_str(a[1]), recv,
                          (vm ? MOY_EVQ_VM : 0u) | (alarms ? MOY_EVQ_ALARMS : 0u)
                          | (notify ? MOY_EVQ_NOTIFY : 0u));
    return MP_OBJ_NEW_SMALL_INT(rc);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_open_obj, 2, 6, ev_open);

static mp_obj_t ev_send(size_t n, const mp_obj_t *a) {
    (void)n;
    size_t len;
    const char *args = mp_obj_str_get_data(a[3], &len);
    uint32_t took = 0, denied = 0;
    int rc = moy_evq_send(mp_obj_get_int(a[0]), mp_obj_str_get_str(a[1]),
                          mp_obj_str_get_str(a[2]), args, len, &took, &denied);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(rc == MOY_EVQ_OK ? (mp_int_t)took : -rc),
                      MP_OBJ_NEW_SMALL_INT(denied) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_send_obj, 4, 4, ev_send);

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

static mp_obj_t ev_screen_frame(size_t n_args, const mp_obj_t *args) {
    const int32_t *in = NULL;
    int n_in = 0;
    if (n_args > 5 && args[5] != mp_const_none) {
        mp_buffer_info_t b;
        mp_get_buffer_raise(args[5], &b, MP_BUFFER_READ);
        in = (const int32_t *)b.buf;
        n_in = (int)(b.len / sizeof(int32_t));
    }
    return MP_OBJ_NEW_SMALL_INT(moy_screen_slot_frame(
        (uint32_t)mp_obj_get_int(args[0]), (uint32_t)mp_obj_get_int(args[1]),
        (uint32_t)mp_obj_get_int(args[2]), (uint32_t)mp_obj_get_int(args[3]), in, n_in,
        (uint32_t)mp_obj_get_int(args[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_screen_frame_obj, 5, 6, ev_screen_frame);

static mp_obj_t ev_screen_pixels(mp_obj_t pid) {
    int w = 0, h = 0, depth = 0;
    const uint8_t *px = moy_screen_slot_pixels((uint32_t)mp_obj_get_int(pid), &w, &h, &depth);
    if (px == NULL) {
        return mp_const_none;
    }
    return mp_obj_new_memoryview('B', (size_t)w * (size_t)h * (size_t)depth, (void *)px);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_screen_pixels_obj, ev_screen_pixels);

static mp_obj_t ev_charge(mp_obj_t pid, mp_obj_t cost, mp_obj_t aborted) {
    return MP_OBJ_NEW_SMALL_INT(moy_super_charge(mp_obj_get_int(pid), mp_obj_get_int(cost),
                                                 mp_obj_is_true(aborted)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ev_charge_obj, ev_charge);

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

static mp_obj_t ev_timer(size_t n_args, const mp_obj_t *args) {
    uint32_t id = 0;
    int rc = moy_evq_timer((uint32_t)mp_obj_get_int(args[0]), (uint32_t)mp_obj_get_int(args[1]),
                           n_args > 2 ? (uint32_t)mp_obj_get_int(args[2]) : 0u, &id);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(rc), mp_obj_new_int_from_uint(id) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_timer_obj, 2, 3, ev_timer);

static mp_obj_t ev_alarm(mp_obj_t pid, mp_obj_t ms) {
    uint32_t id = 0;
    mp_int_t m = mp_obj_get_int(ms);
    int rc = moy_evq_alarm((uint32_t)mp_obj_get_int(pid), m > 0 ? (uint32_t)m : 0u, &id);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(rc), mp_obj_new_int_from_uint(id) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_alarm_obj, ev_alarm);

static mp_obj_t ev_status_gen(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_status_gen() & 0x3FFFFFFFu);    // compared, never counted
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_status_gen_obj, ev_status_gen);

static mp_obj_t ev_status_get(mp_obj_t h) {
    moy_status_item_t it;
    if (!moy_status_get(mp_obj_get_int(h), &it) || it.pid == 0u) {
        return mp_const_none;
    }
    mp_obj_t t[7] = {
        mp_obj_new_int_from_uint(it.pid), mp_obj_new_str(it.icon, strlen(it.icon)),
        MP_OBJ_NEW_SMALL_INT(it.state), mp_obj_new_int(it.order),
        mp_obj_new_int_from_uint(it.age), mp_obj_new_bool(it.shown),
        mp_obj_new_bool(it.compiled),
    };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_status_get_obj, ev_status_get);

static mp_obj_t ev_notes_take(void) {
    moy_notes_note_t n;
    if (!moy_notes_take(&n)) {
        return mp_const_none;
    }
    mp_obj_t t[5] = {
        mp_obj_new_int_from_uint(n.id), mp_obj_new_int_from_uint(n.pid),
        mp_obj_new_str(n.app, strlen(n.app)), mp_obj_new_str(n.summary, strlen(n.summary)),
        mp_obj_new_str(n.body, strlen(n.body)),
    };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_notes_take_obj, ev_notes_take);

static mp_obj_t ev_notes_run(void) {
    uint32_t ms = moy_notes_run();
    return MP_OBJ_NEW_SMALL_INT(ms > 0x3FFFFFFFu ? 0x3FFFFFFF : (mp_int_t)ms);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_notes_run_obj, ev_notes_run);

static mp_obj_t ev_timers_run(void) {
    uint32_t next = moy_evq_timers_run();
    return MP_OBJ_NEW_SMALL_INT(next > 0x3FFFFFFFu ? 0x3FFFFFFF : (mp_int_t)next);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_timers_run_obj, ev_timers_run);

static mp_obj_t ev_vm(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0 && args[0] != mp_const_none) {
        moy_vmq_vm(mp_obj_is_true(args[0]));
    }
    return mp_obj_new_bool(moy_vmq_vm_up());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_vm_obj, 0, 1, ev_vm);

static mp_obj_t ev_reset(void) {
    moy_evq_reset();
    moy_super_reset();
    moy_vmq_reset();
    moy_vmq_vm(true);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_reset_obj, ev_reset);

static const mp_rom_map_elem_t ev_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_evq) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&ev_open_obj) },
    MOY_FACE_MOY_EVQ_ROWS
    { MP_ROM_QSTR(MP_QSTR_send), MP_ROM_PTR(&ev_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&ev_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_frame), MP_ROM_PTR(&ev_screen_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_pixels), MP_ROM_PTR(&ev_screen_pixels_obj) },
    { MP_ROM_QSTR(MP_QSTR_charge), MP_ROM_PTR(&ev_charge_obj) },
    { MP_ROM_QSTR(MP_QSTR_resume), MP_ROM_PTR(&ev_resume_obj) },
    { MP_ROM_QSTR(MP_QSTR_rungs), MP_ROM_PTR(&ev_rungs_obj) },
    { MP_ROM_QSTR(MP_QSTR_vm), MP_ROM_PTR(&ev_vm_obj) },
    { MP_ROM_QSTR(MP_QSTR_timer), MP_ROM_PTR(&ev_timer_obj) },
    { MP_ROM_QSTR(MP_QSTR_alarm), MP_ROM_PTR(&ev_alarm_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_take), MP_ROM_PTR(&ev_notes_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_run), MP_ROM_PTR(&ev_notes_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_gen), MP_ROM_PTR(&ev_status_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_get), MP_ROM_PTR(&ev_status_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_timers_run), MP_ROM_PTR(&ev_timers_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_next_call), MP_ROM_PTR(&ev_next_call_obj) },
    { MP_ROM_QSTR(MP_QSTR_answer), MP_ROM_PTR(&ev_answer_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&ev_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_TIMER_ID), MP_ROM_INT(MOY_EVQ_TIMER_ID) },
    { MP_ROM_QSTR(MP_QSTR_SYSTEM), MP_ROM_QSTR(MP_QSTR_system) },
};
static MP_DEFINE_CONST_DICT(ev_globals, ev_globals_table);

const mp_obj_module_t moy_evq_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&ev_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_evq, moy_evq_module);
