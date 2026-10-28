// moy_ota: the updater and the streaming HTTP(S) client under it
// (docs/kernel_survival_2026-10.md section 6.3).
//
// One body on every tier. The pure half -- a URL, a response head, SHA-256,
// the manifest's RSA signature, its canonical text and the policy -- reads
// untrusted bytes and touches no socket; fuzz_net.c runs it under the
// sanitizers. Above it, the client and the updater's state machine call the
// platform (moy_net_port.c): a TCP connection, TLS on a board, and the
// inactive app slot. The companion radio's sink (the P4s' C6) is
// native/p4/moy_c6's, reached through weak symbols, so an image without it
// refuses that sink.
//
// Get Carts and the updater fetch through the same client. All of its state
// and buffers are the kernel's (moy_net_alloc): a VM holds a handle, an index
// and a generation (docs/native_kernel_2026-09.md section 4.3), and the VM's
// stop closes what it left open (moy_net_vm_stop).
//
// TLS verifies no certificate. What decides an install is the manifest's
// signature under a key baked into the image (OTA_PUBLIC_KEYS, tools/ota_sign.py)
// and the image's sha256, which that signature covers; what Get Carts installs
// is checked against its index's sha256.

#ifndef MOY_OTA_H
#define MOY_OTA_H

#include <stddef.h>
#include <stdint.h>

// -- the pure half -------------------------------------------------------------------

#define MOY_URL_HOST_MAX 128

// An http:// or https:// URL: 0 and its parts (the path is a span of `url`,
// "/" when it has none), or -1. A port is decimal, 1..65535.
int moy_url_parse(const char *url, int *tls, char *host, size_t host_cap,
                  uint16_t *port, const char **path);

typedef struct {
    int status;             // 0 when the status line does not parse
    uint32_t clen;          // Content-Length, 0 when absent or unreadable
    const char *loc;        // Location's value, trimmed; NULL when none
    size_t loc_n;
    size_t head_end;        // the byte past the blank line
} moy_http_resp_t;

// A response head off the first `n` bytes of `buf`: MOY_HTTP_OK with `r`
// filled, or MOY_HTTP_PARTIAL until the "\r\n\r\n" arrives. Header names
// match without regard to ASCII case.
int moy_http_resp_parse(const char *buf, size_t n, moy_http_resp_t *r);

typedef struct {
    uint32_t h[8];
    uint64_t len;
    uint8_t buf[64];
    uint32_t fill;
} moy_sha256_t;

void moy_sha256_init(moy_sha256_t *s);
void moy_sha256_update(moy_sha256_t *s, const void *p, size_t n);
void moy_sha256_final(moy_sha256_t *s, uint8_t out[32]);

#define MOY_OTA_KEY_BYTES 256       // RSA-2048
#define MOY_OTA_EXPONENT 65537u

typedef struct {
    uint8_t n[MOY_OTA_KEY_BYTES];   // the modulus, big-endian
} moy_ota_key_t;

// The keys this image trusts (moy_ota.c, mirrored by device/moy_ota.py's
// OTA_PUBLIC_KEYS): the count, and each by index.
int moy_ota_keys(void);
const moy_ota_key_t *moy_ota_key(int i);

// A modulus from its hex (`n` digits): 0, or -1 for one that is not a usable
// RSA-2048 modulus (not hex, wider than 2048 bits, even, or under 2^2040).
int moy_ota_key_hex(const char *hex, size_t n, moy_ota_key_t *out);

// Whether `sig_hex` is an RSA-2048/SHA-256 PKCS#1 v1.5 signature of the `n`
// bytes at `payload` under one of `keys` (`nkeys` of them; NULL: the baked
// ones). The whole decrypted block is compared, never parsed.
int moy_ota_verify(const void *payload, size_t n, const char *sig_hex,
                   size_t sig_n, const moy_ota_key_t *keys, int nkeys);

// The bytes a manifest's signature covers ("moybyte-ota-v2\n<board>\n
// <channel>\n<version>\n<size>\n<sha256 lowercased>") and the c6 block's
// ("moybyte-c6-v1\n<board>\n<version>\n<size>\n<sha256>"), from the manifest's
// JSON object, as tools/ota_sign.py's canonical and canonical_c6 write them:
// the length the whole needs, at most `cap` written. A field that is absent,
// null or false reads as its empty value; a number field reads as Python's
// int() of it.
size_t moy_ota_canonical(const char *obj, size_t n, char *out, size_t cap);
size_t moy_ota_canonical_c6(const char *obj, size_t n, char *out, size_t cap);

enum {
    MOY_OTA_OK = 0,
    MOY_OTA_ABSENT = 1,         // the channel has nothing for this board (404, 410)
    MOY_OTA_ERR = -1,           // the reason is moy_ota_error()
};

// A fetched manifest judged: the board first (a manifest naming another
// board is refused), then the signature (one present must verify; one absent
// is refused when `require_sig`). OK, or ERR with the reason.
int moy_ota_judge(const char *text, size_t n, const char *board,
                  int require_sig, const moy_ota_key_t *keys, int nkeys,
                  char *why, size_t why_cap);

// The c6 block's signature, by the same policy: OK, or ERR with the reason.
int moy_ota_judge_c6(const char *text, size_t n, int require_sig,
                     const moy_ota_key_t *keys, int nkeys, char *why,
                     size_t why_cap);

// -- the client ---------------------------------------------------------------------

