// moy_event: the event record every source writes and every reader reads
// (docs/os_2026-11/hardware.md section 2, version 1).
//
// THE RECORD is 16 bytes. `t_us` is the kernel's microsecond clock, wrapping
// at 2^32 (esp_timer on a board, CLOCK_MONOTONIC on a host: the clock the
// VM's time.ticks_us reads). `src` is the source's handle cut to 16 bits: its
// slot in the low byte, its generation in the high byte. `prov` says how good
// the stamp is: ISR and FIFO stamps are taken when the hardware saw the event,
// a POLL stamp when a once-a-frame read found it, so "no event lost" holds for
// ISR and FIFO sources only.
//
// THE FIELDS by kind:
//   KEY_DOWN, KEY_UP  a: the USB HID usage (page 7) in the low byte, the
//                     modifiers (MOY_INPUT_MOD_*, HID's own left-hand bits)
//                     in the high byte; b: the console's key event
//                     (moy_input.h: code | mods << MOY_INPUT_MODS_AT)
//   TEXT              b: the codepoint typed (a control byte for Enter,
//                     Backspace, a Ctrl chord)
//   BTN               a: the button's bit (MOY_INPUT_NAMES order); b: 1 down,
//                     0 up
//   TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP
//                     id: the touch id; b: x << 16 | y on the source's surface
//   EDGE              a: the pin; b: the level
//   LOST              a reader's gap, written by no source: b records of the
//                     source in `src` were dropped before this reader read
//                     them (moy_input_events puts it where the gap was)
// The rest are named for the sources that will write them.

#ifndef MOY_EVENT_H
#define MOY_EVENT_H

#include <stdint.h>

#define MOY_EVENT_VERSION 1

typedef struct {
    uint32_t t_us;
    uint16_t src;
    uint8_t kind;
    uint8_t prov;
    uint16_t a;
    uint16_t id;
    uint32_t b;
} moy_event_t;

_Static_assert(sizeof(moy_event_t) == 16, "the event record is 16 bytes");

enum {
    MOY_EV_NONE = 0,
    MOY_EV_KEY_DOWN, MOY_EV_KEY_UP, MOY_EV_TEXT, MOY_EV_BTN,
    MOY_EV_TOUCH_DOWN, MOY_EV_TOUCH_MOVE, MOY_EV_TOUCH_UP,
    MOY_EV_EDGE, MOY_EV_UART, MOY_EV_LINK, MOY_EV_MEDIA, MOY_EV_NET, MOY_EV_BLE,
    MOY_EV_LORA, MOY_EV_WINCH, MOY_EV_USB, MOY_EV_MIDI,
    MOY_EV_LOST,
    MOY_EV_KINDS,
};

// A reader's kinds, a bit each.
#define MOY_EV_BIT(kind) (1u << (kind))
#define MOY_EV_KEYS (MOY_EV_BIT(MOY_EV_KEY_DOWN) | MOY_EV_BIT(MOY_EV_KEY_UP) | MOY_EV_BIT(MOY_EV_TEXT))
#define MOY_EV_TOUCH (MOY_EV_BIT(MOY_EV_TOUCH_DOWN) | MOY_EV_BIT(MOY_EV_TOUCH_MOVE) \
                      | MOY_EV_BIT(MOY_EV_TOUCH_UP))
#define MOY_EV_PAD MOY_EV_BIT(MOY_EV_BTN)
#define MOY_EV_ALL (MOY_EV_BIT(MOY_EV_KINDS) - 2u)    // every kind but NONE

enum {
    MOY_EV_ISR = 0,
    MOY_EV_FIFO = 1,
    MOY_EV_POLL = 2,
};

// A source's ring depth unless it names one: configuration, a board's define
// over this default. The depth is the source's peak rate times the worst frame
// (a collection's); it is rounded up to a power of two.
#ifndef MOY_EV_DEPTH
#define MOY_EV_DEPTH 32u
#endif
#define MOY_EV_DEPTH_MAX 1024u
// An ISR's ring is internal RAM, so its depth has a lower ceiling: 64 records
// (1 KiB) by default, a quarter of the kernel's 4 KiB internal cap
// (native/moy_play/moy_play.h); a board with internal RAM to spare raises it
// (board.toml [input] isr_ring_max). A power of two.
#ifndef MOY_EV_ISR_DEPTH_MAX
#define MOY_EV_ISR_DEPTH_MAX 64u
#endif
// Readers a table keeps cursors for.
#define MOY_EV_READERS 8u

#endif // MOY_EVENT_H
