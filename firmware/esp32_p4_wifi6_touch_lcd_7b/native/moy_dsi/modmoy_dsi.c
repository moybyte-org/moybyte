// Moybyte P4 port (#58): EK79007 MIPI-DSI panel backend for the Waveshare
// ESP32-P4-WIFI6-Touch-LCD-7B (1024x600, 2-lane DSI @ 900Mbps).
//
// DPI mode: the DSI peripheral continuously scans out a PSRAM framebuffer --
// there is no per-frame flush transfer (the T-Deck's ~28ms tx_color ceiling
// does not exist here). Python draws into the framebuffer returned by fb()
// and calls flush() so the DPI DMA sees the CPU's cached writes.

#include "py/runtime.h"
#include "py/objarray.h"

#include "esp_ldo_regulator.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_ek79007.h"
#include "esp_cache.h"
#include "esp_attr.h"

#define MOY_DSI_H_RES        1024
#define MOY_DSI_V_RES        600
#define MOY_DSI_FB_BYTES     (MOY_DSI_H_RES * MOY_DSI_V_RES * 2) // RGB565
#define MOY_DSI_LCD_RST_GPIO 33   // 7B board (xiaozhi/Waveshare board config)
#define MOY_DSI_PHY_LDO_CHAN 3    // MIPI DSI PHY power rail
#define MOY_DSI_PHY_LDO_MV   2500

static esp_ldo_channel_handle_t s_phy_ldo;
static esp_lcd_dsi_bus_handle_t s_bus;
static esp_lcd_panel_io_handle_t s_io;
static esp_lcd_panel_handle_t s_panel;
static void *s_fb;          // fb 0 (kept for the single-buffer flush() compat path)
static void *s_fbs[2];      // DOUBLE-BUFFER (#58): the DPI panel owns 2 framebuffers;
static int s_nfbs;          // show(n) switches scan-out zero-copy (draw_bitmap with an
                            // internal fb pointer), so a full redraw never races the
                            // scan (the "everything visibly refreshes" tearing).
static volatile uint32_t s_underruns;

// Strong implementation of ESP-IDF's P4-build weak diagnostic hook. ISR-safe:
// one internal-RAM counter increment, with all Python/serial work deferred.
IRAM_ATTR void moy_dsi_note_underrun(void) {
    s_underruns++;
}

static void moy_dsi_check(esp_err_t err, const char *what) {
    if (err != ESP_OK) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("moy_dsi %s: err 0x%x"), what, (unsigned)err);
    }
}

static mp_obj_t moy_dsi_init(void) {
    if (s_panel != NULL) {
        return mp_const_none;
    }

    s_underruns = 0;

    esp_ldo_channel_config_t ldo_cfg = {
        .chan_id = MOY_DSI_PHY_LDO_CHAN,
        .voltage_mv = MOY_DSI_PHY_LDO_MV,
    };
    moy_dsi_check(esp_ldo_acquire_channel(&ldo_cfg, &s_phy_ldo), "phy ldo");

    esp_lcd_dsi_bus_config_t bus_cfg = EK79007_PANEL_BUS_DSI_2CH_CONFIG();
    moy_dsi_check(esp_lcd_new_dsi_bus(&bus_cfg, &s_bus), "dsi bus");

    esp_lcd_dbi_io_config_t dbi_cfg = EK79007_PANEL_IO_DBI_CONFIG();
    moy_dsi_check(esp_lcd_new_panel_io_dbi(s_bus, &dbi_cfg, &s_io), "dbi io");

    esp_lcd_dpi_panel_config_t dpi_cfg = EK79007_1024_600_PANEL_60HZ_CONFIG(LCD_COLOR_PIXEL_FORMAT_RGB565);
    dpi_cfg.num_fbs = 2;    // double-buffer: 2x 1.2MB PSRAM (the board has 32MB)
    ek79007_vendor_config_t vendor_cfg = {
        .mipi_config = {
            .dsi_bus = s_bus,
            .dpi_config = &dpi_cfg,
            .lane_num = 2,
        },
    };
    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = MOY_DSI_LCD_RST_GPIO,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
        .vendor_config = &vendor_cfg,
    };
    moy_dsi_check(esp_lcd_new_panel_ek79007(s_io, &panel_cfg, &s_panel), "panel new");
    moy_dsi_check(esp_lcd_panel_reset(s_panel), "panel reset");
    moy_dsi_check(esp_lcd_panel_init(s_panel), "panel init");
    moy_dsi_check(esp_lcd_dpi_panel_get_frame_buffer(s_panel, 2, &s_fbs[0], &s_fbs[1]), "get fbs");
    s_nfbs = 2;
    s_fb = s_fbs[0];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_dsi_init_obj, moy_dsi_init);

