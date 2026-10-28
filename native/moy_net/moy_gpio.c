// The Zero's pins as the webhost's /gpio route (moy_net.h): a batch of digital
// writes and reads, refused pin by pin against the board's allowlist table.
//
//   GET  /gpio?pin=NNNN  -> {"v": 1, "pins": [...]}
//   POST /gpio           {"v": 1, "ops": [{"p": 2, "mode": "out", "v": 1},
//                                         {"p": 2, "mode": "read"}], "pin": "NNNN"}
//                        -> {"ok": n, "reads": {"2": 1}, "err": [[i, why], ...]}
//
// THE ALLOWLIST IS THE SECURITY MODEL: the pins a kid may drive are the board's
// table (MOY_NET_GPIO_PINS in its mpconfigboard.h, whose comment says what each
// excluded pin would break), and a pin outside it is refused and never
// touched. A bad op is skipped and reported; it never aborts the batch, whose
// neighbours may be the write that turns a motor off. An empty batch is the
// page's probe and answers with the allowlist, behind the pin like every write.
// Both methods are pin-gated: the GET by `?pin=`, the POST in its body.
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

#define GPIO_V 1
#define ERR_MAX 8

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
                     size_t bn, const char *pin, char *out, size_t cap) {
    const uint8_t *pins = NULL;
    int count = moy_gpio_pins != NULL ? moy_gpio_pins(&pins) : 0;
    buf_t b = {out, 0, cap};
    out[0] = '\0';
    int gated = pin != NULL && pin[0] != '\0';
    if (!post) {
        const char *q;
        size_t qn;
        moy_http_query(target, tn, "pin", 3, &q, &qn);
        if (gated && (qn != strlen(pin) || memcmp(q, pin, qn) != 0)) {
            put(&b, "{\"error\":\"pin\"}");
            return 403;
        }
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
    if (gated && !(moy_json_get(o, oe, "pin", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
                   && moy_json_str_is(v, ve, pin, strlen(pin)))) {
        put(&b, "{\"error\":\"pin\"}");
        return 403;
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
