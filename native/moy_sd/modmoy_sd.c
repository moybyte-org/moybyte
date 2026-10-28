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
// It exposes the card as sectors (init/read/write/deinit), and as the C calls
// native/moy_store's owned card volume reads through (moy_sd_card_io): the
// store's FATFS over its own read cache, mounted at /sd (moybyte_sd).
//
// `open` is the same card on a host of its own (the Guition S3's SPI3): the
// bus is initialised here, once, and never torn down -- a machine.SDCard's
// finaliser frees its host at a VM stop, which the kernel's store must
// outlive. `mmc` is the card on an SDMMC slot (the P4s' slot 0), likewise
// initialised once and kept: the host is shared with the C6's slot 1, so only
// the card's slot is ever deinitialised, and only by a failed bring-up.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#ifdef ESP_IDF_VERSION
#include "esp_heap_caps.h"
#include "driver/sdspi_host.h"
#include "driver/spi_master.h"
#include "sdmmc_cmd.h"
#include "soc/soc_caps.h"
#if SOC_SDMMC_HOST_SUPPORTED
#include "driver/sdmmc_host.h"
#endif
#define MOY_SD_HAVE_IDF 1
#else
#define MOY_SD_HAVE_IDF 0
#endif

#define MOY_SD_SECTOR 512
// Sectors per multi-block transfer: a 16 KB bounce, taken per call and halved
// until internal DMA memory can give it. The card holds no bounce between
// calls.
#define MOY_SD_RUN 32

#if MOY_SD_HAVE_IDF
static sdmmc_card_t *s_card = NULL;
static sdspi_dev_handle_t s_dev = -1;
static bool s_host_inited = false;
static int s_mmc_slot = -1;
// Where a transfer's bounce comes from: internal DMA memory, unless the host
// can reach PSRAM (the P4's SDMMC), when it is PSRAM on cache-line bounds.
static uint32_t s_bounce_caps = MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL;

static void moy_sd_release(void) {
    if (s_card != NULL) {
        free(s_card);
        s_card = NULL;
    }
#if SOC_SDMMC_HOST_SUPPORTED
    if (s_mmc_slot >= 0) {
        sdmmc_host_deinit_slot(s_mmc_slot);
        s_mmc_slot = -1;
    }
#endif
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
#if MOY_SD_HAVE_IDF
static bool s_bus_ours = false;
#endif

static mp_obj_t moy_sd_init(size_t n_args, const mp_obj_t *args);

// open(host, sck, mosi, miso, cs, freq_khz) -> sectors: the bus initialised
// here, once (a second open returns the card already up), then init's attach.
static mp_obj_t moy_sd_open(size_t n_args, const mp_obj_t *args) {
#if MOY_SD_HAVE_IDF
    if (s_card != NULL) {
        return mp_obj_new_int_from_uint(s_card->csd.capacity);
    }
    int host = mp_obj_get_int(args[0]);
    if (!s_bus_ours) {
        spi_bus_config_t bus = {
            .sclk_io_num = mp_obj_get_int(args[1]),
            .mosi_io_num = mp_obj_get_int(args[2]),
            .miso_io_num = mp_obj_get_int(args[3]),
            .quadwp_io_num = -1,
            .quadhd_io_num = -1,
            .max_transfer_sz = MOY_SD_RUN * MOY_SD_SECTOR,
        };
        esp_err_t err = spi_bus_initialize((spi_host_device_t)host, &bus, SPI_DMA_CH_AUTO);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("moy_sd bus failed: %d"),
                              (int)err);
        }
        s_bus_ours = true;
    }
    mp_obj_t a[3] = { args[0], args[4], n_args > 5 ? args[5] : MP_OBJ_NEW_SMALL_INT(20000) };
    return moy_sd_init(3, a);
#else
    (void)n_args;
    (void)args;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_sd needs ESP-IDF"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_sd_open_obj, 5, 6, moy_sd_open);

#if MOY_SD_HAVE_IDF
// The card's state is no DMA buffer: PSRAM, so an idle card holds no internal
// SRAM of its own.
static sdmmc_card_t *moy_sd_card_state(void) {
    sdmmc_card_t *c = (sdmmc_card_t *)heap_caps_malloc(sizeof(sdmmc_card_t),
                                                        MALLOC_CAP_SPIRAM);
    if (c == NULL) {
        c = (sdmmc_card_t *)malloc(sizeof(sdmmc_card_t));
    }
    if (c == NULL) {
        moy_sd_release();
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_sd: out of memory"));
    }
    return c;
}
#endif

