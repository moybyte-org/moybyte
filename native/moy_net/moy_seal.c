// moy_seal: moy_seal.h is the authority (the protocol, its labels and nonces).

// A board is told by the header it has: ESP_PLATFORM is not defined for a
// usermod's sources on the esp32 port (moy_net.h's MOY_NET_CLOCK_EPOCH).
#if defined(ESP_PLATFORM) || __has_include("esp_random.h")
#define SEAL_BOARD 1
#elif !defined(_DEFAULT_SOURCE)
#define _DEFAULT_SOURCE                 // getentropy
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_seal.h"

#ifdef SEAL_BOARD
#include <sys/lock.h>
#include "bootloader_random.h"
#include "esp_random.h"
#include "esp_rom_sys.h"
#else
#include <pthread.h>
#include <unistd.h>
#endif

#include "mbedtls/ecp.h"
#include "mbedtls/gcm.h"
#include "mbedtls/hmac_drbg.h"
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"

// RFC 9382's M and N for P-256 (RFC 9383's too), uncompressed.
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

// -- the random source (moy_seal.h) ----------------------------------------------------

// The hardware's bytes: a board's RNG register, the host's and the browser's
// getentropy.
static void hw_random(uint8_t *b, size_t n) {
    #ifdef SEAL_BOARD
    esp_fill_random(b, n);
    #else
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

static int hw_entropy(void *ctx, unsigned char *p, size_t n) {
    (void)ctx;
    hw_random(p, n);
    return 0;
}

#define SEED_WORDS 64                   // the boot's draw: 256 bytes
#define SEED_GAP_US 40                  // a word every 40 us, many ADC samples apart
#define ADD_BYTES 16                    // the hardware's bytes mixed into every draw

static uint8_t s_seed[SEED_WORDS * 4];
static mbedtls_hmac_drbg_context s_drbg;
static int s_drbg_on;

#ifdef SEAL_BOARD
// Before app_main, so before anything starts the radio or an ADC unit: the
// SAR ADC's noise mixed into the RNG while the boot's seed is read, then the
// ADC handed back as the bootloader hands it over.
__attribute__((constructor)) static void boot_seed(void) {
    bootloader_random_enable();
    for (int i = 0; i < SEED_WORDS; i++) {
        esp_rom_delay_us(SEED_GAP_US);
        uint32_t w = esp_random();
        memcpy(s_seed + 4 * i, &w, 4);
    }
    bootloader_random_disable();
}

static _lock_t s_lock;
#define RNG_LOCK() _lock_acquire(&s_lock)
#define RNG_UNLOCK() _lock_release(&s_lock)
#else
static pthread_mutex_t s_lock = PTHREAD_MUTEX_INITIALIZER;
#define RNG_LOCK() pthread_mutex_lock(&s_lock)
#define RNG_UNLOCK() pthread_mutex_unlock(&s_lock)
#endif

void moy_seal_random(void *p, size_t n) {
    uint8_t *b = p, add[ADD_BYTES];
    RNG_LOCK();
    if (!s_drbg_on) {
        #ifndef SEAL_BOARD
        hw_random(s_seed, sizeof s_seed);
        #endif
        mbedtls_hmac_drbg_init(&s_drbg);
        if (mbedtls_hmac_drbg_seed(&s_drbg, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),
                                   hw_entropy, NULL, s_seed, sizeof s_seed) != 0) {
            abort();                    // never a key from nothing
        }
        moy_seal_wipe(s_seed, sizeof s_seed);
        s_drbg_on = 1;
    }
    while (n > 0) {
        size_t k = n < MBEDTLS_HMAC_DRBG_MAX_REQUEST ? n : MBEDTLS_HMAC_DRBG_MAX_REQUEST;
        hw_random(add, sizeof add);
        if (mbedtls_hmac_drbg_random_with_add(&s_drbg, b, k, add, sizeof add) != 0) {
            abort();
        }
        b += k;
        n -= k;
    }
    RNG_UNLOCK();
    moy_seal_wipe(add, sizeof add);
}

// -- the primitives --------------------------------------------------------------------

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

// -- the password: its record and SPAKE2+ over P-256 ---------------------------------------

