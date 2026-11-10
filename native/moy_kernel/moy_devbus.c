// moy_devbus: the buses a device handle drives. moy_devbus.h has the contract.

#include "moy_devbus.h"

#include <string.h>

#include "moy_dev.h"
#include "moy_lease.h"
#include "moy_ledc.h"

// A board is known by the header it has (moy_lease.c does the same).
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define DEVBUS_BOARD 1
#endif
#endif

#ifdef DEVBUS_BOARD

#include "py/mpconfig.h"        // the board's MOY_BUS_I2C_PORT (mpconfigboard.h)
#include "driver/i2c.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "driver/uart.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_clk_tree.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"

#if defined(MOY_BUS_I2C_PORT) && MOY_BUS_I2C_PORT == 1
#define MOY_DEVBUS_I2C_PORT I2C_NUM_0
#else
#define MOY_DEVBUS_I2C_PORT I2C_NUM_1
#endif
#define I2C_WAIT pdMS_TO_TICKS(50)      // machine.I2C's own timeout

// A timer's clock and duty bits are machine.PWM's choice for the frequency
// (ports/esp32/machine_pwm.c's set_freq), so a timer either configured
// carries a channel of the other at that frequency as it is.
#if SOC_LEDC_SUPPORT_APB_CLOCK
#define PWM_CLK LEDC_USE_APB_CLK
#elif SOC_LEDC_SUPPORT_PLL_DIV_CLOCK
#define PWM_CLK LEDC_USE_PLL_DIV_CLK
#else
#define PWM_CLK LEDC_USE_XTAL_CLK
#endif

static uint32_t s_txbuf[MOY_DEVBUS_UARTS];

static int32_t esp_rc(esp_err_t e) {
    return e == ESP_OK ? 0 : e == ESP_ERR_NO_MEM ? MOY_DEV_ENOMEM
           : e == ESP_ERR_INVALID_ARG ? MOY_DEV_EBAD
           : e == ESP_ERR_INVALID_STATE || e == ESP_ERR_NOT_FOUND ? MOY_DEV_EBUSY : MOY_DEV_EIO;
}

// -- uart ----------------------------------------------------------------------------

