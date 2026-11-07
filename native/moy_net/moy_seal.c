// moy_seal: moy_seal.h is the authority (the protocol, its labels and nonces).

#if !defined(ESP_PLATFORM) && !defined(_DEFAULT_SOURCE)
#define _DEFAULT_SOURCE                 // getentropy
#endif

#include <stdlib.h>
#include <string.h>

#include "moy_seal.h"

#ifdef ESP_PLATFORM
#include "esp_random.h"
#else
#include <unistd.h>
#endif

#include "mbedtls/ecp.h"
#include "mbedtls/gcm.h"
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"

// RFC 9382's M and N for P-256, uncompressed.
static const uint8_t SPAKE_M[MOY_SEAL_POINT] = {
    0x04, 0x88, 0x6e, 0x2f, 0x97, 0xac, 0xe4, 0x6e, 0x55, 0xba, 0x9d, 0xd7, 0x24, 0x25, 0x79,
    0xf2, 0x99, 0x3b, 0x64, 0xe1, 0x6e, 0xf3, 0xdc, 0xab, 0x95, 0xaf, 0xd4, 0x97, 0x33, 0x3d,
    0x8f, 0xa1, 0x2f, 0x5f, 0xf3, 0x55, 0x16, 0x3e, 0x43, 0xce, 0x22, 0x4e, 0x0b, 0x0e, 0x65,
    0xff, 0x02, 0xac, 0x8e, 0x5c, 0x7b, 0xe0, 0x94, 0x19, 0xc7, 0x85, 0xe0, 0xca, 0x54, 0x7d,
    0x55, 0xa1, 0x2e, 0x2d, 0x20,
};
static const uint8_t SPAKE_N[MOY_SEAL_POINT] = {
    0x04, 0xd8, 0xbb, 0xd6, 0xc6, 0x39, 0xc6, 0x29, 0x37, 0xb0, 0x4d, 0x99, 0x7f, 0x38, 0xc3,
    0x77, 0x07, 0x19, 0xc6, 0x29, 0xd7, 0x01, 0x4d, 0x49, 0xa2, 0x4b, 0x4f, 0x98, 0xba, 0xa1,
    0x29, 0x2b, 0x49, 0x07, 0xd6, 0x0a, 0xa6, 0xbf, 0xad, 0xe4, 0x50, 0x08, 0xa6, 0x36, 0x33,
    0x7f, 0x51, 0x68, 0xc6, 0x4d, 0x9b, 0xd3, 0x60, 0x34, 0x80, 0x8c, 0xd5, 0x64, 0x49, 0x0b,
    0x1e, 0x65, 0x6e, 0xdb, 0xe7,
};

#define PW_ROUNDS 4096

// -- the primitives --------------------------------------------------------------------

void moy_seal_random(void *p, size_t n) {
    #ifdef ESP_PLATFORM
    esp_fill_random(p, n);
    #else
    uint8_t *b = p;
    while (n > 0) {
        size_t k = n < 256 ? n : 256;
        if (getentropy(b, k) != 0) {
            abort();                    // never a key from nothing
        }
        b += k;
        n -= k;
    }
    #endif
}

static int rng(void *ctx, unsigned char *p, size_t n) {
    (void)ctx;
    moy_seal_random(p, n);
    return 0;
}

void moy_seal_wipe(void *p, size_t n) {
    volatile uint8_t *v = p;
    while (n--) {
        *v++ = 0;
    }
}

int moy_seal_eq(const void *a, const void *b, size_t n) {
    const uint8_t *x = a, *y = b;
    uint8_t d = 0;
    for (size_t i = 0; i < n; i++) {
        d |= (uint8_t)(x[i] ^ y[i]);
    }
    return d == 0;
}

