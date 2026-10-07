// moy_net: the links (docs/kernel_survival_2026-10.md section 6).
//
// One body for every tier: the boards, the browser, the unix port and
// CPython (by ctypes, runtime/net_binding.py). The wire's pure half -- the
// HTTP request parser and response head (moy_http.c), the sync batch's
// envelope and root rule (moy_sync.c), the WiFi credential rules -- touches
// no socket, radio or file, and fuzz_net.c runs it under the sanitizers; it
// reads untrusted bytes, and every span it returns lies inside the buffer it
// was given. Above it: the sync apply over native/moy_store
// (moy_sync_apply.c), the webhost (moy_webhost.c) over the platform and its
// listener (moy_net_port.c), the WiFi driver (moy_wifi.c) and the ESP-NOW
// link (moy_link.c).

#ifndef MOY_NET_H
#define MOY_NET_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// -- HTTP ----------------------------------------------------------------------

enum {
    MOY_HTTP_PARTIAL = 0,   // the head's blank line has not arrived
    MOY_HTTP_OK = 1,
    MOY_HTTP_BAD = -1,      // a head whose request line has no target
};

// The largest Content-Length a request is believed about; beyond it the
// receiver's own cap ends the read first.
#define MOY_HTTP_CLEN_MAX 0x7fffffffu

typedef struct {
    const char *method;     // the request line up to its first space
    size_t method_n;
    const char *target;     // the request TARGET verbatim, query and all
    size_t target_n;
    uint32_t clen;          // the last Content-Length header's value, else 0
    size_t head_end;        // the byte just past the blank line
} moy_http_req_t;

// One request head off the first `n` bytes of `buf`. The head ends at the
// first "\r\n\r\n", or, when there is none, at the first "\n\n". The request
// line splits on single spaces (two spaces make an empty target); a header is
// Content-Length when its name, trimmed of ASCII whitespace, matches without
// regard to ASCII case, and its value counts when it is decimal digits after an
// optional '+' (a sign, any other byte or a value past MOY_HTTP_CLEN_MAX reads
// 0). PARTIAL and BAD leave `r` zeroed.
int moy_http_parse(const char *buf, size_t n, moy_http_req_t *r);

// The value of `name` in the target's query string: 1 and its span, or 0 (and
// an empty span). Pairs split on '&' and at their first '='; a name with no
// '=' has the empty value. No percent-decoding: what rides a query here is a
// four-digit pin, and a decoder is code that can be wrong about a credential.
int moy_http_query(const char *target, size_t n, const char *name,
                   size_t name_n, const char **val, size_t *val_n);

// -- the listener and the platform (moy_net_port.c) -----------------------------

#define MOY_HTTP_REQ_MAX 65536      // a request past this is cut there
#define MOY_HTTP_BACKLOG 6          // lwip's accept mbox: past it a SYN is reset
#define MOY_HTTP_RECV_MS 400        // a fresh connection's request is en route
#define MOY_HTTP_SEND_MS 2000       // a client that takes no bytes this long is gone

// A non-blocking listener on every interface: its descriptor, or -errno.
int moy_http_listen(uint16_t port);
// One pending connection, blocking with the two timeouts above, or -1.
int moy_http_accept(int lfd);
// Read one request off `fd` into `buf`: its head and as much of its body as
// Content-Length names, up to `cap` bytes. MOY_HTTP_OK and `*got`, or
// MOY_HTTP_BAD when no head arrived.
int moy_http_recv(int fd, char *buf, size_t cap, moy_http_req_t *r,
                  size_t *got);
// All `n` bytes, or -1 when the client stopped taking them.
int moy_http_send(int fd, const void *p, size_t n);
void moy_http_close(int fd);

// The platform under the links: a millisecond clock, a sleep, and memory that
// is the kernel's (PSRAM on a board), never the VM's heap.
uint32_t moy_net_ms(void);
void moy_net_sleep_ms(uint32_t ms);
void *moy_net_alloc(size_t n);
void moy_net_free(void *p);

// The reason phrase a response's status line carries ("OK" for a status the
// table does not name).
const char *moy_http_reason(int status);

// A complete response head, Connection: close, for a body of `body_n` bytes:
// writes at most `cap` bytes (no NUL) and returns the length the whole needs.
size_t moy_http_head(char *out, size_t cap, int status, const char *ctype,
                     size_t body_n);

// Whether the `n` bytes at `s` are well-formed UTF-8 (no overlong form, no
// surrogate, nothing past U+10FFFF): what a strict decode accepts.
int moy_utf8_valid(const char *s, size_t n);

// -- the sync batch's envelope -----------------------------------------------

// The four fields a batch carries, each a span of the body's JSON (NULL when
// the object has no such member; the LAST member by a name wins, as json.loads
// keeps it).
typedef struct {
    const char *v, *v_end;
    const char *root, *root_end;
    const char *ops, *ops_end;
    const char *pin, *pin_end;
} moy_sync_env_t;

