// The `http` device API by handle (native/moy_kernel/moy_dev.h's http row): a
// GET for a compiled program, on its own task, over this module's client
// (moy_fetch.h), with the process's "http" hold on the radio's book
// (moy_lease.h's THE RADIO) for the handle's life.

#include <string.h>

#include "moy_dev.h"
#include "moy_dev_kinds.h"
#include "moy_fetch.h"
#include "moy_lease.h"

#define TAG "http"
#define TAG_N 4u
#define CAP_DEFAULT 16384u

typedef struct {
    uint32_t pid;               // 0: free
    uint32_t req;               // the client's request
    uint32_t at;                // a kept body's next byte
    uint8_t stream;
} http_t;

static http_t s_http[MOY_DEV_HANDLES];

// The process's "http" hold let go with its last handle.
static void settle(uint32_t pid) {
    for (uint32_t i = 0; i < MOY_DEV_HANDLES; i++) {
        if (s_http[i].pid == pid) {
            return;
        }
    }
    uint32_t mask;
    moy_lease_wifi_release(pid, TAG, TAG_N, &mask);
}

static int32_t http_open(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg,
                         size_t cn) {
    (void)perms;
    char url[MOY_FETCH_URL_MAX];
    bool has_url = false, has_cap = false, has_window = false;
    int64_t cap = CAP_DEFAULT, window = 0;
    words_t it = { cfg, cfg + cn };
    word_t w;
    int got;
    while ((got = moy_dev_word(&it, &w)) > 0) {
        bool ok;
        if (moy_dev_is(w.k, w.kn, "url")) {
            ok = w.vn > 0 && w.vn < sizeof url;
            if (ok) {
                memcpy(url, w.v, w.vn);
                url[w.vn] = '\0';
                has_url = true;
            }
        } else if (moy_dev_is(w.k, w.kn, "cap")) {
            ok = moy_dev_in_range(&w, 1, MOY_FETCH_CAP_MAX, &cap);
            has_cap = true;
        } else if (moy_dev_is(w.k, w.kn, "window")) {
            ok = moy_dev_in_range(&w, 1, MOY_FETCH_CAP_MAX, &window);
            has_window = true;
        } else {
            ok = false;
        }
        if (!ok) {
            return MOY_DEV_EBAD;
        }
    }
    if (got < 0 || !has_url || (has_cap && has_window)) {
        return MOY_DEV_EBAD;            // a url, and a body kept or windowed
    }
    http_t *r = &s_http[slot];
    *r = (http_t){ .stream = has_window };
    uint32_t mask;
    int32_t rc = moy_lease_wifi_hold(pid, TAG, TAG_N, &mask);
    if (rc != MOY_LEASE_OK) {
        return rc == MOY_LEASE_FULL ? MOY_DEV_EFULL : MOY_DEV_ENOMEM;
    }
    rc = moy_dev_errno(has_window ? moy_fetch_stream(pid, url, (uint32_t)window, &r->req)
                                  : moy_fetch_open(pid, url, (uint32_t)cap, &r->req));
    if (rc < 0) {
        settle(pid);
        return rc;
    }
    r->pid = pid;
    return 0;
}

static void http_close(uint32_t slot, uint32_t pid) {
    http_t *r = &s_http[slot];
    moy_fetch_close(r->req);
    *r = (http_t){ 0 };
    settle(pid);
}

// The request's state: < 0 for no response (FAILED, *err its errno), 0 while
// it runs, 1 with a response (a stream's head in, or a kept body all come).
static int answer(const http_t *r, int *status, uint32_t *n, int *err) {
    int st = moy_fetch_state(r->req, status, n, err);
    if (st < 0 || st == MOY_FETCH_FAILED) {
        if (st < 0) {
            *err = -st;
        }
        return -1;
    }
    return st == MOY_FETCH_DONE || (r->stream && st == MOY_FETCH_STREAM) ? 1 : 0;
}

static int32_t http_read(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap) {
    (void)pid;
    http_t *r = &s_http[slot];
    int status, err;
    uint32_t n;
    if (cap == 0 || answer(r, &status, &n, &err) <= 0) {
        return 0;
    }
    uint32_t c = cap > INT32_MAX ? (uint32_t)INT32_MAX : (uint32_t)cap;
    int k = r->stream ? moy_fetch_take(r->req, out, c) : moy_fetch_read(r->req, r->at, out, c);
    if (k < 0) {
        return 0;
    }
    if (!r->stream) {
        r->at += (uint32_t)k;
    }
    return k;
}

static int32_t http_get(uint32_t slot, uint32_t pid, const char *what, size_t n) {
    (void)pid;
    const http_t *r = &s_http[slot];
    int status = 0, err = 0;
    uint32_t len = 0;
    int a = answer(r, &status, &len, &err);
    if (moy_dev_is(what, n, "status")) {
        return a < 0 ? MOY_DEV_EIO : a == 0 ? 0 : status;
    }
    if (moy_dev_is(what, n, "errno")) {
        return a < 0 ? err : 0;
    }
    if (moy_dev_is(what, n, "length")) {
        return a > 0 && !r->stream ? (int32_t)len : 0;
    }
    if (moy_dev_is(what, n, "ended")) {
        int st = moy_fetch_state(r->req, &status, &len, &err);
        return r->stream && st == MOY_FETCH_DONE;
    }
    return MOY_DEV_EBAD;
}

const moy_dev_ops_t moy_dev_http = {
    .open = http_open, .close = http_close, .read = http_read, .get = http_get,
};