static int32_t uart_cfg(uint8_t unit, const moy_devbus_uart_t *c) {
    uart_config_t uc = {
        .baud_rate = (int)c->baud,
        .data_bits = (uart_word_length_t)(UART_DATA_5_BITS + (c->bits - 5u)),
        .parity = c->parity == MOY_DEVBUS_PARITY_EVEN ? UART_PARITY_EVEN
                  : c->parity == MOY_DEVBUS_PARITY_ODD ? UART_PARITY_ODD : UART_PARITY_DISABLE,
        .stop_bits = c->stop == 2u ? UART_STOP_BITS_2 : UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    return esp_rc(uart_param_config(unit, &uc));
}

int32_t moy_devbus_uart_open(uint8_t unit, int tx, int rx, const moy_devbus_uart_t *c) {
    if (unit >= UART_NUM_MAX || unit >= MOY_DEVBUS_UARTS) {
        return MOY_DEV_EBAD;
    }
    if (uart_is_driver_installed(unit)) {
        return MOY_DEV_EBUSY;
    }
    uint32_t rxbuf = c->rxbuf > UART_HW_FIFO_LEN(unit) ? c->rxbuf : UART_HW_FIFO_LEN(unit) + 1u;
    uint32_t txbuf = c->txbuf == 0u || c->txbuf > UART_HW_FIFO_LEN(unit)
                     ? c->txbuf : UART_HW_FIFO_LEN(unit) + 1u;
    int32_t rc = esp_rc(uart_driver_install(unit, (int)rxbuf, (int)txbuf, 0, NULL, 0));
    if (rc != 0) {
        return rc == MOY_DEV_EBUSY ? MOY_DEV_EBUSY : rc;
    }
    rc = uart_cfg(unit, c);
    if (rc == 0) {
        rc = esp_rc(uart_set_pin(unit, tx, rx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    }
    if (rc != 0) {
        uart_driver_delete(unit);
        return rc;
    }
    s_txbuf[unit] = txbuf;
    return 0;
}

int32_t moy_devbus_uart_set(uint8_t unit, const moy_devbus_uart_t *c) {
    return uart_cfg(unit, c);
}

void moy_devbus_uart_close(uint8_t unit) {
    if (unit < UART_NUM_MAX && uart_is_driver_installed(unit)) {
        uart_driver_delete(unit);
    }
}

int32_t moy_devbus_uart_any(uint8_t unit) {
    size_t n = 0;
    int32_t rc = esp_rc(uart_get_buffered_data_len(unit, &n));
    return rc != 0 ? rc : (int32_t)(n > INT32_MAX ? INT32_MAX : n);
}

int32_t moy_devbus_uart_read(uint8_t unit, uint8_t *out, size_t cap) {
    int32_t n = moy_devbus_uart_any(unit);
    if (n <= 0) {
        return n;
    }
    if ((size_t)n > cap) {
        n = (int32_t)cap;
    }
    int got = uart_read_bytes(unit, out, (uint32_t)n, 0);
    return got < 0 ? MOY_DEV_EIO : got;
}

int32_t moy_devbus_uart_write(uint8_t unit, const uint8_t *data, size_t n) {
    if (n > INT32_MAX) {
        n = INT32_MAX;
    }
    int r = s_txbuf[unit] == 0u ? uart_tx_chars(unit, (const char *)data, (uint32_t)n)
                                : uart_write_bytes(unit, data, n);
    return r < 0 ? MOY_DEV_EIO : r;
}

// -- spi -----------------------------------------------------------------------------

typedef struct {
    spi_device_handle_t dev;
    uint8_t *zero;              // MOY_DEVBUS_SPI_CHUNK bytes of the fill, DMA-capable
    uint8_t fill;
} spi_t;

static spi_t s_spi[MOY_DEVBUS_SPIS];

static spi_host_device_t host_of(uint8_t unit) {
    return (spi_host_device_t)(unit - 1);
}

static int32_t spi_add(uint8_t unit, const moy_devbus_spi_t *c) {
    spi_device_interface_config_t dc = {
        .mode = c->mode,
        .clock_speed_hz = (int)c->baud,
        .spics_io_num = -1,
        .queue_size = 1,
        .flags = c->lsb ? SPI_DEVICE_BIT_LSBFIRST : 0u,
    };
    return esp_rc(spi_bus_add_device(host_of(unit), &dc, &s_spi[unit].dev));
}

int32_t moy_devbus_spi_open(uint8_t unit, int sck, int mosi, int miso, const moy_devbus_spi_t *c) {
    // SPI1 is the flash's: spi2 is the first a board may offer.
    if (unit < 2u || unit >= MOY_DEVBUS_SPIS || host_of(unit) >= SPI_HOST_MAX) {
        return MOY_DEV_EBAD;
    }
    spi_t *s = &s_spi[unit];
    s->zero = heap_caps_calloc(1, MOY_DEVBUS_SPI_CHUNK, MALLOC_CAP_DMA);
    if (s->zero == NULL) {
        return MOY_DEV_ENOMEM;
    }
    s->fill = 0;
    spi_bus_config_t bc = {
        .mosi_io_num = mosi,
        .miso_io_num = miso,
        .sclk_io_num = sck,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = MOY_DEVBUS_SPI_CHUNK,
    };
    // A host whose bus is up is another driver's (a panel, a card, a radio):
    // BUSY, and nothing of it is touched.
    int32_t rc = esp_rc(spi_bus_initialize(host_of(unit), &bc, SPI_DMA_CH_AUTO));
    if (rc == 0) {
        rc = spi_add(unit, c);
        if (rc != 0) {
            spi_bus_free(host_of(unit));
        }
    }
    if (rc != 0) {
        heap_caps_free(s->zero);
        memset(s, 0, sizeof *s);
    }
    return rc;
}

int32_t moy_devbus_spi_set(uint8_t unit, const moy_devbus_spi_t *c) {
    spi_bus_remove_device(s_spi[unit].dev);
    s_spi[unit].dev = NULL;
    return spi_add(unit, c);
}

void moy_devbus_spi_close(uint8_t unit) {
    spi_t *s = &s_spi[unit];
    if (s->zero == NULL) {
        return;
    }
    if (s->dev != NULL) {
        spi_bus_remove_device(s->dev);
    }
    spi_bus_free(host_of(unit));
    heap_caps_free(s->zero);
    memset(s, 0, sizeof *s);
}

int32_t moy_devbus_spi_xfer(uint8_t unit, const uint8_t *out, uint8_t fill, uint8_t *in, size_t n) {
    spi_t *s = &s_spi[unit];
    if (s->dev == NULL) {
        return MOY_DEV_EIO;
    }
    if (out == NULL && s->fill != fill) {
        memset(s->zero, fill, MOY_DEVBUS_SPI_CHUNK);
        s->fill = fill;
    }
    if (n > INT32_MAX) {
        n = INT32_MAX;
    }
    for (size_t at = 0; at < n;) {
        size_t k = n - at < MOY_DEVBUS_SPI_CHUNK ? n - at : MOY_DEVBUS_SPI_CHUNK;
        spi_transaction_t t = {
            .length = k * 8u,
            .rxlength = in != NULL ? k * 8u : 0u,
            .tx_buffer = out != NULL ? out + at : s->zero,
            .rx_buffer = in != NULL ? in + at : NULL,
        };
        int32_t rc = esp_rc(spi_device_polling_transmit(s->dev, &t));
        if (rc != 0) {
            return rc;
        }
        at += k;
    }
    return (int32_t)n;
}

// -- i2c -----------------------------------------------------------------------------

static bool s_i2c;

int32_t moy_devbus_i2c_open(int scl, int sda, uint32_t freq) {
    if (s_i2c) {
        return MOY_DEV_EBUSY;
    }
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = sda,
        .scl_io_num = scl,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = freq,
    };
    // The install first: a port another driver installed refuses it, and is
    // left as it was.
    int32_t rc = esp_rc(i2c_driver_install(MOY_DEVBUS_I2C_PORT, I2C_MODE_MASTER, 0, 0, 0));
    if (rc != 0) {
        return rc == MOY_DEV_EIO ? MOY_DEV_EBUSY : rc;
    }
    rc = esp_rc(i2c_param_config(MOY_DEVBUS_I2C_PORT, &conf));
    if (rc != 0) {
        i2c_driver_delete(MOY_DEVBUS_I2C_PORT);
        return rc;
    }
    s_i2c = true;
    return 0;
}

void moy_devbus_i2c_close(void) {
    if (s_i2c) {
        i2c_driver_delete(MOY_DEVBUS_I2C_PORT);
        s_i2c = false;
    }
}

// ESP_FAIL is a NACK: no device answered.
static int32_t i2c_rc(esp_err_t e, size_t n) {
    return e == ESP_OK ? (int32_t)n : e == ESP_FAIL ? MOY_DEV_EABSENT : esp_rc(e);
}

int32_t moy_devbus_i2c_write(uint8_t addr, const uint8_t *data, size_t n) {
    return i2c_rc(i2c_master_write_to_device(MOY_DEVBUS_I2C_PORT, addr, data, n, I2C_WAIT), n);
}

int32_t moy_devbus_i2c_read(uint8_t addr, uint8_t *out, size_t n) {
    return i2c_rc(i2c_master_read_from_device(MOY_DEVBUS_I2C_PORT, addr, out, n, I2C_WAIT), n);
}

int32_t moy_devbus_i2c_write_read(uint8_t addr, const uint8_t *data, size_t n, uint8_t *out,
                                  size_t cap) {
    return i2c_rc(i2c_master_write_read_device(MOY_DEVBUS_I2C_PORT, addr, data, n, out, cap,
                                               I2C_WAIT), cap);
}

// -- pwm -----------------------------------------------------------------------------

typedef struct {
    int pin;                    // -1: free
    int8_t ch;                  // its LEDC channel, from the book
    bool on;                    // the channel configured
    uint8_t bits;
    uint32_t holder;
} pwm_t;

static pwm_t s_pwm[MOY_DEVBUS_PWM_SLOTS] = { { -1, -1, false, 0, 0 }, { -1, -1, false, 0, 0 } };

static uint32_t pwm_hw(uint32_t duty, uint8_t bits) {
    return (uint32_t)(((uint64_t)duty << bits) >> 16);
}

// A SETTING timer configured for `freq` as machine.PWM would, and READY.
static int32_t pwm_config(int t, uint32_t freq, uint8_t *bits) {
    uint32_t src = 0;
    if (esp_clk_tree_src_get_freq_hz((soc_module_clk_t)PWM_CLK,
                                     ESP_CLK_TREE_SRC_FREQ_PRECISION_APPROX, &src) != ESP_OK
        || freq == 0u || freq > src / 2u) {
        return MOY_DEV_EBAD;
    }
    uint32_t r = ledc_find_suitable_duty_resolution(src, freq);
    if (r > 16u) {
        r = 16u;
    }
    if (r >= SOC_LEDC_TIMER_BIT_WIDTH) {
        r--;
    }
    if (r < 1u) {
        return MOY_DEV_EBAD;
    }
    ledc_timer_config_t c = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = (ledc_timer_bit_t)r,
        .timer_num = (ledc_timer_t)t,
        .freq_hz = freq,
        .clk_cfg = PWM_CLK,
    };
    int32_t rc = esp_rc(ledc_timer_config(&c));
    if (rc == 0) {
        ledc_timer_rst(LEDC_LOW_SPEED_MODE, (ledc_timer_t)t);
        moy_ledc_timer_set(t, freq, (uint8_t)r, (int32_t)PWM_CLK);
        *bits = (uint8_t)r;
    }
    return rc;
}

// Channel `ch` onto a timer at `freq`: one there already, shared; its own,
// retuned where it rides it alone; else a free one, configured.
static int32_t pwm_timer(int ch, uint32_t freq, uint8_t *bits) {
    for (int t = 0; t < MOY_LEDC_TIMERS; t++) {
        if (moy_ledc_timer_join(ch, t, freq) && moy_ledc_timer_cfg(t, NULL, bits, NULL)) {
            return t;
        }
    }
    int t = moy_ledc_chan_timer(ch);
    if (t < 0 || !moy_ledc_timer_retune(t, ch)) {
        for (t = 0; t < MOY_LEDC_TIMERS && !moy_ledc_timer_join(ch, t, MOY_LEDC_FRESH); t++) {
        }
        if (t == MOY_LEDC_TIMERS) {
            return MOY_DEV_EFULL;
        }
    }
    int32_t rc = pwm_config(t, freq, bits);
    return rc != 0 ? rc : t;
}

// The channel's output stopped and its book entry dropped; a timer it was
// the last channel of, deconfigured.
static void pwm_release(pwm_t *p) {
    if (p->on) {
        ledc_stop(LEDC_LOW_SPEED_MODE, (ledc_channel_t)p->ch, 0);
    }
    int t = moy_ledc_chan_drop(p->ch, p->holder);
    if (t >= 0) {
        ledc_timer_pause(LEDC_LOW_SPEED_MODE, (ledc_timer_t)t);
        ledc_timer_config_t c = { .speed_mode = LEDC_LOW_SPEED_MODE,
                                  .timer_num = (ledc_timer_t)t, .deconfigure = true };
        ledc_timer_config(&c);
        moy_ledc_timer_closed(t);
    }
    *p = (pwm_t){ -1, -1, false, 0, 0 };
}

int32_t moy_devbus_pwm_open(uint32_t holder, int pin, uint32_t freq, uint32_t duty) {
    int slot = 0;
    while (slot < (int)MOY_DEVBUS_PWM_SLOTS && s_pwm[slot].pin >= 0) {
        slot++;
    }
    if (slot == (int)MOY_DEVBUS_PWM_SLOTS) {
        return MOY_DEV_EFULL;
    }
    pwm_t *p = &s_pwm[slot];
    int ch = moy_ledc_chan_take(holder, true);
    if (ch < 0) {
        return MOY_DEV_EFULL;
    }
    *p = (pwm_t){ pin, (int8_t)ch, false, 0, holder };
    int32_t t = pwm_timer(ch, freq, &p->bits);
    int32_t rc = t < 0 ? t : 0;
    if (rc == 0) {
        ledc_channel_config_t c = {
            .gpio_num = pin,
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = (ledc_channel_t)ch,
            .intr_type = LEDC_INTR_DISABLE,
            .timer_sel = (ledc_timer_t)t,
            .duty = pwm_hw(duty, p->bits),
            .hpoint = 0,
        };
        rc = esp_rc(ledc_channel_config(&c));
        p->on = rc == 0;
    }
    if (rc != 0) {
        pwm_release(p);
        return rc;
    }
    return slot;
}

int32_t moy_devbus_pwm_set(int slot, uint32_t freq, uint32_t duty) {
    if (slot < 0 || slot >= (int)MOY_DEVBUS_PWM_SLOTS || s_pwm[slot].pin < 0) {
        return MOY_DEV_EABSENT;
    }
    pwm_t *p = &s_pwm[slot];
    if (freq != 0u) {
        int was = moy_ledc_chan_timer(p->ch);
        int32_t t = pwm_timer(p->ch, freq, &p->bits);
        if (t < 0) {
            return t;
        }
        if (t != was) {
            int32_t rc = esp_rc(ledc_bind_channel_timer(LEDC_LOW_SPEED_MODE,
                                                        (ledc_channel_t)p->ch, (ledc_timer_t)t));
            if (rc != 0) {
                return rc;
            }
        }
    }
    ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)p->ch, pwm_hw(duty, p->bits));
    return esp_rc(ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)p->ch));
}

