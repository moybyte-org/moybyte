// moy_bus: the kernel's I2C master bus, one per board, its devices added by
// address. The touch controllers, the T-Deck's keyboard and the P4s' codec
// share it, so it is the kernel's rather than any one driver's.
//
// A board names its bus in mpconfigboard.h (MOY_BUS_I2C_PORT, _SDA, _SCL and
// _HZ); without them every call answers MOY_BUS_NONE. The bus installs on the
// first moy_bus_add and stays for the console's lifetime.
//
// It drives the IDF's legacy I2C driver (driver/i2c.h), the one MicroPython's
// machine.I2C links in these images (MICROPY_HW_ESP_NEW_I2C_DRIVER is 0): the
// IDF refuses an image that links both drivers, at boot. A port is installed by
// one of the two, never both: a driver that moves onto this bus stops opening
// machine.I2C on that port in the same change.

#ifndef MOY_BUS_H
#define MOY_BUS_H

#include <stddef.h>
#include <stdint.h>

#define MOY_BUS_DEVICES 4u      // the most devices one board's bus carries

enum {
    MOY_BUS_OK = 0,
    MOY_BUS_NONE = 1,       // this board names no bus
    MOY_BUS_FULL = 2,       // every device slot is taken
    MOY_BUS_ERR = 3,        // the driver refused or the transfer failed
};

// A device at 7-bit `addr`, every transfer of it bounded by `timeout_ms`;
// `*dev` names it from then on. Adding an address twice returns the same dev.
int moy_bus_add(uint8_t addr, uint32_t timeout_ms, uint8_t *dev);

int moy_bus_write(uint8_t dev, const uint8_t *src, size_t n);
int moy_bus_read(uint8_t dev, uint8_t *dst, size_t n);
// A write then a read under one repeated start (a register read).
int moy_bus_write_read(uint8_t dev, const uint8_t *src, size_t n_src,
                       uint8_t *dst, size_t n_dst);

#endif // MOY_BUS_H
