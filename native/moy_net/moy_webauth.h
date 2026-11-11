// moy_webauth: the webhost's sign-in, its board end (moy_webauth.c's header;
// the protocol is moy_seal.h's). moy_webhost.c is its only caller, on the
// console's task; a password's PAKE runs on a worker of its own.

#ifndef MOY_WEBAUTH_H
#define MOY_WEBAUTH_H

#include <stddef.h>
#include <stdint.h>

#include "moy_seal.h"

#define MOY_WA_KEYS 16                  // paired browsers kept
#define MOY_WA_NAME 40                  // a browser's name, its NUL included
#define MOY_WA_CHALLENGES 8
#define MOY_WA_PEER_CHALLENGES 2        // one source's at once
#define MOY_WA_SESSIONS 8
#define MOY_WA_NONCE_MS 30000u          // a hello's nonce, and a PAKE's wait for its confirm
#define MOY_WA_SESSION_MS 1800000u      // a session nobody used for this long is gone
#define MOY_WA_TOKEN_MS 600000u         // a QR's token
#define MOY_WA_WINDOW 64                // a session's sequence numbers taken out of order

// The answer a sealed request gets, and the stream a /term upgrade carries.
typedef struct moy_wa_seal {
    uint8_t on;                         // 0: the open host, nothing sealed
    uint64_t seq;
    uint8_t kc[MOY_SEAL_KEY], kb[MOY_SEAL_KEY];
    uint8_t kid[MOY_SEAL_KID], sid[MOY_SEAL_SID];
} moy_wa_seal_t;

enum {
    MOY_WA_OK = 0,
    MOY_WA_NONE = 1,                    // no session named: 401 {"error":"session"}
    MOY_WA_BAD = 2,                     // a seal that does not open: 401 {"error":"auth"}
};

// The system folder the keys file (`webkeys`) and the password live in; NULL
// leaves the sign-in off, which only a host may (an open host: a test, the
// dev server). 0, MOY_ENOMEM, or MOY_EINVAL for a folder past
// MOY_WEB_PATH_MAX, which is never cut short.
int moy_wa_config(const char *system_dir);
int moy_wa_open(void);                  // 1: no sign-in (an open host)

// The backoff (moy_webhost.c's header): the ms `peer` waits before a
// credential of its is looked at, and a credential's verdict.
uint32_t moy_wa_wait(uint32_t peer);
void moy_wa_note(uint32_t peer, int ok);

// POST /auth: the answer's JSON into doc and its HTTP status, or 0 while the
// password's PAKE runs (moy_wa_later answers it). A credential is refused
// 409 {"error":"nonce"} when its hello's nonce is gone (another hello took its
// place, or it ran out: ask again, nothing counted), 403 {"error":"kid"} for a
// key this board does not know (the page forgets it), and 403 with "key",
// "token" or "password" for one that is wrong.
int moy_wa_auth(uint32_t peer, const char *body, size_t n, char *doc, size_t cap);
// The PAKE's answer once its worker is done: its status, else 0.
int moy_wa_later(char *doc, size_t cap);

// A gated request's seal: MOY_WA_OK with the body opened in place (*bn its
// plain length) and `out` the answer's seal, else MOY_WA_NONE or MOY_WA_BAD.
// The open host answers MOY_WA_OK with `out->on` 0.
int moy_wa_check(const char *method, size_t mn, const char *target, size_t tn, char *body,
                 size_t *bn, moy_wa_seal_t *out);

// The QR's token: a fresh one minted (the last one dropped), as 32 hex
// digits, valid MOY_WA_TOKEN_MS and once; "" while the sign-in is off.
void moy_wa_token(char out[2 * MOY_SEAL_RAND + 1]);
void moy_wa_board_id(char out[2 * MOY_SEAL_RAND + 1]);
// The paired browsers, a line each: "KID CREATED SEEN NAME\n" (Unix
// seconds). Its length (cut at cap).
size_t moy_wa_keys(char *out, size_t cap);
// A line's CREATED and SEEN, read from `p` (a board's scanf has no %lld), and
// where NAME starts: 1, or 0 when either is missing.
int moy_wa_scan_times(const char *p, long long *a, long long *b, const char **rest);
// Forget one browser (16 hex digits) or every one ("all"): how many went.
// Its sessions end, and a web terminal it holds is cut.
int moy_wa_drop(const char *kid);
// Every session ended and a web terminal cut, the keys kept (a password
// changed or removed): how many sessions went.
int moy_wa_signout(void);
// The webhost's poll: sessions unused past MOY_WA_SESSION_MS end, and a web
// terminal whose session is gone is cut.
void moy_wa_poll(void);
// A browser paired since the last call: 1, and the flag cleared.
int moy_wa_paired(void);
// The /term upgrade's session: its key dropped, the session's end (unused,
// evicted, signed out) cuts the terminal.
void moy_wa_term(const moy_wa_seal_t *s);

#endif // MOY_WEBAUTH_H
