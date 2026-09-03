#ifndef H_STUB_ESP_ROM_SYS_H
#define H_STUB_ESP_ROM_SYS_H

#include <stdint.h>

#include "harness.h"

// The fold fence's spin. On a board this burns the VM core while the FEEDER
// runs on the other one, so the harness models it as a BLOCK: the running
// context gives the (single) CPU up for the interval and the feeder makes
// progress inside it. A spin that never yielded here would deadlock the
// cooperative scheduler, which is the honest shape of the same bug on glass --
// a fence that waits on work only the other core can do.
static inline void esp_rom_delay_us(uint32_t us) { h_block_us((int64_t)us); }

#endif // H_STUB_ESP_ROM_SYS_H
