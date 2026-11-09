// moy_crypto: moy_crypto.h is the authority.

#include <string.h>

#include "moy_crypto.h"
#include "moy_seal.h"

#include "mbedtls/ecp.h"
#include "mbedtls/gcm.h"
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"
#include "monocypher-ed25519.h"

static const uint8_t NONE[1];

static const void *bytes(const void *p, size_t n) {
    return p != NULL || n != 0 ? p : NONE;
}

static int good_key(size_t kn) {
    return kn == 16 || kn == 24 || kn == 32;
}

int32_t moy_crypto_random(int32_t n, void *out, size_t cap) {
    if (n < 0 || n > MOY_CRYPTO_RANDOM_MAX) {
        return MOY_CRYPTO_EBAD;
    }
    if ((size_t)n > cap || (n && out == NULL)) {
        return MOY_CRYPTO_EFULL;
    }
    if (n) {
        moy_seal_random(out, (size_t)n);
    }
    return n;
}

int32_t moy_crypto_sha256(const void *p, size_t n, void *out, size_t cap) {
    if (p == NULL && n) {
        return MOY_CRYPTO_EBAD;
    }
    if (cap < MOY_CRYPTO_HASH || out == NULL) {
        return MOY_CRYPTO_EFULL;
    }
    if (mbedtls_sha256(bytes(p, n), n, out, 0) != 0) {
        return MOY_CRYPTO_ENOMEM;
    }
    return MOY_CRYPTO_HASH;
}

int32_t moy_crypto_hmac_sha256(const void *key, size_t kn, const void *p, size_t n,
                               void *out, size_t cap) {
    if ((key == NULL && kn) || (p == NULL && n)) {
        return MOY_CRYPTO_EBAD;
    }
    if (cap < MOY_CRYPTO_HASH || out == NULL) {
        return MOY_CRYPTO_EFULL;
    }
    if (mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), bytes(key, kn), kn,
                        bytes(p, n), n, out) != 0) {
        return MOY_CRYPTO_ENOMEM;
    }
    return MOY_CRYPTO_HASH;
}

int32_t moy_crypto_aes_gcm_seal(const void *key, size_t kn, const void *p, size_t n,
                                void *out, size_t cap) {
    if (key == NULL || !good_key(kn) || (p == NULL && n)
        || n > (size_t)INT32_MAX - MOY_CRYPTO_SEAL_OVER) {
        return MOY_CRYPTO_EBAD;
    }
    size_t need = n + MOY_CRYPTO_SEAL_OVER;
    if (cap < need || out == NULL) {
        return MOY_CRYPTO_EFULL;
    }
    uint8_t *o = out;
    moy_seal_random(o, MOY_CRYPTO_NONCE);
    mbedtls_gcm_context g;
    mbedtls_gcm_init(&g);
    int rc = mbedtls_gcm_setkey(&g, MBEDTLS_CIPHER_ID_AES, key, (unsigned)(kn * 8u));
    if (rc == 0) {
        rc = mbedtls_gcm_crypt_and_tag(&g, MBEDTLS_GCM_ENCRYPT, n, o, MOY_CRYPTO_NONCE,
                                       NULL, 0, bytes(p, n), o + MOY_CRYPTO_NONCE,
                                       MOY_CRYPTO_TAG, o + MOY_CRYPTO_NONCE + n);
    }
    mbedtls_gcm_free(&g);
    if (rc != 0) {
        moy_seal_wipe(o, need);
        return MOY_CRYPTO_ENOMEM;
    }
    return (int32_t)need;
}

int32_t moy_crypto_aes_gcm_open(const void *key, size_t kn, const void *p, size_t n,
                                void *out, size_t cap) {
    if (key == NULL || !good_key(kn) || (p == NULL && n) || n > (size_t)INT32_MAX) {
        return MOY_CRYPTO_EBAD;
    }
    if (n < MOY_CRYPTO_SEAL_OVER) {
        return MOY_CRYPTO_EFORGED;
    }
    const uint8_t *s = p;
    size_t m = n - MOY_CRYPTO_SEAL_OVER;
    if (cap < m || (m && out == NULL)) {
        return MOY_CRYPTO_EFULL;
    }
    uint8_t scratch[1];
    mbedtls_gcm_context g;
    mbedtls_gcm_init(&g);
    int rc = mbedtls_gcm_setkey(&g, MBEDTLS_CIPHER_ID_AES, key, (unsigned)(kn * 8u));
    if (rc == 0) {
        rc = mbedtls_gcm_auth_decrypt(&g, m, s, MOY_CRYPTO_NONCE, NULL, 0,
                                      s + MOY_CRYPTO_NONCE + m, MOY_CRYPTO_TAG,
                                      s + MOY_CRYPTO_NONCE, m ? (uint8_t *)out : scratch);
    }
    mbedtls_gcm_free(&g);
    if (rc == MBEDTLS_ERR_GCM_AUTH_FAILED) {
        if (m) {
            moy_seal_wipe(out, m);
        }
        return MOY_CRYPTO_EFORGED;
    }
    if (rc != 0) {
        if (m) {
            moy_seal_wipe(out, m);
        }
        return MOY_CRYPTO_ENOMEM;
    }
    return (int32_t)m;
}

