// app: a native command's app calls (docs/app_api_v1.md's call table), each
// printed with its answer, or with its errno where it fails.
#include <errno.h>
#include <stdio.h>
#include <string.h>

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

int main(void) {
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
    said("notify", moy_notify("Hello", "from C") >= 0 ? 1 : -1, NULL);
    said("nav.open_app", moy_nav_open_app("calc"), NULL);
    return 0;
}
