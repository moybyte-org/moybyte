// moy_bus: the kernel's I2C master bus. moy_bus.h has the contract.

#include "moy_bus.h"

// ESP_PLATFORM is not defined for a usermod's sources on the esp32 port, so a
// board is recognised by the header it has.
#if defined(__has_include)
#if __has_include("driver/i2c.h")
#define MOY_BUS_BOARD 1
#endif
#endif

#ifdef MOY_BUS_BOARD
#include "py/mpconfig.h"        // the board's MOY_BUS_I2C_* (mpconfigboard.h)
#endif

#if defined(MOY_BUS_BOARD) && defined(MOY_BUS_I2C_PORT)

#include <stdbool.h>

#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#ifdef MOY_BUS_I2C_STRETCH_US
#include "esp_clk_tree.h"
#include "hal/i2c_ll.h"
#endif
#include "freertos/semphr.h"

typedef struct {
    uint8_t addr;
    TickType_t timeout;
} moy_bus_dev_t;

static moy_bus_dev_t s_dev[MOY_BUS_DEVICES];
static uint8_t s_ndev;
static bool s_up;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

static int bus_up(void) {
    if (s_up) {
        return MOY_BUS_OK;
    }
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = MOY_BUS_I2C_SDA,
        .scl_io_num = MOY_BUS_I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = MOY_BUS_I2C_HZ,
    };
    if (i2c_param_config(MOY_BUS_I2C_PORT, &conf) != ESP_OK) {
        return MOY_BUS_ERR;
    }
    #ifdef MOY_BUS_I2C_STRETCH_US
    uint32_t sclk = 0;
    esp_clk_tree_src_get_freq_hz(I2C_CLK_SRC_DEFAULT, ESP_CLK_TREE_SRC_FREQ_PRECISION_APPROX, &sclk);
    int to = i2c_ll_calculate_timeout_us_to_reg_val(sclk, MOY_BUS_I2C_STRETCH_US);
    i2c_set_timeout(MOY_BUS_I2C_PORT, to > I2C_LL_MAX_TIMEOUT ? I2C_LL_MAX_TIMEOUT : to);
    #endif
    if (i2c_driver_install(MOY_BUS_I2C_PORT, I2C_MODE_MASTER, 0, 0, 0) != ESP_OK) {
        return MOY_BUS_ERR;
    }
    s_up = true;
    return MOY_BUS_OK;
}

int moy_bus_add(uint8_t addr, uint32_t timeout_ms, uint8_t *dev) {
    int r = bus_up();
    if (r != MOY_BUS_OK) {
        return r;
    }
    TickType_t t = pdMS_TO_TICKS(timeout_ms);
    portENTER_CRITICAL(&s_lock);
    for (uint8_t i = 0; i < s_ndev; i++) {
        if (s_dev[i].addr == addr) {
            s_dev[i].timeout = t;
            portEXIT_CRITICAL(&s_lock);
            *dev = i;
            return MOY_BUS_OK;
        }
    }
    if (s_ndev == MOY_BUS_DEVICES) {
        portEXIT_CRITICAL(&s_lock);
        return MOY_BUS_FULL;
    }
    s_dev[s_ndev].addr = addr;
    s_dev[s_ndev].timeout = t;
    *dev = s_ndev++;
    portEXIT_CRITICAL(&s_lock);
    return MOY_BUS_OK;
}

static const moy_bus_dev_t *dev_of(uint8_t dev) {
    return dev < s_ndev ? &s_dev[dev] : NULL;
}

int moy_bus_write(uint8_t dev, const uint8_t *src, size_t n) {
    const moy_bus_dev_t *d = dev_of(dev);
    if (d == NULL) {
        return MOY_BUS_ERR;
    }
    return i2c_master_write_to_device(MOY_BUS_I2C_PORT, d->addr, src, n, d->timeout)
           == ESP_OK ? MOY_BUS_OK : MOY_BUS_ERR;
}

int moy_bus_read(uint8_t dev, uint8_t *dst, size_t n) {
    const moy_bus_dev_t *d = dev_of(dev);
    if (d == NULL) {
        return MOY_BUS_ERR;
    }
    return i2c_master_read_from_device(MOY_BUS_I2C_PORT, d->addr, dst, n, d->timeout)
           == ESP_OK ? MOY_BUS_OK : MOY_BUS_ERR;
}

int moy_bus_write_read(uint8_t dev, const uint8_t *src, size_t n_src,
                       uint8_t *dst, size_t n_dst) {
    const moy_bus_dev_t *d = dev_of(dev);
    if (d == NULL) {
        return MOY_BUS_ERR;
    }
    return i2c_master_write_read_device(MOY_BUS_I2C_PORT, d->addr, src, n_src,
                                        dst, n_dst, d->timeout)
           == ESP_OK ? MOY_BUS_OK : MOY_BUS_ERR;
}

#else

int moy_bus_add(uint8_t addr, uint32_t timeout_ms, uint8_t *dev) {
    (void)addr;
    (void)timeout_ms;
    (void)dev;
    return MOY_BUS_NONE;
}

int moy_bus_write(uint8_t dev, const uint8_t *src, size_t n) {
    (void)dev;
    (void)src;
    (void)n;
    return MOY_BUS_NONE;
}

int moy_bus_read(uint8_t dev, uint8_t *dst, size_t n) {
    (void)dev;
    (void)dst;
    (void)n;
    return MOY_BUS_NONE;
}

int moy_bus_write_read(uint8_t dev, const uint8_t *src, size_t n_src,
                       uint8_t *dst, size_t n_dst) {
    (void)dev;
    (void)src;
    (void)n_src;
    (void)dst;
    (void)n_dst;
    return MOY_BUS_NONE;
}

#endif