void moy_devbus_pwm_close(int slot) {
    if (slot < 0 || slot >= (int)MOY_DEVBUS_PWM_SLOTS || s_pwm[slot].pin < 0) {
        return;
    }
    pwm_release(&s_pwm[slot]);
}

// -- adc -----------------------------------------------------------------------------

// A unit's handle, made at its first channel and deleted with its last.
static adc_oneshot_unit_handle_t s_adc[2];
static uint8_t s_adc_users[2];

int32_t moy_devbus_adc_open(int pin) {
    adc_unit_t unit;
    adc_channel_t ch;
    if (adc_oneshot_io_to_channel(pin, &unit, &ch) != ESP_OK || unit > ADC_UNIT_2) {
        return MOY_DEV_EBAD;
    }
    if (s_adc[unit] == NULL) {
        adc_oneshot_unit_init_cfg_t u = { .unit_id = unit, .ulp_mode = ADC_ULP_MODE_DISABLE };
        // NOT_FOUND: MicroPython's machine.ADC holds the unit.
        int32_t rc = esp_rc(adc_oneshot_new_unit(&u, &s_adc[unit]));
        if (rc != 0) {
            s_adc[unit] = NULL;
            return rc;
        }
    }
    adc_oneshot_chan_cfg_t c = { .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_12 };
    int32_t rc = esp_rc(adc_oneshot_config_channel(s_adc[unit], ch, &c));
    if (rc != 0) {
        if (s_adc_users[unit] == 0u) {
            adc_oneshot_del_unit(s_adc[unit]);
            s_adc[unit] = NULL;
        }
        return rc;
    }
    s_adc_users[unit]++;
    return 0;
}

