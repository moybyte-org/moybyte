// moy_midi's MicroPython binding: the `midi` device API's driver (moy_midi.h)
// as the module `moy_midi`, the names runtime/host/moy_midi.py gives CPython.
// A refusal is a negative errno, which the face (runtime/system/dev_midi.py)
// raises.
//
//   open(owner) close(owner) sweep(owner) owner() source()
//   port(owner, transport, unit) -> port  unport(owner, port)
//   uart(owner, port)  feed(port, buf)  ble_in(owner, port, buf)
//   usb_pack(msg, out) -> n  ble_pack(msg, ms, out) -> n  reset()

#include "py/runtime.h"

#include "moy_midi.h"

static uint32_t u32(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int(o);
}

static mp_obj_t rc(int r) {
    return MP_OBJ_NEW_SMALL_INT(r);
}

static mp_obj_t midi_open(mp_obj_t o) {
    return rc(moy_midi_open(u32(o)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(midi_open_obj, midi_open);

static mp_obj_t midi_close(mp_obj_t o) {
    return rc(moy_midi_close(u32(o)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(midi_close_obj, midi_close);

static mp_obj_t midi_sweep(mp_obj_t o) {
    return rc((int)moy_midi_sweep(u32(o)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(midi_sweep_obj, midi_sweep);

static mp_obj_t midi_owner(void) {
    return mp_obj_new_int_from_uint(moy_midi_owner());
}
static MP_DEFINE_CONST_FUN_OBJ_0(midi_owner_obj, midi_owner);

static mp_obj_t midi_source(void) {
    return mp_obj_new_int_from_uint(moy_midi_source());
}
static MP_DEFINE_CONST_FUN_OBJ_0(midi_source_obj, midi_source);

static mp_obj_t midi_port(mp_obj_t owner, mp_obj_t transport, mp_obj_t unit) {
    return rc(moy_midi_port(u32(owner), mp_obj_get_int(transport), u32(unit)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(midi_port_obj, midi_port);

static mp_obj_t midi_unport(mp_obj_t owner, mp_obj_t port) {
    return rc(moy_midi_unport(u32(owner), mp_obj_get_int(port)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(midi_unport_obj, midi_unport);

static mp_obj_t midi_uart(mp_obj_t owner, mp_obj_t port) {
    return rc(moy_midi_uart(u32(owner), mp_obj_get_int(port)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(midi_uart_obj, midi_uart);

static mp_obj_t midi_feed(mp_obj_t port, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    moy_midi_feed(mp_obj_get_int(port), b.buf, b.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(midi_feed_obj, midi_feed);

static mp_obj_t midi_ble_in(mp_obj_t owner, mp_obj_t port, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return rc(moy_midi_ble_in(u32(owner), mp_obj_get_int(port), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(midi_ble_in_obj, midi_ble_in);

static mp_obj_t midi_usb_pack(mp_obj_t msg, mp_obj_t out) {
    mp_buffer_info_t m, o;
    mp_get_buffer_raise(msg, &m, MP_BUFFER_READ);
    mp_get_buffer_raise(out, &o, MP_BUFFER_WRITE);
    return rc(moy_midi_usb_pack(m.buf, m.len, o.buf, o.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(midi_usb_pack_obj, midi_usb_pack);

static mp_obj_t midi_ble_pack(mp_obj_t msg, mp_obj_t ms, mp_obj_t out) {
    mp_buffer_info_t m, o;
    mp_get_buffer_raise(msg, &m, MP_BUFFER_READ);
    mp_get_buffer_raise(out, &o, MP_BUFFER_WRITE);
    return rc(moy_midi_ble_pack(m.buf, m.len, u32(ms), o.buf, o.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(midi_ble_pack_obj, midi_ble_pack);

static mp_obj_t midi_reset(void) {
    moy_midi_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(midi_reset_obj, midi_reset);

static const mp_rom_map_elem_t midi_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_midi) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&midi_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&midi_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&midi_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&midi_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_source), MP_ROM_PTR(&midi_source_obj) },
    { MP_ROM_QSTR(MP_QSTR_port), MP_ROM_PTR(&midi_port_obj) },
    { MP_ROM_QSTR(MP_QSTR_unport), MP_ROM_PTR(&midi_unport_obj) },
    { MP_ROM_QSTR(MP_QSTR_uart), MP_ROM_PTR(&midi_uart_obj) },
    { MP_ROM_QSTR(MP_QSTR_feed), MP_ROM_PTR(&midi_feed_obj) },
    { MP_ROM_QSTR(MP_QSTR_ble_in), MP_ROM_PTR(&midi_ble_in_obj) },
    { MP_ROM_QSTR(MP_QSTR_usb_pack), MP_ROM_PTR(&midi_usb_pack_obj) },
    { MP_ROM_QSTR(MP_QSTR_ble_pack), MP_ROM_PTR(&midi_ble_pack_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&midi_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_UART), MP_ROM_INT(MOY_MIDI_UART) },
    { MP_ROM_QSTR(MP_QSTR_USB), MP_ROM_INT(MOY_MIDI_USB) },
    { MP_ROM_QSTR(MP_QSTR_BLE), MP_ROM_INT(MOY_MIDI_BLE) },
};
static MP_DEFINE_CONST_DICT(midi_globals, midi_globals_table);

const mp_obj_module_t moy_midi_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&midi_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_midi, moy_midi_module);
