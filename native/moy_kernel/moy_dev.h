// moy_dev: a compiled program's device handles, the kernel's (docs/os_2026-11/
// hardware.md section 4; native/os_calls.json's `moybyte.dev_*` rows, how
// "here"). A WASI program, an ELF module and a compiled app hold a lease or a
// device by HANDLE where a Python program holds an object; this table serves
// every lease kind, each over a driver that is safe on the caller's own task,
// so they answer with no VM, through an S3's VM stop too, on every image
// that links moy_app (each links this kernel too). The device APIs are the
// console's Python (runtime/system/dev_handles.py): a call this table does
// not own answers MOY_DEV_PASS, and the door hands it to Python on the VM's
// task (native/moy_app/moy_app.c, dev_row).
//
// WHAT IT OWNS (the buses' drivers are moy_devbus.h's):
//   gpio<n>  a pin of the board's gpio row, leased (moy_lease.h) for the
//            process: cfg mode=in|out|od pull=up|down|none value=0|1; set
//            the same and edges=rising|falling|both|off; get value; read the
//            edges since the last read, 8 bytes each (the stamp in
//            microseconds and the level after it, each a little-endian
//            uint32), whole records only, FULL where `cap` holds none
//   pins     the `pins` permission's digital pins, each leased for the
//            process at its first use (MOY_LEASE_PINS): set gpio<n>=0|1
//            drives each as an output; get gpio<n> reads one, an input where
//            it was not driven; a pin another process holds is BUSY; its
//            close gives back the pins it took; one a process
//   uart<n>  the board's row of that name (`lease:uart<n>`): cfg baudrate
//            (115200) bits (5..8, 8) parity=none|even|odd stop (1, 2) tx rx
//            (the row's pins by default, any of the gpio row instead) timeout
//            (taken; a read never waits) rxbuf (256) txbuf (0); set the same
//            but the pins and the buffers, which are the open's; read: the
//            bytes waiting, at most `cap`; write: with no txbuf what the FIFO
//            takes now, with one into it; get any (the bytes waiting)
//   spi<n>   the board's row of that name (`lease:spi<n>`, spi2 the chip's
//            SPI2; a host whose bus another driver brought up is BUSY): cfg
//            baudrate (500000) polarity phase (0, 1) bits (8 only)
//            firstbit=msb|lsb sck mosi miso (the row's by default); set the
//            same but the pins; read: `cap` bytes clocked in, 0 clocked out;
//            write; xfer: full duplex, as many bytes in as out (FULL where
//            `cap` is shorter). No CS: a program drives its own on a pin
//   i2c      the I2C unit the kernel's bus leaves free, on two pins of the
//            gpio row (`lease:gpio`; one holder at a time): cfg scl sda
//            (required, two pins) freq (400000) addr (0..127); set addr; get
//            addr (-1 before one); read: `cap` bytes from the device at addr;
//            write: to it; xfer: a write, a repeated start and a read of
//            `cap` (a register read); BAD before an addr, ABSENT where no
//            device answers, IO where the bus times out
//   pwm<n>   pin n of the gpio row driven by an LEDC channel (`lease:gpio`;
//            MOY_DEVBUS_PWM_SLOTS at a time, FULL past them): cfg freq
//            (5000) duty (0..65535, 32768); set the same; get freq, duty
//   adc<n>   pin n of the gpio row read by its ADC channel (`lease:gpio`; BAD
//            for a pin with none, BUSY for a unit MicroPython's machine.ADC
//            holds): get value (0..65535)
// Every other name, and a handle without MOY_DEV_BASE, is MOY_DEV_PASS.
//
// THE GRAMMAR. `cfg` is words `key=value` split by spaces, a value a number
// (decimal, 0x.. hex, a sign) where the key takes one; a word with no `=` or
// no key, a key the handle does not take or a value it does not is BAD.
//
// THE ANSWERS are moy_app.h's codes, negated (MOY_DEV_E*): DENIED without
// the permission (MOY_DEV_P_* in the caller's mask, from its grant), BUSY
// where another process holds the pin or the unit (a Python program's lease
// or another handle) or the process holds `pins` already, BAD for a name, a
// row the board does not offer, a key, a value or a verb its kind does not
// take, ABSENT for a handle not the caller's and a console with no gpio row,
// FULL where a read's `cap` holds no record, IO where a driver failed. A
// lease the table takes holds no VM stop off (no MOY_LEASE_VM).
//
// A handle is its process's; its end closes every handle it left open
// (moy_dev_sweep, which the process's event queue's close runs, moy_evq.h).
// Every call holds the lease table's lock (moy_lease_lock) throughout, a
// bus's transfer included, so any task may make one.

#ifndef MOY_DEV_H
#define MOY_DEV_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_DEV_BASE 0x40000000         // every handle of this table has it
#define MOY_DEV_HANDLES 32u
#define MOY_DEV_EDGE_DEPTH 64u          // a pin's edge ring
#define MOY_DEV_PASS (-1000)            // not this table's: the console's Python serves it

enum {
    MOY_DEV_ESTALE = -1, MOY_DEV_EFULL = -2, MOY_DEV_ENOMEM = -3, MOY_DEV_EDENIED = -4,
    MOY_DEV_EIO = -6, MOY_DEV_EBAD = -7, MOY_DEV_EABSENT = -8, MOY_DEV_EBUSY = -11,
};

// The device permissions a grant's mask holds (moy_app's moy_app_grant_devs).
#define MOY_DEV_P_GPIO (1u << 0)        // lease:gpio
#define MOY_DEV_P_PINS (1u << 1)        // pins
#define MOY_DEV_P_MIC (1u << 2)
#define MOY_DEV_P_LORA (1u << 3)
#define MOY_DEV_P_BLE (1u << 4)
#define MOY_DEV_P_USB (1u << 5)
#define MOY_DEV_P_MIDI (1u << 6)
#define MOY_DEV_P_CAMERA (1u << 7)
#define MOY_DEV_P_MEDIA (1u << 8)
#define MOY_DEV_P_MEDIA_URL (1u << 9)
#define MOY_DEV_P_HTTP (1u << 10)
#define MOY_DEV_P_ROW 16                // lease:<row> of offer i: 1 << (MOY_DEV_P_ROW + i)

// The bit of permission `perm` (n bytes), 0 for one that names no device.
uint32_t moy_dev_perm_bit(const char *perm, size_t n);

// The rows, as process `pid` holding the device permissions `perms`: an
// answer >= 0 (a handle, a length, a number), a MOY_DEV_E* code, or
// MOY_DEV_PASS.
int32_t moy_dev_open(uint32_t pid, uint32_t perms, const char *name, size_t n,
                     const char *cfg, size_t cn);
int32_t moy_dev_close(uint32_t pid, int32_t h);
int32_t moy_dev_read(uint32_t pid, int32_t h, uint8_t *out, size_t cap);
int32_t moy_dev_write(uint32_t pid, int32_t h, const uint8_t *data, size_t n);
int32_t moy_dev_xfer(uint32_t pid, int32_t h, const uint8_t *data, size_t n, uint8_t *out,
                     size_t cap);
int32_t moy_dev_set(uint32_t pid, int32_t h, const char *cfg, size_t n);
int32_t moy_dev_get(uint32_t pid, int32_t h, const char *what, size_t n);
// Process `pid` ended: every handle it held closed. How many.
uint32_t moy_dev_sweep(uint32_t pid);
// The handles open (every process's).
uint32_t moy_dev_count(void);
// Every handle closed (a test's teardown).
void moy_dev_reset(void);

#endif // MOY_DEV_H
