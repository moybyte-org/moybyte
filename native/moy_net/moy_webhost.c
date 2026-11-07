// The webhost (moy_net.h): the console served from the board.
//
//   GET  /, /index.html, the bundle's assets   open: the page must load to sign in
//   GET  /sync                                 open: {"sync":1}, "a board lives here"
//   POST /auth                                 open: the sign-in (moy_seal.h, moy_webauth.c)
//   GET  /carts.json, /files.json              sealed: the store, streamed
//   POST /sync                                 sealed: a batch applied
//   POST /sync of the system root              sealed: parked for the VM when it
//                                              answers /console.json, else 400
//   GET, POST /gpio                            sealed: the board's pins, where it has a table (moy_gpio.c)
//   GET  /term (Upgrade: websocket)            sealed: the board's shell, the socket handed
//                                              to the web terminal's bridge (native/moy_tty)
//                                              with the session's keys; without the upgrade,
//                                              {"term":1}: the session is good and a shell is
//                                              here (the page asks it why a socket failed,
//                                              which a WebSocket never says)
//   anything in the configured `defer`         sealed, then parked for the VM (/run, /update,
//                                              /console.json; "*": every path)
//
// A SEALED ROUTE takes a request of a signed-in session and nothing else
// (moy_seal.h: the session, the sequence number and the tag ride the query;
// the body is AES-GCM under the session's key), and every answer to one is
// sealed records, its status in their AAD. A request with no session, an
// unknown or expired one, a sequence number taken before, or a seal that does
// not open is refused 401 {"error":"session"} or {"error":"auth"} in the
// clear, before anything reads its body; the page signs in again. The public
// bundle and the marker stay plain: they are the same bytes for everybody.
// A host configured with no system folder is open end to end and seals
// nothing (a test, the dev server); a board in that state refuses every
// sealed route instead (moy_webauth.c, moy_wa_open).
//
// THE SIGN-IN (POST /auth, {"op": ...}): "hello" answers the board's id, a
// nonce and whether a password is set (and its salt); "key" signs a paired
// browser in with its device key; "token" pairs a browser with the QR's
// single-use token; "pake" and "confirm" pair one with the console's password
// by SPAKE2, its curve work on a worker while this poll goes on (the request
// is held, `afd`, and answered at a later poll). docs/os_2026-11/system.md
// section 3 is the threat model.
//
// THE BACKOFF (moy_webauth.c): every credential the sign-in checks -- a
// device key, a token, a password's PAKE -- counts. A source (the peer's IPv4
// address; 0 off a socket) has MOY_WEB_AUTH_FREE wrong ones; each past them
// shuts it out for a window that starts at MOY_WEB_AUTH_WAIT_MS and doubles
// up to MOY_WEB_AUTH_WAIT_MAX_MS. Past MOY_WEB_AUTH_FREE_ALL wrong ones from
// anywhere, every source that has not signed in since waits the same way, so
// more addresses buy no more guesses, and a browser already paired keeps
// working. While a source waits, POST /auth answers 429 {"error":"wait",
// "ms":N} at once, its credential never looked at, so the window is no
// oracle; the poll never sleeps. A right credential clears its source's
// count; the count from anywhere clears after MOY_WEB_AUTH_FORGET_MS with no
// wrong one. A PAKE counts as wrong from its start until its confirm checks.
// A session's sealed requests are not credentials: a seal that does not open
// is refused and counts nothing (its tag is 128 bits).
//
// THE BUNDLE is the image's own (native/moy_web, pre-gzipped), sent from flash
// with nothing resident in between; there is no copy on storage to shadow it.
// THE PULLS stream the store as one JSON object, {"<top>/<rel>": text} and
// {"<top>/<rel>": {"b": base64}} for a cover, in chunks through one 8 KB
// buffer, a file read in 4 KB pieces, so a pull's cost never scales with the
// biggest file. What the wire skips (moy_store_skip) stays home, a files root
// is walked through its kinds alone (which keeps .history and trash home), a
// file that does not read as UTF-8 is absent, and one that stops reading as it
// mid-way ends its value.
//
// THE GOODBYE: a stop with a reason answers every request, ahead of the sign-in,
// with {"error":"closing","why":...} for MOY_WEB_CLOSING_MS before the socket
// closes, so the page can tell a console switched off from one unplugged.
//
// A store touch drains the panel feeder first where the image has one: on the
// T-Deck the card shares the panel's SPI host, and an SD transaction against
// a band in flight is the documented Cache/MMU panic.

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#include "moy_fs.h"
#include "moy_json.h"
#include "moy_net.h"
#include "moy_vol.h"
#include "moy_webauth.h"

// The bundle is native/moy_web's, where the image takes it; a build without it
// (the browser's own) serves the 404 that says how to get one.
#if __has_include("moy_web_blob.h")
#include "moy_web_blob.h"
#define HAVE_BUNDLE 1
extern const moy_web_asset_t moy_web_assets[] __attribute__((weak));
extern const unsigned int moy_web_asset_count __attribute__((weak));
extern const char moy_web_stamp[] __attribute__((weak));
#else
#define HAVE_BUNDLE 0
#endif
extern bool moy_flush_drain(void) __attribute__((weak));
// The web terminal's bridge (native/moy_tty/moy_tty_ws.c), where the image
// takes it: 0 when it took the upgraded socket, else nonzero (busy, memory).
// `seal` is the session's (its keys and the upgrade's sequence number), or
// NULL on the open host.
extern int moy_web_term(int fd, int cols, int rows, const moy_wa_seal_t *seal)
    __attribute__((weak));

#define CHUNK_MIN 8192
#define CHUNK_HEAD 8
#define CHUNK_DATA (CHUNK_HEAD + MOY_SEAL_HEAD)   // a sealed record's head goes in front
#define CHUNK_ALLOC (CHUNK_DATA + CHUNK_MIN + MOY_SEAL_TAG + 2)
#define SEAL_PIECE 4096                 // a sealed answer's record, at most (READ_CHUNK)
#define DOC_MAX 512
#define READ_CHUNK 4096
#define BIN_CHUNK 3072              // READ_CHUNK * 3 / 4: whole base64 quanta
#define WALK_DEPTH 16
#define IO_TRIES 3
#define IO_BACKOFF_MS 20

static const struct {
    const char *name, *ctype;
} ASSETS[] = {
    {"index.html", "text/html; charset=utf-8"},
    {"worker.js", "text/javascript; charset=utf-8"},
    {"moy_store.mjs", "text/javascript; charset=utf-8"},
    {"micropython.mjs", "text/javascript; charset=utf-8"},
    {"micropython.wasm", "application/wasm"},
};


