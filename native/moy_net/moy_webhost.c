// The webhost (moy_net.h): the console served from the board.
//
//   GET  /, /index.html, the bundle's assets   open: the page must load to ask for a pin
//   GET  /sync                                 open: {"sync":1}, "a board lives here"
//   GET  /carts.json, /files.json              pin (?pin=): the store, streamed
//   POST /sync                                 pin (the body's): a batch applied
//   anything in the configured `defer`         parked for the VM (/run, /update, /gpio)
//
// THE PIN GATES EVERYTHING that reveals or changes what is on the board (owner
// call 2026-08-25): a GET carries it as `?pin=`, a POST /sync in its body. A
// refusal is 403 {"error":"pin"}, which the page tells apart from a 404 and
// answers with a prompt. A host with no pin is open end to end (a test, the dev
// server).
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
// THE GOODBYE: a stop with a reason answers every request, ahead of the pin,
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
#include <time.h>

#include "moy_fs.h"
#include "moy_json.h"
#include "moy_net.h"
#include "moy_vol.h"

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

#define CHUNK_MIN 8192
#define CHUNK_HEAD 8
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

static const char PIN_REFUSED[] = "{\"error\":\"pin\"}";

typedef struct {
    int lfd;                        // the listener, -1 when none
    int cur;                        // the connection being served, -1 when none
    int pfd;                        // the parked connection, -1 when none
    uint8_t closing, taken;
    uint16_t port;
    uint32_t closing_at, parked_at, requests, events;
    int err;
    int64_t epoch;
    char why[32];
    char pin[16];
    char carts[MOY_WEB_PATH_MAX];
    char files[MOY_WEB_PATH_MAX];
    uint8_t has_files;
    char kinds[96];
    char defer[96];
    moy_http_req_t preq;            // the parked request, its spans in req
    size_t pgot;
    char *req;                      // MOY_HTTP_REQ_MAX + 1 bytes
    char *chunk;                    // CHUNK_HEAD + CHUNK_MIN + 2
    char *io;                       // READ_CHUNK + 4 (a UTF-8 carry)
    char *b64;                      // BIN_CHUNK / 3 * 4
} web_t;

static web_t *W;

