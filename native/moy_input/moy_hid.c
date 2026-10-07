// moy_hid: the BLE HID keyboard central's state machine. moy_hid.h has the
// contract.

#include "moy_hid.h"

#include <stdio.h>
#include <string.h>

const char *const MOY_HID_STATES[] = {
    "off", "disabled", "idle", "scanning", "found", "connecting", "pairing",
    "discovering", "subscribe-retry", "ready", "choose",
};

// -- the pure parts --------------------------------------------------------------

typedef struct {
    uint8_t type;
    const uint8_t *v;
    size_t n;
} adv_field_t;

static bool adv_next(const uint8_t *adv, size_t n, size_t *i, adv_field_t *f) {
    if (*i >= n) {
        return false;
    }
    size_t size = adv[*i];
    if (size == 0 || *i + 1 + size > n) {
        return false;
    }
    f->type = adv[*i + 1];
    f->v = &adv[*i + 2];
    f->n = size - 1;
    *i += 1 + size;
    return true;
}

bool moy_hid_adv_has_hid(const uint8_t *adv, size_t n) {
    size_t i = 0;
    adv_field_t f;
    while (adv_next(adv, n, &i, &f)) {
        if (f.type == 0x02 || f.type == 0x03) {
            for (size_t k = 0; k + 1 < f.n; k += 2) {
                if ((f.v[k] | (f.v[k + 1] << 8)) == MOY_HID_UUID_SERVICE) {
                    return true;
                }
            }
        } else if (f.type == 0x16 && f.n >= 2
                   && (f.v[0] | (f.v[1] << 8)) == MOY_HID_UUID_SERVICE) {
            return true;
        }
    }
    return false;
}

size_t moy_hid_adv_name(const uint8_t *adv, size_t n, char *out, size_t cap) {
    size_t i = 0;
    adv_field_t f;
    size_t got = 0;
    while (adv_next(adv, n, &i, &f)) {
        if (f.type == 0x08 || f.type == 0x09) {
            size_t len = f.n < cap - 1 ? f.n : cap - 1;
            memcpy(out, f.v, len);
            out[len] = 0;
            got = len;
            if (f.type == 0x09) {
                return got;
            }
        }
    }
    return got;
}

bool moy_hid_decode(const uint8_t *r, size_t n, uint8_t *mods, uint8_t keys[6], uint8_t *nkeys) {
    if (n == 9 && r[2] == 0) {
        r++;
        n = 8;
    }
    if (n != 8 || r[1] != 0) {
        return false;
    }
    *mods = r[0];
    *nkeys = 0;
    for (int i = 2; i < 8; i++) {
        uint8_t u = r[i];
        bool dup = false;
        for (uint8_t j = 0; j < *nkeys; j++) {
            dup = dup || keys[j] == u;
        }
        // 0 is empty; 1..3 are the HID error sentinels, not keys.
        if (u > 3 && !dup) {
            keys[(*nkeys)++] = u;
        }
    }
    return true;
}

// usage 0x2D.. 0x38 and the specials, plain and shifted.
static int32_t plain_of(uint8_t u) {
    switch (u) {
        case 0x28: return '\r';
        case 0x29: return 0x1B;
        case 0x2A: return 0x08;
        case 0x2B: return '\t';
        case 0x2C: return ' ';
        case 0x2D: return '-';
        case 0x2E: return '=';
        case 0x2F: return '[';
        case 0x30: return ']';
        case 0x31: return '\\';
        case 0x32: return '#';
        case 0x33: return ';';
        case 0x34: return '\'';
        case 0x35: return '`';
        case 0x36: return ',';
        case 0x37: return '.';
        case 0x38: return '/';
        case 0x4C: return 0x7F;
        default: return 0;
    }
}

static int32_t shift_of(uint8_t u) {
    static const char DIGITS[] = "!@#$%^&*()";
    if (u >= 0x1E && u <= 0x27) {
        return DIGITS[u - 0x1E];
    }
    switch (u) {
        case 0x2D: return '_';
        case 0x2E: return '+';
        case 0x2F: return '{';
        case 0x30: return '}';
        case 0x31: return '|';
        case 0x32: return '~';
        case 0x33: return ':';
        case 0x34: return '"';
        case 0x35: return '~';
        case 0x36: return '<';
        case 0x37: return '>';
        case 0x38: return '?';
        default: return 0;
    }
}

