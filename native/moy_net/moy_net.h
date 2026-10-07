// moy_net: the links (docs/kernel_survival_2026-10.md section 6).
//
// The wire's pure half, one body for every tier: the HTTP request parser and
// response head (moy_http.c), the sync batch's envelope (moy_sync.c) and the
// WiFi credential rules (moy_wifi.c). Nothing here touches a socket, a radio
// or a file; the boards, the browser, the unix port and CPython (by ctypes,
// runtime/net_binding.py) all call these, and fuzz_net.c runs them under the
// sanitizers. They read untrusted bytes: every span they return lies inside
// the buffer they were given.

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