static const char SPAKE_CONTEXT[] = "moy2 SPAKE2+-P256-SHA256-HKDF-SHA256-HMAC-SHA256";
static const char PRE[] = "$moy2$";

// PBKDF2-HMAC-SHA256 (RFC 8018) of one 32-byte block: the key's inner and
// outer pads hashed once, every round's two hashes resumed from them, so a
// round costs this end what it costs a guesser: two SHA-256 blocks.
static void pbkdf2(const char *pw, size_t pn, const char *salt, size_t sn, uint32_t iter,
                   uint8_t out[32]) {
    uint8_t k[64], u[32];
    static const uint8_t one[4] = {0, 0, 0, 1};
    memset(k, 0, sizeof k);
    if (pn > sizeof k) {
        moy_seal_sha256(pw, pn, k);
    } else {
        memcpy(k, pw, pn);
    }
    mbedtls_sha256_context ip, op, c;
    mbedtls_sha256_init(&ip);
    mbedtls_sha256_init(&op);
    mbedtls_sha256_init(&c);
    for (int i = 0; i < 64; i++) {
        k[i] ^= 0x36;
    }
    mbedtls_sha256_starts(&ip, 0);
    mbedtls_sha256_update(&ip, k, sizeof k);
    for (int i = 0; i < 64; i++) {
        k[i] ^= 0x36 ^ 0x5c;
    }
    mbedtls_sha256_starts(&op, 0);
    mbedtls_sha256_update(&op, k, sizeof k);
    mbedtls_sha256_clone(&c, &ip);
    mbedtls_sha256_update(&c, (const uint8_t *)salt, sn);
    mbedtls_sha256_update(&c, one, sizeof one);
    mbedtls_sha256_finish(&c, u);
    mbedtls_sha256_clone(&c, &op);
    mbedtls_sha256_update(&c, u, sizeof u);
    mbedtls_sha256_finish(&c, u);
    memcpy(out, u, 32);
    for (uint32_t r = 1; r < iter; r++) {
        mbedtls_sha256_clone(&c, &ip);
        mbedtls_sha256_update(&c, u, sizeof u);
        mbedtls_sha256_finish(&c, u);
        mbedtls_sha256_clone(&c, &op);
        mbedtls_sha256_update(&c, u, sizeof u);
        mbedtls_sha256_finish(&c, u);
        for (int i = 0; i < 32; i++) {
            out[i] ^= u[i];
        }
    }
    mbedtls_sha256_free(&c);
    mbedtls_sha256_free(&ip);
    mbedtls_sha256_free(&op);
    moy_seal_wipe(k, sizeof k);
    moy_seal_wipe(u, sizeof u);
}

typedef struct {
    mbedtls_ecp_group g;
    mbedtls_mpi w0, w1, s, one;
    mbedtls_ecp_point p, q, t, z, v;
} ec_t;

static int ec_init(ec_t *e) {
    mbedtls_ecp_group_init(&e->g);
    mbedtls_mpi_init(&e->w0);
    mbedtls_mpi_init(&e->w1);
    mbedtls_mpi_init(&e->s);
    mbedtls_mpi_init(&e->one);
    mbedtls_ecp_point_init(&e->p);
    mbedtls_ecp_point_init(&e->q);
    mbedtls_ecp_point_init(&e->t);
    mbedtls_ecp_point_init(&e->z);
    mbedtls_ecp_point_init(&e->v);
    if (mbedtls_ecp_group_load(&e->g, MBEDTLS_ECP_DP_SECP256R1) != 0
        || mbedtls_mpi_lset(&e->one, 1) != 0) {
        return MOY_SEAL_ENOMEM;
    }
    return MOY_SEAL_OK;
}

