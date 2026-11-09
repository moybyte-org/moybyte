// app: a native command's app calls (docs/app_api_v1.md's call table), each
// printed with its answer, or with its errno where it fails; a timer's id
// printed as 1 where it is one. `app pick` picks instead: a document it
// hears as `moy_open` on its event descriptor, read, written and read
// again through its handle, then a pick the user cancels (handle 0).
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
               : errno == ENOENT ? "ENOENT" : "other");
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

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "pick") == 0) {
        return pick();
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
