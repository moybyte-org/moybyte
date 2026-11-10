// moy_gatt: the `ble` device API's machine and the host's stand-in (moy_gatt.h).

#include "moy_gatt.h"

#include <stdlib.h>
#include <string.h>

#include "moy_input.h"
#include "moy_drivers.h"
#include "moy_lease.h"

#if defined(MOY_INPUT_BOARD)
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
static moy_drv_lock_t s_lock = portMUX_INITIALIZER_UNLOCKED;
#else
static moy_drv_lock_t s_lock;
#endif


// What the API keeps, taken at the first open (a board's PSRAM): a console
// whose apps never open BLE pays a pointer for it.
typedef struct {
    moy_gatt_seen_t seen[MOY_GATT_SEEN];
    uint32_t nseen;
    uint8_t state;
    uint16_t conn;
    uint8_t value[MOY_GATT_VALUE];
    uint16_t value_n;
    struct {
        uint16_t handle;
        uint8_t n;
        uint8_t d[MOY_GATT_VALUE];
    } notes[MOY_GATT_NOTES];
    uint32_t nhead, nn;
    uint32_t src;
    int advertising;
    volatile uint32_t owner;    // the lease's holder, the BLE task's copy
} gatt_t;
static gatt_t *G;

static const moy_gatt_ops_t *sim_ops(void);

// A board with BLE has moy_gatt_task.c's; any other image the stand-in's.
__attribute__((weak)) const moy_gatt_ops_t *moy_gatt_board_ops(void) {
    return NULL;
}

static const moy_gatt_ops_t *ops(void) {
    const moy_gatt_ops_t *o = moy_gatt_board_ops();
    return o != NULL ? o : sim_ops();
}

// The `midi` API's BLE port, whose machine steps on these events
// (moy_midi.h, moy_midi_gatt): weak, for an image without it.
void moy_midi_gatt(uint8_t ev, uint16_t a, int32_t b) __attribute__((weak));

// An event posted for the holder's reader, then handed to the MIDI port's
// machine (outside the driver's lock).
static void post(uint8_t ev, uint16_t a, int32_t b) {
    moy_input_t *t = moy_input_kernel();
    if (t != NULL && G != NULL && G->src != 0) {
        moy_input_post(t, G->src, MOY_EV_BLE, a, ev, (uint32_t)b);
    }
    if (moy_midi_gatt != NULL && (ev == MOY_GATT_EV_CONNECTED || ev == MOY_GATT_EV_FOUND
                                  || ev == MOY_GATT_EV_NOTIFY
                                  || ev == MOY_GATT_EV_DISCONNECTED)) {
        moy_midi_gatt(ev, a, b);
    }
}

int moy_gatt_present(void) {
    const moy_gatt_ops_t *o = ops();
    return o != NULL && o->up();
}

// The holder is the lease table's (moy_lease.h, MOY_LEASE_BLE); G->owner is
// its copy, which a lease dropped from outside still names as the driver lets
// it go.
static int held(uint32_t owner) {
    return owner != 0 && G != NULL && owner == G->owner;
}

int moy_gatt_open(uint32_t owner) {
    if (owner == 0 || !moy_gatt_present()) {
        return -MOY_GATT_ENODEV;
    }
    if (held(owner)) {
        return 0;
    }
    if (moy_lease_holder(MOY_LEASE_BLE) != 0) {
        return -MOY_GATT_EBUSY;
    }
    if (G == NULL) {
        #if defined(MOY_INPUT_BOARD)
        G = heap_caps_calloc(1, sizeof(*G), MALLOC_CAP_SPIRAM);
        #else
        G = calloc(1, sizeof(*G));
        #endif
        if (G == NULL) {
            return -MOY_GATT_ENODEV;
        }
    }
    moy_input_t *t = moy_input_kernel();
    char was[8];
    if (G->src != 0 && (t == NULL || moy_input_source_name(t, G->src, was, sizeof(was)) != MOY_INPUT_OK
                        || strcmp(was, "gatt") != 0)) {
        G->src = 0;                     // the table it was opened on is gone
    }
    if (G->src == 0) {
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "gatt", MOY_EV_FIFO, 32, &src) == MOY_INPUT_OK) {
            G->src = src;
        }
    }
    MOY_DRV_LOCK(&s_lock);
    G->nseen = 0;
    G->nhead = G->nn = 0;
    G->value_n = 0;
    G->state = MOY_GATT_IDLE;
    MOY_DRV_UNLOCK(&s_lock);
    int lr = moy_lease_hold(owner, MOY_LEASE_BLE);
    if (lr != MOY_LEASE_OK) {
        return lr == MOY_LEASE_BUSY ? -MOY_GATT_EBUSY : -MOY_GATT_ENODEV;
    }
    G->owner = owner;
    return 0;
}