int32_t moy_devbus_adc_read(int pin) {
    adc_unit_t unit;
    adc_channel_t ch;
    int raw = 0;
    if (adc_oneshot_io_to_channel(pin, &unit, &ch) != ESP_OK || unit > ADC_UNIT_2
        || s_adc[unit] == NULL) {
        return MOY_DEV_EIO;
    }
    int32_t rc = esp_rc(adc_oneshot_read(s_adc[unit], ch, &raw));
    if (rc != 0) {
        return rc;
    }
    uint32_t r = (uint32_t)raw & 0xfffu;
    return (int32_t)(r << 4 | r >> 8);              // 12 bits as machine.ADC's read_u16
}

void moy_devbus_adc_close(int pin) {
    adc_unit_t unit;
    adc_channel_t ch;
    if (adc_oneshot_io_to_channel(pin, &unit, &ch) != ESP_OK || unit > ADC_UNIT_2
        || s_adc_users[unit] == 0u) {
        return;
    }
    if (--s_adc_users[unit] == 0u) {
        adc_oneshot_del_unit(s_adc[unit]);
        s_adc[unit] = NULL;
    }
}

// -- the far end: none on a board ---------------------------------------------------

void moy_devbus_sim_uart_feed(uint8_t unit, const uint8_t *data, size_t n) {
    (void)unit;
    (void)data;
    (void)n;
}

