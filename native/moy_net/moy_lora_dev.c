// The `lora` device API by handle (native/moy_kernel/moy_dev.h's lora row):
// the radio's verbs for a compiled program, on its own task, over this
// module's driver (moy_lora.h), whose verbs take the lease table's lock
// before the radio's.

#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_lora.h"

typedef struct {
    moy_lora_cfg_t cfg;         // what the radio was last told
    uint8_t port;               // 0: a write is a raw packet, else an uplink on this port
    uint8_t confirmed, dr;
    int16_t rssi;               // of the packet read last
    int8_t snr;
    uint8_t rx_port;
} lora_t;

static lora_t s_lora[MOY_DEV_HANDLES];

// What a cfg names, every word checked before any is applied.
typedef struct {
    moy_lora_cfg_t cfg;
    bool radio;                 // a radio key named
    int listen;                 // -1: not named
    bool join;
    uint8_t keys;               // which of the join's keys were named (bits)
    uint8_t dev_eui[8], join_eui[8], app_key[16];
    int dr, send, confirmed;    // -1: not named
} words_out_t;

static int32_t lora_words(const lora_t *l, const char *cfg, size_t n, words_out_t *o) {
    memset(o, 0, sizeof *o);
    o->cfg = l->cfg;
    o->listen = o->dr = o->send = o->confirmed = -1;
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        int64_t v = 0;
        bool ok;
        if (moy_dev_is(w.k, w.kn, "freq")) {
            ok = moy_dev_in_range(&w, 0, UINT32_MAX, &v);
            o->cfg.freq = (uint32_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "bw")) {
            ok = moy_dev_in_range(&w, 0, UINT32_MAX, &v);
            o->cfg.bw = (uint32_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "sf")) {
            ok = moy_dev_in_range(&w, 0, UINT8_MAX, &v);
            o->cfg.sf = (uint8_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "cr")) {
            ok = moy_dev_in_range(&w, 0, UINT8_MAX, &v);
            o->cfg.cr = (uint8_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "sync")) {
            ok = moy_dev_in_range(&w, 0, UINT8_MAX, &v);
            o->cfg.sync = (uint8_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "power")) {
            ok = moy_dev_in_range(&w, INT8_MIN, INT8_MAX, &v);
            o->cfg.power = (int8_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "preamble")) {
            ok = moy_dev_in_range(&w, 0, UINT16_MAX, &v);
            o->cfg.preamble = (uint16_t)v;
            o->radio = true;
        } else if (moy_dev_is(w.k, w.kn, "listen")) {
            ok = moy_dev_in_range(&w, 0, 1, &v);
            o->listen = (int)v;
        } else if (moy_dev_is(w.k, w.kn, "join")) {
            ok = o->join = moy_dev_is(w.v, w.vn, "otaa");
        } else if (moy_dev_is(w.k, w.kn, "dev_eui")) {
            ok = moy_dev_hex(w.v, w.vn, o->dev_eui, 8);
            o->keys |= 1u;
        } else if (moy_dev_is(w.k, w.kn, "join_eui")) {
            ok = moy_dev_hex(w.v, w.vn, o->join_eui, 8);
            o->keys |= 2u;
        } else if (moy_dev_is(w.k, w.kn, "app_key")) {
            ok = moy_dev_hex(w.v, w.vn, o->app_key, 16);
            o->keys |= 4u;
        } else if (moy_dev_is(w.k, w.kn, "dr")) {
            ok = moy_dev_in_range(&w, 0, 5, &v);
            o->dr = (int)v;
        } else if (moy_dev_is(w.k, w.kn, "send")) {
            bool raw = moy_dev_is(w.v, w.vn, "raw");
            ok = raw || moy_dev_in_range(&w, 1, 223, &v);
            o->send = raw ? 0 : (int)v;
        } else if (moy_dev_is(w.k, w.kn, "confirmed")) {
            ok = moy_dev_in_range(&w, 0, 1, &v);
            o->confirmed = (int)v;
        } else {
            ok = false;
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    // The keys are a join's, and a join names all three.
    if (got < 0 || (o->keys != 0 && !o->join) || (o->join && o->keys != 7u)) {
        return MOY_DEV_EBAD;
    }
    return 0;
}

static int32_t lora_apply(uint32_t pid, lora_t *l, const words_out_t *o) {
    if (o->dr >= 0) {
        l->dr = (uint8_t)o->dr;
    }
    if (o->send >= 0) {
        l->port = (uint8_t)o->send;
    }
    if (o->confirmed >= 0) {
        l->confirmed = (uint8_t)o->confirmed;
    }
    int rc = 0;
    if (o->radio && (rc = moy_lora_config(pid, &o->cfg)) == 0) {
        l->cfg = o->cfg;
    }
    if (rc == 0 && o->listen >= 0) {
        rc = moy_lora_listen(pid, o->listen);
    }
    if (rc == 0 && o->join) {
        rc = moy_lora_join(pid, o->dev_eui, o->join_eui, o->app_key, l->dr);
    }
    return moy_dev_errno(rc);
}

static int32_t lora_open(uint32_t slot, uint32_t pid, const char *cfg, size_t cn) {
    lora_t *l = &s_lora[slot];
    *l = (lora_t){ .cfg = { .freq = 868100000u, .bw = 125000u, .sf = 9, .cr = 5, .power = 14,
                            .sync = 0x12, .preamble = 8 }, .dr = 5 };
    words_out_t o;
    int32_t r = lora_words(l, cfg, cn, &o);
    if (r < 0) {
        return r;
    }
    if (o.listen < 0) {
        o.listen = 1;                           // listening by default
    }
    if ((r = moy_dev_errno(moy_lora_open(pid))) < 0) {
        return r;
    }
    if ((r = lora_apply(pid, l, &o)) < 0) {
        moy_lora_close(pid);
    }
    return r;
}

static void lora_close(uint32_t slot, uint32_t pid) {
    (void)slot;
    moy_lora_close(pid);
}

static int32_t lora_set(uint32_t slot, uint32_t pid, const char *cfg, size_t n) {
    words_out_t o;
    int32_t r = lora_words(&s_lora[slot], cfg, n, &o);
    return r < 0 ? r : lora_apply(pid, &s_lora[slot], &o);
}

static int32_t lora_write(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n) {
    const lora_t *l = &s_lora[slot];
    int rc = l->port != 0 ? moy_lora_uplink(pid, l->port, data, n, l->confirmed, l->dr)
             : moy_lora_send(pid, data, n);
    return rc < 0 ? moy_dev_errno(rc) : (int32_t)n;
}

static int32_t lora_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    lora_t *l = &s_lora[slot];
    int16_t rssi = 0;
    int8_t snr = 0;
    uint8_t port = 0;
    int rc = moy_lora_recv(pid, out, cap, &rssi, &snr, &port);
    if (rc > 0) {
        l->rssi = rssi;
        l->snr = snr;
        l->rx_port = port;
    }
    return moy_dev_errno(rc);
}

static int32_t lora_get(uint32_t slot, uint32_t pid, const char *what, size_t n) {
    const lora_t *l = &s_lora[slot];
    bool mine = moy_lora_owner() == pid;
    uint32_t addr = 0, fcnt = 0;
    bool joined = mine && moy_lora_joined(&addr, &fcnt);
    if (moy_dev_is(what, n, "rssi")) {
        return l->rssi;
    }
    if (moy_dev_is(what, n, "snr")) {
        return l->snr;
    }
    if (moy_dev_is(what, n, "port")) {
        return l->rx_port;
    }
    if (moy_dev_is(what, n, "joined")) {
        return joined;
    }
    if (moy_dev_is(what, n, "fcnt")) {
        return joined ? (int32_t)fcnt : 0;
    }
    if (moy_dev_is(what, n, "busy")) {
        uint8_t m = moy_lora_mode();
        return mine && m != MOY_LORA_IDLE && m != MOY_LORA_LISTEN;
    }
    return MOY_DEV_EBAD;
}

const moy_dev_ops_t moy_dev_lora = {
    .open = lora_open, .close = lora_close, .read = lora_read, .write = lora_write,
    .set = lora_set, .get = lora_get,
};
