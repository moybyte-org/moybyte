// moy_seal_web: the sign-in's browser end (the protocol is moy_seal.h's), the
// one page's client the worker drives (firmware/web_runner/worker.js, THE
// SIGN-IN): it moves bytes and HTTP; every MAC, key and seal is here. The
// host's tests drive the same functions against the board's end.
//
// Everything crosses through one buffer (moy_sw_buf, grown on demand; the
// worker re-reads its address after every call that may grow it): an answer's
// JSON or sealed records in, a request's JSON or sealed body out. A function
// that writes a request returns its length, or -1 for an answer that does not
// check (a proof, a confirmation, a seal).

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_json.h"
#include "moy_seal.h"

#ifdef __EMSCRIPTEN__
#include <emscripten.h>
#define KEEP EMSCRIPTEN_KEEPALIVE
#else
#define KEEP
#endif

#define NAME_MAX_BYTES 63
#define TARGET_MAX 1024

static struct {
    char *buf;
    size_t cap;
    moy_seal_hs_t hs;
    int pw;
    char salt[64];
    uint8_t kid[MOY_SEAL_KID], key[MOY_SEAL_KEY];
    uint8_t secret[MOY_SEAL_KEY];       // what wraps a new key: the token, or the PAKE's Ke
    int pairing;
    moy_seal_spake_t sp;
    uint8_t ca[MOY_SEAL_MAC];
    char name[NAME_MAX_BYTES + 1];
    uint8_t sid[MOY_SEAL_SID];
    int in;                             // signed in: sid and k are good
    moy_seal_keys_t k;
    uint64_t seq;
    char target[TARGET_MAX];
    uint64_t wseq;
    uint32_t up, down;
    char out[2 * MOY_SEAL_RAND + 1];
} C;

KEEP char *moy_sw_buf(size_t n) {
    if (n + 64 > C.cap) {
        size_t cap = n + 64 > 2 * C.cap ? n + 64 : 2 * C.cap;
        char *b = realloc(C.buf, cap);
        if (b == NULL) {
            return NULL;
        }
        C.buf = b;
        C.cap = cap;
    }
    return C.buf;
}

static int obj_of(size_t n, const char **o, const char **oe) {
    if (C.buf == NULL || n > C.cap || moy_json_valid(C.buf, n) != MOY_JSON_OK) {
        return 0;
    }
    *o = moy_json_ws(C.buf, C.buf + n);
    *oe = moy_json_value(*o, C.buf + n, 0);
    return *oe != NULL && moy_json_kind(*o, *oe) == MOY_JSON_OBJ;
}