int32_t moy_hid_keycode(uint8_t usage, uint8_t mods, bool caps) {
    bool shift = (mods & 0x22) != 0;
    bool ctrl = (mods & 0x11) != 0;
    if (usage >= 0x04 && usage <= 0x1D) {
        int32_t letter = usage - 0x04;
        if (ctrl) {
            return letter + 1;              // Ctrl+A..Z -> 0x01..0x1a
        }
        return (shift != caps ? 'A' : 'a') + letter;
    }
    if (usage >= 0x1E && usage <= 0x27) {
        return shift ? shift_of(usage) : "1234567890"[usage - 0x1E];
    }
    int32_t k = shift ? shift_of(usage) : 0;
    return k ? k : plain_of(usage);
}

enum {
    B_LEFT = 1u << 0, B_RIGHT = 1u << 1, B_UP = 1u << 2, B_DOWN = 1u << 3,
    B_A = 1u << 4, B_B = 1u << 5, B_RUN = 1u << 6, B_HOME = 1u << 7, B_STOP = 1u << 10,
};

uint32_t moy_hid_buttons_for_key(int32_t key) {
    if (key >= 'A' && key <= 'Z') {
        key += 32;
    }
    switch (key) {
        case 'w': return B_UP;
        case 's': return B_DOWN;
        case 'a': return B_LEFT;
        case 'd': return B_RIGHT;
        case 'z': return B_A;
        case ' ': return B_A;
        case 'x': return B_B;
        case 0x0D: return B_RUN;
        case 0x1B: return B_STOP;
        case 0x08: return B_HOME;
        default: return 0;
    }
}

static uint32_t direct_button(uint8_t usage) {
    switch (usage) {
        case 0x4F: return B_RIGHT;
        case 0x50: return B_LEFT;
        case 0x51: return B_DOWN;
        case 0x52: return B_UP;
        default: return 0;
    }
}

// -- the machine -----------------------------------------------------------------

static uint32_t now(moy_hid_t *h) {
    return h->ops->ms(h->ops->ctx);
}

static void set_state(moy_hid_t *h, uint8_t s) {
    h->state = s;
    h->state_at = now(h);
}

static void set_error(moy_hid_t *h, const char *why) {
    snprintf(h->error, sizeof(h->error), "%s", why ? why : "");
}

static void idle_retry(moy_hid_t *h) {
    h->state = MOY_HID_IDLE;
    h->retry_at = now(h) + MOY_HID_RETRY_MS;
}

static bool addr_eq(const moy_hid_addr_t *a, const moy_hid_addr_t *b) {
    return a->type == b->type && memcmp(a->a, b->a, 6) == 0;
}

static void copy_name(char *dst, const char *src) {
    snprintf(dst, MOY_HID_NAME, "%s", src && src[0] ? src : "BLE keyboard");
}

void moy_hid_init(moy_hid_t *h, const moy_hid_ops_t *ops, moy_input_t *table, uint32_t src) {
    memset(h, 0, sizeof(*h));
    MOY_DRV_LOCK_INIT(&h->lock);
    h->ops = ops;
    h->table = table;
    h->src = src;
    h->enabled = true;
    h->conn = -1;
    h->write_pending = -1;
    h->pending_scan = -1;
    h->want_player = -1;
    h->state = MOY_HID_OFF;
}

// The device list: the picked keyboard first, every newly seen peer after.
static int remember(moy_hid_t *h, const moy_hid_addr_t *addr, const char *name, int8_t rssi) {
    for (uint8_t i = 0; i < h->ndev; i++) {
        if (addr_eq(&h->devs[i].addr, addr)) {
            MOY_DRV_LOCK(&h->lock);
            if (name && name[0] && strcmp(name, "?") != 0) {
                copy_name(h->devs[i].name, name);
            }
            h->devs[i].rssi = rssi;
            MOY_DRV_UNLOCK(&h->lock);
            return i;
        }
    }
    if (h->ndev == MOY_HID_DEVICES) {
        return -1;
    }
    bool first = h->has_pref && addr_eq(addr, &h->pref);
    MOY_DRV_LOCK(&h->lock);
    int at = first ? 0 : h->ndev;
    if (first) {
        memmove(&h->devs[1], &h->devs[0], h->ndev * sizeof(h->devs[0]));
    }
    h->devs[at].addr = *addr;
    copy_name(h->devs[at].name, name);
    h->devs[at].rssi = rssi;
    h->ndev++;
    MOY_DRV_UNLOCK(&h->lock);
    return at;
}

