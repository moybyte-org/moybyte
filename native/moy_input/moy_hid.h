// moy_hid: the BLE HID keyboard central's protocol -- scan, connect, pair,
// discover the HID service, subscribe to its keyboard input reports, decode
// them into the kernel table's "ble" source -- as a state machine over events,
// with no radio in it (docs/kernel_survival_2026-10.md section 4.2).
//
// moy_ble_task.c drives it from NimBLE's host task: every GAP and GATT event
// arrives as a moy_hid_on_* call, every request it makes goes out through
// moy_hid_ops_t, and a verb from the frame is posted to that task, so the
// machine runs on one task and needs no lock but the report latch's. The host
// drives the same machine over a fake stack (runtime/moy_input.py, the tests).
//
// THE PROFILE (HOGP). A Boot Keyboard Input characteristic is preferred: the
// host writes Protocol Mode = 0 and consumes only the fixed eight-byte boot
// report. A Report-only keyboard is subscribed and its eight-byte reports
// decoded the same way; other layouts are ignored. Security is Just Works
// with bonding; a CCCD write the keyboard rejects before encryption is
// retried once, when the link is encrypted.
//
// THE KEYMAP is the arrow-key hosts' (owner call, 2026-08-14): arrows are the
// d-pad, W A S D too, Z and SPACE are A, X is B, ENTER is `run`, ESC `stop`,
// BACKSPACE `home`. A text surface gets the key and no alias but the arrows.
//
// RECONNECT. Once a keyboard is picked only that address is reconnected; with
// none picked, the first HOGP keyboard seen is. Background scans are passive
// and 10% duty (the radio is shared with WiFi and ESP-NOW, #7); the picker's
// scan is active and continuous, and it waits for an explicit pick.

#ifndef MOY_HID_H
#define MOY_HID_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "moy_drivers.h"

#define MOY_HID_DEVICES 8
#define MOY_HID_CHRS 16
#define MOY_HID_DSCS 24
#define MOY_HID_INPUTS 4
#define MOY_HID_NAME 24
#define MOY_HID_ERROR 48

#define MOY_HID_SCAN_MS 5000
#define MOY_HID_RETRY_MS 5000
#define MOY_HID_CONNECT_MS 12000
#define MOY_HID_DISCOVERY_MS 15000

#define MOY_HID_UUID_SERVICE 0x1812
#define MOY_HID_UUID_BOOT_KBD 0x2A22
#define MOY_HID_UUID_REPORT 0x2A4D
#define MOY_HID_UUID_PROTOCOL 0x2A4E
#define MOY_HID_UUID_CCCD 0x2902
#define MOY_HID_PROP_NOTIFY 0x10

enum {
    MOY_HID_OFF = 0,
    MOY_HID_DISABLED,
    MOY_HID_IDLE,
    MOY_HID_SCANNING,
    MOY_HID_FOUND,
    MOY_HID_CONNECTING,
    MOY_HID_PAIRING,
    MOY_HID_DISCOVERING,
    MOY_HID_SUBSCRIBE_RETRY,
    MOY_HID_READY,
    MOY_HID_CHOOSE,
};

extern const char *const MOY_HID_STATES[];

typedef struct {
    uint8_t type;
    uint8_t a[6];
} moy_hid_addr_t;

typedef struct moy_hid moy_hid_t;

// What the machine asks of the stack; each returns 0 when the request went out.
typedef struct {
    int (*scan)(void *ctx, bool picker);
    int (*scan_stop)(void *ctx);
    int (*connect)(void *ctx, const moy_hid_addr_t *peer);
    int (*disconnect)(void *ctx, uint16_t conn);
    int (*pair)(void *ctx, uint16_t conn);
    int (*disc_svcs)(void *ctx, uint16_t conn);
    int (*disc_chrs)(void *ctx, uint16_t conn, uint16_t start, uint16_t end);
    int (*disc_dscs)(void *ctx, uint16_t conn, uint16_t start, uint16_t end);
    int (*write)(void *ctx, uint16_t conn, uint16_t handle, const uint8_t *src, size_t n,
                 bool response);
    void (*forget_bonds)(void *ctx);
    void (*save)(void *ctx, const moy_hid_t *h);   // enabled, the picked address, its name
    uint32_t (*ms)(void *ctx);
    void *ctx;
} moy_hid_ops_t;

typedef struct {
    moy_hid_addr_t addr;
    char name[MOY_HID_NAME];
    int8_t rssi;
} moy_hid_dev_t;

typedef struct {
    uint16_t def, val;
    uint8_t props;
    uint16_t uuid;
} moy_hid_chr_t;

typedef struct {
    uint16_t handle;            // its input characteristic's value handle
    uint8_t mods;
    uint8_t keys[6];
    uint8_t nkeys;
} moy_hid_report_t;

