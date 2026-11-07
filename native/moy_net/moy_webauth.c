// moy_webauth: the webhost's sign-in, its board end. The protocol is
// moy_seal.h's; the routes and the backoff's rules are moy_webhost.c's
// header. What this file holds:
//
// THE KEYS FILE, `<system>/webkeys`, the shell's /etc/webkeys: the board's id
// on its first line ("moy-webkeys 1 ID") and a paired browser a line, "KID
// KEY CREATED SEEN NAME" (hex, hex, Unix seconds, moy_net_time; the browser's
// own name for itself). Read at config, written whole (through a .tmp and a
// replace) when a browser pairs, is dropped, or was last seen an hour or more
// before a sign-in. Its keys are secrets: the file is the owner's, as
// system/password is, and no route serves it.
//
// THE CHALLENGES: a hello's nonce is good for MOY_WA_NONCE_MS and one
// credential, right or wrong. A new hello takes a free slot, else the oldest
// one not running a PAKE.
//
// THE SESSIONS: MOY_WA_SESSIONS, each the keys of one sign-in, ended by
// MOY_WA_SESSION_MS unused, by a newer sign-in needing its slot (the least
// recently used goes), or by its browser's key being dropped. A request's
// sequence number is taken once: above the highest seen, or within
// MOY_WA_WINDOW below it and not yet taken (a page's parallel requests can
// arrive out of order; none arrives twice).
//
// THE PAKE runs on a worker (a board's task with a PSRAM stack, made at the
// first and parked between; a host's thread), so the console's task never
// waits for the curve. Its answer goes out at a later poll (moy_wa_later).
// A PAKE counts as a wrong password from its start, and its confirm clears
// it, so a run left unconfirmed spends a guess.

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "moy_json.h"
#include "moy_net.h"
#include "moy_vol.h"
#include "moy_webauth.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#else
#include <pthread.h>
#endif

extern bool moy_flush_drain(void) __attribute__((weak));
// The web terminal's bridge (native/moy_tty/moy_tty_ws.c): end its session.
extern void moy_web_term_cut(void) __attribute__((weak));

#define KEYS_FILE "/webkeys"
#define PW_FILE "/password"
#define FILE_MAX 4096
#define SEEN_EVERY_S 3600
#define SALT_MAX 64
#define JOB_STACK 8192

typedef struct {
    uint8_t used;
    uint8_t kid[MOY_SEAL_KID], key[MOY_SEAL_KEY];
    int64_t created, seen;
    char name[MOY_WA_NAME];
} wkey_t;

enum { CH_FREE, CH_ISSUED, CH_PAKE, CH_CONFIRM };

typedef struct {
    uint8_t state;
    uint8_t nb[MOY_SEAL_RAND], nc[MOY_SEAL_RAND];
    uint32_t at;
    uint8_t ke[MOY_SEAL_KEY], ca[MOY_SEAL_MAC];
    char name[MOY_WA_NAME];
} chal_t;

typedef struct {
    uint8_t used;
    uint8_t sid[MOY_SEAL_SID], kid[MOY_SEAL_KID];
    moy_seal_keys_t k;
    uint64_t top, win;
    uint32_t at;
} sess_t;

typedef struct {
    uint32_t peer, seen, until;         // the address, its last try, its window's end
    uint16_t fails;                     // wrong credentials since its last right one
    uint8_t used, paired;               // paired: signed in since the count was cleared
} src_t;

enum { JOB_IDLE, JOB_RUN, JOB_DONE };

typedef struct {
    int state;                          // JOB_*, read and written atomically
    int slot;
    moy_seal_hs_t hs;
    uint8_t digest[32], x[MOY_SEAL_POINT];
    uint8_t y[MOY_SEAL_POINT], ke[MOY_SEAL_KEY], ca[MOY_SEAL_MAC], cb[MOY_SEAL_MAC];
    int err;
} job_t;

typedef struct {
    uint8_t on;
    char dir[MOY_WEB_PATH_MAX];
    uint8_t id[MOY_SEAL_RAND];
    wkey_t keys[MOY_WA_KEYS];
    chal_t ch[MOY_WA_CHALLENGES];
    sess_t ss[MOY_WA_SESSIONS];
    uint8_t token[MOY_SEAL_RAND];
    uint32_t token_at;
    uint8_t token_on, paired, term_on;
    uint8_t term_kid[MOY_SEAL_KID];
    src_t src[MOY_WEB_AUTH_SOURCES];
    uint32_t all_fails, all_last, all_until;
    job_t job;
    uintptr_t worker;
    char text[FILE_MAX];                // the keys file, read or being written
} wa_t;

static wa_t *A;

static int64_t now_s(void) {
    return moy_net_time();
}

