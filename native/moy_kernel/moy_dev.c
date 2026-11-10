// A compiled program's device handles, the kernel's (moy_dev.h has the
// contract).

#include "moy_dev.h"

#include <stdlib.h>
#include <string.h>

#include "moy_dev_kinds.h"
#include "moy_devbus.h"
#include "moy_lease.h"

// The kernel's input table, where a pin's edges ring: weak, for an image
// without it, and compiled out where a board denies moy_input (the Zero); in
// both a pin has no edges.
#if __has_include("moy_input.h")
#include "moy_input.h"
#define MOY_DEV_EDGES 1
moy_input_t *moy_input_kernel(void) __attribute__((weak));
int moy_input_reader_kept(moy_input_t *t, uint32_t kinds, int *r) __attribute__((weak));
int moy_input_reader_only(moy_input_t *t, int r, uint32_t h) __attribute__((weak));
int moy_input_reader_free(moy_input_t *t, int r) __attribute__((weak));
size_t moy_input_events(moy_input_t *t, int r, moy_event_t *out, size_t n)
    __attribute__((weak));
#else
#define MOY_DEV_EDGES 0
#endif

// A board is known by the header it has (moy_lease.c does the same).
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define DEV_PSRAM 1
#endif

enum { K_GPIO = 1, K_PINS, K_UART, K_SPI, K_I2C, K_PWM, K_ADC, K_API };
enum { EDGE_RISING = 1, EDGE_FALLING = 2 };
#define EDGE_BYTES 8u
#define BATCH 8u
#define PIN_MAX 64

typedef struct {
    uint32_t pid;               // 0: free
    uint32_t lease;             // its lease (a pins handle's: none)
    uint64_t took;              // a pins handle's pins, each leased by it
    uint32_t src;               // a gpio handle's edge ring, 0: none
    int reader;                 // ... its reader, -1: none
    uint8_t kind;
    uint8_t pin;                // gpio, pwm, adc: its pin
    uint8_t unit;               // uart, spi: its unit
    uint8_t trigger;            // EDGE_* bits
    uint8_t gen;
    int16_t addr;               // i2c: the device's address, -1: none yet
    int8_t slot;                // pwm: its devbus slot
    uint32_t freq, duty;        // pwm
    union {
        moy_devbus_uart_t uart;
        moy_devbus_spi_t spi;
    } cfg;
} dev_row_t;

// MOY_DEV_HANDLES rows, PSRAM on a board, made at the first open and kept:
// a boot whose programs open no device holds none of it, and a row's gen
// outlives its close.
static dev_row_t *s_dev;

static int32_t lease_rc(int rc) {
    switch (rc) {
        case MOY_LEASE_OK: return 0;
        case MOY_LEASE_FULL: return MOY_DEV_EFULL;
        case MOY_LEASE_NOMEM: return MOY_DEV_ENOMEM;
        case MOY_LEASE_BUSY: return MOY_DEV_EBUSY;
        case MOY_LEASE_BAD: return MOY_DEV_EBAD;
        default: return MOY_DEV_EABSENT;
    }
}

// -- the permissions ---------------------------------------------------------------

bool moy_dev_is(const char *p, size_t n, const char *s) {
    return strlen(s) == n && memcmp(p, s, n) == 0;
}

uint32_t moy_dev_perm_bit(const char *perm, size_t n) {
    static const char *const named[] = { "lease:gpio", "pins", "mic", "lora", "ble", "usb",
                                         "midi", "camera", "media", "media:url", "http" };
    for (uint32_t i = 0; i < sizeof named / sizeof named[0]; i++) {
        if (moy_dev_is(perm, n, named[i])) {
            return 1u << i;
        }
    }
    if (n > 6 && memcmp(perm, "lease:", 6) == 0) {
        int at = moy_lease_offer_find(perm + 6, n - 6);
        if (at >= 0 && at < 32 - MOY_DEV_P_ROW) {
            return 1u << (MOY_DEV_P_ROW + at);
        }
    }
    return 0u;
}

// -- the grammar ---------------------------------------------------------------------

int moy_dev_word(words_t *it, word_t *w) {
    while (it->p < it->end && (*it->p == ' ' || *it->p == '\t' || *it->p == '\n'
                               || *it->p == '\r')) {
        it->p++;
    }
    if (it->p >= it->end) {
        return 0;
    }
    const char *s = it->p;
    while (it->p < it->end && *it->p != ' ' && *it->p != '\t' && *it->p != '\n'
           && *it->p != '\r') {
        it->p++;
    }
    const char *eq = memchr(s, '=', (size_t)(it->p - s));
    if (eq == NULL || eq == s) {
        return -1;
    }
    w->k = s;
    w->kn = (size_t)(eq - s);
    w->v = eq + 1;
    w->vn = (size_t)(it->p - eq - 1);
    return 1;
}

bool moy_dev_number(const char *v, size_t n, int64_t *out) {
    bool neg = false;
    size_t i = 0;
    if (i < n && (v[i] == '-' || v[i] == '+')) {
        neg = v[i] == '-';
        i++;
    }
    int base = 10;
    if (n - i > 2 && v[i] == '0' && (v[i + 1] == 'x' || v[i + 1] == 'X')) {
        base = 16;
        i += 2;
    }
    if (i >= n) {
        return false;
    }
    int64_t x = 0;
    for (; i < n; i++) {
        char c = v[i];
        int d = c >= '0' && c <= '9' ? c - '0'
                : base == 16 && c >= 'a' && c <= 'f' ? c - 'a' + 10
                : base == 16 && c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (d < 0 || x > (INT64_MAX - d) / base) {
            return false;
        }
        x = x * base + d;
    }
    *out = neg ? -x : x;
    return true;
}

