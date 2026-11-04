// moy_lora's MicroPython binding: the `lora` device API's driver (moy_lora.h)
// as the module `moy_lora`, the names runtime/host/moy_lora.py gives CPython.
// A refusal is a negative errno (Linux's numbers), which the Python face
// (runtime/system/dev_lora.py) raises.
//
//   open(owner) close(owner) sweep(owner) owner()
//   config(owner, freq, sf, bw, cr, power, sync, preamble)   listen(owner, on)
//   send(owner, buf)   recv(owner, buf, meta) -> n   (meta: array('i', 3) <- rssi, snr x4, port)
//   join(owner, dev_eui, join_eui, app_key, dr)   uplink(owner, port, buf, confirmed, dr)
//   joined() -> (joined, dev_addr, fcnt_up)   mode()   step(now_ms) -> due   source()
//   present()   gate_stats() -> (yields, us, xfers, wait_us, wait_max_us) | None:
//               the feeder's yields and time aside, the radio's commands and their
//               wait for a band gap (the longest since the last call)
//   sim(on)   sim_sent() -> (bytes, freq, sf, bw, sync, iq) | None
//   sim_air(buf, freq, sf, rssi, snr) -> bool   reset()

#include "py/runtime.h"

#include "moy_lora.h"

#if defined(__has_include)
#if __has_include("moy_flush.h") && __has_include("esp_heap_caps.h")
#define LORA_GATE 1
#include "moy_flush.h"
#endif
#endif

static uint32_t u32(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int(o);
}

static mp_obj_t rc(int r) {
    return MP_OBJ_NEW_SMALL_INT(r);
}

