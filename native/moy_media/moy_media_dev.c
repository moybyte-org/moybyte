// The `media` device API by handle (native/moy_kernel/moy_dev.h's media row):
// a compiled program a client of the media service (moy_media.h), on its own
// task, the VM up or stopped. Every call runs under the lease table's lock
// (moy_dev.c), which the service's open and its run's end take too
// (moy_media.h's WHO RUNS WHAT).
//
// THE FOLDERS. A file the program names plays only from its cart's folder
// and the folder its `files:<folder>` permission names (its run grant's,
// moy_app_dev_places: the grant's cart folder, and the kind's folder under
// the files root beside the carts folder, moy_uf_path), each path folded
// first, so no ".." leaves them; a relative one is its cart folder's. A
// playlist naming a file outside them is refused once read, before it plays.

#include "moy_platform.h"

#include <stdio.h>
#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_lease.h"
#include "moy_log.h"
#include "moy_media.h"
#include "moy_sync.h"
#include "moy_ufiles.h"

// The grant's places (native/moy_app) and the user-files layer's paths
// (native/moy_store's moy_ufiles); weak, for an image without either: a file
// is then outside every folder.
#if __has_include("moy_app.h")
#include "moy_app.h"
#else
bool moy_app_dev_places(uint32_t pid, char *folder, size_t fcap, char *root, size_t rcap, int *kind);
const char *moy_app_kind_name(int kind);
#endif
#pragma weak moy_app_dev_places
#pragma weak moy_app_kind_name
#pragma weak moy_uf_path
#if __has_include("moy_fs.h")
#include "moy_fs.h"
#else
void moy_buf_free(moy_buf_t *b);
#endif
#pragma weak moy_buf_free

#define PATH_MAX_ MOY_MEDIA_SRC_MAX
#define TAG "media"
#define TAG_N 5u

typedef struct {
    uint32_t pid;               // 0: free
    uint32_t perms;             // the open's device permissions
} media_dev_t;

static media_dev_t *s_rows;             // MOY_DEV_ROWS, made at the first open

// The paths a call works with: one block in PSRAM, made with the rows, which
// every call shares (each holds the lease table's lock), so a program's task
// stack carries none of them.
typedef struct {
    char src[PATH_MAX_], cart[PATH_MAX_], files[PATH_MAX_], path[PATH_MAX_];
    char e[PATH_MAX_], folder[PATH_MAX_], root[PATH_MAX_], joined[PATH_MAX_];
} scratch_t;

static scratch_t *s_x;

static const char *const NOW[] = { "paused", "entry", "entries", "rate", "channels", "kbps",
                                   "volume", "err", "at", "len" };

static bool is_https(const char *s) {
    static const char H[] = "https://";
    for (int i = 0; i < 8; i++) {
        char c = s[i];
        if ((c >= 'A' && c <= 'Z' ? (char)(c + 32) : c) != H[i]) {
            return false;
        }
    }
    return true;
}

static bool any_url(const char *s) {
    static const char H[] = "http://";
    for (int i = 0; i < 7; i++) {
        char c = s[i];
        if ((c >= 'A' && c <= 'Z' ? (char)(c + 32) : c) != H[i]) {
            return is_https(s);
        }
    }
    return true;
}

// `in` (n bytes) with its "." and ".." folded into `out`: "/a/b". False for
// a ".." past its root, a NUL in it, or no room.
static bool fold(const char *in, size_t n, char *out, size_t cap) {
    size_t o = 0;
    size_t i = 0;
    while (i < n) {
        while (i < n && (in[i] == '/' || in[i] == '\\')) {
            i++;
        }
        size_t s = i;
        while (i < n && in[i] != '/' && in[i] != '\\') {
            if (in[i] == 0) {
                return false;
            }
            i++;
        }
        size_t k = i - s;
        if (k == 0 || (k == 1 && in[s] == '.')) {
            continue;
        }
        if (k == 2 && in[s] == '.' && in[s + 1] == '.') {
            if (o == 0) {
                return false;
            }
            while (o > 0 && out[o - 1] != '/') {
                o--;
            }
            o = o > 0 ? o - 1 : 0;
            continue;
        }
        if (o + 1 + k >= cap) {
            return false;
        }
        out[o++] = '/';
        memcpy(out + o, in + s, k);
        o += k;
    }
    if (o == 0) {
        if (cap < 2) {
            return false;
        }
        out[o++] = '/';
    }
    out[o] = 0;
    return true;
}

