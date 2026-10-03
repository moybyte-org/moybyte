// moy_png's MicroPython binding: the cover reader (moy_png.h) for the boards
// and the browser console. runtime/cover_png.py is the one caller, and the
// Python twin the host runs instead.
//
//   begin(work, data, div, fmt[, pal]) -> True (decode it) / False (not a cover)
//   rows(work, data, out, n)           -> 1 done, 0 more to come, -1 not a cover
//
// `work` is WORK bytes the caller keeps and reuses; `data` and `out` are handed
// over again on every call, and nothing here holds a pointer between calls.

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_png.h"

static mp_obj_t moy_png_begin_fn(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t work;
    mp_buffer_info_t data;
    mp_buffer_info_t pal = {0};
    mp_get_buffer_raise(args[0], &work, MP_BUFFER_WRITE);
    mp_get_buffer_raise(args[1], &data, MP_BUFFER_READ);
    int div = mp_obj_get_int(args[2]);
    int fmt = mp_obj_get_int(args[3]);
    if (n_args > 4 && args[4] != mp_const_none) {
        mp_get_buffer_raise(args[4], &pal, MP_BUFFER_READ);
    }
    int r = moy_png_begin(work.buf, work.len, (const uint8_t *)data.buf, data.len,
                          div, fmt, (const uint8_t *)pal.buf, (int)(pal.len / 3));
    if (r < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_png: bad work, div, fmt or palette"));
    }
    return mp_obj_new_bool(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_png_begin_obj, 4, 5, moy_png_begin_fn);

static mp_obj_t moy_png_rows_fn(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    mp_buffer_info_t work;
    mp_buffer_info_t data;
    mp_buffer_info_t out;
    mp_get_buffer_raise(args[0], &work, MP_BUFFER_WRITE);
    mp_get_buffer_raise(args[1], &data, MP_BUFFER_READ);
    mp_get_buffer_raise(args[2], &out, MP_BUFFER_WRITE);
    int r = moy_png_rows(work.buf, (const uint8_t *)data.buf, data.len,
                         (uint8_t *)out.buf, out.len, mp_obj_get_int(args[3]));
    return MP_OBJ_NEW_SMALL_INT(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_png_rows_obj, 4, 4, moy_png_rows_fn);

static const mp_rom_map_elem_t moy_png_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_png) },
    { MP_ROM_QSTR(MP_QSTR_begin), MP_ROM_PTR(&moy_png_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_rows), MP_ROM_PTR(&moy_png_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_WORK), MP_ROM_INT(MOY_PNG_WORK) },
    { MP_ROM_QSTR(MP_QSTR_SIDE), MP_ROM_INT(MOY_PNG_SIDE) },
    { MP_ROM_QSTR(MP_QSTR_MAX_BYTES), MP_ROM_INT(MOY_PNG_MAX_BYTES) },
    { MP_ROM_QSTR(MP_QSTR_RGB888), MP_ROM_INT(MOY_PNG_RGB888) },
    { MP_ROM_QSTR(MP_QSTR_RGB565), MP_ROM_INT(MOY_PNG_RGB565) },
    { MP_ROM_QSTR(MP_QSTR_RGB565_SW), MP_ROM_INT(MOY_PNG_RGB565_SW) },
    { MP_ROM_QSTR(MP_QSTR_INDEX), MP_ROM_INT(MOY_PNG_INDEX) },
};
static MP_DEFINE_CONST_DICT(moy_png_globals, moy_png_globals_table);

const mp_obj_module_t moy_png_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_png_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_png, moy_png_user_cmodule);
