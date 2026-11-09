// The vendored mbedTLS's configuration (native/moy_net/mbedtls/README.md):
// what moy_seal.c and moy_crypto.c call and nothing else, for the host's
// library and the browser's wasm. A board compiles both against the
// ESP-IDF's own mbedTLS and its sdkconfig instead, Curve25519 turned on in
// each console board's sdkconfig.board.

#ifndef MOY_SEAL_MBEDTLS_H
#define MOY_SEAL_MBEDTLS_H

#define MBEDTLS_AES_C
#define MBEDTLS_AES_ROM_TABLES
#define MBEDTLS_AES_FEWER_TABLES
#define MBEDTLS_GCM_C
#define MBEDTLS_HMAC_DRBG_C
#define MBEDTLS_MD_C
#define MBEDTLS_SHA256_C
#define MBEDTLS_BIGNUM_C
#define MBEDTLS_ECP_C
#define MBEDTLS_ECP_DP_SECP256R1_ENABLED
#define MBEDTLS_ECP_DP_CURVE25519_ENABLED
#define MBEDTLS_ECP_NIST_OPTIM

#endif // MOY_SEAL_MBEDTLS_H