int moy_gatt_close(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    ops()->scan(0);
    if (G->state != MOY_GATT_IDLE) {
        ops()->disconnect(G->conn);
    }
    if (G->advertising) {
        ops()->stop_advertising();
        G->advertising = 0;
    }
    G->owner = 0;
    G->state = MOY_GATT_IDLE;
    moy_lease_let(owner, MOY_LEASE_BLE);
    return 0;
}

void moy_gatt_lease_end(uint32_t owner) {
    moy_gatt_close(owner);
}

uint32_t moy_gatt_sweep(uint32_t owner) {
    return moy_gatt_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_gatt_owner(void) {
    return moy_lease_holder(MOY_LEASE_BLE);
}

uint32_t moy_gatt_source(void) {
    return G != NULL ? G->src : 0;
}

int moy_gatt_state(void) {
    return G != NULL ? G->state : MOY_GATT_IDLE;
}

int moy_gatt_scan(uint32_t owner, uint32_t ms) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (ms == 0 || ms > 60000) {
        return -MOY_GATT_EINVAL;
    }
    MOY_DRV_LOCK(&s_lock);
    G->nseen = 0;
    MOY_DRV_UNLOCK(&s_lock);
    return ops()->scan(ms);
}

int moy_gatt_seen(uint32_t i, moy_gatt_seen_t *out) {
    if (G == NULL) {
        return -MOY_GATT_ENOENT;
    }
    MOY_DRV_LOCK(&s_lock);
    int ok = i < G->nseen;
    if (ok) {
        *out = G->seen[i];
    }
    MOY_DRV_UNLOCK(&s_lock);
    return ok ? 0 : -MOY_GATT_ENOENT;
}

uint32_t moy_gatt_seen_count(void) {
    return G != NULL ? G->nseen : 0;
}

int moy_gatt_connect(uint32_t owner, const uint8_t addr[6], uint8_t type) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (G->state != MOY_GATT_IDLE) {
        return -MOY_GATT_EBUSY;
    }
    G->state = MOY_GATT_CONNECTING;
    int rc = ops()->connect(addr, type);
    if (rc != 0) {
        G->state = MOY_GATT_IDLE;
    }
    return rc;
}

int moy_gatt_disconnect(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (G->state == MOY_GATT_IDLE) {
        return -MOY_GATT_ENOTCONN;
    }
    return ops()->disconnect(G->conn);
}

static int connected(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    return G->state == MOY_GATT_CONNECTED ? 0 : -MOY_GATT_ENOTCONN;
}

int moy_gatt_find(uint32_t owner, const moy_gatt_uuid_t *u) {
    int rc = connected(owner);
    if (rc != 0) {
        return rc;
    }
    if (u->len != 2 && u->len != 16) {
        return -MOY_GATT_EINVAL;
    }
    return ops()->find(G->conn, u);
}

int moy_gatt_read(uint32_t owner, uint16_t handle) {
    int rc = connected(owner);
    return rc != 0 ? rc : ops()->read(G->conn, handle);
}

int moy_gatt_value(uint32_t owner, uint8_t *out, size_t cap) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    MOY_DRV_LOCK(&s_lock);
    size_t n = G->value_n < cap ? G->value_n : cap;
    memcpy(out, G->value, n);
    MOY_DRV_UNLOCK(&s_lock);
    return (int)n;
}

int moy_gatt_write(uint32_t owner, uint16_t handle, const uint8_t *d, size_t n, int rsp) {
    int rc = connected(owner);
    if (rc != 0) {
        return rc;
    }
    if (n > MOY_GATT_VALUE) {
        return -MOY_GATT_EINVAL;
    }
    return ops()->write(G->conn, handle, d, n, rsp);
}