// A board's printf and scanf (newlib nano) have no %lld: a time's decimal is
// written here, ending at `buf`'s end, and read back with strtoll.
static const char *dec64(int64_t v, char *buf, size_t cap) {
    char *p = buf + cap;
    uint64_t u = v < 0 ? 0u - (uint64_t)v : (uint64_t)v;
    *--p = '\0';
    do {
        *--p = (char)('0' + u % 10u);
        u /= 10u;
    } while (u);
    if (v < 0) {
        *--p = '-';
    }
    return p;
}

int moy_wa_scan_times(const char *p, long long *a, long long *b, const char **rest) {
    char *e;
    *a = strtoll(p, &e, 10);
    if (e == p) {
        return 0;
    }
    p = e;
    *b = strtoll(p, &e, 10);
    if (e == p) {
        return 0;
    }
    for (p = e; *p == ' '; p++) {
    }
    *rest = p;
    return 1;
}

static int fresh(uint32_t at, uint32_t ms) {
    return (uint32_t)(moy_net_ms() - at) < ms;
}

// -- the store ------------------------------------------------------------------------

static void path_of(char *out, size_t cap, const char *leaf) {
    snprintf(out, cap, "%s%s", A->dir, leaf);
}

// The file at `leaf` into A->text, NUL-terminated: its length, or -1.
static int read_text(const char *leaf) {
    char path[MOY_WEB_PATH_MAX + 16];
    path_of(path, sizeof path, leaf);
    if (moy_flush_drain != NULL) {
        moy_flush_drain();
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f = NULL;
    int g = moy_vol_gate_enter(path);
    int n = -1;
    if (moy_vol_at(path, &v, &rest) == 0 && moy_vol_open(&v, rest, MOY_VOL_READ, &f) == 0) {
        size_t got = 0, k = 1;
        while (got < FILE_MAX - 1u && k > 0) {
            if (moy_vol_read(f, A->text + got, FILE_MAX - 1u - got, &k) != 0) {
                got = 0;
                break;
            }
            got += k;
        }
        moy_vol_close(f);
        A->text[got] = '\0';
        n = (int)got;
    }
    moy_vol_gate_leave(g);
    return n;
}

static int write_text(size_t n) {
    char path[MOY_WEB_PATH_MAX + 16], tmp[MOY_WEB_PATH_MAX + 20];
    path_of(path, sizeof path, KEYS_FILE);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (moy_flush_drain != NULL) {
        moy_flush_drain();
    }
    moy_vol_t v;
    const char *rest, *trest;
    moy_vol_file_t *f = NULL;
    int g = moy_vol_gate_enter(path);
    int rc = moy_vol_at(path, &v, &rest);
    if (rc == 0) {
        moy_vol_mkdir(&v, A->dir + (rest - path));
        rc = moy_vol_at(tmp, &v, &trest);
    }
    if (rc == 0) {
        rc = moy_vol_open(&v, trest, MOY_VOL_WRITE, &f);
    }
    if (rc == 0) {
        rc = moy_vol_write(f, A->text, n);
        int c = moy_vol_close(f);
        rc = rc ? rc : c;
    }
    if (rc == 0) {
        rc = moy_vol_replace(&v, trest, rest);
    }
    moy_vol_gate_leave(g);
    if (rc != 0) {
        printf("WEBAUTH keys not saved: %d\n", rc);
    }
    return rc;
}

static void save(void) {
    char id[2 * MOY_SEAL_RAND + 1];
    moy_seal_hex(A->id, MOY_SEAL_RAND, id);
    size_t n = (size_t)snprintf(A->text, FILE_MAX, "moy-webkeys 1 %s\n", id);
    for (int i = 0; i < MOY_WA_KEYS && n < FILE_MAX; i++) {
        wkey_t *k = &A->keys[i];
        if (!k->used) {
            continue;
        }
        char kid[2 * MOY_SEAL_KID + 1], key[2 * MOY_SEAL_KEY + 1];
        moy_seal_hex(k->kid, MOY_SEAL_KID, kid);
        moy_seal_hex(k->key, MOY_SEAL_KEY, key);
        char cr[24], se[24];
        n += (size_t)snprintf(A->text + n, FILE_MAX - n, "%s %s %s %s %s\n", kid, key,
                              dec64(k->created, cr, sizeof cr), dec64(k->seen, se, sizeof se),
                              k->name);
    }
    if (n < FILE_MAX) {
        write_text(n);
    }
    moy_seal_wipe(A->text, sizeof A->text);
}

static void load(void) {
    memset(A->keys, 0, sizeof A->keys);
    int n = read_text(KEYS_FILE);
    const char *p = A->text, *end = A->text + (n > 0 ? n : 0);
    int have_id = 0, k = 0;
    while (p < end) {
        const char *e = memchr(p, '\n', (size_t)(end - p));
        if (e == NULL) {
            e = end;
        }
        size_t ln = (size_t)(e - p);
        if (!have_id) {
            have_id = ln == 14 + 2 * MOY_SEAL_RAND && memcmp(p, "moy-webkeys 1 ", 14) == 0
                      && moy_seal_unhex(p + 14, 2 * MOY_SEAL_RAND, A->id, MOY_SEAL_RAND);
            if (!have_id) {
                break;                  // not ours: a fresh id, and nothing paired
            }
        } else if (k < MOY_WA_KEYS && ln > 2 * MOY_SEAL_KID + 2 * MOY_SEAL_KEY + 6) {
            wkey_t *w = &A->keys[k];
            char line[160];
            size_t c = ln < sizeof line - 1 ? ln : sizeof line - 1;
            memcpy(line, p, c);
            line[c] = '\0';
            char kid[24], key[40];
            long long cr = 0, se = 0;
            int at = 0;
            const char *name = "";
            if (sscanf(line, "%20s %36s%n", kid, key, &at) == 2 && at > 0
                && moy_wa_scan_times(line + at, &cr, &se, &name)
                && moy_seal_unhex(kid, strlen(kid), w->kid, MOY_SEAL_KID)
                && moy_seal_unhex(key, strlen(key), w->key, MOY_SEAL_KEY)) {
                w->used = 1;
                w->created = cr;
                w->seen = se;
                snprintf(w->name, sizeof w->name, "%s", name);
                k++;
            }
            moy_seal_wipe(line, sizeof line);
        }
        p = e + 1;
    }
    moy_seal_wipe(A->text, sizeof A->text);
    if (!have_id) {
        moy_seal_random(A->id, MOY_SEAL_RAND);
        memset(A->keys, 0, sizeof A->keys);
        save();
    }
}

// The password's digest and salt from system/password: 1, else 0 (none set).
static int password(uint8_t digest[32], char salt[SALT_MAX]) {
    int n = read_text(PW_FILE);
    int ok = n > 0 && moy_seal_pw_parse(A->text, (size_t)n, salt, SALT_MAX, digest);
    moy_seal_wipe(A->text, sizeof A->text);
    return ok;
}

// -- the backoff (moy_webhost.c's header) -------------------------------------------------

// A window's end is never 0 (0: no window), so a clock past 2^31 ms of uptime
// does not read an empty record as one ending 24 days on.
static int32_t left(uint32_t until, uint32_t now) {
    if (until == 0) {
        return 0;
    }
    int32_t d = (int32_t)(until - now);
    return d > 0 ? d : 0;
}

static uint32_t ends(uint32_t now, uint32_t ms) {
    uint32_t t = now + ms;
    return t != 0 ? t : 1;
}

static uint32_t window(uint32_t past) {
    uint32_t ms = MOY_WEB_AUTH_WAIT_MS << (past < 16u ? past : 16u);
    return ms < MOY_WEB_AUTH_WAIT_MAX_MS ? ms : MOY_WEB_AUTH_WAIT_MAX_MS;
}

// The source's record, or a new one in place of the least recently seen, a
// signed-in source's last.
static src_t *source(uint32_t peer, uint32_t now) {
    src_t *best = NULL;
    for (int i = 0; i < MOY_WEB_AUTH_SOURCES; i++) {
        src_t *s = &A->src[i];
        if (s->used && s->peer == peer) {
            s->seen = now;
            return s;
        }
        if (best == NULL || !s->used
            || (best->used && (s->paired < best->paired
                               || (s->paired == best->paired
                                   && (int32_t)(s->seen - best->seen) < 0)))) {
            best = s;
        }
    }
    memset(best, 0, sizeof(*best));
    best->used = 1;
    best->peer = peer;
    best->seen = now;
    return best;
}

uint32_t moy_wa_wait(uint32_t peer) {
    if (A == NULL || !A->on) {
        return 0;
    }
    uint32_t now = moy_net_ms();
    if (A->all_fails && (uint32_t)(now - A->all_last) >= MOY_WEB_AUTH_FORGET_MS) {
        A->all_fails = A->all_until = 0;
    }
    src_t *s = source(peer, now);
    int32_t w = left(s->until, now);
    if (!s->paired && A->all_until) {
        int32_t a = left(A->all_until, now);
        w = a > w ? a : w;
    }
    return (uint32_t)w;
}

void moy_wa_note(uint32_t peer, int ok) {
    if (A == NULL || !A->on) {
        return;
    }
    uint32_t now = moy_net_ms();
    src_t *s = source(peer, now);
    if (ok) {
        s->fails = 0;
        s->until = 0;
        s->paired = 1;
        return;
    }
    if (s->fails < UINT16_MAX) {
        s->fails++;
    }
    if (s->fails > MOY_WEB_AUTH_FREE) {
        s->until = ends(now, window(s->fails - MOY_WEB_AUTH_FREE - 1u));
    }
    A->all_fails++;
    A->all_last = now;
    if (A->all_fails > MOY_WEB_AUTH_FREE_ALL) {
        A->all_until = ends(now, window(A->all_fails - MOY_WEB_AUTH_FREE_ALL - 1u));
    }
}

// -- configuration ----------------------------------------------------------------------

int moy_wa_config(const char *system_dir) {
    if (A == NULL) {
        A = moy_net_alloc(sizeof *A);
        if (A == NULL) {
            return MOY_ENOMEM;
        }
    }
    if (system_dir == NULL || system_dir[0] == '\0') {
        A->on = 0;
        return 0;
    }
    if (strlen(system_dir) >= sizeof A->dir) {
        return MOY_EINVAL;              // never a sign-in over a path cut short
    }
    if (A->on && strcmp(A->dir, system_dir) == 0) {
        return 0;
    }
    snprintf(A->dir, sizeof A->dir, "%s", system_dir);
    memset(A->ss, 0, sizeof A->ss);
    memset(A->ch, 0, sizeof A->ch);
    memset(A->src, 0, sizeof A->src);
    A->all_fails = A->all_until = 0;
    A->token_on = 0;
    A->on = 1;
    load();
    return 0;
}

int moy_wa_open(void) {
    #ifdef ESP_PLATFORM
    return 0;
    #else
    return A == NULL || !A->on;
    #endif
}

void moy_wa_board_id(char out[2 * MOY_SEAL_RAND + 1]) {
    if (A == NULL || !A->on) {
        out[0] = '\0';
        return;
    }
    moy_seal_hex(A->id, MOY_SEAL_RAND, out);
}

void moy_wa_token(char out[2 * MOY_SEAL_RAND + 1]) {
    if (A == NULL || !A->on) {
        out[0] = '\0';
        return;
    }
    moy_seal_random(A->token, MOY_SEAL_RAND);
    A->token_at = moy_net_ms();
    A->token_on = 1;
    moy_seal_hex(A->token, MOY_SEAL_RAND, out);
}

int moy_wa_paired(void) {
    if (A == NULL || !A->paired) {
        return 0;
    }
    A->paired = 0;
    return 1;
}

size_t moy_wa_keys(char *out, size_t cap) {
    size_t n = 0;
    if (cap) {
        out[0] = '\0';
    }
    if (A == NULL || !A->on) {
        return 0;
    }
    for (int i = 0; i < MOY_WA_KEYS; i++) {
        wkey_t *k = &A->keys[i];
        if (!k->used || n >= cap) {
            continue;
        }
        char kid[2 * MOY_SEAL_KID + 1];
        moy_seal_hex(k->kid, MOY_SEAL_KID, kid);
        char cr[24], se[24];
        int w = snprintf(out + n, cap - n, "%s %s %s %s\n", kid, dec64(k->created, cr, sizeof cr),
                         dec64(k->seen, se, sizeof se), k->name);
        n = w > 0 && (size_t)w < cap - n ? n + (size_t)w : n;
    }
    return n;
}

static void end_sessions(const uint8_t *kid) {
    for (int i = 0; i < MOY_WA_SESSIONS; i++) {
        if (A->ss[i].used && (kid == NULL || memcmp(A->ss[i].kid, kid, MOY_SEAL_KID) == 0)) {
            moy_seal_wipe(&A->ss[i], sizeof A->ss[i]);
        }
    }
    if (A->term_on && (kid == NULL || memcmp(A->term_kid, kid, MOY_SEAL_KID) == 0)) {
        A->term_on = 0;
        if (moy_web_term_cut != NULL) {
            moy_web_term_cut();
        }
    }
}

int moy_wa_drop(const char *kid) {
    if (A == NULL || !A->on || kid == NULL) {
        return 0;
    }
    int all = strcmp(kid, "all") == 0, gone = 0;
    uint8_t want[MOY_SEAL_KID];
    if (!all && !moy_seal_unhex(kid, strlen(kid), want, MOY_SEAL_KID)) {
        return 0;
    }
    for (int i = 0; i < MOY_WA_KEYS; i++) {
        wkey_t *k = &A->keys[i];
        if (k->used && (all || memcmp(k->kid, want, MOY_SEAL_KID) == 0)) {
            end_sessions(k->kid);
            moy_seal_wipe(k, sizeof *k);
            gone++;
        }
    }
    if (gone) {
        save();
    }
    return gone;
}

void moy_wa_term(const moy_wa_seal_t *s) {
    if (A == NULL) {
        return;
    }
    A->term_on = s != NULL && s->on;
    if (A->term_on) {
        memcpy(A->term_kid, s->kid, MOY_SEAL_KID);
    }
}

// -- the sign-in ----------------------------------------------------------------------

static chal_t *challenge_new(void) {
    chal_t *best = NULL;
    for (int i = 0; i < MOY_WA_CHALLENGES; i++) {
        chal_t *c = &A->ch[i];
        if (c->state != CH_FREE && !fresh(c->at, MOY_WA_NONCE_MS) && c->state != CH_PAKE) {
            c->state = CH_FREE;
        }
        if (c->state == CH_PAKE) {
            continue;
        }
        if (best == NULL || (best->state != CH_FREE
                             && (c->state == CH_FREE || (int32_t)(c->at - best->at) < 0))) {
            best = c;
        }
    }
    if (best != NULL) {
        moy_seal_wipe(best, sizeof *best);
    }
    return best;
}

// The challenge `nb` names in state `state`, fresh, taken (its slot freed for
// the caller to refill); NULL when there is none.
static chal_t *challenge_take(const uint8_t nb[MOY_SEAL_RAND], int state) {
    for (int i = 0; i < MOY_WA_CHALLENGES; i++) {
        chal_t *c = &A->ch[i];
        if (c->state == state && memcmp(c->nb, nb, MOY_SEAL_RAND) == 0) {
            int ok = fresh(c->at, MOY_WA_NONCE_MS);
            c->state = CH_FREE;
            return ok ? c : NULL;
        }
    }
    return NULL;
}

static wkey_t *key_of(const uint8_t kid[MOY_SEAL_KID]) {
    for (int i = 0; i < MOY_WA_KEYS; i++) {
        if (A->keys[i].used && memcmp(A->keys[i].kid, kid, MOY_SEAL_KID) == 0) {
            return &A->keys[i];
        }
    }
    return NULL;
}

// A new browser's key, in a free place or the least recently seen one's.
static wkey_t *key_new(const char *name) {
    wkey_t *best = NULL;
    for (int i = 0; i < MOY_WA_KEYS; i++) {
        wkey_t *k = &A->keys[i];
        if (best == NULL || !k->used || (best->used && k->seen < best->seen)) {
            best = k;
        }
    }
    if (best->used) {
        end_sessions(best->kid);
    }
    moy_seal_wipe(best, sizeof *best);
    best->used = 1;
    do {
        moy_seal_random(best->kid, MOY_SEAL_KID);
    } while (key_of(best->kid) != best);
    moy_seal_random(best->key, MOY_SEAL_KEY);
    best->created = best->seen = now_s();
    snprintf(best->name, sizeof best->name, "%s", name);
    return best;
}

static sess_t *session_new(void) {
    sess_t *best = NULL;
    for (int i = 0; i < MOY_WA_SESSIONS; i++) {
        sess_t *s = &A->ss[i];
        if (s->used && !fresh(s->at, MOY_WA_SESSION_MS)) {
            moy_seal_wipe(s, sizeof *s);
        }
        if (best == NULL || (best->used && (!s->used || (int32_t)(s->at - best->at) < 0))) {
            best = s;
        }
    }
    moy_seal_wipe(best, sizeof *best);
    return best;
}

// A session for `k` after challenge `h`: its answer's "sid" and "proof".
static int signed_in(wkey_t *k, const moy_seal_hs_t *h, char *doc, size_t cap,
                     const char *extra) {
    sess_t *s = session_new();
    s->used = 1;
    moy_seal_random(s->sid, MOY_SEAL_SID);
    memcpy(s->kid, k->kid, MOY_SEAL_KID);
    moy_seal_session(h, k->kid, k->key, &s->k);
    s->at = moy_net_ms();
    uint8_t proof[MOY_SEAL_MAC];
    moy_seal_proof(h, k->kid, k->key, s->sid, proof);
    char sid[2 * MOY_SEAL_SID + 1], ph[2 * MOY_SEAL_MAC + 1];
    moy_seal_hex(s->sid, MOY_SEAL_SID, sid);
    moy_seal_hex(proof, MOY_SEAL_MAC, ph);
    snprintf(doc, cap, "{\"sid\":\"%s\",\"proof\":\"%s\"%s}", sid, ph, extra ? extra : "");
    int64_t t = now_s();
    if (t - k->seen >= SEEN_EVERY_S) {
        k->seen = t;
        save();
    }
    return 200;
}

// A new browser paired: its key minted, wrapped under `secret`, and signed in.
static int paired(const moy_seal_hs_t *h, const uint8_t *secret, size_t sn, const char *name,
                  char *doc, size_t cap) {
    wkey_t *k = key_new(name);
    save();
    A->paired = 1;
    uint8_t wrapped[MOY_SEAL_KEY];
    memcpy(wrapped, k->key, MOY_SEAL_KEY);
    moy_seal_wrap(h, secret, sn, wrapped);
    char kid[2 * MOY_SEAL_KID + 1], key[2 * MOY_SEAL_KEY + 1], extra[96];
    moy_seal_hex(k->kid, MOY_SEAL_KID, kid);
    moy_seal_hex(wrapped, MOY_SEAL_KEY, key);
    snprintf(extra, sizeof extra, ",\"kid\":\"%s\",\"key\":\"%s\"", kid, key);
    return signed_in(k, h, doc, cap, extra);
}

// A string member: its decoded length (< cap, NUL-terminated), or -1.
static int str_of(const char *o, const char *oe, const char *key, char *out, size_t cap) {
    const char *v, *ve;
    if (!moy_json_get(o, oe, key, &v, &ve) || moy_json_kind(v, ve) != MOY_JSON_STR
        || moy_json_strlen(v, ve) >= cap) {
        return -1;
    }
    size_t n = moy_json_str(v, ve, out);
    out[n] = '\0';
    return (int)n;
}

static int hex_of(const char *o, const char *oe, const char *key, uint8_t *out, size_t want) {
    char buf[2 * MOY_SEAL_POINT + 2];
    int n = str_of(o, oe, key, buf, sizeof buf);
    return n >= 0 && moy_seal_unhex(buf, (size_t)n, out, want);
}

// A browser's name as the keys file keeps it: printable ASCII, cut short.
static void name_of(const char *o, const char *oe, char out[MOY_WA_NAME]) {
    char raw[128];
    int n = str_of(o, oe, "name", raw, sizeof raw);
    size_t k = 0;
    for (int i = 0; i < n && k < MOY_WA_NAME - 1; i++) {
        unsigned char c = (unsigned char)raw[i];
        out[k++] = c >= 0x20 && c < 0x7f ? (char)c : '?';
    }
    while (k > 0 && out[k - 1] == ' ') {
        k--;
    }
    out[k] = '\0';
    if (k == 0) {
        snprintf(out, MOY_WA_NAME, "a browser");
    }
}

static int refuse(char *doc, size_t cap, int status, const char *why) {
    snprintf(doc, cap, "{\"error\":\"%s\"}", why);
    return status;
}

#ifdef ESP_PLATFORM
static void job_task(void *arg);
#else
static void *job_thread(void *arg);
#endif

static void run_job(void) {
    job_t *j = &A->job;
    moy_seal_spake_t s;
    int r = moy_seal_spake_start(&s, 1, j->digest);
    if (r == MOY_SEAL_OK) {
        memcpy(j->y, s.mine, MOY_SEAL_POINT);
        r = moy_seal_spake_end(&s, &j->hs, j->x, j->ke, j->ca, j->cb);
    }
    moy_seal_wipe(&s, sizeof s);
    moy_seal_wipe(j->digest, sizeof j->digest);
    j->err = r;
    __atomic_store_n(&j->state, JOB_DONE, __ATOMIC_RELEASE);
}

#ifdef ESP_PLATFORM
static void job_task(void *arg) {
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (__atomic_load_n(&A->job.state, __ATOMIC_ACQUIRE) == JOB_RUN) {
            run_job();
        }
    }
}

