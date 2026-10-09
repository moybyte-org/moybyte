// moy_match: the link's protocol and the lockstep session (moy_match.h has
// the contract and the measurements behind it).

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "moy_match.h"
#include "moy_json.h"

const uint8_t MOY_MATCH_BROADCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

static void say(moy_match_t *m, const char *fmt, ...) {
    char line[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    if (m->io != NULL && m->io->say != NULL) {
        m->io->say(m->io->ctx, line);
    } else {
        printf("%s\n", line);
    }
}

static void copy_str(char *dst, size_t cap, const char *src, size_t n) {
    if (src == NULL) {
        n = 0;
    }
    if (n >= cap) {
        n = cap - 1u;
    }
    if (n) {
        memcpy(dst, src, n);
    }
    dst[n] = 0;
}

// -- the session's tapes ---------------------------------------------------------
//
// A ring of one-byte masks by frame: each slot keeps the frame it holds, so a
// stale slot reads as ABSENT, never as a plausible old input.

static void tape_put(int32_t *fr, uint8_t *mk, int32_t f, uint8_t mask) {
    unsigned i = (unsigned)f & (MOY_MATCH_TAPE - 1u);
    fr[i] = f;
    mk[i] = mask;
}

static int tape_get(const int32_t *fr, const uint8_t *mk, int32_t f) {
    unsigned i = (unsigned)f & (MOY_MATCH_TAPE - 1u);
    return fr[i] == f ? mk[i] : -1;
}

static void tape_clear(int32_t *fr) {
    for (unsigned i = 0; i < MOY_MATCH_TAPE; i++) {
        fr[i] = -1;
    }
}

// -- the transport ---------------------------------------------------------------

static bool send_to(moy_match_t *m, const uint8_t mac[6], const uint8_t *p, size_t n) {
    if (!m->active || m->io == NULL || m->io->send == NULL) {
        return false;
    }
    if (m->io->send(m->io->ctx, mac, p, n) != 0) {
        m->drops++;
        copy_str(m->error, sizeof(m->error), "send failed", 11);
        return false;
    }
    m->tx++;
    return true;
}

static bool broadcast(moy_match_t *m, const uint8_t *p, size_t n) {
    return send_to(m, MOY_MATCH_BROADCAST, p, n);
}

bool moy_match_broadcast(moy_match_t *m, const uint8_t *p, size_t n) {
    return broadcast(m, p, n);
}

static void add_peer(moy_match_t *m, const uint8_t mac[6]) {
    if (m->io != NULL && m->io->add_peer != NULL) {
        m->io->add_peer(m->io->ctx, mac);
    }
}

// -- the lockstep session ----------------------------------------------------------

uint32_t moy_lockstep_seed_of(uint32_t seed, int32_t f) {
    // Mixed rather than seed ^ frame, so a cart that asks for one number a
    // frame does not see the low bits of a counter.
    uint32_t x = (seed ^ ((uint32_t)f * 0x9E3779B1u)) & 0x7FFFFFFFu;
    x ^= x >> 15;
    x = (x * 0x2545F491u) & 0x7FFFFFFFu;
    return x ? x : 1u;
}

int moy_lockstep_begin(moy_match_t *m, int index, uint32_t seed, uint8_t session,
                       const char *cfg) {
    if (index != 0 && index != 1) {
        return -1;
    }
    moy_lockstep_t *s = &m->s;
    memset(s, 0, sizeof(*s));
    s->live = true;
    s->index = index;
    s->peer = 1 - index;
    s->seed = seed;
    s->session = session;
    s->tick_ms = 1000u / MOY_MATCH_TICK_HZ;
    s->delay = MOY_MATCH_DELAY;
    s->redundancy = MOY_MATCH_REDUNDANCY;
    s->last_peer_frame = -1;
    s->peer_need = -1;
    tape_clear(s->mine_f);
    tape_clear(s->theirs_f);
    for (int i = 0; i < 64; i++) {
        s->arr_f[i] = -1;
    }
    // The match's seed is the frame-zero state both consoles agree on: the
    // cart's _init draws from it.
    s->frame_seed = seed;
    if (cfg != NULL) {
        copy_str(s->config, sizeof(s->config), cfg, strlen(cfg));
    }
    return 0;
}

void moy_lockstep_close(moy_match_t *m) {
    moy_lockstep_t *s = &m->s;
    tape_clear(s->mine_f);
    tape_clear(s->theirs_f);
    s->live = false;
    s->held[0] = s->held[1] = s->prev[0] = s->prev[1] = 0;
    s->pressed[0] = s->pressed[1] = 0;
}

bool moy_lockstep_pending(const moy_match_t *m, uint32_t now) {
    const moy_lockstep_t *s = &m->s;
    return !s->has_next || (int32_t)(now - s->next_ms) >= 0;
}

bool moy_lockstep_due(moy_match_t *m, uint32_t now) {
    moy_lockstep_t *s = &m->s;
    if (!s->has_next) {
        s->has_next = true;
        s->next_ms = now + s->tick_ms;
        if (!s->has_tps) {
            // The rate's window opens at the first tick, so a match formed
            // mid-window never reports a frozen first rate.
            s->has_tps = true;
            s->tps_ms = now;
        }
        return true;
    }
    if ((int32_t)(now - s->next_ms) < 0) {
        return false;
    }
    uint32_t nx = s->next_ms + s->tick_ms;
    if ((int32_t)(now - nx) >= 0) {
        nx = now + s->tick_ms;          // any debt of a full tick is dropped
    }
    s->next_ms = nx;
    return true;
}

static void emit(moy_match_t *m, int32_t newest) {
    moy_lockstep_t *s = &m->s;
    s->has_sent = true;
    s->last_sent = newest;
    int32_t lo = newest - (s->redundancy - 1);
    int32_t need = s->peer_need;
    if (need >= 0 && need < lo) {
        lo = need;
        if (newest - lo >= MOY_MATCH_MAX_SPAN) {
            lo = newest - MOY_MATCH_MAX_SPAN + 1;
        }
    }
    int32_t n = newest - lo + 1;
    if (n < 1) {
        n = 1;
    }
    uint8_t p[7 + MOY_MATCH_MAX_SPAN];
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_INPUT;
    p[2] = s->session;
    p[3] = (uint8_t)(newest & 0xFF);
    p[4] = (uint8_t)((newest >> 8) & 0xFF);
    p[5] = (uint8_t)(s->frame & 0xFF);      // ...and what I am waiting for
    p[6] = (uint8_t)((s->frame >> 8) & 0xFF);
    for (int32_t i = 0; i < n; i++) {
        int v = tape_get(s->mine_f, s->mine_m, newest - i);
        p[7 + i] = v < 0 ? 0 : (uint8_t)v;
    }
    s->packets_out++;
    broadcast(m, p, (size_t)(7 + n));
}

static void apply(moy_lockstep_t *s, int player, uint8_t mask) {
    s->held[player] = mask;
    s->pressed[player] = (uint8_t)(mask & ~s->prev[player]);
    s->prev[player] = mask;
}

int moy_lockstep_advance(moy_match_t *m, uint8_t held, uint32_t now, bool timed) {
    moy_lockstep_t *s = &m->s;
    if (!s->live) {
        return -1;
    }
    int32_t due = s->frame + s->delay;
    tape_put(s->mine_f, s->mine_m, due, held);
    emit(m, due);
    int theirs = tape_get(s->theirs_f, s->theirs_m, s->frame);
    if (theirs < 0) {
        if (!s->waiting) {
            s->stall_ticks++;
            s->win_stalls++;
        }
        s->waiting = true;
        s->stalls++;
        // A match that cannot move is over: the console puts the kid back in
        // a one-player game rather than hold a frozen screen.
        if (s->stalls - s->stall_mark > MOY_MATCH_GIVE_UP) {
            s->dead = true;
        }
        return 0;
    }
    s->stall_mark = s->stalls;
    if (s->frame - s->win_mark >= MOY_MATCH_ESCALATE_TICKS) {
        // The first window is match formation and never escalates.
        if (s->win_mark >= MOY_MATCH_ESCALATE_TICKS
                && s->win_stalls >= MOY_MATCH_ESCALATE_AT
                && s->delay < MOY_MATCH_DELAY_MAX) {
            s->delay++;
            say(m, "Moybyte link: input delay -> %d (stall pressure)", s->delay);
        }
        s->win_mark = s->frame;
        s->win_stalls = 0;
    }
    if (timed && s->delay == 1 && s->index != 0 && s->has_next) {
        unsigned i = (unsigned)s->frame & 63u;
        if (s->arr_f[i] == s->frame) {
            float margin = (float)(int32_t)(now - s->arr_t[i]);
            float e = s->has_ema ? s->m_ema * 0.9f + margin * 0.1f : margin;
            s->m_ema = e;
            s->has_ema = true;
            // One-sided: a thin margin pushes the phase later; a fat one is
            // left alone (pulling it earlier chased the stall cliff,
            // 2026-08-25).
            if (e < (float)(MOY_MATCH_SLEW_TARGET_MS - MOY_MATCH_SLEW_BAND_MS)) {
                s->next_ms += 1u;
            }
        }
    }
    int mine = tape_get(s->mine_f, s->mine_m, s->frame);
    apply(s, s->index, mine < 0 ? 0 : (uint8_t)mine);   // frames before the delay: nobody moved
    apply(s, s->peer, (uint8_t)theirs);
    s->waiting = false;
    s->frame++;
    s->frame_seed = moy_lockstep_seed_of(s->seed, s->frame);
    return 1;
}

void moy_lockstep_resend(moy_match_t *m) {
    if (m->s.live && m->s.has_sent) {
        emit(m, m->s.last_sent);
    }
}

int32_t moy_lockstep_expand(const moy_match_t *m, uint32_t f16) {
    // The peer is within delay + redundancy frames of us, so the nearest
    // congruent value is the right one: the counter survives its 16-bit wrap.
    int32_t base = m->s.frame;
    int32_t f = (int32_t)(((uint32_t)base & ~0xFFFFu) | (f16 & 0xFFFFu));
    if (f - base > 32768) {
        f -= 65536;
    } else if (base - f > 32768) {
        f += 65536;
    }
    return f;
}

int moy_lockstep_packet(moy_match_t *m, const uint8_t *data, size_t n, uint32_t now,
                        bool timed) {
    moy_lockstep_t *s = &m->s;
    if (!s->live || n < 7 || data[0] != MOY_MATCH_PROTO || data[1] != MOY_MATCH_T_INPUT) {
        return 0;
    }
    // A stale packet from the previous match carries its session id.
    if (data[2] != s->session) {
        return 0;
    }
    int32_t newest = moy_lockstep_expand(m, (uint32_t)data[3] | ((uint32_t)data[4] << 8));
    s->peer_need = moy_lockstep_expand(m, (uint32_t)data[5] | ((uint32_t)data[6] << 8));
    size_t k = n - 7u;
    if (k > MOY_MATCH_MAX_SPAN) {
        k = MOY_MATCH_MAX_SPAN;
    }
    for (size_t i = 0; i < k; i++) {
        int32_t f = newest - (int32_t)i;
        if (f >= 0 && tape_get(s->theirs_f, s->theirs_m, f) < 0) {
            tape_put(s->theirs_f, s->theirs_m, f, data[7 + i]);
            if (timed) {
                unsigned j = (unsigned)f & 63u;
                s->arr_f[j] = f;
                s->arr_t[j] = now;
            }
        }
    }
    if (newest > s->last_peer_frame) {
        s->last_peer_frame = newest;
    }
    s->packets_in++;
    return 1;
}

uint32_t moy_lockstep_tps(moy_match_t *m, uint32_t now) {
    moy_lockstep_t *s = &m->s;
    int32_t f0 = s->tps_f;
    bool had = s->has_tps;
    uint32_t t0 = s->tps_ms;
    s->tps_f = s->frame;
    s->tps_ms = now;
    s->has_tps = true;
    if (!had) {
        return 0;               // no tick has run yet: matched, not advancing
    }
    int32_t ms = (int32_t)(now - t0);
    if (ms <= 0) {
        return 0;
    }
    return (uint32_t)(((s->frame - f0) * 1000 + ms / 2) / ms);
}

// -- the link ----------------------------------------------------------------------

void moy_match_init(moy_match_t *m, const moy_match_io_t *io, const char *name,
                    const char *board, uint32_t entropy) {
    memset(m, 0, sizeof(*m));
    m->io = io;
    copy_str(m->board, sizeof(m->board), board, board ? strlen(board) : 0);
    if (name == NULL || !*name) {
        name = m->board;
    }
    copy_str(m->name, sizeof(m->name), name, strlen(name));
    copy_str(m->cfg, sizeof(m->cfg), "{}", 2);
    m->rng = entropy ? entropy : 0x6D6F7962u;
    tape_clear(m->s.mine_f);
    tape_clear(m->s.theirs_f);
}

void moy_match_start(moy_match_t *m, const uint8_t mac[6]) {
    if (mac != NULL) {
        memcpy(m->mac, mac, 6);
    }
    if (m->active) {
        return;
    }
    m->active = true;
    m->error[0] = 0;
    add_peer(m, MOY_MATCH_BROADCAST);
}

void moy_match_end(moy_match_t *m) {
    if (m->s.live) {
        moy_lockstep_close(m);
    }
    if (m->state == 2) {
        m->state = m->cart[0] ? 1 : 0;
    }
}

void moy_match_stop(moy_match_t *m) {
    moy_match_end(m);
    memset(m->peers, 0, sizeof(m->peers));
    m->defer_n = 0;
    m->active = false;
}

static void beacon(moy_match_t *m) {
    uint8_t p[3 + 200];
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_BEACON;
    p[2] = m->state;
    char body[sizeof(m->name) + sizeof(m->board) + sizeof(m->cart) + 4];
    int n = snprintf(body, sizeof(body), "%s|%s|%s", m->name, m->board, m->cart);
    if (n < 0) {
        n = 0;
    }
    if (n > 200) {
        n = 200;
    }
    memcpy(p + 3, body, (size_t)n);
    broadcast(m, p, (size_t)(3 + n));
}

void moy_match_set_config(moy_match_t *m, const char *cfg) {
    size_t n = cfg != NULL ? strlen(cfg) : 0;
    // The seed matters more than the tuning: an oversized config is dropped
    // whole and both sides keep their own defaults.
    if (cfg == NULL || n > MOY_MATCH_CFG || moy_json_valid(cfg, n) != MOY_JSON_OK) {
        copy_str(m->cfg, sizeof(m->cfg), "{}", 2);
    } else {
        copy_str(m->cfg, sizeof(m->cfg), cfg, n);
    }
}

void moy_match_announce(moy_match_t *m, const char *cart, int state) {
    copy_str(m->cart, sizeof(m->cart), cart, cart ? strlen(cart) : 0);
    // A live match outranks whatever the caller thinks the state is.
    m->state = m->s.live ? 2 : (uint8_t)state;
    if (m->active) {
        beacon(m);
    }
}

static moy_match_peer_t *peer_of(moy_match_t *m, const uint8_t mac[6]) {
    for (int i = 0; i < MOY_MATCH_PEERS; i++) {
        if (m->peers[i].used && memcmp(m->peers[i].mac, mac, 6) == 0) {
            return &m->peers[i];
        }
    }
    return NULL;
}

const moy_match_peer_t *moy_match_candidate(const moy_match_t *m, const char *cart,
                                            uint32_t now) {
    if (cart == NULL || !*cart) {
        return NULL;
    }
    for (int i = 0; i < MOY_MATCH_PEERS; i++) {
        const moy_match_peer_t *p = &m->peers[i];
        if (!p->used || p->state == 2 || (int32_t)(now - p->seen) > MOY_MATCH_PEER_TTL_MS) {
            continue;
        }
        if (p->cart[0] && strcmp(p->cart, cart) == 0) {
            return p;
        }
    }
    return NULL;
}

static uint32_t draw_seed(moy_match_t *m) {
    uint32_t x = m->rng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    m->rng = x;
    return x & 0x3FFFFFFFu;
}

static void ask(moy_match_t *m, const char *cart) {
    m->act = MOY_MATCH_ACT_LAUNCH;
    copy_str(m->act_cart, sizeof(m->act_cart), cart, strlen(cart));
}

// A config is carried only when it is a non-empty JSON object.
static const char *cfg_or_null(const char *cfg) {
    if (cfg == NULL || !*cfg || moy_json_valid(cfg, strlen(cfg)) != MOY_JSON_OK) {
        return NULL;
    }
    const char *p = moy_json_ws(cfg, cfg + strlen(cfg));
    if (*p != '{') {
        return NULL;
    }
    p = moy_json_ws(p + 1, cfg + strlen(cfg));
    return *p == '}' ? NULL : cfg;
}

static int host(moy_match_t *m, const moy_match_peer_t *p, bool has_seed, uint32_t seed,
                const char *cart, bool relaunch) {
    if (!has_seed) {
        seed = draw_seed(m);
    }
    m->session_id = (uint8_t)(m->session_id + 1u);
    uint8_t *f = m->start_frame;
    f[0] = MOY_MATCH_PROTO;
    f[1] = MOY_MATCH_T_START;
    f[2] = m->session_id;
    f[3] = 1;
    f[4] = (uint8_t)(seed & 0xFF);
    f[5] = (uint8_t)((seed >> 8) & 0xFF);
    f[6] = (uint8_t)((seed >> 16) & 0xFF);
    f[7] = (uint8_t)((seed >> 24) & 0xFF);
    memcpy(f + 8, p->mac, 6);
    size_t at = 14;
    size_t cn = strlen(cart);
    if (cn > MOY_MATCH_CART) {
        cn = MOY_MATCH_CART;
    }
    memcpy(f + at, cart, cn);
    at += cn;
    f[at++] = 0;
    size_t gn = strlen(m->cfg);
    memcpy(f + at, m->cfg, gn);
    at += gn;
    m->start_len = (uint16_t)at;
    memcpy(m->start_peer, p->mac, 6);
    m->start_tries = 0;
    broadcast(m, m->start_frame, at);
    moy_lockstep_begin(m, 0, seed, m->session_id, cfg_or_null(m->cfg));
    m->state = 2;
    if (relaunch) {
        ask(m, cart);
    }
    return 1;
}

int moy_match_offer(moy_match_t *m, const char *cart, uint32_t now) {
    return moy_match_offer_seeded(m, cart, now, false, 0);
}

int moy_match_offer_seeded(moy_match_t *m, const char *cart, uint32_t now, bool has_seed,
                           uint32_t seed) {
    if (!m->active || m->s.live) {
        return 0;
    }
    const moy_match_peer_t *p = moy_match_candidate(m, cart, now);
    if (p == NULL) {
        return 0;
    }
    if (memcmp(m->mac, p->mac, 6) > 0) {
        // They host. Ask rather than sit still: they may already be playing,
        // and nothing on their side would ever offer again.
        uint8_t j[8] = {MOY_MATCH_PROTO, MOY_MATCH_T_JOIN};
        memcpy(j + 2, p->mac, 6);
        broadcast(m, j, sizeof(j));
        return 0;
    }
    return host(m, p, has_seed, seed, cart, false);
}

static void lost(moy_match_t *m, const char *why) {
    say(m, "Moybyte link: %s, playing solo", why);
    moy_match_end(m);
    m->start_len = 0;
    if (m->cart[0]) {
        ask(m, m->cart);
    }
}

static void chase_start(moy_match_t *m) {
    moy_lockstep_t *s = &m->s;
    if (!s->live || s->index != 0 || m->start_len == 0) {
        return;                         // only the host chases
    }
    if (s->packets_in) {
        m->start_len = 0;               // answered: stop chasing, keep the match
        return;
    }
    m->start_tries++;
    if (m->start_tries > MOY_MATCH_START_TRIES) {
        lost(m, "peer never answered");
        return;
    }
    broadcast(m, m->start_frame, m->start_len);
}

static void maybe_match(moy_match_t *m, uint32_t now) {
    if (m->s.live) {
        if (m->s.dead) {
            lost(m, "match lost");
            return;
        }
        chase_start(m);
        return;
    }
    if (m->state != 1 || !m->cart[0]) {
        return;
    }
    const moy_match_peer_t *p = moy_match_candidate(m, m->cart, now);
    if (p == NULL) {
        return;
    }
    if (memcmp(m->mac, p->mac, 6) > 0) {
        uint8_t j[8] = {MOY_MATCH_PROTO, MOY_MATCH_T_JOIN};
        memcpy(j + 2, p->mac, 6);
        broadcast(m, j, sizeof(j));
    } else {
        host(m, p, false, 0, m->cart, true);
    }
}

static void on_beacon(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n,
                      uint32_t now) {
    if (memcmp(mac, m->mac, 6) == 0 || n < 3) {
        return;
    }
    moy_match_peer_t *p = peer_of(m, mac);
    if (p == NULL) {
        // A new peer takes a free row, or the one heard from longest ago.
        int at = -1;
        for (int i = 0; i < MOY_MATCH_PEERS; i++) {
            if (!m->peers[i].used) {
                at = i;
                break;
            }
            if (at < 0 || (int32_t)(m->peers[at].seen - m->peers[i].seen) > 0) {
                at = i;
            }
        }
        p = &m->peers[at];
        memset(p, 0, sizeof(*p));
        p->used = true;
        memcpy(p->mac, mac, 6);
        // Registered with the radio too: ESP-NOW delivers a unicast only
        // from a registered peer.
        add_peer(m, mac);
    }
    // "name|board|cart": the first three fields, a missing one empty.
    const char *b = (const char *)msg + 3;
    const char *e = (const char *)msg + n;
    char *dst[3] = {p->name, p->board, p->cart};
    size_t cap[3] = {sizeof(p->name), sizeof(p->board), sizeof(p->cart)};
    for (int k = 0; k < 3; k++) {
        const char *bar = b;
        while (bar < e && *bar != '|') {
            bar++;
        }
        copy_str(dst[k], cap[k], b, (size_t)(bar - b));
        b = bar < e ? bar + 1 : e;
    }
    p->state = msg[2];
    p->seen = now;
}

static bool for_us(const moy_match_t *m, const uint8_t *msg, size_t n, size_t at) {
    return n >= at + 6u && memcmp(msg + at, m->mac, 6) == 0;
}

static void on_start(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n) {
    if (n < 14 || m->s.live) {
        return;
    }
    uint8_t session = msg[2];
    int index = msg[3];
    uint32_t seed = (uint32_t)msg[4] | ((uint32_t)msg[5] << 8) | ((uint32_t)msg[6] << 16)
                    | ((uint32_t)msg[7] << 24);
    const char *body = (const char *)msg + 14;
    size_t bn = n - 14u;
    size_t cut = 0;
    while (cut < bn && body[cut] != 0) {
        cut++;
    }
    char cart[MOY_MATCH_CART + 1];
    copy_str(cart, sizeof(cart), body, cut);
    char cfg[MOY_MATCH_CFG + 1] = "";
    if (cut < bn) {
        copy_str(cfg, sizeof(cfg), body + cut + 1, bn - cut - 1u);
    }
    add_peer(m, mac);
    // The session is built before the cart opens, so the run finds it.
    if (moy_lockstep_begin(m, index, seed, session, cfg_or_null(cfg)) != 0) {
        return;
    }
    m->state = 2;
    ask(m, cart);
}

static void on_join(moy_match_t *m, const uint8_t mac[6], uint32_t now) {
    (void)now;
    if (!m->cart[0]) {
        return;
    }
    if (m->s.live) {
        // We believe we are playing this peer and it still asks: the invite
        // never landed. Say it again (same session, same seed).
        if (m->s.index == 0 && m->start_len != 0 && memcmp(mac, m->start_peer, 6) == 0) {
            broadcast(m, m->start_frame, m->start_len);
        }
        return;
    }
    const moy_match_peer_t *p = peer_of(m, mac);
    if (p == NULL || strcmp(p->cart, m->cart) != 0 || p->state == 2) {
        return;
    }
    // We are already running the cart: a lockstep sim cannot join a game in
    // progress, so this half goes back to frame zero too.
    host(m, p, false, 0, m->cart, true);
}

static void inbox_put(moy_match_t *m, const uint8_t *body, size_t n) {
    if (m->inbox_n >= MOY_MATCH_INBOX) {
        m->inbox_drops++;
        return;
    }
    unsigned at = (m->inbox_head + m->inbox_n) % MOY_MATCH_INBOX;
    if (n > MOY_MATCH_FRAME) {
        n = MOY_MATCH_FRAME;
    }
    memcpy(m->inbox[at], body, n);
    m->inbox_len[at] = (uint8_t)n;
    m->inbox_n++;
}

void moy_match_dispatch(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg,
                        size_t n, uint32_t now) {
    if (n < 2 || msg[0] != MOY_MATCH_PROTO) {
        return;
    }
    switch (msg[1]) {
        case MOY_MATCH_T_INPUT:
            moy_lockstep_packet(m, msg, n, now, true);
            return;
        case MOY_MATCH_T_BEACON:
            on_beacon(m, mac, msg, n, now);
            return;
        case MOY_MATCH_T_MSG:
            inbox_put(m, msg + 2, n - 2u);
            return;
        case MOY_MATCH_T_START:
            // 8 header bytes, then the destination.
            if (for_us(m, msg, n, 8)) {
                on_start(m, mac, msg, n);
            }
            return;
        case MOY_MATCH_T_BYE: {
            moy_match_peer_t *p = peer_of(m, mac);
            if (p != NULL) {
                p->used = false;
            }
            if (m->s.live) {
                lost(m, "peer left");
            }
            return;
        }
        case MOY_MATCH_T_JOIN:
            if (for_us(m, msg, n, 2)) {
                on_join(m, mac, now);
            }
            return;
        default:
            return;
    }
}

static void recover(moy_match_t *m) {
    m->recovers++;
    if (m->io != NULL && m->io->recover != NULL && m->io->recover(m->io->ctx) != 0) {
        say(m, "Moybyte espnow recover failed");
        m->active = false;
    }
}

int moy_match_drain_input(moy_match_t *m, uint32_t now, int budget) {
    if (!m->active || m->io == NULL || m->io->recv == NULL) {
        return 0;
    }
    int n = 0;
    uint8_t mac[6], buf[MOY_MATCH_FRAME];
    for (int i = 0; i < budget; i++) {
        int len = m->io->recv(m->io->ctx, mac, buf, sizeof(buf));
        if (len == -1) {
            break;
        }
        if (len < 0) {
            copy_str(m->error, sizeof(m->error), "recv failed", 11);
            say(m, "Moybyte espnow recv: %d", len);
            recover(m);
            break;
        }
        n++;
        m->rx++;
        if (len >= 2 && buf[0] == MOY_MATCH_PROTO && buf[1] == MOY_MATCH_T_INPUT) {
            moy_lockstep_packet(m, buf, (size_t)len, now, true);
        } else if (m->defer_n < MOY_MATCH_DEFER) {
            memcpy(m->defer_mac[m->defer_n], mac, 6);
            memcpy(m->defer[m->defer_n], buf, (size_t)len);
            m->defer_len[m->defer_n] = (uint8_t)len;
            m->defer_n++;
        } else {
            m->drops++;
        }
    }
    return n;
}

static void expire(moy_match_t *m, uint32_t now) {
    for (int i = 0; i < MOY_MATCH_PEERS; i++) {
        if (m->peers[i].used && (int32_t)(now - m->peers[i].seen) > MOY_MATCH_PEER_TTL_MS) {
            m->peers[i].used = false;
        }
    }
}

int moy_match_poll(moy_match_t *m, uint32_t now) {
    if (!m->active) {
        return 0;
    }
    int n = 0;
    for (uint8_t i = 0; i < m->defer_n; i++) {
        moy_match_dispatch(m, m->defer_mac[i], m->defer[i], m->defer_len[i], now);
    }
    m->defer_n = 0;
    if (m->io != NULL && m->io->recv != NULL) {
        uint8_t mac[6], buf[MOY_MATCH_FRAME];
        for (int i = 0; i < MOY_MATCH_DRAIN_MAX; i++) {
            int len = m->io->recv(m->io->ctx, mac, buf, sizeof(buf));
            if (len == -1) {
                break;
            }
            if (len < 0) {
                // The documented failure is a desynced ring, and its cure an
                // active cycle -- not a retry, which fails forever.
                copy_str(m->error, sizeof(m->error), "recv failed", 11);
                say(m, "Moybyte espnow recv: %d", len);
                recover(m);
                break;
            }
            n++;
            m->rx++;
            moy_match_dispatch(m, mac, buf, (size_t)len, now);
        }
    }
    int32_t since = (int32_t)(now - m->beacon_at);
    if (since >= MOY_MATCH_BEACON_MS || since < 0) {
        m->beacon_at = now;
        beacon(m);
        expire(m, now);
        maybe_match(m, now);
    }
    return n;
}

int moy_match_send_msg(moy_match_t *m, const uint8_t *body, size_t n) {
    if (n > MOY_MATCH_MSG_MAX) {
        return -1;
    }
    if (!m->active) {
        return -2;
    }
    uint8_t p[2 + MOY_MATCH_MSG_MAX];
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_MSG;
    memcpy(p + 2, body, n);
    return broadcast(m, p, n + 2u) ? 0 : -2;
}

int moy_match_take_msg(moy_match_t *m, uint8_t *out, size_t cap) {
    if (m->inbox_n == 0) {
        return -1;
    }
    unsigned at = m->inbox_head;
    size_t n = m->inbox_len[at];
    memcpy(out, m->inbox[at], n < cap ? n : cap);
    m->inbox_head = (uint8_t)((at + 1u) % MOY_MATCH_INBOX);
    m->inbox_n--;
    return (int)n;
}

int moy_match_action(moy_match_t *m, char *cart, size_t cap) {
    int a = m->act;
    if (a != MOY_MATCH_ACT_NONE && cart != NULL && cap) {
        copy_str(cart, cap, m->act_cart, strlen(m->act_cart));
    }
    m->act = MOY_MATCH_ACT_NONE;
    return a;
}

int moy_match_peer_count(const moy_match_t *m, uint32_t now) {
    (void)now;
    int n = 0;
    for (int i = 0; i < MOY_MATCH_PEERS; i++) {
        n += m->peers[i].used;
    }
    return n;
}

const moy_match_peer_t *moy_match_peer_at(const moy_match_t *m, int i) {
    int k = 0;
    for (int j = 0; j < MOY_MATCH_PEERS; j++) {
        if (m->peers[j].used) {
            if (k == i) {
                return &m->peers[j];
            }
            k++;
        }
    }
    return NULL;
}

size_t moy_match_size(void) {
    return sizeof(moy_match_t);
}

size_t moy_match_offset_s(void) {
    return offsetof(moy_match_t, s);
}