static void clear_reports(moy_hid_t *h) {
    MOY_DRV_LOCK(&h->lock);
    h->nreports = 0;
    memset(h->pend_usage, 0, sizeof(h->pend_usage));
    h->pend_mods = 0;
    h->level_idle = false;
    MOY_DRV_UNLOCK(&h->lock);
    moy_input_release(h->table, h->src);
    moy_input_set_key(h->table, h->src, 0);
}

static void reset_connection(moy_hid_t *h) {
    h->conn = -1;
    h->has_cand = false;
    h->cand_name[0] = 0;
    h->has_hid = false;
    h->protocol_handle = 0;
    h->nchrs = 0;
    h->ndscs = 0;
    h->nsubs = 0;
    h->sub_next = 0;
    h->write_pending = -1;
    h->sub_retries = 0;
    h->encrypted = false;
    h->interval = 0;
    h->has_mouse = false;
    h->mbuttons = 0;
    h->mdx = 0;
    h->mdy = 0;
    clear_reports(h);
}

static bool start_scan(moy_hid_t *h, bool picker) {
    if (!h->available || !h->enabled || h->conn >= 0) {
        return false;
    }
    h->has_cand = false;
    h->cand_name[0] = 0;
    h->scan_picker = picker;
    if (picker) {
        // Only the saved identity stays while fresh results arrive.
        MOY_DRV_LOCK(&h->lock);
        h->ndev = 0;
        MOY_DRV_UNLOCK(&h->lock);
        if (h->has_pref) {
            remember(h, &h->pref, h->name, -127);
        }
    }
    set_error(h, NULL);
    set_state(h, MOY_HID_SCANNING);
    if (h->ops->scan(h->ops->ctx, picker) != 0) {
        set_error(h, "scan failed");
        idle_retry(h);
        return false;
    }
    return true;
}

static void connect_candidate(moy_hid_t *h) {
    if (!h->has_cand || !h->enabled) {
        idle_retry(h);
        return;
    }
    set_state(h, MOY_HID_CONNECTING);
    if (h->ops->connect(h->ops->ctx, &h->cand) != 0) {
        set_error(h, "connect failed");
        idle_retry(h);
    }
}

static bool begin_pending_connect(moy_hid_t *h) {
    if (!h->pending_connect || !h->enabled) {
        h->pending_connect = false;
        return false;
    }
    h->pending_connect = false;
    h->cand = h->pend_addr;
    h->has_cand = true;
    copy_name(h->cand_name, h->pend_name);
    connect_candidate(h);
    return true;
}

static void disconnect(moy_hid_t *h, const char *why) {
    if (why) {
        set_error(h, why);
    }
    if (h->conn >= 0 && h->ops->disconnect(h->ops->ctx, (uint16_t)h->conn) == 0) {
        return;
    }
    reset_connection(h);
    if (h->enabled) {
        idle_retry(h);
    } else {
        h->state = MOY_HID_DISABLED;
    }
}

static bool request_scan(moy_hid_t *h, bool picker) {
    if (!h->available || !h->enabled) {
        return false;
    }
    h->pending_connect = false;
    if (h->conn >= 0) {
        h->pending_scan = picker;
        if (h->ops->disconnect(h->ops->ctx, (uint16_t)h->conn) == 0) {
            return true;
        }
        reset_connection(h);
    }
    if (h->state == MOY_HID_SCANNING || h->state == MOY_HID_FOUND) {
        h->pending_scan = picker;
        if (h->ops->scan_stop(h->ops->ctx) == 0) {
            return true;
        }
        h->pending_scan = -1;
    }
    reset_connection(h);
    return start_scan(h, picker);
}

void moy_hid_started(moy_hid_t *h, bool ok, const char *why) {
    if (!ok) {
        set_error(h, why);
        h->state = MOY_HID_OFF;
        h->available = false;
        return;
    }
    h->available = true;
    if (h->enabled) {
        start_scan(h, false);
    } else {
        h->state = MOY_HID_DISABLED;
    }
}

void moy_hid_stopped(moy_hid_t *h) {
    reset_connection(h);
    h->available = false;
    h->state = MOY_HID_OFF;
}