// mmc(slot, clk, cmd, data, freq_khz=20000) -> sectors: the card on an SDMMC
// slot, `data` its one or four data pins. The host is initialised here or by
// whoever already holds another slot (ESP-Hosted's), and the slot once; a
// second call returns the card already up.
static mp_obj_t moy_sd_mmc(size_t n_args, const mp_obj_t *args) {
#if MOY_SD_HAVE_IDF && SOC_SDMMC_HOST_SUPPORTED
    if (s_card != NULL) {
        return mp_obj_new_int_from_uint(s_card->csd.capacity);
    }
    int slot = mp_obj_get_int(args[0]);
    size_t width;
    mp_obj_t *data;
    mp_obj_get_array(args[3], &width, &data);
    if (width != 1 && width != 4) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_sd: 1 or 4 data pins"));
    }
    sdmmc_slot_config_t cfg = SDMMC_SLOT_CONFIG_DEFAULT();
    cfg.width = width;
    cfg.clk = (gpio_num_t)mp_obj_get_int(args[1]);
    cfg.cmd = (gpio_num_t)mp_obj_get_int(args[2]);
    cfg.d0 = (gpio_num_t)mp_obj_get_int(data[0]);
    if (width == 4) {
        cfg.d1 = (gpio_num_t)mp_obj_get_int(data[1]);
        cfg.d2 = (gpio_num_t)mp_obj_get_int(data[2]);
        cfg.d3 = (gpio_num_t)mp_obj_get_int(data[3]);
    }
    esp_err_t err = sdmmc_host_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        moy_sd_check(err, "mmc host_init");
    }
    err = sdmmc_host_init_slot(slot, &cfg);
    moy_sd_check(err, "mmc slot");
    s_mmc_slot = slot;
#if SOC_SDMMC_PSRAM_DMA_CAPABLE
    s_bounce_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_CACHE_ALIGNED;
#endif

    sdmmc_host_t hostcfg = SDMMC_HOST_DEFAULT();
    hostcfg.slot = slot;
    hostcfg.max_freq_khz = n_args > 4 ? mp_obj_get_int(args[4]) : 20000;
    s_card = moy_sd_card_state();
    err = sdmmc_card_init(&hostcfg, s_card);
    moy_sd_check(err, "card_init");
    return mp_obj_new_int_from_uint(s_card->csd.capacity);
#else
    (void)n_args;
    (void)args;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_sd: no SDMMC host"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_sd_mmc_obj, 4, 5, moy_sd_mmc);

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

    s_card = moy_sd_card_state();
    err = sdmmc_card_init(&hostcfg, s_card);
    moy_sd_check(err, "card_init");

    return mp_obj_new_int_from_uint(s_card->csd.capacity);
#else
    (void)n_args;
    (void)args;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_sd needs ESP-IDF"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_sd_init_obj, 0, 3, moy_sd_init);

#if MOY_SD_HAVE_IDF
static void moy_sd_require(void) {
    if (s_card == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_sd: not mounted"));
    }
}

// `count` sectors from or to `buf`, which may live anywhere (a PSRAM
// bytearray, unaligned), through a bounce the host's DMA reaches: internal
// DMA memory on SPI, aligned PSRAM on the P4's SDMMC. A run of sectors is one
// multi-block command: on a write the card's busy time is paid once per run
// instead of once per sector, and that wait is most of what a single-block
// write costs. The run's bounce is taken for this call and given back; when
// its memory cannot give it the run halves, down to one sector, and
// with not even that the call fails with ESP_ERR_NO_MEM.
// The card's sectors moved, raising nothing: ESP_OK or the driver's error.
// What native/moy_store's card volume reads and writes through.
int moy_sd_card_io(uint32_t start, uint8_t *buf, uint32_t count, int write) {
    if (s_card == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (count == 0) {
        return ESP_OK;
    }
    uint32_t run = count < MOY_SD_RUN ? count : MOY_SD_RUN;
    uint8_t *bounce = NULL;
    while (run > 0) {
        bounce = heap_caps_malloc((size_t)run * MOY_SD_SECTOR, s_bounce_caps);
        if (bounce != NULL) {
            break;
        }
        run /= 2;
    }
    if (bounce == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = ESP_OK;
    for (uint32_t i = 0; i < count && err == ESP_OK; i += run) {
        uint32_t n = count - i < run ? count - i : run;
        uint8_t *at = buf + (size_t)i * MOY_SD_SECTOR;
        if (write) {
            memcpy(bounce, at, (size_t)n * MOY_SD_SECTOR);
            err = sdmmc_write_sectors(s_card, bounce, start + i, n);
        } else {
            err = sdmmc_read_sectors(s_card, bounce, start + i, n);
            if (err == ESP_OK) {
                memcpy(at, bounce, (size_t)n * MOY_SD_SECTOR);
            }
        }
    }
    heap_caps_free(bounce);
    return err;
}

static void moy_sd_xfer(uint32_t start, uint8_t *buf, uint32_t count, bool write) {
    esp_err_t err = moy_sd_card_io(start, buf, count, write);
    moy_sd_check(err, write ? "write" : "read");
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
    moy_sd_xfer(start, (uint8_t *)bi.buf, count, false);
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
    moy_sd_xfer(start, (uint8_t *)bi.buf, count, true);
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
    { MP_ROM_QSTR(MP_QSTR_open),         MP_ROM_PTR(&moy_sd_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_mmc),          MP_ROM_PTR(&moy_sd_mmc_obj) },
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
