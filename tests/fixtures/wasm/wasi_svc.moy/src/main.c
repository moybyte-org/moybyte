// svc: a WASI program in the service model (docs/os_2026-11/processes.md
// section 1). main is its loop: it blocks in poll on its event descriptor
// (WASI's poll_oneoff), with its next tick's deadline as the timeout, and
// answers each "ping" with a "pong" carrying the same arguments, sent to the
// app the ping came from; a "note" posts a notification and writes the OS
// log, printing the notification's answer and what moy_service_running
// answers (its own pid where it is its app's own service); a "wake" sets a timer that goes
// off once, one that repeats and an alarm already due, heard as the system's
// messages "moybyte.timer" and "moybyte.at" carrying the wake's id (the
// repeating one cancelled at its first tick), then writes and reads back a
// pref and the clipboard and opens an item no app opens, printing each
// answer and its errno; a "wake" carrying ["dev"] instead opens the
// console's leases by handle (a process listens on eight names): gpio6
// driven high and kept until the service ends, uart1 written and read back
// (its peer joined to it) and closed, and a handle it never had, each
// answer printed with its errno; a "wake" carrying ["ble"] opens the BLE
// face by handle with a scan, prints the scan's records (each event and an
// advertisement's name), connects to the first device heard and keeps the
// connection until the service ends; a "wake" carrying ["status"] puts a
// status item in the bar with a panel (a text row and a button; a toggle
// out of its range refused EINVAL), shows it in its error state and names a
// handle it never had, each answer printed with its errno, and keeps the
// item until the service ends, a press in its panel heard as the system's
// message "moybyte.status" [item, id, value]; a "wake" carrying ["timers"] asks for
// minute-long timers until the console answers FULL, printing how many it
// got and that answer's errno, then cancels all but the first, which it keeps
// until the service ends; a "wake" carrying ["front"] listens
// on "moy_open" (there, not at its start: beside its six names, a wake's
// "moybyte.timer" and "moybyte.at" fill a process's eight) and asks for the
// front three ways, printing each answer and its errno: the app "calc"
// opened (nav_open_app, under `launch` and the front's gate), a document
// shared and one picked; the pick reaches it as "moy_open" (handle, mime,
// mode, name), whose item it reads, writes and reads again through its
// handle, and a handle it was never given; a "wake" carrying ["media", path]
// opens the media service by handle, probes the file (its length and
// title), plays it, prints whether the stream is its own and the source it
// reads back, and keeps it playing until the service ends; a "bye" ends it, a "quit" returns
// 7 and a "spin" runs on without polling again, sleeping in slices. Its stop is the
// descriptor's end of file. The calls are the ones an ELF service makes
// (moybyte/os.h), here its moybyte.os imports. The terminal's test runs it
// as a command (tests/test_wasi_commands.py), the scheduler's as a service
// (tests/test_wasi_services.py).
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "moybyte/os.h"

#define TICK_MS 20

