// moy_usb's MicroPython binding: the `usb` device API's driver (moy_usb.h) as
// the module `moy_usb`, the names runtime/host/moy_usb.py gives CPython. A
// refusal is a negative errno, which the face (runtime/system/dev_usb.py)
// raises.
//
//   open(owner) close(owner) sweep(owner) owner() present() source() count()
//   device(i) -> (addr, cls, kind, iface_class, vid, pid, name) | None
//   report(owner, addr, buf) -> n  serial(owner, addr, baud)
//   write(owner, addr, buf)  read(owner, addr, buf) -> n
//   sim(on) sim_attach(addr, cls, kind, vid, pid, name) sim_detach(addr)
//   sim_in(addr, buf) sim_out(addr) -> (bytes, baud) reset()

#include <string.h>

#include "py/runtime.h"

#include "moy_usb.h"

static uint32_t u32(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int(o);
}

static mp_obj_t rc(int r) {
    return MP_OBJ_NEW_SMALL_INT(r);
}

#define ONE(name, expr) \
    static mp_obj_t usb_##name(mp_obj_t a) { return expr; } \
    static MP_DEFINE_CONST_FUN_OBJ_1(usb_##name##_obj, usb_##name);
#define NONE(name, expr) \
    static mp_obj_t usb_##name(void) { return expr; } \
    static MP_DEFINE_CONST_FUN_OBJ_0(usb_##name##_obj, usb_##name);

ONE(open, rc(moy_usb_open(u32(a))))
ONE(close, rc(moy_usb_close(u32(a))))
ONE(sweep, rc((int)moy_usb_sweep(u32(a))))
NONE(owner, mp_obj_new_int_from_uint(moy_usb_owner()))
NONE(present, mp_obj_new_bool(moy_usb_present()))
NONE(source, mp_obj_new_int_from_uint(moy_usb_source()))
NONE(count, mp_obj_new_int_from_uint(moy_usb_count()))

static mp_obj_t usb_device(mp_obj_t i) {
    moy_usb_dev_t d;
    if (moy_usb_device(u32(i), &d) != 0) {
        return mp_const_none;
    }
    mp_obj_t t[7] = { MP_OBJ_NEW_SMALL_INT(d.addr), MP_OBJ_NEW_SMALL_INT(d.cls),
                      MP_OBJ_NEW_SMALL_INT(d.kind), MP_OBJ_NEW_SMALL_INT(d.iface_class),
                      MP_OBJ_NEW_SMALL_INT(d.vid), MP_OBJ_NEW_SMALL_INT(d.pid),
                      mp_obj_new_str(d.name, strlen(d.name)) };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(usb_device_obj, usb_device);

static mp_obj_t usb_report(mp_obj_t owner, mp_obj_t addr, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    return rc(moy_usb_report(u32(owner), (uint8_t)u32(addr), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(usb_report_obj, usb_report);

static mp_obj_t usb_serial(mp_obj_t owner, mp_obj_t addr, mp_obj_t baud) {
    return rc(moy_usb_serial(u32(owner), (uint8_t)u32(addr), u32(baud)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(usb_serial_obj, usb_serial);

static mp_obj_t usb_write(mp_obj_t owner, mp_obj_t addr, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return rc(moy_usb_write(u32(owner), (uint8_t)u32(addr), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(usb_write_obj, usb_write);

static mp_obj_t usb_read(mp_obj_t owner, mp_obj_t addr, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    return rc(moy_usb_read(u32(owner), (uint8_t)u32(addr), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(usb_read_obj, usb_read);

static mp_obj_t usb_sim(mp_obj_t on) {
    moy_usb_sim(mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(usb_sim_obj, usb_sim);

static mp_obj_t usb_sim_attach(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return rc(moy_usb_sim_attach((uint8_t)u32(a[0]), (uint8_t)u32(a[1]), (uint8_t)u32(a[2]),
                                 (uint16_t)u32(a[3]), (uint16_t)u32(a[4]),
                                 mp_obj_str_get_str(a[5])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(usb_sim_attach_obj, 6, 6, usb_sim_attach);

static mp_obj_t usb_sim_detach(mp_obj_t addr) {
    moy_usb_sim_detach((uint8_t)u32(addr));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(usb_sim_detach_obj, usb_sim_detach);

static mp_obj_t usb_sim_in(mp_obj_t addr, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    moy_usb_sim_in((uint8_t)u32(addr), b.buf, b.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(usb_sim_in_obj, usb_sim_in);

static mp_obj_t usb_sim_out(mp_obj_t addr) {
    uint8_t buf[512];
    uint32_t baud = 0;
    int n = moy_usb_sim_out((uint8_t)u32(addr), buf, sizeof(buf), &baud);
    if (n < 0) {
        return mp_const_none;
    }
    mp_obj_t t[2] = { mp_obj_new_bytes(buf, (size_t)n), mp_obj_new_int_from_uint(baud) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(usb_sim_out_obj, usb_sim_out);

static mp_obj_t usb_reset(void) {
    moy_usb_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_reset_obj, usb_reset);

static const mp_rom_map_elem_t usb_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_usb) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&usb_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&usb_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&usb_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&usb_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_present), MP_ROM_PTR(&usb_present_obj) },
    { MP_ROM_QSTR(MP_QSTR_source), MP_ROM_PTR(&usb_source_obj) },
    { MP_ROM_QSTR(MP_QSTR_count), MP_ROM_PTR(&usb_count_obj) },
    { MP_ROM_QSTR(MP_QSTR_device), MP_ROM_PTR(&usb_device_obj) },
    { MP_ROM_QSTR(MP_QSTR_report), MP_ROM_PTR(&usb_report_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial), MP_ROM_PTR(&usb_serial_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&usb_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&usb_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim), MP_ROM_PTR(&usb_sim_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_attach), MP_ROM_PTR(&usb_sim_attach_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_detach), MP_ROM_PTR(&usb_sim_detach_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_in), MP_ROM_PTR(&usb_sim_in_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_out), MP_ROM_PTR(&usb_sim_out_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&usb_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_HID), MP_ROM_INT(MOY_USB_HID) },
    { MP_ROM_QSTR(MP_QSTR_CDC), MP_ROM_INT(MOY_USB_CDC) },
    { MP_ROM_QSTR(MP_QSTR_MIDI), MP_ROM_INT(MOY_USB_MIDI) },
    { MP_ROM_QSTR(MP_QSTR_OTHER), MP_ROM_INT(MOY_USB_OTHER) },
    { MP_ROM_QSTR(MP_QSTR_KEYBOARD), MP_ROM_INT(MOY_USB_KIND_KEYBOARD) },
    { MP_ROM_QSTR(MP_QSTR_MOUSE), MP_ROM_INT(MOY_USB_KIND_MOUSE) },
    { MP_ROM_QSTR(MP_QSTR_EV_ATTACH), MP_ROM_INT(MOY_USB_EV_ATTACH) },
    { MP_ROM_QSTR(MP_QSTR_EV_DETACH), MP_ROM_INT(MOY_USB_EV_DETACH) },
    { MP_ROM_QSTR(MP_QSTR_EV_REPORT), MP_ROM_INT(MOY_USB_EV_REPORT) },
    { MP_ROM_QSTR(MP_QSTR_EV_DATA), MP_ROM_INT(MOY_USB_EV_DATA) },
    { MP_ROM_QSTR(MP_QSTR_EV_SENT), MP_ROM_INT(MOY_USB_EV_SENT) },
};
static MP_DEFINE_CONST_DICT(usb_globals, usb_globals_table);

const mp_obj_module_t moy_usb_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&usb_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_usb, moy_usb_module);
