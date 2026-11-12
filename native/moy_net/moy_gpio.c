// The Zero's pins as the webhost's /gpio route (moy_net.h): a batch of digital
// writes and reads, refused pin by pin against the board's allowlist table.
//
//   GET  /gpio           -> {"v": 1, "pins": [...]}
//   POST /gpio           {"v": 1, "ops": [{"p": 2, "mode": "out", "v": 1},
//                                         {"p": 2, "mode": "read"}]}
//                        -> {"ok": n, "reads": {"2": 1}, "err": [[i, why], ...]}
//
// THE ALLOWLIST IS THE SECURITY MODEL: the pins a kid may drive are the board's
// table (MOY_NET_GPIO_PINS in its mpconfigboard.h, whose comment says what each
// excluded pin would break), and a pin outside it is refused and never
// touched. A bad op is skipped and reported; it never aborts the batch, whose
// neighbours may be the write that turns a motor off. An empty batch is the
// page's probe and answers with the allowlist. Both methods are sealed routes
// of the webhost (moy_webhost.c's header): what reaches here is a signed-in
// page's.
//
// THE PINS ARE LEASED (moy_lease.h, where the image has the table): a write
// takes the pin's lease for the page, a process named "web" (MOY_LEASE_PINS,
// the `pins` permission's digital subset; the page's sign-in is its grant), and keeps it, so the level it drove stays driven and `pins`
// and `ps` show who holds it; the webhost's stop drops them all
// (moy_gpio_release). A pin another process holds -- a cart's lease -- is
// refused "pin N: busy", a read as a write, and never touched. A board that
// offers no lease of a pin has no other holder to meet, and drives it as it is.
//
// The pins themselves are the board's (moy_gpio_pins, moy_gpio_drive,
// moy_gpio_sense in moy_net_port.c): a pin's first touch decides what it is,
// only a write makes it an output, and a read never reconfigures it, so
// reading a lit LED leaves it lit. Inputs are pulled up.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "moy_json.h"
#include "moy_net.h"
#pragma weak moy_gpio_pins
#pragma weak moy_gpio_drive
#pragma weak moy_gpio_sense
#pragma weak moy_gpio_reset

#define GPIO_V 1
#define ERR_MAX 8

// The lease table's calls (moy_lease.h), weak: an image without the kernel
// leases nothing.
#include "moy_lease.h"
#pragma weak moy_lease_take
#pragma weak moy_lease_of_pin
#pragma weak moy_lease_sweep
#include "moy_log.h"
#pragma weak moy_log_spawn
#define LEASE_OK 0
#define LEASE_BAD 4
#define LEASE_GPIO 1
#define LEASE_PINS 0x02u
#define LEASE_PIN_MAX 64

// Kernel memory at the first lease, so a board that never serves /gpio spends
// no internal RAM on it.
static uint32_t s_web;                  // the page's process, 0 until its first lease
static uint32_t *s_lease;               // LEASE_PIN_MAX: the page's lease of each pin, 0 none

// 1 when another process holds `pin`.
static int held_elsewhere(int pin) {
    if (moy_lease_of_pin == NULL || pin < 0 || pin >= LEASE_PIN_MAX) {
        return 0;
    }
    uint32_t h = moy_lease_of_pin(pin);
    return h != 0 && (s_lease == NULL || h != s_lease[pin]);
}

// The page's lease of `pin` before a write: 0, or -1 when it cannot be had.
static int claim(int pin) {
    if (moy_lease_take == NULL || moy_lease_of_pin == NULL || pin < 0
        || pin >= LEASE_PIN_MAX) {
        return 0;
    }
    if (s_lease == NULL) {
        s_lease = moy_net_alloc(LEASE_PIN_MAX * sizeof(uint32_t));
        if (s_lease == NULL) {
            return -1;
        }
        memset(s_lease, 0, LEASE_PIN_MAX * sizeof(uint32_t));
    }
    uint32_t h = moy_lease_of_pin(pin);
    if (h != 0) {
        return h == s_lease[pin] ? 0 : -1;
    }
    s_lease[pin] = 0;
    if (s_web == 0) {
        s_web = moy_log_spawn != NULL ? moy_log_spawn("web") : 0;
    }
    uint32_t nh;
    int r = moy_lease_take(s_web, LEASE_GPIO, 0, 1ull << pin, LEASE_PINS, &nh);
    if (r == LEASE_OK) {
        s_lease[pin] = nh;
        if (moy_gpio_reset != NULL) {
            moy_gpio_reset(pin);        // a lease since may have left it safe
        }
        return 0;
    }
    return r == LEASE_BAD ? 0 : -1;
}

void moy_gpio_release(void) {
    if (s_web != 0 && moy_lease_sweep != NULL) {
        moy_lease_sweep(s_web);
    }
    if (s_lease != NULL) {
        memset(s_lease, 0, LEASE_PIN_MAX * sizeof(uint32_t));
    }
}

typedef struct {
    char *p;
    size_t n, cap;
} buf_t;

