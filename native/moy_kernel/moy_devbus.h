// moy_devbus: the buses a compiled program's device handles drive
// (moy_dev.h): a UART, an SPI host, an I2C unit, an LEDC channel and an ADC
// channel, each over a lease moy_dev.c took first and drops after.
//
// ON A BOARD, ESP-IDF's drivers: driver/uart.h, driver/spi_master.h,
// driver/ledc.h, esp_adc/adc_oneshot.h, and the LEGACY driver/i2c.h, the one
// the images link (moy_bus.h: the IDF refuses an image that links it and
// i2c_master both, at boot). A driver another owner installed is never
// touched: a UART whose driver is in, an SPI host whose bus is up (the
// T-Deck's panel and card share SPI2) and an ADC unit MicroPython made are
// BUSY, and a close ends only what its open began.
//   - The I2C unit is MOY_DEVBUS_I2C_PORT, never the kernel's bus
//     (MOY_BUS_I2C_PORT, the input task's on every board): one holder at a time.
//   - The LEDC channels and timers are MOY_DEVBUS_PWM_SLOTS pairs below the
//     light's (moy_light_ledc.h) and above where MicroPython's machine.PWM
//     starts its own (from 0 up, in a book of its own).
//
// ON THE HOST, a twin whose far end a test moves (moy_devbus_sim_*, no-ops on
// a board): a UART's peer feeds its RX, reads what it wrote and joins TX to
// RX (as uart_set_loop_back does); an SPI host logs what it clocked out and
// reads 0xff; an I2C device is registers a read takes from its pointer (the
// first byte a write sends) and a write fills after it, and no device at an
// address is a NACK; an ADC pin reads what the test set. Its pins read back
// through moy_lease_pin_state as a peripheral's.
//
// Every call answers 0, a count, or a MOY_DEV_E* code (moy_dev.h). The
// handles call it under the lease table's lock (moy_lease_lock); the far
// end's calls take it too.

#ifndef MOY_DEVBUS_H
#define MOY_DEVBUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_DEVBUS_UARTS 4u             // UART units 0..3
#define MOY_DEVBUS_SPIS 4u              // SPI hosts as lease units (spi2 is 2)
#define MOY_DEVBUS_PWM_SLOTS 2u
#define MOY_DEVBUS_SPI_CHUNK 4092u      // one transaction's bytes at most

enum { MOY_DEVBUS_PARITY_NONE = 0, MOY_DEVBUS_PARITY_EVEN = 1, MOY_DEVBUS_PARITY_ODD = 2 };

typedef struct {
    uint32_t baud;
    uint8_t bits;                       // 5..8
    uint8_t parity;                     // MOY_DEVBUS_PARITY_*
    uint8_t stop;                       // 1, 2
    uint32_t rxbuf, txbuf;              // the driver's rings: rxbuf past the FIFO, txbuf 0 or past it
} moy_devbus_uart_t;

typedef struct {
    uint32_t baud;
    uint8_t mode;                       // polarity << 1 | phase
    bool lsb;                           // firstbit=lsb
} moy_devbus_spi_t;

// UART `unit` on pins tx, rx (equal: one wire): its driver installed and
// configured. Set: configured again. Any: the bytes waiting. Read: up to
// `cap` of them, never waiting. Write: with no TX ring, what the FIFO takes
// now; with one, into it (waiting only while it is full).
int32_t moy_devbus_uart_open(uint8_t unit, int tx, int rx, const moy_devbus_uart_t *c);
int32_t moy_devbus_uart_set(uint8_t unit, const moy_devbus_uart_t *c);
void moy_devbus_uart_close(uint8_t unit);
int32_t moy_devbus_uart_any(uint8_t unit);
int32_t moy_devbus_uart_read(uint8_t unit, uint8_t *out, size_t cap);
int32_t moy_devbus_uart_write(uint8_t unit, const uint8_t *data, size_t n);

// SPI host `unit` (spi2: the chip's SPI2) on sck, mosi, miso, its bus up and
// one device on it (no CS: a program drives its own on a gpio pin). Xfer: `n`
// bytes clocked, `out`'s (or `fill` each where it is NULL), what came in
// into `in` (dropped where it is NULL); n.
int32_t moy_devbus_spi_open(uint8_t unit, int sck, int mosi, int miso, const moy_devbus_spi_t *c);
int32_t moy_devbus_spi_set(uint8_t unit, const moy_devbus_spi_t *c);
void moy_devbus_spi_close(uint8_t unit);
int32_t moy_devbus_spi_xfer(uint8_t unit, const uint8_t *out, uint8_t fill, uint8_t *in, size_t n);

// The I2C unit on scl, sda at `freq` Hz. A device that does not answer is
// ABSENT, a bus that times out IO. Write_read: a write, a repeated start, a read.
int32_t moy_devbus_i2c_open(int scl, int sda, uint32_t freq);
void moy_devbus_i2c_close(void);
int32_t moy_devbus_i2c_write(uint8_t addr, const uint8_t *data, size_t n);
int32_t moy_devbus_i2c_read(uint8_t addr, uint8_t *out, size_t n);
int32_t moy_devbus_i2c_write_read(uint8_t addr, const uint8_t *data, size_t n, uint8_t *out,
                                  size_t cap);

// A PWM on `pin` at `freq` Hz and `duty` (0..65535): its slot (>= 0), FULL
// where every slot drives one. Set: the slot's duty, and its freq (0 keeps it).
int32_t moy_devbus_pwm_open(int pin, uint32_t freq, uint32_t duty);
int32_t moy_devbus_pwm_set(int slot, uint32_t freq, uint32_t duty);
void moy_devbus_pwm_close(int slot);

// An ADC channel on `pin` (BAD for a pin with none); read: 0..65535.
int32_t moy_devbus_adc_open(int pin);
int32_t moy_devbus_adc_read(int pin);
void moy_devbus_adc_close(int pin);

// The host's far end (above). Sent/regs answer how many bytes they copied.
void moy_devbus_sim_uart_feed(uint8_t unit, const uint8_t *data, size_t n);
size_t moy_devbus_sim_uart_sent(uint8_t unit, uint8_t *out, size_t cap);
void moy_devbus_sim_uart_loop(uint8_t unit, bool on);
size_t moy_devbus_sim_spi_sent(uint8_t unit, uint8_t *out, size_t cap);
// A device at `addr` with `n` registers (n 0: none there).
void moy_devbus_sim_i2c(uint8_t addr, const uint8_t *regs, size_t n);
size_t moy_devbus_sim_i2c_regs(uint8_t addr, uint8_t *out, size_t cap);
void moy_devbus_sim_adc(int pin, uint16_t value);
// What a PWM slot drives now: its freq (0: none) and duty.
uint32_t moy_devbus_sim_pwm(int pin, uint32_t *duty);
void moy_devbus_sim_reset(void);

#endif // MOY_DEVBUS_H
