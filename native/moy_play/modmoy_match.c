// moy_play.Match: the kernel's radio link and its lockstep session as a VM
// sees them (moy_match.h). runtime/moy_play.py's Match is the same class on
// CPython, method for method and attribute for attribute; there it takes an
// io (the host's tests run two consoles in one process), here the instance is
// the kernel's one over native/moy_net's ring:
//
//   Match(io=None, name="", board="", entropy=0)   OSError where no link
//   start(mac), stop(), announce(cart, state), set_config(cfg|None),
//   offer(cart, now[, seed]) -> bool, poll(now), drain(now, budget), end(),
//   broadcast(b), send_msg(b) -> 0|-1|-2, take_msg() -> bytes|None,
//   action() -> title|None, dispatch(mac, msg, now), peers(),
//   candidate(cart, now), begin(index, seed, session, cfg), close(),
//   pending(now), due(now), advance(held[, now]) -> 1|0|-1, resend(),
//   packet(data[, now]) -> bool, tps(now), expand(f16)
//   attributes: the link's and the session's fields (moy_match.h)
//   seed_of(seed, frame)   the frame's seed (module level)

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_match.h"

typedef struct {
    mp_obj_base_t base;
    moy_match_t *m;
} match_obj_t;

static moy_match_t *M(mp_obj_t self) {
    return ((match_obj_t *)MP_OBJ_TO_PTR(self))->m;
}

static uint32_t now_of(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int_truncated(o);
}

static mp_obj_t str_of(const char *s) {
    return mp_obj_new_str(s, strlen(s));
}

static mp_obj_t match_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                               const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 4, false);
    if (n_args > 0 && args[0] != mp_const_none) {
        mp_raise_ValueError(MP_ERROR_TEXT("Match: a board's link is the kernel's"));
    }
    const char *name = n_args > 1 ? mp_obj_str_get_str(args[1]) : "";
    const char *board = n_args > 2 ? mp_obj_str_get_str(args[2]) : "";
    uint32_t ent = n_args > 3 ? (uint32_t)mp_obj_get_int_truncated(args[3]) : 0u;
    moy_match_t *m = moy_match_kernel_make(name, board, ent);
    if (m == NULL) {
        mp_raise_OSError(19);           // ENODEV: no link in this image
    }
    match_obj_t *o = mp_obj_malloc(match_obj_t, type);
    o->m = m;
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t mt_start(mp_obj_t self, mp_obj_t mac) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(mac, &b, MP_BUFFER_READ);
    uint8_t m6[6] = {0};
    memcpy(m6, b.buf, b.len < 6 ? b.len : 6);
    moy_match_start(M(self), m6);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_start_obj, mt_start);

