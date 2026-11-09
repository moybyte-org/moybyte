// moy_crypto: the crypto an app calls (native/os_calls.json's rows
// moybyte.random, sha256, hmac_sha256, aes_gcm_seal, aes_gcm_open and x25519,
// docs/app_api_v1.md section 10), one body every tier reaches: a compiled
// app's and a native or WASI program's through the app ABI's door
// (native/moy_app), a Lua run's through its role names, a Python program's
// through the module moy_app. A board compiles it against the ESP-IDF's
// mbedTLS (AES and SHA in hardware), the host's library and the browser's
// wasm against the vendored copy of the same release (mbedtls/README.md),
// beside moy_seal.c, whose random source it shares.
//
// Pure computation over the caller's buffers: nothing is kept, no key leaves
// but into the buffer the caller handed, and every call answers the length
// it wrote or a code below, never more than `cap` bytes written.
//
//   random       n bytes of the hardware RNG (esp_fill_random; the host's
//                and the browser's getentropy), n at most RANDOM_MAX.
//   sha256       the digest, 32 bytes.
//   hmac_sha256  HMAC-SHA256 (RFC 2104) under a key of any length, 32 bytes.
//   aes_gcm_seal AES-GCM under a 16-, 24- or 32-byte key: a fresh random
//                12-byte nonce, the ciphertext, the 16-byte tag, in that
//                order (SEAL_OVER more bytes than the plaintext). A random
//                nonce bounds one key to about 2^32 seals (NIST SP 800-38D
//                section 8.3).
//   aes_gcm_open what aes_gcm_seal made, back: the plaintext, or FORGED
//                with the caller's buffer wiped where the tag does not check
//                (the wrong key, or a byte changed).
//   x25519       RFC 7748's X25519 of a 32-byte secret (clamped here) and
//                a 32-byte peer key, or the base point for an empty peer
//                (the secret's public key): 32 bytes. A peer key of small
//                order answers POINT; an image built without the curve
//                (MBEDTLS_ECP_DP_CURVE25519_ENABLED) ABSENT.

#ifndef MOY_CRYPTO_H
#define MOY_CRYPTO_H

#include <stddef.h>
#include <stdint.h>

#define MOY_CRYPTO_HASH 32
#define MOY_CRYPTO_NONCE 12
#define MOY_CRYPTO_TAG 16
#define MOY_CRYPTO_SEAL_OVER (MOY_CRYPTO_NONCE + MOY_CRYPTO_TAG)
#define MOY_CRYPTO_KEY25519 32
#define MOY_CRYPTO_RANDOM_MAX 4096

enum {
    MOY_CRYPTO_EBAD = -1,       // a length the call refuses: a key, a secret, a count
    MOY_CRYPTO_EFULL = -2,      // the answer does not fit the caller's buffer
    MOY_CRYPTO_EFORGED = -3,    // sealed bytes that do not open
    MOY_CRYPTO_EPOINT = -4,     // a peer key of small order
    MOY_CRYPTO_EABSENT = -5,    // an image built without it
    MOY_CRYPTO_ENOMEM = -6,
};

int32_t moy_crypto_random(int32_t n, void *out, size_t cap);
int32_t moy_crypto_sha256(const void *p, size_t n, void *out, size_t cap);
int32_t moy_crypto_hmac_sha256(const void *key, size_t kn, const void *p, size_t n,
                               void *out, size_t cap);
int32_t moy_crypto_aes_gcm_seal(const void *key, size_t kn, const void *p, size_t n,
                                void *out, size_t cap);
int32_t moy_crypto_aes_gcm_open(const void *key, size_t kn, const void *p, size_t n,
                                void *out, size_t cap);
int32_t moy_crypto_x25519(const void *secret, size_t sn, const void *peer, size_t pn,
                          void *out, size_t cap);

#endif // MOY_CRYPTO_H