struct moy_hid {
    const moy_hid_ops_t *ops;
    moy_input_t *table;
    uint32_t src;
    // what persists
    bool enabled;
    bool has_pref;
    moy_hid_addr_t pref;
    char name[MOY_HID_NAME];
    bool dirty;
    // the machine
    bool available;
    uint8_t state;
    uint32_t state_at, retry_at;
    char error[MOY_HID_ERROR];
    moy_hid_dev_t devs[MOY_HID_DEVICES];
    uint8_t ndev;
    bool scan_picker;
    int8_t pending_scan;        // -1 none, else picker or not
    bool pending_connect;
    moy_hid_addr_t pend_addr;
    char pend_name[MOY_HID_NAME];
    bool manual_hold;
    bool has_cand;
    moy_hid_addr_t cand;
    char cand_name[MOY_HID_NAME];
    int32_t conn;               // -1: none
    bool has_hid;
    uint16_t hid_start, hid_end;
    moy_hid_chr_t chrs[MOY_HID_CHRS];
    uint8_t nchrs;
    uint16_t dscs[MOY_HID_DSCS][2];     // handle, uuid
    uint8_t ndscs;
    uint16_t protocol_handle;
    uint8_t protocol;           // 0 none, 1 boot, 2 report
    uint16_t subs[MOY_HID_INPUTS];      // the CCCDs to write
    uint8_t nsubs;
    uint8_t sub_next;
    int32_t write_pending;
    uint8_t sub_retries;
    bool encrypted;
    uint16_t interval;          // the connection interval, 1.25 ms units
    // the reports: written by the stack's task, applied by the frame
    moy_drv_lock_t lock;
    moy_hid_report_t reports[MOY_HID_INPUTS];
    uint8_t nreports;
    uint8_t pend_usage[32];     // makes seen since the last frame, a bitmap
    uint8_t pend_mods;
    bool caps;
    bool level_idle;
    uint32_t notify_count;
    int8_t want_player;         // -1: nobody asked
};

// -- the pure parts, for both the machine and the host's tests --
bool moy_hid_adv_has_hid(const uint8_t *adv, size_t n);
size_t moy_hid_adv_name(const uint8_t *adv, size_t n, char *out, size_t cap);
// A boot-shaped report (eight bytes, or nine with a leading report id) ->
// modifiers and up to six usages; false for any other layout.
bool moy_hid_decode(const uint8_t *r, size_t n, uint8_t *mods, uint8_t keys[6], uint8_t *nkeys);
int32_t moy_hid_keycode(uint8_t usage, uint8_t mods, bool caps);
uint32_t moy_hid_buttons_for_key(int32_t key);

// -- the machine --
void moy_hid_init(moy_hid_t *h, const moy_hid_ops_t *ops, moy_input_t *table, uint32_t src);
// The radio came up (available) or could not.
void moy_hid_started(moy_hid_t *h, bool ok, const char *why);
void moy_hid_stopped(moy_hid_t *h);
void moy_hid_tick(moy_hid_t *h);                    // timeouts and retries, ~2 Hz
void moy_hid_set_enabled(moy_hid_t *h, bool on);
void moy_hid_discover(moy_hid_t *h);                // the picker's scan
bool moy_hid_pick(moy_hid_t *h, const moy_hid_addr_t *addr);
void moy_hid_forget(moy_hid_t *h);
bool moy_hid_scan(moy_hid_t *h);                    // a background scan now

void moy_hid_on_scan_result(moy_hid_t *h, const moy_hid_addr_t *addr, int8_t rssi,
                            const uint8_t *adv, size_t n);
void moy_hid_on_scan_done(moy_hid_t *h);
void moy_hid_on_connect(moy_hid_t *h, uint16_t conn, const moy_hid_addr_t *addr);
void moy_hid_on_connect_failed(moy_hid_t *h);
void moy_hid_on_disconnect(moy_hid_t *h, uint16_t conn);
void moy_hid_on_svc(moy_hid_t *h, uint16_t conn, uint16_t start, uint16_t end, uint16_t uuid);
void moy_hid_on_svc_done(moy_hid_t *h, uint16_t conn, int status);
void moy_hid_on_chr(moy_hid_t *h, uint16_t conn, uint16_t def, uint16_t val, uint8_t props,
                    uint16_t uuid);
void moy_hid_on_chr_done(moy_hid_t *h, uint16_t conn, int status);
void moy_hid_on_dsc(moy_hid_t *h, uint16_t conn, uint16_t handle, uint16_t uuid);
void moy_hid_on_dsc_done(moy_hid_t *h, uint16_t conn, int status);
void moy_hid_on_write_done(moy_hid_t *h, uint16_t conn, uint16_t handle, int status);
void moy_hid_on_notify(moy_hid_t *h, uint16_t conn, uint16_t handle, const uint8_t *r, size_t n);
void moy_hid_on_conn_update(moy_hid_t *h, uint16_t conn, uint16_t interval, int status);
void moy_hid_on_enc_change(moy_hid_t *h, uint16_t conn, bool encrypted, bool bonded);

// -- the frame: the reports since the last frame into the source --
void moy_hid_frame(moy_hid_t *h);
void moy_hid_set_player(moy_hid_t *h, int8_t slot);

// What persists, read back at start: enabled, and the picked keyboard (NULL
// for none) with its name.
void moy_hid_load(moy_hid_t *h, bool enabled, const moy_hid_addr_t *pref, const char *name);

// -- reads for the host's binding --
size_t moy_hid_sizeof(void);
uint8_t moy_hid_state(const moy_hid_t *h);
const char *moy_hid_text(const moy_hid_t *h, int which);   // 0 name, 1 error
// 0 enabled, 1 protocol, 2 conn, 3 notifications, 4 devices, 5 available,
// 6 caps lock, 7 the connection interval
int32_t moy_hid_int(const moy_hid_t *h, int which);
bool moy_hid_dev(const moy_hid_t *h, uint8_t i, moy_hid_addr_t *addr, char *name, int8_t *rssi);
bool moy_hid_pref(const moy_hid_t *h, moy_hid_addr_t *addr);

#endif // MOY_HID_H
