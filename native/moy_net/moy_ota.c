// The updater and its client (moy_ota.h). Pure C over the platform's
// connection and slot (moy_net_port.c): no board test in this file.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_json.h"
#include "moy_net.h"
#include "moy_ota.h"

// -- a URL and a response head ----------------------------------------------------------

int moy_url_parse(const char *url, int *tls, char *host, size_t host_cap,
                  uint16_t *port, const char **path) {
    const char *p;
    if (strncmp(url, "https://", 8) == 0) {
        *tls = 1;
        *port = 443;
        p = url + 8;
    } else if (strncmp(url, "http://", 7) == 0) {
        *tls = 0;
        *port = 80;
        p = url + 7;
    } else {
        return -1;
    }
    const char *slash = strchr(p, '/');
    const char *end = slash != NULL ? slash : p + strlen(p);
    const char *colon = memchr(p, ':', (size_t)(end - p));
    const char *hend = colon != NULL ? colon : end;
    size_t hn = (size_t)(hend - p);
    if (hn == 0 || hn >= host_cap) {
        return -1;
    }
    memcpy(host, p, hn);
    host[hn] = '\0';
    if (colon != NULL) {
        uint32_t v = 0;
        const char *d = colon + 1;
        if (d == end) {
            return -1;
        }
        for (; d < end; d++) {
            if (*d < '0' || *d > '9') {
                return -1;
            }
            v = v * 10u + (uint32_t)(*d - '0');
            if (v > 65535u) {
                return -1;
            }
        }
        if (v == 0) {
            return -1;
        }
        *port = (uint16_t)v;
    }
    *path = slash != NULL ? slash : "/";
    return 0;
}

static int lower(int c) {
    return c >= 'A' && c <= 'Z' ? c + 32 : c;
}

static int name_is(const char *s, size_t n, const char *want) {
    size_t w = strlen(want);
    if (n != w) {
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        if (lower((unsigned char)s[i]) != want[i]) {
            return 0;
        }
    }
    return 1;
}

static int is_ws(char c) {
    return c == ' ' || c == '\t';
}

int moy_http_resp_parse(const char *buf, size_t n, moy_http_resp_t *r) {
    memset(r, 0, sizeof(*r));
    size_t end = 0;
    for (size_t i = 3; i < n; i++) {
        if (buf[i - 3] == '\r' && buf[i - 2] == '\n' && buf[i - 1] == '\r' && buf[i] == '\n') {
            end = i + 1u;
            break;
        }
    }
    if (end == 0) {
        return MOY_HTTP_PARTIAL;
    }
    r->head_end = end;
    const char *p = buf, *stop = buf + end - 2u;
    int first = 1;
    while (p < stop) {
        const char *eol = p;
        while (eol + 1 < stop + 2 && !(eol[0] == '\r' && eol[1] == '\n')) {
            eol++;
        }
        if (first) {
            first = 0;
            const char *sp = memchr(p, ' ', (size_t)(eol - p));
            if (sp != NULL) {
                int v = 0, digits = 0;
                for (const char *d = sp + 1; d < eol && *d >= '0' && *d <= '9' && digits < 4; d++) {
                    v = v * 10 + (*d - '0');
                    digits++;
                }
                r->status = digits == 3 ? v : 0;
            }
        } else {
            const char *colon = memchr(p, ':', (size_t)(eol - p));
            if (colon != NULL) {
                const char *v = colon + 1, *ve = eol;
                while (v < ve && is_ws(*v)) {
                    v++;
                }
                while (ve > v && is_ws(ve[-1])) {
                    ve--;
                }
                if (name_is(p, (size_t)(colon - p), "content-length")) {
                    uint64_t x = 0;
                    const char *d = v;
                    for (; d < ve && *d >= '0' && *d <= '9'; d++) {
                        x = x * 10u + (uint64_t)(*d - '0');
                        if (x > 0xffffffffu) {
                            break;
                        }
                    }
                    r->clen = (d == ve && d > v && x <= 0xffffffffu) ? (uint32_t)x : 0;
                } else if (name_is(p, (size_t)(colon - p), "location")) {
                    r->loc = v;
                    r->loc_n = (size_t)(ve - v);
                }
            }
        }
        p = eol + 2;
    }
    return MOY_HTTP_OK;
}

// -- SHA-256 (FIPS 180-4) ------------------------------------------------------------------

static const uint32_t K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha_block(uint32_t h[8], const uint8_t *b) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16
               | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = hh + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
        uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
        hh = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = bb;
        bb = a;
        a = t1 + t2;
    }
    h[0] += a;
    h[1] += bb;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    h[5] += f;
    h[6] += g;
    h[7] += hh;
}

void moy_sha256_init(moy_sha256_t *s) {
    static const uint32_t iv[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    };
    memcpy(s->h, iv, sizeof(iv));
    s->len = 0;
    s->fill = 0;
}

void moy_sha256_update(moy_sha256_t *s, const void *p, size_t n) {
    const uint8_t *b = p;
    s->len += n;
    if (s->fill) {
        size_t take = 64u - s->fill < n ? 64u - s->fill : n;
        memcpy(s->buf + s->fill, b, take);
        s->fill += (uint32_t)take;
        b += take;
        n -= take;
        if (s->fill < 64u) {
            return;
        }
        sha_block(s->h, s->buf);
        s->fill = 0;
    }
    while (n >= 64u) {
        sha_block(s->h, b);
        b += 64;
        n -= 64u;
    }
    memcpy(s->buf, b, n);
    s->fill = (uint32_t)n;
}