int moy_gatt_subscribe(uint32_t owner, uint16_t handle, int on) {
    uint8_t cccd[2] = { (uint8_t)(on ? 1 : 0), 0 };
    return moy_gatt_write(owner, (uint16_t)(handle + 1u), cccd, 2, 1);
}

int moy_gatt_notified(uint32_t owner, uint8_t *out, size_t cap, uint16_t *handle) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    MOY_DRV_LOCK(&s_lock);
    int n = 0;
    if (G->nn != 0) {
        size_t k = G->notes[G->nhead].n < cap ? G->notes[G->nhead].n : cap;
        memcpy(out, G->notes[G->nhead].d, k);
        *handle = G->notes[G->nhead].handle;
        G->nhead = (G->nhead + 1u) % MOY_GATT_NOTES;
        G->nn--;
        n = (int)k;
    }
    MOY_DRV_UNLOCK(&s_lock);
    return n;
}

int moy_gatt_advertise(uint32_t owner, const char *name, const uint8_t *mfg, size_t n,
                       uint32_t interval_ms) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    size_t nl = strlen(name);
    uint8_t ad[MOY_GATT_ADV];
    size_t k = 0;
    ad[k++] = 2;
    ad[k++] = 0x01;                     // flags: general discoverable, no BR/EDR
    ad[k++] = 0x06;
    if (nl > 0) {
        if (k + 2 + nl > sizeof(ad)) {
            return -MOY_GATT_EINVAL;
        }
        ad[k++] = (uint8_t)(nl + 1);
        ad[k++] = 0x09;
        memcpy(ad + k, name, nl);
        k += nl;
    }
    if (n > 0) {
        if (n < 2 || k + 2 + n > sizeof(ad)) {
            return -MOY_GATT_EINVAL;
        }
        ad[k++] = (uint8_t)(n + 1);
        ad[k++] = 0xff;
        memcpy(ad + k, mfg, n);
        k += n;
    }
    if (interval_ms < 20 || interval_ms > 10000) {
        return -MOY_GATT_EINVAL;
    }
    if (G->advertising) {
        ops()->stop_advertising();
    }
    G->advertising = 1;
    int rc = ops()->advertise(ad, k, interval_ms);
    if (rc != 0) {
        G->advertising = 0;
    }
    return rc;
}

int moy_gatt_stop_advertising(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (G->advertising) {
        G->advertising = 0;
        return ops()->stop_advertising();
    }
    return 0;
}

// -- completions ----------------------------------------------------------------------

static void name_of(const uint8_t *ad, size_t n, char *out) {
    out[0] = 0;
    for (size_t i = 0; i + 1 < n && ad[i] != 0; i += (size_t)ad[i] + 1u) {
        uint8_t len = ad[i], type = ad[i + 1];
        if ((type == 0x09 || type == 0x08) && i + 1u + len <= n) {
            size_t k = len - 1u < MOY_GATT_NAME ? len - 1u : MOY_GATT_NAME;
            memcpy(out, ad + i + 2, k);
            out[k] = 0;
            if (type == 0x09) {
                return;
            }
        }
    }
}

void moy_gatt_on_adv(const uint8_t addr[6], uint8_t type, int8_t rssi, const uint8_t *ad,
                     size_t n) {
    if (G == NULL) {
        return;
    }
    if (G->owner == 0) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    uint32_t i = 0;
    while (i < G->nseen && memcmp(G->seen[i].addr, addr, 6) != 0) {
        i++;
    }
    int fresh = i == G->nseen;
    if (fresh && G->nseen == MOY_GATT_SEEN) {
        MOY_DRV_UNLOCK(&s_lock);
        return;
    }
    moy_gatt_seen_t *s = &G->seen[i];
    if (fresh) {
        memcpy(s->addr, addr, 6);
        s->name[0] = 0;
        G->nseen++;
    }
    s->type = type;
    s->rssi = rssi;
    char name[MOY_GATT_NAME + 1];
    name_of(ad, n, name);
    if (name[0] != 0) {
        memcpy(s->name, name, sizeof(name));
    }
    MOY_DRV_UNLOCK(&s_lock);
    if (fresh) {
        post(MOY_GATT_EV_ADV, (uint16_t)i, rssi);
    }
}

