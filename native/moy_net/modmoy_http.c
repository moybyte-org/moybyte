// moy_fetch's MicroPython binding: the `http` device API's driver (moy_fetch.h)
// as the module `moy_http`, its names those face.json lists, every row
// generated into modmoy_http_face.h. A refusal is
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

static const mp_rom_map_elem_t http_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_http) },
    MOY_FACE_MOY_HTTP_ROWS
};
static MP_DEFINE_CONST_DICT(http_globals, http_globals_table);

const mp_obj_module_t moy_http_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&http_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_http, moy_http_module);
