// The `usb` device API by handle (native/moy_kernel/moy_dev.h's usb row): the
// USB host's devices for a compiled program, on its own task, over this
// module's driver (moy_usb.h), whose holder it shares with the face on the
// VM's task. Its records are read through a kernel input reader on the
// driver's ring, each one's body taken from the driver as the record is
// made.

#include <stdlib.h>
#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_input.h"
#include "moy_usb.h"

#define HEAD 12u                // a record's head: event, n (u16), a, b (i32)
#define REC_MAX (HEAD + MOY_USB_RX)

typedef struct {
    int reader;
    uint8_t attach;             // devices plugged in at the open still to read as ATTACH
    uint8_t attach_at;
    int16_t target;             // where a write goes, -1: nowhere yet
    uint16_t pend_n;            // a record made that did not fit
    uint8_t *pend;              // REC_MAX bytes while the handle is open
} usb_t;

static usb_t s_usb[MOY_DEV_HANDLES];

static void put16(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *q, uint32_t v) {
    put16(q, v);
    put16(q + 2, v >> 16);
}

static size_t about(const moy_usb_dev_t *d, uint8_t *q) {
    size_t n = 0;
    while (n < MOY_USB_NAME && d->name[n] != '\0') {
        n++;
    }
    q[0] = d->cls;
    q[1] = d->kind;
    put16(q + 2, d->vid);
    put16(q + 4, d->pid);
    memcpy(q + 6, d->name, n);
    return 6u + n;
}

// Record (ev, a, b) with its body into the pending slot: false for none (a
// DATA whose bytes an earlier record carried).
static bool make(usb_t *u, uint32_t pid, uint16_t ev, uint32_t a, uint32_t b) {
    uint8_t *body = u->pend + HEAD;
    int n = 0;
    if (ev == MOY_USB_EV_ATTACH) {
        moy_usb_dev_t d;
        for (uint32_t i = 0; moy_usb_device(i, &d) == 0; i++) {
            if (d.addr == a) {
                n = (int)about(&d, body);
                break;
            }
        }
    } else if (ev == MOY_USB_EV_REPORT) {
        n = moy_usb_report(pid, (uint8_t)a, body, MOY_USB_REPORT);
    } else if (ev == MOY_USB_EV_DATA) {
        n = moy_usb_read(pid, (uint8_t)a, body, MOY_USB_RX);
        if (n == 0) {
            return false;
        }
    }
    if (n < 0) {
        n = 0;                          // gone before its record was read
    }
    put16(u->pend, ev);
    put16(u->pend + 2, (uint32_t)n);
    put32(u->pend + 4, a);
    put32(u->pend + 8, b);
    u->pend_n = (uint16_t)(HEAD + (uint32_t)n);
    return true;
}

static int32_t usb_set(uint32_t slot, uint32_t pid, const char *cfg, size_t n) {
    usb_t *u = &s_usb[slot];
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    int64_t serial = -1, baud = 115200, target = -1;
    bool has_baud = false;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        bool ok;
        if (moy_dev_is(w.k, w.kn, "serial")) {
            ok = moy_dev_in_range(&w, 1, 127, &serial);
        } else if (moy_dev_is(w.k, w.kn, "baud")) {
            ok = moy_dev_in_range(&w, 1, 4000000, &baud);
            has_baud = true;
        } else if (moy_dev_is(w.k, w.kn, "write")) {
            ok = moy_dev_in_range(&w, 1, 127, &target);
        } else {
            ok = false;
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    if (got < 0 || (has_baud && serial < 0)) {
        return MOY_DEV_EBAD;            // baud is serial's
    }
    if (target >= 0) {
        u->target = (int16_t)target;
    }
    if (serial >= 0) {
        int32_t r = moy_dev_errno(moy_usb_serial(pid, (uint8_t)serial, (uint32_t)baud));
        if (r < 0) {
            return r;
        }
    }
    return 0;
}

static void usb_close(uint32_t slot, uint32_t pid) {
    usb_t *u = &s_usb[slot];
    moy_input_t *t = moy_input_kernel();
    if (u->reader >= 0 && t != NULL) {
        moy_input_reader_free(t, u->reader);
    }
    free(u->pend);
    *u = (usb_t){ .reader = -1, .target = -1 };
    moy_usb_close(pid);
}

static int32_t usb_open(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg,
                        size_t cn) {
    (void)perms;
    usb_t *u = &s_usb[slot];
    *u = (usb_t){ .reader = -1, .target = -1 };
    moy_input_t *t = moy_input_kernel();
    if (t == NULL) {
        return MOY_DEV_EABSENT;         // no input table: no ring to read
    }
    int32_t r = moy_dev_errno(moy_usb_open(pid));
    if (r < 0) {
        return r;
    }
    u->pend = malloc(REC_MAX);
    if (u->pend == NULL) {
        r = MOY_DEV_ENOMEM;
    } else if (moy_input_reader_kept(t, MOY_EV_BIT(MOY_EV_USB), &u->reader) != 0) {
        u->reader = -1;
        r = MOY_DEV_EFULL;
    } else {
        moy_input_reader_only(t, u->reader, moy_usb_source());
        u->attach = (uint8_t)moy_usb_count();   // the open reads as their ATTACHes
        r = usb_set(slot, pid, cfg, cn);
    }
    if (r < 0) {
        usb_close(slot, pid);
    }
    return r;
}

static int32_t usb_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    usb_t *u = &s_usb[slot];
    moy_input_t *t = moy_input_kernel();
    size_t got = 0;
    for (;;) {
        if (u->pend_n == 0) {
            if (u->attach_at < u->attach) {
                moy_usb_dev_t d;
                if (moy_usb_device(u->attach_at++, &d) == 0) {
                    make(u, pid, MOY_USB_EV_ATTACH, d.addr, d.cls);
                }
                continue;
            }
            moy_event_t ev;
            if (t == NULL || u->reader < 0 || moy_input_events(t, u->reader, &ev, 1) == 0) {
                break;
            }
            if (ev.kind != MOY_EV_USB || !make(u, pid, ev.id, ev.a, ev.b)) {
                continue;
            }
        }
        if (cap - got < u->pend_n) {
            break;
        }
        memcpy(out + got, u->pend, u->pend_n);
        got += u->pend_n;
        u->pend_n = 0;
    }
    return got == 0 && u->pend_n != 0 ? MOY_DEV_EFULL : (int32_t)got;
}

static int32_t usb_write(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n) {
    const usb_t *u = &s_usb[slot];
    if (u->target < 0) {
        return MOY_DEV_EBAD;            // set write=<addr> first
    }
    int32_t r = moy_dev_errno(moy_usb_write(pid, (uint8_t)u->target, data, n));
    return r < 0 ? r : (int32_t)n;
}

const moy_dev_ops_t moy_dev_usb = {
    .open = usb_open, .close = usb_close, .read = usb_read, .write = usb_write,
    .set = usb_set,
};