typedef struct {
    int lfd;                        // the listener, -1 when none
    int cur;                        // the connection being served, -1 when none
    int pfd;                        // the parked connection, -1 when none
    int afd;                        // the connection a PAKE answers, -1 when none
    uint8_t closing, taken;
    uint16_t port;
    uint32_t closing_at, parked_at, requests, events;
    int err;
    int64_t epoch;
    char why[32];
    char carts[MOY_WEB_PATH_MAX];
    char files[MOY_WEB_PATH_MAX];
    uint8_t has_files;
    char kinds[96];
    char defer[96];
    char shelf[MOY_WEB_SHELF_BYTES];   // the changed cart folders (moy_web_shelf)
    uint8_t shelf_all;                 // ... and some that did not fit
    moy_http_req_t preq;            // the parked request, its spans in req
    size_t pbn;                     // its body's length, opened
    moy_wa_seal_t pseal;            // its answer's seal
    moy_wa_seal_t hseal;            // moy_web_handle's parked request's
    char *req;                      // MOY_HTTP_REQ_MAX + 1 bytes
    char *chunk;                    // CHUNK_HEAD + CHUNK_MIN + 2
    char *io;                       // READ_CHUNK + 4 (a UTF-8 carry)
    char *b64;                      // BIN_CHUNK / 3 * 4
    uint32_t peer;                  // the request's source
} web_t;

static web_t *W;

typedef struct {
    int fd;                         // -1: into buf
    char *buf;
    size_t n, cap;
    int err;
    int chunked;
    size_t cn;                      // bytes waiting in W->chunk
    int status;                     // a sealed answer's (its records' AAD)
    uint32_t ri;                    // its next record's index
    const moy_wa_seal_t *seal;      // NULL: the answer goes in the clear
    moy_wa_seal_t sealed;           // what `seal` points at for a gated route
    size_t bn;                      // the request body's length, once opened
} out_t;

// -- the host's state -----------------------------------------------------------

static int ensure(void) {
    if (W != NULL) {
        return 1;
    }
    web_t *w = moy_net_alloc(sizeof(web_t));
    if (w == NULL) {
        return 0;
    }
    w->req = moy_net_alloc(MOY_HTTP_REQ_MAX + 1u);
    w->chunk = moy_net_alloc(CHUNK_ALLOC);
    w->io = moy_net_alloc(READ_CHUNK + 4u);
    w->b64 = moy_net_alloc(BIN_CHUNK / 3u * 4u + 4u);
    if (w->req == NULL || w->chunk == NULL || w->io == NULL || w->b64 == NULL) {
        moy_net_free(w->req);
        moy_net_free(w->chunk);
        moy_net_free(w->io);
        moy_net_free(w->b64);
        moy_net_free(w);
        return 0;
    }
    w->lfd = w->cur = w->pfd = w->afd = -1;
    W = w;
    return 1;
}

static void copy_str(char *dst, size_t cap, const char *src) {
    snprintf(dst, cap, "%s", src ? src : "");
}

// A NUL-separated list ending in an empty name, copied whole or not at all.
static void copy_list(char *dst, size_t cap, const char *src) {
    size_t n = 0;
    if (src != NULL) {
        while (src[n] != '\0') {
            n += strlen(src + n) + 1u;
        }
    }
    if (n + 1u > cap) {
        n = 0;
    }
    if (n) {
        memcpy(dst, src, n);
    }
    dst[n] = '\0';
}

static void config(const moy_web_cfg_t *cfg) {
    W->port = cfg->port;
    copy_str(W->carts, sizeof(W->carts), cfg->carts);
    W->has_files = cfg->files != NULL;
    copy_str(W->files, sizeof(W->files), cfg->files);
    copy_list(W->kinds, sizeof(W->kinds), cfg->kinds);
    copy_list(W->defer, sizeof(W->defer), cfg->defer);
    W->epoch = cfg->epoch;
    moy_wa_config(cfg->system, cfg->epoch);
}

static void close_conn(int *fd) {
    if (*fd >= 0) {
        moy_http_close(*fd);
        *fd = -1;
    }
}

int moy_web_start(const moy_web_cfg_t *cfg) {
    if (!ensure()) {
        return MOY_ENOMEM;
    }
    if (W->lfd >= 0 && cfg->port != 0 && W->port != cfg->port) {
        close_conn(&W->lfd);
    }
    uint16_t bound = W->port;
    config(cfg);
    if (W->lfd >= 0 && cfg->port == 0) {
        W->port = bound;            // a configure-only start keeps the socket's port
    }
    W->closing = 0;
    W->err = 0;
    if (W->lfd < 0 && cfg->port != 0) {
        int fd = moy_http_listen(cfg->port);
        if (fd < 0) {
            W->err = -fd;
            return -fd;
        }
        W->lfd = fd;
        W->requests = 0;
    }
    return 0;
}

int moy_web_bind(uint16_t port) {
    if (!ensure()) {
        return MOY_ENOMEM;
    }
    if (W->lfd >= 0) {
        if (W->port == port) {
            return 0;
        }
        close_conn(&W->lfd);
    }
    int fd = moy_http_listen(port);
    if (fd < 0) {
        W->err = -fd;
        return -fd;
    }
    W->lfd = fd;
    W->port = port;
    W->requests = 0;
    W->closing = 0;
    W->err = 0;
    return 0;
}

static void stop_now(void) {
    moy_gpio_release();
    close_conn(&W->lfd);
    close_conn(&W->pfd);
    close_conn(&W->afd);
    W->closing = 0;
    W->taken = 0;
    W->events |= MOY_WEB_EV_STOPPED;
}

void moy_web_stop(const char *why) {
    if (W == NULL) {
        return;
    }
    if (why == NULL) {
        stop_now();
        return;
    }
    copy_str(W->why, sizeof(W->why), why);
    W->closing = 1;
    W->closing_at = moy_net_ms();
}

int moy_web_auth(const char *system, int64_t epoch) {
    return moy_wa_config(system, epoch);
}

void moy_web_board_id(char out[33]) {
    moy_wa_board_id(out);
}

void moy_web_token(char out[33]) {
    moy_wa_token(out);
}

size_t moy_web_keys(char *out, size_t cap) {
    return moy_wa_keys(out, cap);
}

int moy_web_key_drop(const char *kid) {
    return moy_wa_drop(kid);
}

void moy_web_state(moy_web_state_t *out) {
    memset(out, 0, sizeof(*out));
    if (W == NULL) {
        return;
    }
    out->serving = W->lfd >= 0 && !W->closing;
    out->listening = W->lfd >= 0;
    out->closing = W->closing;
    out->parked = W->pfd >= 0;
    out->port = W->port;
    out->requests = W->requests;
    out->events = W->events;
    out->err = W->err;
}

uint32_t moy_web_events(void) {
    if (W == NULL) {
        return 0;
    }
    if (moy_wa_paired()) {
        W->events |= MOY_WEB_EV_PAIRED;
    }
    uint32_t e = W->events;
    W->events = 0;
    return e;
}

