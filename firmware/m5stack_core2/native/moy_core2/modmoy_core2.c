// moy_core2: the M5Stack Core2 v1.1's AXP2101 power IC, on the kernel's I2C
// bus (native/moy_kernel/moy_bus.h) at 0x34.
//
// On this board the AXP2101 is the panel's power and reset, its backlight and
// the speaker amplifier's enable, so the panel module (the T-Deck's moy_lcd,
// taken) calls the two hooks below through MOY_LCD_POWER and
// MOY_LCD_BACKLIGHT (mpconfigboard.h) -- from the kernel's recovery floor too,
// before any VM, which is why this is C over the kernel's bus.
//
// The rails, read off this glass's factory configuration 2026-11-01 (LDO
// enable register 0x90 = 0x7B, every voltage register 0x1C = 3.3 V) against
// M5Stack's own M5Unified/M5GFX assignments for the Core2 v1.1:
//
//   ALDO1  0x01  3.3 V   on   (always-on peripherals)
//   ALDO2  0x02  3.3 V   on   the panel's and the touch's reset line: off->on
//                             is the panel reset
//   ALDO3  0x04  3.3 V        the NS4168 speaker amplifier
//   ALDO4  0x08  3.3 V   on   the TF slot
//   BLDO1  0x10  2.5..3.3 V   the panel backlight (its voltage is brightness)
//   BLDO2  0x20  3.3 V   on   the panel's logic rail
//   DLDO1  0x80               the vibration motor; never turned on here
//
// The AXP2101 keeps its registers across an ESP32 reset (it is powered by the
// battery and USB, not by the ESP32), so every write here states the full
// target rather than toggling what a previous image left.

#include <stdint.h>

#include "py/mperrno.h"
#include "py/runtime.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_err.h"

#include "moy_bus.h"

#define AXP_ADDR        0x34
#define AXP_TIMEOUT_MS  50
#define AXP_CHIP_ID     0x03
#define AXP_LDO_EN      0x90
#define AXP_ALDO1_V     0x92
#define AXP_BLDO1_V     0x96
#define AXP_BLDO2_V     0x97

#define LDO_ALDO1       0x01
#define LDO_ALDO2       0x02
#define LDO_ALDO3       0x04
#define LDO_ALDO4       0x08
#define LDO_BLDO1       0x10
#define LDO_BLDO2       0x20
#define LDO_DLDO1       0x80

#define V_3V3           0x1C    // (3.3 V - 0.5 V) / 100 mV
#define V_BACKLIGHT     0x1A    // 3.1 V: M5GFX's default brightness, near full

static uint8_t s_dev;
static bool s_up;

static int axp_dev(void) {
    if (!s_up) {
        if (moy_bus_add(AXP_ADDR, AXP_TIMEOUT_MS, &s_dev) != MOY_BUS_OK) {
            return ESP_FAIL;
        }
        s_up = true;
    }
    return ESP_OK;
}

static int axp_read(uint8_t reg, uint8_t *v) {
    if (axp_dev() != ESP_OK) {
        return ESP_FAIL;
    }
    return moy_bus_write_read(s_dev, &reg, 1, v, 1) == MOY_BUS_OK ? ESP_OK : ESP_FAIL;
}

static int axp_write(uint8_t reg, uint8_t v) {
    if (axp_dev() != ESP_OK) {
        return ESP_FAIL;
    }
    uint8_t w[2] = {reg, v};
    return moy_bus_write(s_dev, w, 2) == MOY_BUS_OK ? ESP_OK : ESP_FAIL;
}

static int axp_ldo(uint8_t set, uint8_t clear) {
    uint8_t v;
    if (axp_read(AXP_LDO_EN, &v) != ESP_OK) {
        return ESP_FAIL;
    }
    return axp_write(AXP_LDO_EN, (uint8_t)((v | set) & ~clear));
}

// The panel's power-up, called by moy_lcd before its bus exists: every rail
// the board runs at 3.3 V, the speaker's included, the backlight left off
// (moy_lcd lights it after the first frame), and the panel reset as an ALDO2
// off -> on pulse with the ILI9342C's 120 ms wake after it.
int moy_core2_power(void) {
    // The first read may meet a bus a reset left mid-transaction (a device
    // still holding SDA low): the IDF's driver clears the bus after a failed
    // transfer, so the read is tried again before the AXP2101 is called
    // absent.
    uint8_t id;
    int tries = 0;
    while (axp_read(AXP_CHIP_ID, &id) != ESP_OK) {
        if (++tries >= 4) {
            return ESP_ERR_NOT_FOUND;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    for (uint8_t r = AXP_ALDO1_V; r < AXP_BLDO1_V; r++) {     // ALDO1..ALDO4
        if (axp_write(r, V_3V3) != ESP_OK) {
            return ESP_FAIL;
        }
    }
    if (axp_write(AXP_BLDO2_V, V_3V3) != ESP_OK
        || axp_write(AXP_BLDO1_V, V_BACKLIGHT) != ESP_OK
        || axp_ldo(LDO_ALDO1 | LDO_ALDO3 | LDO_ALDO4 | LDO_BLDO2,
                   LDO_ALDO2 | LDO_BLDO1 | LDO_DLDO1) != ESP_OK) {
        return ESP_FAIL;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
    if (axp_ldo(LDO_ALDO2, 0) != ESP_OK) {
        return ESP_FAIL;
    }
    vTaskDelay(pdMS_TO_TICKS(120));
    return ESP_OK;
}

void moy_core2_backlight(int on) {
    (void)axp_ldo(on ? LDO_BLDO1 : 0, on ? 0 : LDO_BLDO1);
}

// ---- the bring-up module: the AXP2101's registers from the REPL -----------

// reg(r[, v]) -> the register's value, after writing v when given.
static mp_obj_t moy_core2_reg(size_t n_args, const mp_obj_t *args) {
    uint8_t r = (uint8_t)mp_obj_get_int(args[0]);
    uint8_t v;
    if (n_args > 1 && axp_write(r, (uint8_t)mp_obj_get_int(args[1])) != ESP_OK) {
        mp_raise_OSError(MP_EIO);
    }
    if (axp_read(r, &v) != ESP_OK) {
        mp_raise_OSError(MP_EIO);
    }
    return MP_OBJ_NEW_SMALL_INT(v);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_core2_reg_obj, 1, 2, moy_core2_reg);

static const mp_rom_map_elem_t moy_core2_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_core2) },
    { MP_ROM_QSTR(MP_QSTR_reg), MP_ROM_PTR(&moy_core2_reg_obj) },
};
static MP_DEFINE_CONST_DICT(moy_core2_globals, moy_core2_globals_table);

const mp_obj_module_t mp_module_moy_core2 = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_core2_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_core2, mp_module_moy_core2);