size_t moy_devbus_sim_uart_sent(uint8_t unit, uint8_t *out, size_t cap) {
    (void)unit;
    (void)out;
    (void)cap;
    return 0;
}

void moy_devbus_sim_uart_loop(uint8_t unit, bool on) {
    (void)unit;
    (void)on;
}

size_t moy_devbus_sim_spi_sent(uint8_t unit, uint8_t *out, size_t cap) {
    (void)unit;
    (void)out;
    (void)cap;
    return 0;
}

void moy_devbus_sim_i2c(uint8_t addr, const uint8_t *regs, size_t n) {
    (void)addr;
    (void)regs;
    (void)n;
}

size_t moy_devbus_sim_i2c_regs(uint8_t addr, uint8_t *out, size_t cap) {
    (void)addr;
    (void)out;
    (void)cap;
    return 0;
}

void moy_devbus_sim_adc(int pin, uint16_t value) {
    (void)pin;
    (void)value;
}

uint32_t moy_devbus_sim_pwm(int pin, uint32_t *duty) {
    (void)pin;
    *duty = 0;
    return 0;
}

void moy_devbus_sim_reset(void) {
}

#else // the host's twin

#define SIM_BYTES 1024u
#define SIM_I2C_DEVS 8u
#define SIM_I2C_REGS 64u

typedef struct {
    bool open, loop;
    size_t rxn, txn;
    uint8_t rx[SIM_BYTES], tx[SIM_BYTES];
} sim_uart_t;

typedef struct {
    bool open;
    size_t n;
    uint8_t log[SIM_BYTES];
} sim_spi_t;

