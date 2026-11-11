// moy_gatt's MicroPython binding: the `ble` device API's driver (moy_gatt.h)
// as the module `moy_gatt`, the names runtime/host/moy_gatt.py gives CPython.
// A refusal is a negative errno (Linux's numbers), which the Python face
// (runtime/system/dev_ble.py) raises. An address is 6 bytes little-endian, a
// UUID 2 or 16 bytes little-endian.
//
//   open(owner) close(owner) sweep(owner) owner() present() state() source()
//   scan(owner, ms)  seen(i) -> (addr, type, rssi, name) | None  seen_count()
//   connect(owner, addr, type)  disconnect(owner)
//   find(owner, uuid)  read(owner, handle)  value(owner, buf) -> n
//   write(owner, handle, buf, rsp)  subscribe(owner, handle, on)
//   notified(owner, buf, meta) -> n   (meta: array('i', 1) <- the handle)
//   advertise(owner, name, mfg, interval_ms)  stop_advertising(owner)
//   sim(on) sim_device(addr, rssi, name) sim_chr(addr, uuid, handle, value)
//   sim_written(addr, handle) -> bytes  sim_notify(addr, handle, buf) -> bool
//   sim_adv() -> bytes  reset()

#include <string.h>

#include "py/runtime.h"

#include "moy_gatt.h"

static uint32_t u32(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int(o);
}

static mp_obj_t rc(int r) {
    return MP_OBJ_NEW_SMALL_INT(r);
}

static const uint8_t *addr_of(mp_obj_t o) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    if (b.len != 6) {
        mp_raise_ValueError(MP_ERROR_TEXT("address"));
    }
    return b.buf;
}

static void uuid_of(mp_obj_t o, moy_gatt_uuid_t *u) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    if (b.len != 2 && b.len != 16) {
        mp_raise_ValueError(MP_ERROR_TEXT("uuid"));
    }
    u->len = (uint8_t)b.len;
    memcpy(u->v, b.buf, b.len);
}