void moy_gatt_on_scan_done(int err) {
    if (G == NULL) {
        return;
    }
    if (G->owner != 0) {
        post(MOY_GATT_EV_SCAN_DONE, (uint16_t)G->nseen, err);
    }
}

void moy_gatt_on_connect(int err, uint16_t conn) {
    if (G == NULL) {
        return;
    }
    if (err == 0 && G->owner == 0) {
        ops()->disconnect(conn);        // its holder ended while it connected
        return;
    }
    G->state = err == 0 ? MOY_GATT_CONNECTED : MOY_GATT_IDLE;
    G->conn = conn;
    post(MOY_GATT_EV_CONNECTED, conn, err);
}

void moy_gatt_on_disconnect(uint16_t conn) {
    if (G == NULL) {
        return;
    }
    if (G->state == MOY_GATT_CONNECTING) {
        // A link that dropped before it was reported up: the attempt failed.
        G->state = MOY_GATT_IDLE;
        post(MOY_GATT_EV_CONNECTED, conn, -MOY_GATT_ENOTCONN);
        return;
    }
    if (conn != G->conn || G->state == MOY_GATT_IDLE) {
        return;
    }
    G->state = MOY_GATT_IDLE;
    post(MOY_GATT_EV_DISCONNECTED, conn, 0);
}

void moy_gatt_on_found(int err, uint16_t val_handle) {
    if (G == NULL) {
        return;
    }
    post(MOY_GATT_EV_FOUND, val_handle, err);
}

void moy_gatt_on_read(int err, const uint8_t *d, size_t n) {
    if (G == NULL) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    G->value_n = 0;
    if (err == 0) {
        G->value_n = (uint16_t)(n < MOY_GATT_VALUE ? n : MOY_GATT_VALUE);
        memcpy(G->value, d, G->value_n);
    }
    MOY_DRV_UNLOCK(&s_lock);
    post(MOY_GATT_EV_READ, G->value_n, err);
}

void moy_gatt_on_write(int err) {
    if (G == NULL) {
        return;
    }
    post(MOY_GATT_EV_WRITTEN, 0, err);
}

void moy_gatt_on_notify(uint16_t conn, uint16_t handle, const uint8_t *d, size_t n) {
    if (G == NULL) {
        return;
    }
    if (G->owner == 0 || conn != G->conn) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    if (G->nn == MOY_GATT_NOTES) {
        G->nhead = (G->nhead + 1u) % MOY_GATT_NOTES;
        G->nn--;
    }
    uint32_t i = (G->nhead + G->nn) % MOY_GATT_NOTES;
    G->notes[i].handle = handle;
    G->notes[i].n = (uint8_t)(n < MOY_GATT_VALUE ? n : MOY_GATT_VALUE);
    memcpy(G->notes[i].d, d, G->notes[i].n);
    G->nn++;
    MOY_DRV_UNLOCK(&s_lock);
    post(MOY_GATT_EV_NOTIFY, handle, (int32_t)n);
}

void moy_gatt_on_advertising(int err) {
    if (G == NULL) {
        return;
    }
    if (err != 0) {
        G->advertising = 0;
    }
    post(MOY_GATT_EV_ADVERTISING, 0, err);
}

// -- the host's stand-in --------------------------------------------------------------
// Peripherals a test declares, answering at once: each op completes before it
// returns, as a quick radio would at the next host-task turn.

#if !defined(MOY_INPUT_BOARD)

#define SIM_DEVS 4
#define SIM_CHRS 8
static int s_sim_on;
static struct {
    uint8_t addr[6];
    int8_t rssi;
    char name[MOY_GATT_NAME + 1];
    struct {
        moy_gatt_uuid_t u;
        uint16_t handle;
        uint8_t n, w;
        uint8_t v[MOY_GATT_VALUE], wv[MOY_GATT_VALUE];
        uint8_t notify;
    } chr[SIM_CHRS];
    int nchr;
} s_dev[SIM_DEVS];
static int s_ndev;
static int s_sim_conn = -1;
static uint8_t s_sim_ad[MOY_GATT_ADV];
static size_t s_sim_adn;