size_t moy_web_shelf(char *out, size_t cap) {
    size_t n = 0;
    if (W != NULL && !W->shelf_all) {
        while (W->shelf[n] != '\0') {
            n += strlen(W->shelf + n) + 1u;
        }
    }
    int all = W != NULL && (W->shelf_all || n + 1u > cap);
    if (!all && cap > 0) {
        memcpy(out, W != NULL ? W->shelf : "", n);
        out[n] = '\0';
    }
    if (W != NULL) {
        W->shelf[0] = '\0';
        W->shelf_all = 0;
    }
    return all ? MOY_WEB_SHELF_ALL : n;
}

const char *moy_web_stamp_text(void) {
    #if HAVE_BUNDLE
    if (moy_web_stamp != NULL && moy_web_assets != NULL
        && moy_web_asset_count != 0) {
        return moy_web_stamp;
    }
    #endif
    return NULL;
}

// -- the response writer ---------------------------------------------------------

static void raw(out_t *o, const void *p, size_t n) {
    if (o->err || n == 0) {
        return;
    }
    if (o->fd >= 0) {
        if (moy_http_send(o->fd, p, n) != 0) {
            o->err = 1;
        }
        return;
    }
    if (o->n + n > o->cap) {
        size_t cap = (o->cap ? o->cap * 2u : 1024u) + n;
        char *b = moy_net_alloc(cap);
        if (b == NULL) {
            o->err = 1;
            return;
        }
        if (o->buf != NULL) {
            memcpy(b, o->buf, o->n);
            moy_net_free(o->buf);
        }
        o->buf = b;
        o->cap = cap;
    }
    memcpy(o->buf + o->n, p, n);
    o->n += n;
}

static void rawz(out_t *o, const char *s) {
    raw(o, s, strlen(s));
}

// One HTTP chunk of what W->chunk holds: a sealed record of it when the
// answer is sealed, its head and tag around the bytes in place.
static void frame(out_t *o) {
    char *start = W->chunk + CHUNK_DATA;
    size_t n = o->cn;
    if (o->seal != NULL) {
        uint8_t *d = (uint8_t *)start;
        moy_seal_rec(1, o->seal->kb, o->seal->seq, o->ri++, o->status, 0, d - MOY_SEAL_HEAD,
                     d, n, d + n);
        start -= MOY_SEAL_HEAD;
        n += MOY_SEAL_HEAD + MOY_SEAL_TAG;
    }
    char head[CHUNK_HEAD + 1];
    int k = snprintf(head, sizeof(head), "%x\r\n", (unsigned)n);
    char *c = start - k;
    memcpy(c, head, (size_t)k);
    start[n] = '\r';
    start[n + 1u] = '\n';
    raw(o, c, (size_t)k + n + 2u);
    o->cn = 0;
}

// A sealed answer's records for `p`, in pieces of SEAL_PIECE, and its last,
// empty one. Through W->io: no answer that seals this way is also a pull.
static void sealed_records(out_t *o, const char *p, size_t n) {
    uint8_t head[MOY_SEAL_HEAD], tag[MOY_SEAL_TAG];
    while (n > 0 && !o->err) {
        size_t k = n < SEAL_PIECE ? n : SEAL_PIECE;
        memcpy(W->io, p, k);
        moy_seal_rec(1, o->seal->kb, o->seal->seq, o->ri++, o->status, 0, head,
                     (uint8_t *)W->io, k, tag);
        raw(o, head, sizeof head);
        raw(o, W->io, k);
        raw(o, tag, sizeof tag);
        p += k;
        n -= k;
    }
    moy_seal_rec(1, o->seal->kb, o->seal->seq, o->ri++, o->status, 1, head, NULL, 0, tag);
    raw(o, head, sizeof head);
    raw(o, tag, sizeof tag);
}

static size_t sealed_len(size_t n) {
    size_t recs = (n + SEAL_PIECE - 1u) / SEAL_PIECE;
    return n + (recs + 1u) * (MOY_SEAL_HEAD + MOY_SEAL_TAG);
}

static void body(out_t *o, const char *p, size_t n) {
    if (!o->chunked) {
        raw(o, p, n);
        return;
    }
    while (n > 0 && !o->err) {
        size_t take = CHUNK_MIN - o->cn;
        if (take > n) {
            take = n;
        }
        memcpy(W->chunk + CHUNK_DATA + o->cn, p, take);
        o->cn += take;
        p += take;
        n -= take;
        if (o->cn == CHUNK_MIN) {
            frame(o);
        }
    }
}

static void bodyz(out_t *o, const char *s) {
    body(o, s, strlen(s));
}

static void answer(out_t *o, int status, const char *ctype, const char *p, size_t n) {
    char head[256];
    if (o->seal == NULL) {
        size_t k = moy_http_head(head, sizeof(head), status, ctype, n);
        raw(o, head, k < sizeof(head) ? k : sizeof(head) - 1u);
        raw(o, p, n);
        return;
    }
    o->status = status;
    o->ri = 0;
    size_t k = moy_http_head(head, sizeof(head), status, "application/octet-stream",
                             sealed_len(n));
    raw(o, head, k < sizeof(head) ? k : sizeof(head) - 1u);
    sealed_records(o, p, n);
}

static void simple(out_t *o, int status, const char *ctype, const char *text) {
    answer(o, status, ctype, text, strlen(text));
}

static void json(out_t *o, int status, const char *text) {
    simple(o, status, "application/json", text);
}

static void chunked_head(out_t *o) {
    o->status = 200;
    o->ri = 0;
    rawz(o, "HTTP/1.1 200 OK\r\n");
    rawz(o, o->seal != NULL ? "Content-Type: application/octet-stream\r\n"
                            : "Content-Type: application/json\r\n");
    rawz(o, "Transfer-Encoding: chunked\r\n"
               "Cache-Control: no-store\r\n"
               "Access-Control-Allow-Origin: *\r\n"
               "Connection: close\r\n\r\n");
    o->chunked = 1;
    o->cn = 0;
}

static void chunked_end(out_t *o) {
    if (o->cn) {
        frame(o);
    }
    if (o->seal != NULL) {
        char last[4 + MOY_SEAL_HEAD + MOY_SEAL_TAG + 2];
        uint8_t *r = (uint8_t *)last + 4;
        memcpy(last, "14\r\n", 4);
        moy_seal_rec(1, o->seal->kb, o->seal->seq, o->ri++, o->status, 1, r, NULL, 0,
                     r + MOY_SEAL_HEAD);
        memcpy(last + 4 + MOY_SEAL_HEAD + MOY_SEAL_TAG, "\r\n", 2);
        raw(o, last, sizeof last);
    }
    o->chunked = 0;
    rawz(o, "0\r\n\r\n");
}

// -- JSON text, as MicroPython's json.dumps writes it -----------------------------

static void esc(out_t *o, const char *s, size_t n) {
    const char *run = s;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        const char *rep = NULL;
        char u[8];
        if (c == '"') {
            rep = "\\\"";
        } else if (c == '\\') {
            rep = "\\\\";
        } else if (c >= 32) {
            continue;
        } else if (c == '\n') {
            rep = "\\n";
        } else if (c == '\r') {
            rep = "\\r";
        } else if (c == '\t') {
            rep = "\\t";
        } else {
            snprintf(u, sizeof(u), "\\u%04x", c);
            rep = u;
        }
        body(o, run, (size_t)(s + i - run));
        bodyz(o, rep);
        run = s + i + 1;
    }
    body(o, run, (size_t)(s + n - run));
}