static mp_obj_t lora_open(mp_obj_t owner) {
    return rc(moy_lora_open(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lora_open_obj, lora_open);

static mp_obj_t lora_close(mp_obj_t owner) {
    return rc(moy_lora_close(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lora_close_obj, lora_close);

static mp_obj_t lora_sweep(mp_obj_t owner) {
    return rc((int)moy_lora_sweep(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lora_sweep_obj, lora_sweep);

static mp_obj_t lora_owner(void) {
    return mp_obj_new_int_from_uint(moy_lora_owner());
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_owner_obj, lora_owner);

static mp_obj_t lora_config(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_lora_cfg_t c = {
        .freq = u32(a[1]), .sf = (uint8_t)u32(a[2]), .bw = u32(a[3]), .cr = (uint8_t)u32(a[4]),
        .power = (int8_t)mp_obj_get_int(a[5]), .sync = (uint8_t)u32(a[6]),
        .preamble = (uint16_t)u32(a[7]),
    };
    return rc(moy_lora_config(u32(a[0]), &c));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lora_config_obj, 8, 8, lora_config);

static mp_obj_t lora_listen(mp_obj_t owner, mp_obj_t on) {
    return rc(moy_lora_listen(u32(owner), mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(lora_listen_obj, lora_listen);

static mp_obj_t lora_send(mp_obj_t owner, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return rc(moy_lora_send(u32(owner), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(lora_send_obj, lora_send);

static mp_obj_t lora_recv(mp_obj_t owner, mp_obj_t buf, mp_obj_t meta) {
    mp_buffer_info_t b, m;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    mp_get_buffer_raise(meta, &m, MP_BUFFER_WRITE);
    if (m.len < 3 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("meta"));
    }
    int16_t rssi = 0;
    int8_t snr = 0;
    uint8_t port = 0;
    int n = moy_lora_recv(u32(owner), b.buf, b.len, &rssi, &snr, &port);
    if (n > 0) {
        int32_t *o = m.buf;
        o[0] = rssi;
        o[1] = snr;
        o[2] = port;
    }
    return rc(n);
}
static MP_DEFINE_CONST_FUN_OBJ_3(lora_recv_obj, lora_recv);

static const uint8_t *bytes_of(mp_obj_t o, size_t want) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    if (b.len != want) {
        mp_raise_ValueError(MP_ERROR_TEXT("length"));
    }
    return b.buf;
}

static mp_obj_t lora_join(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return rc(moy_lora_join(u32(a[0]), bytes_of(a[1], 8), bytes_of(a[2], 8), bytes_of(a[3], 16),
                            (uint8_t)u32(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lora_join_obj, 5, 5, lora_join);

static mp_obj_t lora_uplink(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[2], &b, MP_BUFFER_READ);
    return rc(moy_lora_uplink(u32(a[0]), (uint8_t)u32(a[1]), b.buf, b.len, mp_obj_is_true(a[3]),
                              (uint8_t)u32(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lora_uplink_obj, 5, 5, lora_uplink);

static mp_obj_t lora_joined(void) {
    uint32_t addr, fcnt;
    int j = moy_lora_joined(&addr, &fcnt);
    mp_obj_t t[3] = { mp_obj_new_bool(j), mp_obj_new_int_from_uint(addr),
                      mp_obj_new_int_from_uint(fcnt) };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_joined_obj, lora_joined);

static mp_obj_t lora_mode(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_lora_mode());
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_mode_obj, lora_mode);

static mp_obj_t lora_step(mp_obj_t now) {
    return mp_obj_new_int_from_uint(moy_lora_step(u32(now)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lora_step_obj, lora_step);

static mp_obj_t lora_source(void) {
    return mp_obj_new_int_from_uint(moy_lora_source());
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_source_obj, lora_source);

static mp_obj_t lora_present(void) {
    return mp_obj_new_bool(moy_lora_present());
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_present_obj, lora_present);

static mp_obj_t lora_gate_stats(void) {
    #ifdef LORA_GATE
    uint32_t n, us, x, w, wmax;
    moy_flush_gate_stats(&n, &us);
    moy_lora_xfer_stats(&x, &w, &wmax);
    mp_obj_t t[5] = { mp_obj_new_int_from_uint(n), mp_obj_new_int_from_uint(us),
                      mp_obj_new_int_from_uint(x), mp_obj_new_int_from_uint(w),
                      mp_obj_new_int_from_uint(wmax) };
    return mp_obj_new_tuple(5, t);
    #else
    return mp_const_none;
    #endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_gate_stats_obj, lora_gate_stats);

static mp_obj_t lora_sim(mp_obj_t on) {
    moy_lora_sim(mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(lora_sim_obj, lora_sim);

static mp_obj_t lora_sim_sent(void) {
    uint8_t buf[MOY_LORA_PKT];
    uint32_t freq, bw;
    uint8_t sf, sync, iq;
    int n = moy_lora_sim_sent(buf, sizeof(buf), &freq, &sf, &bw, &sync, &iq);
    if (n <= 0) {
        return mp_const_none;
    }
    mp_obj_t t[6] = { mp_obj_new_bytes(buf, (size_t)n), mp_obj_new_int_from_uint(freq),
                      MP_OBJ_NEW_SMALL_INT(sf), mp_obj_new_int_from_uint(bw),
                      MP_OBJ_NEW_SMALL_INT(sync), mp_obj_new_bool(iq) };
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_sim_sent_obj, lora_sim_sent);

static mp_obj_t lora_sim_air(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[0], &b, MP_BUFFER_READ);
    return mp_obj_new_bool(moy_lora_sim_air(b.buf, b.len, u32(a[1]), (uint8_t)u32(a[2]),
                                            (int16_t)mp_obj_get_int(a[3]),
                                            (int8_t)mp_obj_get_int(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lora_sim_air_obj, 5, 5, lora_sim_air);

static mp_obj_t lora_reset(void) {
    moy_lora_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(lora_reset_obj, lora_reset);

static const mp_rom_map_elem_t lora_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_lora) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&lora_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&lora_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&lora_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&lora_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_config), MP_ROM_PTR(&lora_config_obj) },
    { MP_ROM_QSTR(MP_QSTR_listen), MP_ROM_PTR(&lora_listen_obj) },
    { MP_ROM_QSTR(MP_QSTR_send), MP_ROM_PTR(&lora_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_recv), MP_ROM_PTR(&lora_recv_obj) },
    { MP_ROM_QSTR(MP_QSTR_join), MP_ROM_PTR(&lora_join_obj) },
    { MP_ROM_QSTR(MP_QSTR_uplink), MP_ROM_PTR(&lora_uplink_obj) },
    { MP_ROM_QSTR(MP_QSTR_joined), MP_ROM_PTR(&lora_joined_obj) },
    { MP_ROM_QSTR(MP_QSTR_mode), MP_ROM_PTR(&lora_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&lora_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_source), MP_ROM_PTR(&lora_source_obj) },
    { MP_ROM_QSTR(MP_QSTR_present), MP_ROM_PTR(&lora_present_obj) },
    { MP_ROM_QSTR(MP_QSTR_gate_stats), MP_ROM_PTR(&lora_gate_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim), MP_ROM_PTR(&lora_sim_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_sent), MP_ROM_PTR(&lora_sim_sent_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_air), MP_ROM_PTR(&lora_sim_air_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&lora_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_EV_RX), MP_ROM_INT(MOY_LORA_EV_RX) },
    { MP_ROM_QSTR(MP_QSTR_EV_TX_DONE), MP_ROM_INT(MOY_LORA_EV_TX_DONE) },
    { MP_ROM_QSTR(MP_QSTR_EV_JOINED), MP_ROM_INT(MOY_LORA_EV_JOINED) },
    { MP_ROM_QSTR(MP_QSTR_EV_JOIN_FAILED), MP_ROM_INT(MOY_LORA_EV_JOIN_FAILED) },
    { MP_ROM_QSTR(MP_QSTR_EV_DOWN), MP_ROM_INT(MOY_LORA_EV_DOWN) },
    { MP_ROM_QSTR(MP_QSTR_EV_UP_DONE), MP_ROM_INT(MOY_LORA_EV_UP_DONE) },
    { MP_ROM_QSTR(MP_QSTR_IDLE), MP_ROM_INT(MOY_LORA_IDLE) },
    { MP_ROM_QSTR(MP_QSTR_LISTEN), MP_ROM_INT(MOY_LORA_LISTEN) },
    { MP_ROM_QSTR(MP_QSTR_PKT), MP_ROM_INT(MOY_LORA_PKT) },
};
static MP_DEFINE_CONST_DICT(lora_globals, lora_globals_table);

const mp_obj_module_t moy_lora_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&lora_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_lora, moy_lora_module);