// The folders process `pid` plays files from, folded: its cart's and its
// files permission's, each "" for none.
static void roots(uint32_t pid, char *cart, char *files) {
    char *folder = s_x->folder, *root = s_x->root;
    int kind = -1;
    cart[0] = files[0] = 0;
    if (moy_app_dev_places == NULL
        || !moy_app_dev_places(pid, folder, PATH_MAX_, root, PATH_MAX_, &kind)) {
        return;
    }
    if (folder[0] == '/' && !fold(folder, strlen(folder), cart, PATH_MAX_)) {
        cart[0] = 0;
    }
    if (kind < 0 || root[0] != '/' || moy_app_kind_name == NULL || moy_uf_path == NULL) {
        return;
    }
    moy_buf_t b = { NULL, 0 };
    if (moy_uf_path(MOY_UF_PATH_KIND, root, moy_app_kind_name(kind), NULL, &b) == 0
        && b.p != NULL && !fold(b.p, strlen(b.p), files, PATH_MAX_)) {
        files[0] = 0;
    }
    if (b.p != NULL && moy_buf_free != NULL) {
        moy_buf_free(&b);
    }
}

static bool under(const char *root, const char *p) {
    size_t n = strlen(root);
    if (n == 0) {
        return false;
    }
    if (root[n - 1] == '/') {
        n--;
    }
    return strncmp(p, root, n) == 0 && p[n] == '/';
}

// `name` (n bytes) as the path the service plays, folded, into `out`, a
// relative one under the cart folder `cart`: false for none (no cart folder
// for a relative one, a ".." past its root).
static bool resolve(const char *cart, const char *name, size_t n, char *out) {
    char *joined = s_x->joined;
    if (n > 0 && name[0] == '/') {
        return fold(name, n, out, PATH_MAX_);
    }
    size_t cn = strlen(cart);
    if (cn == 0 || cn + 1 + n >= PATH_MAX_) {
        return false;
    }
    memcpy(joined, cart, cn);
    joined[cn] = '/';
    memcpy(joined + cn + 1, name, n);
    return fold(joined, cn + 1 + n, out, PATH_MAX_);
}

// Whether `path` (folded) is inside one of process `pid`'s folders.
static bool within(const char *cart, const char *files, const char *path) {
    return under(cart, path) || under(files, path);
}

static int32_t media_open(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg,
                          size_t cn) {
    words_t it = { cfg, cfg + cn };
    word_t w;
    int got = moy_dev_word(&it, &w);
    if (got != 0) {
        return MOY_DEV_EBAD;                    // it takes no cfg
    }
    if (MOY_DEV_ROWS(s_rows) == NULL || MOY_SYNC_TABLE(s_x) == NULL) {
        return MOY_DEV_ENOMEM;
    }
    s_rows[slot] = (media_dev_t){ .pid = pid, .perms = perms };
    return 0;
}

static void media_close(uint32_t slot, uint32_t pid) {
    (void)pid;
    s_rows[slot] = (media_dev_t){ 0 };          // the stream is the service's: it plays on
}

// The radio's "media" row under the run's pid while a URL of its list may
// play (moy_lease.h's THE RADIO; the book dials for it).
static void hold_radio(void) {
    uint32_t run = moy_media_pid(), mask;
    if (run != 0u) {
        moy_lease_wifi_hold(run, TAG, TAG_N, &mask);
    }
}