#define MOY_HTTPC_HOPS 4            // redirects followed
#define MOY_HTTPC_HEAD_MAX 16384    // GitHub's redirect head measured 5147 bytes
#define MOY_HTTPC_IO_MS 15000       // a connect, or a read that has no byte this long
#define MOY_HTTPC_SLOTS 2

// GET `url` with redirects followed: a handle (> 0), or -errno (EINVAL for a
// URL that does not parse, EIO for a head that never ends). `*status` and
// `*clen` are the final response's; its body is the handle's to read.
int moy_httpc_open(const char *url, const char *agent, int *status,
                   uint32_t *clen);
// Up to `n` body bytes: their count, 0 at the end, -errno on a failure or a
// handle that is not open.
int moy_httpc_read(int h, void *buf, size_t n);
void moy_httpc_close(int h);
// The body whole, up to `cap` bytes, into a kernel buffer the caller frees
// with moy_net_free: 0, or -errno; `*status` the final response's.
int moy_httpc_get(const char *url, const char *agent, size_t cap, int *status,
                  char **body, size_t *n);

// The platform's connection (moy_net_port.c): 0 and a connection, or -errno.
// A board speaks TLS; the host, the unix port and the browser refuse it with
// EPROTONOSUPPORT.
int moy_conn_open(const char *host, uint16_t port, int tls, void **conn);
// >0 bytes, 0 at the end, -errno (ETIMEDOUT after MOY_HTTPC_IO_MS of nothing).
int moy_conn_read(void *conn, void *p, size_t n);
int moy_conn_write(void *conn, const void *p, size_t n);
void moy_conn_close(void *conn);

// -- the updater ----------------------------------------------------------------------

#define MOY_OTA_MANIFEST_MAX 8192
#define MOY_OTA_ERR_MAX 48
#define MOY_OTA_C6_CHUNK 1500       // the hosted slave OTA's example size; 4096 fails EIO

enum {
    MOY_OTA_SINK_SLOT = 1,          // the inactive app slot
    MOY_OTA_SINK_C6 = 2,            // the companion radio's slave OTA
};

enum {
    MOY_OTA_IDLE = 0,
    MOY_OTA_FETCHING = 1,           // a download streams
    MOY_OTA_VERIFIED = 2,           // the bytes are in the sink, size and sha256 checked
    MOY_OTA_WRITING = 3,            // the caller feeds the slot (a copied image)
    MOY_OTA_FAILED = 4,
};

typedef struct {
    uint8_t phase;
    uint8_t sink;
    uint32_t dl_done, dl_total;     // bytes the download has taken, and its size
    uint32_t done, total;           // bytes in the sink, and the image's size
    char err[MOY_OTA_ERR_MAX];      // the last failure, "" when none
} moy_ota_state_t;

void moy_ota_state(moy_ota_state_t *out);
const char *moy_ota_error(void);

// Fetch and judge a manifest (moy_ota_judge) under the trusted keys: OK and
// its text (a kernel buffer, freed with moy_net_free), ABSENT, or ERR.
int moy_ota_check(const char *url, const char *board, int require_sig,
                  char **text, size_t *n);
// The keys moy_ota_check trusts: `n` of `keys` (copied), or the baked ones
// when `keys` is NULL (the default). The host's tests sign with their own.
void moy_ota_trust(const moy_ota_key_t *keys, int n);

// Open the image's stream and the sink. 0, or -1 with the reason. A size of 0
// takes the response's Content-Length; a sha256 of "" checks none.
int moy_ota_dl_begin(const char *url, uint32_t size, const char *sha_hex,
                     int sink);
// Up to `max` bytes stream -> sink: 1 while more remains, 0 at the end, -1 on
// a failure (the sink is dropped: nothing can activate it).
int moy_ota_dl_step(uint32_t max);
// The tail written and the size and sha256 checked: 0 (VERIFIED), or -1.
int moy_ota_dl_finish(void);
// Drop a download or a write in progress; the sink is left unbootable.
void moy_ota_cancel(void);

// The slot fed by the caller (a copied image): begin, the bytes in order,
// then close, which verifies the image as the slot sink's finish does.
int moy_ota_slot_begin(uint32_t size);
int moy_ota_slot_write(const void *p, size_t n);
int moy_ota_slot_close(void);

// Make a VERIFIED slot the next boot's: 0 and its label ("ota_1"), or -1.
int moy_ota_activate(char *label, size_t cap);
// Hand a VERIFIED C6 image to the radio and restart it: 0, or -1.
int moy_ota_c6_commit(void);

// The platform's slot (moy_net_port.c). open: the inactive slot for an image
// of `size` bytes (0: unknown), its capacity in `*cap`; write: the next bytes;
// close: the image checked whole; boot: it made the next boot's; abort: it
// dropped. Each 0, or -errno (EFBIG for an image past the slot).
int moy_slot_open(uint32_t size, uint32_t *cap);
int moy_slot_write(const void *p, size_t n);
int moy_slot_close(void);
int moy_slot_boot(char *label, size_t cap);
void moy_slot_abort(void);

// The companion radio's sink and its version, where the image has one
// (native/p4/moy_c6): 0, or an ESP error.
int moy_c6_ota_begin(void) __attribute__((weak));
int moy_c6_ota_write(const void *p, size_t n) __attribute__((weak));
int moy_c6_ota_end(void) __attribute__((weak));
int moy_c6_ota_activate(void) __attribute__((weak));
int moy_c6_version(void) __attribute__((weak));     // -1 when it says nothing

// The VM stopped: close the client's connections and drop an update that was
// streaming (the kernel's teardown calls it).
void moy_net_vm_stop(void);

#endif // MOY_OTA_H
