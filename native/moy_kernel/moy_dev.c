// A compiled program's device handles, the kernel's (moy_dev.h has the
// contract).

#include "moy_dev.h"

#include <string.h>

#include "moy_input.h"
#include "moy_lease.h"

// The kernel's input table, where a pin's edges ring: weak, for an image
// without it, where a pin has no edges.
moy_input_t *moy_input_kernel(void) __attribute__((weak));
int moy_input_reader_kept(moy_input_t *t, uint32_t kinds, int *r) __attribute__((weak));
int moy_input_reader_only(moy_input_t *t, int r, uint32_t h) __attribute__((weak));
int moy_input_reader_free(moy_input_t *t, int r) __attribute__((weak));
size_t moy_input_events(moy_input_t *t, int r, moy_event_t *out, size_t n)
    __attribute__((weak));

enum { K_GPIO = 1, K_PINS = 2 };
enum { EDGE_RISING = 1, EDGE_FALLING = 2 };
#define EDGE_BYTES 8u
#define BATCH 8u
#define PIN_MAX 64

typedef struct {
    uint32_t pid;               // 0: free
    uint32_t lease;             // a gpio handle's lease
    uint64_t took;              // a pins handle's pins, each leased by it
    uint32_t src;               // a gpio handle's edge ring, 0: none
    int reader;                 // ... its reader, -1: none
    uint8_t kind;
    uint8_t pin;
    uint8_t trigger;            // EDGE_* bits
    uint8_t gen;
} dev_t;

static dev_t s_dev[MOY_DEV_HANDLES];

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

static bool is(const char *p, size_t n, const char *s) {
    return strlen(s) == n && memcmp(p, s, n) == 0;
}