static mp_obj_t moy_dsi_deinit(void) {
    if (s_panel) {
        esp_lcd_panel_del(s_panel);
        s_panel = NULL;
        s_fb = NULL;
        s_fbs[0] = s_fbs[1] = NULL;
        s_nfbs = 0;
    }
    if (s_io) {
        esp_lcd_panel_io_del(s_io);
        s_io = NULL;
    }
    if (s_bus) {
        esp_lcd_del_dsi_bus(s_bus);
        s_bus = NULL;
    }
    if (s_phy_ldo) {
        esp_ldo_release_channel(s_phy_ldo);
        s_phy_ldo = NULL;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_dsi_deinit_obj, moy_dsi_deinit);

// fb([n]) -> writable memoryview over framebuffer n (default 0).
static mp_obj_t moy_dsi_fb(size_t n_args, const mp_obj_t *a) {
    if (s_nfbs == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_dsi not initialized"));
    }
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    return mp_obj_new_memoryview('B' | MP_OBJ_ARRAY_TYPECODE_FLAG_RW, MOY_DSI_FB_BYTES, s_fbs[n]);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_dsi_fb_obj, 0, 1, moy_dsi_fb);

static mp_obj_t moy_dsi_nfbs(void) {
    return MP_OBJ_NEW_SMALL_INT(s_nfbs);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_dsi_nfbs_obj, moy_dsi_nfbs);

static mp_obj_t moy_dsi_underruns(void) {
    return mp_obj_new_int_from_uint(s_underruns);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_dsi_underruns_obj, moy_dsi_underruns);

// show(n): make framebuffer n the scan-out source -- msync its CPU-cached writes,
// then a zero-copy draw_bitmap (the DPI driver recognizes its own fb pointer and
// just switches buffers at the next VSYNC; no pixel copy).
static mp_obj_t moy_dsi_show(mp_obj_t n_in) {
    if (s_panel == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_dsi not initialized"));
    }
    mp_int_t n = mp_obj_get_int(n_in);
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    moy_dsi_check(esp_cache_msync(s_fbs[n], MOY_DSI_FB_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_C2M), "msync");
    moy_dsi_check(esp_lcd_panel_draw_bitmap(s_panel, 0, 0, MOY_DSI_H_RES, MOY_DSI_V_RES, s_fbs[n]),
                  "show");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_dsi_show_obj, moy_dsi_show);

// Push CPU cache to memory so the DPI scan-out DMA sees the writes.
static mp_obj_t moy_dsi_flush(void) {
    if (s_fb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_dsi not initialized"));
    }
    moy_dsi_check(esp_cache_msync(s_fb, MOY_DSI_FB_BYTES, ESP_CACHE_MSYNC_FLAG_DIR_C2M), "msync");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_dsi_flush_obj, moy_dsi_flush);

// Hardware test pattern: 0 = none, 1 = vertical bars, 2 = horizontal bars.
static mp_obj_t moy_dsi_set_pattern(mp_obj_t pat_in) {
    if (s_panel == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_dsi not initialized"));
    }
    mp_int_t pat = mp_obj_get_int(pat_in);
    mipi_dsi_pattern_type_t types[] = {
        MIPI_DSI_PATTERN_NONE, MIPI_DSI_PATTERN_BAR_VERTICAL, MIPI_DSI_PATTERN_BAR_HORIZONTAL,
    };
    if (pat < 0 || pat > 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("pattern 0..2"));
    }
    moy_dsi_check(esp_lcd_dpi_panel_set_pattern(s_panel, types[pat]), "pattern");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_dsi_set_pattern_obj, moy_dsi_set_pattern);

static const mp_rom_map_elem_t moy_dsi_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_dsi) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&moy_dsi_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&moy_dsi_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_fb), MP_ROM_PTR(&moy_dsi_fb_obj) },
    { MP_ROM_QSTR(MP_QSTR_nfbs), MP_ROM_PTR(&moy_dsi_nfbs_obj) },
    { MP_ROM_QSTR(MP_QSTR_underruns), MP_ROM_PTR(&moy_dsi_underruns_obj) },
    { MP_ROM_QSTR(MP_QSTR_show), MP_ROM_PTR(&moy_dsi_show_obj) },
    { MP_ROM_QSTR(MP_QSTR_flush), MP_ROM_PTR(&moy_dsi_flush_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_pattern), MP_ROM_PTR(&moy_dsi_set_pattern_obj) },
    { MP_ROM_QSTR(MP_QSTR_WIDTH), MP_ROM_INT(MOY_DSI_H_RES) },
    { MP_ROM_QSTR(MP_QSTR_HEIGHT), MP_ROM_INT(MOY_DSI_V_RES) },
};
static MP_DEFINE_CONST_DICT(moy_dsi_module_globals, moy_dsi_module_globals_table);

const mp_obj_module_t moy_dsi_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_dsi_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_dsi, moy_dsi_module);