void moy_sha256_final(moy_sha256_t *s, uint8_t out[32]) {
    uint64_t bits = s->len * 8u;
    uint8_t pad = 0x80;
    moy_sha256_update(s, &pad, 1);
    pad = 0;
    while (s->fill != 56u) {
        moy_sha256_update(s, &pad, 1);
    }
    uint8_t lb[8];
    for (int i = 0; i < 8; i++) {
        lb[i] = (uint8_t)(bits >> (56 - 8 * i));
    }
    moy_sha256_update(s, lb, 8);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(s->h[i] >> 24);
        out[4 * i + 1] = (uint8_t)(s->h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(s->h[i] >> 8);
        out[4 * i + 3] = (uint8_t)s->h[i];
    }
}

// -- the keys and the signature -------------------------------------------------------------

// device/moy_ota.py's OTA_PUBLIC_KEYS, exponent 65537 (tests/test_ota_signing.py
// holds the two to each other).
static const char *const KEYS_HEX[] = {
    "cde3f291071ec24c5c24af208757caf7d06a7f70a42c35435586d3a4d6b20c70"
    "e0f5dadb9b4405eae83e1d86f1410b730d8f59dba0eba47159e6ac60b91c13e9"
    "83da56f5867f8540242bcdb0b9f5c2b9b5bafd1959dddefe7cf42ec75ad92140"
    "fb18eaee715e22eb80754b45f3d4848ed06e8d8d49652da0c3239afced318c69"
    "50b6e55639970340353f32354d4f2537486c89f8129a0553c0c18391be95f73e"
    "e30c0c98decf20ad04abd7c7b74b68bc102502bf9b98f07d22b8fe459ebf2580"
    "2abf721b362b96000eeb8056e8308d45d1d5346cec1434992af3c80abce02366"
    "1aaddd9580585a52a27906314d5d0f71177487a9089e63cf77a79b40b328ec19",
};
#define NKEYS ((int)(sizeof(KEYS_HEX) / sizeof(KEYS_HEX[0])))

