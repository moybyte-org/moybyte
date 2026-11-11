// moy_scan: the one GAP master procedure NimBLE runs at a time -- a discovery
// or a connection attempt -- shared by the console's BLE keyboard machine
// (moy_ble_task.c) and the `ble` device API (moy_gatt.h).
//
// A DISCOVERY IS SHARED. Each client asks for a scan of its own length and
// kind (MOY_SCAN_ACTIVE: scan requests, for the names; MOY_SCAN_DENSE: a
// continuous window rather than the background's 10% duty); the radio runs
// one discovery with no end of its own, of the union of what the clients
// on it ask, and every advertisement goes to each of them. A client joining
// with more than the running discovery gives is a restart with the union; a
// client leaving restarts it with less. A client's scan ends at its own time
// (moy_scan_tick, whose next deadline moy_scan_due names) or at its stop, and
// each end is that client's `done`.
//
// A CONNECTION ATTEMPT IS EXCLUSIVE. moy_scan_connect takes the master for
// one client (-EBUSY while another's attempt runs), pausing the discovery;
// moy_scan_connected gives it back when the attempt ends either way, and the
// discovery resumes for the clients still on it.
//
// The machine is single-threaded: the board runs every call on NimBLE's host
// task. Its ops start and cancel the radio's discovery and report a client's
// end; the host test drives it through ops of its own.

#ifndef MOY_SCAN_H
#define MOY_SCAN_H

#include <stdint.h>

enum { MOY_SCAN_KBD = 0, MOY_SCAN_APP, MOY_SCAN_CLIENTS };

#define MOY_SCAN_ACTIVE 1
#define MOY_SCAN_DENSE 2

#define MOY_SCAN_EBUSY 16

typedef struct {
    int (*disc)(void *ctx, int flags);              // a discovery with no end of its own
    int (*cancel)(void *ctx);
    void (*done)(void *ctx, int client, int err);   // the client's scan is over
} moy_scan_ops_t;

typedef struct {
    const moy_scan_ops_t *ops;
    void *ctx;
    uint32_t until[MOY_SCAN_CLIENTS];
    uint8_t on[MOY_SCAN_CLIENTS];
    uint8_t flags[MOY_SCAN_CLIENTS];
    int8_t running;                     // the flags on the air, -1 none
    int8_t connecting;                  // the client holding the master, -1 none
} moy_scan_t;

void moy_scan_init(moy_scan_t *s, const moy_scan_ops_t *ops, void *ctx);
// 0, or the radio's error (no `done` follows; the other clients scan on).
int moy_scan_start(moy_scan_t *s, int client, int flags, uint32_t ms, uint32_t now);
// The client's scan ends now: its `done` (0) is called before this returns.
void moy_scan_stop(moy_scan_t *s, int client);
int moy_scan_on(const moy_scan_t *s, int client);
void moy_scan_tick(moy_scan_t *s, uint32_t now);
// Milliseconds to the next client's end, -1 for none.
int32_t moy_scan_due(const moy_scan_t *s, uint32_t now);
int moy_scan_connect(moy_scan_t *s, int client);   // 0, or -EBUSY
void moy_scan_connected(moy_scan_t *s, int client);
int moy_scan_connecting(const moy_scan_t *s);      // the client, -1 none
// The radio ended the discovery itself: it runs again, or every client ends.
void moy_scan_lost(moy_scan_t *s, int err);
uint32_t moy_scan_size(void);

#endif // MOY_SCAN_H
