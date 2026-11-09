// app: a native command's app calls (docs/app_api_v1.md's call table), each
// printed with its answer, or with its errno where it fails; a timer's id
// printed as 1 where it is one. `app pick` picks instead: a document it
// hears as `moy_open` on its event descriptor, read, written and read
// again through its handle, then a pick the user cancels (handle 0).
// `app lease` opens the console's leases by handle (a handle printed as 1):
// a pin driven and read, a UART written and read back through its peer, an
// SPI transfer and one too long for its buffer, a UART it already holds, a
// row the board does not offer and a handle it never had; it ends holding
// the pin, for its end to sweep. `app ble` drives the BLE face by handle:
// a scan (each record's event and the bytes it carried, an advertisement's
// name after its address), a connection to the first device heard, a
// characteristic found and read, a write to it, a second handle refused and
// two actions in one set refused; it ends connected, for its end to drop.
// `app timer` hears a timer that goes off once, one that repeats (cancelled
// at its first tick) and an alarm already due on its event descriptor, each
// as the system's message carrying its id, then asks for minute-long timers
// until the console answers FULL and cancels all but the first, which it
// keeps for its end to sweep.
// `app status` puts a status item in the bar with a panel (a description
// out of its range refused EINVAL, a handle it never had ENOENT) and waits
// to hear a press in it, the system's message "moybyte.status" carrying the
// item first; it ends holding the item, for its end to sweep.
// `app usb` drives the USB host by handle: the devices plugged in read as
// ATTACH records (each event, address and value, a device's name), a serial
// device's line opened and written, the write's SENT record read back, a
// keyboard's line refused and a second handle refused; it ends holding the
// host, for its end to let go.
// `app net` opens port 7 of the radio link by handle and says "elf" on it,
// reads the port until a record comes (two seconds at most) and prints it
// (the sender's last byte, the port and the bytes), a second handle on the
// port refused; it ends holding the port, for its end to close.
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "moybyte/os.h"

static void said(const char *what, int32_t r, const char *buf) {
    if (r < 0) {
        printf("%s -1 %s\n", what, errno == EACCES ? "EACCES" : errno == ERANGE ? "ERANGE"
               : errno == ENOENT ? "ENOENT" : errno == EBUSY ? "EBUSY"
               : errno == EINVAL ? "EINVAL" : "other");
    } else if (buf != NULL) {
        printf("%s %.*s\n", what, (int)r, buf);
    } else {
        printf("%s %d\n", what, (int)r);
    }
}

// The next `moy_open` the process hears: its handle, its arguments in
// `args`; -1 where none came within ten seconds.
static int32_t opened(char *args, size_t cap) {
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    for (int waits = 0; waits < 100; waits++) {
        struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };
        if (poll(&p, 1, 100) <= 0) {
            continue;
        }
        ssize_t got = read(MOY_EVQ_FD, &m, sizeof m);
        if (got <= 0 || strcmp(m.ev.name, "moy_open") != 0) {
            continue;
        }
        snprintf(args, cap, "%.*s", (int)m.ev.n, m.b + sizeof m.ev);
        return (int32_t)strtol(args + 1, NULL, 10);
    }
    return -1;
}

static int pick(void) {
    char buf[256], args[256];
    said("on", moy_on("moy_open", 1), NULL);
    said("pick", moy_pick("text/markdown", "rw"), NULL);
    int32_t h = opened(args, sizeof args);
    printf("open %s\n", h > 0 ? strchr(args, ',') + 2 : "none");
    said("item_read", moy_item_read(h, buf, sizeof buf), buf);
    said("item_read.short", moy_item_read(h, buf, 2), NULL);
    said("item_write", moy_item_write(h, "# kept\n", 7), NULL);
    said("item_read.again", moy_item_read(h, buf, sizeof buf), buf);
    said("pick.again", moy_pick("text/markdown", "r"), NULL);
    printf("cancelled %s\n", opened(args, sizeof args) == 0 ? args : "no");
    said("item_read.none", moy_item_read(0, buf, sizeof buf), NULL);
    return 0;
}

// 1 where the process hears message `name` carrying `id` within ten
// seconds, else 0.
static int heard(const char *name, int32_t id) {
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 64];
    } m;
    for (int waits = 0; waits < 100; waits++) {
        struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };
        if (poll(&p, 1, 100) <= 0) {
            continue;
        }
        ssize_t got = read(MOY_EVQ_FD, &m, sizeof m);
        if (got > 0 && strcmp(m.ev.name, name) == 0
            && strtol(m.b + sizeof m.ev + 1, NULL, 10) == id) {
            return 1;
        }
    }
    return 0;
}

