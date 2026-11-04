// moy_gatt: the `ble` device API's machine and the host's stand-in (moy_gatt.h).

#include "moy_gatt.h"

#include <string.h>

#include "moy_input.h"
#include "moy_drivers.h"

#if defined(MOY_INPUT_BOARD)
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
static moy_drv_lock_t s_lock = portMUX_INITIALIZER_UNLOCKED;
#else
static moy_drv_lock_t s_lock;
#endif

static const moy_gatt_ops_t *s_ops;
static volatile uint32_t s_owner;
static moy_gatt_seen_t s_seen[MOY_GATT_SEEN];
static uint32_t s_nseen;
static uint8_t s_state;
static uint16_t s_conn;
static uint8_t s_value[MOY_GATT_VALUE];
static uint16_t s_value_n;
static struct {
    uint16_t handle;
    uint8_t n;
    uint8_t d[MOY_GATT_VALUE];
} *s_notes;                             // MOY_GATT_NOTES, taken at the first open
static uint32_t s_nhead, s_nn;
static uint32_t s_src;
static int s_advertising;

static const moy_gatt_ops_t *sim_ops(void);

// A board with BLE has moy_gatt_task.c's; any other image the stand-in's.
__attribute__((weak)) const moy_gatt_ops_t *moy_gatt_board_ops(void) {
    return NULL;
}

static const moy_gatt_ops_t *ops(void) {
    if (s_ops == NULL) {
        s_ops = moy_gatt_board_ops();
        if (s_ops == NULL) {
            s_ops = sim_ops();
        }
    }
    return s_ops;
}

static void post(uint8_t ev, uint16_t a, int32_t b) {
    moy_input_t *t = moy_input_kernel();
    if (t != NULL && s_src != 0) {
        moy_input_post(t, s_src, MOY_EV_BLE, a, ev, (uint32_t)b);
    }
}

int moy_gatt_present(void) {
    const moy_gatt_ops_t *o = ops();
    return o != NULL && o->up();
}

static int held(uint32_t owner) {
    return owner != 0 && owner == s_owner;
}

int moy_gatt_open(uint32_t owner) {
    if (owner == 0 || !moy_gatt_present()) {
        return -MOY_GATT_ENODEV;
    }
    if (s_owner == owner) {
        return 0;
    }
    if (s_owner != 0) {
        return -MOY_GATT_EBUSY;
    }
    if (s_notes == NULL) {
        #if defined(MOY_INPUT_BOARD)
        s_notes = heap_caps_calloc(MOY_GATT_NOTES, sizeof(*s_notes), MALLOC_CAP_SPIRAM);
        #else
        static __typeof__(*s_notes) notes[MOY_GATT_NOTES];
        s_notes = notes;
        #endif
        if (s_notes == NULL) {
            return -MOY_GATT_ENODEV;
        }
    }
    moy_input_t *t = moy_input_kernel();
    char was[8];
    if (s_src != 0 && (t == NULL || moy_input_source_name(t, s_src, was, sizeof(was)) != MOY_INPUT_OK
                       || strcmp(was, "gatt") != 0)) {
        s_src = 0;                      // the table it was opened on is gone
    }
    if (s_src == 0) {
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "gatt", MOY_EV_FIFO, 32, &src) == MOY_INPUT_OK) {
            s_src = src;
        }
    }
    MOY_DRV_LOCK(&s_lock);
    s_nseen = 0;
    s_nhead = s_nn = 0;
    s_value_n = 0;
    s_state = MOY_GATT_IDLE;
    MOY_DRV_UNLOCK(&s_lock);
    s_owner = owner;
    return 0;
}

int moy_gatt_close(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (s_state != MOY_GATT_IDLE) {
        ops()->disconnect(s_conn);
    }
    if (s_advertising) {
        ops()->stop_advertising();
        s_advertising = 0;
    }
    s_owner = 0;
    s_state = MOY_GATT_IDLE;
    return 0;
}

uint32_t moy_gatt_sweep(uint32_t owner) {
    return moy_gatt_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_gatt_owner(void) {
    return s_owner;
}

uint32_t moy_gatt_source(void) {
    return s_src;
}

int moy_gatt_state(void) {
    return s_state;
}

int moy_gatt_scan(uint32_t owner, uint32_t ms) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (ms == 0 || ms > 60000) {
        return -MOY_GATT_EINVAL;
    }
    MOY_DRV_LOCK(&s_lock);
    s_nseen = 0;
    MOY_DRV_UNLOCK(&s_lock);
    return ops()->scan(ms);
}

int moy_gatt_seen(uint32_t i, moy_gatt_seen_t *out) {
    MOY_DRV_LOCK(&s_lock);
    int ok = i < s_nseen;
    if (ok) {
        *out = s_seen[i];
    }
    MOY_DRV_UNLOCK(&s_lock);
    return ok ? 0 : -MOY_GATT_ENOENT;
}

uint32_t moy_gatt_seen_count(void) {
    return s_nseen;
}

int moy_gatt_connect(uint32_t owner, const uint8_t addr[6], uint8_t type) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (s_state != MOY_GATT_IDLE) {
        return -MOY_GATT_EBUSY;
    }
    s_state = MOY_GATT_CONNECTING;
    int rc = ops()->connect(addr, type);
    if (rc != 0) {
        s_state = MOY_GATT_IDLE;
    }
    return rc;
}