#if defined(MBEDTLS_ECP_DP_CURVE25519_ENABLED)
static int rng(void *ctx, unsigned char *p, size_t n) {
    (void)ctx;
    moy_seal_random(p, n);
    return 0;
}
#endif

int32_t moy_crypto_x25519(const void *secret, size_t sn, const void *peer, size_t pn,
                          void *out, size_t cap) {
    if (secret == NULL || sn != MOY_CRYPTO_KEY25519 || (pn != 0 && pn != MOY_CRYPTO_KEY25519)
        || (peer == NULL && pn)) {
        return MOY_CRYPTO_EBAD;
    }
    if (cap < MOY_CRYPTO_KEY25519 || out == NULL) {
        return MOY_CRYPTO_EFULL;
    }
#if defined(MBEDTLS_ECP_DP_CURVE25519_ENABLED)
    uint8_t k[MOY_CRYPTO_KEY25519];
    memcpy(k, secret, sizeof k);
    k[0] &= 248u;                       // RFC 7748 section 5, decodeScalar25519
    k[31] &= 127u;
    k[31] |= 64u;
    mbedtls_ecp_group grp;
    mbedtls_ecp_point P, R;
    mbedtls_mpi d;
    mbedtls_ecp_group_init(&grp);
    mbedtls_ecp_point_init(&P);
    mbedtls_ecp_point_init(&R);
    mbedtls_mpi_init(&d);
    int rc = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_CURVE25519);
    if (rc == 0) {
        rc = mbedtls_mpi_read_binary_le(&d, k, sizeof k);
    }
    if (rc == 0) {
        rc = pn ? mbedtls_ecp_point_read_binary(&grp, &P, peer, pn)
                : mbedtls_ecp_copy(&P, &grp.G);
    }
    if (rc == 0) {
        rc = mbedtls_ecp_mul(&grp, &R, &d, &P, rng, NULL);
    }
    if (rc == 0) {
        rc = mbedtls_mpi_write_binary_le(&R.MBEDTLS_PRIVATE(X), out, MOY_CRYPTO_KEY25519);
    }
    moy_seal_wipe(k, sizeof k);
    mbedtls_mpi_free(&d);
    mbedtls_ecp_point_free(&R);
    mbedtls_ecp_point_free(&P);
    mbedtls_ecp_group_free(&grp);
    if (rc == MBEDTLS_ERR_ECP_INVALID_KEY) {
        moy_seal_wipe(out, MOY_CRYPTO_KEY25519);
        return MOY_CRYPTO_EPOINT;
    }
    if (rc != 0) {
        moy_seal_wipe(out, MOY_CRYPTO_KEY25519);
        return MOY_CRYPTO_ENOMEM;
    }
    return MOY_CRYPTO_KEY25519;
#else
    (void)peer;
    return MOY_CRYPTO_EABSENT;
#endif
}

// Monocypher's key pair from a copy of the seed, which it wipes: the 64-byte
// secret key (the seed, then the public key) and the public key.
static void ed25519_pair(const void *secret, uint8_t sk[64], uint8_t pk[MOY_CRYPTO_KEY25519]) {
    uint8_t seed[MOY_CRYPTO_KEY25519];
    memcpy(seed, secret, sizeof seed);
    crypto_ed25519_key_pair(sk, pk, seed);
}

int32_t moy_crypto_ed25519_public(const void *secret, size_t sn, void *out, size_t cap) {
    if (secret == NULL || sn != MOY_CRYPTO_KEY25519) {
        return MOY_CRYPTO_EBAD;
    }
    if (cap < MOY_CRYPTO_KEY25519 || out == NULL) {
        return MOY_CRYPTO_EFULL;
    }
    uint8_t sk[64];
    ed25519_pair(secret, sk, out);
    crypto_wipe(sk, sizeof sk);
    return MOY_CRYPTO_KEY25519;
}

int32_t moy_crypto_ed25519_sign(const void *secret, size_t sn, const void *p, size_t n,
                                void *out, size_t cap) {
    if (secret == NULL || sn != MOY_CRYPTO_KEY25519 || (p == NULL && n)) {
        return MOY_CRYPTO_EBAD;
    }
    if (cap < MOY_CRYPTO_SIG25519 || out == NULL) {
        return MOY_CRYPTO_EFULL;
    }
    uint8_t sk[64], pk[MOY_CRYPTO_KEY25519];
    ed25519_pair(secret, sk, pk);
    crypto_ed25519_sign(out, sk, bytes(p, n), n);
    crypto_wipe(sk, sizeof sk);
    return MOY_CRYPTO_SIG25519;
}

int32_t moy_crypto_ed25519_verify(const void *pub, size_t pn, const void *p, size_t n,
                                  const void *sig, size_t gn) {
    if (pub == NULL || pn != MOY_CRYPTO_KEY25519 || sig == NULL || gn != MOY_CRYPTO_SIG25519
        || (p == NULL && n)) {
        return MOY_CRYPTO_EBAD;
    }
    return crypto_ed25519_check(sig, pub, bytes(p, n), n) == 0;
}