static void put(buf_t *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

static void put(buf_t *b, const char *fmt, ...) {
    if (b->n >= b->cap) {
        return;
    }
    va_list ap;
    va_start(ap, fmt);
    int k = vsnprintf(b->p + b->n, b->cap - b->n, fmt, ap);
    va_end(ap);
    if (k > 0) {
        b->n += (size_t)k < b->cap - b->n ? (size_t)k : b->cap - b->n - 1u;
    }
}

static void pin_list(buf_t *b, const uint8_t *pins, int count) {
    put(b, "[");
    for (int i = 0; i < count; i++) {
        put(b, "%s%u", i ? ", " : "", (unsigned)pins[i]);
    }
    put(b, "]");
}

static int allowed(const uint8_t *pins, int count, int64_t n) {
    for (int i = 0; i < count; i++) {
        if (pins[i] == n) {
            return 1;
        }
    }
    return 0;
}

// One op checked: 0 and its pin, mode (1 out, 0 read) and value, or a reason.
static const char *check(const char *o, const char *oe, const uint8_t *pins,
                         int count, int *pin, int *out, int *val, char *why,
                         size_t why_cap) {
    const char *v, *ve;
    int64_t n;
    if (moy_json_kind(o, oe) != MOY_JSON_OBJ) {
        return "op is not an object";
    }
    if (!moy_json_get(o, oe, "p", &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_INT
        || moy_json_int(v, ve, &n) != 1) {
        return "p must be a pin number";
    }
    if (!allowed(pins, count, n)) {
        // %ld: a board's newlib-nano printf has no 64-bit conversion.
        snprintf(why, why_cap, "pin %ld is not on this board's allowlist", (long)n);
        return why;
    }
    *pin = (int)n;
    if (!moy_json_get(o, oe, "mode", &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_STR) {
        return "mode must be 'out' or 'read'";
    }
    if (moy_json_str_is(v, ve, "read", 4)) {
        *out = 0;
        return NULL;
    }
    if (!moy_json_str_is(v, ve, "out", 3)) {
        return "mode must be 'out' or 'read'";
    }
    *out = 1;
    if (!moy_json_get(o, oe, "v", &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_INT
        || moy_json_int(v, ve, &n) != 1 || (n != 0 && n != 1)) {
        return "v must be 0 or 1";
    }
    *val = (int)n;
    return NULL;
}

int moy_gpio_request(int post, const char *target, size_t tn, const char *body,
                     size_t bn, char *out, size_t cap) {
    const uint8_t *pins = NULL;
    int count = moy_gpio_pins != NULL ? moy_gpio_pins(&pins) : 0;
    buf_t b = {out, 0, cap};
    out[0] = '\0';
    (void)target;
    (void)tn;
    if (!post) {
        put(&b, "{\"v\": %d, \"pins\": ", GPIO_V);
        pin_list(&b, pins, count);
        put(&b, "}");
        return 200;
    }
    const char *o = moy_json_ws(body, body + bn);
    const char *oe = moy_json_value(o, body + bn, 0);
    const char *v, *ve, *ops, *ops_e;
    int64_t ver;
    if (oe == NULL || moy_json_ws(oe, body + bn) != body + bn
        || moy_json_kind(o, oe) != MOY_JSON_OBJ
        || !moy_json_get(o, oe, "v", &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_INT
        || moy_json_int(v, ve, &ver) != 1 || ver != GPIO_V
        || !moy_json_get(o, oe, "ops", &ops, &ops_e) || moy_json_kind(ops, ops_e) != MOY_JSON_ARR) {
        put(&b, "{\"error\":\"bad batch\"}");
        return 400;
    }
    moy_json_iter_t it;
    const char *k, *ke, *e, *ee;
    moy_json_iter(&it, ops, ops_e);
    if (!moy_json_next(&it, &k, &ke, &e, &ee)) {
        put(&b, "{\"ok\": 0, \"reads\": {}, \"err\": [], \"pins\": ");
        pin_list(&b, pins, count);
        put(&b, "}");
        return 200;
    }
    int applied = 0, nerr = 0, nread = 0;
    int read_pin[32], read_val[32];
    char errs[ERR_MAX][72];
    int err_at[ERR_MAX];
    uint32_t i = 0;
    do {
        int p = 0, isout = 0, val = 0;
        char why[64];
        const char *bad = check(e, ee, pins, count, &p, &isout, &val, why, sizeof(why));
        if (bad == NULL && (held_elsewhere(p) || (isout && claim(p) != 0))) {
            snprintf(why, sizeof(why), "pin %d: busy", p);
            bad = why;
        }
        if (bad == NULL) {
            if (isout) {
                if (moy_gpio_drive(p, val) != 0) {
                    snprintf(why, sizeof(why), "pin %d: not driven", p);
                    bad = why;
                }
            } else {
                int lv = moy_gpio_sense(p);
                if (lv < 0) {
                    snprintf(why, sizeof(why), "pin %d: not read", p);
                    bad = why;
                } else {
                    int j = 0;
                    while (j < nread && read_pin[j] != p) {
                        j++;
                    }
                    if (j < 32) {
                        read_pin[j] = p;
                        read_val[j] = lv ? 1 : 0;
                        if (j == nread) {
                            nread++;
                        }
                    }
                }
            }
        }
        if (bad != NULL) {
            if (nerr < ERR_MAX) {
                err_at[nerr] = (int)i;
                snprintf(errs[nerr], sizeof(errs[nerr]), "%s", bad);
            }
            nerr++;
        } else {
            applied++;
        }
        i++;
    } while (moy_json_next(&it, &k, &ke, &e, &ee));
    put(&b, "{\"ok\": %d, \"reads\": {", applied);
    for (int j = 0; j < nread; j++) {
        put(&b, "%s\"%d\": %d", j ? ", " : "", read_pin[j], read_val[j]);
    }
    put(&b, "}, \"err\": [");
    for (int j = 0; j < nerr && j < ERR_MAX; j++) {
        put(&b, "%s[%d, \"%s\"]", j ? ", " : "", err_at[j], errs[j]);
    }
    put(&b, "]}");
    return 200;
}
