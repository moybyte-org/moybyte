// moy_midi: the `midi` device API's driver (docs/os_2026-11/hardware.md
// section 1): MIDI over any transport, for one process at a time, every
// message a MIDI record on the ring source "midi" (moy_input.h): `id` the
// port, `a` the status byte | the first data byte << 8, `b` the second data
// byte. A port is one transport's end:
//
//   UART  a leased UART's bytes, read by a task of the driver's own off the
//         UART's FIFO (31250 baud on the wire, any rate a converter speaks)
//   USB   a USB-MIDI device's 4-byte event packets (native/p4/moy_usb)
//   BLE   a BLE-MIDI peripheral's notifications (the `ble` API's), each a
//         header, timestamps and messages
//
// A byte stream is parsed with running status; realtime bytes come through
// whole wherever they fall; system exclusive is read past, not delivered.
// What an app sends is MIDI bytes, which the driver packs for the transport
// (moy_midi_usb_pack, moy_midi_ble_pack; a UART's are the bytes).
//
// The ports' state is one allocation at the first open (a board's PSRAM).
// The UART reader is the board's (moy_midi_uart); on the host the bytes
// reach a port through moy_midi_feed, which is what the reader calls.

#ifndef MOY_MIDI_H
#define MOY_MIDI_H

#include <stddef.h>
#include <stdint.h>

#define MOY_MIDI_PORTS 4

#define MOY_MIDI_EBADF 9
#define MOY_MIDI_EBUSY 16
#define MOY_MIDI_ENODEV 19
#define MOY_MIDI_EINVAL 22
#define MOY_MIDI_EMFILE 24

enum { MOY_MIDI_UART = 1, MOY_MIDI_USB, MOY_MIDI_BLE };

int moy_midi_open(uint32_t owner);      // 0, -EBUSY
int moy_midi_close(uint32_t owner);     // every port closed
uint32_t moy_midi_sweep(uint32_t owner);
uint32_t moy_midi_owner(void);
uint32_t moy_midi_source(void);
// A port on `transport`'s `unit` (a UART's number, a USB device's address, 0
// for BLE): its number, or -errno.
int moy_midi_port(uint32_t owner, int transport, uint32_t unit);
int moy_midi_unport(uint32_t owner, int port);
// The holder's port's transport and unit: 0, or -EBADF.
int moy_midi_port_of(uint32_t owner, int port, int *transport, uint32_t *unit);
// The board's reader on a UART port's unit: 0, or -ENODEV where there is none
// (the host: moy_midi_feed stands in for the wire).
int moy_midi_uart(uint32_t owner, int port);
// What a port's transport delivered: a byte stream (a UART's), USB-MIDI event
// packets (for the USB port on device `addr`), one BLE-MIDI packet.
void moy_midi_feed(int port, const uint8_t *d, size_t n);
void moy_midi_usb_in(uint8_t addr, const uint8_t *d, size_t n);
int moy_midi_ble_in(uint32_t owner, int port, const uint8_t *d, size_t n);
// MIDI bytes (whole messages, no system exclusive) as a transport carries
// them: their length in `out`, or -EINVAL.
int moy_midi_usb_pack(const uint8_t *msg, size_t n, uint8_t *out, size_t cap);
int moy_midi_ble_pack(const uint8_t *msg, size_t n, uint32_t ms, uint8_t *out, size_t cap);
void moy_midi_reset(void);

#endif // MOY_MIDI_H