// "gpio<n>" as its pin: -1 for anything else.
static int pin_of(const char *p, size_t n) {
    if (n < 5 || n > 6 || memcmp(p, "gpio", 4) != 0) {
        return -1;
    }
    int pin = 0;
    for (size_t i = 4; i < n; i++) {
        if (p[i] < '0' || p[i] > '9') {
            return -1;
        }
        pin = pin * 10 + (p[i] - '0');
    }
    return pin < PIN_MAX ? pin : -1;
}

// A pin's cfg: its mode, pull and level (-1 each where it names none) and,
// where `edges` is non-NULL, its edges (-1 none, 0 off, else EDGE_* bits).
typedef struct {
    int mode, pull, level, edges;
} pin_cfg_t;

static int32_t gpio_cfg(const char *cfg, size_t n, pin_cfg_t *c, bool edges) {
    c->mode = c->pull = c->level = c->edges = -1;
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        int64_t v;
        if (moy_dev_is(w.k, w.kn, "mode")) {
            c->mode = moy_dev_is(w.v, w.vn, "in") ? MOY_PIN_MODE_IN : moy_dev_is(w.v, w.vn, "out")
                      ? MOY_PIN_MODE_OUT : moy_dev_is(w.v, w.vn, "od") ? MOY_PIN_MODE_OD : -2;
        } else if (moy_dev_is(w.k, w.kn, "pull")) {
            c->pull = moy_dev_is(w.v, w.vn, "up") ? MOY_PIN_PULL_UP : moy_dev_is(w.v, w.vn, "down")
                      ? MOY_PIN_PULL_DOWN : moy_dev_is(w.v, w.vn, "none") ? MOY_PIN_PULL_NONE : -2;
        } else if (moy_dev_is(w.k, w.kn, "value")) {
            c->level = moy_dev_number(w.v, w.vn, &v) ? v != 0 : -2;
        } else if (edges && moy_dev_is(w.k, w.kn, "edges")) {
            c->edges = moy_dev_is(w.v, w.vn, "rising") ? EDGE_RISING : moy_dev_is(w.v, w.vn, "falling")
                       ? EDGE_FALLING : moy_dev_is(w.v, w.vn, "both") ? EDGE_RISING | EDGE_FALLING
                       : moy_dev_is(w.v, w.vn, "off") ? 0 : -2;
        } else {
            return MOY_DEV_EBAD;
        }
        if (c->mode == -2 || c->pull == -2 || c->level == -2 || c->edges == -2) {
            return MOY_DEV_EBAD;
        }
    }
    return got < 0 ? MOY_DEV_EBAD : 0;
}

// -- the table -----------------------------------------------------------------------

static dev_row_t *row_of(uint32_t pid, int32_t h) {
    if (!(h & MOY_DEV_BASE)) {
        return NULL;
    }
    uint32_t slot = (uint32_t)h & 0xffu, gen = ((uint32_t)h >> 8) & 0xffu;
    if (s_dev == NULL || slot >= MOY_DEV_HANDLES || s_dev[slot].pid != pid
        || s_dev[slot].gen != gen) {
        return NULL;
    }
    return &s_dev[slot];
}

static int32_t handle_of(const dev_row_t *d) {
    return (int32_t)(MOY_DEV_BASE | (uint32_t)d->gen << 8 | (uint32_t)(d - s_dev));
}

static dev_row_t *row_new(uint32_t pid, uint8_t kind) {
    if (s_dev == NULL) {
        #ifdef DEV_PSRAM
        s_dev = heap_caps_calloc(MOY_DEV_HANDLES, sizeof *s_dev, MALLOC_CAP_SPIRAM);
        #endif
        if (s_dev == NULL && (s_dev = calloc(MOY_DEV_HANDLES, sizeof *s_dev)) == NULL) {
            return NULL;
        }
    }
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
        dev_row_t *d = &s_dev[i];
        if (d->pid == 0) {
            uint8_t gen = (uint8_t)(d->gen + 1u);
            memset(d, 0, sizeof *d);
            d->gen = gen != 0 ? gen : 1u;
            d->pid = pid;
            d->kind = kind;
            d->reader = -1;
            return d;
        }
    }
    return NULL;
}

// -- gpio<n> -----------------------------------------------------------------------

static void edges_off(dev_row_t *d) {
#if MOY_DEV_EDGES
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    if (d->reader >= 0 && t != NULL) {
        moy_input_reader_free(t, d->reader);
    }
#endif
    d->reader = -1;
    if (d->src != 0) {
        moy_lease_edge_close(d->lease, d->pin);
    }
    d->src = 0;
    d->trigger = 0;
}

