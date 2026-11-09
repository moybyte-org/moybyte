// moy_seal: the crypto of the webhost's sign-in and its sealed sessions
// (moy_webauth.c is the board's end, moy_seal_web.c the browser's), both
// ends in this one body: a board compiles it against the ESP-IDF's mbedTLS
// (AES and SHA in hardware), the host's library and the browser's wasm
// against the vendored copy of the same release (mbedtls/README.md).
//
// THE PROTOCOL, "moy1" (docs/os_2026-11/system.md section 3 is the why):
//
//   a hello      the board's id (16 random bytes, kept in system/webkeys)
//                and a fresh board nonce nb; the page brings its own nc.
//   a key login  mac = HMAC(K, "moy1 key" 0 id nb nc kid) proves the page
//                holds device key K; the board answers a session id and
//                proof = HMAC(K, "moy1 board" 0 id nb nc kid sid).
//   a session    kc || kb = HKDF-SHA256(salt nb||nc, ikm K, info
//                "moy1 session" 0 kid): kc seals what the page sends, kb
//                what the board sends.
//   a token      a QR's single-use 128-bit token T pairs a browser:
//                mac = HMAC(T, "moy1 token" 0 id nb nc name); the board mints
//                K and sends it XOR HMAC(T, "moy1 wrap" 0 id nb nc).
//   a password   SPAKE2+ (RFC 9383, P256-SHA256-HKDF-SHA256-HMAC-SHA256,
//                its M and N, transcript and key schedule), the browser the
//                prover with w0 and w1 from the password, the board the
//                verifier with the record's w0 and L = w1*G (THE PASSWORD
//                below); context "moy2 SPAKE2+-P256-SHA256-HKDF-SHA256-HMAC-
//                SHA256", idProver "moy1 browser" nc, idVerifier "moy1
//                board" id nb. Ke is K_shared's first 16 bytes; once both
//                confirmations check, the board mints K and sends it XOR
//                HMAC(Ke, "moy1 wrap" 0 id nb nc).
//
//   A SEALED REQUEST carries `s=<sid>&n=<seq>` in its query; its body is
//   AES-128-GCM under kc, nonce (1, 0, seq), AAD "moy1 req" 0 METHOD 0 TARGET
//   (the whole request line's target). A request with no body seals nothing
//   and appends its tag as the query's LAST parameter, `&t=<32 hex>`, which
//   the AAD's target excludes. A SEALED ANSWER is records: a 4-byte head (the
//   length, its top bit set on the last, empty record), the ciphertext and
//   its tag, under kb, nonce (2, record index, seq), AAD "moy1 res" 0
//   STATUS(2) HEAD(4). A sealed WebSocket's frames are (3, i, seq) up and
//   (4, i, seq) down, i counting from 0 each way, AAD "moy1 ws". A nonce is
//   kind(1) index(3) seq(8), big-endian.

#ifndef MOY_SEAL_H
#define MOY_SEAL_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SEAL_KEY 16
#define MOY_SEAL_TAG 16
#define MOY_SEAL_RAND 16                // the board id, a nonce, a token
#define MOY_SEAL_KID 8
#define MOY_SEAL_SID 8
#define MOY_SEAL_MAC 32
#define MOY_SEAL_POINT 65               // an uncompressed P-256 point
#define MOY_SEAL_HEAD 4
#define MOY_SEAL_LAST 0x80000000u
#define MOY_SEAL_INDEX_MAX 0xffffffu

enum {
    MOY_SEAL_OK = 0,
    MOY_SEAL_EBAD = -1,                 // a tag or a confirmation that does not check
    MOY_SEAL_EPOINT = -2,               // a point off the curve, or the identity
    MOY_SEAL_ENOMEM = -3,
};

enum {
    MOY_SEAL_REQ = 1,
    MOY_SEAL_RES = 2,
    MOY_SEAL_UP = 3,
    MOY_SEAL_DOWN = 4,
};

typedef struct {
    uint8_t id[MOY_SEAL_RAND], nb[MOY_SEAL_RAND], nc[MOY_SEAL_RAND];
} moy_seal_hs_t;

typedef struct {
    uint8_t kc[MOY_SEAL_KEY], kb[MOY_SEAL_KEY];
} moy_seal_keys_t;