void moy_hid_tick(moy_hid_t *h) {
    uint32_t t = now(h);
    if (h->dirty) {
        h->dirty = false;
        h->ops->save(h->ops->ctx, h);
    }
    if (h->available && h->enabled && !h->manual_hold && h->conn < 0
        && h->state == MOY_HID_IDLE && moy_input_ticks_diff(t, h->retry_at) >= 0) {
        start_scan(h, false);
    } else if (h->conn < 0 && h->state == MOY_HID_CONNECTING
               && moy_input_ticks_diff(t, h->state_at) > MOY_HID_CONNECT_MS) {
        idle_retry(h);
    } else if (h->conn >= 0
               && (h->state == MOY_HID_PAIRING || h->state == MOY_HID_DISCOVERING
                   || h->state == MOY_HID_SUBSCRIBE_RETRY)
               && moy_input_ticks_diff(t, h->state_at) > MOY_HID_DISCOVERY_MS) {
        disconnect(h, "discovery timeout");
    }
}

void moy_hid_set_enabled(moy_hid_t *h, bool on) {
    if (on == h->enabled) {
        return;
    }
    h->enabled = on;
    h->dirty = true;
    h->pending_scan = -1;
    h->pending_connect = false;
    h->manual_hold = false;
    if (!on) {
        clear_reports(h);
        if (h->state == MOY_HID_SCANNING || h->state == MOY_HID_FOUND) {
            h->ops->scan_stop(h->ops->ctx);
        }
        if (h->conn < 0 || h->ops->disconnect(h->ops->ctx, (uint16_t)h->conn) != 0) {
            reset_connection(h);
        }
        h->state = MOY_HID_DISABLED;
        return;
    }
    if (h->available) {
        h->state = MOY_HID_IDLE;
        h->retry_at = now(h);
        start_scan(h, false);
    }
}

void moy_hid_discover(moy_hid_t *h) {
    if (!h->enabled) {
        return;
    }
    h->manual_hold = true;
    request_scan(h, true);
}

bool moy_hid_pick(moy_hid_t *h, const moy_hid_addr_t *addr) {
    int at = -1;
    for (uint8_t i = 0; i < h->ndev; i++) {
        if (addr_eq(&h->devs[i].addr, addr)) {
            at = i;
        }
    }
    if (at < 0) {
        return false;
    }
    h->pref = *addr;
    h->has_pref = true;
    copy_name(h->name, h->devs[at].name);
    h->dirty = true;
    h->manual_hold = false;
    h->pending_scan = -1;
    h->pending_connect = true;
    h->pend_addr = *addr;
    copy_name(h->pend_name, h->name);
    if (h->conn >= 0) {
        if (h->has_cand && addr_eq(&h->cand, addr) && h->state == MOY_HID_READY) {
            h->pending_connect = false;
            return true;
        }
        if (h->ops->disconnect(h->ops->ctx, (uint16_t)h->conn) == 0) {
            return true;
        }
        reset_connection(h);
    }
    if ((h->state == MOY_HID_SCANNING || h->state == MOY_HID_FOUND)
        && h->ops->scan_stop(h->ops->ctx) == 0) {
        return true;
    }
    begin_pending_connect(h);
    return true;
}

void moy_hid_forget(moy_hid_t *h) {
    int32_t conn = h->conn;
    h->ops->forget_bonds(h->ops->ctx);
    h->has_pref = false;
    MOY_DRV_LOCK(&h->lock);
    h->ndev = 0;
    MOY_DRV_UNLOCK(&h->lock);
    h->name[0] = 0;
    h->dirty = true;
    h->manual_hold = true;
    h->pending_scan = -1;
    h->pending_connect = false;
    clear_reports(h);
    if (conn >= 0) {
        h->ops->disconnect(h->ops->ctx, (uint16_t)conn);
    }
    reset_connection(h);
    h->state = h->enabled ? MOY_HID_CHOOSE : MOY_HID_DISABLED;
}

bool moy_hid_scan(moy_hid_t *h) {
    if (!h->available || !h->enabled) {
        return false;
    }
    h->manual_hold = false;
    return request_scan(h, false);
}

// -- the stack's events ---------------------------------------------------------