static int32_t edges_on(dev_row_t *d, int trigger) {
#if !MOY_DEV_EDGES
    (void)d;
    (void)trigger;
    return MOY_DEV_EABSENT;
#else
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    if (t == NULL || moy_input_reader_kept == NULL) {
        return MOY_DEV_EABSENT;
    }
    uint32_t src = 0;
    int32_t rc = lease_rc(moy_lease_edge(d->lease, d->pin, MOY_DEV_EDGE_DEPTH, false, &src));
    if (rc != 0) {
        return rc;
    }
    d->src = src;
    int r;
    if (moy_input_reader_kept(t, MOY_EV_BIT(MOY_EV_EDGE), &r) != 0) {
        edges_off(d);
        return MOY_DEV_EFULL;
    }
    moy_input_reader_only(t, r, src);
    d->reader = r;
    d->trigger = (uint8_t)trigger;
    return 0;
#endif
}

static int32_t pin_apply(int pin, const pin_cfg_t *c) {
    if (c->mode < 0 && c->pull < 0 && c->level < 0) {
        return 0;
    }
    return lease_rc(moy_lease_pin_set(pin, c->mode, c->pull, c->level));
}

static int32_t gpio_open(uint32_t pid, uint32_t perms, int pin, const char *cfg, size_t cn) {
    pin_cfg_t c;
    int32_t rc = gpio_cfg(cfg, cn, &c, false);
    if (rc != 0) {
        return rc;
    }
    if (!(perms & MOY_DEV_P_GPIO)) {
        return MOY_DEV_EDENIED;
    }
    dev_row_t *d = row_new(pid, K_GPIO);
    if (d == NULL) {
        return MOY_DEV_EFULL;
    }
    uint32_t h;
    rc = lease_rc(moy_lease_take(pid, MOY_LEASE_GPIO, 0, 1ull << pin, 0, &h));
    if (rc == 0) {
        d->lease = h;
        d->pin = (uint8_t)pin;
        rc = pin_apply(pin, &c);
        if (rc != 0) {
            moy_lease_drop(h);
        }
    }
    if (rc != 0) {
        d->pid = 0;
        return rc;
    }
    return handle_of(d);
}

static int32_t gpio_set(dev_row_t *d, const char *cfg, size_t n) {
    pin_cfg_t c;
    int32_t rc = gpio_cfg(cfg, n, &c, true);
    if (rc == 0 && (c.mode >= 0 || c.pull >= 0)) {
        pin_cfg_t m = { c.mode, c.pull, -1, -1 };
        rc = pin_apply(d->pin, &m);
    }
    if (rc == 0 && c.level >= 0) {
        rc = lease_rc(moy_lease_pin_set(d->pin, -1, -1, c.level));
    }
    if (rc == 0 && c.edges >= 0) {
        edges_off(d);
        if (c.edges != 0) {
            rc = edges_on(d, c.edges);
        }
    }
    return rc;
}

static void put32(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
    q[2] = (uint8_t)(v >> 16);
    q[3] = (uint8_t)(v >> 24);
}

// The edges since the last read, as many whole records as `cap` holds.
static int32_t gpio_read(dev_row_t *d, uint8_t *out, size_t cap) {
#if !MOY_DEV_EDGES
    (void)d;
    (void)out;
    (void)cap;
    return 0;
#else
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    if (d->reader < 0 || t == NULL || moy_input_events == NULL) {
        return 0;
    }
    if (cap < EDGE_BYTES) {
        return MOY_DEV_EFULL;
    }
    size_t want = cap / EDGE_BYTES, got = 0;
    if (want > INT32_MAX / EDGE_BYTES) {
        want = INT32_MAX / EDGE_BYTES;
    }
    moy_event_t ev[BATCH];
    while (got < want) {
        size_t ask = want - got < BATCH ? want - got : BATCH;
        size_t n = moy_input_events(t, d->reader, ev, ask);
        for (size_t i = 0; i < n; i++) {
            if (ev[i].kind != MOY_EV_EDGE) {
                continue;
            }
            uint32_t level = ev[i].b ? 1u : 0u;
            if (d->trigger & (level ? EDGE_RISING : EDGE_FALLING)) {
                put32(out + got * EDGE_BYTES, ev[i].t_us);
                put32(out + got * EDGE_BYTES + 4, level);
                got++;
            }
        }
        if (n < ask) {
            break;
        }
    }
    return (int32_t)(got * EDGE_BYTES);
#endif
}

// -- pins ----------------------------------------------------------------------------

// Pin `pin` for the pins handle `d`: its process's lease of it, taken at its
// first use (an output where `out`, else an input) and remembered as the
// handle's to give back.
static int32_t pins_take(dev_row_t *d, int pin, bool out) {
    uint32_t h = moy_lease_of_pin(pin);
    moy_lease_row_t r;
    if (h != 0) {
        if (moy_lease_get(h, &r) != MOY_LEASE_OK || r.owner != d->pid) {
            return MOY_DEV_EBUSY;
        }
        return out ? lease_rc(moy_lease_pin_set(pin, MOY_PIN_MODE_OUT, -1, -1)) : 0;
    }
    int32_t rc = lease_rc(moy_lease_take(d->pid, MOY_LEASE_GPIO, 0, 1ull << pin,
                                         MOY_LEASE_PINS, &h));
    if (rc != 0) {
        return rc;
    }
    d->took |= 1ull << pin;
    return lease_rc(moy_lease_pin_set(pin, out ? MOY_PIN_MODE_OUT : MOY_PIN_MODE_IN, -1, -1));
}