static int nib(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

int moy_seal_unhex(const char *s, size_t n, uint8_t *out, size_t want) {
    if (s == NULL || n != want * 2u) {
        return 0;
    }
    for (size_t i = 0; i < want; i++) {
        int hi = nib(s[2 * i]), lo = nib(s[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            return 0;
        }
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return 1;
}

void moy_seal_hex(const uint8_t *p, size_t n, char *out) {
    static const char D[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = D[p[i] >> 4];
        out[2 * i + 1] = D[p[i] & 15];
    }
    out[2 * n] = '\0';
}

void moy_seal_sha256(const void *p, size_t n, uint8_t out[32]) {
    mbedtls_sha256(p, n, out, 0);
}

void moy_seal_hmac(const uint8_t *key, size_t kn, const void *p, size_t n, uint8_t out[32]) {
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, kn, p, n, out);
}

void moy_seal_hkdf(const uint8_t *salt, size_t sn, const uint8_t *ikm, size_t in,
                   const void *info, size_t infon, uint8_t *out, size_t on) {
    static const uint8_t zero[32];
    uint8_t prk[32], t[32 + 64 + 1];
    moy_seal_hmac(salt != NULL && sn ? salt : zero, salt != NULL && sn ? sn : 32, ikm, in, prk);
    size_t tn = 0;
    if (infon > 64) {
        infon = 64;
    }
    for (uint8_t i = 1; on > 0; i++) {
        uint8_t blk[32];
        memcpy(t + tn, info, infon);
        t[tn + infon] = i;
        moy_seal_hmac(prk, 32, t, tn + infon + 1, blk);
        size_t k = on < 32 ? on : 32;
        memcpy(out, blk, k);
        memcpy(t, blk, 32);
        tn = 32;
        out += k;
        on -= k;
    }
    moy_seal_wipe(prk, sizeof prk);
    moy_seal_wipe(t, sizeof t);
}

void moy_seal_pw_hash(const char *pw, size_t pn, const char *salt, size_t sn, uint8_t out[32]) {
    mbedtls_sha256_context c;
    mbedtls_sha256_init(&c);
    mbedtls_sha256_starts(&c, 0);
    mbedtls_sha256_update(&c, (const uint8_t *)salt, sn);
    mbedtls_sha256_update(&c, (const uint8_t *)pw, pn);
    mbedtls_sha256_finish(&c, out);
    for (int i = 1; i < PW_ROUNDS; i++) {
        mbedtls_sha256_starts(&c, 0);
        mbedtls_sha256_update(&c, out, 32);
        mbedtls_sha256_update(&c, (const uint8_t *)pw, pn);
        mbedtls_sha256_finish(&c, out);
    }
    mbedtls_sha256_free(&c);
}

int moy_seal_pw_parse(const char *text, size_t n, char *salt, size_t salt_cap, uint8_t h[32]) {
    static const char PRE[] = "$moy1$";
    if (text == NULL || n < sizeof(PRE) - 1u || memcmp(text, PRE, sizeof(PRE) - 1u) != 0) {
        return 0;
    }
    const char *s = text + sizeof(PRE) - 1u, *end = text + n;
    const char *d = memchr(s, '$', (size_t)(end - s));
    if (d == NULL || (size_t)(d - s) + 1u > salt_cap || d == s) {
        return 0;
    }
    const char *e = d + 1;
    while (e < end && *e != '\n' && *e != '\r' && *e != ' ') {
        e++;
    }
    if (!moy_seal_unhex(d + 1, (size_t)(e - d - 1), h, 32)) {
        return 0;
    }
    memcpy(salt, s, (size_t)(d - s));
    salt[d - s] = '\0';
    return 1;
}

// -- the protocol's MACs (the header) -----------------------------------------------------

// HMAC(key, label 0 [id nb nc] a b)
static void mac_of(const uint8_t *key, size_t kn, const char *label, const moy_seal_hs_t *h,
                   const void *a, size_t an, const void *b, size_t bn, uint8_t out[32]) {
    uint8_t m[24 + 3 * MOY_SEAL_RAND + 16 + 72];
    size_t ln = strlen(label), k = 0;
    if (an > 16) {
        an = 16;
    }
    if (bn > 72) {
        bn = 72;
    }
    memcpy(m, label, ln);
    m[ln] = 0;
    k = ln + 1u;
    if (h != NULL) {
        memcpy(m + k, h->id, MOY_SEAL_RAND);
        memcpy(m + k + MOY_SEAL_RAND, h->nb, MOY_SEAL_RAND);
        memcpy(m + k + 2 * MOY_SEAL_RAND, h->nc, MOY_SEAL_RAND);
        k += 3 * MOY_SEAL_RAND;
    }
    if (an) {
        memcpy(m + k, a, an);
        k += an;
    }
    if (bn) {
        memcpy(m + k, b, bn);
        k += bn;
    }
    moy_seal_hmac(key, kn, m, k, out);
}

void moy_seal_key_mac(const moy_seal_hs_t *h, const uint8_t kid[MOY_SEAL_KID],
                      const uint8_t key[MOY_SEAL_KEY], uint8_t mac[MOY_SEAL_MAC]) {
    mac_of(key, MOY_SEAL_KEY, "moy1 key", h, kid, MOY_SEAL_KID, NULL, 0, mac);
}

void moy_seal_proof(const moy_seal_hs_t *h, const uint8_t kid[MOY_SEAL_KID],
                    const uint8_t key[MOY_SEAL_KEY], const uint8_t sid[MOY_SEAL_SID],
                    uint8_t mac[MOY_SEAL_MAC]) {
    mac_of(key, MOY_SEAL_KEY, "moy1 board", h, kid, MOY_SEAL_KID, sid, MOY_SEAL_SID, mac);
}

void moy_seal_session(const moy_seal_hs_t *h, const uint8_t kid[MOY_SEAL_KID],
                      const uint8_t key[MOY_SEAL_KEY], moy_seal_keys_t *out) {
    static const char L[] = "moy1 session";
    uint8_t salt[2 * MOY_SEAL_RAND], info[sizeof L + MOY_SEAL_KID], k[2 * MOY_SEAL_KEY];
    memcpy(salt, h->nb, MOY_SEAL_RAND);
    memcpy(salt + MOY_SEAL_RAND, h->nc, MOY_SEAL_RAND);
    memcpy(info, L, sizeof L);          // the label and its NUL
    memcpy(info + sizeof L, kid, MOY_SEAL_KID);
    moy_seal_hkdf(salt, sizeof salt, key, MOY_SEAL_KEY, info, sizeof info, k, sizeof k);
    memcpy(out->kc, k, MOY_SEAL_KEY);
    memcpy(out->kb, k + MOY_SEAL_KEY, MOY_SEAL_KEY);
    moy_seal_wipe(k, sizeof k);
}

void moy_seal_token_mac(const moy_seal_hs_t *h, const uint8_t token[MOY_SEAL_RAND],
                        const char *name, size_t nn, uint8_t mac[MOY_SEAL_MAC]) {
    mac_of(token, MOY_SEAL_RAND, "moy1 token", h, NULL, 0, name, nn, mac);
}

void moy_seal_wrap(const moy_seal_hs_t *h, const uint8_t *secret, size_t sn,
                   uint8_t key[MOY_SEAL_KEY]) {
    uint8_t pad[32];
    mac_of(secret, sn, "moy1 wrap", h, NULL, 0, NULL, 0, pad);
    for (int i = 0; i < MOY_SEAL_KEY; i++) {
        key[i] ^= pad[i];
    }
    moy_seal_wipe(pad, sizeof pad);
}

// -- SPAKE2 over P-256 ----------------------------------------------------------------------

typedef struct {
    mbedtls_ecp_group g;
    mbedtls_mpi w, s, one;
    mbedtls_ecp_point a, b, c;
} ec_t;

static int ec_init(ec_t *e) {
    mbedtls_ecp_group_init(&e->g);
    mbedtls_mpi_init(&e->w);
    mbedtls_mpi_init(&e->s);
    mbedtls_mpi_init(&e->one);
    mbedtls_ecp_point_init(&e->a);
    mbedtls_ecp_point_init(&e->b);
    mbedtls_ecp_point_init(&e->c);
    if (mbedtls_ecp_group_load(&e->g, MBEDTLS_ECP_DP_SECP256R1) != 0
        || mbedtls_mpi_lset(&e->one, 1) != 0) {
        return MOY_SEAL_ENOMEM;
    }
    return MOY_SEAL_OK;
}

static void ec_free(ec_t *e) {
    mbedtls_ecp_point_free(&e->a);
    mbedtls_ecp_point_free(&e->b);
    mbedtls_ecp_point_free(&e->c);
    mbedtls_mpi_free(&e->w);
    mbedtls_mpi_free(&e->s);
    mbedtls_mpi_free(&e->one);
    mbedtls_ecp_group_free(&e->g);
}

// out = p + k*q, k*q multiplied in constant time and the sum added in the
// clear (its scalars are 1); `neg` subtracts it instead.
static int add_mul(ec_t *e, mbedtls_ecp_point *out, const mbedtls_ecp_point *p,
                   const mbedtls_mpi *k, const mbedtls_ecp_point *q, int neg) {
    mbedtls_ecp_point kq;
    mbedtls_ecp_point_init(&kq);
    int r = mbedtls_ecp_mul(&e->g, &kq, k, q, rng, NULL);
    if (r == 0 && neg && mbedtls_mpi_cmp_int(&kq.MBEDTLS_PRIVATE(Y), 0) != 0) {
        r = mbedtls_mpi_sub_mpi(&kq.MBEDTLS_PRIVATE(Y), &e->g.P, &kq.MBEDTLS_PRIVATE(Y));
    }
    if (r == 0) {
        r = mbedtls_ecp_muladd(&e->g, out, &e->one, p, &e->one, &kq);
    }
    mbedtls_ecp_point_free(&kq);
    return r;
}

int moy_seal_spake_start(moy_seal_spake_t *s, int board, const uint8_t digest[32]) {
    static const char L[] = "moy1 spake2 w";
    uint8_t wide[40];
    memset(s, 0, sizeof *s);
    s->board = board ? 1 : 0;
    moy_seal_hkdf(NULL, 0, digest, 32, L, sizeof L - 1u, wide, sizeof wide);
    ec_t e;
    int r = ec_init(&e);
    size_t olen = 0;
    if (r == 0) {
        r = mbedtls_mpi_read_binary(&e.w, wide, sizeof wide);
    }
    if (r == 0) {
        r = mbedtls_mpi_mod_mpi(&e.w, &e.w, &e.g.N);
    }
    if (r == 0 && mbedtls_mpi_cmp_int(&e.w, 0) == 0) {
        r = mbedtls_mpi_lset(&e.w, 1);
    }
    if (r == 0) {
        r = mbedtls_ecp_gen_privkey(&e.g, &e.s, rng, NULL);
    }
    if (r == 0) {
        r = mbedtls_ecp_point_read_binary(&e.g, &e.b, board ? SPAKE_N : SPAKE_M, MOY_SEAL_POINT);
    }
    if (r == 0) {
        r = mbedtls_ecp_mul(&e.g, &e.a, &e.s, &e.g.G, rng, NULL);
    }
    if (r == 0) {
        r = add_mul(&e, &e.c, &e.a, &e.w, &e.b, 0);
    }
    if (r == 0) {
        r = mbedtls_ecp_point_write_binary(&e.g, &e.c, MBEDTLS_ECP_PF_UNCOMPRESSED, &olen,
                                           s->mine, sizeof s->mine);
    }
    if (r == 0) {
        r = mbedtls_mpi_write_binary(&e.s, s->s, sizeof s->s);
    }
    if (r == 0) {
        r = mbedtls_mpi_write_binary(&e.w, s->w, sizeof s->w);
    }
    ec_free(&e);
    moy_seal_wipe(wide, sizeof wide);
    if (r != 0 || olen != MOY_SEAL_POINT) {
        moy_seal_wipe(s, sizeof *s);
        return MOY_SEAL_ENOMEM;
    }
    return MOY_SEAL_OK;
}

static size_t tt_put(uint8_t *tt, size_t k, const void *p, size_t n) {
    for (int i = 0; i < 8; i++) {
        tt[k + (size_t)i] = (uint8_t)((uint64_t)n >> (8 * i));
    }
    memcpy(tt + k + 8, p, n);
    return k + 8 + n;
}

int moy_seal_spake_end(moy_seal_spake_t *s, const moy_seal_hs_t *h,
                       const uint8_t peer[MOY_SEAL_POINT], uint8_t ke[MOY_SEAL_KEY],
                       uint8_t ca[MOY_SEAL_MAC], uint8_t cb[MOY_SEAL_MAC]) {
    static const char LA[] = "moy1 browser", LB[] = "moy1 board";
    uint8_t k[MOY_SEAL_POINT];
    size_t olen = 0;
    ec_t e;
    int r = ec_init(&e);
    if (r != 0) {
        ec_free(&e);
        return r;
    }
    int bad = mbedtls_ecp_point_read_binary(&e.g, &e.a, peer, MOY_SEAL_POINT) != 0
              || mbedtls_ecp_check_pubkey(&e.g, &e.a) != 0;
    if (!bad) {
        r = mbedtls_mpi_read_binary(&e.w, s->w, sizeof s->w);
        if (r == 0) {
            r = mbedtls_mpi_read_binary(&e.s, s->s, sizeof s->s);
        }
        if (r == 0) {
            r = mbedtls_ecp_point_read_binary(&e.g, &e.b, s->board ? SPAKE_M : SPAKE_N,
                                              MOY_SEAL_POINT);
        }
        if (r == 0) {
            r = add_mul(&e, &e.c, &e.a, &e.w, &e.b, 1);   // the peer's point unmasked
        }
        if (r == 0 && mbedtls_ecp_is_zero(&e.c)) {
            bad = 1;
        }
        if (r == 0 && !bad) {
            r = mbedtls_ecp_mul(&e.g, &e.b, &e.s, &e.c, rng, NULL);
        }
        if (r == 0 && !bad && mbedtls_ecp_is_zero(&e.b)) {
            bad = 1;
        }
        if (r == 0 && !bad) {
            r = mbedtls_ecp_point_write_binary(&e.g, &e.b, MBEDTLS_ECP_PF_UNCOMPRESSED, &olen,
                                               k, sizeof k);
        }
    }
    ec_free(&e);
    if (bad) {
        return MOY_SEAL_EPOINT;
    }
    if (r != 0 || olen != MOY_SEAL_POINT) {
        return MOY_SEAL_ENOMEM;
    }
    uint8_t ida[sizeof LA - 1 + MOY_SEAL_RAND], idb[sizeof LB - 1 + 2 * MOY_SEAL_RAND];
    memcpy(ida, LA, sizeof LA - 1);
    memcpy(ida + sizeof LA - 1, h->nc, MOY_SEAL_RAND);
    memcpy(idb, LB, sizeof LB - 1);
    memcpy(idb + sizeof LB - 1, h->id, MOY_SEAL_RAND);
    memcpy(idb + sizeof LB - 1 + MOY_SEAL_RAND, h->nb, MOY_SEAL_RAND);
    uint8_t tt[6 * 8 + sizeof ida + sizeof idb + 3 * MOY_SEAL_POINT + 32];
    size_t n = 0;
    n = tt_put(tt, n, ida, sizeof ida);
    n = tt_put(tt, n, idb, sizeof idb);
    n = tt_put(tt, n, s->board ? peer : s->mine, MOY_SEAL_POINT);
    n = tt_put(tt, n, s->board ? s->mine : peer, MOY_SEAL_POINT);
    n = tt_put(tt, n, k, MOY_SEAL_POINT);
    n = tt_put(tt, n, s->w, sizeof s->w);
    uint8_t hash[32], kc[32];
    moy_seal_sha256(tt, n, hash);
    memcpy(ke, hash, MOY_SEAL_KEY);
    moy_seal_hkdf(NULL, 0, hash + 16, 16, "ConfirmationKeys", 16, kc, sizeof kc);
    moy_seal_hmac(kc, 16, tt, n, ca);
    moy_seal_hmac(kc + 16, 16, tt, n, cb);
    moy_seal_wipe(k, sizeof k);
    moy_seal_wipe(tt, sizeof tt);
    moy_seal_wipe(hash, sizeof hash);
    moy_seal_wipe(kc, sizeof kc);
    moy_seal_wipe(s->s, sizeof s->s);
    return MOY_SEAL_OK;
}

// -- the seals --------------------------------------------------------------------------

int moy_seal_gcm(int seal, const uint8_t key[MOY_SEAL_KEY], int kind, uint32_t index,
                 uint64_t seq, const void *aad, size_t an, uint8_t *p, size_t n,
                 uint8_t tag[MOY_SEAL_TAG]) {
    if (index > MOY_SEAL_INDEX_MAX) {
        return MOY_SEAL_EBAD;
    }
    uint8_t iv[12];
    iv[0] = (uint8_t)kind;
    iv[1] = (uint8_t)(index >> 16);
    iv[2] = (uint8_t)(index >> 8);
    iv[3] = (uint8_t)index;
    for (int i = 0; i < 8; i++) {
        iv[4 + i] = (uint8_t)(seq >> (56 - 8 * i));
    }
    mbedtls_gcm_context g;
    mbedtls_gcm_init(&g);
    int r = mbedtls_gcm_setkey(&g, MBEDTLS_CIPHER_ID_AES, key, 128);
    if (r == 0) {
        r = seal ? mbedtls_gcm_crypt_and_tag(&g, MBEDTLS_GCM_ENCRYPT, n, iv, sizeof iv, aad, an,
                                             p, p, MOY_SEAL_TAG, tag)
                 : mbedtls_gcm_auth_decrypt(&g, n, iv, sizeof iv, aad, an, tag, MOY_SEAL_TAG,
                                            p, p);
    }
    mbedtls_gcm_free(&g);
    if (r != 0) {
        moy_seal_wipe(p, n);
        return MOY_SEAL_EBAD;
    }
    return MOY_SEAL_OK;
}

#define AAD_MAX 640

int moy_seal_req(int seal, const uint8_t kc[MOY_SEAL_KEY], uint64_t seq, const char *method,
                 size_t mn, const char *target, size_t tn, uint8_t *p, size_t n,
                 uint8_t tag[MOY_SEAL_TAG]) {
    static const char L[] = "moy1 req";
    uint8_t aad[AAD_MAX];
    if (sizeof L + mn + 1u + tn > sizeof aad) {
        return MOY_SEAL_EBAD;
    }
    memcpy(aad, L, sizeof L);
    memcpy(aad + sizeof L, method, mn);
    aad[sizeof L + mn] = 0;
    memcpy(aad + sizeof L + mn + 1u, target, tn);
    return moy_seal_gcm(seal, kc, MOY_SEAL_REQ, 0, seq, aad, sizeof L + mn + 1u + tn, p, n, tag);
}

int moy_seal_rec(int seal, const uint8_t kb[MOY_SEAL_KEY], uint64_t seq, uint32_t index,
                 int status, int last, uint8_t head[MOY_SEAL_HEAD], uint8_t *p, size_t n,
                 uint8_t tag[MOY_SEAL_TAG]) {
    static const char L[] = "moy1 res";
    if (n >= MOY_SEAL_LAST || (last && n != 0)) {
        return MOY_SEAL_EBAD;
    }
    if (seal) {
        uint32_t v = (uint32_t)n | (last ? MOY_SEAL_LAST : 0u);
        head[0] = (uint8_t)(v >> 24);
        head[1] = (uint8_t)(v >> 16);
        head[2] = (uint8_t)(v >> 8);
        head[3] = (uint8_t)v;
    }
    uint8_t aad[sizeof L + 2 + MOY_SEAL_HEAD];
    memcpy(aad, L, sizeof L);
    aad[sizeof L] = (uint8_t)(status >> 8);
    aad[sizeof L + 1] = (uint8_t)status;
    memcpy(aad + sizeof L + 2, head, MOY_SEAL_HEAD);
    return moy_seal_gcm(seal, kb, MOY_SEAL_RES, index, seq, aad, sizeof aad, p, n, tag);
}

int moy_seal_ws(int seal, const uint8_t key[MOY_SEAL_KEY], int kind, uint32_t index,
                uint64_t seq, uint8_t *p, size_t n, uint8_t tag[MOY_SEAL_TAG]) {
    static const char L[] = "moy1 ws";
    return moy_seal_gcm(seal, key, kind, index, seq, L, sizeof L, p, n, tag);
}
