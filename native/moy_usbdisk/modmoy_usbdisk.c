// moy_usbdisk's Python face (moy_usbdisk.h), what runtime/system/usb_drive.py
// drives: start() raising OSError with the C's errno, stop(), state(), and
// SERIAL, True where the drive takes the board's serial dev channel while it
// runs (the S3's one PHY).

#include "py/runtime.h"
#include "sdkconfig.h"

#include "moy_usbdisk.h"

static mp_obj_t mod_start(void) {
    int rc = moy_usbdisk_start();
    if (rc != 0) {
        mp_raise_OSError(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_start_obj, mod_start);

static mp_obj_t mod_stop(void) {
    MP_THREAD_GIL_EXIT();
    moy_usbdisk_stop();
    MP_THREAD_GIL_ENTER();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_stop_obj, mod_stop);

static mp_obj_t mod_state(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_usbdisk_state());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_state_obj, mod_state);

static const mp_rom_map_elem_t moy_usbdisk_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_usbdisk) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&mod_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&mod_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_state), MP_ROM_PTR(&mod_state_obj) },
    #if CONFIG_IDF_TARGET_ESP32S3
    { MP_ROM_QSTR(MP_QSTR_SERIAL), MP_ROM_TRUE },
    #else
    { MP_ROM_QSTR(MP_QSTR_SERIAL), MP_ROM_FALSE },
    #endif
};
static MP_DEFINE_CONST_DICT(moy_usbdisk_globals, moy_usbdisk_globals_table);

const mp_obj_module_t moy_usbdisk_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_usbdisk_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_usbdisk, moy_usbdisk_module);