int moy_gatt_disconnect(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (s_state == MOY_GATT_IDLE) {
        return -MOY_GATT_ENOTCONN;
    }
    return ops()->disconnect(s_conn);
}

static int connected(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    return s_state == MOY_GATT_CONNECTED ? 0 : -MOY_GATT_ENOTCONN;
}

int moy_gatt_find(uint32_t owner, const moy_gatt_uuid_t *u) {
    int rc = connected(owner);
    if (rc != 0) {
        return rc;
    }
    if (u->len != 2 && u->len != 16) {
        return -MOY_GATT_EINVAL;
    }
    return ops()->find(s_conn, u);
}

int moy_gatt_read(uint32_t owner, uint16_t handle) {
    int rc = connected(owner);
    return rc != 0 ? rc : ops()->read(s_conn, handle);
}

int moy_gatt_value(uint32_t owner, uint8_t *out, size_t cap) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    MOY_DRV_LOCK(&s_lock);
    size_t n = s_value_n < cap ? s_value_n : cap;
    memcpy(out, s_value, n);
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
    return ops()->write(s_conn, handle, d, n, rsp);
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
    if (s_nn != 0) {
        size_t k = s_notes[s_nhead].n < cap ? s_notes[s_nhead].n : cap;
        memcpy(out, s_notes[s_nhead].d, k);
        *handle = s_notes[s_nhead].handle;
        s_nhead = (s_nhead + 1u) % MOY_GATT_NOTES;
        s_nn--;
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
    if (s_advertising) {
        ops()->stop_advertising();
    }
    s_advertising = 1;
    int rc = ops()->advertise(ad, k, interval_ms);
    if (rc != 0) {
        s_advertising = 0;
    }
    return rc;
}

int moy_gatt_stop_advertising(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_GATT_EBADF;
    }
    if (s_advertising) {
        s_advertising = 0;
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
    if (s_owner == 0) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    uint32_t i = 0;
    while (i < s_nseen && memcmp(s_seen[i].addr, addr, 6) != 0) {
        i++;
    }
    int fresh = i == s_nseen;
    if (fresh && s_nseen == MOY_GATT_SEEN) {
        MOY_DRV_UNLOCK(&s_lock);
        return;
    }
    moy_gatt_seen_t *s = &s_seen[i];
    if (fresh) {
        memcpy(s->addr, addr, 6);
        s->name[0] = 0;
        s_nseen++;
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
    if (s_owner != 0) {
        post(MOY_GATT_EV_SCAN_DONE, (uint16_t)s_nseen, err);
    }
}

void moy_gatt_on_connect(int err, uint16_t conn) {
    if (err == 0 && s_owner == 0) {
        ops()->disconnect(conn);        // its holder ended while it connected
        return;
    }
    s_state = err == 0 ? MOY_GATT_CONNECTED : MOY_GATT_IDLE;
    s_conn = conn;
    post(MOY_GATT_EV_CONNECTED, conn, err);
}

void moy_gatt_on_disconnect(uint16_t conn) {
    if (conn != s_conn || s_state == MOY_GATT_IDLE) {
        return;
    }
    s_state = MOY_GATT_IDLE;
    post(MOY_GATT_EV_DISCONNECTED, conn, 0);
}

void moy_gatt_on_found(int err, uint16_t val_handle) {
    post(MOY_GATT_EV_FOUND, val_handle, err);
}

void moy_gatt_on_read(int err, const uint8_t *d, size_t n) {
    MOY_DRV_LOCK(&s_lock);
    s_value_n = 0;
    if (err == 0) {
        s_value_n = (uint16_t)(n < MOY_GATT_VALUE ? n : MOY_GATT_VALUE);
        memcpy(s_value, d, s_value_n);
    }
    MOY_DRV_UNLOCK(&s_lock);
    post(MOY_GATT_EV_READ, s_value_n, err);
}

void moy_gatt_on_write(int err) {
    post(MOY_GATT_EV_WRITTEN, 0, err);
}

void moy_gatt_on_notify(uint16_t conn, uint16_t handle, const uint8_t *d, size_t n) {
    if (s_owner == 0 || conn != s_conn || s_notes == NULL) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    if (s_nn == MOY_GATT_NOTES) {
        s_nhead = (s_nhead + 1u) % MOY_GATT_NOTES;
        s_nn--;
    }
    uint32_t i = (s_nhead + s_nn) % MOY_GATT_NOTES;
    s_notes[i].handle = handle;
    s_notes[i].n = (uint8_t)(n < MOY_GATT_VALUE ? n : MOY_GATT_VALUE);
    memcpy(s_notes[i].d, d, s_notes[i].n);
    s_nn++;
    MOY_DRV_UNLOCK(&s_lock);
    post(MOY_GATT_EV_NOTIFY, handle, (int32_t)n);
}

void moy_gatt_on_advertising(int err) {
    if (err != 0) {
        s_advertising = 0;
    }
    post(MOY_GATT_EV_ADVERTISING, 0, err);
}

// -- the host's stand-in --------------------------------------------------------------
// Peripherals a test declares, answering at once: each op completes before it
// returns, as a quick radio would at the next host-task turn.

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
    (void)ms;
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

void moy_gatt_reset(void) {
    if (s_owner != 0) {
        moy_gatt_close(s_owner);
    }
    moy_gatt_sim(0);
    if (s_src != 0) {
        moy_input_ring_close(moy_input_kernel(), s_src);
    }
    s_src = 0;
    s_ops = NULL;
}
