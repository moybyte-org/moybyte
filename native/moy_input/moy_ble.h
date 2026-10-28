// moy_input's BLE HID central, the board half (moy_ble_task.c): the NimBLE
// host's lifecycle and the verbs the frame asks of the machine (moy_hid.h).
// Every call answers false where the board takes no BLE (MOY_INPUT_BLE).

#ifndef MOY_BLE_H
#define MOY_BLE_H

#include <stdbool.h>
#include <stdint.h>

#include "moy_hid.h"

enum {
    MOY_BLE_ENABLE = 0,
    MOY_BLE_DISABLE,
    MOY_BLE_DISCOVER,       // the picker's scan
    MOY_BLE_PICK,           // connect the address and remember it
    MOY_BLE_FORGET,         // the picked keyboard and every bond
    MOY_BLE_SCAN,           // a background scan now
};

// The machine, made on first ask with its settings read from NVS; NULL where
// the board takes no BLE. Its fields are read by the frame without the host
// task's lock: a status, never a decision.
moy_hid_t *moy_ble_hid(void);
// Bring the NimBLE host up (the controller first, on a hosted board). The
// console starts it once and never stops it; stop is the on-glass suites'.
bool moy_ble_start(void);
void moy_ble_stop(void);
bool moy_ble_up(void);
// Queue a verb for the host task.
bool moy_ble_verb(uint8_t verb, const moy_hid_addr_t *addr);

#endif // MOY_BLE_H