static int32_t media_write(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n) {
    const media_dev_t *r = &s_rows[slot];
    char *src = s_x->src, *cart = s_x->cart, *files = s_x->files, *e = s_x->e, t[8];
    if (n == 0 || n >= PATH_MAX_ || memchr(data, 0, n) != NULL) {
        return MOY_DEV_EBAD;
    }
    memcpy(src, data, n);
    src[n] = 0;
    bool url = any_url(src);
    if (url ? (r->perms & (MOY_DEV_P_MEDIA_URL | MOY_DEV_P_HTTP))
                  != (MOY_DEV_P_MEDIA_URL | MOY_DEV_P_HTTP)
            : !(r->perms & MOY_DEV_P_MEDIA)) {
        return MOY_DEV_EDENIED;
    }
    roots(pid, cart, files);
    if (!url) {
        char *path = s_x->path;
        if (!resolve(cart, src, n, path) || !within(cart, files, path)) {
            return MOY_DEV_EDENIED;             // not one of its folders, there or not
        }
        int st = moy_media_stat(path);
        if (st != 0) {
            return moy_dev_errno(-st);          // nothing started for a file not there
        }
        memcpy(src, path, strlen(path) + 1u);
    } else if (is_https(src) && !moy_media_tls()) {
        return MOY_DEV_EBAD;                    // nothing verifies it
    }
    int rc = moy_media_open(0, 0);
    if (rc < 0) {
        return moy_dev_errno(rc);
    }
    int k = moy_media_play(src, 0);
    if (k < 0) {
        moy_media_stop();
        return moy_dev_errno(k);
    }
    bool urls = false;
    for (int i = 0; i < k; i++) {
        if (moy_media_entry(i, e, PATH_MAX_, t, sizeof t) < 0) {
            continue;
        }
        if (any_url(e)) {
            urls = true;
        } else {
            char *p = s_x->path;
            if (!fold(e, strlen(e), p, PATH_MAX_) || !within(cart, files, p)) {
                moy_media_stop();
                return MOY_DEV_EDENIED;         // its list names a file outside its folders
            }
        }
    }
    char nb[16];
    const char *who = moy_log_name(pid);
    if (who == NULL || *who == 0) {
        snprintf(nb, sizeof nb, "pid %u", (unsigned)pid);
        who = nb;
    }
    uint32_t was = moy_media_take(pid, who, NULL);
    if (urls) {
        hold_radio();
    }
    moy_media_tell(was);
    return (int32_t)n;
}

static int32_t media_set(uint32_t slot, uint32_t pid, const char *cfg, size_t n) {
    (void)pid;
    int64_t vol = -1, pause = -1, seek = -1, stop = -1;
    int skip = 0;
    bool other = false;
    words_t it = { cfg, cfg + n };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        bool ok;
        if (moy_dev_is(w.k, w.kn, "volume")) {
            ok = moy_dev_in_range(&w, 0, 100, &vol);
        } else if (moy_dev_is(w.k, w.kn, "pause")) {
            ok = moy_dev_in_range(&w, 0, 1, &pause);
        } else if (moy_dev_is(w.k, w.kn, "seek")) {
            ok = moy_dev_in_range(&w, 0, 0x7FFFFFFF, &seek);
        } else if (moy_dev_is(w.k, w.kn, "skip")) {
            skip = moy_dev_is(w.v, w.vn, "next") ? 1 : moy_dev_is(w.v, w.vn, "prev") ? -1 : 0;
            ok = skip != 0;
        } else if (moy_dev_is(w.k, w.kn, "stop")) {
            ok = moy_dev_in_range(&w, 1, 1, &stop);
        } else {
            ok = false;
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
        other |= !moy_dev_is(w.k, w.kn, "stop");
    }
    if (got < 0) {
        return MOY_DEV_EBAD;
    }
    if (moy_media_pid() == 0u) {
        return other ? MOY_DEV_EABSENT : 0;     // no stream: a stop has nothing to do
    }
    if (!(s_rows[slot].perms & MOY_DEV_P_MEDIA)) {
        return MOY_DEV_EDENIED;                 // a client's commands are `media`'s
    }
    if (vol >= 0) {
        moy_media_volume((int)vol);
    }
    if (pause >= 0) {
        moy_media_pause((int)pause);
    }
    if (seek >= 0) {
        uint64_t ms = (uint64_t)seek * 1000u;
        if (moy_media_seek(ms > UINT32_MAX ? UINT32_MAX : (uint32_t)ms) != 0) {
            return MOY_DEV_EABSENT;             // a stream, or a length not known yet
        }
    }
    if (skip != 0) {
        moy_media_status_t st;
        moy_media_status(&st);
        int i = st.entry + skip;
        char *e = s_x->e, t[8];
        if (i < 0 || i >= st.entries || moy_media_go(i) != 0) {
            return MOY_DEV_EABSENT;             // past the list's end
        }
        if (moy_media_entry(i, e, PATH_MAX_, t, sizeof t) >= 0 && any_url(e)) {
            hold_radio();
        }
    }
    if (stop > 0) {
        moy_media_stop();
    }
    return 0;
}

