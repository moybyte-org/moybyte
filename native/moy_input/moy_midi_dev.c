// The `midi` device API by handle (native/moy_kernel/moy_dev.h's midi row):
// one port a handle for a compiled program, on its own task, over this
// module's driver (moy_midi.h), whose holder it shares with the face on the
// VM's task: a UART row's port (the row leased and its driver up through the
// kernel's buses, moy_dev_kinds.h), a USB-MIDI device's (the USB host's,
// native/p4/moy_usb, weak) and a BLE-MIDI peripheral's (the BLE driver's,
// moy_gatt.h, the port's machine the driver's own, moy_midi_ble).

#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_gatt.h"
#include "moy_devbus.h"
#include "moy_input.h"
#include "moy_lease.h"
#include "moy_midi.h"

// The USB host (native/p4/moy_usb/moy_usb.h), where the image has one.
#if __has_include("moy_usb.h")
#include "moy_usb.h"
#else
int moy_usb_open(uint32_t owner);
int moy_usb_close(uint32_t owner);
uint32_t moy_usb_owner(void);
int moy_usb_class_of(uint8_t addr);
int moy_usb_write(uint32_t owner, uint8_t addr, const uint8_t *d, size_t n);
#endif
#pragma weak moy_usb_open
#pragma weak moy_usb_close
#pragma weak moy_usb_owner
#pragma weak moy_usb_class_of
#pragma weak moy_usb_write
#define USB_CLASS_MIDI 3        // moy_usb.h's MOY_USB_MIDI

#pragma weak moy_input_kernel

#define REC 3u                  // a message read: status, data1, data2
#define WRITE_MAX 256u          // the bytes one write carries

typedef struct {
    int8_t port;                // the driver's port, -1: none
    uint8_t transport;          // MOY_MIDI_UART, _USB
    uint8_t unit;               // a UART's unit, a USB device's address
    uint8_t took_usb;           // this handle opened the USB host for its process
    uint8_t took_gatt;          // ... the BLE driver
    uint8_t pend_n;             // a message read off the ring that did not fit
    uint8_t pend[REC];
    int reader;
    uint32_t lease;             // a UART's
} midi_t;

static midi_t *s_midi;                  // MOY_DEV_ROWS, made at the first open

// "aa:bb:cc:dd:ee:ff" as the BLE driver holds an address (little-endian).
static bool ble_addr(const char *v, size_t n, uint8_t out[6]) {
    if (n != 17) {
        return false;
    }
    for (int i = 0; i < 6; i++) {
        if (i < 5 && v[3 * i + 2] != ':') {
            return false;
        }
        if (!moy_dev_hex(v + 3 * i, 2, &out[5 - i], 1)) {
            return false;
        }
    }
    return true;
}

