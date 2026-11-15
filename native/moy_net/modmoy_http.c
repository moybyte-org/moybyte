// moy_fetch's MicroPython binding: the `http` device API's driver (moy_fetch.h)
// as the module `moy_http`, its names those face.json lists: the row here is
// the hand one, the rest are generated into modmoy_http_face.h. A refusal is
// a negative errno (Linux's numbers), which the Python face
// (runtime/system/devices.py) raises.
//
//   open(owner, url, cap) -> h | -errno   tls() -> 1 where https verifies, else 0
//   stream(owner, url, window) -> h | -errno   take(h, buf) -> n | -errno
//   state(h) -> (state, status, n, err) | -errno
//   read(h, off, buf) -> n | -errno     close(h)   sweep(owner) -> n
//   count(owner) -> n   source() -> src   run()   present()   reset()
//   QUEUED RUNNING DONE FAILED STREAM, CAP_MAX, ETLS, ECERT

#include "py/runtime.h"

#include "moy_fetch.h"
#include "moy_ota.h"

#include "modmoy_http_face.h"

static mp_obj_t http_state(mp_obj_t h) {
    int status = 0, err = 0;
    uint32_t n = 0;
    int st = moy_fetch_state((uint32_t)mp_obj_get_int(h), &status, &n, &err);
    if (st < 0) {
        return MP_OBJ_NEW_SMALL_INT(st);
    }
    mp_obj_t t[4] = { MP_OBJ_NEW_SMALL_INT(st), MP_OBJ_NEW_SMALL_INT(status),
                      mp_obj_new_int_from_uint(n), MP_OBJ_NEW_SMALL_INT(err) };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(http_state_obj, http_state);

static const mp_rom_map_elem_t http_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_http) },
    MOY_FACE_MOY_HTTP_ROWS
    { MP_ROM_QSTR(MP_QSTR_state), MP_ROM_PTR(&http_state_obj) },
};
static MP_DEFINE_CONST_DICT(http_globals, http_globals_table);

const mp_obj_module_t moy_http_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&http_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_http, moy_http_module);