static int32_t media_get(uint32_t slot, uint32_t pid, const char *what, size_t n) {
    (void)slot;
    if (moy_dev_is(what, n, "tls")) {
        return moy_media_tls() ? 1 : 0;
    }
    if (moy_dev_is(what, n, "stamp")) {
        int32_t h = moy_media_stamp();
        return h < 0 ? 0 : h;
    }
    moy_media_status_t st;
    bool live = moy_media_status(&st) != 0;
    if (moy_dev_is(what, n, "state")) {
        return live ? st.state : 0;
    }
    if (moy_dev_is(what, n, "mine")) {
        return live && st.client == pid;
    }
    for (size_t i = 0; i < sizeof NOW / sizeof NOW[0]; i++) {
        if (!moy_dev_is(what, n, NOW[i])) {
            continue;
        }
        if (!live) {
            return 0;
        }
        bool fed = st.len_ms > 0u;          // a file's: only a file has a length
        switch (i) {
            case 0: return st.paused ? 1 : 0;
            case 1: return st.entry;
            case 2: return st.entries;
            case 3: return st.rate;
            case 4: return st.channels;
            case 5: return st.kbps;
            case 6: return st.volume;
            case 7: return st.err;
            case 8: return fed ? (int32_t)(st.at_ms / 1000u) : 0;
            default: return fed ? (int32_t)(st.len_ms / 1000u) : 0;
        }
    }
    return MOY_DEV_EBAD;
}

// One text line of what plays into out at *o: false where it does not fit.
static bool line(int which, int alt1, int alt2, uint8_t *out, size_t cap, size_t *o) {
    char *t = s_x->e;
    int k = moy_media_text(which, t, PATH_MAX_);
    if (k <= 0 && alt1 >= 0) {
        k = moy_media_text(alt1, t, PATH_MAX_);
    }
    if (k <= 0 && alt2 >= 0) {
        k = moy_media_text(alt2, t, PATH_MAX_);
    }
    size_t len = k > 0 ? (size_t)k : 0u;
    if (*o + len + 1u > cap) {
        return false;
    }
    memcpy(out + *o, t, len);
    out[*o + len] = '\n';
    *o += len + 1u;
    return true;
}

static int32_t media_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    (void)slot;
    (void)pid;
    if (moy_media_pid() == 0u) {
        return 0;
    }
    size_t o = 0;
    if (!line(MOY_MEDIA_T_TITLE, MOY_MEDIA_T_ENTRY, MOY_MEDIA_T_SONG, out, cap, &o)
        || !line(MOY_MEDIA_T_ARTIST, -1, -1, out, cap, &o)
        || !line(MOY_MEDIA_T_STATION, -1, -1, out, cap, &o)
        || !line(MOY_MEDIA_T_SRC, -1, -1, out, cap, &o)) {
        return MOY_DEV_EFULL;
    }
    return (int32_t)o;
}

static int32_t media_xfer(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n,
                          uint8_t *out, size_t cap) {
    (void)slot;
    char *cart = s_x->cart, *files = s_x->files, *path = s_x->path;
    if (n == 0 || n >= PATH_MAX_ || memchr(data, 0, n) != NULL) {
        return MOY_DEV_EBAD;
    }
    roots(pid, cart, files);
    if (!resolve(cart, (const char *)data, n, path) || !within(cart, files, path)) {
        return MOY_DEV_EDENIED;
    }
    moy_media_probe_t p;
    if (moy_media_probe(path, &p) != 0) {
        return MOY_DEV_EABSENT;
    }
    size_t tn = strlen(p.title), an = strlen(p.artist);
    if (4u + tn + 1u + an > cap) {
        return MOY_DEV_EFULL;
    }
    out[0] = (uint8_t)p.seconds;
    out[1] = (uint8_t)(p.seconds >> 8);
    out[2] = (uint8_t)(p.seconds >> 16);
    out[3] = (uint8_t)(p.seconds >> 24);
    memcpy(out + 4, p.title, tn);
    out[4 + tn] = '\n';
    memcpy(out + 5 + tn, p.artist, an);
    return (int32_t)(5u + tn + an);
}

const moy_dev_ops_t moy_dev_media = {
    .open = media_open, .close = media_close, .read = media_read, .write = media_write,
    .xfer = media_xfer, .set = media_set, .get = media_get,
};
