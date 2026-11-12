// moy_usb: the `usb` device API's driver (docs/os_2026-11/hardware.md section
// 1, #83): the USB host on a board's OTG port, for one process at a time.
//
// THE CLASSES the OS drives: HID (a boot keyboard or mouse in its boot
// protocol, any other HID in its report protocol: each report as it comes),
// CDC-ACM serial (line coding, bytes both ways) and USB-MIDI (the class's
// 4-byte event packets both ways; a device's packets also reach the `midi`
// API's port on it, native/moy_input/moy_midi.h's moy_midi_usb_in). Another device is listed, its class named,
// and not driven.
//
// Every device the host enumerates is in a table of MOY_USB_DEVS (its bus
// address, class, VID, PID and product name). What a device sends is kept
// for the holder: a HID device's latest report, and a serial or MIDI
// device's bytes in a ring of MOY_USB_RX. Every change is a USB record on
// the ring source "usb" (moy_input.h): `id` the event (MOY_USB_EV_*), `a` the
// device's address, `b` a length or a status (0, or a negative errno).
//
// The machine and the host's stand-in are this file's moy_usb.c; the board's
// class drivers over ESP-IDF's USB Host Library are moy_usb_host.c, started at
// the first open (the host library's tasks and the table's one PSRAM
// allocation), present where the board's [hardware] table names the `usb`
// API (MOY_API_USB).

#ifndef MOY_USB_H
#define MOY_USB_H

#include <stddef.h>
#include <stdint.h>

#define MOY_USB_DEVS 4
#define MOY_USB_NAME 24
#define MOY_USB_REPORT 64               // a HID report kept
#define MOY_USB_RX 1024                 // a serial or MIDI device's bytes kept

#define MOY_USB_EIO 5
#define MOY_USB_EBADF 9
#define MOY_USB_EBUSY 16
#define MOY_USB_ENODEV 19
#define MOY_USB_EINVAL 22
#define MOY_USB_ENOENT 2

enum {
    MOY_USB_OTHER = 0,
    MOY_USB_HID,
    MOY_USB_CDC,
    MOY_USB_MIDI,
};

enum {
    MOY_USB_KIND_NONE = 0,
    MOY_USB_KIND_KEYBOARD = 1,          // a HID boot keyboard
    MOY_USB_KIND_MOUSE = 2,             // a HID boot mouse
};

enum {
    MOY_USB_EV_ATTACH = 1,              // a: the address, b: the class
    MOY_USB_EV_DETACH,
    MOY_USB_EV_REPORT,                  // b: the report's length
    MOY_USB_EV_DATA,                    // b: the bytes waiting
    MOY_USB_EV_SENT,                    // b: 0 or -errno
};

typedef struct {
    uint8_t addr;
    uint8_t cls;                        // MOY_USB_HID, _CDC, _MIDI, _OTHER
    uint8_t kind;                       // MOY_USB_KIND_*
    uint8_t iface_class;                // the first interface's USB class code
    uint16_t vid, pid;
    char name[MOY_USB_NAME + 1];
} moy_usb_dev_t;

// What drives the bus: each call starts a transfer and answers 0 or a
// negative errno; its outcome comes back through moy_usb_on_*.
typedef struct {
    int (*start)(void);                 // the host up: 0, or -ENODEV
    int (*line)(uint8_t addr, uint32_t baud);   // a serial device's line coding
    int (*send)(uint8_t addr, const uint8_t *d, size_t n);
} moy_usb_ops_t;

// The board's ops (moy_usb_host.c), NULL where it has no USB host.
const moy_usb_ops_t *moy_usb_board_ops(void);

int moy_usb_present(void);
int moy_usb_open(uint32_t owner);       // 0, -EBUSY, -ENODEV
int moy_usb_close(uint32_t owner);
uint32_t moy_usb_sweep(uint32_t owner);
uint32_t moy_usb_owner(void);
uint32_t moy_usb_count(void);
int moy_usb_device(uint32_t i, moy_usb_dev_t *out);    // 0, or -ENOENT past the table
int moy_usb_class_of(uint8_t addr);     // the device at `addr`'s class, or -ENOENT
// A HID device's latest report: its length (0: none yet).
int moy_usb_report(uint32_t owner, uint8_t addr, uint8_t *out, size_t cap);
// A serial device's line at `baud` (8N1).
int moy_usb_serial(uint32_t owner, uint8_t addr, uint32_t baud);
int moy_usb_write(uint32_t owner, uint8_t addr, const uint8_t *d, size_t n);
// A serial or MIDI device's bytes, oldest first: how many.
int moy_usb_read(uint32_t owner, uint8_t addr, uint8_t *out, size_t cap);
uint32_t moy_usb_source(void);

// -- what the bus reports (the class drivers' completions) --
void moy_usb_on_attach(const moy_usb_dev_t *d);
void moy_usb_on_detach(uint8_t addr);
void moy_usb_on_in(uint8_t addr, const uint8_t *d, size_t n);
void moy_usb_on_sent(uint8_t addr, int err);

// -- the host's stand-in: devices on the bus --
void moy_usb_sim(int on);
int moy_usb_sim_attach(uint8_t addr, uint8_t cls, uint8_t kind, uint16_t vid, uint16_t pid,
                       const char *name);
void moy_usb_sim_detach(uint8_t addr);
void moy_usb_sim_in(uint8_t addr, const uint8_t *d, size_t n);
// What the console sent a stand-in device, its length; its line's baud.
int moy_usb_sim_out(uint8_t addr, uint8_t *out, size_t cap, uint32_t *baud);
void moy_usb_reset(void);

// The host port's stack is up (moy_usb_host.c).
int moy_usb_host_up(void);

#endif // MOY_USB_H
