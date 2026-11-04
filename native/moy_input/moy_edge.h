// moy_edge: a GPIO pin's edges as a ring source of the kernel's input table
// (moy_input.h's ring sources; docs/os_2026-11/hardware.md section 2): the
// stand-in GPIO source that lease:gpio adopts (P2.3), with no lease table yet.
//
// An open pin is an input with an any-edge ISR, which writes an EDGE record
// (a: the pin, b: the level read in the ISR) stamped ISR into the source's
// internal ring through its moy_input_isr_t, so an edge is stamped when the
// pin moved and none is lost while the ring has room, whatever the VM is
// doing. Closing detaches the ISR, returns the pin to an input with no pull
// and its interrupt off, and closes the ring source. The caller says which pin
// is free: the lease rows of its board.toml's [hardware] (P2.3 checks them);
// this refuses only a pin the chip cannot use.
//
// A `loop` pin is an input AND an output, so moy_edge_drive's writes are edges
// its own ISR sees: a test drives taps with no wire, from an esp_timer, off
// the VM's task.
//
// Board only; every call answers a MOY_INPUT_* code.

#ifndef MOY_EDGE_H
#define MOY_EDGE_H

#include <stdbool.h>
#include <stdint.h>

#define MOY_EDGE_PINS 4u            // pins open at once

int moy_edge_open(int pin, uint32_t depth, bool loop, uint32_t *h);
int moy_edge_close(uint32_t h);
// `n` level changes on a loop pin, one every `period_us` (at least 50), from
// an esp_timer task; MOY_INPUT_BAD on a pin that is not a loop.
int moy_edge_drive(uint32_t h, uint32_t n, uint32_t period_us);
// Level changes a drive has still to make.
uint32_t moy_edge_driving(uint32_t h);

#endif // MOY_EDGE_H