static int sim_scan(uint32_t ms) {
    if (ms == 0) {
        return 0;
    }
    for (int i = 0; i < s_ndev; i++) {
        uint8_t ad[MOY_GATT_ADV];
        size_t nl = strlen(s_dev[i].name);
        ad[0] = (uint8_t)(nl + 1);
        ad[1] = 0x09;
        memcpy(ad + 2, s_dev[i].name, nl);
        moy_gatt_on_adv(s_dev[i].addr, 0, s_dev[i].rssi, ad, nl + 2);
    }
    moy_gatt_on_scan_done(0);
    return 0;
}

static int sim_dev(const uint8_t addr[6]) {
    for (int i = 0; i < s_ndev; i++) {
        if (memcmp(s_dev[i].addr, addr, 6) == 0) {
            return i;
        }
    }
    return -1;
}

static int sim_connect(const uint8_t addr[6], uint8_t type) {
    (void)type;
    int i = sim_dev(addr);
    s_sim_conn = i;
    moy_gatt_on_connect(i < 0 ? -MOY_GATT_ETIMEDOUT : 0, i < 0 ? 0 : (uint16_t)(i + 1));
    return 0;
}

static int sim_disconnect(uint16_t conn) {
    s_sim_conn = -1;
    moy_gatt_on_disconnect(conn);
    return 0;
}

static int sim_find(uint16_t conn, const moy_gatt_uuid_t *u) {
    int d = conn - 1;
    for (int i = 0; d >= 0 && i < s_dev[d].nchr; i++) {
        moy_gatt_uuid_t *c = &s_dev[d].chr[i].u;
        if (c->len == u->len && memcmp(c->v, u->v, u->len) == 0) {
            moy_gatt_on_found(0, s_dev[d].chr[i].handle);
            return 0;
        }
    }
    moy_gatt_on_found(-MOY_GATT_ENOENT, 0);
    return 0;
}

static int sim_chr(uint16_t conn, uint16_t handle) {
    int d = conn - 1;
    for (int i = 0; d >= 0 && i < s_dev[d].nchr; i++) {
        if (s_dev[d].chr[i].handle == handle) {
            return i;
        }
    }
    return -1;
}

static int sim_read(uint16_t conn, uint16_t handle) {
    int c = sim_chr(conn, handle);
    if (c < 0) {
        moy_gatt_on_read(-MOY_GATT_ENOENT, NULL, 0);
    } else {
        moy_gatt_on_read(0, s_dev[conn - 1].chr[c].v, s_dev[conn - 1].chr[c].n);
    }
    return 0;
}

static int sim_write(uint16_t conn, uint16_t handle, const uint8_t *d, size_t n, int rsp) {
    int c = sim_chr(conn, handle);
    int cccd = c < 0 ? sim_chr(conn, (uint16_t)(handle - 1u)) : -1;
    if (cccd >= 0 && n == 2) {
        s_dev[conn - 1].chr[cccd].notify = d[0] & 1;
    } else if (c >= 0) {
        memcpy(s_dev[conn - 1].chr[c].wv, d, n);
        s_dev[conn - 1].chr[c].w = (uint8_t)n;
    }
    if (rsp) {
        moy_gatt_on_write(c < 0 && cccd < 0 ? -MOY_GATT_ENOENT : 0);
    }
    return 0;
}

static int sim_advertise(const uint8_t *ad, size_t n, uint32_t interval_ms) {
    (void)interval_ms;
    memcpy(s_sim_ad, ad, n);
    s_sim_adn = n;
    moy_gatt_on_advertising(0);
    return 0;
}

static int sim_stop_advertising(void) {
    s_sim_adn = 0;
    return 0;
}

static int sim_up(void) {
    return s_sim_on;
}

static const moy_gatt_ops_t s_sim_ops = {
    sim_scan, sim_connect, sim_disconnect, sim_find, sim_read, sim_write, sim_advertise,
    sim_stop_advertising, sim_up,
};