static mp_obj_t mt_stop(mp_obj_t self) {
    moy_match_stop(M(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_stop_obj, mt_stop);

static mp_obj_t mt_announce(mp_obj_t self, mp_obj_t cart, mp_obj_t state) {
    moy_match_announce(M(self), mp_obj_str_get_str(cart), mp_obj_get_int(state));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mt_announce_obj, mt_announce);

static mp_obj_t mt_set_config(mp_obj_t self, mp_obj_t cfg) {
    moy_match_set_config(M(self), cfg == mp_const_none ? NULL : mp_obj_str_get_str(cfg));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_set_config_obj, mt_set_config);

static mp_obj_t mt_offer(size_t n, const mp_obj_t *a) {
    bool has = n > 3 && a[3] != mp_const_none;
    return mp_obj_new_bool(moy_match_offer_seeded(
        M(a[0]), mp_obj_str_get_str(a[1]), now_of(a[2]), has,
        has ? (uint32_t)mp_obj_get_int_truncated(a[3]) : 0u));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mt_offer_obj, 3, 4, mt_offer);

static mp_obj_t mt_poll(mp_obj_t self, mp_obj_t now) {
    return MP_OBJ_NEW_SMALL_INT(moy_match_poll(M(self), now_of(now)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_poll_obj, mt_poll);

static mp_obj_t mt_drain(mp_obj_t self, mp_obj_t now, mp_obj_t budget) {
    return MP_OBJ_NEW_SMALL_INT(moy_match_drain_input(M(self), now_of(now),
                                                      mp_obj_get_int(budget)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(mt_drain_obj, mt_drain);

static mp_obj_t mt_end(mp_obj_t self) {
    moy_match_end(M(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_end_obj, mt_end);

static mp_obj_t mt_broadcast(mp_obj_t self, mp_obj_t data) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(data, &b, MP_BUFFER_READ);
    return mp_obj_new_bool(moy_match_broadcast(M(self), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_broadcast_obj, mt_broadcast);

static mp_obj_t mt_send_msg(mp_obj_t self, mp_obj_t data) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(data, &b, MP_BUFFER_READ);
    return MP_OBJ_NEW_SMALL_INT(moy_match_send_msg(M(self), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_send_msg_obj, mt_send_msg);

static mp_obj_t mt_take_msg(mp_obj_t self) {
    uint8_t buf[MOY_MATCH_FRAME];
    int n = moy_match_take_msg(M(self), buf, sizeof(buf));
    return n < 0 ? mp_const_none : mp_obj_new_bytes(buf, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_take_msg_obj, mt_take_msg);

static mp_obj_t mt_action(mp_obj_t self) {
    char cart[MOY_MATCH_CART + 1];
    return moy_match_action(M(self), cart, sizeof(cart)) ? str_of(cart) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_action_obj, mt_action);

static mp_obj_t mt_dispatch(size_t n, const mp_obj_t *a) {
    (void)n;
    mp_buffer_info_t mac, msg;
    mp_get_buffer_raise(a[1], &mac, MP_BUFFER_READ);
    mp_get_buffer_raise(a[2], &msg, MP_BUFFER_READ);
    uint8_t m6[6] = {0};
    memcpy(m6, mac.buf, mac.len < 6 ? mac.len : 6);
    moy_match_dispatch(M(a[0]), m6, msg.buf, msg.len, now_of(a[3]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mt_dispatch_obj, 4, 4, mt_dispatch);

static mp_obj_t peer_tuple(const moy_match_peer_t *p) {
    mp_obj_t t[6] = {
        mp_obj_new_bytes(p->mac, 6), str_of(p->name), str_of(p->board), str_of(p->cart),
        MP_OBJ_NEW_SMALL_INT(p->state), mp_obj_new_int_from_uint(p->seen),
    };
    return mp_obj_new_tuple(6, t);
}

static mp_obj_t mt_peers(mp_obj_t self) {
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0;; i++) {
        const moy_match_peer_t *p = moy_match_peer_at(M(self), i);
        if (p == NULL) {
            break;
        }
        mp_obj_list_append(out, peer_tuple(p));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_peers_obj, mt_peers);

static mp_obj_t mt_candidate(mp_obj_t self, mp_obj_t cart, mp_obj_t now) {
    const moy_match_peer_t *p = moy_match_candidate(M(self), mp_obj_str_get_str(cart),
                                                    now_of(now));
    return p == NULL ? mp_const_none : peer_tuple(p);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mt_candidate_obj, mt_candidate);

static mp_obj_t mt_begin(size_t n, const mp_obj_t *a) {
    (void)n;
    return mp_obj_new_bool(moy_lockstep_begin(
        M(a[0]), mp_obj_get_int(a[1]), (uint32_t)mp_obj_get_int_truncated(a[2]),
        (uint8_t)mp_obj_get_int(a[3]),
        a[4] == mp_const_none ? NULL : mp_obj_str_get_str(a[4])) == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mt_begin_obj, 5, 5, mt_begin);

static mp_obj_t mt_close(mp_obj_t self) {
    moy_lockstep_close(M(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_close_obj, mt_close);

static mp_obj_t mt_pending(mp_obj_t self, mp_obj_t now) {
    return mp_obj_new_bool(moy_lockstep_pending(M(self), now_of(now)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_pending_obj, mt_pending);

static mp_obj_t mt_due(mp_obj_t self, mp_obj_t now) {
    return mp_obj_new_bool(moy_lockstep_due(M(self), now_of(now)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_due_obj, mt_due);

static mp_obj_t mt_advance(size_t n, const mp_obj_t *a) {
    bool timed = n > 2 && a[2] != mp_const_none;
    return MP_OBJ_NEW_SMALL_INT(moy_lockstep_advance(
        M(a[0]), (uint8_t)mp_obj_get_int(a[1]), timed ? now_of(a[2]) : 0u, timed));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mt_advance_obj, 2, 3, mt_advance);

static mp_obj_t mt_resend(mp_obj_t self) {
    moy_lockstep_resend(M(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mt_resend_obj, mt_resend);

static mp_obj_t mt_packet(size_t n, const mp_obj_t *a) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[1], &b, MP_BUFFER_READ);
    bool timed = n > 2 && a[2] != mp_const_none;
    return mp_obj_new_bool(moy_lockstep_packet(M(a[0]), b.buf, b.len,
                                               timed ? now_of(a[2]) : 0u, timed));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mt_packet_obj, 2, 3, mt_packet);

static mp_obj_t mt_tps(mp_obj_t self, mp_obj_t now) {
    return mp_obj_new_int_from_uint(moy_lockstep_tps(M(self), now_of(now)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_tps_obj, mt_tps);

static mp_obj_t mt_expand(mp_obj_t self, mp_obj_t f16) {
    return mp_obj_new_int(moy_lockstep_expand(M(self), (uint32_t)mp_obj_get_int(f16)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mt_expand_obj, mt_expand);

static void match_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    if (dest[0] != MP_OBJ_NULL) {
        return;                         // read-only on a board
    }
    const moy_match_t *m = M(self_in);
    const moy_lockstep_t *s = &m->s;
    switch (attr) {
        case MP_QSTR_active: dest[0] = mp_obj_new_bool(m->active); return;
        case MP_QSTR_state: dest[0] = MP_OBJ_NEW_SMALL_INT(m->state); return;
        case MP_QSTR_rx: dest[0] = mp_obj_new_int_from_uint(m->rx); return;
        case MP_QSTR_tx: dest[0] = mp_obj_new_int_from_uint(m->tx); return;
        case MP_QSTR_drops: dest[0] = mp_obj_new_int_from_uint(m->drops); return;
        case MP_QSTR_recovers: dest[0] = mp_obj_new_int_from_uint(m->recovers); return;
        case MP_QSTR_session_id: dest[0] = MP_OBJ_NEW_SMALL_INT(m->session_id); return;
        case MP_QSTR_start_len: dest[0] = MP_OBJ_NEW_SMALL_INT(m->start_len); return;
        case MP_QSTR_cart: dest[0] = str_of(m->cart); return;
        case MP_QSTR_error: dest[0] = str_of(m->error); return;
        case MP_QSTR_mac: dest[0] = mp_obj_new_bytes(m->mac, 6); return;
        case MP_QSTR_live: dest[0] = mp_obj_new_bool(s->live); return;
        case MP_QSTR_index: dest[0] = MP_OBJ_NEW_SMALL_INT(s->index); return;
        case MP_QSTR_seed: dest[0] = mp_obj_new_int_from_uint(s->seed); return;
        case MP_QSTR_session: dest[0] = MP_OBJ_NEW_SMALL_INT(s->session); return;
        case MP_QSTR_tick_ms: dest[0] = MP_OBJ_NEW_SMALL_INT(s->tick_ms); return;
        case MP_QSTR_frame: dest[0] = mp_obj_new_int(s->frame); return;
        case MP_QSTR_delay: dest[0] = MP_OBJ_NEW_SMALL_INT(s->delay); return;
        case MP_QSTR_redundancy: dest[0] = MP_OBJ_NEW_SMALL_INT(s->redundancy); return;
        case MP_QSTR_stalls: dest[0] = mp_obj_new_int_from_uint(s->stalls); return;
        case MP_QSTR_stall_ticks: dest[0] = mp_obj_new_int_from_uint(s->stall_ticks); return;
        case MP_QSTR_packets_in: dest[0] = mp_obj_new_int_from_uint(s->packets_in); return;
        case MP_QSTR_packets_out: dest[0] = mp_obj_new_int_from_uint(s->packets_out); return;
        case MP_QSTR_waiting: dest[0] = mp_obj_new_bool(s->waiting); return;
        case MP_QSTR_dead: dest[0] = mp_obj_new_bool(s->dead); return;
        case MP_QSTR_last_peer_frame: dest[0] = mp_obj_new_int(s->last_peer_frame); return;
        case MP_QSTR_peer_need: dest[0] = mp_obj_new_int(s->peer_need); return;
        case MP_QSTR_frame_seed: dest[0] = mp_obj_new_int_from_uint(s->frame_seed); return;
        case MP_QSTR_config:
            dest[0] = s->config[0] ? str_of(s->config) : mp_const_none;
            return;
        case MP_QSTR_m_ema:
            dest[0] = s->has_ema ? mp_obj_new_float((mp_float_t)s->m_ema) : mp_const_none;
            return;
        case MP_QSTR_held: {
            mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(s->held[0]), MP_OBJ_NEW_SMALL_INT(s->held[1])};
            dest[0] = mp_obj_new_tuple(2, t);
            return;
        }
        default:
            dest[1] = MP_OBJ_SENTINEL;  // the locals dict's methods
            return;
    }
}

static const mp_rom_map_elem_t match_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&mt_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&mt_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_announce), MP_ROM_PTR(&mt_announce_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_config), MP_ROM_PTR(&mt_set_config_obj) },
    { MP_ROM_QSTR(MP_QSTR_offer), MP_ROM_PTR(&mt_offer_obj) },
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&mt_poll_obj) },
    { MP_ROM_QSTR(MP_QSTR_drain), MP_ROM_PTR(&mt_drain_obj) },
    { MP_ROM_QSTR(MP_QSTR_end), MP_ROM_PTR(&mt_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_broadcast), MP_ROM_PTR(&mt_broadcast_obj) },
    { MP_ROM_QSTR(MP_QSTR_send_msg), MP_ROM_PTR(&mt_send_msg_obj) },
    { MP_ROM_QSTR(MP_QSTR_take_msg), MP_ROM_PTR(&mt_take_msg_obj) },
    { MP_ROM_QSTR(MP_QSTR_action), MP_ROM_PTR(&mt_action_obj) },
    { MP_ROM_QSTR(MP_QSTR_dispatch), MP_ROM_PTR(&mt_dispatch_obj) },
    { MP_ROM_QSTR(MP_QSTR_peers), MP_ROM_PTR(&mt_peers_obj) },
    { MP_ROM_QSTR(MP_QSTR_candidate), MP_ROM_PTR(&mt_candidate_obj) },
    { MP_ROM_QSTR(MP_QSTR_begin), MP_ROM_PTR(&mt_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&mt_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_pending), MP_ROM_PTR(&mt_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_due), MP_ROM_PTR(&mt_due_obj) },
    { MP_ROM_QSTR(MP_QSTR_advance), MP_ROM_PTR(&mt_advance_obj) },
    { MP_ROM_QSTR(MP_QSTR_resend), MP_ROM_PTR(&mt_resend_obj) },
    { MP_ROM_QSTR(MP_QSTR_packet), MP_ROM_PTR(&mt_packet_obj) },
    { MP_ROM_QSTR(MP_QSTR_tps), MP_ROM_PTR(&mt_tps_obj) },
    { MP_ROM_QSTR(MP_QSTR_expand), MP_ROM_PTR(&mt_expand_obj) },
};
static MP_DEFINE_CONST_DICT(match_locals, match_locals_table);

MP_DEFINE_CONST_OBJ_TYPE(
    moy_play_match_type, MP_QSTR_Match, MP_TYPE_FLAG_NONE,
    make_new, match_make_new,
    attr, match_attr,
    locals_dict, &match_locals);

static mp_obj_t mod_seed_of(mp_obj_t seed, mp_obj_t frame) {
    return mp_obj_new_int_from_uint(moy_lockstep_seed_of(
        (uint32_t)mp_obj_get_int_truncated(seed), (int32_t)mp_obj_get_int(frame)));
}
MP_DEFINE_CONST_FUN_OBJ_2(moy_play_seed_of_obj, mod_seed_of);