static int hex_of(const char *o, const char *oe, const char *key, uint8_t *out, size_t want) {
    const char *v, *ve;
    if (!moy_json_get(o, oe, key, &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_STR) {
        return 0;
    }
    return moy_seal_unhex(v + 1, (size_t)(ve - v) - 2u, out, want);
}

// The hello's answer: the board's id and nonce, and this page's nonce.
KEEP int moy_sw_hello(size_t n) {
    const char *o, *oe, *v, *ve;
    moy_seal_wipe(&C.hs, sizeof C.hs);
    C.in = 0;
    C.pairing = 0;
    if (!obj_of(n, &o, &oe) || !hex_of(o, oe, "id", C.hs.id, MOY_SEAL_RAND)
        || !hex_of(o, oe, "nb", C.hs.nb, MOY_SEAL_RAND)) {
        return -1;
    }
    C.pw = moy_json_get(o, oe, "pw", &v, &ve) && moy_json_truthy(v, ve);
    C.salt[0] = '\0';
    if (moy_json_get(o, oe, "salt", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof C.salt) {
        C.salt[moy_json_str(v, ve, C.salt)] = '\0';
    }
    moy_seal_random(C.hs.nc, MOY_SEAL_RAND);
    return 0;
}

KEEP const char *moy_sw_board(void) {
    moy_seal_hex(C.hs.id, MOY_SEAL_RAND, C.out);
    return C.out;
}

KEEP int moy_sw_has_password(void) {
    return C.pw;
}

static int put(const char *fmt, const char *a, const char *b, const char *c, const char *d) {
    char nb[2 * MOY_SEAL_RAND + 1], nc[2 * MOY_SEAL_RAND + 1];
    moy_seal_hex(C.hs.nb, MOY_SEAL_RAND, nb);
    moy_seal_hex(C.hs.nc, MOY_SEAL_RAND, nc);
    size_t need = strlen(fmt) + 64 + strlen(a) + strlen(b) + strlen(c) + strlen(d);
    if (moy_sw_buf(need) == NULL) {
        return -1;
    }
    return snprintf(C.buf, C.cap, fmt, nb, nc, a, b, c, d);
}

static void set_name(const char *name) {
    size_t n = strlen(name);
    if (n > NAME_MAX_BYTES) {
        n = NAME_MAX_BYTES;
        while (n > 0 && ((unsigned char)name[n] & 0xC0) == 0x80) {
            n--;                        // whole UTF-8 sequences only
        }
    }
    memcpy(C.name, name, n);
    C.name[n] = '\0';
}

static const char *quoted(char *out, size_t cap) {
    size_t need = moy_json_quote(C.name, strlen(C.name), out, cap);
    if (need >= cap) {
        snprintf(out, cap, "\"a browser\"");
    } else {
        out[need] = '\0';
    }
    return out;
}

// A paired browser's sign-in: {"op":"key",...}.
KEEP int moy_sw_key(const char *kid_hex, const char *key_hex) {
    if (!moy_seal_unhex(kid_hex, strlen(kid_hex), C.kid, MOY_SEAL_KID)
        || !moy_seal_unhex(key_hex, strlen(key_hex), C.key, MOY_SEAL_KEY)) {
        return -1;
    }
    uint8_t mac[MOY_SEAL_MAC];
    char mh[2 * MOY_SEAL_MAC + 1], kh[2 * MOY_SEAL_KID + 1];
    moy_seal_key_mac(&C.hs, C.kid, C.key, mac);
    moy_seal_hex(mac, MOY_SEAL_MAC, mh);
    moy_seal_hex(C.kid, MOY_SEAL_KID, kh);
    C.pairing = 0;
    return put("{\"op\":\"key\",\"nb\":\"%s\",\"nc\":\"%s\",\"kid\":\"%s\",\"mac\":\"%s\"%s%s}",
               kh, mh, "", "");
}

// The QR's token pairs this browser: {"op":"token",...}.
KEEP int moy_sw_token(const char *token_hex, const char *name) {
    uint8_t t[MOY_SEAL_RAND], mac[MOY_SEAL_MAC];
    if (!moy_seal_unhex(token_hex, strlen(token_hex), t, MOY_SEAL_RAND)) {
        return -1;
    }
    set_name(name);
    moy_seal_token_mac(&C.hs, t, C.name, strlen(C.name), mac);
    memcpy(C.secret, t, MOY_SEAL_RAND);
    moy_seal_wipe(t, sizeof t);
    C.pairing = 1;
    char mh[2 * MOY_SEAL_MAC + 1], q[2 * NAME_MAX_BYTES * 6 + 8];
    moy_seal_hex(mac, MOY_SEAL_MAC, mh);
    return put("{\"op\":\"token\",\"nb\":\"%s\",\"nc\":\"%s\",\"mac\":\"%s\",\"name\":%s%s%s}",
               mh, quoted(q, sizeof q), "", "");
}

// The console's password pairs this browser: {"op":"pake",...} with X.
KEEP int moy_sw_pake(const char *password, const char *name) {
    uint8_t digest[32];
    moy_seal_pw_hash(password, strlen(password), C.salt, strlen(C.salt), digest);
    int r = moy_seal_spake_start(&C.sp, 0, digest);
    moy_seal_wipe(digest, sizeof digest);
    if (r != MOY_SEAL_OK) {
        return -1;
    }
    set_name(name);
    char x[2 * MOY_SEAL_POINT + 1], q[2 * NAME_MAX_BYTES * 6 + 8];
    moy_seal_hex(C.sp.mine, MOY_SEAL_POINT, x);
    return put("{\"op\":\"pake\",\"nb\":\"%s\",\"nc\":\"%s\",\"X\":\"%s\",\"name\":%s%s%s}", x,
               quoted(q, sizeof q), "", "");
}

// The board's {"Y","cB"}: its confirmation checked, ours {"op":"confirm"}.
KEEP int moy_sw_confirm(size_t n) {
    const char *o, *oe;
    uint8_t y[MOY_SEAL_POINT], cb[MOY_SEAL_MAC], want[MOY_SEAL_MAC];
    if (!obj_of(n, &o, &oe) || !hex_of(o, oe, "Y", y, MOY_SEAL_POINT)
        || !hex_of(o, oe, "cB", cb, MOY_SEAL_MAC)
        || moy_seal_spake_end(&C.sp, &C.hs, y, C.secret, C.ca, want) != MOY_SEAL_OK
        || !moy_seal_eq(cb, want, MOY_SEAL_MAC)) {
        moy_seal_wipe(&C.sp, sizeof C.sp);
        moy_seal_wipe(C.secret, sizeof C.secret);
        return -1;
    }
    moy_seal_wipe(&C.sp, sizeof C.sp);
    C.pairing = 1;
    char nb[2 * MOY_SEAL_RAND + 1], ca[2 * MOY_SEAL_MAC + 1];
    moy_seal_hex(C.hs.nb, MOY_SEAL_RAND, nb);
    moy_seal_hex(C.ca, MOY_SEAL_MAC, ca);
    moy_seal_wipe(C.ca, sizeof C.ca);
    if (moy_sw_buf(256) == NULL) {
        return -1;
    }
    return snprintf(C.buf, C.cap, "{\"op\":\"confirm\",\"nb\":\"%s\",\"cA\":\"%s\"}", nb, ca);
}

// The sign-in's answer {"sid","proof"} (a pairing's with "kid" and its
// wrapped "key"): the board's proof checked and the session's keys made.
KEEP int moy_sw_done(size_t n) {
    const char *o, *oe;
    uint8_t proof[MOY_SEAL_MAC], want[MOY_SEAL_MAC];
    int ok = obj_of(n, &o, &oe) && hex_of(o, oe, "sid", C.sid, MOY_SEAL_SID)
             && hex_of(o, oe, "proof", proof, MOY_SEAL_MAC);
    if (ok && C.pairing) {
        ok = hex_of(o, oe, "kid", C.kid, MOY_SEAL_KID) && hex_of(o, oe, "key", C.key, MOY_SEAL_KEY);
        if (ok) {
            moy_seal_wrap(&C.hs, C.secret, MOY_SEAL_KEY, C.key);
        }
    }
    moy_seal_wipe(C.secret, sizeof C.secret);
    C.pairing = 0;
    if (ok) {
        moy_seal_proof(&C.hs, C.kid, C.key, C.sid, want);
        ok = moy_seal_eq(proof, want, MOY_SEAL_MAC);
    }
    if (!ok) {
        C.in = 0;
        return -1;
    }
    moy_seal_session(&C.hs, C.kid, C.key, &C.k);
    C.seq = 0;
    C.in = 1;
    return 0;
}

KEEP const char *moy_sw_kid(void) {
    moy_seal_hex(C.kid, MOY_SEAL_KID, C.out);
    return C.out;
}

KEEP const char *moy_sw_device_key(void) {
    moy_seal_hex(C.key, MOY_SEAL_KEY, C.out);
    return C.out;
}

// A request sealed: the body (n bytes in the buffer) sealed in place with its
// tag after it, or for a GET the tag on the target. Its body's length, the
// target at moy_sw_target, its sequence number at moy_sw_seq.
KEEP int moy_sw_seal(const char *method, const char *path, size_t n) {
    if (!C.in) {
        return -1;
    }
    int get = strcmp(method, "GET") == 0;
    if (get && n != 0) {
        return -1;
    }
    if (moy_sw_buf(n + MOY_SEAL_TAG) == NULL) {
        return -1;
    }
    char sid[2 * MOY_SEAL_SID + 1];
    moy_seal_hex(C.sid, MOY_SEAL_SID, sid);
    C.seq++;
    int tn = snprintf(C.target, sizeof C.target, "%s%cs=%s&n=%llu", path,
                      strchr(path, '?') ? '&' : '?', sid, (unsigned long long)C.seq);
    if (tn < 0 || (size_t)tn + 3 + 2 * MOY_SEAL_TAG >= sizeof C.target) {
        return -1;
    }
    uint8_t tag[MOY_SEAL_TAG];
    if (moy_seal_req(1, C.k.kc, C.seq, method, strlen(method), C.target, (size_t)tn,
                     (uint8_t *)C.buf, n, tag) != MOY_SEAL_OK) {
        return -1;
    }
    if (get) {
        memcpy(C.target + tn, "&t=", 3);
        moy_seal_hex(tag, MOY_SEAL_TAG, C.target + tn + 3);
        return 0;
    }
    memcpy(C.buf + n, tag, MOY_SEAL_TAG);
    return (int)(n + MOY_SEAL_TAG);
}

KEEP const char *moy_sw_target(void) {
    return C.target;
}

KEEP double moy_sw_seq(void) {
    return (double)C.seq;
}

// A sealed answer's records (n bytes in the buffer) to request `seq` with
// HTTP status `status`: the plaintext at the buffer's start, its length; -1
// for a record that does not open, one missing, or anything past the last.
KEEP int moy_sw_open(double seq, int status, size_t n) {
    if (!C.in || C.buf == NULL || n > C.cap) {
        return -1;
    }
    uint8_t *b = (uint8_t *)C.buf;
    size_t at = 0, out = 0;
    for (uint32_t i = 0;; i++) {
        if (n - at < MOY_SEAL_HEAD + MOY_SEAL_TAG) {
            return -1;
        }
        uint32_t h = (uint32_t)b[at] << 24 | (uint32_t)b[at + 1] << 16
                     | (uint32_t)b[at + 2] << 8 | b[at + 3];
        int last = (h & MOY_SEAL_LAST) != 0;
        size_t len = h & ~MOY_SEAL_LAST;
        if (len > n - at - MOY_SEAL_HEAD - MOY_SEAL_TAG) {
            return -1;
        }
        uint8_t head[MOY_SEAL_HEAD];
        memcpy(head, b + at, MOY_SEAL_HEAD);
        uint8_t *p = b + at + MOY_SEAL_HEAD;
        if (moy_seal_rec(0, C.k.kb, (uint64_t)seq, i, status, last, head, p, len, p + len)
            != MOY_SEAL_OK) {
            return -1;
        }
        memmove(b + out, p, len);
        out += len;
        at += MOY_SEAL_HEAD + len + MOY_SEAL_TAG;
        if (last) {
            return at == n ? (int)out : -1;
        }
    }
}

// The /term upgrade's stream: its frames each way from 0 under request `seq`.
KEEP void moy_sw_ws(double seq) {
    C.wseq = (uint64_t)seq;
    C.up = C.down = 0;
}

// A frame up: the plaintext (its kind's byte first) sealed in place, its
// length with the tag.
KEEP int moy_sw_ws_up(size_t n) {
    if (!C.in || moy_sw_buf(n + MOY_SEAL_TAG) == NULL
        || moy_seal_ws(1, C.k.kc, MOY_SEAL_UP, C.up++, C.wseq, (uint8_t *)C.buf, n,
                       (uint8_t *)C.buf + n) != MOY_SEAL_OK) {
        return -1;
    }
    return (int)(n + MOY_SEAL_TAG);
}

// A frame down opened in place: its plaintext's length, or -1.
KEEP int moy_sw_ws_down(size_t n) {
    if (!C.in || C.buf == NULL || n < MOY_SEAL_TAG || n > C.cap
        || moy_seal_ws(0, C.k.kb, MOY_SEAL_DOWN, C.down++, C.wseq, (uint8_t *)C.buf,
                       n - MOY_SEAL_TAG, (uint8_t *)C.buf + n - MOY_SEAL_TAG) != MOY_SEAL_OK) {
        return -1;
    }
    return (int)(n - MOY_SEAL_TAG);
}

// Signed out: the session and the key forgotten.
KEEP void moy_sw_reset(void) {
    char *b = C.buf;
    size_t cap = C.cap;
    moy_seal_wipe(&C, sizeof C);
    C.buf = b;
    C.cap = cap;
}