static long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int main(void) {
    if (moy_on("ping", 1) < 0 || moy_on("bye", 1) < 0 || moy_on("quit", 1) < 0
        || moy_on("spin", 1) < 0 || moy_on("note", 1) < 0 || moy_on("wake", 1) < 0) {
        printf("no queue: %d\n", errno);
        return 1;
    }
    printf("listening\n");
    fflush(stdout);
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    int ticks = 0, pings = 0, ticker = 0;
    long next = now_ms() + TICK_MS;
    for (;;) {
        long left = next - now_ms();
        struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };
        int n = poll(&p, 1, left > 0 ? (int)left : 0);
        if (n < 0) {
            return 1;
        }
        if (n == 0) {
            ticks++;
            next += TICK_MS;
            continue;
        }
        ssize_t got = read(MOY_EVQ_FD, &m, sizeof m);
        if (got == 0) {
            printf("stopped after %d pings, %s\n", pings, ticks >= 2 ? "ticking" : "still");
            return 0;
        }
        if (got < 0) {
            continue;
        }
        if (strcmp(m.ev.name, "ping") == 0) {
            pings++;
            printf("ping %u from %s: %d\n", m.ev.n, m.ev.app,
                   moy_send(m.ev.app, "pong", m.b + sizeof m.ev));
            fflush(stdout);
        } else if (strcmp(m.ev.name, "note") == 0) {
            int id = moy_notify("Service Wasi", m.b + sizeof m.ev);
            printf("note %d errno %d\n", id, id < 0 ? errno : 0);
            fflush(stdout);
            moy_log(MOY_LOG_INFO, "noted");
            int r = moy_service_running();
            printf("service_running %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0 && strcmp(m.b + sizeof m.ev, "[\"dev\"]") == 0) {
            int pin = (int)moy_dev_open("gpio6", "mode=out value=1");
            printf("dev_open.gpio6 %d errno %d\n", pin > 0 ? 1 : pin, pin < 0 ? errno : 0);
            int r = (int)moy_dev_get(pin, "value");
            printf("dev_get.value %d errno %d\n", r, r < 0 ? errno : 0);
            int u = (int)moy_dev_open("uart1", "baudrate=9600");
            printf("dev_open.uart1 %d errno %d\n", u > 0 ? 1 : u, u < 0 ? errno : 0);
            r = (int)moy_dev_write(u, "ping", 4);
            printf("dev_write %d errno %d\n", r, r < 0 ? errno : 0);
            char b[16];
            r = (int)moy_dev_read(u, b, sizeof b);
            printf("dev_read %.*s errno %d\n", r > 0 ? r : 0, b, r < 0 ? errno : 0);
            r = (int)moy_dev_close(u);
            printf("dev_close %d errno %d\n", r, r < 0 ? errno : 0);
            r = (int)moy_dev_get(9999, "value");
            printf("dev_get.none %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0 && strcmp(m.b + sizeof m.ev, "[\"ble\"]") == 0) {
            int h = (int)moy_dev_open("ble", "scan=500");
            printf("ble_open %d errno %d\n", h > 0 ? 1 : h, h < 0 ? errno : 0);
            unsigned char r[128];
            int n = (int)moy_dev_read(h, r, sizeof r);
            printf("ble_scan");
            for (int k = 0; k + 12 <= n; k += 12 + (r[k + 2] | r[k + 3] << 8)) {
                int len = r[k + 2] | r[k + 3] << 8;
                printf(" %d", r[k]);
                if (r[k] == 1 && len > 6) {
                    printf(":%.*s", len - 6, (const char *)r + k + 18);
                }
            }
            printf(" errno %d\n", n < 0 ? errno : 0);
            char cfg[32];
            snprintf(cfg, sizeof cfg, "connect=%02x:%02x:%02x:%02x:%02x:%02x", r[12], r[13],
                     r[14], r[15], r[16], r[17]);
            int c = (int)moy_dev_set(h, cfg);
            printf("ble_connect %d errno %d\n", c, c < 0 ? errno : 0);
            c = (int)moy_dev_get(h, "connected");
            printf("ble_connected %d errno %d\n", c, c < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0
                   && strcmp(m.b + sizeof m.ev, "[\"timers\"]") == 0) {
            static int held[64];
            int t = 0, r = 0;
            while (t < 64 && (r = (int)moy_after(60000)) > 0) {
                held[t++] = r;
            }
            printf("timers %d %d errno %d\n", t, r, r < 0 ? errno : 0);
            int undone = 0;
            for (int i = 1; i < t; i++) {
                undone += (int)moy_cancel(held[i]);
            }
            printf("uncap %d\n", undone);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0
                   && strcmp(m.b + sizeof m.ev, "[\"front\"]") == 0) {
            int r = moy_on("moy_open", 1);
            printf("on.moy_open %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_nav_open_app("calc");
            printf("nav.open_app %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_share("# shared\n", 9, "text/markdown");
            printf("share %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_pick("text/markdown", "rw");
            printf("pick %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "moy_open") == 0) {
            const char *args = m.b + sizeof m.ev;
            int h = atoi(args + 1);
            printf("moy_open %s\n", args);
            char b[64];
            int r = moy_item_read(h, b, sizeof b);
            printf("item_read %.*s errno %d\n", r > 0 ? r - 1 : 0, b, r < 0 ? errno : 0);
            r = moy_item_write(h, "# kept\n", 7);
            printf("item_write %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_item_read(h, b, sizeof b);
            printf("item_read.again %.*s errno %d\n", r > 0 ? r - 1 : 0, b, r < 0 ? errno : 0);
            r = moy_item_read(h + 1000, b, sizeof b);
            printf("item_read.none %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0
                   && strncmp(m.b + sizeof m.ev, "[\"media\",\"", 10) == 0) {
            char path[200];
            snprintf(path, sizeof path, "%s", m.b + sizeof m.ev + 10);
            char *end = strstr(path, "\"]");
            if (end != NULL) {
                *end = 0;
            }
            int h = (int)moy_dev_open("media", NULL);
            printf("media_open %d errno %d\n", h > 0 ? 1 : h, h < 0 ? errno : 0);
            unsigned char r[256];
            int n = (int)moy_dev_xfer(h, path, strlen(path), r, sizeof r);
            int title = 0;
            while (4 + title < n && r[4 + title] != '\n') {
                title++;
            }
            printf("media_probe %d:%.*s errno %d\n", n >= 4 ? r[0] : -1, title,
                   (const char *)r + 4, n < 0 ? errno : 0);
            n = (int)moy_dev_write(h, path, strlen(path));
            printf("media_play %d errno %d\n", n == (int)strlen(path) ? 1 : n, n < 0 ? errno : 0);
            n = (int)moy_dev_get(h, "mine");
            printf("media_mine %d errno %d\n", n, n < 0 ? errno : 0);
            n = (int)moy_dev_read(h, r, sizeof r);
            int src = 0;
            for (int k = 0, lines = 0; k < n && lines < 3; k++) {
                if (r[k] == '\n' && ++lines == 3) {
                    src = k + 1;
                }
            }
            printf("media_src %s errno %d\n",
                   n > src && strncmp((const char *)r + src, path, strlen(path)) == 0 ? "same"
                                                                                       : "other",
                   n < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0
                   && strcmp(m.b + sizeof m.ev, "[\"status\"]") == 0) {
            int h = (int)moy_status_show("pulse", MOY_STATUS_ON, 0);
            printf("status_show %d errno %d\n", h, h < 0 ? errno : 0);
            int r = (int)moy_status_panel(h, "text Uplink\tup\nbutton sync Sync now");
            printf("status_panel %d errno %d\n", r, r < 0 ? errno : 0);
            r = (int)moy_status_panel(h, "toggle t T 5");
            printf("status_bad %d errno %d\n", r, r < 0 ? errno : 0);
            r = (int)moy_status_state(h, MOY_STATUS_ERROR);
            printf("status_state %d errno %d\n", r, r < 0 ? errno : 0);
            r = (int)moy_status_state(h + 1, MOY_STATUS_ON);
            printf("status_none %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strcmp(m.ev.name, "wake") == 0) {
            printf("after %d\n", (int)moy_after(0));
            ticker = (int)moy_every(TICK_MS);
            printf("every %d\n", ticker);
            int id = (int)moy_at(1);
            printf("at %d errno %d\n", id, id < 0 ? errno : 0);
            char b[64];
            int r = moy_prefs_set("level", "42");
            printf("prefs.set %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_prefs_get("level", b, sizeof b);
            printf("prefs.get %.*s errno %d\n", r > 0 ? r : 0, b, r < 0 ? errno : 0);
            r = moy_clipboard_put_text("from wasi");
            printf("clipboard.put_text %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_clipboard_text(b, sizeof b);
            printf("clipboard.text %.*s errno %d\n", r > 0 ? r : 0, b, r < 0 ? errno : 0);
            r = moy_clipboard_text(b, 2);
            printf("clipboard.short %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_open_with("nothing/opens", 0);
            printf("open_with %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strncmp(m.ev.name, "moybyte.", 8) == 0) {
            printf("%s from %s %s\n", m.ev.name, m.ev.app, m.b + sizeof m.ev);
            if (strcmp(m.ev.name, "moybyte.timer") == 0 && atoi(m.b + sizeof m.ev + 1) == ticker) {
                printf("cancel %d\n", (int)moy_cancel(ticker));
            }
            fflush(stdout);
        } else if (strcmp(m.ev.name, "bye") == 0) {
            printf("bye after %d pings, %s\n", pings, ticks >= 2 ? "ticking" : "still");
            return 0;
        } else if (strcmp(m.ev.name, "quit") == 0) {
            return 7;
        } else if (strcmp(m.ev.name, "spin") == 0) {
            for (;;) {
                usleep(10000);
            }
        }
    }
}