static void ec_free(ec_t *e) {
    mbedtls_ecp_point_free(&e->p);
    mbedtls_ecp_point_free(&e->q);
    mbedtls_ecp_point_free(&e->t);
    mbedtls_ecp_point_free(&e->z);
    mbedtls_ecp_point_free(&e->v);
    mbedtls_mpi_free(&e->w0);
    mbedtls_mpi_free(&e->w1);
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

static int put_point(ec_t *e, const mbedtls_ecp_point *p, uint8_t out[MOY_SEAL_POINT]) {
    size_t olen = 0;
    int r = mbedtls_ecp_point_write_binary(&e->g, p, MBEDTLS_ECP_PF_UNCOMPRESSED, &olen, out,
                                           MOY_SEAL_POINT);
    return r == 0 && olen == MOY_SEAL_POINT ? 0 : -1;
}

// A share from a peer, or a record's L: on the curve, never the identity.
static int get_point(ec_t *e, mbedtls_ecp_point *p, const uint8_t in[MOY_SEAL_POINT]) {
    return mbedtls_ecp_point_read_binary(&e->g, p, in, MOY_SEAL_POINT) == 0
           && mbedtls_ecp_check_pubkey(&e->g, p) == 0 ? 0 : -1;
}

// w0 and w1 from the password: PBKDF2's key widened by HKDF to two 40-byte
// halves, each reduced mod the order (RFC 9383 section 3.2), a zero read as 1.
static int derive(ec_t *e, const char *pw, size_t pn, const char *salt, size_t sn,
                  uint32_t iter) {
    static const char L[] = "moy2 spake2+ w0 w1";
    uint8_t k[32], wide[80];
    pbkdf2(pw, pn, salt, sn, iter, k);
    moy_seal_hkdf(NULL, 0, k, sizeof k, L, sizeof L - 1u, wide, sizeof wide);
    int r = mbedtls_mpi_read_binary(&e->w0, wide, 40);
    if (r == 0) {
        r = mbedtls_mpi_mod_mpi(&e->w0, &e->w0, &e->g.N);
    }
    if (r == 0 && mbedtls_mpi_cmp_int(&e->w0, 0) == 0) {
        r = mbedtls_mpi_lset(&e->w0, 1);
    }
    if (r == 0) {
        r = mbedtls_mpi_read_binary(&e->w1, wide + 40, 40);
    }
    if (r == 0) {
        r = mbedtls_mpi_mod_mpi(&e->w1, &e->w1, &e->g.N);
    }
    if (r == 0 && mbedtls_mpi_cmp_int(&e->w1, 0) == 0) {
        r = mbedtls_mpi_lset(&e->w1, 1);
    }
    moy_seal_wipe(k, sizeof k);
    moy_seal_wipe(wide, sizeof wide);
    return r;
}

static int salt_ok(const char *salt, size_t sn) {
    if (sn == 0 || sn > MOY_SEAL_PW_SALT_MAX) {
        return 0;
    }
    for (size_t i = 0; i < sn; i++) {
        if (salt[i] <= ' ' || salt[i] > '~' || salt[i] == '$') {
            return 0;
        }
    }
    return 1;
}

int moy_seal_pw_derive(const char *pw, size_t pn, const char *salt, size_t sn, uint32_t iter,
                       uint8_t w0[32], uint8_t w1[32]) {
    if (!salt_ok(salt, sn) || iter < MOY_SEAL_PW_ITER_MIN || iter > MOY_SEAL_PW_ITER_MAX) {
        return MOY_SEAL_EBAD;
    }
    ec_t e;
    int r = ec_init(&e);
    if (r == 0) {
        r = derive(&e, pw, pn, salt, sn, iter);
    }
    if (r == 0) {
        r = mbedtls_mpi_write_binary(&e.w0, w0, 32);
    }
    if (r == 0) {
        r = mbedtls_mpi_write_binary(&e.w1, w1, 32);
    }
    ec_free(&e);
    return r == 0 ? MOY_SEAL_OK : MOY_SEAL_ENOMEM;
}

int moy_seal_pw_record(const char *pw, size_t pn, const char *salt, size_t sn, uint32_t iter,
                       char *out, size_t cap) {
    if (!salt_ok(salt, sn) || iter < MOY_SEAL_PW_ITER_MIN || iter > MOY_SEAL_PW_ITER_MAX
        || cap < MOY_SEAL_PW_RECORD) {
        return -1;
    }
    uint8_t w0[32], l[MOY_SEAL_POINT];
    ec_t e;
    int r = ec_init(&e);
    if (r == 0) {
        r = derive(&e, pw, pn, salt, sn, iter);
    }
    if (r == 0) {
        r = mbedtls_ecp_mul(&e.g, &e.p, &e.w1, &e.g.G, rng, NULL);
    }
    if (r == 0) {
        r = mbedtls_mpi_write_binary(&e.w0, w0, sizeof w0);
    }
    if (r == 0) {
        r = put_point(&e, &e.p, l);
    }
    ec_free(&e);
    if (r != 0) {
        moy_seal_wipe(w0, sizeof w0);
        return -1;
    }
    int n = snprintf(out, cap, "%s%u$%.*s$", PRE, (unsigned)iter, (int)sn, salt);
    moy_seal_hex(w0, sizeof w0, out + n);
    n += 2 * (int)sizeof w0;
    out[n++] = '$';
    moy_seal_hex(l, sizeof l, out + n);
    moy_seal_wipe(w0, sizeof w0);
    return n + 2 * (int)sizeof l;
}

int moy_seal_pw_parse(const char *text, size_t n, uint32_t *iter, char *salt, size_t salt_cap,
                      uint8_t w0[32], uint8_t l[MOY_SEAL_POINT]) {
    if (text == NULL || n < sizeof PRE - 1u || memcmp(text, PRE, sizeof PRE - 1u) != 0) {
        return 0;
    }
    const char *p = text + sizeof PRE - 1u, *end = text;
    while (end < text + n && *end != '\n' && *end != '\r') {
        end++;
    }
    uint32_t it = 0;
    const char *d = p;
    while (d < end && *d >= '0' && *d <= '9' && d - p < 8) {
        it = it * 10u + (uint32_t)(*d - '0');
        d++;
    }
    if (d == p || *p == '0' || d >= end || *d != '$' || it < MOY_SEAL_PW_ITER_MIN
        || it > MOY_SEAL_PW_ITER_MAX) {
        return 0;
    }
    const char *s = d + 1;
    const char *sd = memchr(s, '$', (size_t)(end - s));
    if (sd == NULL || !salt_ok(s, (size_t)(sd - s)) || (size_t)(sd - s) + 1u > salt_cap) {
        return 0;
    }
    const char *h = sd + 1;
    if (end - h != 2 * 32 + 1 + 2 * MOY_SEAL_POINT || h[2 * 32] != '$'
        || !moy_seal_unhex(h, 2 * 32, w0, 32)
        || !moy_seal_unhex(h + 2 * 32 + 1, 2 * MOY_SEAL_POINT, l, MOY_SEAL_POINT)) {
        return 0;
    }
    *iter = it;
    memcpy(salt, s, (size_t)(sd - s));
    salt[sd - s] = '\0';
    return 1;
}

int moy_seal_pw_check(const char *pw, size_t pn, const char *text, size_t n) {
    uint32_t iter;
    char salt[MOY_SEAL_PW_SALT_MAX + 1];
    uint8_t w0[32], l[MOY_SEAL_POINT], mine[32], w1[32];
    if (!moy_seal_pw_parse(text, n, &iter, salt, sizeof salt, w0, l)) {
        return 0;
    }
    int ok = moy_seal_pw_derive(pw, pn, salt, strlen(salt), iter, mine, w1) == MOY_SEAL_OK
             && moy_seal_eq(mine, w0, sizeof w0);
    moy_seal_wipe(mine, sizeof mine);
    moy_seal_wipe(w1, sizeof w1);
    moy_seal_wipe(w0, sizeof w0);
    return ok;
}

// This side's share: s*G + w0*M (the prover's) or s*G + w0*N (the verifier's),
// s the given scalar or a fresh one.
static int share(moy_seal_spake_t *s, const uint8_t *scalar) {
    ec_t e;
    int r = ec_init(&e);
    if (r == 0) {
        r = mbedtls_mpi_read_binary(&e.w0, s->w0, sizeof s->w0);
    }
    if (r == 0 && s->board && get_point(&e, &e.q, s->l) != 0) {
        ec_free(&e);
        return MOY_SEAL_EPOINT;
    }
    if (r == 0) {
        r = scalar != NULL ? mbedtls_mpi_read_binary(&e.s, scalar, 32)
                           : mbedtls_ecp_gen_privkey(&e.g, &e.s, rng, NULL);
    }
    if (r == 0) {
        r = mbedtls_ecp_point_read_binary(&e.g, &e.q, s->board ? SPAKE_N : SPAKE_M,
                                          MOY_SEAL_POINT);
    }
    if (r == 0) {
        r = mbedtls_ecp_mul(&e.g, &e.p, &e.s, &e.g.G, rng, NULL);
    }
    if (r == 0) {
        r = add_mul(&e, &e.t, &e.p, &e.w0, &e.q, 0);
    }
    if (r == 0) {
        r = put_point(&e, &e.t, s->mine);
    }
    if (r == 0) {
        r = mbedtls_mpi_write_binary(&e.s, s->s, sizeof s->s);
    }
    ec_free(&e);
    if (r != 0) {
        moy_seal_wipe(s, sizeof *s);
        return MOY_SEAL_ENOMEM;
    }
    return MOY_SEAL_OK;
}

int moy_seal_spake_prover(moy_seal_spake_t *s, const uint8_t w0[32], const uint8_t w1[32],
                          const uint8_t *x) {
    memset(s, 0, sizeof *s);
    memcpy(s->w0, w0, sizeof s->w0);
    memcpy(s->w1, w1, sizeof s->w1);
    return share(s, x);
}

int moy_seal_spake_verifier(moy_seal_spake_t *s, const uint8_t w0[32],
                            const uint8_t l[MOY_SEAL_POINT], const uint8_t *y) {
    memset(s, 0, sizeof *s);
    s->board = 1;
    memcpy(s->w0, w0, sizeof s->w0);
    memcpy(s->l, l, sizeof s->l);
    return share(s, y);
}

static size_t tt_put(uint8_t *tt, size_t k, const void *p, size_t n) {
    for (int i = 0; i < 8; i++) {
        tt[k + (size_t)i] = (uint8_t)((uint64_t)n >> (8 * i));
    }
    memcpy(tt + k + 8, p, n);
    return k + 8 + n;
}

#define TT_ID_MAX 64

int moy_seal_spake_keys(moy_seal_spake_t *s, const void *context, size_t cn, const void *idp,
                        size_t pn, const void *idv, size_t vn, const uint8_t peer[MOY_SEAL_POINT],
                        uint8_t shared[32], uint8_t cp[MOY_SEAL_MAC], uint8_t cv[MOY_SEAL_MAC]) {
    if (cn > TT_ID_MAX || pn > TT_ID_MAX || vn > TT_ID_MAX) {
        return MOY_SEAL_EBAD;
    }
    uint8_t z[MOY_SEAL_POINT], v[MOY_SEAL_POINT];
    ec_t e;
    int r = ec_init(&e), bad = 0;
    if (r == 0) {
        bad = get_point(&e, &e.p, peer) != 0;
    }
    if (r == 0 && !bad) {
        r = mbedtls_mpi_read_binary(&e.w0, s->w0, sizeof s->w0);
        if (r == 0) {
            r = mbedtls_mpi_read_binary(&e.s, s->s, sizeof s->s);
        }
        if (r == 0) {
            r = mbedtls_ecp_point_read_binary(&e.g, &e.q, s->board ? SPAKE_M : SPAKE_N,
                                              MOY_SEAL_POINT);
        }
        if (r == 0) {
            r = add_mul(&e, &e.t, &e.p, &e.w0, &e.q, 1);   // the peer's share unmasked
        }
        if (r == 0 && mbedtls_ecp_is_zero(&e.t)) {
            bad = 1;
        }
        if (r == 0 && !bad) {
            r = mbedtls_ecp_mul(&e.g, &e.z, &e.s, &e.t, rng, NULL);
        }
        if (r == 0 && !bad) {
            if (s->board) {
                r = mbedtls_ecp_point_read_binary(&e.g, &e.q, s->l, MOY_SEAL_POINT);
                if (r == 0) {
                    r = mbedtls_ecp_mul(&e.g, &e.v, &e.s, &e.q, rng, NULL);
                }
            } else {
                r = mbedtls_mpi_read_binary(&e.w1, s->w1, sizeof s->w1);
                if (r == 0) {
                    r = mbedtls_ecp_mul(&e.g, &e.v, &e.w1, &e.t, rng, NULL);
                }
            }
        }
        if (r == 0 && !bad && (mbedtls_ecp_is_zero(&e.z) || mbedtls_ecp_is_zero(&e.v))) {
            bad = 1;
        }
        if (r == 0 && !bad && (put_point(&e, &e.z, z) != 0 || put_point(&e, &e.v, v) != 0)) {
            r = -1;
        }
    }
    ec_free(&e);
    if (bad) {
        moy_seal_wipe(z, sizeof z);
        return MOY_SEAL_EPOINT;
    }
    if (r != 0) {
        return MOY_SEAL_ENOMEM;
    }
    const uint8_t *x = s->board ? peer : s->mine, *y = s->board ? s->mine : peer;
    uint8_t tt[10 * 8 + 3 * TT_ID_MAX + 6 * MOY_SEAL_POINT + 32];
    size_t n = 0;
    n = tt_put(tt, n, context, cn);
    n = tt_put(tt, n, idp, pn);
    n = tt_put(tt, n, idv, vn);
    n = tt_put(tt, n, SPAKE_M, MOY_SEAL_POINT);
    n = tt_put(tt, n, SPAKE_N, MOY_SEAL_POINT);
    n = tt_put(tt, n, x, MOY_SEAL_POINT);
    n = tt_put(tt, n, y, MOY_SEAL_POINT);
    n = tt_put(tt, n, z, MOY_SEAL_POINT);
    n = tt_put(tt, n, v, MOY_SEAL_POINT);
    n = tt_put(tt, n, s->w0, sizeof s->w0);
    uint8_t main_key[32], kc[64];
    moy_seal_sha256(tt, n, main_key);
    moy_seal_hkdf(NULL, 0, main_key, sizeof main_key, "ConfirmationKeys", 16, kc, sizeof kc);
    moy_seal_hkdf(NULL, 0, main_key, sizeof main_key, "SharedKey", 9, shared, 32);
    moy_seal_hmac(kc, 32, y, MOY_SEAL_POINT, cp);
    moy_seal_hmac(kc + 32, 32, x, MOY_SEAL_POINT, cv);
    moy_seal_wipe(z, sizeof z);
    moy_seal_wipe(v, sizeof v);
    moy_seal_wipe(tt, sizeof tt);
    moy_seal_wipe(main_key, sizeof main_key);
    moy_seal_wipe(kc, sizeof kc);
    moy_seal_wipe(s->s, sizeof s->s);
    return MOY_SEAL_OK;
}

int moy_seal_spake_end(moy_seal_spake_t *s, const moy_seal_hs_t *h,
                       const uint8_t peer[MOY_SEAL_POINT], uint8_t ke[MOY_SEAL_KEY],
                       uint8_t ca[MOY_SEAL_MAC], uint8_t cb[MOY_SEAL_MAC]) {
    static const char LA[] = "moy1 browser", LB[] = "moy1 board";
    uint8_t ida[sizeof LA - 1 + MOY_SEAL_RAND], idb[sizeof LB - 1 + 2 * MOY_SEAL_RAND];
    memcpy(ida, LA, sizeof LA - 1);
    memcpy(ida + sizeof LA - 1, h->nc, MOY_SEAL_RAND);
    memcpy(idb, LB, sizeof LB - 1);
    memcpy(idb + sizeof LB - 1, h->id, MOY_SEAL_RAND);
    memcpy(idb + sizeof LB - 1 + MOY_SEAL_RAND, h->nb, MOY_SEAL_RAND);
    uint8_t shared[32];
    int r = moy_seal_spake_keys(s, SPAKE_CONTEXT, sizeof SPAKE_CONTEXT - 1u, ida, sizeof ida, idb,
                                sizeof idb, peer, shared, ca, cb);
    memcpy(ke, shared, MOY_SEAL_KEY);
    moy_seal_wipe(shared, sizeof shared);
    return r;
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
    // A board's GCM is ESP-IDF's (MBEDTLS_GCM_ALT), which refuses a NULL
    // input even at length 0: an empty record's seal never wrote its tag.
    uint8_t none[1];
    if (p == NULL) {
        p = none;
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