static void jstr(out_t *o, const char *s, size_t n) {
    body(o, "\"", 1);
    esc(o, s, n);
    body(o, "\"", 1);
}

// -- the pull -----------------------------------------------------------------------

typedef struct {
    char *names;            // each NUL-terminated, then its is_dir byte
    size_t n, cap;
    size_t count;
    int fail;
} names_t;

static int collect(void *ctx, const char *name, size_t len, int is_dir,
                   uint32_t size) {
    (void)size;
    names_t *l = ctx;
    if (l->n + len + 2u > l->cap) {
        size_t cap = (l->cap ? l->cap * 2u : 512u) + len + 2u;
        char *p = moy_store_alloc(cap);
        if (p == NULL) {
            l->fail = 1;
            return 1;
        }
        if (l->names != NULL) {
            memcpy(p, l->names, l->n);
            moy_store_free(l->names, l->cap);
        }
        l->names = p;
        l->cap = cap;
    }
    memcpy(l->names + l->n, name, len);
    l->names[l->n + len] = '\0';
    l->names[l->n + len + 1u] = (char)(is_dir != 0);
    l->n += len + 2u;
    l->count++;
    return 0;
}

static int by_name(const void *a, const void *b) {
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

// The folder's entries, sorted by name: `*index` (moy_store_alloc'd, `count`
// pointers into l->names), or 0 when it cannot be listed after IO_TRIES.
static int list_sorted(const char *path, names_t *l, const char ***index) {
    *index = NULL;
    for (int t = 0; t < IO_TRIES; t++) {
        memset(l, 0, sizeof(*l));
        moy_vol_t v;
        const char *rest;
        int rc = moy_vol_at(path, &v, &rest);
        if (rc == 0) {
            rc = moy_vol_list(&v, rest, collect, l);
        }
        if (rc == 0 && !l->fail) {
            break;
        }
        if (l->names != NULL) {
            moy_store_free(l->names, l->cap);
        }
        memset(l, 0, sizeof(*l));
        if (rc == 2 || t + 1 == IO_TRIES) {    // ENOENT: nothing to retry
            return 0;
        }
        moy_net_sleep_ms(IO_BACKOFF_MS);
    }
    if (l->count == 0) {
        return 1;
    }
    const char **ix = moy_store_alloc(l->count * sizeof(char *));
    if (ix == NULL) {
        moy_store_free(l->names, l->cap);
        memset(l, 0, sizeof(*l));
        return 0;
    }
    size_t k = 0;
    for (size_t i = 0; i < l->n; k++) {
        ix[k] = l->names + i;
        i += strlen(l->names + i) + 2u;
    }
    qsort(ix, l->count, sizeof(char *), by_name);
    *index = ix;
    return 1;
}

static void list_free(names_t *l, const char **ix) {
    if (ix != NULL) {
        moy_store_free((void *)ix, l->count * sizeof(char *));
    }
    if (l->names != NULL) {
        moy_store_free(l->names, l->cap);
    }
}

static int is_dir_of(const char *name) {
    return name[strlen(name) + 1u] != 0;
}

static int open_read(const char *path, moy_vol_file_t **f) {
    moy_vol_t v;
    const char *rest;
    int rc = moy_vol_at(path, &v, &rest);
    return rc ? rc : moy_vol_open(&v, rest, MOY_VOL_READ, f);
}

// The bytes of `s` up to its last whole UTF-8 sequence: `*whole` that length.
// 0 when the bytes cannot be UTF-8 however they continue.
static int utf8_whole(const char *s, size_t n, size_t *whole) {
    size_t cut = n;
    for (size_t back = 1; back <= 3 && back <= n; back++) {
        unsigned char c = (unsigned char)s[n - back];
        if ((c & 0xC0) == 0x80) {
            continue;
        }
        size_t need = c >= 0xF0 ? 4u : c >= 0xE0 ? 3u : c >= 0xC0 ? 2u : 1u;
        if (need > back) {
            cut = n - back;
        }
        break;
    }
    *whole = cut;
    return moy_utf8_valid(s, cut);
}

// Up to `cap` bytes of the file into buf, the carry first.
static int read_some(moy_vol_file_t *f, char *buf, size_t cap, size_t *got) {
    *got = 0;
    while (*got < cap) {
        size_t k = 0;
        int rc = moy_vol_read(f, buf + *got, cap - *got, &k);
        if (rc != 0) {
            return rc;
        }
        if (k == 0) {
            break;
        }
        *got += k;
    }
    return 0;
}

// A re-open past the first `pos` bytes, for a card whose handle latched its
// error (a FatFS handle answers every later call with it).
static int reopen_at(const char *path, uint32_t pos, moy_vol_file_t **f) {
    for (int t = 0; t < IO_TRIES; t++) {
        if (t) {
            moy_net_sleep_ms(IO_BACKOFF_MS);
        }
        if (open_read(path, f) != 0) {
            continue;
        }
        uint32_t left = pos;
        int rc = 0;
        while (left > 0 && rc == 0) {
            size_t k = 0;
            // Into the base64 scratch: W->io holds the caller's carry.
            rc = moy_vol_read(*f, W->b64, left < READ_CHUNK ? left : READ_CHUNK, &k);
            if (k == 0) {
                rc = MOY_EIO;
            }
            left -= (uint32_t)k;
        }
        if (rc == 0) {
            return 0;
        }
        moy_vol_close(*f);
    }
    *f = NULL;
    return MOY_EIO;
}

// The file open with its first `cap` bytes read: 0, or the card's error after
// IO_TRIES (a transient EIO on a card is retried by re-opening, the read
// included, never taken for the file's end).
static int open_first(const char *path, size_t cap, moy_vol_file_t **f,
                      size_t *got) {
    int rc = 0;
    for (int t = 0; t < IO_TRIES; t++) {
        if (t) {
            moy_net_sleep_ms(IO_BACKOFF_MS);
        }
        rc = open_read(path, f);
        if (rc != 0) {
            continue;
        }
        rc = read_some(*f, W->io, cap, got);
        if (rc == 0) {
            return 0;
        }
        moy_vol_close(*f);
    }
    *f = NULL;
    return rc;
}

// The next `cap` bytes into W->io + at, re-opening past `pos` on a card error.
static int read_next(const char *path, moy_vol_file_t **f, uint32_t pos,
                     size_t at, size_t cap, size_t *got) {
    if (read_some(*f, W->io + at, cap, got) == 0) {
        return 0;
    }
    moy_vol_close(*f);
    *f = NULL;
    if (reopen_at(path, pos, f) != 0) {
        return MOY_EIO;
    }
    return read_some(*f, W->io + at, cap, got);
}

static void text_value(out_t *o, const char *path, const char *rel, int *first) {
    moy_vol_file_t *f;
    size_t carry = 0, got = 0, whole = 0;
    uint32_t pos = 0;
    if (open_first(path, READ_CHUNK, &f, &got) != 0) {
        return;                     // the card gave up: skip, never fail the pull
    }
    if (!utf8_whole(W->io, got, &whole)) {
        moy_vol_close(f);
        return;                     // not text: absent, never present and empty
    }
    if (!*first) {
        body(o, ",", 1);
    }
    *first = 0;
    jstr(o, rel, strlen(rel));
    body(o, ":\"", 2);
    for (;;) {
        esc(o, W->io, whole);
        pos += (uint32_t)whole;
        carry = got - whole;
        memmove(W->io, W->io + whole, carry);
        if (got < READ_CHUNK || o->err) {
            break;                  // the end, or a client gone
        }
        size_t k = 0;
        if (read_next(path, &f, pos + (uint32_t)carry, carry,
                      READ_CHUNK - carry, &k) != 0 || k == 0) {
            break;                  // the card gave up, or the end
        }
        got = carry + k;
        if (!utf8_whole(W->io, got, &whole)) {
            break;                  // not text past here: end the value
        }
    }
    if (f != NULL) {
        moy_vol_close(f);
    }
    body(o, "\"", 1);
}

static const char B64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t b64(const unsigned char *p, size_t n, char *out) {
    size_t k = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)p[i] << 16;
        if (i + 1 < n) {
            v |= (uint32_t)p[i + 1] << 8;
        }
        if (i + 2 < n) {
            v |= p[i + 2];
        }
        out[k++] = B64[(v >> 18) & 63];
        out[k++] = B64[(v >> 12) & 63];
        out[k++] = i + 1 < n ? B64[(v >> 6) & 63] : '=';
        out[k++] = i + 2 < n ? B64[v & 63] : '=';
    }
    return k;
}