void moy_hid_on_scan_result(moy_hid_t *h, const moy_hid_addr_t *addr, int8_t rssi,
                            const uint8_t *adv, size_t n) {
    if (h->state != MOY_HID_SCANNING) {
        return;
    }
    bool hid = moy_hid_adv_has_hid(adv, n);
    bool known = false;
    for (uint8_t i = 0; i < h->ndev; i++) {
        known = known || addr_eq(&h->devs[i].addr, addr);
    }
    bool preferred = h->has_pref && addr_eq(addr, &h->pref);
    // A scan response often carries only the name, after the advertisement
    // that established HIDS: remember or update that same peer.
    if (!hid && !known && !preferred) {
        return;
    }
    char name[MOY_HID_NAME];
    name[0] = 0;
    moy_hid_adv_name(adv, n, name, sizeof(name));
    int at = remember(h, addr, name, rssi);
    if (h->scan_picker || at < 0) {
        return;
    }
    if ((!h->has_pref && hid) || preferred) {
        h->cand = *addr;
        h->has_cand = true;
        copy_name(h->cand_name, h->devs[at].name);
        set_state(h, MOY_HID_FOUND);
        if (h->ops->scan_stop(h->ops->ctx) != 0) {
            connect_candidate(h);
        }
    }
}

void moy_hid_on_scan_done(moy_hid_t *h) {
    if (!h->enabled) {
        h->state = MOY_HID_DISABLED;
    } else if (h->pending_connect) {
        begin_pending_connect(h);
    } else if (h->pending_scan >= 0) {
        bool picker = h->pending_scan;
        h->pending_scan = -1;
        start_scan(h, picker);
    } else if (h->scan_picker) {
        h->state = MOY_HID_CHOOSE;
        h->manual_hold = true;
    } else if (h->state == MOY_HID_FOUND) {
        connect_candidate(h);
    } else if (h->state == MOY_HID_SCANNING) {
        idle_retry(h);
    }
}

void moy_hid_on_connect(moy_hid_t *h, uint16_t conn, const moy_hid_addr_t *addr) {
    if (!h->enabled) {
        h->ops->disconnect(h->ops->ctx, conn);
        return;
    }
    if (h->has_cand && !addr_eq(addr, &h->cand)) {
        return;
    }
    h->conn = conn;
    h->encrypted = false;
    copy_name(h->name, h->cand_name[0] ? h->cand_name : h->name);
    h->pref = *addr;
    h->has_pref = true;
    remember(h, addr, h->name, -127);
    h->dirty = true;
    set_state(h, MOY_HID_PAIRING);
    // Pairing and discovery proceed together: a cheap keyboard's Just Works
    // encryption completes the encrypted CCCD writes once SMP has the keys.
    h->ops->pair(h->ops->ctx, conn);
    h->has_hid = false;
    h->nchrs = 0;
    h->ndscs = 0;
    clear_reports(h);
    set_state(h, MOY_HID_DISCOVERING);
    if (h->ops->disc_svcs(h->ops->ctx, conn) != 0) {
        disconnect(h, "service discovery failed");
    }
}

void moy_hid_on_connect_failed(moy_hid_t *h) {
    if (h->conn < 0 && h->state == MOY_HID_CONNECTING) {
        idle_retry(h);
    }
}

void moy_hid_on_disconnect(moy_hid_t *h, uint16_t conn) {
    if (h->conn != conn) {
        return;
    }
    reset_connection(h);
    if (!h->enabled) {
        h->state = MOY_HID_DISABLED;
    } else if (h->pending_connect) {
        begin_pending_connect(h);
    } else if (h->pending_scan >= 0) {
        bool picker = h->pending_scan;
        h->pending_scan = -1;
        start_scan(h, picker);
    } else if (h->manual_hold) {
        h->state = MOY_HID_CHOOSE;
    } else {
        idle_retry(h);
    }
}

void moy_hid_on_svc(moy_hid_t *h, uint16_t conn, uint16_t start, uint16_t end, uint16_t uuid) {
    if (conn == h->conn && uuid == MOY_HID_UUID_SERVICE) {
        h->has_hid = true;
        h->hid_start = start;
        h->hid_end = end;
    }
}

void moy_hid_on_svc_done(moy_hid_t *h, uint16_t conn, int status) {
    if (conn != h->conn) {
        return;
    }
    if (status || !h->has_hid) {
        disconnect(h, "HID service not found");
    } else if (h->ops->disc_chrs(h->ops->ctx, conn, h->hid_start, h->hid_end) != 0) {
        disconnect(h, "characteristic discovery failed");
    }
}

void moy_hid_on_chr(moy_hid_t *h, uint16_t conn, uint16_t def, uint16_t val, uint8_t props,
                    uint16_t uuid) {
    if (conn == h->conn && h->nchrs < MOY_HID_CHRS) {
        h->chrs[h->nchrs++] = (moy_hid_chr_t){def, val, props, uuid};
    }
}

