// The `ble` device API by handle (native/moy_kernel/moy_dev.h's ble row):
// general Bluetooth LE for a compiled program, on its own task, over this
// module's driver (moy_gatt.h), its one holder the lease table's
// MOY_LEASE_BLE. Its events are read through a kernel input reader on the
// driver's ring, each one's body taken from the driver as the record is made.

#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_gatt.h"
#include "moy_input.h"

#define HEAD 12u                // a record's head: event, n (u16), a, b (i32)
#define REC_MAX (HEAD + MOY_GATT_VALUE)
#define NAME_MAX 29u            // what one advertisement carries of a name

typedef struct {
    int reader;
    int32_t target;             // where a write goes, -1: nowhere yet
    uint8_t response;
    uint16_t pend_n;            // a record made that did not fit
    uint8_t pend[REC_MAX];
} ble_t;

static ble_t *s_ble;                    // MOY_DEV_ROWS, made at the first open

static void put16(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *q, uint32_t v) {
    put16(q, v);
    put16(q + 2, v >> 16);
}

// "aa:bb:cc:dd:ee:ff" as the driver holds an address (little-endian).
static bool addr_of(const char *v, size_t n, uint8_t out[6]) {
    if (n != 17) {
        return false;
    }
    for (int i = 0; i < 6; i++) {
        if ((i < 5 && v[3 * i + 2] != ':') || !moy_dev_hex(v + 3 * i, 2, &out[5 - i], 1)) {
            return false;
        }
    }
    return true;
}

// A UUID: 16-bit hex (0x.. or not) or the 128-bit string, little-endian.
static bool uuid_of(const char *v, size_t n, moy_gatt_uuid_t *u) {
    if (n >= 2 && v[0] == '0' && (v[1] == 'x' || v[1] == 'X')) {
        v += 2;
        n -= 2;
    }
    if (n >= 1 && n <= 4) {
        char pad[4] = { '0', '0', '0', '0' };
        uint8_t be[2];
        memcpy(pad + 4 - n, v, n);
        if (!moy_dev_hex(pad, 4, be, 2)) {
            return false;
        }
        *u = (moy_gatt_uuid_t){ .len = 2, .v = { be[1], be[0] } };
        return true;
    }
    char hex[32];
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        if (v[i] == '-') {
            continue;
        }
        if (k == sizeof hex) {
            return false;
        }
        hex[k++] = v[i];
    }
    uint8_t be[16];
    if (k != sizeof hex || !moy_dev_hex(hex, k, be, 16)) {
        return false;
    }
    u->len = 16;
    for (int i = 0; i < 16; i++) {
        u->v[i] = be[15 - i];
    }
    return true;
}

static int32_t gatt_rc(int rc) {
    return rc < 0 ? moy_dev_errno(rc) : 0;
}

static int32_t ble_set(uint32_t slot, uint32_t pid, const char *cfg, size_t n) {
    ble_t *b = &s_ble[slot];
    enum { NONE, SCAN, CONNECT, OFF, FIND, READ, SUB, UNSUB, ADV, ADV_OFF } act = NONE;
    int acts = 0;
    int64_t v = 0, interval = 200, target = -1, response = -1;
    uint8_t addr[6], mfg[MOY_GATT_ADV];
    size_t mfg_n = 0;
    bool has_data = false, has_interval = false;
    char name[NAME_MAX + 1];
    moy_gatt_uuid_t uuid;
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        bool ok = true, is_act = true;
        if (moy_dev_is(w.k, w.kn, "scan")) {
            act = SCAN;
            ok = moy_dev_in_range(&w, 1, 60000, &v);
        } else if (moy_dev_is(w.k, w.kn, "connect")) {
            act = moy_dev_is(w.v, w.vn, "off") ? OFF : CONNECT;
            ok = act == OFF || addr_of(w.v, w.vn, addr);
        } else if (moy_dev_is(w.k, w.kn, "find")) {
            act = FIND;
            ok = uuid_of(w.v, w.vn, &uuid);
        } else if (moy_dev_is(w.k, w.kn, "read") || moy_dev_is(w.k, w.kn, "subscribe")
                   || moy_dev_is(w.k, w.kn, "unsubscribe")) {
            act = *w.k == 'r' ? READ : *w.k == 's' ? SUB : UNSUB;
            ok = moy_dev_in_range(&w, 1, 0xffff, &v);
        } else if (moy_dev_is(w.k, w.kn, "advertise")) {
            act = moy_dev_is(w.v, w.vn, "off") ? ADV_OFF : ADV;
            ok = w.vn <= NAME_MAX;
            if (ok) {
                memcpy(name, w.v, w.vn);
                name[w.vn] = '\0';
            }
        } else {
            is_act = false;
            if (moy_dev_is(w.k, w.kn, "data")) {
                const char *d = w.v;
                size_t dn = w.vn;
                if (dn >= 2 && d[0] == '0' && (d[1] == 'x' || d[1] == 'X')) {
                    d += 2;
                    dn -= 2;
                }
                mfg_n = dn / 2;
                ok = dn % 2 == 0 && mfg_n <= sizeof mfg && moy_dev_hex(d, dn, mfg, mfg_n);
                has_data = true;
            } else if (moy_dev_is(w.k, w.kn, "interval")) {
                ok = moy_dev_in_range(&w, 20, 10240, &interval);
                has_interval = true;
            } else if (moy_dev_is(w.k, w.kn, "write")) {
                ok = moy_dev_in_range(&w, 1, 0xffff, &target);
            } else if (moy_dev_is(w.k, w.kn, "response")) {
                ok = moy_dev_in_range(&w, 0, 1, &response);
            } else {
                ok = false;
            }
        }
        acts += is_act;
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    if (got < 0 || acts > 1 || ((has_data || has_interval) && act != ADV)) {
        return MOY_DEV_EBAD;            // one action a set; data and interval are advertise's
    }
    if (target >= 0) {
        b->target = (int32_t)target;
    }
    if (response >= 0) {
        b->response = (uint8_t)response;
    }
    switch (act) {
        case SCAN: return gatt_rc(moy_gatt_scan(pid, (uint32_t)v));
        case OFF: return gatt_rc(moy_gatt_disconnect(pid));
        case CONNECT: {
            uint8_t type = 0;
            moy_gatt_seen_t s;
            for (uint32_t i = 0; moy_gatt_seen(i, &s) == 0; i++) {
                if (memcmp(s.addr, addr, 6) == 0) {
                    type = s.type;
                }
            }
            return gatt_rc(moy_gatt_connect(pid, addr, type));
        }
        case FIND: return gatt_rc(moy_gatt_find(pid, &uuid));
        case READ: return gatt_rc(moy_gatt_read(pid, (uint16_t)v));
        case SUB: case UNSUB: return gatt_rc(moy_gatt_subscribe(pid, (uint16_t)v, act == SUB));
        case ADV:
            return gatt_rc(moy_gatt_advertise(pid, name, mfg, mfg_n, (uint32_t)interval));
        case ADV_OFF: return gatt_rc(moy_gatt_stop_advertising(pid));
        default: return 0;
    }
}