static int hexval(int c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    c = lower(c);
    return c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

// `n` hex digits, right-aligned into `out` (`cap` bytes, big-endian): 0, or -1
// for a digit that is not one or a number wider than `cap`.
static int hex_to(const char *s, size_t n, uint8_t *out, size_t cap) {
    while (n > 0 && *s == '0') {
        s++;
        n--;
    }
    if ((n + 1u) / 2u > cap) {
        return -1;
    }
    memset(out, 0, cap);
    for (size_t i = 0; i < n; i++) {
        int v = hexval((unsigned char)s[n - 1u - i]);
        if (v < 0) {
            return -1;
        }
        out[cap - 1u - i / 2u] |= (uint8_t)(i & 1u ? v << 4 : v);
    }
    return 0;
}

int moy_ota_key_hex(const char *hex, size_t n, moy_ota_key_t *out) {
    if (hex_to(hex, n, out->n, MOY_OTA_KEY_BYTES) != 0 || (out->n[MOY_OTA_KEY_BYTES - 1] & 1u) == 0
        || out->n[0] == 0) {
        return -1;
    }
    return 0;
}

int moy_ota_keys(void) {
    return NKEYS;
}

const moy_ota_key_t *moy_ota_key(int i) {
    static moy_ota_key_t *keys;             // kernel memory, decoded once
    if (i < 0 || i >= NKEYS) {
        return NULL;
    }
    if (keys == NULL) {
        keys = moy_net_alloc(sizeof(moy_ota_key_t) * NKEYS);
        if (keys == NULL) {
            return NULL;
        }
        for (int k = 0; k < NKEYS; k++) {
            hex_to(KEYS_HEX[k], strlen(KEYS_HEX[k]), keys[k].n, MOY_OTA_KEY_BYTES);
        }
    }
    return &keys[i];
}

#define LIMBS (MOY_OTA_KEY_BYTES / 4)

typedef struct {
    uint32_t n[LIMBS], r2[LIMBS], a[LIMBS], x[LIMBS], t[LIMBS + 2];
    uint32_t n0;
} mont_t;

static void limbs_of(uint32_t *l, const uint8_t *be) {
    for (int i = 0; i < LIMBS; i++) {
        const uint8_t *p = be + MOY_OTA_KEY_BYTES - 4 * (i + 1);
        l[i] = (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
    }
}

static int cmp_limbs(const uint32_t *a, const uint32_t *b) {
    for (int i = LIMBS - 1; i >= 0; i--) {
        if (a[i] != b[i]) {
            return a[i] < b[i] ? -1 : 1;
        }
    }
    return 0;
}

static void sub_limbs(uint32_t *a, const uint32_t *b) {
    uint64_t borrow = 0;
    for (int i = 0; i < LIMBS; i++) {
        uint64_t d = (uint64_t)a[i] - b[i] - borrow;
        a[i] = (uint32_t)d;
        borrow = (d >> 63) & 1u;
    }
}

// out = a * b / R mod n (CIOS); out may alias a or b.
static void mont_mul(mont_t *m, uint32_t *out, const uint32_t *a, const uint32_t *b) {
    uint32_t *t = m->t;
    memset(t, 0, sizeof(m->t));
    for (int i = 0; i < LIMBS; i++) {
        uint64_t c = 0;
        for (int j = 0; j < LIMBS; j++) {
            uint64_t s = (uint64_t)a[j] * b[i] + t[j] + c;
            t[j] = (uint32_t)s;
            c = s >> 32;
        }
        uint64_t s = (uint64_t)t[LIMBS] + c;
        t[LIMBS] = (uint32_t)s;
        t[LIMBS + 1] = (uint32_t)(s >> 32);
        uint32_t u = t[0] * m->n0;
        c = ((uint64_t)u * m->n[0] + t[0]) >> 32;
        for (int j = 1; j < LIMBS; j++) {
            s = (uint64_t)u * m->n[j] + t[j] + c;
            t[j - 1] = (uint32_t)s;
            c = s >> 32;
        }
        s = (uint64_t)t[LIMBS] + c;
        t[LIMBS - 1] = (uint32_t)s;
        t[LIMBS] = t[LIMBS + 1] + (uint32_t)(s >> 32);
    }
    if (t[LIMBS] != 0 || cmp_limbs(t, m->n) >= 0) {
        sub_limbs(t, m->n);
    }
    memcpy(out, t, LIMBS * 4);
}

// sig^65537 mod n into `out` (big-endian): 0, or -1 when sig >= n or n is even.
static int rsa_public(const uint8_t *n_be, const uint8_t *sig_be, uint8_t *out) {
    mont_t *m = moy_net_alloc(sizeof(mont_t));
    if (m == NULL) {
        return -1;
    }
    int rc = -1;
    limbs_of(m->n, n_be);
    limbs_of(m->a, sig_be);
    if ((m->n[0] & 1u) == 0 || cmp_limbs(m->a, m->n) >= 0) {
        goto done;
    }
    uint32_t inv = 1;                       // n^-1 mod 2^32 by Newton
    for (int i = 0; i < 5; i++) {
        inv *= 2u - m->n[0] * inv;
    }
    m->n0 = (uint32_t)(0u - inv);
    // R^2 mod n: 1 doubled 2 * 32 * LIMBS times.
    memset(m->r2, 0, sizeof(m->r2));
    m->r2[0] = 1;
    for (int i = 0; i < 2 * 32 * LIMBS; i++) {
        uint32_t carry = m->r2[LIMBS - 1] >> 31;
        for (int j = LIMBS - 1; j > 0; j--) {
            m->r2[j] = m->r2[j] << 1 | m->r2[j - 1] >> 31;
        }
        m->r2[0] <<= 1;
        if (carry || cmp_limbs(m->r2, m->n) >= 0) {
            sub_limbs(m->r2, m->n);
        }
    }
    mont_mul(m, m->a, m->a, m->r2);         // sig in Montgomery form
    memcpy(m->x, m->a, sizeof(m->x));
    for (int i = 0; i < 16; i++) {
        mont_mul(m, m->x, m->x, m->x);
    }
    mont_mul(m, m->x, m->x, m->a);          // sig^65537, Montgomery form
    memset(m->r2, 0, sizeof(m->r2));
    m->r2[0] = 1;
    mont_mul(m, m->x, m->x, m->r2);         // out of it
    for (int i = 0; i < LIMBS; i++) {
        uint8_t *p = out + MOY_OTA_KEY_BYTES - 4 * (i + 1);
        p[0] = (uint8_t)(m->x[i] >> 24);
        p[1] = (uint8_t)(m->x[i] >> 16);
        p[2] = (uint8_t)(m->x[i] >> 8);
        p[3] = (uint8_t)m->x[i];
    }
    rc = 0;
done:
    moy_net_free(m);
    return rc;
}

static const uint8_t SHA256_DER[19] = {
    0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65,
    0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20,
};

int moy_ota_verify(const void *payload, size_t n, const char *sig_hex,
                   size_t sig_n, const moy_ota_key_t *keys, int nkeys) {
    if (keys == NULL) {
        nkeys = moy_ota_keys();
    }
    if (sig_hex == NULL || sig_n == 0 || nkeys <= 0) {
        return 0;
    }
    uint8_t *b = moy_net_alloc(3 * MOY_OTA_KEY_BYTES);
    if (b == NULL) {
        return 0;
    }
    uint8_t *sig = b, *want = b + MOY_OTA_KEY_BYTES, *got = b + 2 * MOY_OTA_KEY_BYTES;
    int ok = 0;
    if (hex_to(sig_hex, sig_n, sig, MOY_OTA_KEY_BYTES) == 0) {
        moy_sha256_t h;
        moy_sha256_init(&h);
        moy_sha256_update(&h, payload, n);
        size_t k = MOY_OTA_KEY_BYTES, tail = sizeof(SHA256_DER) + 32u;
        want[0] = 0x00;
        want[1] = 0x01;
        memset(want + 2, 0xff, k - tail - 3u);
        want[k - tail - 1u] = 0x00;
        memcpy(want + k - tail, SHA256_DER, sizeof(SHA256_DER));
        moy_sha256_final(&h, want + k - 32u);
        for (int i = 0; i < nkeys && !ok; i++) {
            const moy_ota_key_t *key = keys != NULL ? &keys[i] : moy_ota_key(i);
            if (key != NULL && rsa_public(key->n, sig, got) == 0 && memcmp(got, want, k) == 0) {
                ok = 1;
            }
        }
    }
    moy_net_free(b);
    return ok;
}

// -- the manifest ---------------------------------------------------------------------------

// The object's `key` as Python's `m.get(key) or ""` formats under "%s": a
// string's text (lowercased with `low`) or "" for a falsy value. -1 for a
// truthy value that is not a string.
static int field_str(const char *o, const char *oe, const char *key, int low,
                     char *out, size_t cap, size_t *n) {
    const char *v, *ve;
    *n = 0;
    if (!moy_json_get(o, oe, key, &v, &ve) || !moy_json_truthy(v, ve)) {
        return 0;
    }
    if (moy_json_kind(v, ve) != MOY_JSON_STR) {
        return -1;
    }
    size_t len = moy_json_strlen(v, ve);
    if (len >= cap) {
        return -1;
    }
    *n = moy_json_str(v, ve, out);
    if (low) {
        for (size_t i = 0; i < *n; i++) {
            out[i] = (char)lower((unsigned char)out[i]);
        }
    }
    return 0;
}

// Python's `int(m.get(key) or 0)`: 0 and the value, or -1 when int() raises
// or the value is past 64 bits.
static int field_int(const char *o, const char *oe, const char *key, int64_t *out) {
    const char *v, *ve;
    *out = 0;
    if (!moy_json_get(o, oe, key, &v, &ve) || !moy_json_truthy(v, ve)) {
        return 0;
    }
    return moy_json_int(v, ve, out) == 1 ? 0 : -1;
}

#define FIELD_MAX 160

// A decimal and a newline into `out` (24 bytes): its length. Not printf, whose
// 64-bit conversion a board's newlib may not carry.
static size_t dec_line(int64_t v, char *out) {
    char tmp[24];
    size_t n = 0, k = 0;
    uint64_t u = v < 0 ? (uint64_t)0 - (uint64_t)v : (uint64_t)v;
    do {
        tmp[n++] = (char)('0' + (int)(u % 10u));
        u /= 10u;
    } while (u != 0);
    if (v < 0) {
        out[k++] = '-';
    }
    while (n > 0) {
        out[k++] = tmp[--n];
    }
    out[k++] = '\n';
    return k;
}

static size_t put(char *out, size_t cap, size_t at, const char *s, size_t n) {
    if (at < cap) {
        memcpy(out + at, s, at + n <= cap ? n : cap - at);
    }
    return at + n;
}

static size_t canonical(const char *obj, size_t n, int c6, char *out, size_t cap) {
    const char *oe = obj + n;
    const char *o = moy_json_ws(obj, oe);
    const char *end = moy_json_value(o, oe, 0);
    if (end == NULL || moy_json_kind(o, end) != MOY_JSON_OBJ) {
        return (size_t)-1;
    }
    char f[FIELD_MAX], num[24];
    size_t fn, at = 0;
    int64_t version, size;
    const char *c = o, *ce = end;
    if (c6) {
        const char *v, *ve;
        static const char empty[] = "{}";
        if (moy_json_get(o, end, "c6", &v, &ve) && moy_json_truthy(v, ve)) {
            if (moy_json_kind(v, ve) != MOY_JSON_OBJ) {
                return (size_t)-1;
            }
            c = v;
            ce = ve;
        } else {
            c = empty;
            ce = empty + 2;
        }
    }
    at = put(out, cap, at, c6 ? "moybyte-c6-v1\n" : "moybyte-ota-v2\n", c6 ? 14 : 15);
    if (field_str(o, end, "board", 0, f, sizeof(f), &fn) != 0) {
        return (size_t)-1;
    }
    at = put(out, cap, at, f, fn);
    at = put(out, cap, at, "\n", 1);
    if (!c6) {
        if (field_str(o, end, "channel", 0, f, sizeof(f), &fn) != 0) {
            return (size_t)-1;
        }
        at = put(out, cap, at, f, fn);
        at = put(out, cap, at, "\n", 1);
    }
    if (field_int(c, ce, "version", &version) != 0 || field_int(c, ce, "size", &size) != 0) {
        return (size_t)-1;
    }
    at = put(out, cap, at, num, dec_line(version, num));
    at = put(out, cap, at, num, dec_line(size, num));
    if (field_str(c, ce, "sha256", 1, f, sizeof(f), &fn) != 0) {
        return (size_t)-1;
    }
    return put(out, cap, at, f, fn);
}

size_t moy_ota_canonical(const char *obj, size_t n, char *out, size_t cap) {
    return canonical(obj, n, 0, out, cap);
}

size_t moy_ota_canonical_c6(const char *obj, size_t n, char *out, size_t cap) {
    return canonical(obj, n, 1, out, cap);
}

static void say(char *why, size_t cap, const char *s) {
    if (why != NULL && cap > 0) {
        snprintf(why, cap, "%s", s);
    }
}

// The signature member `name` checked against `canon` under the policy: OK,
// or ERR with `bad` or `unsigned_` as the reason.
static int judge_sig(const char *o, const char *oe, const char *name,
                     const char *canon, size_t canon_n, int require_sig,
                     const moy_ota_key_t *keys, int nkeys, const char *bad,
                     const char *unsigned_, char *why, size_t why_cap) {
    const char *v, *ve;
    if (moy_json_get(o, oe, name, &v, &ve) && moy_json_truthy(v, ve)) {
        int ok = 0;
        if (moy_json_kind(v, ve) == MOY_JSON_STR && ve - v - 2 <= 2 * MOY_OTA_KEY_BYTES) {
            ok = moy_ota_verify(canon, canon_n, v + 1, (size_t)(ve - v - 2), keys, nkeys);
        }
        if (!ok) {
            say(why, why_cap, bad);
            return MOY_OTA_ERR;
        }
        return MOY_OTA_OK;
    }
    if (require_sig) {
        say(why, why_cap, unsigned_);
        return MOY_OTA_ERR;
    }
    return MOY_OTA_OK;
}

static int judge(const char *text, size_t n, int c6, const char *board,
                 int require_sig, const moy_ota_key_t *keys, int nkeys,
                 char *why, size_t why_cap) {
    const char *te = text + n;
    const char *o = moy_json_ws(text, te);
    const char *end = moy_json_value(o, te, 0);
    if (end == NULL || moy_json_ws(end, te) != te || moy_json_kind(o, end) != MOY_JSON_OBJ) {
        say(why, why_cap, "bad manifest");
        return MOY_OTA_ERR;
    }
    if (!c6 && board != NULL) {
        char f[FIELD_MAX];
        size_t fn;
        if (field_str(o, end, "board", 0, f, sizeof(f), &fn) != 0
            || (fn > 0 && (fn != strlen(board) || memcmp(f, board, fn) != 0))) {
            say(why, why_cap, "wrong board");
            return MOY_OTA_ERR;
        }
    }
    char canon[2 * FIELD_MAX + 96];
    size_t cn = canonical(o, (size_t)(end - o), c6, canon, sizeof(canon));
    if (cn == (size_t)-1 || cn > sizeof(canon)) {
        say(why, why_cap, c6 ? "bad c6 version" : "bad manifest");
        return MOY_OTA_ERR;
    }
    return judge_sig(o, end, c6 ? "c6_sig" : "sig", canon, cn, require_sig, keys, nkeys,
                     c6 ? "bad c6 signature" : "bad signature",
                     c6 ? "unsigned c6 image" : "unsigned update", why, why_cap);
}

int moy_ota_judge(const char *text, size_t n, const char *board,
                  int require_sig, const moy_ota_key_t *keys, int nkeys,
                  char *why, size_t why_cap) {
    return judge(text, n, 0, board, require_sig, keys, nkeys, why, why_cap);
}

int moy_ota_judge_c6(const char *text, size_t n, int require_sig,
                     const moy_ota_key_t *keys, int nkeys, char *why,
                     size_t why_cap) {
    return judge(text, n, 1, NULL, require_sig, keys, nkeys, why, why_cap);
}

// -- the client ---------------------------------------------------------------------------------

#define URL_MAX 4096
#define EIO_ 5
#define EINVAL_ 22
#define EBADF_ 9
#define ENOMEM_ 12

typedef struct {
    uint16_t gen;
    uint8_t open;
    void *conn;
    char *head;                     // MOY_HTTPC_HEAD_MAX: the head, then the body's first bytes
    size_t rest, rest_n;            // the body bytes still in `head`
} client_t;

static client_t *s_cl;                  // MOY_HTTPC_SLOTS of them, kernel memory

static int handle_of(int i) {
    return (int)s_cl[i].gen << 8 | (i + 1);
}

static client_t *client_at(int h) {
    int i = (h & 0xff) - 1;
    if (s_cl == NULL || h <= 0 || i < 0 || i >= MOY_HTTPC_SLOTS || !s_cl[i].open
        || s_cl[i].gen != (uint16_t)(h >> 8)) {
        return NULL;
    }
    return &s_cl[i];
}

static void client_drop(client_t *c) {
    if (c->conn != NULL) {
        moy_conn_close(c->conn);
        c->conn = NULL;
    }
    if (c->head != NULL) {
        moy_net_free(c->head);
        c->head = NULL;
    }
    c->open = 0;
    c->gen = (uint16_t)((c->gen + 1u) & 0x7fffu);
}

// One GET: the connection open with its head read into c->head. 0, or -errno.
static int get_once(client_t *c, const char *url, const char *agent,
                    moy_http_resp_t *r) {
    char host[MOY_URL_HOST_MAX];
    int tls;
    uint16_t port;
    const char *path;
    if (moy_url_parse(url, &tls, host, sizeof(host), &port, &path) != 0) {
        return -EINVAL_;
    }
    int rc = moy_conn_open(host, port, tls, &c->conn);
    if (rc != 0) {
        c->conn = NULL;
        return rc;
    }
    int k = snprintf(c->head, MOY_HTTPC_HEAD_MAX,
                     "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: %s\r\nConnection: close\r\n\r\n",
                     path, host, agent);
    if (k <= 0 || k >= MOY_HTTPC_HEAD_MAX) {
        return -EINVAL_;
    }
    rc = moy_conn_write(c->conn, c->head, (size_t)k);
    if (rc != 0) {
        return rc;
    }
    size_t n = 0;
    for (;;) {
        if (n >= MOY_HTTPC_HEAD_MAX) {
            return -EIO_;
        }
        int got = moy_conn_read(c->conn, c->head + n, MOY_HTTPC_HEAD_MAX - n);
        if (got < 0) {
            return got;
        }
        if (got == 0) {
            return -EIO_;
        }
        n += (size_t)got;
        if (moy_http_resp_parse(c->head, n, r) == MOY_HTTP_OK) {
            break;
        }
    }
    c->rest = r->head_end;
    c->rest_n = n - r->head_end;
    return 0;
}

int moy_httpc_open(const char *url, const char *agent, int *status,
                   uint32_t *clen) {
    if (s_cl == NULL) {
        s_cl = moy_net_alloc(sizeof(client_t) * MOY_HTTPC_SLOTS);
        if (s_cl == NULL) {
            return -ENOMEM_;
        }
    }
    int i = 0;
    while (i < MOY_HTTPC_SLOTS && s_cl[i].open) {
        i++;
    }
    if (i == MOY_HTTPC_SLOTS) {
        return -EBADF_;
    }
    client_t *c = &s_cl[i];
    char *cur = moy_net_alloc(URL_MAX);
    c->head = moy_net_alloc(MOY_HTTPC_HEAD_MAX);
    if (cur == NULL || c->head == NULL) {
        moy_net_free(cur);
        moy_net_free(c->head);
        c->head = NULL;
        return -ENOMEM_;
    }
    c->open = 1;
    snprintf(cur, URL_MAX, "%s", url);
    int rc;
    for (int hop = 0;; hop++) {
        moy_http_resp_t r;
        rc = get_once(c, cur, agent, &r);
        if (rc != 0) {
            break;
        }
        int redirect = r.status == 301 || r.status == 302 || r.status == 303
                       || r.status == 307 || r.status == 308;
        if (!redirect || r.loc == NULL || r.loc_n == 0 || hop >= MOY_HTTPC_HOPS) {
            *status = r.status;
            *clen = r.clen;
            break;
        }
        char *next = moy_net_alloc(URL_MAX);
        if (next == NULL) {
            rc = -ENOMEM_;
            break;
        }
        if (r.loc[0] == '/') {
            char host[MOY_URL_HOST_MAX];
            int tls;
            uint16_t port;
            const char *path;
            moy_url_parse(cur, &tls, host, sizeof(host), &port, &path);
            int dflt = (tls && port == 443) || (!tls && port == 80);
            char pp[8] = "";
            if (!dflt) {
                snprintf(pp, sizeof(pp), ":%u", (unsigned)port);
            }
            snprintf(next, URL_MAX, "%s://%s%s%.*s", tls ? "https" : "http", host, pp,
                     (int)r.loc_n, r.loc);
        } else {
            snprintf(next, URL_MAX, "%.*s", (int)r.loc_n, r.loc);
        }
        moy_net_free(cur);
        cur = next;
        moy_conn_close(c->conn);
        c->conn = NULL;
    }
    moy_net_free(cur);
    if (rc != 0) {
        client_drop(c);
        return rc;
    }
    return handle_of(i);
}

int moy_httpc_read(int h, void *buf, size_t n) {
    client_t *c = client_at(h);
    if (c == NULL) {
        return -EBADF_;
    }
    if (n == 0) {
        return 0;
    }
    if (c->rest_n > 0) {
        size_t k = c->rest_n < n ? c->rest_n : n;
        memcpy(buf, c->head + c->rest, k);
        c->rest += k;
        c->rest_n -= k;
        return (int)k;
    }
    if (n > 0x7fffffffu) {
        n = 0x7fffffffu;
    }
    return moy_conn_read(c->conn, buf, n);
}

void moy_httpc_close(int h) {
    client_t *c = client_at(h);
    if (c != NULL) {
        client_drop(c);
    }
}

int moy_httpc_get(const char *url, const char *agent, size_t cap, int *status,
                  char **body, size_t *n) {
    uint32_t clen = 0;
    *body = NULL;
    *n = 0;
    int h = moy_httpc_open(url, agent, status, &clen);
    if (h < 0) {
        return h;
    }
    char *b = moy_net_alloc(cap + 1u);
    if (b == NULL) {
        moy_httpc_close(h);
        return -ENOMEM_;
    }
    size_t want = clen && clen < cap ? clen : cap, got = 0;
    int rc = 0;
    if (*status == 200) {
        while (got < want) {
            int k = moy_httpc_read(h, b + got, want - got);
            if (k < 0) {
                rc = k;
                break;
            }
            if (k == 0) {
                break;
            }
            got += (size_t)k;
        }
    }
    moy_httpc_close(h);
    if (rc != 0) {
        moy_net_free(b);
        return rc;
    }
    b[got] = '\0';
    *body = b;
    *n = got;
    return 0;
}

// -- the updater -----------------------------------------------------------------------------------

#define DL_BUF 16384
#define AGENT "moybyte-ota"

typedef struct {
    moy_ota_state_t st;
    int h;                          // the download's client handle, 0 when none
    moy_sha256_t sha;
    uint8_t want[32];
    uint8_t check_sha;
    uint8_t first;                  // the sink has had no byte yet
    char *buf;                      // DL_BUF, the step's bytes
    uint8_t *carry;                 // the C6 sink's partial chunk
    uint32_t carry_n;
} ota_t;

// The updater's state is kernel memory, made at its first use; every entry
// answers a failure when it cannot be made.
static ota_t *s_up;
#define s_u (*s_up)

static int ota_ready(void) {
    if (s_up == NULL) {
        s_up = moy_net_alloc(sizeof(ota_t));
    }
    return s_up != NULL;
}

void moy_ota_state(moy_ota_state_t *out) {
    if (!ota_ready()) {
        memset(out, 0, sizeof(*out));
        snprintf(out->err, sizeof(out->err), "no memory");
        return;
    }
    *out = s_u.st;
}

const char *moy_ota_error(void) {
    return ota_ready() ? s_u.st.err : "no memory";
}

static int fail(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static int fail(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(s_u.st.err, sizeof(s_u.st.err), fmt, ap);
    va_end(ap);
    return -1;
}

// What the screen says when the sink refuses a write: no room, or no write.
static int sink_fail(int rc) {
    return fail("%s", rc == -28 ? "Not enough room for it." : "Couldn't save the update.");
}

static int c6_present(void) {
    return moy_c6_ota_begin != NULL && moy_c6_ota_write != NULL
           && moy_c6_ota_end != NULL && moy_c6_ota_activate != NULL;
}

static void drop_sink(void) {
    if (s_u.st.sink == MOY_OTA_SINK_SLOT
        && (s_u.st.phase == MOY_OTA_FETCHING || s_u.st.phase == MOY_OTA_WRITING
            || s_u.st.phase == MOY_OTA_VERIFIED)) {
        moy_slot_abort();
    }
    s_u.carry_n = 0;
}

static void close_stream(void) {
    if (s_u.h > 0) {
        moy_httpc_close(s_u.h);
        s_u.h = 0;
    }
}

static int dl_failed(void) {
    close_stream();
    drop_sink();
    s_u.st.phase = MOY_OTA_FAILED;
    s_u.st.done = 0;
    return -1;
}

static moy_ota_key_t *s_trust;
static int s_ntrust;

void moy_ota_trust(const moy_ota_key_t *keys, int n) {
    moy_net_free(s_trust);
    s_trust = NULL;
    s_ntrust = 0;
    if (keys != NULL && n > 0) {
        s_trust = moy_net_alloc(sizeof(moy_ota_key_t) * (size_t)n);
        if (s_trust != NULL) {
            memcpy(s_trust, keys, sizeof(moy_ota_key_t) * (size_t)n);
            s_ntrust = n;
        }
    }
}

int moy_ota_check(const char *url, const char *board, int require_sig,
                  char **text, size_t *n) {
    if (!ota_ready()) {
        return MOY_OTA_ERR;
    }
    s_u.st.err[0] = '\0';
    *text = NULL;
    *n = 0;
    int status = 0;
    char *body;
    size_t got;
    int rc = moy_httpc_get(url, AGENT, MOY_OTA_MANIFEST_MAX, &status, &body, &got);
    if (rc != 0) {
        fail("net error %d", -rc);
        return MOY_OTA_ERR;
    }
    if (status != 200) {
        moy_net_free(body);
        if (status == 404 || status == 410) {
            return MOY_OTA_ABSENT;
        }
        fail("http %d", status);
        return MOY_OTA_ERR;
    }
    if (moy_ota_judge(body, got, board, require_sig, s_trust, s_ntrust, s_u.st.err,
                      sizeof(s_u.st.err)) != MOY_OTA_OK) {
        moy_net_free(body);
        return MOY_OTA_ERR;
    }
    *text = body;
    *n = got;
    return MOY_OTA_OK;
}

static int ensure_buf(void) {
    if (s_u.buf == NULL) {
        s_u.buf = moy_net_alloc(DL_BUF);
    }
    return s_u.buf != NULL;
}

int moy_ota_dl_begin(const char *url, uint32_t size, const char *sha_hex,
                     int sink) {
    if (!ota_ready()) {
        return -1;
    }
    moy_ota_cancel();
    memset(&s_u.st, 0, sizeof(s_u.st));
    s_u.st.sink = (uint8_t)sink;
    if (url == NULL || url[0] == '\0') {
        return fail("manifest has no url");
    }
    if (sink == MOY_OTA_SINK_C6 && !c6_present()) {
        return fail("no c6 updater");
    }
    if (sink != MOY_OTA_SINK_SLOT && sink != MOY_OTA_SINK_C6) {
        return fail("bad sink");
    }
    size_t sn = sha_hex != NULL ? strlen(sha_hex) : 0;
    s_u.check_sha = sn > 0;
    if (sn > 0 && (sn != 64 || hex_to(sha_hex, sn, s_u.want, 32) != 0)) {
        return fail("bad sha256");
    }
    if (!ensure_buf()) {
        return fail("no memory");
    }
    int status = 0;
    uint32_t clen = 0;
    int h = moy_httpc_open(url, AGENT, &status, &clen);
    if (h < 0) {
        return fail("net error %d", -h);
    }
    if (status != 200) {
        moy_httpc_close(h);
        return fail("http %d", status);
    }
    s_u.h = h;
    s_u.st.dl_total = size ? size : clen;
    s_u.st.total = s_u.st.dl_total;
    if (sink == MOY_OTA_SINK_SLOT) {
        uint32_t cap = 0;
        int rc = moy_slot_open(s_u.st.dl_total, &cap);
        if (rc == -27 || (rc == 0 && s_u.st.dl_total > cap)) {
            if (rc == 0) {
                moy_slot_abort();
            }
            close_stream();
            return fail("image %uK > slot %uK", (unsigned)(s_u.st.dl_total / 1024u),
                        (unsigned)(cap / 1024u));
        }
        if (rc != 0) {
            close_stream();
            return sink_fail(rc);
        }
    } else {
        if (s_u.carry == NULL) {
            s_u.carry = moy_net_alloc(MOY_OTA_C6_CHUNK);
        }
        if (s_u.carry == NULL) {
            close_stream();
            return fail("no memory");
        }
        if (moy_c6_ota_begin() != 0) {
            close_stream();
            return fail("c6 ota_begin refused");
        }
        s_u.carry_n = 0;
    }
    moy_sha256_init(&s_u.sha);
    s_u.first = 1;
    s_u.st.phase = MOY_OTA_FETCHING;
    return 0;
}

// The C6 sink in whole chunks: `flush` writes the partial one too.
static int c6_put(const uint8_t *p, size_t n, int flush) {
    while (n > 0 || (flush && s_u.carry_n > 0)) {
        size_t take = MOY_OTA_C6_CHUNK - s_u.carry_n;
        if (take > n) {
            take = n;
        }
        memcpy(s_u.carry + s_u.carry_n, p, take);
        s_u.carry_n += (uint32_t)take;
        p += take;
        n -= take;
        if (s_u.carry_n == MOY_OTA_C6_CHUNK || (flush && n == 0)) {
            if (moy_c6_ota_write(s_u.carry, s_u.carry_n) != 0) {
                return -1;
            }
            s_u.carry_n = 0;
        }
    }
    return 0;
}

static int sink_put(const uint8_t *p, size_t n) {
    if (s_u.st.sink == MOY_OTA_SINK_C6) {
        if (c6_put(p, n, 0) != 0) {
            return fail("c6 write refused");
        }
    } else {
        if (s_u.first && n > 0 && p[0] != 0xE9) {
            return fail("not an app image");
        }
        int rc = moy_slot_write(p, n);
        if (rc != 0) {
            return sink_fail(rc);
        }
    }
    if (n > 0) {
        s_u.first = 0;
    }
    s_u.st.done += (uint32_t)n;
    return 0;
}

int moy_ota_dl_step(uint32_t max) {
    if (!ota_ready()) {
        return -1;
    }
    if (s_u.st.phase != MOY_OTA_FETCHING || s_u.h <= 0) {
        return 0;
    }
    if (max == 0 || max > DL_BUF) {
        max = DL_BUF;
    }
    size_t got = 0;
    while (got < max) {
        int k = moy_httpc_read(s_u.h, s_u.buf + got, max - got);
        if (k < 0) {
            fail("read failed (errno %d)", -k);
            return dl_failed();
        }
        if (k == 0) {
            break;
        }
        got += (size_t)k;
    }
    if (got == 0) {
        return 0;
    }
    moy_sha256_update(&s_u.sha, s_u.buf, got);
    if (sink_put((const uint8_t *)s_u.buf, got) != 0) {
        return dl_failed();
    }
    s_u.st.dl_done += (uint32_t)got;
    return 1;
}

int moy_ota_dl_finish(void) {
    if (!ota_ready()) {
        return -1;
    }
    if (s_u.st.phase != MOY_OTA_FETCHING) {
        return s_u.st.phase == MOY_OTA_VERIFIED ? 0 : -1;
    }
    close_stream();
    if (s_u.st.sink == MOY_OTA_SINK_C6 && c6_put(NULL, 0, 1) != 0) {
        fail("c6 write refused");
        return dl_failed();
    }
    if (s_u.st.dl_total && s_u.st.dl_done != s_u.st.dl_total) {
        fail("size %u/%u", (unsigned)s_u.st.dl_done, (unsigned)s_u.st.dl_total);
        return dl_failed();
    }
    if (s_u.check_sha) {
        uint8_t got[32];
        moy_sha256_final(&s_u.sha, got);
        if (memcmp(got, s_u.want, 32) != 0) {
            fail("sha256 mismatch");
            return dl_failed();
        }
    }
    if (s_u.st.sink == MOY_OTA_SINK_SLOT) {
        int rc = moy_slot_close();
        if (rc != 0) {
            fail("image refused (errno %d)", -rc);
            s_u.st.phase = MOY_OTA_FAILED;
            s_u.st.done = 0;
            return -1;
        }
    }
    s_u.st.phase = MOY_OTA_VERIFIED;
    return 0;
}

void moy_ota_cancel(void) {
    if (!ota_ready()) {
        return;
    }
    close_stream();
    drop_sink();
    if (s_u.st.phase != MOY_OTA_IDLE) {
        s_u.st.phase = MOY_OTA_IDLE;
    }
    s_u.st.done = 0;
    s_u.st.dl_done = 0;
}

int moy_ota_slot_begin(uint32_t size) {
    if (!ota_ready()) {
        return -1;
    }
    moy_ota_cancel();
    memset(&s_u.st, 0, sizeof(s_u.st));
    s_u.st.sink = MOY_OTA_SINK_SLOT;
    if (size == 0) {
        return fail("empty image");
    }
    uint32_t cap = 0;
    int rc = moy_slot_open(size, &cap);
    if (rc == -27 || (rc == 0 && size > cap)) {
        if (rc == 0) {
            moy_slot_abort();
        }
        return fail("image %uK > slot %uK", (unsigned)(size / 1024u), (unsigned)(cap / 1024u));
    }
    if (rc != 0) {
        return sink_fail(rc);
    }
    s_u.st.total = size;
    s_u.first = 1;
    s_u.st.phase = MOY_OTA_WRITING;
    return 0;
}

int moy_ota_slot_write(const void *p, size_t n) {
    if (!ota_ready()) {
        return -1;
    }
    if (s_u.st.phase != MOY_OTA_WRITING) {
        return fail("no install open");
    }
    if (sink_put(p, n) != 0) {
        drop_sink();
        s_u.st.phase = MOY_OTA_FAILED;
        s_u.st.done = 0;
        return -1;
    }
    return 0;
}

int moy_ota_slot_close(void) {
    if (!ota_ready()) {
        return -1;
    }
    if (s_u.st.phase != MOY_OTA_WRITING) {
        return fail("no install open");
    }
    if (s_u.st.done != s_u.st.total) {
        drop_sink();
        s_u.st.phase = MOY_OTA_FAILED;
        return fail("size %u/%u", (unsigned)s_u.st.done, (unsigned)s_u.st.total);
    }
    int rc = moy_slot_close();
    if (rc != 0) {
        s_u.st.phase = MOY_OTA_FAILED;
        return fail("image refused (errno %d)", -rc);
    }
    s_u.st.phase = MOY_OTA_VERIFIED;
    return 0;
}

int moy_ota_activate(char *label, size_t cap) {
    if (!ota_ready()) {
        return -1;
    }
    if (s_u.st.phase != MOY_OTA_VERIFIED || s_u.st.sink != MOY_OTA_SINK_SLOT) {
        return fail("nothing verified to install");
    }
    int rc = moy_slot_boot(label, cap);
    if (rc != 0) {
        return fail("set_boot failed (errno %d)", -rc);
    }
    s_u.st.phase = MOY_OTA_IDLE;
    return 0;
}

int moy_ota_c6_commit(void) {
    if (!ota_ready()) {
        return -1;
    }
    if (s_u.st.phase != MOY_OTA_VERIFIED || s_u.st.sink != MOY_OTA_SINK_C6) {
        return fail("nothing verified to install");
    }
    if (moy_c6_ota_end() != 0) {
        s_u.st.phase = MOY_OTA_FAILED;
        return fail("c6 ota_end refused");
    }
    if (moy_c6_ota_activate() != 0) {
        s_u.st.phase = MOY_OTA_FAILED;
        return fail("c6 activate refused");
    }
    s_u.st.phase = MOY_OTA_IDLE;
    return 0;
}

void moy_net_vm_stop(void) {
    if (s_up != NULL && (s_u.st.phase == MOY_OTA_FETCHING || s_u.st.phase == MOY_OTA_WRITING)) {
        moy_ota_cancel();
    }
    for (int i = 0; s_cl != NULL && i < MOY_HTTPC_SLOTS; i++) {
        if (s_cl[i].open) {
            client_drop(&s_cl[i]);
        }
    }
}
