// Moybyte moy_sd: SD card on the SPI host the display already initialized.
//
// The T-Deck shares ONE SPI host (SPI2) between the ST7789 panel (esp_lcd, via
// lcd_bus.SPIBus) and the microSD card. machine.SDCard hard-hangs the board when
// the panel is live because it calls spi_bus_initialize() again on a host esp_lcd
// already owns -- two driver stacks fighting over one peripheral.
//
// The ESP-IDF-documented fix ("Sharing the SPI Bus Among SD Cards and Other SPI
// Devices") is to initialize the bus ONCE and ATTACH each driver as a device:
// esp_lcd already ran spi_bus_initialize(), so here we only sdspi_host_init() +
// sdspi_host_init_device() (which spi_bus_add_device's the card) and probe it.
// No bus re-init, no teardown of the panel -- SD reads AND writes work while the
// display runs, as long as the caller never flushes the panel mid-transaction
// (the device desktop loop is single-threaded, so SD sessions run between frames).
//
// This exposes a thin block-device backend (init/read/write/deinit); the Python
// side (moybyte_sd._NativeSDBlockDev) wraps it for vfs.mount.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#ifdef ESP_IDF_VERSION
#include "esp_heap_caps.h"
#include "driver/sdspi_host.h"
#include "sdmmc_cmd.h"
#define MOY_SD_HAVE_IDF 1
#else
#define MOY_SD_HAVE_IDF 0
#endif

#define MOY_SD_SECTOR 512

#if MOY_SD_HAVE_IDF
static sdmmc_card_t *s_card = NULL;
static sdspi_dev_handle_t s_dev = -1;
static bool s_host_inited = false;
static uint8_t *s_bounce = NULL;  // 1-sector DMA-capable bounce (device lifetime)

static void moy_sd_release(void) {
    if (s_card != NULL) {
        free(s_card);
        s_card = NULL;
    }
    if (s_dev >= 0) {
        sdspi_host_remove_device(s_dev);
        s_dev = -1;
    }
    if (s_host_inited) {
        sdspi_host_deinit();
        s_host_inited = false;
    }
}

static void moy_sd_check(esp_err_t err, const char *what) {
    if (err != ESP_OK) {
        moy_sd_release();
        mp_raise_msg_varg(&mp_type_OSError,
                          MP_ERROR_TEXT("moy_sd %s failed: %d"), what, (int)err);
    }
}
#endif