void moy_hid_on_chr_done(moy_hid_t *h, uint16_t conn, int status) {
    if (conn != h->conn) {
        return;
    }
    if (status) {
        disconnect(h, "characteristic discovery failed");
    } else if (h->ops->disc_dscs(h->ops->ctx, conn, h->hid_start, h->hid_end) != 0) {
        disconnect(h, "descriptor discovery failed");
    }
}

void moy_hid_on_dsc(moy_hid_t *h, uint16_t conn, uint16_t handle, uint16_t uuid) {
    if (conn == h->conn && h->ndscs < MOY_HID_DSCS) {
        h->dscs[h->ndscs][0] = handle;
        h->dscs[h->ndscs][1] = uuid;
        h->ndscs++;
    }
}

static bool set_boot_protocol(moy_hid_t *h) {
    static const uint8_t BOOT = 0;
    // Protocol Mode is Write Without Response in HIDS.
    return h->conn >= 0 && h->protocol_handle
           && h->ops->write(h->ops->ctx, (uint16_t)h->conn, h->protocol_handle, &BOOT, 1,
                            false) == 0;
}

static void write_next(moy_hid_t *h) {
    if (h->conn < 0) {
        return;
    }
    if (h->sub_next >= h->nsubs) {
        h->write_pending = -1;
        set_state(h, MOY_HID_READY);
        return;
    }
    uint16_t cccd = h->subs[h->sub_next++];
    static const uint8_t NOTIFY[2] = {1, 0};
    h->write_pending = cccd;
    if (h->ops->write(h->ops->ctx, (uint16_t)h->conn, cccd, NOTIFY, 2, true) != 0) {
        disconnect(h, "subscribe failed");
    }
}

void moy_hid_on_dsc_done(moy_hid_t *h, uint16_t conn, int status) {
    if (conn != h->conn) {
        return;
    }
    if (status) {
        disconnect(h, "descriptor discovery failed");
        return;
    }
    // Prefer Boot Host whenever the keyboard exposes it: Protocol Mode = 0 and
    // only the fixed boot report. Mixing Boot and Report characteristics leaves
    // the payload layout ambiguous. A Boot Mouse Input is subscribed beside it.
    bool boot = false;
    bool mouse = false;
    h->protocol_handle = 0;
    for (uint8_t i = 0; i < h->nchrs; i++) {
        moy_hid_chr_t *c = &h->chrs[i];
        if (c->uuid == MOY_HID_UUID_PROTOCOL) {
            h->protocol_handle = c->val;
        } else if (c->uuid == MOY_HID_UUID_BOOT_KBD && (c->props & MOY_HID_PROP_NOTIFY)) {
            boot = true;
        } else if (c->uuid == MOY_HID_UUID_BOOT_MOUSE && (c->props & MOY_HID_PROP_NOTIFY)) {
            mouse = true;
        }
    }
    uint16_t want = boot ? MOY_HID_UUID_BOOT_KBD : MOY_HID_UUID_REPORT;
    h->protocol = boot || mouse ? 1 : 2;
    if (h->protocol == 1) {
        set_boot_protocol(h);
        if (!boot) {
            want = 0;                   // a mouse alone: no keyboard report
        }
    }
    h->nsubs = 0;
    MOY_DRV_LOCK(&h->lock);
    h->nreports = 0;
    h->has_mouse = false;
    MOY_DRV_UNLOCK(&h->lock);
    for (uint8_t i = 0; i < h->nchrs && h->nsubs < MOY_HID_INPUTS; i++) {
        moy_hid_chr_t *c = &h->chrs[i];
        bool is_mouse = c->uuid == MOY_HID_UUID_BOOT_MOUSE;
        if ((c->uuid != want && !is_mouse) || !(c->props & MOY_HID_PROP_NOTIFY)) {
            continue;
        }
        uint32_t next_def = (uint32_t)h->hid_end + 1;
        for (uint8_t j = 0; j < h->nchrs; j++) {
            if (h->chrs[j].def > c->def && h->chrs[j].def < next_def) {
                next_def = h->chrs[j].def;
            }
        }
        for (uint8_t d = 0; d < h->ndscs; d++) {
            if (h->dscs[d][0] > c->val && h->dscs[d][0] < next_def
                && h->dscs[d][1] == MOY_HID_UUID_CCCD) {
                h->subs[h->nsubs++] = h->dscs[d][0];
                MOY_DRV_LOCK(&h->lock);
                moy_hid_report_t *r = &h->reports[h->nreports++];
                memset(r, 0, sizeof(*r));
                r->handle = c->val;
                r->mouse = is_mouse;
                h->has_mouse = h->has_mouse || is_mouse;
                MOY_DRV_UNLOCK(&h->lock);
                break;
            }
        }
    }
    h->sub_next = 0;
    h->write_pending = -1;
    h->sub_retries = 0;
    if (h->nsubs == 0) {
        disconnect(h, "no keyboard input report CCCD");
        return;
    }
    write_next(h);
}

