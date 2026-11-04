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
};

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
    uint8_t cls;
    uint8_t unit;
    uint8_t flags;
} moy_lease_row_t;

// The offers made again from `spec` (NULL or "" offers nothing): BAD for a
// malformed one, which leaves none. Refused while a lease is held.
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
