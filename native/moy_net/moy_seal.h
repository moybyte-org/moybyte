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
//   a password   SPAKE2 over P-256 (RFC 9382's M and N, its transcript and
//                key schedule) with w from the password's stored hash
//                (moy_seal_pw_hash), A = "moy1 browser" nc, B = "moy1 board"
//                id nb; once both confirmations check, the board mints K
//                and sends it XOR HMAC(Ke, "moy1 wrap" 0 id nb nc).
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
    uint8_t s[32];                      // this side's secret scalar
    uint8_t w[32];                      // the password's scalar
    uint8_t mine[MOY_SEAL_POINT];       // X (the browser's) or Y (the board's)
    uint8_t board;
} moy_seal_spake_t;

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

// The console password's hash as system/password keeps it ("$moy1$SALT$HEX",
// runtime/system/ssh_service.py): SHA-256 of SALT PASSWORD, then 4095 times
// of the digest and PASSWORD.
void moy_seal_pw_hash(const char *pw, size_t pn, const char *salt, size_t sn, uint8_t out[32]);
// That file's text: its salt (NUL-terminated) and digest, 1; else 0.
int moy_seal_pw_parse(const char *text, size_t n, char *salt, size_t salt_cap, uint8_t h[32]);

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

// SPAKE2: this side's point from the password's digest, then the peer's
// point to the session's Ke and both confirmations, cA the browser's.
int moy_seal_spake_start(moy_seal_spake_t *s, int board, const uint8_t digest[32]);
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