static int timer(void) {
    int32_t once = moy_after(10);
    said("after", once > 0 ? 1 : once, NULL);
    said("heard.after", heard("moybyte.timer", once), NULL);
    int32_t every = moy_every(10);
    said("every", every > 0 ? 1 : every, NULL);
    said("heard.every", heard("moybyte.timer", every), NULL);
    said("cancel.every", moy_cancel(every), NULL);
    int32_t at = moy_at(1);
    said("at", at > 0 ? 1 : at, NULL);
    said("heard.at", heard("moybyte.at", at), NULL);
    static int32_t held[64];
    int32_t t = 0, r = 0;
    while (t < 64 && (r = moy_after(60000)) > 0) {
        held[t++] = r;
    }
    printf("timers %d\n", (int)t);
    said("timers.full", r, NULL);
    int32_t undone = 0;
    for (int32_t i = 1; i < t; i++) {
        undone += moy_cancel(held[i]);
    }
    said("uncap", undone, NULL);
    return 0;
}

static int status(void) {
    int32_t h = moy_status_show("star", MOY_STATUS_ON, 0);
    said("status_show", h > 0 ? 1 : h, NULL);
    said("status_panel", moy_status_panel(h, "text Calls\nbutton go Go\ntoggle twice Twice 1"),
         NULL);
    said("status_bad", moy_status_panel(h, "slider v V 9 1 5"), NULL);
    said("status_state", moy_status_state(h, MOY_STATUS_PAUSED), NULL);
    said("status_none", moy_status_remove(99), NULL);
    said("heard.status", heard("moybyte.status", h), NULL);
    return 0;
}

static int lease(void) {
    char buf[16];
    int32_t pin = moy_dev_open("gpio6", "mode=out");
    said("dev_open.gpio6", pin > 0 ? 1 : pin, NULL);
    said("dev_set.value", moy_dev_set(pin, "value=1"), NULL);
    said("dev_get.value", moy_dev_get(pin, "value"), NULL);
    int32_t u = moy_dev_open("uart1", "baudrate=9600");
    said("dev_open.uart1", u > 0 ? 1 : u, NULL);
    said("dev_write", moy_dev_write(u, "hello", 5), NULL);
    said("dev_read", moy_dev_read(u, buf, sizeof buf), buf);
    said("dev_open.uart1.again", moy_dev_open("uart1", NULL), NULL);
    said("dev_close", moy_dev_close(u), NULL);
    int32_t s = moy_dev_open("spi2", "baudrate=1000000");
    said("dev_open.spi2", s > 0 ? 1 : s, NULL);
    said("dev_xfer", moy_dev_xfer(s, "\x9f\x00", 2, buf, sizeof buf), NULL);
    said("dev_xfer.short", moy_dev_xfer(s, "\x9f\x00", 2, buf, 1), NULL);
    said("dev_open.gpio7", moy_dev_open("gpio7", NULL), NULL);
    said("dev_get.none", moy_dev_get(9999, "value"), NULL);
    return 0;
}

// Each record a ble handle's read holds: the event, a, b and the bytes.
static void records(const char *what, const uint8_t *r, int32_t n) {
    printf("%s", what);
    for (int32_t k = 0; k + 12 <= n;) {
        uint16_t ev = (uint16_t)(r[k] | r[k + 1] << 8), len = (uint16_t)(r[k + 2] | r[k + 3] << 8);
        int32_t a, b;
        memcpy(&a, r + k + 4, 4);
        memcpy(&b, r + k + 8, 4);
        printf(" %u:%d:%d", ev, (int)a, (int)b);
        if (ev == 1 && len > 6) {
            printf(":%.*s", (int)len - 6, (const char *)r + k + 18);
        } else if (len > 0) {
            printf(":%02x", r[k + 12]);
        }
        k += 12 + len;
    }
    printf("\n");
}

static int ble(void) {
    uint8_t buf[128];
    int32_t h = moy_dev_open("ble", "scan=500");
    said("dev_open.ble", h > 0 ? 1 : h, NULL);
    int32_t n = moy_dev_read(h, buf, sizeof buf);
    records("scan", buf, n);
    char cfg[32];
    snprintf(cfg, sizeof cfg, "connect=%02x:%02x:%02x:%02x:%02x:%02x", buf[12], buf[13],
             buf[14], buf[15], buf[16], buf[17]);
    said("dev_set.connect", moy_dev_set(h, cfg), NULL);
    said("dev_set.find", moy_dev_set(h, "find=2a19"), NULL);
    n = moy_dev_read(h, buf, sizeof buf);
    records("found", buf, n);
    said("dev_set.read", moy_dev_set(h, "read=0x20"), NULL);
    said("dev_read.short", moy_dev_read(h, buf, 8), NULL);
    n = moy_dev_read(h, buf, sizeof buf);
    records("read", buf, n);
    said("dev_set.write", moy_dev_set(h, "write=0x20"), NULL);
    said("dev_write", moy_dev_write(h, "\x07", 1), NULL);
    said("dev_open.again", moy_dev_open("ble", NULL), NULL);
    said("dev_set.two", moy_dev_set(h, "scan=1 read=0x20"), NULL);
    said("dev_get.connected", moy_dev_get(h, "connected"), NULL);
    return 0;
}