static int32_t midi_cfg(const char *cfg, size_t cn, int *way, const char **row, size_t *rn,
                        int64_t *addr, int64_t *baud, uint8_t mac[6]) {
    words_t it = { cfg, cfg + cn };
    word_t w;
    int got, ways = 0;
    bool has_baud = false;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        bool ok = true;
        if (moy_dev_is(w.k, w.kn, "uart")) {
            *way = MOY_MIDI_UART;
            *row = w.v;
            *rn = w.vn;
            ok = w.vn > 4 && memcmp(w.v, "uart", 4) == 0;
            ways++;
        } else if (moy_dev_is(w.k, w.kn, "usb")) {
            *way = MOY_MIDI_USB;
            ok = moy_dev_in_range(&w, 1, 127, addr);
            ways++;
        } else if (moy_dev_is(w.k, w.kn, "ble")) {
            *way = MOY_MIDI_BLE;
            ok = ble_addr(w.v, w.vn, mac);
            ways++;
        } else if (moy_dev_is(w.k, w.kn, "baud")) {
            ok = moy_dev_in_range(&w, 1, 4000000, baud);
            has_baud = true;
        } else {
            ok = false;
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    if (got < 0 || ways != 1 || (has_baud && *way != MOY_MIDI_UART)) {
        return MOY_DEV_EBAD;            // one port a handle; baud is a UART's
    }
    return 0;
}

// The USB host for `pid`: 0, with *took where this call opened it.
static int32_t usb_take(uint32_t pid, uint8_t addr, bool *took) {
    *took = false;
    if (moy_usb_open == NULL) {
        return MOY_DEV_EABSENT;
    }
    if (moy_usb_owner() != pid) {
        int32_t r = moy_dev_errno(moy_usb_open(pid));
        if (r < 0) {
            return r;
        }
        *took = true;
    }
    if (moy_usb_class_of(addr) != USB_CLASS_MIDI) {
        if (*took) {
            moy_usb_close(pid);
        }
        return MOY_DEV_EABSENT;         // no USB-MIDI device there
    }
    return 0;
}

static void midi_drop(uint32_t slot, uint32_t pid) {
    midi_t *m = &s_midi[slot];
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    if (m->reader >= 0 && t != NULL) {
        moy_input_reader_free(t, m->reader);
    }
    if (m->port >= 0) {
        moy_midi_unport(pid, m->port);  // a UART's reader stops before its driver goes
    }
    if (m->transport == MOY_MIDI_UART && m->lease != 0) {
        moy_dev_uart_give(m->lease, m->unit);
    }
    if (m->took_gatt) {
        moy_gatt_close(pid);            // its link dropped with the driver
    }
    if (m->took_usb) {
        // Another of the process's USB ports here keeps the host it opened.
        bool kept = false;
        for (uint32_t i = 0; i < MOY_DEV_HANDLES && !kept; i++) {
            midi_t *o = &s_midi[i];
            if (i != slot && o->port >= 0 && o->transport == MOY_MIDI_USB
                && moy_midi_port_of(pid, o->port, &(int){ 0 }, &(uint32_t){ 0 }) == 0) {
                o->took_usb = 1;
                kept = true;
            }
        }
        if (!kept) {
            moy_usb_close(pid);
        }
    }
    if (moy_midi_ports(pid) == 0) {
        moy_midi_close(pid);            // its last port, the face's ones counted
    }
    *m = (midi_t){ .port = -1, .reader = -1 };
}

// The address type the BLE driver heard `mac` advertise with (0: public).
static uint8_t ble_type(const uint8_t mac[6]) {
    moy_gatt_seen_t s;
    for (uint32_t i = 0; moy_gatt_seen(i, &s) == 0; i++) {
        if (memcmp(s.addr, mac, 6) == 0) {
            return s.type;
        }
    }
    return 0;
}

static int32_t midi_open(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg,
                         size_t cn) {
    if (MOY_DEV_ROWS(s_midi) == NULL) {
        return MOY_DEV_ENOMEM;
    }
    midi_t *m = &s_midi[slot];
    *m = (midi_t){ .port = -1, .reader = -1 };
    int way = 0;
    const char *row = NULL;
    size_t rn = 0;
    int64_t addr = 0, baud = 31250;
    uint8_t mac[6];
    int32_t r = midi_cfg(cfg, cn, &way, &row, &rn, &addr, &baud, mac);
    if (r < 0) {
        return r;
    }
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    if (t == NULL) {
        return MOY_DEV_EABSENT;         // no input table: no ring to read
    }
    if ((r = moy_dev_errno(moy_midi_open(pid))) < 0) {
        return r;
    }
    m->transport = (uint8_t)way;
    if (way == MOY_MIDI_UART) {
        r = moy_dev_uart_take(pid, perms, row, rn, (uint32_t)baud, &m->lease, &m->unit);
        if (r < 0) {
            m->lease = 0;
        }
    } else if (way == MOY_MIDI_USB) {
        bool took;
        m->unit = (uint8_t)addr;
        r = usb_take(pid, m->unit, &took);
        m->took_usb = took;
    } else if (!moy_gatt_present()) {
        r = MOY_DEV_EABSENT;
    } else if (moy_gatt_owner() != pid) {
        r = moy_dev_errno(moy_gatt_open(pid));
        m->took_gatt = r == 0;
    }
    if (r == 0) {
        int port = moy_midi_port(pid, way, m->unit);
        if (port < 0) {
            r = moy_dev_errno(port);
        } else {
            m->port = (int8_t)port;
        }
    }
    // The reader before the port's first message can come.
    if (r == 0 && moy_input_reader_kept(t, MOY_EV_BIT(MOY_EV_MIDI), &m->reader) != 0) {
        m->reader = -1;
        r = MOY_DEV_EFULL;
    }
    if (r == 0) {
        moy_input_reader_only(t, m->reader, moy_midi_source());
        r = way == MOY_MIDI_UART ? moy_dev_errno(moy_midi_uart(pid, m->port))
            : way == MOY_MIDI_BLE ? moy_dev_errno(moy_midi_ble(pid, m->port, mac, ble_type(mac)))
            : 0;
    }
    if (r == 0) {
        return 0;
    }
    midi_drop(slot, pid);
    return r;
}

static void midi_close(uint32_t slot, uint32_t pid) {
    midi_drop(slot, pid);
}

// The port's messages, whole ones only: one that does not fit waits.
static int32_t midi_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    (void)pid;
    midi_t *m = &s_midi[slot];
    moy_input_t *t = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    size_t got = 0;
    for (;;) {
        if (m->pend_n == 0) {
            moy_event_t ev;
            if (t == NULL || m->reader < 0 || moy_input_events(t, m->reader, &ev, 1) == 0) {
                break;
            }
            if (ev.kind != MOY_EV_MIDI || ev.id != (uint16_t)m->port) {
                continue;
            }
            m->pend[0] = (uint8_t)(ev.a & 0xffu);
            m->pend[1] = (uint8_t)((ev.a >> 8) & 0x7fu);
            m->pend[2] = (uint8_t)(ev.b & 0x7fu);
            m->pend_n = REC;
        }
        if (cap - got < REC) {
            break;
        }
        memcpy(out + got, m->pend, REC);
        got += REC;
        m->pend_n = 0;
    }
    return got == 0 && m->pend_n != 0 ? MOY_DEV_EFULL : (int32_t)got;
}