typedef struct {
    uint8_t s[32];                      // this side's secret scalar, x or y
    uint8_t w0[32];
    uint8_t w1[32];                     // the prover's
    uint8_t l[MOY_SEAL_POINT];          // the verifier's, w1*G
    uint8_t mine[MOY_SEAL_POINT];       // X (the browser's) or Y (the board's)
    uint8_t board;                      // the verifier
} moy_seal_spake_t;

// THE RANDOM SOURCE every key, nonce and token the console makes draws from:
// the sign-in's, the SSH server's (moy_ssh_entropy), the TLS client's, a WASI
// program's random_get, LoRaWAN's and the crypto rows' (moy_crypto.h). It is
// an HMAC-DRBG (SHA-256, mbedTLS's) seeded at its first draw from 256 bytes a
// board reads at boot, before app_main, with the SAR ADC's noise mixed into
// its RNG (bootloader_random_enable, disabled again before anything starts an
// ADC unit or the radio): ESP-IDF counts the RNG as true random only while that
// source or the chip's own radio runs, and a P4's radio is the C6. Every draw
// mixes in 16 more bytes of the RNG as additional input, and a reseed (every
// 10,000 draws) takes 32. The host and the browser seed it from getentropy.
// Thread-safe.
void moy_seal_random(void *p, size_t n);
void moy_seal_sha256(const void *p, size_t n, uint8_t out[32]);
void moy_seal_hmac(const uint8_t *key, size_t kn, const void *p, size_t n, uint8_t out[32]);
void moy_seal_hkdf(const uint8_t *salt, size_t sn, const uint8_t *ikm, size_t in,
                   const void *info, size_t infon, uint8_t *out, size_t on);
int moy_seal_eq(const void *a, const void *b, size_t n);   // constant time
void moy_seal_wipe(void *p, size_t n);
// `want` bytes from exactly 2*want hex digits: 1, else 0.
int moy_seal_unhex(const char *s, size_t n, uint8_t *out, size_t want);
void moy_seal_hex(const uint8_t *p, size_t n, char *out);  // 2n digits and a NUL

// THE PASSWORD's record, as system/password keeps it (runtime/system/
// ssh_service.py) and the SSH server and the sign-in read it:
//   $moy2$ITER$SALT$W0$L
// k = PBKDF2-HMAC-SHA256(password, SALT, ITER rounds, 32 bytes); w0s || w1s
// = HKDF-SHA256(no salt, k, "moy2 spake2+ w0 w1", 80 bytes); w0 and w1 each
// 40-byte half mod P-256's order (a zero read as 1); W0 is w0's 32 bytes and
// L the uncompressed w1*G, in hex. The record verifies a password (the SSH
// server, moy_seal_pw_check) and is the SPAKE2+ verifier's: it holds no w1,
// so a copy of it pairs no browser, and a guess against it costs ITER rounds.
// SALT is 1 to MOY_SEAL_PW_SALT_MAX printable characters, no '$'. A record of
// another form, or ITER outside MIN..MAX, reads as no password.
#define MOY_SEAL_PW_ITER (1u << 15)
#define MOY_SEAL_PW_ITER_MIN (1u << 15)
#define MOY_SEAL_PW_ITER_MAX (1u << 22)
#define MOY_SEAL_PW_SALT_MAX 32
#define MOY_SEAL_PW_RECORD 256          // the longest record and its NUL
// w0 and w1 of a password: MOY_SEAL_OK, else MOY_SEAL_EBAD (a salt or ITER
// out of bounds) or MOY_SEAL_ENOMEM.
int moy_seal_pw_derive(const char *pw, size_t pn, const char *salt, size_t sn, uint32_t iter,
                       uint8_t w0[32], uint8_t w1[32]);
// The record into out (cap at least MOY_SEAL_PW_RECORD): its length, or -1.
int moy_seal_pw_record(const char *pw, size_t pn, const char *salt, size_t sn, uint32_t iter,
                       char *out, size_t cap);
// A record's text up to its line's end: ITER, SALT (NUL-terminated), w0 and
// L, 1; else 0.
int moy_seal_pw_parse(const char *text, size_t n, uint32_t *iter, char *salt, size_t salt_cap,
                      uint8_t w0[32], uint8_t l[MOY_SEAL_POINT]);
// Whether `pw` is the password a record was made from (w0 compared in
// constant time): 1, else 0.
int moy_seal_pw_check(const char *pw, size_t pn, const char *text, size_t n);