typedef struct {
    uint8_t addr, n, at;        // n 0: none there
    uint8_t reg[SIM_I2C_REGS];
} sim_i2c_t;

typedef struct {
    int pin;                    // -1: free
    int ch;                     // its LEDC channel, from the book
    uint32_t freq, duty, holder;
} sim_pwm_t;

static sim_uart_t s_uart[MOY_DEVBUS_UARTS];
static sim_spi_t s_spi[MOY_DEVBUS_SPIS];
static sim_i2c_t s_i2c_dev[SIM_I2C_DEVS];
static bool s_i2c;
static sim_pwm_t s_pwm[MOY_DEVBUS_PWM_SLOTS] = { { -1, -1, 0, 0, 0 }, { -1, -1, 0, 0, 0 } };
static uint16_t s_adc[MOY_LEASE_PIN_MAX];
static uint8_t s_adc_on[MOY_LEASE_PIN_MAX];

// A peripheral's signal on `pin`, driving it where `out`.
static void periph(int pin, bool out) {
    moy_lease_sim_pin(pin, (uint8_t)(MOY_PIN_IN | MOY_PIN_PERIPH | (out ? MOY_PIN_OUT : 0u)),
                      moy_lease_sim_get(pin));
}

static void append(uint8_t *buf, size_t *n, const uint8_t *data, size_t k) {
    size_t room = SIM_BYTES - *n;
    if (k > room) {
        k = room;
    }
    memcpy(buf + *n, data, k);
    *n += k;
}

int32_t moy_devbus_uart_open(uint8_t unit, int tx, int rx, const moy_devbus_uart_t *c) {
    (void)c;
    if (unit >= MOY_DEVBUS_UARTS) {
        return MOY_DEV_EBAD;
    }
    if (s_uart[unit].open) {
        return MOY_DEV_EBUSY;
    }
    s_uart[unit].open = true;
    periph(rx, false);
    periph(tx, true);
    return 0;
}

int32_t moy_devbus_uart_set(uint8_t unit, const moy_devbus_uart_t *c) {
    (void)unit;
    (void)c;
    return 0;
}

void moy_devbus_uart_close(uint8_t unit) {
    if (unit < MOY_DEVBUS_UARTS) {
        s_uart[unit].open = false;
    }
}

int32_t moy_devbus_uart_any(uint8_t unit) {
    return (int32_t)s_uart[unit].rxn;
}

int32_t moy_devbus_uart_read(uint8_t unit, uint8_t *out, size_t cap) {
    sim_uart_t *u = &s_uart[unit];
    size_t n = u->rxn < cap ? u->rxn : cap;
    memcpy(out, u->rx, n);
    memmove(u->rx, u->rx + n, u->rxn - n);
    u->rxn -= n;
    return (int32_t)n;
}

int32_t moy_devbus_uart_write(uint8_t unit, const uint8_t *data, size_t n) {
    sim_uart_t *u = &s_uart[unit];
    if (n > SIM_BYTES) {
        n = SIM_BYTES;
    }
    append(u->tx, &u->txn, data, n);
    if (u->loop) {
        append(u->rx, &u->rxn, data, n);
    }
    return (int32_t)n;
}

int32_t moy_devbus_spi_open(uint8_t unit, int sck, int mosi, int miso, const moy_devbus_spi_t *c) {
    (void)c;
    if (unit < 2u || unit >= MOY_DEVBUS_SPIS) {
        return MOY_DEV_EBAD;
    }
    if (s_spi[unit].open) {
        return MOY_DEV_EBUSY;
    }
    s_spi[unit].open = true;
    periph(sck, true);
    periph(mosi, true);
    periph(miso, false);
    return 0;
}

int32_t moy_devbus_spi_set(uint8_t unit, const moy_devbus_spi_t *c) {
    (void)unit;
    (void)c;
    return 0;
}

void moy_devbus_spi_close(uint8_t unit) {
    if (unit < MOY_DEVBUS_SPIS) {
        s_spi[unit].open = false;
    }
}

int32_t moy_devbus_spi_xfer(uint8_t unit, const uint8_t *out, uint8_t fill, uint8_t *in, size_t n) {
    sim_spi_t *s = &s_spi[unit];
    if (n > INT32_MAX) {
        n = INT32_MAX;
    }
    for (size_t i = 0; i < n; i++) {
        uint8_t b = out != NULL ? out[i] : fill;
        append(s->log, &s->n, &b, 1);
        if (in != NULL) {
            in[i] = 0xff;
        }
    }
    return (int32_t)n;
}

