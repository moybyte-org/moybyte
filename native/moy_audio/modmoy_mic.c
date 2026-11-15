// moy_mic's MicroPython binding: the `mic` device API's driver (moy_mic.h) as
// the module `moy_mic`, its names those face.json lists: the row here is the
// hand one, the rest are generated into modmoy_mic_face.c. A refusal is a
// negative errno (Linux's numbers), which the Python face
// (runtime/system/dev_mic.py) raises.
//
//   open(owner) -> 0 | -errno   close(owner)   sweep(owner) -> n   owner()
//   read(owner, buf) -> samples | -errno   (buf: array('h') or bytearray of PCM16)
//   rate()  drops()  pending()  present()  push(buf)  sim(rate)  reset()

#include "py/runtime.h"

#include "moy_mic.h"

#include "modmoy_mic_face.c"

static mp_obj_t mic_push(mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    moy_mic_push((const int16_t *)b.buf, (uint32_t)(b.len / sizeof(int16_t)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mic_push_obj, mic_push);

static const mp_rom_map_elem_t mic_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_mic) },
    MOY_FACE_MOY_MIC_ROWS
    { MP_ROM_QSTR(MP_QSTR_push), MP_ROM_PTR(&mic_push_obj) },
};
static MP_DEFINE_CONST_DICT(mic_globals, mic_globals_table);

const mp_obj_module_t moy_mic_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&mic_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_mic, moy_mic_module);