static void retry_subscriptions(moy_hid_t *h) {
    if (h->conn < 0 || h->nsubs == 0) {
        return;
    }
    if (h->protocol == 1) {
        set_boot_protocol(h);      // the first may have preceded encryption
    }
    h->sub_next = 0;
    h->write_pending = -1;
    write_next(h);
}

void moy_hid_on_write_done(moy_hid_t *h, uint16_t conn, uint16_t handle, int status) {
    if (conn != h->conn || (int32_t)handle != h->write_pending) {
        return;
    }
    h->write_pending = -1;
    if (status == 0) {
        write_next(h);
        return;
    }
    // A security-gated CCCD commonly rejects the first write while SMP is still
    // establishing encryption: keep the link and retry the whole set once the
    // link is encrypted. A second failure is a real incompatibility.
    if (h->sub_retries < 1) {
        h->sub_retries++;
        set_state(h, MOY_HID_SUBSCRIBE_RETRY);
        if (h->encrypted) {
            retry_subscriptions(h);
        }
    } else {
        disconnect(h, "keyboard notification subscribe failed");
    }
}

void moy_hid_on_notify(moy_hid_t *h, uint16_t conn, uint16_t handle, const uint8_t *r, size_t n) {
    if (conn != h->conn) {
        return;
    }
    uint8_t mods, keys[6], nkeys;
    MOY_DRV_LOCK(&h->lock);
    moy_hid_report_t *rep = NULL;
    for (uint8_t i = 0; i < h->nreports; i++) {
        if (h->reports[i].handle == handle) {
            rep = &h->reports[i];
        }
    }
    if (rep != NULL && rep->mouse) {
        h->notify_count++;
        if (n >= 3) {
            h->mbuttons = r[0];
            h->mdx += (int8_t)r[1];
            h->mdy += (int8_t)r[2];
        }
    } else if (rep != NULL) {
        h->notify_count++;
        if (moy_hid_decode(r, n, &mods, keys, &nkeys)) {
            for (uint8_t i = 0; i < nkeys; i++) {
                bool held = false;
                for (uint8_t j = 0; j < rep->nkeys; j++) {
                    held = held || rep->keys[j] == keys[i];
                }
                if (!held) {
                    // A make that may break before the next frame: kept for one.
                    h->pend_usage[keys[i] >> 3] |= (uint8_t)(1u << (keys[i] & 7));
                    h->pend_mods |= mods;
                    if (keys[i] == 0x39) {      // Caps Lock's make
                        h->caps = !h->caps;
                    }
                }
            }
            rep->mods = mods;
            memcpy(rep->keys, keys, sizeof(keys));
            rep->nkeys = nkeys;
        }
    }
    MOY_DRV_UNLOCK(&h->lock);
}

void moy_hid_on_conn_update(moy_hid_t *h, uint16_t conn, uint16_t interval, int status) {
    if (conn == h->conn && status == 0) {
        h->interval = interval;
    }
}

void moy_hid_on_enc_change(moy_hid_t *h, uint16_t conn, bool encrypted, bool bonded) {
    if (conn != h->conn) {
        return;
    }
    h->encrypted = encrypted;
    if (bonded) {
        h->dirty = true;
    }
    if (encrypted && h->state == MOY_HID_SUBSCRIBE_RETRY) {
        retry_subscriptions(h);
    }
}

// -- the frame -------------------------------------------------------------------

// The player slot this keyboard drives, held only while it is connected: a
// cart must not field a character nobody can move. Nobody asking leaves the
// source's slot alone.
static void sync_player(moy_hid_t *h) {
    if (h->want_player < 0) {
        return;
    }
    uint8_t want = h->state == MOY_HID_READY ? (uint8_t)h->want_player : 0;
    uint8_t now_p;
    if (moy_input_source_player(h->table, h->src, &now_p) == MOY_INPUT_OK && now_p != want) {
        moy_input_set_player(h->table, h->src, want);
    }
}