static int32_t pins_set(dev_row_t *d, const char *cfg, size_t n) {
    for (int pass = 0; pass < 2; pass++) {
        words_t it = { cfg, cfg + n };
        word_t w;
        int got;
        while ((got = moy_dev_word(&it, &w)) > 0) {
            int pin = pin_of(w.k, w.kn);
            int64_t v;
            if (pin < 0 || !moy_dev_number(w.v, w.vn, &v) || (v != 0 && v != 1)) {
                return MOY_DEV_EBAD;
            }
            if (pass == 1) {
                int32_t rc = pins_take(d, pin, true);
                if (rc == 0) {
                    rc = lease_rc(moy_lease_pin_set(pin, -1, -1, (int)v));
                }
                if (rc != 0) {
                    return rc;
                }
            }
        }
        if (got < 0) {
            return MOY_DEV_EBAD;
        }
    }
    return 0;
}

static void pins_close(dev_row_t *d) {
    for (int pin = 0; pin < PIN_MAX; pin++) {
        if (!(d->took & (1ull << pin))) {
            continue;
        }
        uint32_t h = moy_lease_of_pin(pin);
        moy_lease_row_t r;
        if (h != 0 && moy_lease_get(h, &r) == MOY_LEASE_OK && r.owner == d->pid) {
            moy_lease_drop(h);
        }
    }
    d->took = 0;
}

static int32_t pins_open(uint32_t pid, uint32_t perms, const char *cfg, size_t cn) {
    if (!(perms & MOY_DEV_P_PINS)) {
        return MOY_DEV_EDENIED;
    }
    moy_lease_offer_t o;
    int at = moy_lease_offer_find("gpio", 4);
    if (at < 0 || moy_lease_offer_at((uint32_t)at, &o) != MOY_LEASE_OK || o.pins == 0) {
        return MOY_DEV_EABSENT;
    }
    for (uint32_t i = 0; s_dev != NULL && i < MOY_DEV_HANDLES; i++) {
        if (s_dev[i].pid == pid && s_dev[i].kind == K_PINS) {
            return MOY_DEV_EBUSY;
        }
    }
    dev_row_t *d = row_new(pid, K_PINS);
    if (d == NULL) {
        return MOY_DEV_EFULL;
    }
    int32_t rc = pins_set(d, cfg, cn);
    if (rc != 0) {
        pins_close(d);
        d->pid = 0;
        return rc;
    }
    return handle_of(d);
}

// -- the buses -----------------------------------------------------------------------

bool moy_dev_in_range(const word_t *w, int64_t lo, int64_t hi, int64_t *out) {
    return moy_dev_number(w->v, w->vn, out) && *out >= lo && *out <= hi;
}

// `name` as kind `pre` and its number ("uart1": 1): -1 for anything else.
static int unit_of(const char *name, size_t n, const char *pre) {
    size_t p = strlen(pre);
    if (n <= p || n > p + 2u || memcmp(name, pre, p) != 0) {
        return -1;
    }
    int u = 0;
    for (size_t i = p; i < n; i++) {
        if (name[i] < '0' || name[i] > '9') {
            return -1;
        }
        u = u * 10 + (name[i] - '0');
    }
    return u;
}

// The pins a bus's open names, -1 each where it names none.
typedef struct {
    int pin[3];
} bus_pins_t;

