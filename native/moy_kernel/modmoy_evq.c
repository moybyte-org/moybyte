// moy_evq's MicroPython binding: the service model's kernel half -- the
// event queues (moy_evq.h), the supervisor (moy_super.h) and the queue of
// calls into the VM (moy_vmq.h) -- as the module `moy_evq`, the same names
// runtime/host/moy_evq.py gives CPython.
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

static mp_obj_t ev_hears(mp_obj_t pid, mp_obj_t name) {
    return mp_obj_new_bool(moy_evq_hears(mp_obj_get_int(pid), mp_obj_str_get_str(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_hears_obj, ev_hears);

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
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(took),
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

// The screen slots (moy_screen_slot.h). screen_open(pid, w, h, depth) -> 0
// or an errno negated; screen_close(pid); screen_frame(pid, held, pressed,
// ms, max_ms, words=None) -> the frame call's mask, `words` an array('i');
// screen_pixels(pid) -> a memoryview of the slot's bytes, or None.
static mp_obj_t ev_screen_open(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    return MP_OBJ_NEW_SMALL_INT(moy_screen_slot_open(
        (uint32_t)mp_obj_get_int(args[0]), mp_obj_get_int(args[1]), mp_obj_get_int(args[2]),
        mp_obj_get_int(args[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_screen_open_obj, 4, 4, ev_screen_open);

static mp_obj_t ev_screen_close(mp_obj_t pid) {
    moy_screen_slot_close((uint32_t)mp_obj_get_int(pid));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_screen_close_obj, ev_screen_close);

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

static mp_obj_t ev_ready(void) {
    return mp_obj_new_int_from_uint(moy_evq_ready());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_ready_obj, ev_ready);

static mp_obj_t ev_step(mp_obj_t pid) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_step(mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_step_obj, ev_step);

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

static mp_obj_t ev_woke(void) {
    return mp_obj_new_int_from_uint(moy_evq_woke());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_woke_obj, ev_woke);

// The status items (moy_status.h): status_add(pid, icon, order, compiled)
// -> handle or a code negated; status_show(pid, h, state),
// status_remove(pid, h) -> 0 or a code negated (pid 0: any process's);
// status_sweep(pid) -> n; status_gen(); status_get(h) -> (pid, icon, state,
// order, age, shown, compiled), None for a free slot; status_reset().
static mp_obj_t ev_status_add(size_t n_args, const mp_obj_t *a) {
    size_t n;
    const char *icon = mp_obj_str_get_data(a[1], &n);
    return MP_OBJ_NEW_SMALL_INT(moy_status_add(mp_obj_get_int(a[0]), icon, n,
                                               mp_obj_get_int(a[2]), mp_obj_is_true(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_status_add_obj, 4, 4, ev_status_add);

static mp_obj_t ev_status_show(mp_obj_t pid, mp_obj_t h, mp_obj_t state) {
    return MP_OBJ_NEW_SMALL_INT(moy_status_show(mp_obj_get_int(pid), mp_obj_get_int(h),
                                                mp_obj_get_int(state)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ev_status_show_obj, ev_status_show);

static mp_obj_t ev_status_remove(mp_obj_t pid, mp_obj_t h) {
    return MP_OBJ_NEW_SMALL_INT(moy_status_remove(mp_obj_get_int(pid), mp_obj_get_int(h)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_status_remove_obj, ev_status_remove);

static mp_obj_t ev_status_sweep(mp_obj_t pid) {
    return MP_OBJ_NEW_SMALL_INT(moy_status_sweep(mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_status_sweep_obj, ev_status_sweep);

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

static mp_obj_t ev_status_reset(void) {
    moy_status_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_status_reset_obj, ev_status_reset);

// The notes (moy_notes.h): notes_id(); notes_waiting(); notes_take() ->
// (id, pid, app, summary, body) or None; notes_goal(pid, id, goal);
// notes_award(pid, id); notes_progress(pid, id, value); notes_run();
// notes_end(pid); notes_reset().
static mp_obj_t ev_notes_id(void) {
    return mp_obj_new_int_from_uint(moy_notes_id());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_notes_id_obj, ev_notes_id);

static mp_obj_t ev_notes_waiting(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_notes_waiting());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_notes_waiting_obj, ev_notes_waiting);

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

static mp_obj_t ev_notes_goal(mp_obj_t pid, mp_obj_t id, mp_obj_t goal) {
    size_t n;
    const char *s = mp_obj_str_get_data(id, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_notes_goal(mp_obj_get_int(pid), s, n, mp_obj_get_int(goal)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ev_notes_goal_obj, ev_notes_goal);

static mp_obj_t ev_notes_award(mp_obj_t pid, mp_obj_t id) {
    size_t n;
    const char *s = mp_obj_str_get_data(id, &n);
    return mp_obj_new_int(moy_notes_award(mp_obj_get_int(pid), s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_notes_award_obj, ev_notes_award);

static mp_obj_t ev_notes_progress(mp_obj_t pid, mp_obj_t id, mp_obj_t v) {
    size_t n;
    const char *s = mp_obj_str_get_data(id, &n);
    return mp_obj_new_int(moy_notes_progress(mp_obj_get_int(pid), s, n, mp_obj_get_int(v)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ev_notes_progress_obj, ev_notes_progress);

static mp_obj_t ev_notes_run(void) {
    uint32_t ms = moy_notes_run();
    return MP_OBJ_NEW_SMALL_INT(ms > 0x3FFFFFFFu ? 0x3FFFFFFF : (mp_int_t)ms);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_notes_run_obj, ev_notes_run);

static mp_obj_t ev_notes_end(mp_obj_t pid) {
    moy_notes_end(mp_obj_get_int(pid));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_notes_end_obj, ev_notes_end);

static mp_obj_t ev_notes_reset(void) {
    moy_notes_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_notes_reset_obj, ev_notes_reset);

// The items (moy_items.h): items_hand(pid, h, kind, name, rw) -> bool;
// items_end(pid, h=0); items_reset().
static mp_obj_t ev_items_hand(size_t n, const mp_obj_t *a) {
    (void)n;
    size_t kn, nn;
    const char *k = mp_obj_str_get_data(a[2], &kn);
    const char *nm = mp_obj_str_get_data(a[3], &nn);
    return mp_obj_new_bool(moy_items_hand(mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), k, kn, nm,
                                          nn, mp_obj_is_true(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_items_hand_obj, 5, 5, ev_items_hand);

static mp_obj_t ev_items_end(size_t n, const mp_obj_t *a) {
    moy_items_end(mp_obj_get_int(a[0]), n > 1 ? mp_obj_get_int(a[1]) : 0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ev_items_end_obj, 1, 2, ev_items_end);

static mp_obj_t ev_items_reset(void) {
    moy_items_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_items_reset_obj, ev_items_reset);

static mp_obj_t ev_termed(void) {
    return mp_obj_new_int_from_uint(moy_evq_termed());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_termed_obj, ev_termed);

static mp_obj_t ev_timer_cancel(mp_obj_t pid, mp_obj_t id) {
    return mp_obj_new_bool(moy_evq_timer_cancel((uint32_t)mp_obj_get_int(pid),
                                                (uint32_t)mp_obj_get_int_truncated(id)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ev_timer_cancel_obj, ev_timer_cancel);

static mp_obj_t ev_timers(mp_obj_t pid) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_timers((uint32_t)mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_timers_obj, ev_timers);

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

static mp_obj_t ev_vm_stop(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_vm_stop());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_vm_stop_obj, ev_vm_stop);

static mp_obj_t ev_live(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_live());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_live_obj, ev_live);

static mp_obj_t ev_want(mp_obj_t bits) {
    moy_evq_want((uint32_t)mp_obj_get_int(bits));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_want_obj, ev_want);

static mp_obj_t ev_wanted(mp_obj_t mask) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_wanted((uint32_t)mp_obj_get_int(mask)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_wanted_obj, ev_wanted);

static mp_obj_t ev_wants(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_wants());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ev_wants_obj, ev_wants);

static mp_obj_t ev_listening(mp_obj_t name) {
    return MP_OBJ_NEW_SMALL_INT(moy_evq_listening(mp_obj_str_get_str(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ev_listening_obj, ev_listening);

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
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&ev_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_has), MP_ROM_PTR(&ev_has_obj) },
    { MP_ROM_QSTR(MP_QSTR_app), MP_ROM_PTR(&ev_app_obj) },
    { MP_ROM_QSTR(MP_QSTR_listen), MP_ROM_PTR(&ev_listen_obj) },
    { MP_ROM_QSTR(MP_QSTR_hears), MP_ROM_PTR(&ev_hears_obj) },
    { MP_ROM_QSTR(MP_QSTR_post), MP_ROM_PTR(&ev_post_obj) },
    { MP_ROM_QSTR(MP_QSTR_send), MP_ROM_PTR(&ev_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_route), MP_ROM_PTR(&ev_route_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&ev_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_queued), MP_ROM_PTR(&ev_queued_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_open), MP_ROM_PTR(&ev_screen_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_close), MP_ROM_PTR(&ev_screen_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_frame), MP_ROM_PTR(&ev_screen_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_pixels), MP_ROM_PTR(&ev_screen_pixels_obj) },
    { MP_ROM_QSTR(MP_QSTR_ready), MP_ROM_PTR(&ev_ready_obj) },
    { MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&ev_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_charge), MP_ROM_PTR(&ev_charge_obj) },
    { MP_ROM_QSTR(MP_QSTR_held), MP_ROM_PTR(&ev_held_obj) },
    { MP_ROM_QSTR(MP_QSTR_resume), MP_ROM_PTR(&ev_resume_obj) },
    { MP_ROM_QSTR(MP_QSTR_rungs), MP_ROM_PTR(&ev_rungs_obj) },
    { MP_ROM_QSTR(MP_QSTR_forget), MP_ROM_PTR(&ev_forget_obj) },
    { MP_ROM_QSTR(MP_QSTR_serve), MP_ROM_PTR(&ev_serve_obj) },
    { MP_ROM_QSTR(MP_QSTR_vm), MP_ROM_PTR(&ev_vm_obj) },
    { MP_ROM_QSTR(MP_QSTR_timer), MP_ROM_PTR(&ev_timer_obj) },
    { MP_ROM_QSTR(MP_QSTR_timer_cancel), MP_ROM_PTR(&ev_timer_cancel_obj) },
    { MP_ROM_QSTR(MP_QSTR_alarm), MP_ROM_PTR(&ev_alarm_obj) },
    { MP_ROM_QSTR(MP_QSTR_woke), MP_ROM_PTR(&ev_woke_obj) },
    { MP_ROM_QSTR(MP_QSTR_termed), MP_ROM_PTR(&ev_termed_obj) },
    { MP_ROM_QSTR(MP_QSTR_items_hand), MP_ROM_PTR(&ev_items_hand_obj) },
    { MP_ROM_QSTR(MP_QSTR_items_end), MP_ROM_PTR(&ev_items_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_items_reset), MP_ROM_PTR(&ev_items_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_id), MP_ROM_PTR(&ev_notes_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_waiting), MP_ROM_PTR(&ev_notes_waiting_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_take), MP_ROM_PTR(&ev_notes_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_goal), MP_ROM_PTR(&ev_notes_goal_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_award), MP_ROM_PTR(&ev_notes_award_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_progress), MP_ROM_PTR(&ev_notes_progress_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_run), MP_ROM_PTR(&ev_notes_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_end), MP_ROM_PTR(&ev_notes_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_notes_reset), MP_ROM_PTR(&ev_notes_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_add), MP_ROM_PTR(&ev_status_add_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_show), MP_ROM_PTR(&ev_status_show_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_remove), MP_ROM_PTR(&ev_status_remove_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_sweep), MP_ROM_PTR(&ev_status_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_gen), MP_ROM_PTR(&ev_status_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_get), MP_ROM_PTR(&ev_status_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_status_reset), MP_ROM_PTR(&ev_status_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_timers), MP_ROM_PTR(&ev_timers_obj) },
    { MP_ROM_QSTR(MP_QSTR_timers_run), MP_ROM_PTR(&ev_timers_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_TIMER_ID), MP_ROM_INT(MOY_EVQ_TIMER_ID) },
    { MP_ROM_QSTR(MP_QSTR_next_call), MP_ROM_PTR(&ev_next_call_obj) },
    { MP_ROM_QSTR(MP_QSTR_answer), MP_ROM_PTR(&ev_answer_obj) },
    { MP_ROM_QSTR(MP_QSTR_drop), MP_ROM_PTR(&ev_drop_obj) },
    { MP_ROM_QSTR(MP_QSTR_vm_stop), MP_ROM_PTR(&ev_vm_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_live), MP_ROM_PTR(&ev_live_obj) },
    { MP_ROM_QSTR(MP_QSTR_want), MP_ROM_PTR(&ev_want_obj) },
    { MP_ROM_QSTR(MP_QSTR_wanted), MP_ROM_PTR(&ev_wanted_obj) },
    { MP_ROM_QSTR(MP_QSTR_wants), MP_ROM_PTR(&ev_wants_obj) },
    { MP_ROM_QSTR(MP_QSTR_listening), MP_ROM_PTR(&ev_listening_obj) },
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
    { MP_ROM_QSTR(MP_QSTR_SENDER), MP_ROM_INT(MOY_EVQ_SENDER) },
    { MP_ROM_QSTR(MP_QSTR_BATCH), MP_ROM_INT(MOY_EVQ_BATCH) },
    { MP_ROM_QSTR(MP_QSTR_WANT_MAIL), MP_ROM_INT(MOY_EVQ_WANT_MAIL) },
    { MP_ROM_QSTR(MP_QSTR_WANT_POLL), MP_ROM_INT(MOY_EVQ_WANT_POLL) },
    { MP_ROM_QSTR(MP_QSTR_SYSTEM), MP_ROM_QSTR(MP_QSTR_system) },
};
static MP_DEFINE_CONST_DICT(ev_globals, ev_globals_table);

const mp_obj_module_t moy_evq_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&ev_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_evq, moy_evq_module);