static const moy_gatt_ops_t *sim_ops(void) {
    return &s_sim_ops;
}

void moy_gatt_sim(int on) {
    s_sim_on = on;
    s_ndev = 0;
    s_sim_conn = -1;
    s_sim_adn = 0;
}

int moy_gatt_sim_device(const uint8_t addr[6], int8_t rssi, const char *name) {
    if (s_ndev == SIM_DEVS) {
        return -1;
    }
    memset(&s_dev[s_ndev], 0, sizeof(s_dev[0]));
    memcpy(s_dev[s_ndev].addr, addr, 6);
    s_dev[s_ndev].rssi = rssi;
    strncpy(s_dev[s_ndev].name, name, MOY_GATT_NAME);
    s_ndev++;
    return 0;
}

int moy_gatt_sim_chr(const uint8_t addr[6], const moy_gatt_uuid_t *u, uint16_t handle,
                     const uint8_t *value, size_t n) {
    int d = sim_dev(addr);
    if (d < 0 || s_dev[d].nchr == SIM_CHRS || n > MOY_GATT_VALUE) {
        return -1;
    }
    int i = s_dev[d].nchr++;
    s_dev[d].chr[i].u = *u;
    s_dev[d].chr[i].handle = handle;
    s_dev[d].chr[i].n = (uint8_t)n;
    memcpy(s_dev[d].chr[i].v, value, n);
    return 0;
}

int moy_gatt_sim_written(const uint8_t addr[6], uint16_t handle, uint8_t *out, size_t cap) {
    int d = sim_dev(addr);
    int c = d < 0 ? -1 : sim_chr((uint16_t)(d + 1), handle);
    if (c < 0) {
        return -1;
    }
    size_t n = s_dev[d].chr[c].w < cap ? s_dev[d].chr[c].w : cap;
    memcpy(out, s_dev[d].chr[c].wv, n);
    return (int)n;
}

int moy_gatt_sim_notify(const uint8_t addr[6], uint16_t handle, const uint8_t *d, size_t n) {
    int dv = sim_dev(addr);
    int c = dv < 0 ? -1 : sim_chr((uint16_t)(dv + 1), handle);
    if (c < 0 || dv != s_sim_conn || !s_dev[dv].chr[c].notify) {
        return 0;
    }
    moy_gatt_on_notify((uint16_t)(dv + 1), handle, d, n);
    return 1;
}

int moy_gatt_sim_adv(uint8_t *out, size_t cap) {
    size_t n = s_sim_adn < cap ? s_sim_adn : cap;
    memcpy(out, s_sim_ad, n);
    return (int)n;
}

#else   // a board drives its radio: the stand-in is the host's

static const moy_gatt_ops_t *sim_ops(void) {
    return NULL;
}

void moy_gatt_sim(int on) {
    (void)on;
}

int moy_gatt_sim_device(const uint8_t addr[6], int8_t rssi, const char *name) {
    (void)addr;
    (void)rssi;
    (void)name;
    return -1;
}

int moy_gatt_sim_chr(const uint8_t addr[6], const moy_gatt_uuid_t *u, uint16_t handle,
                     const uint8_t *value, size_t n) {
    (void)addr;
    (void)u;
    (void)handle;
    (void)value;
    (void)n;
    return -1;
}

int moy_gatt_sim_written(const uint8_t addr[6], uint16_t handle, uint8_t *out, size_t cap) {
    (void)addr;
    (void)handle;
    (void)out;
    (void)cap;
    return -1;
}

int moy_gatt_sim_notify(const uint8_t addr[6], uint16_t handle, const uint8_t *d, size_t n) {
    (void)addr;
    (void)handle;
    (void)d;
    (void)n;
    return 0;
}

int moy_gatt_sim_adv(uint8_t *out, size_t cap) {
    (void)out;
    (void)cap;
    return 0;
}

#endif

void moy_gatt_reset(void) {
    if (G != NULL && G->owner != 0) {
        moy_gatt_close(G->owner);
    }
    moy_gatt_sim(0);
    if (G != NULL && G->src != 0) {
        moy_input_ring_close(moy_input_kernel(), G->src);
        G->src = 0;
    }
}