enum {
    MOY_SYNC_OK = 0,
    MOY_SYNC_BAD = 1,       // not UTF-8, not JSON, or not an object
};

int moy_sync_decode(const char *body, size_t n, moy_sync_env_t *e);

// A batch's wire JSON from its fields as JSON texts: `v` and `ops` always,
// `root` only when not NULL (the v1 shape names none), `pin` only when not
// NULL. Writes at most `cap` bytes (no NUL) and returns the length the whole
// needs.
size_t moy_sync_encode(char *out, size_t cap, const char *v, size_t v_n,
                       const char *root, size_t root_n, const char *ops,
                       size_t ops_n, const char *pin, size_t pin_n);

// The store a batch speaks for, off its `v` and `root`: v1 is the carts root
// by definition (a `root` other than "carts" is refused, never read as
// carts); v2 names a root whose wire is 2, which is "files" alone.
enum {
    MOY_SYNC_CARTS = 0,
    MOY_SYNC_FILES = 1,
};

typedef struct {
    int root;                       // MOY_SYNC_CARTS or MOY_SYNC_FILES
    const char *ops, *ops_end;      // the ops array
    const char *pin, *pin_end;      // the pin's value, NULL when it has none
} moy_sync_batch_t;

// A POST /sync body read whole: OK, or BAD for anything moy_sync_decode
// refuses, a `v` that is not the integer 1 or 2, a root that pair does not
// serve, or `ops` that is not an array.
int moy_sync_batch(const char *body, size_t n, moy_sync_batch_t *b);

// Whether the batch's pin is the string `pin` (a host with no pin, NULL, takes
// any batch).
int moy_sync_pin_ok(const moy_sync_batch_t *b, const char *pin);

// -- the sync apply (moy_sync_apply.c, over native/moy_store) ------------------

#define MOY_SYNC_PART_MAX (16 * 1024)
#define MOY_SYNC_ERRS 8

// The store a receiver applies into. `kinds` is the files root's allowlist
// (moy_files.FILE_KINDS), each name NUL-terminated and an empty one last; NULL
// when the receiver has no files layer, which refuses every files op.
typedef struct {
    const char *carts;
    const char *files;              // NULL: no files layer
    const char *kinds;
    int journal;                    // of record: a published carts file is a commit
    int64_t ts;                     // what that commit records as its time
} moy_sync_store_t;

typedef struct {
    uint32_t applied;
    uint32_t refused;
    int shelf;                      // a cart was born or died, or a shelf file changed
    uint32_t nerr;                  // the refusals held below, the first MOY_SYNC_ERRS
    struct {
        uint32_t index;
        char why[40];
    } err[MOY_SYNC_ERRS];
} moy_sync_result_t;

// Apply the ops array at [ops, ops_end) into `root` of `s`, one op at a time:
// a bad op is refused with its reason and never aborts the batch.
void moy_sync_apply(const moy_sync_store_t *s, int root, const char *ops,
                    const char *ops_end, moy_sync_result_t *res);

// -- the webhost (moy_webhost.c) ---------------------------------------------------
//
// The console's own HTTP server: the baked bundle (native/moy_web) open, the
// capability marker open, and behind the pin the store's two pulls, the sync
// apply, and whatever routes the VM answers (`defer`). It is the kernel's: its
// listener, state and buffers are outside the VM's heap, so a soft reset leaves
// it serving and the next VM finds it so. The poll calls no Python; a deferred
// request is parked until the VM takes it (moy_web_take) and answers it
// (moy_web_answer), and one nobody answers in MOY_WEB_DEFER_MS is a 503.

#define MOY_WEB_CLOSING_MS 5000     // the goodbye window: outlasts the page's 3 s heartbeat
#define MOY_WEB_DEFER_MS 3000
#define MOY_WEB_POLL_MAX 4          // new connections one poll accepts
#define MOY_WEB_PATH_MAX 96

enum {
    MOY_WEB_EV_SHELF = 1,           // a batch changed the shelf: rescan
    MOY_WEB_EV_STOPPED = 2,         // the listener closed (the goodbye ran out)
};

typedef struct {
    uint16_t port;
    const char *carts;              // the carts root
    const char *files;              // the files root, NULL when there is none
    const char *kinds;              // the files root's kinds (moy_sync_store_t's)
    const char *pin;                // NULL or "": open end to end
    const char *defer;              // the paths the VM answers, NUL-separated, "" last
    int64_t epoch;                  // seconds the VM's clock starts after 1970's
} moy_web_cfg_t;

typedef struct {
    uint8_t serving;                // the listener is up and not saying goodbye
    uint8_t closing;                // in the goodbye window
    uint8_t parked;                 // a deferred request waits for the VM
    uint8_t listening;              // the socket is bound (serving or saying goodbye)
    uint16_t port;
    uint32_t requests;              // served since start
    uint32_t events;                // pending MOY_WEB_EV_* (read by moy_web_events)
    int err;                        // the last start's errno, 0 when it bound
} moy_web_state_t;

