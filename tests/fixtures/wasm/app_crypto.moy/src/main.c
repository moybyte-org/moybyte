// Crypto Wasm: a compiled app in C calling the crypto rows through its
// moybyte.app imports (docs/app_api_v1.md section 10), with no permission:
// they are pure computation. Built by tools/wasm_cart.py with wasi-sdk as a
// reactor. At _init it writes each answer to the OS log, as hex or its
// length, or the app ABI's code negated where it fails (FULL 2, BAD 7,
// FORGED 10): FIPS 180-2's "abc", RFC 4231's case 2, the GCM spec's test
// case 3 opened and changed, a seal opened, RFC 7748 section 6.1's keys, a
// peer key of small order, RFC 8032 section 7.1's Ed25519 TEST 3 (its
// public key and signature, checked, then a changed message answering 0, a
// short signature BAD, a short buffer FULL). tests/test_crypto_rows.py reads
// the lines.

#include <stdint.h>

#include "moybyte/os.h"

#define APP(name) __attribute__((import_module("moybyte.app"), import_name(#name)))
#define MOY(name) __attribute__((import_module("moy"), import_name(#name)))
#define EXPORT(name) __attribute__((export_name(#name)))

int32_t moybyte_random(int32_t n, uint8_t *data, uint32_t cap) APP(moybyte_random);
int32_t moybyte_sha256(const void *data, uint32_t n, uint8_t *digest, uint32_t cap)
    APP(moybyte_sha256);
int32_t moybyte_hmac_sha256(const void *key, uint32_t kn, const void *data, uint32_t n,
                            uint8_t *mac, uint32_t cap) APP(moybyte_hmac_sha256);
int32_t moybyte_aes_gcm_seal(const void *key, uint32_t kn, const void *plain, uint32_t n,
                             uint8_t *sealed, uint32_t cap) APP(moybyte_aes_gcm_seal);
int32_t moybyte_aes_gcm_open(const void *key, uint32_t kn, const void *sealed, uint32_t n,
                             uint8_t *plain, uint32_t cap) APP(moybyte_aes_gcm_open);
int32_t moybyte_x25519(const void *secret, uint32_t sn, const void *peer, uint32_t pn,
                       uint8_t *shared, uint32_t cap) APP(moybyte_x25519);
int32_t moybyte_ed25519_public(const void *secret, uint32_t sn, uint8_t *pub, uint32_t cap)
    APP(moybyte_ed25519_public);
int32_t moybyte_ed25519_sign(const void *secret, uint32_t sn, const void *msg, uint32_t n,
                             uint8_t *sig, uint32_t cap) APP(moybyte_ed25519_sign);
int32_t moybyte_ed25519_verify(const void *pub, uint32_t pn, const void *msg, uint32_t n,
                               const void *sig, uint32_t gn) APP(moybyte_ed25519_verify);
int32_t moybyte_log(int32_t level, const char *text, uint32_t text_n) APP(moybyte_log);
void cls(int32_t c) MOY(cls);

static uint32_t len(const char *s) {
    uint32_t n = 0;
    while (s[n]) {
        n++;
    }
    return n;
}

static int nib(char c) {
    return c <= '9' ? c - '0' : c - 'a' + 10;
}

static uint32_t unhex(const char *s, uint8_t *o) {
    uint32_t n = len(s) / 2u;
    for (uint32_t i = 0; i < n; i++) {
        o[i] = (uint8_t)(nib(s[2 * i]) << 4 | nib(s[2 * i + 1]));
    }
    return n;
}

static char line[256];
static uint32_t at;

static void put(const char *s) {
    for (uint32_t i = 0; s[i] && at < sizeof line - 1; i++) {
        line[at++] = s[i];
    }
}

// `what`, then the answer: the bytes as hex where `buf` is given, else the number.
static void said(const char *what, int32_t r, const uint8_t *buf) {
    static const char D[] = "0123456789abcdef";
    put(what);
    put(" ");
    if (r >= 0 && buf != 0) {
        for (int32_t i = 0; i < r && at < sizeof line - 2; i++) {
            line[at++] = D[buf[i] >> 4];
            line[at++] = D[buf[i] & 15];
        }
    } else {
        char d[12];
        int k = 0;
        uint32_t u = r < 0 ? (uint32_t)-r : (uint32_t)r;
        do {
            d[k++] = (char)('0' + u % 10u);
            u /= 10u;
        } while (u);
        if (r < 0) {
            put("-");
        }
        while (k) {
            line[at++] = d[--k];
        }
    }
    moybyte_log(MOY_LOG_INFO, line, at);
    at = 0;
}

static uint8_t buf[128], key[32], sealed[128], a[32], b[32], sig[64], msg[2];

EXPORT(_init) void init(void) {
    said("random", moybyte_random(16, buf, sizeof buf), 0);
    said("random.short", moybyte_random(8, buf, 4), 0);
    said("sha256", moybyte_sha256("abc", 3, buf, sizeof buf), buf);
    said("sha256.short", moybyte_sha256("abc", 3, buf, 31), 0);
    said("hmac_sha256", moybyte_hmac_sha256("Jefe", 4, "what do ya want for nothing?", 28,
                                            buf, sizeof buf), buf);
    uint32_t kn = unhex("feffe9928665731c6d6a8f9467308308", key);
    uint32_t n = unhex("cafebabefacedbaddecaf888"
                       "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
                       "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985"
                       "4d5c2af327cd64a62cf35abd2ba6fab4", sealed);
    said("aes_gcm_open", moybyte_aes_gcm_open(key, kn, sealed, n, buf, sizeof buf), buf);
    said("aes_gcm_open.key", moybyte_aes_gcm_open(key, 15, sealed, n, buf, sizeof buf), 0);
    sealed[20] ^= 1;
    said("aes_gcm_open.changed", moybyte_aes_gcm_open(key, kn, sealed, n, buf, sizeof buf), 0);
    int32_t r = moybyte_aes_gcm_seal(key, kn, "moybyte", 7, sealed, sizeof sealed);
    said("aes_gcm_seal", r, 0);
    said("aes_gcm_seal.opened",
         moybyte_aes_gcm_open(key, kn, sealed, r > 0 ? (uint32_t)r : 0u, buf, sizeof buf), buf);
    unhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a", a);
    unhex("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f", b);
    said("x25519.public", moybyte_x25519(a, 32, 0, 0, buf, sizeof buf), buf);
    said("x25519.shared", moybyte_x25519(a, 32, b, 32, buf, sizeof buf), buf);
    for (int i = 0; i < 32; i++) {
        b[i] = 0;
    }
    said("x25519.small", moybyte_x25519(a, 32, b, 32, buf, sizeof buf), 0);
    unhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7", a);
    unhex("af82", msg);
    said("ed25519.public", moybyte_ed25519_public(a, 32, b, sizeof b), b);
    said("ed25519.sign", moybyte_ed25519_sign(a, 32, msg, 2, sig, sizeof sig), sig);
    said("ed25519.verify", moybyte_ed25519_verify(b, 32, msg, 2, sig, 64), 0);
    said("ed25519.verify.changed", moybyte_ed25519_verify(b, 32, msg, 1, sig, 64), 0);
    said("ed25519.verify.sig", moybyte_ed25519_verify(b, 32, msg, 2, sig, 63), 0);
    said("ed25519.sign.short", moybyte_ed25519_sign(a, 32, msg, 2, buf, 63), 0);
}

EXPORT(_update) void update(float dt) {
    (void)dt;
}

EXPORT(_draw) void draw(void) {
    cls(1);
}
