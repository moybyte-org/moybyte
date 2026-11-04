// moy_gatt: the `ble` device API's driver (docs/os_2026-11/hardware.md section
// 1): general Bluetooth LE for one process at a time, beside the console's BLE
// keyboard (moy_ble.h), on the same NimBLE host.
//
// AS A CENTRAL it scans (the devices it heard, their RSSI and names, a table
// of MOY_GATT_SEEN), connects to one peripheral, finds a characteristic by
// UUID, reads it, writes it (with or without a response) and subscribes to
// its notifications (the CCCD taken as the handle after the value's, where
// GATT servers put it). AS A PERIPHERAL it advertises: a name and
// manufacturer data, non-connectable (a beacon); a GATT server of the app's
// own would need its services registered before the host starts, which the
// console's host is by then.
//
// Every verb answers at once and reports through an event: a BLE record on
// the ring source "gatt" (moy_input.h), `id` the event (MOY_GATT_EV_*), `a`
// its value (a table index, a handle, a length), `b` its status (0, or a
// negative errno) or, for an advertisement, its RSSI.
//
// SHARING THE HOST WITH THE KEYBOARD. The keyboard machine and the app share
// NimBLE's one GAP master procedure through moy_scan.h: an app's scan joins
// the keyboard's discovery (the union of the two scans' kinds on the air,
// every advertisement to both), and a connection attempt pauses it, the
// other side's attempt answering -EBUSY until it ends. The host runs only
// where the keyboard machine started it (Settings' keyboard input on:
// moy_ble_up), and where it does not the API is absent (moy_gatt_present),
// never started for an app.
//
// The machine is this file's; what drives NimBLE is moy_gatt_task.c's
// moy_gatt_ops_t, run on the host task. The host's stand-in ops are
// moy_gatt.c's own, over peripherals a test declares (moy_gatt_sim_*,
// runtime/host/gatt_sim.py).

#ifndef MOY_GATT_H
#define MOY_GATT_H

#include <stddef.h>
#include <stdint.h>

#define MOY_GATT_SEEN 16
#define MOY_GATT_NAME 20
#define MOY_GATT_VALUE 244              // a read's or a notification's value
#define MOY_GATT_NOTES 8                // notifications waiting for the holder
#define MOY_GATT_ADV 31

#define MOY_GATT_EIO 5
#define MOY_GATT_EBADF 9
#define MOY_GATT_EBUSY 16
#define MOY_GATT_ENODEV 19
#define MOY_GATT_EINVAL 22
#define MOY_GATT_ENOENT 2
#define MOY_GATT_ENOTCONN 107
#define MOY_GATT_ETIMEDOUT 110

enum {
    MOY_GATT_EV_ADV = 1,                // a = the device's index in the table, b = RSSI
    MOY_GATT_EV_SCAN_DONE,              // b = 0 or -errno
    MOY_GATT_EV_CONNECTED,              // b = 0 or -errno
    MOY_GATT_EV_DISCONNECTED,
    MOY_GATT_EV_FOUND,                  // a = the value handle, b = 0 or -ENOENT
    MOY_GATT_EV_READ,                   // a = the length, b = 0 or -errno
    MOY_GATT_EV_WRITTEN,                // b = 0 or -errno
    MOY_GATT_EV_NOTIFY,                 // a = the handle
    MOY_GATT_EV_ADVERTISING,            // b = 0 or -errno
};

enum {
    MOY_GATT_IDLE = 0,
    MOY_GATT_CONNECTING,
    MOY_GATT_CONNECTED,
};

typedef struct {
    uint8_t len;                        // 2 or 16
    uint8_t v[16];                      // little-endian, as the air carries it
} moy_gatt_uuid_t;

typedef struct {
    uint8_t addr[6];                    // little-endian, as NimBLE holds it
    uint8_t type;
    int8_t rssi;
    char name[MOY_GATT_NAME + 1];
} moy_gatt_seen_t;