// Each record a usb handle's read holds: the event, the address and the
// value, then an ATTACH's name or the bytes of any other.
static void usb_records(const char *what, const uint8_t *r, int32_t n) {
    printf("%s", what);
    for (int32_t k = 0; k + 12 <= n;) {
        uint16_t ev = (uint16_t)(r[k] | r[k + 1] << 8), len = (uint16_t)(r[k + 2] | r[k + 3] << 8);
        int32_t a, b;
        memcpy(&a, r + k + 4, 4);
        memcpy(&b, r + k + 8, 4);
        printf(" %u:%d:%d", ev, (int)a, (int)b);
        if (ev == 1 && len > 6) {
            printf(":%.*s", (int)len - 6, (const char *)r + k + 18);
        } else if (len > 0) {
            printf(":%.*s", (int)len, (const char *)r + k + 12);
        }
        k += 12 + len;
    }
    printf("\n");
}

static int usb(void) {
    uint8_t buf[256];
    int32_t h = moy_dev_open("usb", NULL);
    said("dev_open.usb", h > 0 ? 1 : h, NULL);
    usb_records("plugged", buf, moy_dev_read(h, buf, sizeof buf));
    said("dev_set.serial", moy_dev_set(h, "serial=2 baud=9600 write=2"), NULL);
    said("dev_write", moy_dev_write(h, "ping", 4), NULL);
    usb_records("sent", buf, moy_dev_read(h, buf, sizeof buf));
    said("dev_set.keyboard", moy_dev_set(h, "serial=1"), NULL);
    said("dev_open.again", moy_dev_open("usb", NULL), NULL);
    return 0;
}

static int net(void) {
    uint8_t r[256];
    int32_t h = moy_dev_open("net", "port=7");
    said("dev_open.net", h > 0 ? 1 : h, NULL);
    said("dev_write", moy_dev_write(h, "elf", 3), NULL);
    int32_t n = 0;
    for (int i = 0; i < 200 && (n = moy_dev_read(h, r, sizeof r)) == 0; i++) {
        usleep(10000);
    }
    if (n >= 10) {
        printf("heard %d %d %.*s\n", r[5], r[6] | r[7] << 8, r[8] | r[9] << 8,
               (const char *)r + 10);
    } else {
        said("dev_read", n, NULL);
    }
    said("dev_open.again", moy_dev_open("net", "port=7"), NULL);
    return 0;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "pick") == 0) {
        return pick();
    }
    if (argc > 1 && strcmp(argv[1], "status") == 0) {
        return status();
    }
    if (argc > 1 && strcmp(argv[1], "lease") == 0) {
        return lease();
    }
    if (argc > 1 && strcmp(argv[1], "ble") == 0) {
        return ble();
    }
    if (argc > 1 && strcmp(argv[1], "timer") == 0) {
        return timer();
    }
    if (argc > 1 && strcmp(argv[1], "usb") == 0) {
        return usb();
    }
    if (argc > 1 && strcmp(argv[1], "net") == 0) {
        return net();
    }
    char buf[256];
    int32_t r = moy_prefs_set("level", "42");
    said("prefs.set", r, NULL);
    said("prefs.get", moy_prefs_get("level", buf, sizeof buf), buf);
    said("clipboard.put_text", moy_clipboard_put_text("from C"), NULL);
    said("clipboard.text", moy_clipboard_text(buf, sizeof buf), buf);
    said("clipboard.short", moy_clipboard_text(buf, 2), NULL);
    char saved[64];
    r = moy_files_save("docs", "note", "# hi\n", 5, saved, sizeof saved);
    said("files.save", r, saved);
    said("files.load", moy_files_load("docs", "note", buf, sizeof buf), buf);
    r = moy_files_list("docs", buf, sizeof buf);
    for (int32_t i = 0; i < r; i++) {
        if (buf[i] == 0) {
            buf[i] = ',';
        }
    }
    said("files.list", r, buf);
    said("files.load.other", moy_files_load("drawings", "x", buf, sizeof buf), NULL);
    char copy[64];
    r = moy_files_duplicate("docs", "note", copy, sizeof copy);
    said("files.duplicate", r, copy);
    said("files.rename", moy_files_rename("docs", copy, "Kept", buf, sizeof buf), buf);
    said("files.new_name", moy_files_new_name("docs", "note", buf, sizeof buf), buf);
    said("files.new_name.untitled", moy_files_new_name("docs", NULL, buf, sizeof buf), buf);
    said("notify", moy_notify("Hello", "from C") >= 0 ? 1 : -1, NULL);
    said("nav.open_app", moy_nav_open_app("calc"), NULL);
    said("award", moy_award("first"), NULL);
    said("progress", moy_progress("far", 3), NULL);
    int32_t once = moy_after(60000), every = moy_every(60000);
    said("after", once > 0 ? 1 : once, NULL);
    said("every", every > 0 ? 1 : every, NULL);
    said("cancel", moy_cancel(once), NULL);
    said("cancel.again", moy_cancel(once), NULL);
    said("cancel.every", moy_cancel(every), NULL);
    said("at", moy_at(1), NULL);
    return 0;
}
