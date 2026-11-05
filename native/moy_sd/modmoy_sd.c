// moy_sd's Python face: the board's card (moy_sd.h, moy_sd.c) as sectors.
//
//   up() -> sectors          the board's card brought up from its defines, the
//                            same C the kernel calls with no VM; a second call
//                            answers the card already up. Raises OSError
//                            "moy_sd <step> failed: <err>".
//   read(start, buf, count)  count * 512 bytes; a failed transfer raises and
//   write(start, buf, count) keeps the card (a teardown is a failed bring-up's)
//   sector_count()           0 while the card is not up
//   deinit()                 what the card took given back, never the panel's
//                            bus (card_store's failed mount on the boards
//                            whose card has a bus of its own)
//
// native/moy_store's card volume (moy_card.c) reads through moy_sd_card_io,
// not through this module.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_sd.h"

#define MOY_SD_SECTOR 512

#ifdef ESP_IDF_VERSION

static mp_obj_t moy_sd_up(void) {
    uint32_t sectors;
    const char *what;
    int err = moy_sd_card_up(&sectors, &what);
    if (err != 0) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("moy_sd %s failed: %d"), what, err);
    }
    return mp_obj_new_int_from_uint(sectors);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_sd_up_obj, moy_sd_up);

static void xfer(mp_obj_t start_in, mp_obj_t buf_in, mp_obj_t count_in, int write) {
    if (moy_sd_card_sectors() == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_sd: not mounted"));
    }
    uint32_t start = (uint32_t)mp_obj_get_int(start_in);
    uint32_t count = (uint32_t)mp_obj_get_int(count_in);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(buf_in, &bi, write ? MP_BUFFER_READ : MP_BUFFER_WRITE);
    if (bi.len < (size_t)count * MOY_SD_SECTOR) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_sd: buffer too small"));
    }
    int err = moy_sd_card_io(start, (uint8_t *)bi.buf, count, write);
    if (err != 0) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("moy_sd %s failed: %d"),
                          write ? "write" : "read", err);
    }
}

static mp_obj_t moy_sd_read(mp_obj_t start, mp_obj_t buf, mp_obj_t count) {
    xfer(start, buf, count, 0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_sd_read_obj, moy_sd_read);

static mp_obj_t moy_sd_write(mp_obj_t start, mp_obj_t buf, mp_obj_t count) {
    xfer(start, buf, count, 1);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_sd_write_obj, moy_sd_write);

static mp_obj_t moy_sd_sector_count(void) {
    return mp_obj_new_int_from_uint(moy_sd_card_sectors());
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_sd_sector_count_obj, moy_sd_sector_count);

static mp_obj_t moy_sd_deinit(void) {
    moy_sd_card_release();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_sd_deinit_obj, moy_sd_deinit);

static const mp_rom_map_elem_t moy_sd_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),     MP_OBJ_NEW_QSTR(MP_QSTR_moy_sd) },
    { MP_ROM_QSTR(MP_QSTR_up),           MP_ROM_PTR(&moy_sd_up_obj) },
    { MP_ROM_QSTR(MP_QSTR_read),         MP_ROM_PTR(&moy_sd_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_write),        MP_ROM_PTR(&moy_sd_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_sector_count), MP_ROM_PTR(&moy_sd_sector_count_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit),       MP_ROM_PTR(&moy_sd_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_SECTOR_SIZE),  MP_ROM_INT(MOY_SD_SECTOR) },
};
static MP_DEFINE_CONST_DICT(moy_sd_globals, moy_sd_globals_table);

const mp_obj_module_t mp_module_moy_sd = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_sd_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_sd, mp_module_moy_sd);

#endif // ESP_IDF_VERSION