static void binary_value(out_t *o, const char *path, const char *rel,
                         int *first) {
    moy_vol_file_t *f;
    size_t got = 0;
    uint32_t pos = 0;
    if (open_first(path, BIN_CHUNK, &f, &got) != 0) {
        return;
    }
    if (!*first) {
        body(o, ",", 1);
    }
    *first = 0;
    jstr(o, rel, strlen(rel));
    body(o, ":{\"b\":\"", 7);
    while (got > 0 && !o->err) {
        body(o, W->b64, b64((const unsigned char *)W->io, got, W->b64));
        pos += (uint32_t)got;
        if (got < BIN_CHUNK
            || read_next(path, &f, pos, 0, BIN_CHUNK, &got) != 0) {
            break;
        }
    }
    if (f != NULL) {
        moy_vol_close(f);
    }
    body(o, "\"}", 2);
}

// runtime/carts/moy_sync.py's is_binary: a cart's cover by name, a theme package's
// fonts and sheets by suffix.
static int is_binary(const char *name) {
    static const char *const suffixes[] = {".fnt", ".moygfx"};
    size_t n = strlen(name);
    if (strcmp(name, "cover.png") == 0) {
        return 1;
    }
    for (size_t i = 0; i < sizeof(suffixes) / sizeof(suffixes[0]); i++) {
        size_t k = strlen(suffixes[i]);
        if (n > k && strcmp(name + n - k, suffixes[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

// The walk's one path and rel, grown and cut in place.
typedef struct {
    char path[MOY_WEB_PATH_MAX + 512];
    char rel[512];
} walk_t;

static void walk(out_t *o, walk_t *w, size_t pn, size_t rn, int depth,
                 int *first) {
    if (depth > WALK_DEPTH || o->err) {
        return;
    }
    names_t l;
    const char **ix;
    if (!list_sorted(w->path, &l, &ix)) {
        return;
    }
    for (size_t i = 0; i < l.count && !o->err; i++) {
        const char *name = ix[i];
        size_t len = strlen(name);
        if (moy_store_skip(name, len, 0)) {
            continue;
        }
        if (pn + 1u + len >= sizeof(w->path) || rn + 1u + len >= sizeof(w->rel)) {
            continue;
        }
        w->path[pn] = '/';
        memcpy(w->path + pn + 1u, name, len + 1u);
        w->rel[rn] = '/';
        memcpy(w->rel + rn + 1u, name, len + 1u);
        if (is_dir_of(name)) {
            walk(o, w, pn + 1u + len, rn + 1u + len, depth + 1, first);
        } else if (is_binary(name)) {
            binary_value(o, w->path, w->rel, first);
        } else {
            text_value(o, w->path, w->rel, first);
        }
        w->path[pn] = '\0';
        w->rel[rn] = '\0';
    }
    list_free(&l, ix);
}

// "*" in the list is every path: a host the VM answers whole (the Zero's
// setup form).
static int in_list(const char *list, const char *s, size_t n) {
    for (const char *k = list; *k; k += strlen(k) + 1u) {
        if ((k[0] == '*' && k[1] == '\0') || (strlen(k) == n && memcmp(k, s, n) == 0)) {
            return 1;
        }
    }
    return 0;
}

static void pack(out_t *o, const char *root, const char *kinds) {
    body(o, "{", 1);
    int first = 1;
    walk_t *w = moy_store_alloc(sizeof(walk_t));
    names_t l;
    const char **ix;
    size_t rn = strlen(root);
    if (w != NULL && rn < sizeof(w->path) && list_sorted(root, &l, &ix)) {
        for (size_t i = 0; i < l.count && !o->err; i++) {
            const char *top = ix[i];
            size_t len = strlen(top);
            if (!is_dir_of(top) || (kinds != NULL && !in_list(kinds, top, len))
                || rn + 1u + len >= sizeof(w->path) || len >= sizeof(w->rel)) {
                continue;
            }
            memcpy(w->path, root, rn);
            w->path[rn] = '/';
            memcpy(w->path + rn + 1u, top, len + 1u);
            memcpy(w->rel, top, len + 1u);
            walk(o, w, rn + 1u + len, len, 0, &first);
        }
        list_free(&l, ix);
    }
    if (w != NULL) {
        moy_store_free(w, sizeof(walk_t));
    }
    body(o, "}", 1);
}

// -- the routes ------------------------------------------------------------------------

static void store_gate(void) {
    if (moy_flush_drain != NULL) {
        moy_flush_drain();
    }
}

static int path_is(const char *p, size_t n, const char *want) {
    return strlen(want) == n && memcmp(p, want, n) == 0;
}

static void asset(out_t *o, const char *name, const char *ctype) {
    char gz[40];
    snprintf(gz, sizeof(gz), "%s.gz", name);
    const unsigned char *data = NULL;
    unsigned len = 0;
    int zipped = 0;
    #if HAVE_BUNDLE
    const moy_web_asset_t *hit = NULL;
    if (moy_web_assets != NULL && moy_web_asset_count != 0) {
        for (unsigned i = 0; i < moy_web_asset_count && hit == NULL; i++) {
            if (strcmp(moy_web_assets[i].name, gz) == 0) {
                hit = &moy_web_assets[i];
                zipped = 1;
            }
        }
        for (unsigned i = 0; i < moy_web_asset_count && hit == NULL; i++) {
            if (strcmp(moy_web_assets[i].name, name) == 0) {
                hit = &moy_web_assets[i];
            }
        }
    }
    if (hit != NULL) {
        data = hit->data;
        len = hit->len;
    }
    #endif
    if (data == NULL) {
        simple(o, 404, "text/plain; charset=utf-8",
               "this firmware has no web console baked in -- build "
               "firmware/web_runner/dist and reflash");
        return;
    }
    char head[320];
    int k = snprintf(head, sizeof(head),
                     "HTTP/1.1 200 OK\r\n"
                     "Content-Type: %s\r\n"
                     "Content-Length: %u\r\n"
                     "%s"
                     "Cache-Control: no-store\r\n"
                     "Access-Control-Allow-Origin: *\r\n"
                     "Connection: close\r\n\r\n",
                     ctype, len,
                     zipped ? "Content-Encoding: gzip\r\n" : "");
    raw(o, head, (size_t)k);
    for (unsigned i = 0; i < len && !o->err; i += 4096u) {
        unsigned n = len - i < 4096u ? len - i : 4096u;
        raw(o, data + i, n);
    }
}

// THE SYSTEM ROOT IS THE VM'S. Which of a browser's Settings a board takes,
// and what changing one sets off, are the console's own (runtime/carts/moy_sync.py,
// merge_system), so a signed-in system batch is parked for the VM -- the one that
// serves its pull, /console.json -- and refused where no VM answers it.
static const char SYSTEM_PULL[] = "/console.json";

// 1 when answered, 0 when the batch is parked for the VM.
static int sync_post(out_t *o, const char *b, size_t n) {
    moy_sync_batch_t batch;
    if (moy_sync_batch(b, n, &batch) != MOY_SYNC_OK) {
        json(o, 400, "{\"error\":\"bad batch\"}");
        return 1;
    }
    if (batch.root == MOY_SYNC_SYSTEM
        && in_list(W->defer, SYSTEM_PULL, sizeof(SYSTEM_PULL) - 1u)) {
        return 0;
    }
    if ((batch.root == MOY_SYNC_FILES && !W->has_files)
        || batch.root == MOY_SYNC_SYSTEM) {
        json(o, 400, "{\"error\":\"no such store\"}");
        return 1;
    }
    moy_sync_store_t s = {
        .carts = W->carts,
        .files = W->has_files ? W->files : NULL,
        .kinds = W->kinds[0] ? W->kinds : NULL,
        .journal = 1,
        .ts = (int64_t)time(NULL) - W->epoch,
        .shelf = W->shelf,
        .shelf_cap = sizeof(W->shelf),
    };
    moy_sync_result_t r;
    store_gate();
    moy_sync_apply(&s, batch.root, batch.ops, batch.ops_end, &r);
    if (r.shelf && batch.root == MOY_SYNC_CARTS) {
        W->events |= MOY_WEB_EV_SHELF;
        if (r.shelf & MOY_SYNC_SHELF_FULL) {
            W->shelf_all = 1;
        }
    }
    if (r.refused) {
        printf("SYNC %u applied, %u refused: %s\n", (unsigned)r.applied,
               (unsigned)r.refused, r.err[0].why);
    }
    char doc[64 + MOY_SYNC_ERRS * 56];
    int k = snprintf(doc, sizeof(doc), "{\"ok\": %u, \"err\": [",
                     (unsigned)r.applied);
    for (uint32_t i = 0; i < r.nerr && (size_t)k < sizeof(doc); i++) {
        k += snprintf(doc + k, sizeof(doc) - (size_t)k, "%s[%u, \"%s\"]",
                      i ? ", " : "", (unsigned)r.err[i].index, r.err[i].why);
    }
    if ((size_t)k < sizeof(doc)) {
        snprintf(doc + k, sizeof(doc) - (size_t)k, "]}");
    }
    json(o, 200, doc);
    return 1;
}

static void pull(out_t *o, const char *root, const char *kinds) {
    store_gate();
    chunked_head(o);
    pack(o, root, kinds);
    chunked_end(o);
}

// Whether the route at path [p, p+pn) is a sealed one (the header's table).
static int gated(int post, int get, const char *p, size_t pn) {
    if ((post || get) && in_list(W->defer, p, pn)) {
        return 1;
    }
    return (post && path_is(p, pn, "/sync"))
           || (get && (path_is(p, pn, "/carts.json") || path_is(p, pn, "/term")
                       || (path_is(p, pn, "/files.json") && W->has_files)))
           || ((post || get) && path_is(p, pn, "/gpio"));
}

// A sealed route's request opened (`o->bn` its body's length, `o->seal` its
// answer's seal): 1, else its 401 answered and 0.
static int signed_in(out_t *o, const char *m, size_t mn, const char *t, size_t tn, char *b,
                     size_t bn) {
    o->bn = bn;
    int g = moy_wa_check(m, mn, t, tn, b, &o->bn, &o->sealed);
    if (g != MOY_WA_OK) {
        json(o, 401, g == MOY_WA_NONE ? "{\"error\":\"session\"}" : "{\"error\":\"auth\"}");
        return 0;
    }
    o->seal = o->sealed.on ? &o->sealed : NULL;
    return 1;
}

enum { ROUTE_PARKED = 0, ROUTE_DONE = 1, ROUTE_LATER = 2 };

// ROUTE_DONE when answered, ROUTE_PARKED when the VM answers it, ROUTE_LATER
// when the sign-in's worker does.
static int route(out_t *o, const char *m, size_t mn, const char *t, size_t tn,
                 const char *b, size_t bn) {
    if (W->closing) {
        char doc[80];
        snprintf(doc, sizeof(doc), "{\"error\":\"closing\",\"why\":\"%s\"}",
                 W->why);
        json(o, 503, doc);
        return ROUTE_DONE;
    }
    const char *q = memchr(t, '?', tn);
    size_t pn = q ? (size_t)(q - t) : tn;
    int post = path_is(m, mn, "POST"), get = path_is(m, mn, "GET");
    if (post && path_is(t, pn, "/auth")) {
        char doc[DOC_MAX];
        int st = moy_wa_auth(W->peer, b, bn, doc, sizeof doc);
        if (st == 0) {
            return ROUTE_LATER;
        }
        json(o, st, doc);
        return ROUTE_DONE;
    }
    if (gated(post, get, t, pn)) {
        if (!signed_in(o, m, mn, t, tn, (char *)b, bn)) {
            return ROUTE_DONE;
        }
        bn = o->bn;
        if (in_list(W->defer, t, pn)) {
            return ROUTE_PARKED;
        }
    }
    if (post && path_is(t, pn, "/sync")) {
        return sync_post(o, b, bn) ? ROUTE_DONE : ROUTE_PARKED;
    }
    const uint8_t *gpins;
    if (path_is(t, pn, "/gpio") && (post || get) && moy_gpio_pins != NULL
        && moy_gpio_pins(&gpins) > 0) {
        char doc[512];
        int status = moy_gpio_request(post, t, tn, b, bn, doc, sizeof(doc));
        json(o, status, doc);
        return ROUTE_DONE;
    }
    if (!get) {
        json(o, 405, "{\"error\":\"GET, or POST /auth /sync /run /update\"}");
        return ROUTE_DONE;
    }
    if (path_is(t, pn, "/sync")) {
        json(o, 200, "{\"sync\":1}");
        return ROUTE_DONE;
    }
    if (path_is(t, pn, "/") || path_is(t, pn, "/index.html")) {
        asset(o, "index.html", ASSETS[0].ctype);
        return ROUTE_DONE;
    }
    for (size_t i = 0; i < sizeof(ASSETS) / sizeof(ASSETS[0]); i++) {
        if (pn > 1 && path_is(t + 1, pn - 1, ASSETS[i].name)) {
            asset(o, ASSETS[i].name, ASSETS[i].ctype);
            return ROUTE_DONE;
        }
    }
    int carts = path_is(t, pn, "/carts.json");
    if (carts || (path_is(t, pn, "/files.json") && W->has_files)) {
        pull(o, carts ? W->carts : W->files, carts ? NULL : W->kinds);
        return ROUTE_DONE;
    }
    simple(o, 404, "text/plain; charset=utf-8", "not found");
    return ROUTE_DONE;
}

// -- the web terminal's WebSocket (native/moy_tty/moy_tty_ws.c) ------------------------

typedef struct {
    uint32_t h[5];
    uint8_t b[64];
} sha1_t;

static uint32_t rol(uint32_t v, int k) {
    return (v << k) | (v >> (32 - k));
}

static void sha1_block(sha1_t *c) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = (uint32_t)c->b[4 * i] << 24 | (uint32_t)c->b[4 * i + 1] << 16
               | (uint32_t)c->b[4 * i + 2] << 8 | c->b[4 * i + 3];
    }
    for (int i = 16; i < 80; i++) {
        w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    }
    uint32_t a = c->h[0], b = c->h[1], cc = c->h[2], d = c->h[3], e = c->h[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) {
            f = (b & cc) | (~b & d), k = 0x5A827999u;
        } else if (i < 40) {
            f = b ^ cc ^ d, k = 0x6ED9EBA1u;
        } else if (i < 60) {
            f = (b & cc) | (b & d) | (cc & d), k = 0x8F1BBCDCu;
        } else {
            f = b ^ cc ^ d, k = 0xCA62C1D6u;
        }
        uint32_t t = rol(a, 5) + f + e + k + w[i];
        e = d, d = cc, cc = rol(b, 30), b = a, a = t;
    }
    c->h[0] += a, c->h[1] += b, c->h[2] += cc, c->h[3] += d, c->h[4] += e;
}

static void sha1(const char *p, size_t n, uint8_t out[20]) {
    sha1_t c = {{0x67452301u, 0xEFCDAB89u, 0x98BADCFEu, 0x10325476u, 0xC3D2E1F0u}, {0}};
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        c.b[k++] = (uint8_t)p[i];
        if (k == 64) {
            sha1_block(&c);
            k = 0;
        }
    }
    uint64_t bits = (uint64_t)n * 8u;
    c.b[k++] = 0x80;
    if (k > 56) {
        memset(c.b + k, 0, 64 - k);
        sha1_block(&c);
        k = 0;
    }
    memset(c.b + k, 0, 56 - k);
    for (int i = 0; i < 8; i++) {
        c.b[56 + i] = (uint8_t)(bits >> (56 - 8 * i));
    }
    sha1_block(&c);
    for (int i = 0; i < 20; i++) {
        out[i] = (uint8_t)(c.h[i / 4] >> (24 - 8 * (i % 4)));
    }
}

// A header's value in the head [p, p+n), its name matched without case.
static const char *header(const char *p, size_t n, const char *name, size_t *vn) {
    size_t ln = strlen(name);
    for (size_t i = 0; i + ln + 1 < n; i++) {
        if ((i == 0 || p[i - 1] == '\n') && strncasecmp(p + i, name, ln) == 0
            && p[i + ln] == ':') {
            const char *v = p + i + ln + 1;
            while (v < p + n && *v == ' ') {
                v++;
            }
            const char *e = v;
            while (e < p + n && *e != '\r' && *e != '\n') {
                e++;
            }
            *vn = (size_t)(e - v);
            return v;
        }
    }
    return NULL;
}

static int query_int(const char *t, size_t tn, const char *name, int dflt) {
    const char *v;
    size_t vn;
    moy_http_query(t, tn, name, strlen(name), &v, &vn);
    int x = 0;
    for (size_t i = 0; i < vn && v[i] >= '0' && v[i] <= '9' && x < 1000; i++) {
        x = x * 10 + (v[i] - '0');
    }
    return vn && x > 0 && x < 1000 ? x : dflt;
}

// GET /term. 1 when the socket was handed to the bridge (the caller neither
// answers nor closes it), 0 when the request was answered here.
static int term(out_t *o, const moy_http_req_t *r) {
    if (moy_web_term == NULL) {
        simple(o, 404, "text/plain; charset=utf-8", "not found");
        return 0;
    }
    if (!signed_in(o, r->method, r->method_n, r->target, r->target_n, NULL, 0)) {
        return 0;
    }
    size_t kn, pn = 0;
    const char *key = header(W->req, r->head_end, "Sec-WebSocket-Key", &kn);
    if (key == NULL) {
        json(o, 200, "{\"term\":1}");
        return 0;
    }
    if (kn == 0 || kn > 64) {
        json(o, 400, "{\"error\":\"key\"}");
        return 0;
    }
    char cat[64 + 36];
    memcpy(cat, key, kn);
    memcpy(cat + kn, "258EAFA5-E914-47DA-95CA-C5AB0DC85B11", 36);
    uint8_t dg[20];
    sha1(cat, kn + 36, dg);
    char acc[32];
    size_t an = b64(dg, 20, acc);
    const char *proto = header(W->req, r->head_end, "Sec-WebSocket-Protocol", &pn);
    int sh = proto != NULL && pn >= 6 && strstr(proto, "moy.sh") != NULL
             && (size_t)(strstr(proto, "moy.sh") - proto) + 6 <= pn;
    char head[200];
    int hn = snprintf(head, sizeof(head),
                      "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: %.*s\r\n%s\r\n",
                      (int)an, acc, sh ? "Sec-WebSocket-Protocol: moy.sh\r\n" : "");
    if (moy_http_send(o->fd, head, (size_t)hn) < 0) {
        return 0;
    }
    int cols = query_int(r->target, r->target_n, "cols", 80);
    int rows = query_int(r->target, r->target_n, "rows", 24);
    if (moy_web_term(o->fd, cols, rows, o->seal) == 0) {
        moy_wa_term(o->seal);
        return 1;
    }
    // the bridge serves another page: a close it can read
    static const char busy[] = "\x88\x06\x03\xf5" "busy";
    moy_http_send(o->fd, busy, sizeof(busy) - 1);
    return 0;
}

// -- the poll -------------------------------------------------------------------------

static void serve(int fd) {
    W->requests++;
    size_t got = 0;
    moy_http_req_t r;
    if (moy_http_recv(fd, W->req, MOY_HTTP_REQ_MAX, &r, &got) != MOY_HTTP_OK) {
        moy_http_close(fd);
        return;
    }
    size_t bn = got > r.head_end ? got - r.head_end : 0;
    if (bn > r.clen) {
        bn = r.clen;
    }
    out_t o = {.fd = fd};
    const char *q = memchr(r.target, '?', r.target_n);
    size_t pn = q ? (size_t)(q - r.target) : r.target_n;
    if (!W->closing && path_is(r.method, r.method_n, "GET") && path_is(r.target, pn, "/term")) {
        W->cur = fd;
        int kept = term(&o, &r);
        W->cur = -1;
        if (!kept) {
            moy_http_close(fd);
        }
        return;
    }
    W->cur = fd;
    int done = route(&o, r.method, r.method_n, r.target, r.target_n, W->req + r.head_end, bn);
    W->cur = -1;
    if (done == ROUTE_DONE) {
        moy_http_close(fd);
        return;
    }
    if (done == ROUTE_LATER) {
        close_conn(&W->afd);
        W->afd = fd;
        return;
    }
    W->pfd = fd;
    W->preq = r;
    W->pbn = o.bn;
    W->pseal = o.sealed;
    W->taken = 0;
    W->parked_at = moy_net_ms();
}

// The PAKE's answer, once its worker is done, to the connection held for it.
static void later(void) {
    char doc[DOC_MAX];
    int st = moy_wa_later(doc, sizeof doc);
    if (st != 0 && W->afd >= 0) {
        out_t o = {.fd = W->afd};
        json(&o, st, doc);
        close_conn(&W->afd);
    }
}

int moy_web_poll(void) {
    if (W == NULL) {
        return 0;
    }
    later();
    if (W->closing
        && (uint32_t)(moy_net_ms() - W->closing_at) >= MOY_WEB_CLOSING_MS) {
        stop_now();
        return 0;
    }
    if (W->lfd < 0) {
        return 0;
    }
    if (W->pfd >= 0) {
        if ((uint32_t)(moy_net_ms() - W->parked_at) < MOY_WEB_DEFER_MS) {
            return 0;               // the request buffer is the parked one's
        }
        out_t o = {.fd = W->pfd, .seal = W->pseal.on ? &W->pseal : NULL};
        json(&o, 503, "{\"error\":\"busy\"}");
        close_conn(&W->pfd);
        W->taken = 0;
    }
    int did = 0;
    for (int i = 0; i < MOY_WEB_POLL_MAX && W->pfd < 0; i++) {
        int fd = moy_http_accept(W->lfd, &W->peer);
        if (fd < 0) {
            break;
        }
        did = 1;
        serve(fd);
    }
    return did;
}

void moy_web_abort(void) {
    if (W != NULL) {
        close_conn(&W->cur);
    }
}

int moy_web_take(const char **method, size_t *method_n, const char **target,
                 size_t *target_n, const char **body, size_t *body_n) {
    if (W == NULL || W->pfd < 0 || W->taken) {
        return 0;
    }
    moy_http_req_t *r = &W->preq;
    *method = r->method;
    *method_n = r->method_n;
    *target = r->target;
    *target_n = r->target_n;
    *body = W->req + r->head_end;
    *body_n = W->pbn;
    W->taken = 1;
    return 1;
}

// The VM's response, as it wrote it: sealed when the request was, its status
// kept and its body the records' plaintext.
static void answer_vm(out_t *o, const char *resp, size_t n) {
    if (o->seal == NULL) {
        raw(o, resp, n);
        return;
    }
    int status = 500;
    if (n > 12 && memcmp(resp, "HTTP/1.", 7) == 0) {
        status = atoi(resp + 9);
    }
    const char *end = resp + n, *b = resp;
    for (; b + 3 < end && memcmp(b, "\r\n\r\n", 4) != 0; b++) {
    }
    b = b + 3 < end ? b + 4 : end;
    answer(o, status, NULL, b, (size_t)(end - b));
}

void moy_web_answer(const void *resp, size_t n) {
    if (W == NULL || W->pfd < 0) {
        return;
    }
    out_t o = {.fd = W->pfd, .seal = W->pseal.on ? &W->pseal : NULL};
    answer_vm(&o, resp, n);
    close_conn(&W->pfd);
    moy_seal_wipe(&W->pseal, sizeof W->pseal);
    W->taken = 0;
}

int moy_web_handle(const char *method, size_t method_n, const char *target,
                   size_t target_n, char *b, size_t body_n, char **out,
                   size_t *out_n) {
    *out = NULL;
    *out_n = 0;
    if (W == NULL) {
        return 0;
    }
    out_t o = {.fd = -1};
    W->peer = 0;
    moy_seal_wipe(&W->hseal, sizeof W->hseal);
    int done = route(&o, method, method_n, target, target_n, b, body_n);
    if (done == ROUTE_PARKED) {
        moy_net_free(o.buf);
        W->hseal = o.sealed;
        *out_n = o.bn;
        return 0;
    }
    if (done == ROUTE_LATER) {
        char doc[DOC_MAX];
        int st;
        while ((st = moy_wa_later(doc, sizeof doc)) == 0) {
            moy_net_sleep_ms(1);
        }
        json(&o, st, doc);
        done = ROUTE_DONE;
    }
    *out = o.buf;
    *out_n = o.n;
    return done == ROUTE_DONE;
}

int moy_web_handle_answer(const char *resp, size_t n, char **out, size_t *out_n) {
    *out = NULL;
    *out_n = 0;
    if (W == NULL) {
        return MOY_ENOMEM;
    }
    out_t o = {.fd = -1, .seal = W->hseal.on ? &W->hseal : NULL};
    answer_vm(&o, resp, n);
    moy_seal_wipe(&W->hseal, sizeof W->hseal);
    *out = o.buf;
    *out_n = o.n;
    return o.err ? MOY_ENOMEM : 0;
}

int moy_web_pack(const char *root, const char *kinds, char **out,
                 size_t *out_n) {
    *out = NULL;
    *out_n = 0;
    if (!ensure()) {
        return MOY_ENOMEM;
    }
    out_t o = {.fd = -1};
    pack(&o, root, kinds);
    *out = o.buf;
    *out_n = o.n;
    return o.err ? MOY_ENOMEM : 0;
}