// The protocol's MACs and keys (the header).
void moy_seal_key_mac(const moy_seal_hs_t *h, const uint8_t kid[MOY_SEAL_KID],
                      const uint8_t key[MOY_SEAL_KEY], uint8_t mac[MOY_SEAL_MAC]);
void moy_seal_proof(const moy_seal_hs_t *h, const uint8_t kid[MOY_SEAL_KID],
                    const uint8_t key[MOY_SEAL_KEY], const uint8_t sid[MOY_SEAL_SID],
                    uint8_t mac[MOY_SEAL_MAC]);
void moy_seal_session(const moy_seal_hs_t *h, const uint8_t kid[MOY_SEAL_KID],
                      const uint8_t key[MOY_SEAL_KEY], moy_seal_keys_t *out);
void moy_seal_token_mac(const moy_seal_hs_t *h, const uint8_t token[MOY_SEAL_RAND],
                        const char *name, size_t nn, uint8_t mac[MOY_SEAL_MAC]);
// key ^= HMAC(secret, "moy1 wrap" 0 id nb nc): wraps and unwraps.
void moy_seal_wrap(const moy_seal_hs_t *h, const uint8_t *secret, size_t sn,
                   uint8_t key[MOY_SEAL_KEY]);

// SPAKE2+: this side's share (X = x*G + w0*M, the prover's; Y = y*G + w0*N,
// the verifier's, after L is checked on the curve), from the scalar given or,
// with NULL, a fresh one; then the peer's share to K_shared and both
// confirmations, confirmP = HMAC(K_confirmP, Y) the prover's and confirmV =
// HMAC(K_confirmV, X) the verifier's, under the transcript's context and
// identities (moy_seal_spake_keys, each at most 64 bytes), or under the
// console's (moy_seal_spake_end: Ke, cA the browser's, cB the board's). A peer
// share off the curve, or one that leaves Z or V the identity, is
// MOY_SEAL_EPOINT.
int moy_seal_spake_prover(moy_seal_spake_t *s, const uint8_t w0[32], const uint8_t w1[32],
                          const uint8_t *x);
int moy_seal_spake_verifier(moy_seal_spake_t *s, const uint8_t w0[32],
                            const uint8_t l[MOY_SEAL_POINT], const uint8_t *y);
int moy_seal_spake_keys(moy_seal_spake_t *s, const void *context, size_t cn, const void *idp,
                        size_t pn, const void *idv, size_t vn, const uint8_t peer[MOY_SEAL_POINT],
                        uint8_t shared[32], uint8_t cp[MOY_SEAL_MAC], uint8_t cv[MOY_SEAL_MAC]);
int moy_seal_spake_end(moy_seal_spake_t *s, const moy_seal_hs_t *h,
                       const uint8_t peer[MOY_SEAL_POINT], uint8_t ke[MOY_SEAL_KEY],
                       uint8_t ca[MOY_SEAL_MAC], uint8_t cb[MOY_SEAL_MAC]);

// AES-128-GCM over p in place (seal: 1, open: 0), the nonce (kind, index,
// seq): MOY_SEAL_OK, or MOY_SEAL_EBAD with p wiped.
int moy_seal_gcm(int seal, const uint8_t key[MOY_SEAL_KEY], int kind, uint32_t index,
                 uint64_t seq, const void *aad, size_t an, uint8_t *p, size_t n,
                 uint8_t tag[MOY_SEAL_TAG]);
int moy_seal_req(int seal, const uint8_t kc[MOY_SEAL_KEY], uint64_t seq, const char *method,
                 size_t mn, const char *target, size_t tn, uint8_t *p, size_t n,
                 uint8_t tag[MOY_SEAL_TAG]);
// One answer record: `head` is its 4 bytes (written when sealing).
int moy_seal_rec(int seal, const uint8_t kb[MOY_SEAL_KEY], uint64_t seq, uint32_t index,
                 int status, int last, uint8_t head[MOY_SEAL_HEAD], uint8_t *p, size_t n,
                 uint8_t tag[MOY_SEAL_TAG]);
int moy_seal_ws(int seal, const uint8_t key[MOY_SEAL_KEY], int kind, uint32_t index,
                uint64_t seq, uint8_t *p, size_t n, uint8_t tag[MOY_SEAL_TAG]);

#endif // MOY_SEAL_H