static int32_t uart_cfg(const char *cfg, size_t n, moy_devbus_uart_t *c, bus_pins_t *p) {
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        int64_t v = 0;
        bool ok;
        if (moy_dev_is(w.k, w.kn, "parity")) {
            ok = true;
            c->parity = moy_dev_is(w.v, w.vn, "none") ? MOY_DEVBUS_PARITY_NONE
                        : moy_dev_is(w.v, w.vn, "even") ? MOY_DEVBUS_PARITY_EVEN
                        : moy_dev_is(w.v, w.vn, "odd") ? MOY_DEVBUS_PARITY_ODD : (ok = false, 0);
        } else if (moy_dev_is(w.k, w.kn, "baudrate")) {
            ok = moy_dev_in_range(&w, 1, 5000000, &v);
            c->baud = (uint32_t)v;
        } else if (moy_dev_is(w.k, w.kn, "bits")) {
            ok = moy_dev_in_range(&w, 5, 8, &v);
            c->bits = (uint8_t)v;
        } else if (moy_dev_is(w.k, w.kn, "stop")) {
            ok = moy_dev_in_range(&w, 1, 2, &v);
            c->stop = (uint8_t)v;
        } else if (moy_dev_is(w.k, w.kn, "timeout")) {
            ok = moy_dev_in_range(&w, 0, INT32_MAX, &v);
        } else if (p != NULL && moy_dev_is(w.k, w.kn, "rxbuf")) {
            ok = moy_dev_in_range(&w, 0, 32768, &v);
            c->rxbuf = (uint32_t)v;
        } else if (p != NULL && moy_dev_is(w.k, w.kn, "txbuf")) {
            ok = moy_dev_in_range(&w, 0, 32768, &v);
            c->txbuf = (uint32_t)v;
        } else if (p != NULL && (moy_dev_is(w.k, w.kn, "tx") || moy_dev_is(w.k, w.kn, "rx"))) {
            ok = moy_dev_in_range(&w, 0, PIN_MAX - 1, &v);
            p->pin[*w.k == 't' ? 0 : 1] = (int)v;
        } else {
            ok = false;
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    return got < 0 ? MOY_DEV_EBAD : 0;
}

static int32_t spi_cfg(const char *cfg, size_t n, moy_devbus_spi_t *c, bus_pins_t *p) {
    static const char *const pins[] = { "sck", "mosi", "miso" };
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        int64_t v = 0;
        bool ok = false;
        if (moy_dev_is(w.k, w.kn, "firstbit")) {
            ok = moy_dev_is(w.v, w.vn, "msb") || moy_dev_is(w.v, w.vn, "lsb");
            c->lsb = moy_dev_is(w.v, w.vn, "lsb");
        } else if (moy_dev_is(w.k, w.kn, "baudrate")) {
            ok = moy_dev_in_range(&w, 1, 80000000, &v);
            c->baud = (uint32_t)v;
        } else if (moy_dev_is(w.k, w.kn, "polarity") || moy_dev_is(w.k, w.kn, "phase")) {
            ok = moy_dev_in_range(&w, 0, 1, &v);
            uint8_t bit = *w.k == 'p' && w.k[1] == 'o' ? 2u : 1u;
            c->mode = (uint8_t)(v ? c->mode | bit : c->mode & ~bit);
        } else if (moy_dev_is(w.k, w.kn, "bits")) {
            ok = moy_dev_in_range(&w, 8, 8, &v);
        } else {
            for (int i = 0; p != NULL && i < 3; i++) {
                if (moy_dev_is(w.k, w.kn, pins[i])) {
                    ok = moy_dev_in_range(&w, 0, PIN_MAX - 1, &v);
                    p->pin[i] = (int)v;
                }
            }
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    return got < 0 ? MOY_DEV_EBAD : 0;
}

// The handle `d` with its lease taken of `pins`, for `open` to run on.
static int32_t bus_lease(dev_row_t *d, uint8_t cls, uint8_t unit, uint64_t pins) {
    uint32_t h;
    int32_t rc = lease_rc(moy_lease_take(d->pid, cls, unit, pins, 0, &h));
    if (rc == 0) {
        d->lease = h;
    }
    return rc;
}

static uint64_t bits_of(const bus_pins_t *p, int n) {
    uint64_t m = 0;
    for (int i = 0; i < n; i++) {
        m |= 1ull << p->pin[i];
    }
    return m;
}

int32_t moy_dev_uart_take(uint32_t pid, uint32_t perms, const char *row, size_t n,
                          uint32_t baud, uint32_t *lease, uint8_t *unit) {
    moy_lease_offer_t o;
    int at = moy_lease_offer_find(row, n);
    if (at < 0 || moy_lease_offer_at((uint32_t)at, &o) != MOY_LEASE_OK
        || o.cls != MOY_LEASE_UART) {
        return MOY_DEV_EBAD;
    }
    if (at >= 32 - MOY_DEV_P_ROW || !(perms & (1u << (MOY_DEV_P_ROW + at)))) {
        return MOY_DEV_EDENIED;
    }
    moy_devbus_uart_t c = { baud, 8u, MOY_DEVBUS_PARITY_NONE, 1u, 256u, 512u };
    int32_t rc = lease_rc(moy_lease_take(pid, MOY_LEASE_UART, o.unit,
                                         1ull << o.pin[0] | 1ull << o.pin[1], 0, lease));
    if (rc == 0 && (rc = moy_devbus_uart_open(o.unit, o.pin[0], o.pin[1], &c)) != 0) {
        moy_lease_drop(*lease);
    }
    *unit = o.unit;
    return rc;
}

void moy_dev_uart_give(uint32_t lease, uint8_t unit) {
    moy_devbus_uart_close(unit);
    moy_lease_drop(lease);
}

// uart<n> and spi<n>: a unit the board offers, under `lease:<row>`.
static int32_t unit_open(dev_row_t *d, uint32_t perms, const char *name, size_t n,
                         const char *cfg, size_t cn) {
    bool uart = d->kind == K_UART;
    bus_pins_t p = { { -1, -1, -1 } };
    int32_t rc;
    if (uart) {
        d->cfg.uart = (moy_devbus_uart_t){ 115200u, 8u, MOY_DEVBUS_PARITY_NONE, 1u, 256u, 0u };
        rc = uart_cfg(cfg, cn, &d->cfg.uart, &p);
    } else {
        d->cfg.spi = (moy_devbus_spi_t){ 500000u, 0u, false };
        rc = spi_cfg(cfg, cn, &d->cfg.spi, &p);
    }
    if (rc != 0) {
        return rc;
    }
    moy_lease_offer_t o;
    int at = moy_lease_offer_find(name, n);
    if (at < 0 || moy_lease_offer_at((uint32_t)at, &o) != MOY_LEASE_OK
        || o.cls != (uart ? MOY_LEASE_UART : MOY_LEASE_SPI)) {
        return MOY_DEV_EBAD;
    }
    if (at >= 32 - MOY_DEV_P_ROW || !(perms & (1u << (MOY_DEV_P_ROW + at)))) {
        return MOY_DEV_EDENIED;
    }
    int npins = uart ? 2 : 3;
    for (int i = 0; i < npins; i++) {
        if (p.pin[i] < 0) {
            p.pin[i] = o.pin[i];
        }
    }
    if (!uart && (p.pin[0] == p.pin[1] || p.pin[0] == p.pin[2] || p.pin[1] == p.pin[2])) {
        return MOY_DEV_EBAD;
    }
    d->unit = o.unit;
    rc = bus_lease(d, uart ? MOY_LEASE_UART : MOY_LEASE_SPI, o.unit, bits_of(&p, npins));
    if (rc == 0) {
        rc = uart ? moy_devbus_uart_open(o.unit, p.pin[0], p.pin[1], &d->cfg.uart)
                  : moy_devbus_spi_open(o.unit, p.pin[0], p.pin[1], p.pin[2], &d->cfg.spi);
        if (rc != 0) {
            moy_lease_drop(d->lease);
        }
    }
    return rc;
}

static int32_t i2c_open(dev_row_t *d, uint32_t perms, const char *cfg, size_t cn) {
    int64_t scl = -1, sda = -1, freq = 400000, addr = -1;
    words_t it = { cfg, cfg + cn };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        bool ok = moy_dev_is(w.k, w.kn, "scl") ? moy_dev_in_range(&w, 0, PIN_MAX - 1, &scl)
                  : moy_dev_is(w.k, w.kn, "sda") ? moy_dev_in_range(&w, 0, PIN_MAX - 1, &sda)
                  : moy_dev_is(w.k, w.kn, "freq") ? moy_dev_in_range(&w, 1, 1000000, &freq)
                  : moy_dev_is(w.k, w.kn, "addr") ? moy_dev_in_range(&w, 0, 127, &addr) : false;
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    if (got < 0 || scl < 0 || sda < 0 || scl == sda) {
        return MOY_DEV_EBAD;
    }
    if (!(perms & MOY_DEV_P_GPIO)) {
        return MOY_DEV_EDENIED;
    }
    d->addr = (int16_t)addr;
    int32_t rc = bus_lease(d, MOY_LEASE_GPIO, 0, 1ull << scl | 1ull << sda);
    if (rc == 0) {
        rc = moy_devbus_i2c_open((int)scl, (int)sda, (uint32_t)freq);
        if (rc != 0) {
            moy_lease_drop(d->lease);
        }
    }
    return rc;
}

static int32_t pwm_cfg(dev_row_t *d, const char *cfg, size_t n) {
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        int64_t v;
        bool ok = moy_dev_is(w.k, w.kn, "freq") ? moy_dev_in_range(&w, 1, 40000000, &v)
                  : moy_dev_is(w.k, w.kn, "duty") ? moy_dev_in_range(&w, 0, 65535, &v) : false;
        if (!ok) {
            return MOY_DEV_EBAD;
        }
        if (*w.k == 'f') {
            d->freq = (uint32_t)v;
        } else {
            d->duty = (uint32_t)v;
        }
    }
    return got < 0 ? MOY_DEV_EBAD : 0;
}

// pwm<n> and adc<n>: a pin of the gpio row, under `lease:gpio`.
static int32_t pin_bus_open(dev_row_t *d, uint32_t perms, int pin, const char *cfg, size_t cn) {
    int32_t rc = 0;
    d->freq = 5000u;                    // machine.PWM's defaults
    d->duty = 32768u;
    if (d->kind == K_PWM) {
        rc = pwm_cfg(d, cfg, cn);
    } else {
        words_t it = { cfg, cfg + cn };
        word_t w;
        rc = moy_dev_word(&it, &w) != 0 ? MOY_DEV_EBAD : 0;   // an adc takes no key
    }
    if (rc != 0) {
        return rc;
    }
    if (!(perms & MOY_DEV_P_GPIO)) {
        return MOY_DEV_EDENIED;
    }
    d->pin = (uint8_t)pin;
    rc = bus_lease(d, MOY_LEASE_GPIO, 0, 1ull << pin);
    if (rc != 0) {
        return rc;
    }
    if (d->kind == K_PWM) {
        rc = moy_devbus_pwm_open(d->pid, pin, d->freq, d->duty);
        d->slot = (int8_t)rc;
    } else {
        rc = moy_devbus_adc_open(pin);
    }
    if (rc < 0) {
        moy_lease_drop(d->lease);
        return rc;
    }
    return 0;
}

static int32_t bus_open(uint32_t pid, uint32_t perms, uint8_t kind, int pin, const char *name,
                        size_t n, const char *cfg, size_t cn) {
    dev_row_t *d = row_new(pid, kind);
    if (d == NULL) {
        return MOY_DEV_EFULL;
    }
    int32_t rc = kind == K_UART || kind == K_SPI ? unit_open(d, perms, name, n, cfg, cn)
                 : kind == K_I2C ? i2c_open(d, perms, cfg, cn)
                 : pin_bus_open(d, perms, pin, cfg, cn);
    if (rc != 0) {
        d->pid = 0;
        return rc;
    }
    return handle_of(d);
}

static void bus_close(dev_row_t *d) {
    switch (d->kind) {
        case K_UART: moy_devbus_uart_close(d->unit); break;
        case K_SPI: moy_devbus_spi_close(d->unit); break;
        case K_I2C: moy_devbus_i2c_close(); break;
        case K_PWM: moy_devbus_pwm_close(d->slot); break;
        default: moy_devbus_adc_close(d->pin); break;
    }
    moy_lease_drop(d->lease);
}

static int32_t bus_read(dev_row_t *d, uint8_t *out, size_t cap) {
    switch (d->kind) {
        case K_UART: return moy_devbus_uart_read(d->unit, out, cap);
        case K_SPI: return cap ? moy_devbus_spi_xfer(d->unit, NULL, 0, out, cap) : 0;
        case K_I2C:
            return d->addr < 0 ? MOY_DEV_EBAD
                   : cap ? moy_devbus_i2c_read((uint8_t)d->addr, out, cap) : 0;
        default: return MOY_DEV_EBAD;
    }
}

static int32_t bus_write(dev_row_t *d, const uint8_t *data, size_t n) {
    switch (d->kind) {
        case K_UART: return moy_devbus_uart_write(d->unit, data, n);
        case K_SPI: return moy_devbus_spi_xfer(d->unit, data, 0, NULL, n);
        case K_I2C:
            return d->addr < 0 ? MOY_DEV_EBAD : moy_devbus_i2c_write((uint8_t)d->addr, data, n);
        default: return MOY_DEV_EBAD;
    }
}

static int32_t bus_xfer(dev_row_t *d, const uint8_t *data, size_t n, uint8_t *out, size_t cap) {
    if (d->kind == K_SPI) {
        return cap < n ? MOY_DEV_EFULL : moy_devbus_spi_xfer(d->unit, data, 0, out, n);
    }
    if (d->kind != K_I2C) {
        return MOY_DEV_EBAD;
    }
    if (d->addr < 0) {
        return MOY_DEV_EBAD;
    }
    if (cap == 0u) {
        int32_t r = moy_devbus_i2c_write((uint8_t)d->addr, data, n);
        return r < 0 ? r : 0;
    }
    return moy_devbus_i2c_write_read((uint8_t)d->addr, data, n, out, cap);
}

static int32_t bus_set(dev_row_t *d, const char *cfg, size_t n) {
    int32_t rc;
    switch (d->kind) {
        case K_UART: {
            moy_devbus_uart_t c = d->cfg.uart;
            rc = uart_cfg(cfg, n, &c, NULL);
            if (rc == 0 && (rc = moy_devbus_uart_set(d->unit, &c)) == 0) {
                d->cfg.uart = c;
            }
            return rc;
        }
        case K_SPI: {
            moy_devbus_spi_t c = d->cfg.spi;
            rc = spi_cfg(cfg, n, &c, NULL);
            if (rc == 0 && (rc = moy_devbus_spi_set(d->unit, &c)) == 0) {
                d->cfg.spi = c;
            }
            return rc;
        }
        case K_I2C: {
            int64_t addr = d->addr;
            words_t it = { cfg, cfg + n };
            word_t w;
            int got;
            while ((got = moy_dev_word(&it, &w)) > 0) {
                if (!moy_dev_is(w.k, w.kn, "addr") || !moy_dev_in_range(&w, 0, 127, &addr)) {
                    return MOY_DEV_EBAD;
                }
            }
            if (got < 0) {
                return MOY_DEV_EBAD;
            }
            d->addr = (int16_t)addr;
            return 0;
        }
        case K_PWM: {
            uint32_t freq = d->freq, duty = d->duty;
            rc = pwm_cfg(d, cfg, n);
            if (rc == 0) {
                rc = moy_devbus_pwm_set(d->slot, d->freq != freq ? d->freq : 0u, d->duty);
            }
            if (rc != 0) {
                d->freq = freq;
                d->duty = duty;
            }
            return rc;
        }
        default:
            return MOY_DEV_EBAD;
    }
}

static int32_t bus_get(dev_row_t *d, const char *what, size_t n) {
    switch (d->kind) {
        case K_UART: return moy_dev_is(what, n, "any") ? moy_devbus_uart_any(d->unit) : MOY_DEV_EBAD;
        case K_I2C: return moy_dev_is(what, n, "addr") ? d->addr : MOY_DEV_EBAD;
        case K_PWM:
            return moy_dev_is(what, n, "freq") ? (int32_t)d->freq
                   : moy_dev_is(what, n, "duty") ? (int32_t)d->duty : MOY_DEV_EBAD;
        case K_ADC: return moy_dev_is(what, n, "value") ? moy_devbus_adc_read(d->pin) : MOY_DEV_EBAD;
        default: return MOY_DEV_EBAD;
    }
}

// -- the rows ------------------------------------------------------------------------

static uint32_t slot_of(const dev_row_t *d) {
    return (uint32_t)(d - s_dev);
}

static void close_row(dev_row_t *d) {
    if (d->kind == K_API) {
        moy_dev_kind_close(slot_of(d));
    } else if (d->kind == K_GPIO) {
        edges_off(d);
        moy_lease_drop(d->lease);
    } else if (d->kind == K_PINS) {
        pins_close(d);
    } else {
        bus_close(d);
    }
    d->pid = 0;
}

// The bus a name opens (its kind, and a pwm's or an adc's pin): 0 for none.
static uint8_t bus_kind(const char *name, size_t n, int *pin) {
    *pin = -1;
    if (moy_dev_is(name, n, "i2c")) {
        return K_I2C;
    }
    if (unit_of(name, n, "uart") >= 0) {
        return K_UART;
    }
    if (unit_of(name, n, "spi") >= 0) {
        return K_SPI;
    }
    int at = unit_of(name, n, "pwm");
    if (at >= 0 && at < PIN_MAX) {
        *pin = at;
        return K_PWM;
    }
    at = unit_of(name, n, "adc");
    if (at >= 0 && at < PIN_MAX) {
        *pin = at;
        return K_ADC;
    }
    return 0;
}

static int32_t api_open(uint32_t pid, uint32_t perms, int api, const char *cfg, size_t cn) {
    dev_row_t *d = row_new(pid, K_API);
    if (d == NULL) {
        return MOY_DEV_EFULL;
    }
    int32_t r = moy_dev_kind_open(slot_of(d), api, pid, perms, cfg, cn);
    if (r < 0) {
        d->pid = 0;
        return r;
    }
    return handle_of(d);
}

int32_t moy_dev_open(uint32_t pid, uint32_t perms, const char *name, size_t n,
                     const char *cfg, size_t cn) {
    int pin = pin_of(name, n), bpin;
    uint8_t bus = bus_kind(name, n, &bpin);
    int api = moy_dev_kind(name, n);
    if (pid == 0 || (pin < 0 && bus == 0 && api == 0 && !moy_dev_is(name, n, "pins"))) {
        return MOY_DEV_PASS;
    }
    moy_lease_lock();
    int32_t r = api != 0 ? api_open(pid, perms, api, cfg, cn)
                : pin >= 0 ? gpio_open(pid, perms, pin, cfg, cn)
                : bus != 0 ? bus_open(pid, perms, bus, bpin, name, n, cfg, cn)
                : pins_open(pid, perms, cfg, cn);
    moy_lease_unlock();
    return r;
}

// Row `h` of process `pid` locked for `fn`: PASS for a handle not this
// table's, ABSENT for one not the caller's.
#define WITH_ROW(pid, h, d, body)                   \
    if (!((h) & MOY_DEV_BASE)) {                    \
        return MOY_DEV_PASS;                        \
    }                                               \
    moy_lease_lock();                               \
    dev_row_t *d = row_of((pid), (h));              \
    int32_t r = MOY_DEV_EABSENT;                    \
    if (d != NULL) {                                \
        body;                                       \
    }                                               \
    moy_lease_unlock();                             \
    return r;

int32_t moy_dev_close(uint32_t pid, int32_t h) {
    WITH_ROW(pid, h, d, { close_row(d); r = 0; })
}

int32_t moy_dev_read(uint32_t pid, int32_t h, uint8_t *out, size_t cap) {
    WITH_ROW(pid, h, d, {
        r = d->kind == K_API ? moy_dev_kind_read(slot_of(d), out, cap)
            : d->kind == K_GPIO ? gpio_read(d, out, cap)
            : d->kind == K_PINS ? MOY_DEV_EBAD : bus_read(d, out, cap);
    })
}

int32_t moy_dev_write(uint32_t pid, int32_t h, const uint8_t *data, size_t n) {
    WITH_ROW(pid, h, d, {
        r = d->kind == K_API ? moy_dev_kind_write(slot_of(d), data, n)
            : d->kind == K_GPIO || d->kind == K_PINS ? MOY_DEV_EBAD : bus_write(d, data, n);
    })
}

int32_t moy_dev_xfer(uint32_t pid, int32_t h, const uint8_t *data, size_t n, uint8_t *out,
                     size_t cap) {
    WITH_ROW(pid, h, d, {
        r = d->kind == K_API ? moy_dev_kind_xfer(slot_of(d), data, n, out, cap)
            : d->kind == K_GPIO || d->kind == K_PINS ? MOY_DEV_EBAD
            : bus_xfer(d, data, n, out, cap);
    })
}

int32_t moy_dev_set(uint32_t pid, int32_t h, const char *cfg, size_t n) {
    WITH_ROW(pid, h, d, {
        r = d->kind == K_API ? moy_dev_kind_set(slot_of(d), cfg, n)
            : d->kind == K_GPIO ? gpio_set(d, cfg, n)
            : d->kind == K_PINS ? pins_set(d, cfg, n) : bus_set(d, cfg, n);
    })
}

int32_t moy_dev_get(uint32_t pid, int32_t h, const char *what, size_t n) {
    WITH_ROW(pid, h, d, {
        if (d->kind == K_API) {
            r = moy_dev_kind_get(slot_of(d), what, n);
        } else if (d->kind == K_GPIO) {
            r = moy_dev_is(what, n, "value") ? moy_lease_pin_level(d->pin) : MOY_DEV_EBAD;
        } else if (d->kind != K_PINS) {
            r = bus_get(d, what, n);
        } else {
            int pin = pin_of(what, n);
            r = pin < 0 ? MOY_DEV_EBAD : pins_take(d, pin, false);
            if (r == 0) {
                r = moy_lease_pin_level(pin);
            }
        }
    })
}

uint32_t moy_dev_sweep(uint32_t pid) {
    uint32_t n = 0;
    if (pid == 0) {
        return 0;
    }
    moy_lease_lock();
    for (uint32_t i = 0; s_dev != NULL && i < MOY_DEV_HANDLES; i++) {
        if (s_dev[i].pid == pid) {
            close_row(&s_dev[i]);
            n++;
        }
    }
    moy_lease_unlock();
    return n;
}

uint32_t moy_dev_count(void) {
    uint32_t n = 0;
    moy_lease_lock();
    for (uint32_t i = 0; s_dev != NULL && i < MOY_DEV_HANDLES; i++) {
        n += s_dev[i].pid != 0;
    }
    moy_lease_unlock();
    return n;
}

void moy_dev_reset(void) {
    moy_lease_lock();
    for (uint32_t i = 0; s_dev != NULL && i < MOY_DEV_HANDLES; i++) {
        if (s_dev[i].pid != 0) {
            close_row(&s_dev[i]);
        }
    }
    moy_lease_unlock();
}