static void ble_close(uint32_t slot, uint32_t pid) {
    ble_t *b = &s_ble[slot];
    moy_input_t *t = moy_input_kernel();
    if (b->reader >= 0 && t != NULL) {
        moy_input_reader_free(t, b->reader);
    }
    b->reader = -1;
    moy_gatt_close(pid);
}

static int32_t ble_open(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg,
                        size_t cn) {
    (void)perms;
    if (MOY_DEV_ROWS(s_ble) == NULL) {
        return MOY_DEV_ENOMEM;
    }
    ble_t *b = &s_ble[slot];
    b->reader = -1;
    b->target = -1;
    b->response = 1;
    b->pend_n = 0;
    moy_input_t *t = moy_input_kernel();
    if (t == NULL) {
        return MOY_DEV_EABSENT;         // no input table: no ring to read
    }
    int32_t r = moy_dev_errno(moy_gatt_open(pid));
    if (r < 0) {
        return r;
    }
    if (moy_input_reader_kept(t, MOY_EV_BIT(MOY_EV_BLE), &b->reader) != 0) {
        b->reader = -1;
        r = MOY_DEV_EFULL;
    } else {
        moy_input_reader_only(t, b->reader, moy_gatt_source());
        r = ble_set(slot, pid, cfg, cn);
    }
    if (r < 0) {
        ble_close(slot, pid);
    }
    return r;
}

// Record (ev, a, b) with its body into the pending slot.
static void make(ble_t *b, uint32_t pid, uint16_t ev, uint32_t a, uint32_t v) {
    uint8_t *body = b->pend + HEAD;
    int n = 0;
    if (ev == MOY_GATT_EV_ADV) {
        moy_gatt_seen_t s;
        if (moy_gatt_seen(a, &s) == 0) {
            for (int i = 0; i < 6; i++) {
                body[i] = s.addr[5 - i];        // as printed
            }
            size_t k = 0;
            while (k < MOY_GATT_NAME && s.name[k] != '\0') {
                k++;
            }
            memcpy(body + 6, s.name, k);
            n = 6 + (int)k;
        }
    } else if (ev == MOY_GATT_EV_READ && v == 0 && a > 0) {
        n = moy_gatt_value(pid, body, MOY_GATT_VALUE);
    } else if (ev == MOY_GATT_EV_NOTIFY) {
        uint16_t h;
        n = moy_gatt_notified(pid, body, MOY_GATT_VALUE, &h);
    }
    if (n < 0) {
        n = 0;
    }
    put16(b->pend, ev);
    put16(b->pend + 2, (uint32_t)n);
    put32(b->pend + 4, a);
    put32(b->pend + 8, v);
    b->pend_n = (uint16_t)(HEAD + (uint32_t)n);
}

static int32_t ble_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    ble_t *b = &s_ble[slot];
    moy_input_t *t = moy_input_kernel();
    size_t got = 0;
    for (;;) {
        if (b->pend_n == 0) {
            moy_event_t ev;
            if (t == NULL || b->reader < 0 || moy_input_events(t, b->reader, &ev, 1) == 0) {
                break;
            }
            if (ev.kind != MOY_EV_BLE) {
                continue;
            }
            make(b, pid, ev.id, ev.a, ev.b);
        }
        if (cap - got < b->pend_n) {
            break;
        }
        memcpy(out + got, b->pend, b->pend_n);
        got += b->pend_n;
        b->pend_n = 0;
    }
    return got == 0 && b->pend_n != 0 ? MOY_DEV_EFULL : (int32_t)got;
}

static int32_t ble_write(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n) {
    const ble_t *b = &s_ble[slot];
    if (b->target < 0) {
        return MOY_DEV_EBAD;            // set write=<handle> first
    }
    int32_t r = gatt_rc(moy_gatt_write(pid, (uint16_t)b->target, data, n, b->response));
    return r < 0 ? r : (int32_t)n;
}

static int32_t ble_get(uint32_t slot, uint32_t pid, const char *what, size_t n) {
    (void)slot;
    if (moy_dev_is(what, n, "connected")) {
        return moy_gatt_owner() == pid && moy_gatt_state() == MOY_GATT_CONNECTED;
    }
    return MOY_DEV_EBAD;
}

const moy_dev_ops_t moy_dev_ble = {
    .open = ble_open, .close = ble_close, .read = ble_read, .write = ble_write,
    .set = ble_set, .get = ble_get,
};