// Bind and serve: 0, or the errno the bind failed with. A host already
// serving takes the new configuration and keeps its socket.
int moy_web_start(const moy_web_cfg_t *cfg);
// Stop. With `why`, answer every request with {"error":"closing","why":...}
// for MOY_WEB_CLOSING_MS first; without, close now.
void moy_web_stop(const char *why);
void moy_web_set_pin(const char *pin);
// One poll: accept and serve up to MOY_WEB_POLL_MAX connections. 1 when it
// did any work.
int moy_web_poll(void);
void moy_web_state(moy_web_state_t *out);
uint32_t moy_web_events(void);      // the pending events, cleared

// The parked request: 1 and its spans (good until moy_web_answer), or 0.
int moy_web_take(const char **method, size_t *method_n, const char **target,
                 size_t *target_n, const char **body, size_t *body_n);
// Answer the parked request with a complete response and close it.
void moy_web_answer(const void *resp, size_t n);
// Close the connection a poll was serving when the VM's store raised under it.
void moy_web_abort(void);

// The router without a socket: a request's whole response appended to `out`
// (moy_net_alloc'd, grown as needed; `*out_n` its length), or 0 when the
// request is one the VM answers. The host's tests and the dev server.
int moy_web_handle(const char *method, size_t method_n, const char *target,
                   size_t target_n, const char *body, size_t body_n,
                   char **out, size_t *out_n);

// A store root as the pull's JSON object, into `out` as moy_web_handle's.
// `kinds` restricts the top-level folders walked (NULL: every one).
int moy_web_pack(const char *root, const char *kinds, char **out,
                 size_t *out_n);

// The image's bundle: "<count> <bytes> <digest>", or NULL with none baked.
const char *moy_web_stamp_text(void);

// -- the WiFi credential rules --------------------------------------------------

// Whether a connect uses the stored password: when the one given is empty and
// a stored one is not. The panel's known-network reconnect passes "" (it has
// no credential access), and associating with "" AND remembering it destroyed
// the saved password (the on-glass P4, 2026-07-25).
static inline int moy_wifi_use_stored(size_t password_n, size_t stored_n) {
    return password_n == 0 && stored_n != 0;
}

// Whether a connect's credentials are written to the store: when the radio
// ASSOCIATED, or when a non-blank password differs from the stored one
// (`stored` NULL when there is none). Never a blank one the radio did not
// verify: "" is both an open network and one the store could not tell us
// about, so remembering it unconditionally let one unreadable load rewrite
// wifi.json as a single empty-password entry. A non-blank password is kept
// even on failure: the connect's short poll giving up is the late association
// the updater's ensure_online waits for.
static inline int moy_wifi_remember(int ok, const char *password,
                                    size_t password_n, const char *stored,
                                    size_t stored_n) {
    if (ok) {
        return 1;
    }
    if (password_n == 0) {
        return 0;
    }
    return stored == NULL || stored_n != password_n
           || memcmp(password, stored, password_n) != 0;
}

// -- the WiFi driver (a board that defines MOY_NET_WIFI) -------------------------

#ifdef ESP_PLATFORM
#include "py/mpconfig.h"            // the board's MOY_NET_WIFI (mpconfigboard.h)
#endif

#if defined(MOY_NET_WIFI) && MOY_NET_WIFI

typedef struct {
    uint8_t driver;         // initialised this boot (one-way: its RAM stays)
    uint8_t on;             // started: the radio is up
    uint8_t connected;      // associated and holding an address
    uint8_t reason;         // the last disconnect's reason, 0 after an address
    uint32_t ip;            // network order, 0 when none
    char ssid[33];          // the network last asked for
} moy_wifi_state_t;

typedef struct {
    char ssid[33];
    int8_t rssi;
    uint8_t auth;           // 0 open
} moy_wifi_ap_t;

#define MOY_WIFI_SCAN_MAX 24

// Up and down are the lease's halves: the spine's WiFi lease calls them, and
// nothing else starts or stops the radio. A connect is asked once and kept
// (the driver re-associates on a loss) until a disconnect or the radio goes
// down; its result is the state, polled.
int moy_wifi_on(void);
void moy_wifi_off(void);
int moy_wifi_connect(const char *ssid, const char *password);
void moy_wifi_disconnect(void);
void moy_wifi_state(moy_wifi_state_t *out);
// A blocking scan into `out`: the count, or -1 when the radio is down.
int moy_wifi_scan(moy_wifi_ap_t *out, int max);
int moy_wifi_mac(uint8_t mac[6]);
// The power-save mode, set first when `set` is 0 or more; -1 before the driver.
int moy_wifi_ps(int set);

#endif

#endif // MOY_NET_H
