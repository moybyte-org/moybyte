// moy_usb: the `usb` device API's machine and the host's stand-in (moy_usb.h).

#include "moy_usb.h"

#include <stdlib.h>
#include <string.h>

#include "moy_drivers.h"
#include "moy_input.h"
#include "moy_midi.h"

#if defined(MOY_INPUT_BOARD)
#include "esp_heap_caps.h"
static moy_drv_lock_t s_lock = portMUX_INITIALIZER_UNLOCKED;
#else
static moy_drv_lock_t s_lock;
#endif

static volatile uint32_t s_owner;

// What the API keeps, taken at the first open (a board's PSRAM).
typedef struct {
    moy_usb_dev_t dev[MOY_USB_DEVS];
    uint8_t ndev;
    struct {
        uint8_t report[MOY_USB_REPORT];
        uint8_t report_n;
        uint8_t rx[MOY_USB_RX];
        uint16_t head, n;
    } io[MOY_USB_DEVS];
    uint32_t src;
    int started;
} usb_t;
static usb_t *U;

static const moy_usb_ops_t *sim_ops(void);

__attribute__((weak)) const moy_usb_ops_t *moy_usb_board_ops(void) {
    return NULL;
}

static const moy_usb_ops_t *ops(void) {
    const moy_usb_ops_t *o = moy_usb_board_ops();
    return o != NULL ? o : sim_ops();
}

static int s_sim_on;

int moy_usb_present(void) {
    return moy_usb_board_ops() != NULL || s_sim_on;
}

static void post(uint8_t ev, uint8_t addr, int32_t b) {
    moy_input_t *t = moy_input_kernel();
    if (t != NULL && U != NULL && U->src != 0) {
        moy_input_post(t, U->src, MOY_EV_USB, addr, ev, (uint32_t)b);
    }
}

static int held(uint32_t owner) {
    return owner != 0 && owner == s_owner;
}

static int find(uint8_t addr) {
    for (int i = 0; i < U->ndev; i++) {
        if (U->dev[i].addr == addr) {
            return i;
        }
    }
    return -1;
}

static int alloc(void) {
    if (U == NULL) {
        #if defined(MOY_INPUT_BOARD)
        U = heap_caps_calloc(1, sizeof(*U), MALLOC_CAP_SPIRAM);
        #else
        U = calloc(1, sizeof(*U));
        #endif
    }
    return U != NULL;
}

int moy_usb_open(uint32_t owner) {
    if (owner == 0 || !moy_usb_present()) {
        return -MOY_USB_ENODEV;
    }
    if (s_owner == owner) {
        return 0;
    }
    if (s_owner != 0) {
        return -MOY_USB_EBUSY;
    }
    if (!alloc()) {
        return -MOY_USB_ENODEV;
    }
    moy_input_t *t = moy_input_kernel();
    char was[8];
    if (U->src != 0 && (t == NULL || moy_input_source_name(t, U->src, was, sizeof(was)) != MOY_INPUT_OK
                        || strcmp(was, "usb") != 0)) {
        U->src = 0;                     // the table it was opened on is gone
    }
    if (U->src == 0) {
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "usb", MOY_EV_FIFO, 32, &src) == MOY_INPUT_OK) {
            U->src = src;
        }
    }
    if (!U->started) {
        int rc = ops()->start();
        if (rc != 0) {
            return rc;
        }
        U->started = 1;
    }
    MOY_DRV_LOCK(&s_lock);
    for (int i = 0; i < MOY_USB_DEVS; i++) {
        U->io[i].report_n = 0;
        U->io[i].head = U->io[i].n = 0;
    }
    MOY_DRV_UNLOCK(&s_lock);
    s_owner = owner;
    return 0;
}

int moy_usb_close(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_USB_EBADF;
    }
    s_owner = 0;
    return 0;
}

