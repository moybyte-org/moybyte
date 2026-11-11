// moy_fetch's MicroPython binding: the `http` device API's driver (moy_fetch.h)
// as the module `moy_http`, the names runtime/host/moy_http.py gives CPython.
// A refusal is a negative errno (Linux's numbers), which the Python face
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

static mp_obj_t http_open(size_t n_args, const mp_obj_t *args) {
    uint32_t h = 0;
    int rc = moy_fetch_open((uint32_t)mp_obj_get_int(args[0]), mp_obj_str_get_str(args[1]),
                            (uint32_t)mp_obj_get_int(args[2]), &h);
    return mp_obj_new_int(rc < 0 ? rc : (mp_int_t)h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(http_open_obj, 3, 3, http_open);

static mp_obj_t http_stream(size_t n_args, const mp_obj_t *args) {
    uint32_t h = 0;
    int rc = moy_fetch_stream((uint32_t)mp_obj_get_int(args[0]), mp_obj_str_get_str(args[1]),
                              (uint32_t)mp_obj_get_int(args[2]), &h);
    return mp_obj_new_int(rc < 0 ? rc : (mp_int_t)h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(http_stream_obj, 3, 3, http_stream);

static mp_obj_t http_take(mp_obj_t h, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    return MP_OBJ_NEW_SMALL_INT(moy_fetch_take((uint32_t)mp_obj_get_int(h), b.buf,
                                               (uint32_t)b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(http_take_obj, http_take);

static mp_obj_t http_tls(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_fetch_tls());
}
static MP_DEFINE_CONST_FUN_OBJ_0(http_tls_obj, http_tls);

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

static mp_obj_t http_read(mp_obj_t h, mp_obj_t off, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    return MP_OBJ_NEW_SMALL_INT(moy_fetch_read((uint32_t)mp_obj_get_int(h),
                                               (uint32_t)mp_obj_get_int(off), b.buf,
                                               (uint32_t)b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_3(http_read_obj, http_read);

static mp_obj_t http_close(mp_obj_t h) {
    return MP_OBJ_NEW_SMALL_INT(moy_fetch_close((uint32_t)mp_obj_get_int(h)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(http_close_obj, http_close);

static mp_obj_t http_sweep(mp_obj_t owner) {
    return mp_obj_new_int_from_uint(moy_fetch_sweep((uint32_t)mp_obj_get_int(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(http_sweep_obj, http_sweep);

static mp_obj_t http_count(mp_obj_t owner) {
    return mp_obj_new_int_from_uint(moy_fetch_count((uint32_t)mp_obj_get_int(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(http_count_obj, http_count);

static mp_obj_t http_source(void) {
    return mp_obj_new_int_from_uint(moy_fetch_source());
}
static MP_DEFINE_CONST_FUN_OBJ_0(http_source_obj, http_source);

static mp_obj_t http_run(void) {
    moy_fetch_run();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(http_run_obj, http_run);

static mp_obj_t http_present(void) {
    return mp_obj_new_bool(moy_fetch_present());
}
static MP_DEFINE_CONST_FUN_OBJ_0(http_present_obj, http_present);

static mp_obj_t http_reset(void) {
    moy_fetch_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(http_reset_obj, http_reset);

static const mp_rom_map_elem_t http_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_http) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&http_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_state), MP_ROM_PTR(&http_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&http_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_stream), MP_ROM_PTR(&http_stream_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&http_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&http_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&http_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_count), MP_ROM_PTR(&http_count_obj) },
    { MP_ROM_QSTR(MP_QSTR_source), MP_ROM_PTR(&http_source_obj) },
    { MP_ROM_QSTR(MP_QSTR_run), MP_ROM_PTR(&http_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_present), MP_ROM_PTR(&http_present_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&http_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_tls), MP_ROM_PTR(&http_tls_obj) },
    { MP_ROM_QSTR(MP_QSTR_QUEUED), MP_ROM_INT(MOY_FETCH_QUEUED) },
    { MP_ROM_QSTR(MP_QSTR_RUNNING), MP_ROM_INT(MOY_FETCH_RUNNING) },
    { MP_ROM_QSTR(MP_QSTR_DONE), MP_ROM_INT(MOY_FETCH_DONE) },
    { MP_ROM_QSTR(MP_QSTR_FAILED), MP_ROM_INT(MOY_FETCH_FAILED) },
    { MP_ROM_QSTR(MP_QSTR_STREAM), MP_ROM_INT(MOY_FETCH_STREAM) },
    { MP_ROM_QSTR(MP_QSTR_CAP_MAX), MP_ROM_INT(MOY_FETCH_CAP_MAX) },
    { MP_ROM_QSTR(MP_QSTR_ETLS), MP_ROM_INT(MOY_HTTPC_ETLS) },
    { MP_ROM_QSTR(MP_QSTR_ECERT), MP_ROM_INT(MOY_HTTPC_ECERT) },
};
static MP_DEFINE_CONST_DICT(http_globals, http_globals_table);

const mp_obj_module_t moy_http_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&http_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_http, moy_http_module);