bool moy_hid_take_mouse(moy_hid_t *h, int32_t *dx, int32_t *dy, uint8_t *buttons) {
    MOY_DRV_LOCK(&h->lock);
    bool has = h->has_mouse && h->state == MOY_HID_READY;
    *dx = h->mdx;
    *dy = h->mdy;
    *buttons = h->mbuttons;
    h->mdx = 0;
    h->mdy = 0;
    MOY_DRV_UNLOCK(&h->lock);
    return has;
}

void moy_hid_set_player(moy_hid_t *h, int8_t slot) {
    h->want_player = slot;
    sync_player(h);
}

void moy_hid_frame(moy_hid_t *h) {
    sync_player(h);
    uint8_t usage[32];
    uint8_t mods = 0;
    bool any = false;
    MOY_DRV_LOCK(&h->lock);
    bool pending = h->pend_mods != 0;
    for (int i = 0; i < 32 && !pending; i++) {
        pending = h->pend_usage[i] != 0;
    }
    bool held = false;
    for (uint8_t i = 0; i < h->nreports && !held; i++) {
        held = h->reports[i].nkeys || h->reports[i].mods;
    }
    if (h->level_idle && !pending && !held) {
        MOY_DRV_UNLOCK(&h->lock);
        return;
    }
    memcpy(usage, h->pend_usage, sizeof(usage));
    mods = h->pend_mods;
    for (uint8_t i = 0; i < h->nreports; i++) {
        mods |= h->reports[i].mods;
        for (uint8_t j = 0; j < h->reports[i].nkeys; j++) {
            uint8_t u = h->reports[i].keys[j];
            usage[u >> 3] |= (uint8_t)(1u << (u & 7));
        }
    }
    memset(h->pend_usage, 0, sizeof(h->pend_usage));
    h->pend_mods = 0;
    bool caps = h->caps;
    MOY_DRV_UNLOCK(&h->lock);

    bool text = moy_input_text_mode(h->table);
    uint32_t buttons = 0;
    int32_t key = 0;
    // Ascending usage: the first one that types a byte is the held key, like
    // the T-Deck's raw matrix -- editors edge-detect it, carts read its level.
    for (int u = 0; u < 256; u++) {
        if (!(usage[u >> 3] & (1u << (u & 7)))) {
            continue;
        }
        any = true;
        buttons |= direct_button((uint8_t)u);
        int32_t k = moy_hid_keycode((uint8_t)u, mods, caps);
        if (!text) {
            buttons |= moy_hid_buttons_for_key(k);
        }
        if (key == 0) {
            key = k;
        }
    }
    moy_input_set_mask(h->table, h->src, buttons & ((1u << moy_input_nbuttons(h->table)) - 1u));
    moy_input_set_key(h->table, h->src, key);
    h->level_idle = !any && !mods;
}

size_t moy_hid_sizeof(void) {
    return sizeof(moy_hid_t);
}

// -- reads for the host's binding (a board's binding reads the struct) --

uint8_t moy_hid_state(const moy_hid_t *h) {
    return h->state;
}

const char *moy_hid_text(const moy_hid_t *h, int which) {
    return which == 0 ? h->name : h->error;
}

int32_t moy_hid_int(const moy_hid_t *h, int which) {
    switch (which) {
        case 0: return h->enabled;
        case 1: return h->protocol;
        case 2: return h->conn;
        case 3: return (int32_t)h->notify_count;
        case 4: return h->ndev;
        case 5: return h->available;
        case 6: return h->caps;
        case 7: return h->interval;
        default: return -1;
    }
}

bool moy_hid_dev(const moy_hid_t *h, uint8_t i, moy_hid_addr_t *addr, char *name, int8_t *rssi) {
    if (i >= h->ndev) {
        return false;
    }
    *addr = h->devs[i].addr;
    memcpy(name, h->devs[i].name, MOY_HID_NAME);
    *rssi = h->devs[i].rssi;
    return true;
}

bool moy_hid_pref(const moy_hid_t *h, moy_hid_addr_t *addr) {
    if (h->has_pref) {
        *addr = h->pref;
    }
    return h->has_pref;
}

void moy_hid_load(moy_hid_t *h, bool enabled, const moy_hid_addr_t *pref, const char *name) {
    h->enabled = enabled;
    if (pref != NULL) {
        h->pref = *pref;
        h->has_pref = true;
        copy_name(h->name, name);
        h->devs[0].addr = *pref;
        copy_name(h->devs[0].name, name);
        h->devs[0].rssi = -127;
        h->ndev = 1;
    }
}