static int job_start(void) {
    if (A->worker == 0) {
        TaskHandle_t h = NULL;
        if (xTaskCreatePinnedToCoreWithCaps(job_task, "moy_pake", JOB_STACK, NULL, 1, &h, 0,
                                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
            return -1;
        }
        A->worker = (uintptr_t)h;
    }
    __atomic_store_n(&A->job.state, JOB_RUN, __ATOMIC_RELEASE);
    xTaskNotifyGive((TaskHandle_t)A->worker);
    return 0;
}
#else
static void *job_thread(void *arg) {
    (void)arg;
    run_job();
    return NULL;
}

static int job_start(void) {
    pthread_t t;
    __atomic_store_n(&A->job.state, JOB_RUN, __ATOMIC_RELEASE);
    if (pthread_create(&t, NULL, job_thread, NULL) != 0) {
        __atomic_store_n(&A->job.state, JOB_IDLE, __ATOMIC_RELEASE);
        return -1;
    }
    pthread_detach(t);
    return 0;
}
#endif

int moy_wa_later(char *doc, size_t cap) {
    if (A == NULL || __atomic_load_n(&A->job.state, __ATOMIC_ACQUIRE) != JOB_DONE) {
        return 0;
    }
    job_t *j = &A->job;
    chal_t *c = &A->ch[j->slot];
    int status;
    if (j->err != MOY_SEAL_OK) {
        c->state = CH_FREE;
        status = refuse(doc, cap, j->err == MOY_SEAL_EPOINT ? 403 : 503,
                        j->err == MOY_SEAL_EPOINT ? "password" : "busy");
    } else {
        c->state = CH_CONFIRM;
        c->at = moy_net_ms();
        memcpy(c->ke, j->ke, MOY_SEAL_KEY);
        memcpy(c->ca, j->ca, MOY_SEAL_MAC);
        char y[2 * MOY_SEAL_POINT + 1], cb[2 * MOY_SEAL_MAC + 1];
        moy_seal_hex(j->y, MOY_SEAL_POINT, y);
        moy_seal_hex(j->cb, MOY_SEAL_MAC, cb);
        snprintf(doc, cap, "{\"Y\":\"%s\",\"cB\":\"%s\"}", y, cb);
        status = 200;
    }
    moy_seal_wipe(j, sizeof *j);
    __atomic_store_n(&j->state, JOB_IDLE, __ATOMIC_RELEASE);
    return status;
}

static int hello(char *doc, size_t cap) {
    chal_t *c = challenge_new();
    if (c == NULL) {
        return refuse(doc, cap, 503, "busy");
    }
    c->state = CH_ISSUED;
    c->at = moy_net_ms();
    moy_seal_random(c->nb, MOY_SEAL_RAND);
    char id[2 * MOY_SEAL_RAND + 1], nb[2 * MOY_SEAL_RAND + 1], salt[SALT_MAX];
    uint8_t digest[32];
    int pw = password(digest, salt);
    moy_seal_wipe(digest, sizeof digest);
    moy_seal_hex(A->id, MOY_SEAL_RAND, id);
    moy_seal_hex(c->nb, MOY_SEAL_RAND, nb);
    snprintf(doc, cap, "{\"v\":1,\"id\":\"%s\",\"nb\":\"%s\",\"pw\":%s,\"salt\":\"%s\"}", id, nb,
             pw ? "true" : "false", pw ? salt : "");
    return 200;
}

int moy_wa_auth(uint32_t peer, const char *body, size_t n, char *doc, size_t cap) {
    if (A == NULL || !A->on) {
        #ifdef ESP_PLATFORM
        return refuse(doc, cap, 503, "auth");
        #else
        snprintf(doc, cap, "{\"v\":1,\"open\":true}");
        return 200;
        #endif
    }
    const char *end = body + n;
    const char *o = moy_json_ws(body, end);
    const char *oe = o < end ? moy_json_value(o, end, 0) : NULL;
    char op[16];
    if (moy_json_valid(body, n) != MOY_JSON_OK || oe == NULL
        || moy_json_kind(o, oe) != MOY_JSON_OBJ || str_of(o, oe, "op", op, sizeof op) < 0) {
        return refuse(doc, cap, 400, "bad auth");
    }
    uint32_t ms = moy_wa_wait(peer);
    if (ms) {
        snprintf(doc, cap, "{\"error\":\"wait\",\"ms\":%u}", (unsigned)ms);
        return 429;
    }
    if (strcmp(op, "hello") == 0) {
        return hello(doc, cap);
    }
    moy_seal_hs_t h;
    memcpy(h.id, A->id, MOY_SEAL_RAND);
    if (!hex_of(o, oe, "nb", h.nb, MOY_SEAL_RAND)) {
        return refuse(doc, cap, 400, "bad auth");
    }
    if (strcmp(op, "confirm") == 0) {
        uint8_t ca[MOY_SEAL_MAC];
        chal_t *c = challenge_take(h.nb, CH_CONFIRM);
        if (c == NULL || !hex_of(o, oe, "cA", ca, MOY_SEAL_MAC)
            || !moy_seal_eq(ca, c->ca, MOY_SEAL_MAC)) {
            if (c != NULL) {
                moy_seal_wipe(c, sizeof *c);
            }
            return refuse(doc, cap, 403, "password");
        }
        memcpy(h.nc, c->nc, MOY_SEAL_RAND);
        moy_wa_note(peer, 1);
        int st = paired(&h, c->ke, MOY_SEAL_KEY, c->name, doc, cap);
        moy_seal_wipe(c, sizeof *c);
        return st;
    }
    if (!hex_of(o, oe, "nc", h.nc, MOY_SEAL_RAND)) {
        return refuse(doc, cap, 400, "bad auth");
    }
    chal_t *c = challenge_take(h.nb, CH_ISSUED);
    if (strcmp(op, "key") == 0) {
        uint8_t kid[MOY_SEAL_KID], mac[MOY_SEAL_MAC], want[MOY_SEAL_MAC];
        wkey_t *k = hex_of(o, oe, "kid", kid, MOY_SEAL_KID) ? key_of(kid) : NULL;
        int ok = c != NULL && k != NULL && hex_of(o, oe, "mac", mac, MOY_SEAL_MAC);
        if (ok) {
            moy_seal_key_mac(&h, kid, k->key, want);
            ok = moy_seal_eq(mac, want, MOY_SEAL_MAC);
        }
        moy_wa_note(peer, ok);
        return ok ? signed_in(k, &h, doc, cap, NULL) : refuse(doc, cap, 403, "key");
    }
    char name[MOY_WA_NAME];
    name_of(o, oe, name);
    if (strcmp(op, "token") == 0) {
        uint8_t mac[MOY_SEAL_MAC], want[MOY_SEAL_MAC];
        int ok = c != NULL && A->token_on && fresh(A->token_at, MOY_WA_TOKEN_MS)
                 && hex_of(o, oe, "mac", mac, MOY_SEAL_MAC);
        if (ok) {
            char raw[128];
            int rn = str_of(o, oe, "name", raw, sizeof raw);
            moy_seal_token_mac(&h, A->token, raw, rn > 0 ? (size_t)rn : 0, want);
            ok = moy_seal_eq(mac, want, MOY_SEAL_MAC);
        }
        moy_wa_note(peer, ok);
        if (!ok) {
            return refuse(doc, cap, 403, "token");
        }
        uint8_t t[MOY_SEAL_RAND];
        memcpy(t, A->token, MOY_SEAL_RAND);
        moy_seal_wipe(A->token, MOY_SEAL_RAND);
        A->token_on = 0;
        int st = paired(&h, t, MOY_SEAL_RAND, name, doc, cap);
        moy_seal_wipe(t, sizeof t);
        return st;
    }
    if (strcmp(op, "pake") == 0) {
        job_t *j = &A->job;
        uint8_t x[MOY_SEAL_POINT];
        if (c == NULL || !hex_of(o, oe, "X", x, MOY_SEAL_POINT)) {
            return refuse(doc, cap, 403, "password");
        }
        if (__atomic_load_n(&j->state, __ATOMIC_ACQUIRE) != JOB_IDLE) {
            return refuse(doc, cap, 503, "busy");
        }
        char salt[SALT_MAX];
        if (!password(j->digest, salt)) {
            return refuse(doc, cap, 403, "nopw");
        }
        moy_wa_note(peer, 0);
        c->state = CH_PAKE;
        c->at = moy_net_ms();
        memcpy(c->nc, h.nc, MOY_SEAL_RAND);
        memcpy(c->name, name, sizeof c->name);
        j->slot = (int)(c - A->ch);
        j->hs = h;
        memcpy(j->x, x, MOY_SEAL_POINT);
        if (job_start() != 0) {
            c->state = CH_FREE;
            moy_seal_wipe(j, sizeof *j);
            return refuse(doc, cap, 503, "busy");
        }
        return 0;
    }
    return refuse(doc, cap, 400, "bad auth");
}

// -- a sealed request -------------------------------------------------------------------

static int seq_of(const char *v, size_t n, uint64_t *out) {
    uint64_t x = 0;
    if (n == 0 || n > 15) {
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        if (v[i] < '0' || v[i] > '9') {
            return 0;
        }
        x = x * 10u + (uint64_t)(v[i] - '0');
    }
    *out = x;
    return x != 0;
}

int moy_wa_check(const char *method, size_t mn, const char *target, size_t tn, char *body,
                 size_t *bn, moy_wa_seal_t *out) {
    memset(out, 0, sizeof *out);
    if (A == NULL || !A->on) {
        return moy_wa_open() ? MOY_WA_OK : MOY_WA_NONE;
    }
    const char *v;
    size_t vn;
    uint8_t sid[MOY_SEAL_SID];
    moy_http_query(target, tn, "s", 1, &v, &vn);
    if (!moy_seal_unhex(v, vn, sid, MOY_SEAL_SID)) {
        return MOY_WA_NONE;
    }
    sess_t *s = NULL;
    for (int i = 0; i < MOY_WA_SESSIONS && s == NULL; i++) {
        sess_t *c = &A->ss[i];
        if (c->used && memcmp(c->sid, sid, MOY_SEAL_SID) == 0) {
            if (fresh(c->at, MOY_WA_SESSION_MS)) {
                s = c;
            } else {
                moy_seal_wipe(c, sizeof *c);
            }
        }
    }
    if (s == NULL) {
        return MOY_WA_NONE;
    }
    uint64_t seq;
    moy_http_query(target, tn, "n", 1, &v, &vn);
    if (!seq_of(v, vn, &seq)) {
        return MOY_WA_BAD;
    }
    if (seq <= s->top && (s->top - seq >= MOY_WA_WINDOW || (s->win >> (s->top - seq) & 1u))) {
        return MOY_WA_BAD;              // taken, or too far behind to tell
    }
    uint8_t tag[MOY_SEAL_TAG];
    size_t an = tn, pn = *bn;
    if (tn > 3 + 2 * MOY_SEAL_TAG && memcmp(target + tn - 3 - 2 * MOY_SEAL_TAG, "&t=", 3) == 0) {
        an = tn - 3 - 2 * MOY_SEAL_TAG;
        if (pn != 0 || !moy_seal_unhex(target + an + 3, 2 * MOY_SEAL_TAG, tag, MOY_SEAL_TAG)) {
            return MOY_WA_BAD;
        }
    } else {
        if (pn < MOY_SEAL_TAG) {
            return MOY_WA_BAD;
        }
        pn -= MOY_SEAL_TAG;
        memcpy(tag, body + pn, MOY_SEAL_TAG);
    }
    if (moy_seal_req(0, s->k.kc, seq, method, mn, target, an, (uint8_t *)body, pn, tag)
        != MOY_SEAL_OK) {
        return MOY_WA_BAD;
    }
    if (seq > s->top) {
        uint64_t d = seq - s->top;
        s->win = d >= 64 ? 0 : s->win << d;
        s->top = seq;
        s->win |= 1u;
    } else {
        s->win |= (uint64_t)1 << (s->top - seq);
    }
    s->at = moy_net_ms();
    *bn = pn;
    out->on = 1;
    out->seq = seq;
    memcpy(out->kc, s->k.kc, MOY_SEAL_KEY);
    memcpy(out->kb, s->k.kb, MOY_SEAL_KEY);
    memcpy(out->kid, s->kid, MOY_SEAL_KID);
    return MOY_WA_OK;
}