int32_t moy_devbus_i2c_open(int scl, int sda, uint32_t freq) {
    (void)freq;
    if (s_i2c) {
        return MOY_DEV_EBUSY;
    }
    s_i2c = true;
    periph(scl, true);
    periph(sda, true);
    return 0;
}

void moy_devbus_i2c_close(void) {
    s_i2c = false;
}

static sim_i2c_t *i2c_dev(uint8_t addr) {
    for (uint32_t i = 0; i < SIM_I2C_DEVS; i++) {
        if (s_i2c_dev[i].n != 0u && s_i2c_dev[i].addr == addr) {
            return &s_i2c_dev[i];
        }
    }
    return NULL;
}

int32_t moy_devbus_i2c_write(uint8_t addr, const uint8_t *data, size_t n) {
    sim_i2c_t *d = i2c_dev(addr);
    if (d == NULL) {
        return MOY_DEV_EABSENT;
    }
    if (n > 0u) {
        d->at = data[0];
        for (size_t i = 1; i < n; i++) {
            if (d->at + i - 1u < d->n) {
                d->reg[d->at + i - 1u] = data[i];
            }
        }
    }
    return n > INT32_MAX ? INT32_MAX : (int32_t)n;
}

int32_t moy_devbus_i2c_read(uint8_t addr, uint8_t *out, size_t n) {
    sim_i2c_t *d = i2c_dev(addr);
    if (d == NULL) {
        return MOY_DEV_EABSENT;
    }
    if (n > INT32_MAX) {
        n = INT32_MAX;
    }
    for (size_t i = 0; i < n; i++) {
        out[i] = d->at + i < d->n ? d->reg[d->at + i] : 0xff;
    }
    d->at = (uint8_t)(d->at + n);
    return (int32_t)n;
}

int32_t moy_devbus_i2c_write_read(uint8_t addr, const uint8_t *data, size_t n, uint8_t *out,
                                  size_t cap) {
    int32_t r = moy_devbus_i2c_write(addr, data, n);
    return r < 0 ? r : moy_devbus_i2c_read(addr, out, cap);
}

// The board's book walk (a timer at the frequency, shared; its own retuned;
// a free one), with no hardware: what a timer was set to is the book's.
static int sim_pwm_timer(int ch, uint32_t freq) {
    for (int t = 0; t < MOY_LEDC_TIMERS; t++) {
        if (moy_ledc_timer_join(ch, t, freq)) {
            return t;
        }
    }
    int t = moy_ledc_chan_timer(ch);
    if (t < 0 || !moy_ledc_timer_retune(t, ch)) {
        for (t = 0; t < MOY_LEDC_TIMERS && !moy_ledc_timer_join(ch, t, MOY_LEDC_FRESH); t++) {
        }
        if (t == MOY_LEDC_TIMERS) {
            return -1;
        }
    }
    moy_ledc_timer_set(t, freq, 13u, 0);
    return t;
}

static void sim_pwm_release(sim_pwm_t *p) {
    int t = moy_ledc_chan_drop(p->ch, p->holder);
    if (t >= 0) {
        moy_ledc_timer_closed(t);
    }
    *p = (sim_pwm_t){ -1, -1, 0, 0, 0 };
}

int32_t moy_devbus_pwm_open(uint32_t holder, int pin, uint32_t freq, uint32_t duty) {
    for (int slot = 0; slot < (int)MOY_DEVBUS_PWM_SLOTS; slot++) {
        if (s_pwm[slot].pin < 0) {
            int ch = moy_ledc_chan_take(holder, true);
            if (ch < 0) {
                return MOY_DEV_EFULL;
            }
            s_pwm[slot] = (sim_pwm_t){ pin, ch, freq, duty, holder };
            if (sim_pwm_timer(ch, freq) < 0) {
                sim_pwm_release(&s_pwm[slot]);
                return MOY_DEV_EFULL;
            }
            periph(pin, true);
            return slot;
        }
    }
    return MOY_DEV_EFULL;
}

int32_t moy_devbus_pwm_set(int slot, uint32_t freq, uint32_t duty) {
    if (slot < 0 || slot >= (int)MOY_DEVBUS_PWM_SLOTS || s_pwm[slot].pin < 0) {
        return MOY_DEV_EABSENT;
    }
    if (freq != 0u) {
        if (sim_pwm_timer(s_pwm[slot].ch, freq) < 0) {
            return MOY_DEV_EFULL;
        }
        s_pwm[slot].freq = freq;
    }
    s_pwm[slot].duty = duty;
    return 0;
}