typedef struct {
    int fd;                         // -1: into buf
    char *buf;
    size_t n, cap;
    int err;
    int chunked;
    size_t cn;                      // bytes waiting in W->chunk
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
    w->chunk = moy_net_alloc(CHUNK_HEAD + CHUNK_MIN + 2u);
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
    w->lfd = w->cur = w->pfd = -1;
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
    copy_str(W->pin, sizeof(W->pin), cfg->pin);
    W->epoch = cfg->epoch;
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
    close_conn(&W->lfd);
    close_conn(&W->pfd);
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

void moy_web_set_pin(const char *pin) {
    if (ensure()) {
        copy_str(W->pin, sizeof(W->pin), pin);
    }
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
    uint32_t e = W->events;
    W->events = 0;
    return e;
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

static void frame(out_t *o) {
    char head[CHUNK_HEAD + 1];
    int k = snprintf(head, sizeof(head), "%x\r\n", (unsigned)o->cn);
    char *c = W->chunk + CHUNK_HEAD - k;
    memcpy(c, head, (size_t)k);
    W->chunk[CHUNK_HEAD + o->cn] = '\r';
    W->chunk[CHUNK_HEAD + o->cn + 1u] = '\n';
    raw(o, c, (size_t)k + o->cn + 2u);
    o->cn = 0;
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
        memcpy(W->chunk + CHUNK_HEAD + o->cn, p, take);
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

static void simple(out_t *o, int status, const char *ctype, const char *text) {
    char head[256];
    size_t n = strlen(text);
    size_t k = moy_http_head(head, sizeof(head), status, ctype, n);
    raw(o, head, k < sizeof(head) ? k : sizeof(head) - 1u);
    raw(o, text, n);
}

static void json(out_t *o, int status, const char *text) {
    simple(o, status, "application/json", text);
}

static void chunked_head(out_t *o) {
    rawz(o, "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Transfer-Encoding: chunked\r\n"
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

static int is_binary(const char *name) {
    return strcmp(name, "cover.png") == 0;
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

static int in_list(const char *list, const char *s, size_t n) {
    for (const char *k = list; *k; k += strlen(k) + 1u) {
        if (strlen(k) == n && memcmp(k, s, n) == 0) {
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

static int pin_ok_query(const char *target, size_t n) {
    if (W->pin[0] == '\0') {
        return 1;
    }
    const char *v;
    size_t vn;
    moy_http_query(target, n, "pin", 3, &v, &vn);
    return vn == strlen(W->pin) && memcmp(v, W->pin, vn) == 0;
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

static void sync_post(out_t *o, const char *b, size_t n) {
    moy_sync_batch_t batch;
    if (moy_sync_batch(b, n, &batch) != MOY_SYNC_OK) {
        json(o, 400, "{\"error\":\"bad batch\"}");
        return;
    }
    if (!moy_sync_pin_ok(&batch, W->pin)) {
        json(o, 403, PIN_REFUSED);
        return;
    }
    if (batch.root == MOY_SYNC_FILES && !W->has_files) {
        json(o, 400, "{\"error\":\"no such store\"}");
        return;
    }
    moy_sync_store_t s = {
        .carts = W->carts,
        .files = W->has_files ? W->files : NULL,
        .kinds = W->kinds[0] ? W->kinds : NULL,
        .journal = 1,
        .ts = (int64_t)time(NULL) - W->epoch,
    };
    moy_sync_result_t r;
    store_gate();
    moy_sync_apply(&s, batch.root, batch.ops, batch.ops_end, &r);
    if (r.shelf && batch.root == MOY_SYNC_CARTS) {
        W->events |= MOY_WEB_EV_SHELF;
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
}

static void pull(out_t *o, const char *root, const char *kinds) {
    store_gate();
    chunked_head(o);
    pack(o, root, kinds);
    chunked_end(o);
}

// 1 when answered, 0 when the VM answers it.
static int route(out_t *o, const char *m, size_t mn, const char *t, size_t tn,
                 const char *b, size_t bn) {
    if (W->closing) {
        char doc[80];
        snprintf(doc, sizeof(doc), "{\"error\":\"closing\",\"why\":\"%s\"}",
                 W->why);
        json(o, 503, doc);
        return 1;
    }
    const char *q = memchr(t, '?', tn);
    size_t pn = q ? (size_t)(q - t) : tn;
    int post = path_is(m, mn, "POST");
    if ((post || path_is(m, mn, "GET")) && in_list(W->defer, t, pn)) {
        return 0;
    }
    if (post && path_is(t, pn, "/sync")) {
        sync_post(o, b, bn);
        return 1;
    }
    if (!path_is(m, mn, "GET")) {
        json(o, 405, "{\"error\":\"GET, or POST /sync /run /update\"}");
        return 1;
    }
    if (path_is(t, pn, "/sync")) {
        json(o, 200, "{\"sync\":1}");
        return 1;
    }
    if (path_is(t, pn, "/") || path_is(t, pn, "/index.html")) {
        asset(o, "index.html", ASSETS[0].ctype);
        return 1;
    }
    for (size_t i = 0; i < sizeof(ASSETS) / sizeof(ASSETS[0]); i++) {
        if (pn > 1 && path_is(t + 1, pn - 1, ASSETS[i].name)) {
            asset(o, ASSETS[i].name, ASSETS[i].ctype);
            return 1;
        }
    }
    int carts = path_is(t, pn, "/carts.json");
    if (carts || (path_is(t, pn, "/files.json") && W->has_files)) {
        if (!pin_ok_query(t, tn)) {
            json(o, 403, PIN_REFUSED);
        } else if (carts) {
            pull(o, W->carts, NULL);
        } else {
            pull(o, W->files, W->kinds);
        }
        return 1;
    }
    simple(o, 404, "text/plain; charset=utf-8", "not found");
    return 1;
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
    W->cur = fd;
    if (route(&o, r.method, r.method_n, r.target, r.target_n,
              W->req + r.head_end, bn)) {
        W->cur = -1;
        moy_http_close(fd);
        return;
    }
    W->cur = -1;
    W->pfd = fd;
    W->preq = r;
    W->pgot = got;
    W->taken = 0;
    W->parked_at = moy_net_ms();
}

int moy_web_poll(void) {
    if (W == NULL) {
        return 0;
    }
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
        out_t o = {.fd = W->pfd};
        json(&o, 503, "{\"error\":\"busy\"}");
        close_conn(&W->pfd);
        W->taken = 0;
    }
    int did = 0;
    for (int i = 0; i < MOY_WEB_POLL_MAX && W->pfd < 0; i++) {
        int fd = moy_http_accept(W->lfd);
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
    size_t bn = W->pgot > r->head_end ? W->pgot - r->head_end : 0;
    *method = r->method;
    *method_n = r->method_n;
    *target = r->target;
    *target_n = r->target_n;
    *body = W->req + r->head_end;
    *body_n = bn < r->clen ? bn : r->clen;
    W->taken = 1;
    return 1;
}

void moy_web_answer(const void *resp, size_t n) {
    if (W == NULL || W->pfd < 0) {
        return;
    }
    out_t o = {.fd = W->pfd};
    raw(&o, resp, n);
    close_conn(&W->pfd);
    W->taken = 0;
}

int moy_web_handle(const char *method, size_t method_n, const char *target,
                   size_t target_n, const char *b, size_t body_n, char **out,
                   size_t *out_n) {
    *out = NULL;
    *out_n = 0;
    if (W == NULL) {
        return 0;
    }
    out_t o = {.fd = -1};
    int done = route(&o, method, method_n, target, target_n, b, body_n);
    *out = o.buf;
    *out_n = o.n;
    return done;
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