uint32_t moy_usb_sweep(uint32_t owner) {
    return moy_usb_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_usb_owner(void) {
    return s_owner;
}

uint32_t moy_usb_source(void) {
    return U != NULL ? U->src : 0;
}

uint32_t moy_usb_count(void) {
    return U != NULL ? U->ndev : 0;
}

int moy_usb_device(uint32_t i, moy_usb_dev_t *out) {
    if (U == NULL) {
        return -MOY_USB_ENOENT;
    }
    MOY_DRV_LOCK(&s_lock);
    int ok = i < U->ndev;
    if (ok) {
        *out = U->dev[i];
    }
    MOY_DRV_UNLOCK(&s_lock);
    return ok ? 0 : -MOY_USB_ENOENT;
}

// The holder's device at `addr` of class `cls` (any class: 0xff): its index.
static int device(uint32_t owner, uint8_t addr, int cls) {
    if (!held(owner)) {
        return -MOY_USB_EBADF;
    }
    int i = find(addr);
    if (i < 0) {
        return -MOY_USB_ENODEV;
    }
    if (cls != 0xff && U->dev[i].cls != cls) {
        return -MOY_USB_EINVAL;
    }
    return i;
}

int moy_usb_report(uint32_t owner, uint8_t addr, uint8_t *out, size_t cap) {
    int i = device(owner, addr, MOY_USB_HID);
    if (i < 0) {
        return i;
    }
    MOY_DRV_LOCK(&s_lock);
    size_t n = U->io[i].report_n;
    n = n < cap ? n : cap;
    memcpy(out, U->io[i].report, n);
    MOY_DRV_UNLOCK(&s_lock);
    return (int)n;
}

int moy_usb_serial(uint32_t owner, uint8_t addr, uint32_t baud) {
    int i = device(owner, addr, MOY_USB_CDC);
    if (i < 0) {
        return i;
    }
    if (baud < 300 || baud > 4000000) {
        return -MOY_USB_EINVAL;
    }
    return ops()->line(addr, baud);
}

int moy_usb_write(uint32_t owner, uint8_t addr, const uint8_t *d, size_t n) {
    int i = device(owner, addr, 0xff);
    if (i < 0) {
        return i;
    }
    if (U->dev[i].cls != MOY_USB_CDC && U->dev[i].cls != MOY_USB_MIDI) {
        return -MOY_USB_EINVAL;
    }
    if (n == 0 || n > 512) {
        return -MOY_USB_EINVAL;
    }
    return ops()->send(addr, d, n);
}

int moy_usb_read(uint32_t owner, uint8_t addr, uint8_t *out, size_t cap) {
    int i = device(owner, addr, 0xff);
    if (i < 0) {
        return i;
    }
    MOY_DRV_LOCK(&s_lock);
    size_t n = U->io[i].n < cap ? U->io[i].n : cap;
    for (size_t k = 0; k < n; k++) {
        out[k] = U->io[i].rx[(U->io[i].head + k) % MOY_USB_RX];
    }
    U->io[i].head = (uint16_t)((U->io[i].head + n) % MOY_USB_RX);
    U->io[i].n = (uint16_t)(U->io[i].n - n);
    MOY_DRV_UNLOCK(&s_lock);
    return (int)n;
}

// -- what the bus reports ------------------------------------------------------------

void moy_usb_on_attach(const moy_usb_dev_t *d) {
    if (U == NULL) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    int i = find(d->addr);
    if (i < 0 && U->ndev < MOY_USB_DEVS) {
        i = U->ndev++;
    }
    if (i >= 0) {
        U->dev[i] = *d;
        U->io[i].report_n = 0;
        U->io[i].head = U->io[i].n = 0;
    }
    MOY_DRV_UNLOCK(&s_lock);
    if (i >= 0) {
        post(MOY_USB_EV_ATTACH, d->addr, d->cls);
    }
}

void moy_usb_on_detach(uint8_t addr) {
    if (U == NULL) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    int i = find(addr);
    if (i >= 0) {
        for (int k = i; k + 1 < U->ndev; k++) {
            U->dev[k] = U->dev[k + 1];
            U->io[k] = U->io[k + 1];
        }
        U->ndev--;
    }
    MOY_DRV_UNLOCK(&s_lock);
    if (i >= 0) {
        post(MOY_USB_EV_DETACH, addr, 0);
    }
}

void moy_usb_on_in(uint8_t addr, const uint8_t *d, size_t n) {
    if (U == NULL || n == 0) {
        return;
    }
    MOY_DRV_LOCK(&s_lock);
    int i = find(addr);
    int cls = i >= 0 ? U->dev[i].cls : -1;
    int32_t b = 0;
    if (cls == MOY_USB_HID) {
        size_t k = n < MOY_USB_REPORT ? n : MOY_USB_REPORT;
        memcpy(U->io[i].report, d, k);
        U->io[i].report_n = (uint8_t)k;
        b = (int32_t)k;
    } else if (cls >= 0 && s_owner != 0) {
        for (size_t k = 0; k < n; k++) {
            if (U->io[i].n == MOY_USB_RX) {         // the oldest byte goes
                U->io[i].head = (uint16_t)((U->io[i].head + 1) % MOY_USB_RX);
                U->io[i].n--;
            }
            U->io[i].rx[(U->io[i].head + U->io[i].n) % MOY_USB_RX] = d[k];
            U->io[i].n++;
        }
        b = U->io[i].n;
    }
    MOY_DRV_UNLOCK(&s_lock);
    if (cls == MOY_USB_MIDI) {
        moy_midi_usb_in(addr, d, n);    // the `midi` API's port on it, if one is open
    }
    if (cls >= 0 && s_owner != 0) {
        post(cls == MOY_USB_HID ? MOY_USB_EV_REPORT : MOY_USB_EV_DATA, addr, b);
    }
}

void moy_usb_on_sent(uint8_t addr, int err) {
    if (s_owner != 0) {
        post(MOY_USB_EV_SENT, addr, err);
    }
}

// -- the host's stand-in ---------------------------------------------------------------
// Devices a test declares: what the console sends one is kept, and a line's
// baud; each op completes before it returns.

#if !defined(MOY_INPUT_BOARD)

#define SIM_OUT 512
static struct {
    uint8_t addr;
    uint8_t out[SIM_OUT];
    uint16_t n;
    uint32_t baud;
} s_sim[MOY_USB_DEVS];

static int sim_at(uint8_t addr) {
    for (int i = 0; i < MOY_USB_DEVS; i++) {
        if (s_sim[i].addr == addr) {
            return i;
        }
    }
    return -1;
}

static int sim_start(void) {
    return s_sim_on ? 0 : -MOY_USB_ENODEV;
}

static int sim_line(uint8_t addr, uint32_t baud) {
    int i = sim_at(addr);
    if (i < 0) {
        return -MOY_USB_ENODEV;
    }
    s_sim[i].baud = baud;
    return 0;
}

static int sim_send(uint8_t addr, const uint8_t *d, size_t n) {
    int i = sim_at(addr);
    if (i < 0) {
        return -MOY_USB_ENODEV;
    }
    size_t k = n < SIM_OUT - s_sim[i].n ? n : (size_t)(SIM_OUT - s_sim[i].n);
    memcpy(s_sim[i].out + s_sim[i].n, d, k);
    s_sim[i].n = (uint16_t)(s_sim[i].n + k);
    moy_usb_on_sent(addr, 0);
    return 0;
}

static const moy_usb_ops_t s_sim_ops = {sim_start, sim_line, sim_send};

static const moy_usb_ops_t *sim_ops(void) {
    return &s_sim_ops;
}

void moy_usb_sim(int on) {
    s_sim_on = on;
}

int moy_usb_sim_attach(uint8_t addr, uint8_t cls, uint8_t kind, uint16_t vid, uint16_t pid,
                       const char *name) {
    int i = sim_at(0);
    if (addr == 0 || i < 0 || !alloc()) {
        return -MOY_USB_EINVAL;
    }
    memset(&s_sim[i], 0, sizeof(s_sim[i]));
    s_sim[i].addr = addr;
    moy_usb_dev_t d;
    memset(&d, 0, sizeof(d));
    d.addr = addr;
    d.cls = cls;
    d.kind = kind;
    d.iface_class = cls == MOY_USB_HID ? 3 : cls == MOY_USB_CDC ? 2 : cls == MOY_USB_MIDI ? 1 : 0xff;
    d.vid = vid;
    d.pid = pid;
    strncpy(d.name, name ? name : "", MOY_USB_NAME);
    moy_usb_on_attach(&d);
    return 0;
}

void moy_usb_sim_detach(uint8_t addr) {
    int i = sim_at(addr);
    if (i >= 0) {
        s_sim[i].addr = 0;
    }
    moy_usb_on_detach(addr);
}

void moy_usb_sim_in(uint8_t addr, const uint8_t *d, size_t n) {
    moy_usb_on_in(addr, d, n);
}

int moy_usb_sim_out(uint8_t addr, uint8_t *out, size_t cap, uint32_t *baud) {
    int i = sim_at(addr);
    if (i < 0) {
        return -MOY_USB_ENODEV;
    }
    size_t n = s_sim[i].n < cap ? s_sim[i].n : cap;
    memcpy(out, s_sim[i].out, n);
    s_sim[i].n = 0;
    *baud = s_sim[i].baud;
    return (int)n;
}

#else

static int sim_none(void) {
    return -MOY_USB_ENODEV;
}

static const moy_usb_ops_t *sim_ops(void) {
    static const moy_usb_ops_t none = {sim_none, NULL, NULL};
    return &none;
}

void moy_usb_sim(int on) {
    (void)on;
}

int moy_usb_sim_attach(uint8_t addr, uint8_t cls, uint8_t kind, uint16_t vid, uint16_t pid,
                       const char *name) {
    (void)addr, (void)cls, (void)kind, (void)vid, (void)pid, (void)name;
    return -MOY_USB_ENODEV;
}

void moy_usb_sim_detach(uint8_t addr) {
    (void)addr;
}

void moy_usb_sim_in(uint8_t addr, const uint8_t *d, size_t n) {
    (void)addr, (void)d, (void)n;
}

int moy_usb_sim_out(uint8_t addr, uint8_t *out, size_t cap, uint32_t *baud) {
    (void)addr, (void)out, (void)cap, (void)baud;
    return -MOY_USB_ENODEV;
}

#endif

void moy_usb_reset(void) {
    s_owner = 0;
    #if !defined(MOY_INPUT_BOARD)
    memset(s_sim, 0, sizeof(s_sim));
    #endif
    if (U != NULL) {
        U->ndev = 0;
        U->started = 0;
        if (U->src != 0) {
            moy_input_ring_close(moy_input_kernel(), U->src);
            U->src = 0;
        }
    }
}