// What drives the radio: every call starts a procedure and answers 0 or a
// negative errno; its outcome comes back through the moy_gatt_on_* calls.
typedef struct {
    int (*scan)(uint32_t ms);           // 0: the scan running ends, quietly
    int (*connect)(const uint8_t addr[6], uint8_t type);
    int (*disconnect)(uint16_t conn);
    int (*find)(uint16_t conn, const moy_gatt_uuid_t *u);
    int (*read)(uint16_t conn, uint16_t handle);
    int (*write)(uint16_t conn, uint16_t handle, const uint8_t *d, size_t n, int rsp);
    int (*advertise)(const uint8_t *adv, size_t n, uint32_t interval_ms);
    int (*stop_advertising)(void);
    int (*up)(void);                    // the host runs
} moy_gatt_ops_t;

// The board's ops (moy_gatt_task.c), NULL where it takes no BLE.
const moy_gatt_ops_t *moy_gatt_board_ops(void);

int moy_gatt_present(void);
int moy_gatt_open(uint32_t owner);      // 0, -EBUSY, -ENODEV
int moy_gatt_close(uint32_t owner);     // a connection dropped, advertising stopped
uint32_t moy_gatt_sweep(uint32_t owner);
uint32_t moy_gatt_owner(void);
int moy_gatt_scan(uint32_t owner, uint32_t ms);
int moy_gatt_seen(uint32_t i, moy_gatt_seen_t *out);   // 0, or -ENOENT past the table
uint32_t moy_gatt_seen_count(void);
int moy_gatt_connect(uint32_t owner, const uint8_t addr[6], uint8_t type);
int moy_gatt_disconnect(uint32_t owner);
int moy_gatt_state(void);
int moy_gatt_find(uint32_t owner, const moy_gatt_uuid_t *u);
int moy_gatt_read(uint32_t owner, uint16_t handle);
// The last read's value: its length.
int moy_gatt_value(uint32_t owner, uint8_t *out, size_t cap);
int moy_gatt_write(uint32_t owner, uint16_t handle, const uint8_t *d, size_t n, int rsp);
int moy_gatt_subscribe(uint32_t owner, uint16_t handle, int on);
// The oldest waiting notification: its length (0: none) and handle.
int moy_gatt_notified(uint32_t owner, uint8_t *out, size_t cap, uint16_t *handle);
// Advertising as a beacon: `name` and manufacturer data `mfg` (its company id
// first), every `interval_ms`.
int moy_gatt_advertise(uint32_t owner, const char *name, const uint8_t *mfg, size_t n,
                       uint32_t interval_ms);
int moy_gatt_stop_advertising(uint32_t owner);
uint32_t moy_gatt_source(void);

// -- what the radio reports (the ops' completions) --
void moy_gatt_on_adv(const uint8_t addr[6], uint8_t type, int8_t rssi, const uint8_t *ad,
                     size_t n);
void moy_gatt_on_scan_done(int err);
void moy_gatt_on_connect(int err, uint16_t conn);
void moy_gatt_on_disconnect(uint16_t conn);
void moy_gatt_on_found(int err, uint16_t val_handle);
void moy_gatt_on_read(int err, const uint8_t *d, size_t n);
void moy_gatt_on_write(int err);
void moy_gatt_on_notify(uint16_t conn, uint16_t handle, const uint8_t *d, size_t n);
void moy_gatt_on_advertising(int err);

// -- the host's stand-in: peripherals on the air --
void moy_gatt_sim(int on);
int moy_gatt_sim_device(const uint8_t addr[6], int8_t rssi, const char *name);
int moy_gatt_sim_chr(const uint8_t addr[6], const moy_gatt_uuid_t *u, uint16_t handle,
                     const uint8_t *value, size_t n);
// The value last written to a stand-in characteristic: its length.
int moy_gatt_sim_written(const uint8_t addr[6], uint16_t handle, uint8_t *out, size_t cap);
int moy_gatt_sim_notify(const uint8_t addr[6], uint16_t handle, const uint8_t *d, size_t n);
// What the console advertises: its length (0: nothing).
int moy_gatt_sim_adv(uint8_t *out, size_t cap);
void moy_gatt_reset(void);

#endif // MOY_GATT_H