static int32_t midi_write(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n) {
    midi_t *m = &s_midi[slot];
    if (n > WRITE_MAX) {
        return MOY_DEV_EBAD;
    }
    uint8_t pkt[4u * WRITE_MAX + 4u];
    int k = moy_midi_usb_pack(data, n, pkt, sizeof pkt);   // whole messages only
    if (k < 0) {
        return moy_dev_errno(k);
    }
    int32_t r = m->transport == MOY_MIDI_UART ? moy_devbus_uart_write(m->unit, data, n)
                : m->transport == MOY_MIDI_USB
                ? moy_dev_errno(moy_usb_write(pid, m->unit, pkt, (size_t)k))
                : moy_dev_errno(moy_midi_ble_send(pid, m->port, data, n,
                                                  moy_input_now_us() / 1000u));
    return r < 0 ? r : (int32_t)n;
}

static int32_t midi_get(uint32_t slot, uint32_t pid, const char *what, size_t n) {
    const midi_t *m = &s_midi[slot];
    if (moy_dev_is(what, n, "port")) {
        return m->port;
    }
    if (moy_dev_is(what, n, "state")) {
        if (m->transport != MOY_MIDI_BLE) {
            return 1;                   // a UART's and a USB device's flow at once
        }
        int st = moy_midi_ble_state(pid, m->port);
        return st == MOY_MIDI_BLE_READY ? 1 : st == MOY_MIDI_BLE_CONNECTING
               || st == MOY_MIDI_BLE_FINDING ? 0 : 2;
    }
    return MOY_DEV_EBAD;
}

static uint8_t midi_holds(uint32_t slot) {
    return s_midi[slot].took_gatt ? MOY_LEASE_BLE : 0u;
}

const moy_dev_ops_t moy_dev_midi = {
    .open = midi_open, .close = midi_close, .read = midi_read, .write = midi_write,
    .get = midi_get, .holds = midi_holds,
};
