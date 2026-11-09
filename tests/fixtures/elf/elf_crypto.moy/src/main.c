// crypto: the crypto rows (docs/app_api_v1.md section 10) from C, each answer
// printed as its first 8 bytes in hex (a line the terminal does not wrap) or
// its length, or with its errno where it fails: FIPS 180-2's
// "abc", RFC 4231's case 2, the GCM spec's test case 3 opened (the nonce
// first, no AAD), a seal opened, RFC 7748 section 6.1's keys, RFC 8032
// section 7.1's Ed25519 TEST 3 (signed, checked, then a changed message and
// another key answering 0); then a buffer too short (ERANGE), a length
// refused (EINVAL), a changed byte (EBADMSG) and a peer key of small order
// (EINVAL). tests/fixtures/wasm/wasi_crypto.moy
// builds this same file for the WASI tier; tests/crypto_vectors.py holds the
// lines both print.
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "moybyte/os.h"

static size_t unhex(const char *s, uint8_t *o) {
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++) {
        unsigned v;
        sscanf(s + 2 * i, "%2x", &v);
        o[i] = (uint8_t)v;
    }
    return n;
}

static const char *err(void) {
    return errno == ERANGE ? "ERANGE" : errno == EINVAL ? "EINVAL"
           : errno == EBADMSG ? "EBADMSG" : errno == ENOENT ? "ENOENT" : "other";
}

static void said(const char *what, int32_t r, const uint8_t *buf) {
    if (r < 0) {
        printf("%s -1 %s\n", what, err());
        return;
    }
    printf("%s", what);
    if (buf == NULL) {
        printf(" %d\n", (int)r);
        return;
    }
    printf(" ");
    for (int32_t i = 0; i < r && i < 8; i++) {
        printf("%02x", buf[i]);
    }
    printf("\n");
}

int main(void) {
    static uint8_t buf[128], a[32], b[32], key[32], sealed[128], plain[64];
    said("random", moy_random(16, buf, sizeof buf), NULL);
    said("random.max", moy_random(4097, buf, sizeof buf), NULL);
    said("random.short", moy_random(8, buf, 4), NULL);
    said("sha256", moy_sha256("abc", 3, buf, sizeof buf), buf);
    said("sha256.short", moy_sha256("abc", 3, buf, 31), NULL);
    said("hmac_sha256", moy_hmac_sha256("Jefe", 4, "what do ya want for nothing?", 28, buf,
                                        sizeof buf), buf);
    size_t kn = unhex("feffe9928665731c6d6a8f9467308308", key);
    size_t n = unhex("cafebabefacedbaddecaf888"
                     "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
                     "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985"
                     "4d5c2af327cd64a62cf35abd2ba6fab4", sealed);
    unhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
          "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255", plain);
    int32_t r = moy_aes_gcm_open(key, kn, sealed, n, buf, sizeof buf);
    said("aes_gcm_open", r, NULL);
    said("aes_gcm_open.same", r == 64 && memcmp(buf, plain, 64) == 0, NULL);
    said("aes_gcm_open.key", moy_aes_gcm_open(key, 15, sealed, n, buf, sizeof buf), NULL);
    sealed[20] ^= 1;
    said("aes_gcm_open.changed", moy_aes_gcm_open(key, kn, sealed, n, buf, sizeof buf), NULL);
    r = moy_aes_gcm_seal(key, kn, "moybyte", 7, sealed, sizeof sealed);
    said("aes_gcm_seal", r, NULL);
    r = moy_aes_gcm_open(key, kn, sealed, (size_t)(r > 0 ? r : 0), buf, sizeof buf);
    printf("aes_gcm_seal.opened %.*s\n", r > 0 ? (int)r : 0, (const char *)buf);
    said("aes_gcm_seal.short", moy_aes_gcm_seal(key, kn, "moybyte", 7, sealed, 34), NULL);
    unhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a", a);
    unhex("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f", b);
    said("x25519.public", moy_x25519(a, 32, NULL, 0, buf, sizeof buf), buf);
    said("x25519.shared", moy_x25519(a, 32, b, 32, buf, sizeof buf), buf);
    memset(b, 0, sizeof b);
    said("x25519.small", moy_x25519(a, 32, b, 32, buf, sizeof buf), NULL);
    said("x25519.secret", moy_x25519(a, 31, NULL, 0, buf, sizeof buf), NULL);
    static uint8_t sk[32], pk[32], sig[64], msg[2];
    unhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7", sk);
    unhex("6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
          "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a", sig);
    unhex("af82", msg);
    said("ed25519.public", moy_ed25519_public(sk, 32, pk, sizeof pk), pk);
    r = moy_ed25519_sign(sk, 32, msg, 2, buf, sizeof buf);
    said("ed25519.sign", r, buf);
    said("ed25519.sign.same", r == 64 && memcmp(buf, sig, 64) == 0, NULL);
    said("ed25519.verify", moy_ed25519_verify(pk, 32, msg, 2, sig, 64), NULL);
    said("ed25519.verify.changed", moy_ed25519_verify(pk, 32, msg, 1, sig, 64), NULL);
    said("ed25519.verify.key", moy_ed25519_verify(a, 32, msg, 2, sig, 64), NULL);
    said("ed25519.verify.sig", moy_ed25519_verify(pk, 32, msg, 2, sig, 63), NULL);
    said("ed25519.sign.short", moy_ed25519_sign(sk, 32, msg, 2, buf, 63), NULL);
    said("ed25519.secret", moy_ed25519_public(sk, 31, pk, sizeof pk), NULL);
    return 0;
}