#define ONE(name, expr) \
    static mp_obj_t gatt_##name(mp_obj_t a) { return expr; } \
    static MP_DEFINE_CONST_FUN_OBJ_1(gatt_##name##_obj, gatt_##name);
#define NONE(name, expr) \
    static mp_obj_t gatt_##name(void) { return expr; } \
    static MP_DEFINE_CONST_FUN_OBJ_0(gatt_##name##_obj, gatt_##name);

ONE(open, rc(moy_gatt_open(u32(a))))
ONE(close, rc(moy_gatt_close(u32(a))))
ONE(sweep, rc((int)moy_gatt_sweep(u32(a))))
ONE(disconnect, rc(moy_gatt_disconnect(u32(a))))
ONE(stop_advertising, rc(moy_gatt_stop_advertising(u32(a))))
NONE(owner, mp_obj_new_int_from_uint(moy_gatt_owner()))
NONE(present, mp_obj_new_bool(moy_gatt_present()))
NONE(state, rc(moy_gatt_state()))
NONE(source, mp_obj_new_int_from_uint(moy_gatt_source()))
NONE(seen_count, mp_obj_new_int_from_uint(moy_gatt_seen_count()))

static mp_obj_t gatt_scan(mp_obj_t owner, mp_obj_t ms) {
    return rc(moy_gatt_scan(u32(owner), u32(ms)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(gatt_scan_obj, gatt_scan);

static mp_obj_t gatt_seen(mp_obj_t i) {
    moy_gatt_seen_t s;
    if (moy_gatt_seen(u32(i), &s) != 0) {
        return mp_const_none;
    }
    mp_obj_t t[4] = { mp_obj_new_bytes(s.addr, 6), MP_OBJ_NEW_SMALL_INT(s.type),
                      MP_OBJ_NEW_SMALL_INT(s.rssi), mp_obj_new_str(s.name, strlen(s.name)) };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(gatt_seen_obj, gatt_seen);

static mp_obj_t gatt_connect(mp_obj_t owner, mp_obj_t addr, mp_obj_t type) {
    return rc(moy_gatt_connect(u32(owner), addr_of(addr), (uint8_t)u32(type)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(gatt_connect_obj, gatt_connect);

static mp_obj_t gatt_find(mp_obj_t owner, mp_obj_t uuid) {
    moy_gatt_uuid_t u;
    uuid_of(uuid, &u);
    return rc(moy_gatt_find(u32(owner), &u));
}
static MP_DEFINE_CONST_FUN_OBJ_2(gatt_find_obj, gatt_find);

static mp_obj_t gatt_read(mp_obj_t owner, mp_obj_t handle) {
    return rc(moy_gatt_read(u32(owner), (uint16_t)u32(handle)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(gatt_read_obj, gatt_read);

static mp_obj_t gatt_value(mp_obj_t owner, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    return rc(moy_gatt_value(u32(owner), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(gatt_value_obj, gatt_value);

static mp_obj_t gatt_write(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[2], &b, MP_BUFFER_READ);
    return rc(moy_gatt_write(u32(a[0]), (uint16_t)u32(a[1]), b.buf, b.len, mp_obj_is_true(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gatt_write_obj, 4, 4, gatt_write);

static mp_obj_t gatt_subscribe(mp_obj_t owner, mp_obj_t handle, mp_obj_t on) {
    return rc(moy_gatt_subscribe(u32(owner), (uint16_t)u32(handle), mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(gatt_subscribe_obj, gatt_subscribe);

static mp_obj_t gatt_notified(mp_obj_t owner, mp_obj_t buf, mp_obj_t meta) {
    mp_buffer_info_t b, m;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    mp_get_buffer_raise(meta, &m, MP_BUFFER_WRITE);
    if (m.len < sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("meta"));
    }
    uint16_t h = 0;
    int n = moy_gatt_notified(u32(owner), b.buf, b.len, &h);
    if (n > 0) {
        ((int32_t *)m.buf)[0] = h;
    }
    return rc(n);
}
static MP_DEFINE_CONST_FUN_OBJ_3(gatt_notified_obj, gatt_notified);

static mp_obj_t gatt_advertise(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[2], &b, MP_BUFFER_READ);
    return rc(moy_gatt_advertise(u32(a[0]), mp_obj_str_get_str(a[1]), b.buf, b.len, u32(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gatt_advertise_obj, 4, 4, gatt_advertise);

static mp_obj_t gatt_sim(mp_obj_t on) {
    moy_gatt_sim(mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(gatt_sim_obj, gatt_sim);

static mp_obj_t gatt_sim_device(mp_obj_t addr, mp_obj_t rssi, mp_obj_t name) {
    return rc(moy_gatt_sim_device(addr_of(addr), (int8_t)mp_obj_get_int(rssi),
                                  mp_obj_str_get_str(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(gatt_sim_device_obj, gatt_sim_device);

static mp_obj_t gatt_sim_chr(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_gatt_uuid_t u;
    uuid_of(a[1], &u);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[3], &b, MP_BUFFER_READ);
    return rc(moy_gatt_sim_chr(addr_of(a[0]), &u, (uint16_t)u32(a[2]), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(gatt_sim_chr_obj, 4, 4, gatt_sim_chr);

static mp_obj_t gatt_sim_written(mp_obj_t addr, mp_obj_t handle) {
    uint8_t buf[MOY_GATT_VALUE];
    int n = moy_gatt_sim_written(addr_of(addr), (uint16_t)u32(handle), buf, sizeof(buf));
    return n < 0 ? mp_const_none : mp_obj_new_bytes(buf, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_2(gatt_sim_written_obj, gatt_sim_written);

static mp_obj_t gatt_sim_notify(mp_obj_t addr, mp_obj_t handle, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return mp_obj_new_bool(moy_gatt_sim_notify(addr_of(addr), (uint16_t)u32(handle), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(gatt_sim_notify_obj, gatt_sim_notify);

static mp_obj_t gatt_sim_adv(void) {
    uint8_t buf[MOY_GATT_ADV];
    int n = moy_gatt_sim_adv(buf, sizeof(buf));
    return mp_obj_new_bytes(buf, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_0(gatt_sim_adv_obj, gatt_sim_adv);

static mp_obj_t gatt_reset(void) {
    moy_gatt_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(gatt_reset_obj, gatt_reset);

static const mp_rom_map_elem_t gatt_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_gatt) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&gatt_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&gatt_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&gatt_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&gatt_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_present), MP_ROM_PTR(&gatt_present_obj) },
    { MP_ROM_QSTR(MP_QSTR_state), MP_ROM_PTR(&gatt_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_source), MP_ROM_PTR(&gatt_source_obj) },
    { MP_ROM_QSTR(MP_QSTR_scan), MP_ROM_PTR(&gatt_scan_obj) },
    { MP_ROM_QSTR(MP_QSTR_seen), MP_ROM_PTR(&gatt_seen_obj) },
    { MP_ROM_QSTR(MP_QSTR_seen_count), MP_ROM_PTR(&gatt_seen_count_obj) },
    { MP_ROM_QSTR(MP_QSTR_connect), MP_ROM_PTR(&gatt_connect_obj) },
    { MP_ROM_QSTR(MP_QSTR_disconnect), MP_ROM_PTR(&gatt_disconnect_obj) },
    { MP_ROM_QSTR(MP_QSTR_find), MP_ROM_PTR(&gatt_find_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&gatt_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_value), MP_ROM_PTR(&gatt_value_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&gatt_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_subscribe), MP_ROM_PTR(&gatt_subscribe_obj) },
    { MP_ROM_QSTR(MP_QSTR_notified), MP_ROM_PTR(&gatt_notified_obj) },
    { MP_ROM_QSTR(MP_QSTR_advertise), MP_ROM_PTR(&gatt_advertise_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop_advertising), MP_ROM_PTR(&gatt_stop_advertising_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim), MP_ROM_PTR(&gatt_sim_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_device), MP_ROM_PTR(&gatt_sim_device_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_chr), MP_ROM_PTR(&gatt_sim_chr_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_written), MP_ROM_PTR(&gatt_sim_written_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_notify), MP_ROM_PTR(&gatt_sim_notify_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_adv), MP_ROM_PTR(&gatt_sim_adv_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&gatt_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_EV_ADV), MP_ROM_INT(MOY_GATT_EV_ADV) },
    { MP_ROM_QSTR(MP_QSTR_EV_SCAN_DONE), MP_ROM_INT(MOY_GATT_EV_SCAN_DONE) },
    { MP_ROM_QSTR(MP_QSTR_EV_CONNECTED), MP_ROM_INT(MOY_GATT_EV_CONNECTED) },
    { MP_ROM_QSTR(MP_QSTR_EV_DISCONNECTED), MP_ROM_INT(MOY_GATT_EV_DISCONNECTED) },
    { MP_ROM_QSTR(MP_QSTR_EV_FOUND), MP_ROM_INT(MOY_GATT_EV_FOUND) },
    { MP_ROM_QSTR(MP_QSTR_EV_READ), MP_ROM_INT(MOY_GATT_EV_READ) },
    { MP_ROM_QSTR(MP_QSTR_EV_WRITTEN), MP_ROM_INT(MOY_GATT_EV_WRITTEN) },
    { MP_ROM_QSTR(MP_QSTR_EV_NOTIFY), MP_ROM_INT(MOY_GATT_EV_NOTIFY) },
    { MP_ROM_QSTR(MP_QSTR_EV_ADVERTISING), MP_ROM_INT(MOY_GATT_EV_ADVERTISING) },
    { MP_ROM_QSTR(MP_QSTR_CONNECTED), MP_ROM_INT(MOY_GATT_CONNECTED) },
};
static MP_DEFINE_CONST_DICT(gatt_globals, gatt_globals_table);

const mp_obj_module_t moy_gatt_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&gatt_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_gatt, moy_gatt_module);
