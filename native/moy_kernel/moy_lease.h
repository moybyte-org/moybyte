// moy_lease: the lease table (docs/os_2026-11/hardware.md section 1). A process
// takes a resource its board offers -- a set of GPIO pins, a UART, an SPI host
// -- and holds it alone until it drops it or ends; the sweep that ends a
// process ends its leases.
//
// THE OFFERS are the board's `lease` rows ([hardware] in its board.toml), as
// one spec string: "gpio:0,1,2/uart1:47,48/spi2:49,50,51", each row its name
// and its pins in the class's order (a UART tx, rx; an SPI host sck, mosi,
// miso). tools/board_config.py writes it as MOY_LEASE_ROWS, which
// moy_lease_offer_board reads; the host's stand-in offers a named board's the
// same way. A UART or SPI row rides the gpio row (its `bus`): its pins are the
// defaults, and a take may name other pins of the gpio row instead. With no
// offer a board leases nothing.
//
// A LEASE is a row of kind MOY_KIND_LEASE: its class, its unit, the pins it
// holds and its OWNER, the pid of the process that took it (moy_log.h's
// process ids). A pin is in one lease at most and a unit in one; a take that
// would share either is BUSY. MOY_LEASE_VM marks a lease a VM process took:
// a VM stop is refused while one is held (moy_play_stop.c), since the stop
// would end its holder's heap and leave the lease with no one to drop it.
// MOY_LEASE_PINS marks the `pins` permission's digital subset.
//
// DROPPING a lease -- moy_lease_drop, or the sweep of its owner on exit, crash
// or kill -- closes its edge sources and leaves every pin it held SAFE: an
// input, no pull, its interrupt off, no peripheral driving it; a UART's driver
// is uninstalled. An SPI host's driver is its binding's to end (the Python
// lease deinits its machine.SPI before the drop); the drop leaves its pins safe.
// moy_lease_pin_state reads a pin back: on a board from the GPIO matrix, on
// the host from the stand-in's record.
//
// DEVICES. A driver with one holder -- the microphone, the LoRa radio, the
// BLE central (its scan and its connection), the media service -- holds it
// as a lease of its DEVICE class: a row with no pins and no offer, taken by
// the driver's own open for the process opening it (moy_lease_hold) and let
// go by its close (moy_lease_let), so whoever asks -- a Python app's face, a
// Lua, wasm or ELF process, the shell (pid 1) -- is refused while another
// holds it, and the table names the holder (moy_lease_holder: the driver's
// owner word). Dropped any other way -- its owner's sweep, a process's end
// in any tier -- the row ends the driver's hold too (the driver's
// moy_<driver>_lease_end, weak: an image without the driver has none).
//
// THE RADIO. The WiFi station is off unless something holds it (the
// consoles' rule, .claude/rules/boards.md): MOY_LEASE_WIFI is a SHARED class
// whose rows are its holders, each row's `tags` a mask of the closed table
// moy_lease_wifi_tag names ("web", "update", "settings", "cart", "link",
// "carts", "dev", "http", "media", "ssh", "native", "net"). The console's own
// holds are one row owned by the shell (pid 1, which powers the station up and
// down); a process's (a run's "cart", a request's "http", a stream's "media",
// a native process's "native", a radio link's port's "net") are one row it owns, so its end
// (moy_lease_sweep) lets exactly its holds go and the listing names who
// holds the radio. A hold is idempotent per tag and owner; a row goes with
// its last tag; the station is held while any row is. The rows survive a VM
// stop with the table; a VM's fresh start (not a return start) lets them go
// with moy_lease_wifi_reset. Dropped any other way, the holds are forgotten
// and the station is powered down at the next release.
//
// EDGES. moy_lease_edge opens a held pin's edges as a ring source of the
// kernel's input table: on a board moy_edge.h's ISR source (BAD on one that
// stages no moy_input, the Zero), on the host a stand-in whose edges
// moy_lease_sim_level makes. The lease keeps it, so the drop closes it.
//
// Every call is the VM's task's; nothing here allocates but the offers, at the
// first offer, and the table, at the first take (PSRAM on a board).

#ifndef MOY_LEASE_H
#define MOY_LEASE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    MOY_LEASE_OK = 0,
    MOY_LEASE_STALE = 1,        // no live lease by that handle
    MOY_LEASE_FULL = 2,         // every row, or every edge of a lease, is taken
    MOY_LEASE_NOMEM = 3,
    MOY_LEASE_BAD = 4,          // not offered: a unit, a pin outside its row, a bad spec
    MOY_LEASE_BUSY = 5,         // another lease holds the unit or a pin
};

// The lease classes.
enum {
    MOY_LEASE_GPIO = 1,
    MOY_LEASE_UART = 2,
    MOY_LEASE_SPI = 3,
    // the device classes (DEVICES above)
    MOY_LEASE_MIC = 4,
    MOY_LEASE_LORA = 5,
    MOY_LEASE_BLE = 6,
    MOY_LEASE_MEDIA = 7,
    MOY_LEASE_WIFI = 8,         // THE RADIO above
};
#define MOY_LEASE_DEVICE_FIRST MOY_LEASE_MIC
#define MOY_LEASE_DEVICE_LAST MOY_LEASE_MEDIA

// A lease's flags.
#define MOY_LEASE_VM 0x01u          // taken by a VM process
#define MOY_LEASE_PINS 0x02u        // the `pins` permission's digital subset

// A pin as it reads back (moy_lease_pin_state): SAFE is an input and nothing else.
#define MOY_PIN_IN 0x01u
#define MOY_PIN_OUT 0x02u
#define MOY_PIN_PU 0x04u
#define MOY_PIN_PD 0x08u
#define MOY_PIN_PERIPH 0x10u        // a peripheral's signal drives it
#define MOY_PIN_SAFE MOY_PIN_IN