uint32_t moy_dev_perm_bit(const char *perm, size_t n) {
    static const char *const named[] = { "lease:gpio", "pins", "mic", "lora", "ble", "usb",
                                         "midi", "camera", "media", "media:url", "http" };
    for (uint32_t i = 0; i < sizeof named / sizeof named[0]; i++) {
        if (is(perm, n, named[i])) {
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

typedef struct {
    const char *p, *end;
} words_t;

typedef struct {
    const char *k, *v;
    size_t kn, vn;
} word_t;

// The next word into *w: 1, 0 at the end, -1 for a word that is no key=value.
static int next_word(words_t *it, word_t *w) {
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

// A value as a number (decimal or 0x.. hex, with a sign): false for none.
static bool number(const char *v, size_t n, int64_t *out) {
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
    while ((got = next_word(&it, &w)) > 0) {
        int64_t v;
        if (is(w.k, w.kn, "mode")) {
            c->mode = is(w.v, w.vn, "in") ? MOY_PIN_MODE_IN : is(w.v, w.vn, "out")
                      ? MOY_PIN_MODE_OUT : is(w.v, w.vn, "od") ? MOY_PIN_MODE_OD : -2;
        } else if (is(w.k, w.kn, "pull")) {
            c->pull = is(w.v, w.vn, "up") ? MOY_PIN_PULL_UP : is(w.v, w.vn, "down")
                      ? MOY_PIN_PULL_DOWN : is(w.v, w.vn, "none") ? MOY_PIN_PULL_NONE : -2;
        } else if (is(w.k, w.kn, "value")) {
            c->level = number(w.v, w.vn, &v) ? v != 0 : -2;
        } else if (edges && is(w.k, w.kn, "edges")) {
            c->edges = is(w.v, w.vn, "rising") ? EDGE_RISING : is(w.v, w.vn, "falling")
                       ? EDGE_FALLING : is(w.v, w.vn, "both") ? EDGE_RISING | EDGE_FALLING
                       : is(w.v, w.vn, "off") ? 0 : -2;
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

static dev_t *row_of(uint32_t pid, int32_t h) {
    if (!(h & MOY_DEV_BASE)) {
        return NULL;
    }
    uint32_t slot = (uint32_t)h & 0xffu, gen = ((uint32_t)h >> 8) & 0xffu;
    if (slot >= MOY_DEV_HANDLES || s_dev[slot].pid != pid || s_dev[slot].gen != gen) {
        return NULL;
    }
    return &s_dev[slot];
}

static int32_t handle_of(const dev_t *d) {
    return (int32_t)(MOY_DEV_BASE | (uint32_t)d->gen << 8 | (uint32_t)(d - s_dev));
}

static dev_t *row_new(uint32_t pid, uint8_t kind) {
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
        dev_t *d = &s_dev[i];
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

static void edges_off(dev_t *d) {
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    if (d->reader >= 0 && t != NULL) {
        moy_input_reader_free(t, d->reader);
    }
    d->reader = -1;
    if (d->src != 0) {
        moy_lease_edge_close(d->lease, d->pin);
    }
    d->src = 0;
    d->trigger = 0;
}

static int32_t edges_on(dev_t *d, int trigger) {
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
    dev_t *d = row_new(pid, K_GPIO);
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

static int32_t gpio_set(dev_t *d, const char *cfg, size_t n) {
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
static int32_t gpio_read(dev_t *d, uint8_t *out, size_t cap) {
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
}

// -- pins ----------------------------------------------------------------------------

// Pin `pin` for the pins handle `d`: its process's lease of it, taken at its
// first use (an output where `out`, else an input) and remembered as the
// handle's to give back.
static int32_t pins_take(dev_t *d, int pin, bool out) {
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

static int32_t pins_set(dev_t *d, const char *cfg, size_t n) {
    for (int pass = 0; pass < 2; pass++) {
        words_t it = { cfg, cfg + n };
        word_t w;
        int got;
        while ((got = next_word(&it, &w)) > 0) {
            int pin = pin_of(w.k, w.kn);
            int64_t v;
            if (pin < 0 || !number(w.v, w.vn, &v) || (v != 0 && v != 1)) {
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

static void pins_close(dev_t *d) {
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
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
        if (s_dev[i].pid == pid && s_dev[i].kind == K_PINS) {
            return MOY_DEV_EBUSY;
        }
    }
    dev_t *d = row_new(pid, K_PINS);
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

// -- the rows ------------------------------------------------------------------------

static void close_row(dev_t *d) {
    if (d->kind == K_GPIO) {
        edges_off(d);
        moy_lease_drop(d->lease);
    } else {
        pins_close(d);
    }
    d->pid = 0;
}

int32_t moy_dev_open(uint32_t pid, uint32_t perms, const char *name, size_t n,
                     const char *cfg, size_t cn) {
    int pin = pin_of(name, n);
    if (pid == 0 || (pin < 0 && !is(name, n, "pins"))) {
        return MOY_DEV_PASS;
    }
    moy_lease_lock();
    int32_t r = pin >= 0 ? gpio_open(pid, perms, pin, cfg, cn) : pins_open(pid, perms, cfg, cn);
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
    dev_t *d = row_of((pid), (h));                  \
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
    WITH_ROW(pid, h, d, { r = d->kind == K_GPIO ? gpio_read(d, out, cap) : MOY_DEV_EBAD; })
}

int32_t moy_dev_write(uint32_t pid, int32_t h, const uint8_t *data, size_t n) {
    (void)data;
    (void)n;
    WITH_ROW(pid, h, d, { r = MOY_DEV_EBAD; })
}

int32_t moy_dev_xfer(uint32_t pid, int32_t h, const uint8_t *data, size_t n, uint8_t *out,
                     size_t cap) {
    (void)data;
    (void)n;
    (void)out;
    (void)cap;
    WITH_ROW(pid, h, d, { r = MOY_DEV_EBAD; })
}

int32_t moy_dev_set(uint32_t pid, int32_t h, const char *cfg, size_t n) {
    WITH_ROW(pid, h, d, { r = d->kind == K_GPIO ? gpio_set(d, cfg, n) : pins_set(d, cfg, n); })
}

int32_t moy_dev_get(uint32_t pid, int32_t h, const char *what, size_t n) {
    WITH_ROW(pid, h, d, {
        if (d->kind == K_GPIO) {
            r = is(what, n, "value") ? moy_lease_pin_level(d->pin) : MOY_DEV_EBAD;
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
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
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
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
        n += s_dev[i].pid != 0;
    }
    moy_lease_unlock();
    return n;
}

void moy_dev_reset(void) {
    moy_lease_lock();
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
        if (s_dev[i].pid != 0) {
            close_row(&s_dev[i]);
        }
    }
    moy_lease_unlock();
}