// init(host=1, cs=39, freq_khz=20000) -> sector count.
// host is the IDF SPI host id (SPI2_HOST == 1), the SAME host esp_lcd initialized.
static mp_obj_t moy_sd_init(size_t n_args, const mp_obj_t *args) {
#if MOY_SD_HAVE_IDF
    int host = (n_args > 0) ? mp_obj_get_int(args[0]) : 1;
    int cs = (n_args > 1) ? mp_obj_get_int(args[1]) : 39;
    int freq_khz = (n_args > 2) ? mp_obj_get_int(args[2]) : 20000;

    if (s_card != NULL) {
        return mp_obj_new_int_from_uint(s_card->csd.capacity);  // already up
    }

    esp_err_t err = sdspi_host_init();
    moy_sd_check(err, "host_init");
    s_host_inited = true;

    sdspi_device_config_t devcfg = SDSPI_DEVICE_CONFIG_DEFAULT();
    devcfg.host_id = (spi_host_device_t)host;
    devcfg.gpio_cs = (gpio_num_t)cs;
    err = sdspi_host_init_device(&devcfg, &s_dev);
    moy_sd_check(err, "init_device");

    sdmmc_host_t hostcfg = SDSPI_HOST_DEFAULT();
    hostcfg.slot = s_dev;
    hostcfg.max_freq_khz = freq_khz;

    s_card = (sdmmc_card_t *)malloc(sizeof(sdmmc_card_t));
    if (s_card == NULL) {
        moy_sd_release();
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_sd: out of memory"));
    }
    err = sdmmc_card_init(&hostcfg, s_card);
    moy_sd_check(err, "card_init");

    if (s_bounce == NULL) {
        s_bounce = (uint8_t *)heap_caps_malloc(MOY_SD_SECTOR, MALLOC_CAP_DMA);
        if (s_bounce == NULL) {
            moy_sd_release();
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_sd: no DMA bounce"));
        }
    }
    return mp_obj_new_int_from_uint(s_card->csd.capacity);
#else
    (void)n_args;
    (void)args;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_sd needs ESP-IDF"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_sd_init_obj, 0, 3, moy_sd_init);

#if MOY_SD_HAVE_IDF
// Copy `count` sectors through the DMA bounce so the caller's buffer can live
// anywhere (PSRAM bytearray, unaligned, etc.) without breaking sdmmc DMA.
static void moy_sd_require(void) {
    if (s_card == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_sd: not mounted"));
    }
}
#endif

// read(start_block, buf, count) -> None. buf must hold count*512 bytes.
static mp_obj_t moy_sd_read(mp_obj_t start_in, mp_obj_t buf_in, mp_obj_t count_in) {
#if MOY_SD_HAVE_IDF
    moy_sd_require();
    uint32_t start = (uint32_t)mp_obj_get_int(start_in);
    uint32_t count = (uint32_t)mp_obj_get_int(count_in);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(buf_in, &bi, MP_BUFFER_WRITE);
    if (bi.len < (size_t)count * MOY_SD_SECTOR) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_sd: read buffer too small"));
    }
    uint8_t *dst = (uint8_t *)bi.buf;
    for (uint32_t i = 0; i < count; i++) {
        esp_err_t err = sdmmc_read_sectors(s_card, s_bounce, start + i, 1);
        moy_sd_check(err, "read");
        memcpy(dst + (size_t)i * MOY_SD_SECTOR, s_bounce, MOY_SD_SECTOR);
    }
    return mp_const_none;
#else
    (void)start_in; (void)buf_in; (void)count_in;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_sd needs ESP-IDF"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_sd_read_obj, moy_sd_read);

// write(start_block, buf, count) -> None. buf must hold count*512 bytes.
static mp_obj_t moy_sd_write(mp_obj_t start_in, mp_obj_t buf_in, mp_obj_t count_in) {
#if MOY_SD_HAVE_IDF
    moy_sd_require();
    uint32_t start = (uint32_t)mp_obj_get_int(start_in);
    uint32_t count = (uint32_t)mp_obj_get_int(count_in);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(buf_in, &bi, MP_BUFFER_READ);
    if (bi.len < (size_t)count * MOY_SD_SECTOR) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_sd: write buffer too small"));
    }
    const uint8_t *src = (const uint8_t *)bi.buf;
    for (uint32_t i = 0; i < count; i++) {
        memcpy(s_bounce, src + (size_t)i * MOY_SD_SECTOR, MOY_SD_SECTOR);
        esp_err_t err = sdmmc_write_sectors(s_card, s_bounce, start + i, 1);
        moy_sd_check(err, "write");
    }
    return mp_const_none;
#else
    (void)start_in; (void)buf_in; (void)count_in;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_sd needs ESP-IDF"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_sd_write_obj, moy_sd_write);

// sector_count() -> total 512-byte sectors on the card (0 if not mounted).
static mp_obj_t moy_sd_sector_count(void) {
#if MOY_SD_HAVE_IDF
    return mp_obj_new_int_from_uint(s_card ? s_card->csd.capacity : 0);
#else
    return mp_obj_new_int(0);
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_sd_sector_count_obj, moy_sd_sector_count);

// deinit() -> None. Removes only the SD device + sdspi driver; the esp_lcd panel
// device on the same bus is untouched, so the display keeps working after.
static mp_obj_t moy_sd_deinit(void) {
#if MOY_SD_HAVE_IDF
    moy_sd_release();
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_sd_deinit_obj, moy_sd_deinit);

static const mp_rom_map_elem_t moy_sd_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),     MP_OBJ_NEW_QSTR(MP_QSTR_moy_sd) },
    { MP_ROM_QSTR(MP_QSTR_init),         MP_ROM_PTR(&moy_sd_init_obj) },
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