void moy_devbus_pwm_close(int slot) {
    if (slot >= 0 && slot < (int)MOY_DEVBUS_PWM_SLOTS && s_pwm[slot].pin >= 0) {
        sim_pwm_release(&s_pwm[slot]);
    }
}

int32_t moy_devbus_adc_open(int pin) {
    if (pin < 0 || pin >= (int)MOY_LEASE_PIN_MAX) {
        return MOY_DEV_EBAD;
    }
    s_adc_on[pin] = 1;
    periph(pin, false);
    return 0;
}

int32_t moy_devbus_adc_read(int pin) {
    return pin >= 0 && pin < (int)MOY_LEASE_PIN_MAX && s_adc_on[pin] ? s_adc[pin] : MOY_DEV_EIO;
}

void moy_devbus_adc_close(int pin) {
    if (pin >= 0 && pin < (int)MOY_LEASE_PIN_MAX) {
        s_adc_on[pin] = 0;
    }
}

void moy_devbus_sim_uart_feed(uint8_t unit, const uint8_t *data, size_t n) {
    if (unit < MOY_DEVBUS_UARTS) {
        moy_lease_lock();
        append(s_uart[unit].rx, &s_uart[unit].rxn, data, n);
        moy_lease_unlock();
    }
}

size_t moy_devbus_sim_uart_sent(uint8_t unit, uint8_t *out, size_t cap) {
    if (unit >= MOY_DEVBUS_UARTS) {
        return 0;
    }
    moy_lease_lock();
    size_t n = s_uart[unit].txn < cap ? s_uart[unit].txn : cap;
    memcpy(out, s_uart[unit].tx, n);
    moy_lease_unlock();
    return n;
}

void moy_devbus_sim_uart_loop(uint8_t unit, bool on) {
    if (unit < MOY_DEVBUS_UARTS) {
        s_uart[unit].loop = on;
    }
}

size_t moy_devbus_sim_spi_sent(uint8_t unit, uint8_t *out, size_t cap) {
    if (unit >= MOY_DEVBUS_SPIS) {
        return 0;
    }
    moy_lease_lock();
    size_t n = s_spi[unit].n < cap ? s_spi[unit].n : cap;
    memcpy(out, s_spi[unit].log, n);
    moy_lease_unlock();
    return n;
}

void moy_devbus_sim_i2c(uint8_t addr, const uint8_t *regs, size_t n) {
    moy_lease_lock();
    sim_i2c_t *d = i2c_dev(addr);
    for (uint32_t i = 0; d == NULL && i < SIM_I2C_DEVS; i++) {
        if (s_i2c_dev[i].n == 0u) {
            d = &s_i2c_dev[i];
        }
    }
    if (d != NULL) {
        memset(d, 0, sizeof *d);
        d->addr = addr;
        d->n = (uint8_t)(n < SIM_I2C_REGS ? n : SIM_I2C_REGS);
        memcpy(d->reg, regs, d->n);
    }
    moy_lease_unlock();
}

size_t moy_devbus_sim_i2c_regs(uint8_t addr, uint8_t *out, size_t cap) {
    moy_lease_lock();
    const sim_i2c_t *d = i2c_dev(addr);
    size_t n = d == NULL ? 0u : d->n < cap ? d->n : cap;
    if (n != 0u) {
        memcpy(out, d->reg, n);
    }
    moy_lease_unlock();
    return n;
}

void moy_devbus_sim_adc(int pin, uint16_t value) {
    if (pin >= 0 && pin < (int)MOY_LEASE_PIN_MAX) {
        s_adc[pin] = value;
    }
}

uint32_t moy_devbus_sim_pwm(int pin, uint32_t *duty) {
    *duty = 0;
    for (int slot = 0; slot < (int)MOY_DEVBUS_PWM_SLOTS; slot++) {
        if (s_pwm[slot].pin == pin) {
            *duty = s_pwm[slot].duty;
            return s_pwm[slot].freq;
        }
    }
    return 0;
}

void moy_devbus_sim_reset(void) {
    moy_lease_lock();
    for (uint32_t i = 0; i < MOY_DEVBUS_UARTS; i++) {
        bool open = s_uart[i].open;
        memset(&s_uart[i], 0, sizeof s_uart[i]);
        s_uart[i].open = open;
    }
    for (uint32_t i = 0; i < MOY_DEVBUS_SPIS; i++) {
        s_spi[i].n = 0;
    }
    memset(s_i2c_dev, 0, sizeof s_i2c_dev);
    memset(s_adc, 0, sizeof s_adc);
    moy_lease_unlock();
}

#endif