#define MOY_LEASE_ROWS_MAX 16u      // leases held at once
#define MOY_LEASE_OFFERS 8u         // rows a board offers
#define MOY_LEASE_EDGES 4u          // edge sources a lease holds
#define MOY_LEASE_NAME 7u
#define MOY_LEASE_PIN_MAX 64u
#define MOY_LEASE_WIFI_TAGS 12u     // the radio's holder tags
#define MOY_LEASE_SHELL 1u          // the console's own radio holds' owner, the shell's pid

typedef struct {
    char name[MOY_LEASE_NAME + 1u];     // "gpio", "uart1", "spi2"
    uint8_t cls;
    uint8_t unit;
    uint8_t npins;                      // the named pins of a UART or SPI row
    uint8_t pin[3];                     // tx, rx | sck, mosi, miso
    uint64_t pins;                      // every pin of the row
} moy_lease_offer_t;

typedef struct {
    uint64_t pins;
    uint32_t owner;
    uint32_t edge[MOY_LEASE_EDGES];     // ring source handles, 0: none
    uint8_t edge_pin[MOY_LEASE_EDGES];
    uint32_t tags;                      // a WIFI row: its holders' tag bits
    uint8_t cls;
    uint8_t unit;
    uint8_t flags;
} moy_lease_row_t;

// The offers made again from `spec` (NULL or "" offers nothing): BAD for a
// malformed one, which leaves none. Refused (BUSY) while a pin or unit lease
// is held; a device row or the radio's does not hold an offer.
int moy_lease_offer(const char *spec);
// The board's MOY_LEASE_ROWS, where its build defines one.
int moy_lease_offer_board(void);
uint32_t moy_lease_offers(void);
int moy_lease_offer_at(uint32_t i, moy_lease_offer_t *out);
// The offer named `name`, or -1.
int moy_lease_offer_find(const char *name, size_t n);

// Take `unit` of class `cls` with `pins`, for process `owner`: the pins all in
// the gpio row, the unit offered. OK with *h, BUSY, BAD, FULL or NOMEM.
int moy_lease_take(uint32_t owner, uint8_t cls, uint8_t unit, uint64_t pins,
                   uint8_t flags, uint32_t *h);
int moy_lease_drop(uint32_t h);
// Drop every lease of `owner`; answers how many.
uint32_t moy_lease_sweep(uint32_t owner);
int moy_lease_get(uint32_t h, moy_lease_row_t *out);
bool moy_lease_live(uint32_t h);
// The live leases in slot order: slots() bounds the walk, at(slot) is a
// handle or 0.
uint32_t moy_lease_slots(void);
uint32_t moy_lease_at(uint32_t slot);
// Leases held with every bit of `flags` (0: every lease).
uint32_t moy_lease_held(uint8_t flags);
// The lease holding `pin`, or 0.
uint32_t moy_lease_of_pin(int pin);

// A device class's one holder: `owner` holds `cls` (OK, again for its holder
// too), BUSY while another does, BAD for a class that is no device, FULL,
// NOMEM. Let: OK, STALE when `owner` holds none. The holder's pid, or 0.
int moy_lease_hold(uint32_t owner, uint8_t cls);
int moy_lease_let(uint32_t owner, uint8_t cls);
uint32_t moy_lease_holder(uint8_t cls);
// A device class's name ("mic", "lora", "ble", "media", and "wifi" for the
// radio's row), or NULL.
const char *moy_lease_device_name(uint8_t cls);

// The radio's holders (THE RADIO). Tag i's name (NULL past the table) and a
// tag's bit (0: no such tag). Hold: `owner` holds `tag` (MOY_LEASE_SHELL for
// the console's own), OK with every holder's mask after, BAD for a tag
// outside the table or owner 0, FULL or NOMEM when its row cannot be made.
// Release: OK with the mask after (a tag the owner does not hold is no
// error), BAD; owner 0 lets every owner's hold of the tag go (a run that
// ended in the kernel, whose process is gone). The mask, 0 while nothing
// holds the radio. Holder i (in table order): OK with its owner and tags,
// STALE past the last. Reset lets every holder go.
const char *moy_lease_wifi_tag(uint32_t i);
uint32_t moy_lease_wifi_bit(const char *tag, size_t n);
int moy_lease_wifi_hold(uint32_t owner, const char *tag, size_t n, uint32_t *mask);
int moy_lease_wifi_release(uint32_t owner, const char *tag, size_t n, uint32_t *mask);
uint32_t moy_lease_wifi_mask(void);
int moy_lease_wifi_holder(uint32_t i, uint32_t *owner, uint32_t *tags);
void moy_lease_wifi_reset(void);

// Pin `pin` of lease `h` as an edge source of `depth` records; a `loop` pin is
// an output too (moy_edge.h). *src is the ring source's handle.
int moy_lease_edge(uint32_t h, int pin, uint32_t depth, bool loop, uint32_t *src);
int moy_lease_edge_close(uint32_t h, int pin);

// The pin as it reads back now: MOY_PIN_* bits.
uint8_t moy_lease_pin_state(int pin);

// The host's stand-in: what a stand-in Pin's init made of `pin` (MOY_PIN_*
// bits) and its level; the far end moving an input (an EDGE record where an
// edge source is open on it). No-ops on a board.
void moy_lease_sim_pin(int pin, uint8_t state, int level);
void moy_lease_sim_level(int pin, int level);
int moy_lease_sim_get(int pin);

// Every lease dropped and every offer forgotten (a test's teardown).
void moy_lease_reset(void);

#endif // MOY_LEASE_H
