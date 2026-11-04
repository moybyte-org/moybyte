// moy_mic's MicroPython binding: the `mic` device API's driver (moy_mic.h) as
// the module `moy_mic`, the names runtime/host/moy_mic.py gives CPython. A
// refusal is a negative errno (Linux's numbers), which the Python face
// (runtime/system/dev_mic.py) raises.
//
//   open(owner) -> 0 | -errno   close(owner)   sweep(owner) -> n   owner()
//   read(owner, buf) -> samples | -errno   (buf: array('h') or bytearray of PCM16)
//   rate()  drops()  pending()  present()  push(buf)  sim(rate)  reset()

#include "py/runtime.h"

#include "moy_mic.h"

static uint32_t u32(mp_obj_t o) {
    return (uint32_t)mp_obj_get_int(o);
}

static mp_obj_t mic_open(mp_obj_t owner) {
    return MP_OBJ_NEW_SMALL_INT(moy_mic_open(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mic_open_obj, mic_open);

static mp_obj_t mic_close(mp_obj_t owner) {
    return MP_OBJ_NEW_SMALL_INT(moy_mic_close(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mic_close_obj, mic_close);

static mp_obj_t mic_sweep(mp_obj_t owner) {
    return MP_OBJ_NEW_SMALL_INT(moy_mic_sweep(u32(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mic_sweep_obj, mic_sweep);

static mp_obj_t mic_owner(void) {
    return mp_obj_new_int_from_uint(moy_mic_owner());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mic_owner_obj, mic_owner);

static mp_obj_t mic_read(mp_obj_t owner, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    return MP_OBJ_NEW_SMALL_INT(moy_mic_read(u32(owner), (int16_t *)b.buf,
                                             (uint32_t)(b.len / sizeof(int16_t))));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mic_read_obj, mic_read);

static mp_obj_t mic_push(mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    moy_mic_push((const int16_t *)b.buf, (uint32_t)(b.len / sizeof(int16_t)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mic_push_obj, mic_push);

static mp_obj_t mic_rate(void) {
    return mp_obj_new_int_from_uint(moy_mic_rate());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mic_rate_obj, mic_rate);

static mp_obj_t mic_drops(void) {
    return mp_obj_new_int_from_uint(moy_mic_drops());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mic_drops_obj, mic_drops);

static mp_obj_t mic_pending(void) {
    return mp_obj_new_int_from_uint(moy_mic_pending());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mic_pending_obj, mic_pending);

static mp_obj_t mic_present(void) {
    return mp_obj_new_bool(moy_mic_present());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mic_present_obj, mic_present);

static mp_obj_t mic_sim(mp_obj_t rate) {
    moy_mic_sim(u32(rate));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mic_sim_obj, mic_sim);

static mp_obj_t mic_reset(void) {
    moy_mic_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mic_reset_obj, mic_reset);

static const mp_rom_map_elem_t mic_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_mic) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&mic_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&mic_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&mic_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&mic_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mic_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_push), MP_ROM_PTR(&mic_push_obj) },
    { MP_ROM_QSTR(MP_QSTR_rate), MP_ROM_PTR(&mic_rate_obj) },
    { MP_ROM_QSTR(MP_QSTR_drops), MP_ROM_PTR(&mic_drops_obj) },
    { MP_ROM_QSTR(MP_QSTR_pending), MP_ROM_PTR(&mic_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_present), MP_ROM_PTR(&mic_present_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim), MP_ROM_PTR(&mic_sim_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&mic_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_RING), MP_ROM_INT(MOY_MIC_RING) },
};
static MP_DEFINE_CONST_DICT(mic_globals, mic_globals_table);

const mp_obj_module_t moy_mic_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&mic_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_mic, moy_mic_module);
