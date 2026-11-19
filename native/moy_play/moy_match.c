// moy_match: the link's protocol and the lockstep session (moy_match.h has
// the contract and the measurements behind it).

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "moy_match.h"
#include "moy_json.h"
#include "moy_kmem.h"

const uint8_t MOY_MATCH_BROADCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

// BEAMING A FILE (the end of this file).
static void on_beam(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n,
                    uint32_t now);
static void beam_tick(moy_match_t *m, uint32_t now);
static bool beam_holds_channel(const moy_match_t *m);
static bool beam_hears(const moy_match_t *m, uint32_t now);
static bool beam_keeps(const moy_match_t *m, uint32_t now);
static bool beam_listens(const moy_match_t *m);
static int beam_rests(moy_match_t *m, uint32_t now);

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
    s->hist = MOY_LOCKSTEP_HASH0;
    s->world_f = -1;
    s->ck_last = -1;
    s->peer_ck_f = -1;
    s->peer_ck_cmp = -1;
    s->desync = -1;
    s->peer_margin = 255;
    s->rep_min = 255;
    s->last_win = 255;
    s->peer_win = 255;
    for (int i = 0; i < MOY_MATCH_CHECKS; i++) {
        s->ck_f[i] = -1;
    }
    if (cfg != NULL) {
        copy_str(s->config, sizeof(s->config), cfg, strlen(cfg));
    }
    moy_lockstep_rate(m, MOY_MATCH_TICK_HZ);
    return 0;
}

// The pace and what is a time at `hz`: the period, the delay's ceiling and
// the escalation window.
static void pace(moy_lockstep_t *s, int hz) {
    s->tick_hz = (uint16_t)hz;
    s->tick_ms = 1000u / (uint32_t)hz;
    s->tick_rem = 0;
    s->delay_max = (MOY_MATCH_DELAY_MAX_MS * hz + 999) / 1000;
    if (s->delay_max < 1) {
        s->delay_max = 1;
    }
    s->escalate_ticks = MOY_MATCH_ESCALATE_S * hz;
    s->escalate_at = (uint32_t)((MOY_MATCH_ESCALATE_AT * hz + MOY_MATCH_TICK_HZ / 2)
                                / MOY_MATCH_TICK_HZ);
}

void moy_lockstep_rate(moy_match_t *m, int hz) {
    moy_lockstep_t *s = &m->s;
    if (hz < 10) {
        hz = 10;
    } else if (hz > MOY_MATCH_TICK_FAST) {
        hz = MOY_MATCH_TICK_FAST;
    }
    pace(s, hz);
    s->hz0 = (uint16_t)hz;
    s->step_hz = 0;
    s->stepped = false;
}

int moy_lockstep_hz_at(const moy_match_t *m, int32_t f) {
    const moy_lockstep_t *s = &m->s;
    if (s->step_hz != 0 && f >= s->step_from) {
        return s->step_hz;
    }
    return s->hz0 ? s->hz0 : MOY_MATCH_TICK_HZ;
}

int32_t moy_lockstep_ms(const moy_match_t *m, int32_t f) {
    const moy_lockstep_t *s = &m->s;
    int64_t hz0 = s->hz0 ? s->hz0 : MOY_MATCH_TICK_HZ;
    if (s->step_hz == 0 || f <= s->step_from) {
        return (int32_t)((int64_t)f * 1000 / hz0);
    }
    int64_t base = (int64_t)s->step_from * 1000 / hz0;
    return (int32_t)(base + (int64_t)(f - s->step_from) * 1000 / s->step_hz);
}

// This console reached the step's frame: its pace and its delay move to the
// new rate, the delay keeping its ms (rounded up), and the window opens anew.
static void take_step(moy_match_t *m) {
    moy_lockstep_t *s = &m->s;
    int was = s->tick_hz;
    pace(s, s->step_hz);
    int d = (s->delay * s->step_hz + was - 1) / was;
    s->delay = d < MOY_MATCH_DELAY ? MOY_MATCH_DELAY : d > s->delay_max ? s->delay_max : d;
    s->stepped = true;
    s->delay_mark = s->frame;
    s->win_mark = s->frame;
    s->win_stalls = 0;
    s->last_win = 255;
    s->has_win_ms = false;
    say(m, "Moybyte link: %d Hz from frame %ld, input delay %d (%d ms)", s->step_hz,
        (long)s->step_from, s->delay, (int)(s->delay * 1000 / s->tick_hz));
}

// One period: tick_ms, plus the ms the fraction has gathered (60 Hz is
// 16 ms twice and 17 once).
static uint32_t period(moy_lockstep_t *s) {
    uint32_t hz = s->tick_hz ? s->tick_hz : MOY_MATCH_TICK_HZ;
    s->tick_rem = (uint16_t)(s->tick_rem + 1000u % hz);
    if (s->tick_rem >= hz) {
        s->tick_rem = (uint16_t)(s->tick_rem - hz);
        return s->tick_ms + 1u;
    }
    return s->tick_ms;
}

bool moy_lockstep_gate(moy_match_t *m, uint32_t now, bool due) {
    moy_lockstep_t *s = &m->s;
    bool ready = s->waiting && tape_get(s->theirs_f, s->theirs_m, s->frame) >= 0;
    if (!due && !ready && s->has_lock && now - s->lock_at < MOY_MATCH_LOCK_MS) {
        return false;
    }
    s->has_lock = true;
    s->lock_at = now;
    return true;
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
        s->next_ms = now + period(s);
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
    uint32_t step = period(s);
    uint32_t nx = s->next_ms + step;
    // A slot a stalled frame takes ticks nothing, and a dropped debt moves
    // every later slot: both are ticks the match never gets back.
    uint32_t lost = s->waiting ? step : 0u;
    if ((int32_t)(now - nx) >= 0) {
        lost += now - s->next_ms;
        nx = now + step;                // any debt of a full tick is dropped
    }
    if (s->waiting) {
        s->stall_ms += lost;
    } else {
        s->late_ms += lost;
    }
    s->next_ms = nx;
    return true;
}

uint32_t moy_lockstep_hash(uint32_t h, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        h = (h ^ p[i]) * 16777619u;
    }
    return h;
}

bool moy_lockstep_world_due(const moy_match_t *m) {
    const moy_lockstep_t *s = &m->s;
    return m->world_check && s->live && s->frame > 0 && s->frame % MOY_MATCH_CHECK_EVERY == 0
           && s->world_f != s->frame && s->ck_last != s->frame;
}

void moy_match_world_check(moy_match_t *m, bool on) {
    m->world_check = on;
}

void moy_lockstep_world(moy_match_t *m, int32_t frame, uint32_t digest) {
    moy_lockstep_t *s = &m->s;
    if (s->live && frame == s->frame && s->ck_last != frame) {
        s->world_f = frame;
        s->world_d = digest;
    }
}

// One checkpoint against another: the first disagreement is the match's
// desync, said once.
static void compare(moy_match_t *m, int32_t f, uint32_t in_a, uint32_t w_a, uint32_t in_b,
                    uint32_t w_b) {
    moy_lockstep_t *s = &m->s;
    uint8_t kind = in_a != in_b ? 1 : (w_a != 0 && w_b != 0 && w_a != w_b) ? 2 : 0;
    if (kind == 0) {
        s->checks++;
        return;
    }
    if (s->desync < 0) {
        s->desync = f;
        s->desync_kind = kind;
        say(m, "Moybyte link: OUT OF SYNC at frame %ld (%s differ)", (long)f,
            kind == 1 ? "inputs" : "worlds");
    }
}

static void check_peer(moy_match_t *m) {
    moy_lockstep_t *s = &m->s;
    if (s->peer_ck_f <= s->peer_ck_cmp) {
        return;
    }
    for (int i = 0; i < MOY_MATCH_CHECKS; i++) {
        if (s->ck_f[i] == s->peer_ck_f) {
            compare(m, s->peer_ck_f, s->ck_in[i], s->ck_w[i], s->peer_ck_in, s->peer_ck_w);
            s->peer_ck_cmp = s->peer_ck_f;
            return;
        }
    }
}

static void checkpoint(moy_match_t *m) {
    moy_lockstep_t *s = &m->s;
    int32_t f = s->frame;
    if (f <= 0 || f % MOY_MATCH_CHECK_EVERY != 0 || s->ck_last == f) {
        return;
    }
    unsigned i = (unsigned)(f / MOY_MATCH_CHECK_EVERY) % MOY_MATCH_CHECKS;
    s->ck_f[i] = f;
    s->ck_in[i] = s->hist;
    s->ck_w[i] = s->world_f == f ? s->world_d : 0u;
    s->ck_last = f;
    check_peer(m);
}

static void fold(moy_lockstep_t *s) {
    uint8_t b[7] = {(uint8_t)s->frame, (uint8_t)(s->frame >> 8), (uint8_t)(s->frame >> 16),
                    (uint8_t)(s->frame >> 24), s->held[0], s->held[1], 0};
    s->hist = moy_lockstep_hash(s->hist, b, 6);
}

static void put16(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
}

static void put32(uint8_t *p, uint32_t v) {
    put16(p, v);
    put16(p + 2, v >> 16);
}

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16)
           | ((uint32_t)p[3] << 24);
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
    uint8_t p[MOY_MATCH_IN_HDR + MOY_MATCH_MAX_SPAN];
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_INPUT;
    p[2] = s->session;
    put16(p + 3, (uint32_t)newest);
    put16(p + 5, (uint32_t)s->frame);       // ...and what I am waiting for
    // ...and my newest checkpoint (frame 0 is never one: none yet)
    int ck = -1;
    for (int i = 0; i < MOY_MATCH_CHECKS; i++) {
        if (s->ck_last >= 0 && s->ck_f[i] == s->ck_last) {
            ck = i;
        }
    }
    put16(p + 7, ck < 0 ? 0u : (uint32_t)s->ck_last);
    put32(p + 9, ck < 0 ? 0u : s->ck_in[ck]);
    put32(p + 13, ck < 0 ? 0u : s->ck_w[ck]);
    // ...and how early the peer's inputs reach me, for its delay's way down
    float e = s->m_ema < 0.0f ? 0.0f : s->m_ema > 254.0f ? 254.0f : s->m_ema;
    p[17] = s->has_ema ? (uint8_t)e : 255u;
    p[18] = s->last_win;                    // ...and how often I stalled on you
    p[19] = s->step_hz;                     // ...and the rate we step down to
    put16(p + 20, (uint32_t)s->step_from);
    for (int32_t i = 0; i < n; i++) {
        int v = tape_get(s->mine_f, s->mine_m, newest - i);
        p[MOY_MATCH_IN_HDR + i] = v < 0 ? 0 : (uint8_t)v;
    }
    s->packets_out++;
    broadcast(m, p, (size_t)(MOY_MATCH_IN_HDR + n));
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
    // A frame's local input is sampled ONCE: a stalled tick retries this call
    // with whatever is held by then, and the first sample may already be on
    // the air, where the peer keeps the first copy it hears. Every frame up
    // to `due` is written before it is emitted, so a raise's skipped frame
    // never goes out as a placeholder a later write contradicts.
    if (s->step_hz != 0 && !s->stepped && s->frame >= s->step_from) {
        take_step(m);
    }
    checkpoint(m);
    int32_t due = s->frame + s->delay;
    for (int32_t f = s->frame + 1; f <= due; f++) {
        if (tape_get(s->mine_f, s->mine_m, f) < 0) {
            tape_put(s->mine_f, s->mine_m, f, held);
        }
    }
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
    if (s->frame - s->win_mark >= s->escalate_ticks) {
        // The first window is match formation and changes nothing, nor
        // does the first at a stepped rate. The peer's stalls are what this
        // console's delay buys down, so the peer's report steers it.
        bool settled = s->win_mark >= s->escalate_ticks
                       && !(s->stepped && s->win_mark == s->delay_mark && s->last_win == 255u);
        s->last_win = settled ? (uint8_t)(s->win_stalls > 254u ? 254u : s->win_stalls) : 255u;
        uint32_t pw = s->peer_win;
        if (settled && pw != 255u && pw >= s->escalate_at && s->delay < s->delay_max) {
            s->delay++;
            s->delay_mark = s->frame;
            s->raises++;
            say(m, "Moybyte link: input delay -> %d (%d ms, the peer stalled %d ticks)",
                s->delay, (int)(s->delay * 1000 / s->tick_hz), (int)pw);
        } else if (settled && s->delay > MOY_MATCH_DELAY && pw != 255u
                   && pw * 4u <= s->escalate_at
                   && s->frame - s->delay_mark >= MOY_MATCH_LOWER_HOLD * s->escalate_ticks
                   && s->rep_min != 255u
                   && s->rep_min >= s->tick_ms + MOY_MATCH_LOWER_GUARD_MS) {
            s->delay--;
            s->delay_mark = s->frame;
            s->lowers++;
            say(m, "Moybyte link: input delay -> %d (%d ms, peer margin %d ms)", s->delay,
                (int)(s->delay * 1000 / s->tick_hz), (int)s->rep_min);
        }
        // The host's step down: a 60 Hz match ticking under STEP_BAR% of
        // its rate is bound by a console's frame (lockstep ticks both at the
        // slower one's pace), and 30 Hz halves what that frame must do.
        if (settled && timed && s->has_win_ms && s->index == 0 && s->step_hz == 0
                && !s->hold && s->tick_hz > MOY_MATCH_TICK_HZ) {
            int32_t ms = (int32_t)(now - s->win_ms);
            int32_t ticks = s->frame - s->win_mark;
            if (ms > 0 && ticks * 1000 * 100 < (int32_t)s->tick_hz * MOY_MATCH_STEP_BAR * ms) {
                s->step_hz = MOY_MATCH_TICK_HZ;
                s->step_from = s->frame + MOY_MATCH_STEP_LEAD;
                say(m, "Moybyte link: %ld ticks/s of %d, stepping to %d Hz at frame %ld",
                    (long)(ticks * 1000 / ms), (int)s->tick_hz, MOY_MATCH_TICK_HZ,
                    (long)s->step_from);
            }
        }
        s->win_mark = s->frame;
        s->win_stalls = 0;
        s->rep_min = 255;
        s->has_win_ms = timed;
        s->win_ms = now;
    }
    if (timed && s->has_next) {
        unsigned i = (unsigned)s->frame & 63u;
        if (s->arr_f[i] == s->frame) {
            float margin = (float)(int32_t)(now - s->arr_t[i]);
            float e = s->has_ema ? s->m_ema * 0.9f + margin * 0.1f : margin;
            s->m_ema = e;
            s->has_ema = true;
            // The guest's phase at delay one, one-sided: a thin margin pushes
            // it later; a fat one is left alone (pulling it earlier chased the
            // stall cliff). The host is the anchor.
            if (s->delay == 1 && s->index != 0
                    && e < (float)(MOY_MATCH_SLEW_TARGET_MS - MOY_MATCH_SLEW_BAND_MS)) {
                s->next_ms += 1u;
                s->slew_ms++;
            }
        }
    }
    int mine = tape_get(s->mine_f, s->mine_m, s->frame);
    apply(s, s->index, mine < 0 ? 0 : (uint8_t)mine);   // frames before the delay: nobody moved
    apply(s, s->peer, (uint8_t)theirs);
    fold(s);
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
    if (!s->live || n < MOY_MATCH_IN_HDR || data[0] != MOY_MATCH_PROTO
            || data[1] != MOY_MATCH_T_INPUT) {
        return 0;
    }
    // A stale packet from the previous match carries its session id.
    if (data[2] != s->session) {
        return 0;
    }
    int32_t newest = moy_lockstep_expand(m, (uint32_t)data[3] | ((uint32_t)data[4] << 8));
    s->peer_need = moy_lockstep_expand(m, (uint32_t)data[5] | ((uint32_t)data[6] << 8));
    s->peer_win = data[18];
    if (data[19] != 0u && s->step_hz == 0 && s->index != 0 && data[19] < s->tick_hz) {
        s->step_hz = data[19];
        s->step_from = moy_lockstep_expand(m, (uint32_t)data[20] | ((uint32_t)data[21] << 8));
    }
    s->peer_margin = data[17];
    if (data[17] < s->rep_min) {
        s->rep_min = data[17];
    }
    uint32_t ck16 = (uint32_t)data[7] | ((uint32_t)data[8] << 8);
    if (ck16 != 0u) {
        int32_t ck = moy_lockstep_expand(m, ck16);
        if (ck > s->peer_ck_f) {
            s->peer_ck_f = ck;
            s->peer_ck_in = get32(data + 9);
            s->peer_ck_w = get32(data + 13);
            check_peer(m);
        }
    }
    size_t k = n - (size_t)MOY_MATCH_IN_HDR;
    if (k > MOY_MATCH_MAX_SPAN) {
        k = MOY_MATCH_MAX_SPAN;
    }
    for (size_t i = 0; i < k; i++) {
        int32_t f = newest - (int32_t)i;
        if (f >= 0 && tape_get(s->theirs_f, s->theirs_m, f) < 0) {
            tape_put(s->theirs_f, s->theirs_m, f, data[MOY_MATCH_IN_HDR + i]);
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
    m->ports_budget.credit = MOY_MATCH_PORTS_BURST * 1000u;
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
    moy_match_beam_end(m);
    memset(m->peers, 0, sizeof(m->peers));
    m->defer_n = 0;
    // A request the console never synced belongs to the run that stopped
    // the link: the next run's offer() syncs, and would re-run itself.
    m->act = MOY_MATCH_ACT_NONE;
    m->active = false;
}

static void beacon(moy_match_t *m) {
    uint8_t p[3 + 200];
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_BEACON;
    p[2] = m->state;
    char body[sizeof(m->name) + sizeof(m->board) + sizeof(m->cart) + 8];
    int n = snprintf(body, sizeof(body), "%s|%s|%s|%d|%d", m->name, m->board, m->cart,
                     m->rate ? m->rate : MOY_MATCH_TICK_HZ,
                     m->pinned ? -(int)m->ch : m->away ? MOY_MATCH_HOME_CH : (int)m->ch);
    if (n < 0) {
        n = 0;
    }
    if (n > 200) {
        n = 200;
    }
    memcpy(p + 3, body, (size_t)n);
    broadcast(m, p, (size_t)(3 + n));
}

void moy_match_set_rate(moy_match_t *m, int hz, bool hold) {
    m->rate = (uint8_t)(hz == MOY_MATCH_TICK_FAST ? MOY_MATCH_TICK_FAST : MOY_MATCH_TICK_HZ);
    m->hold = hold;
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

static void bind_peer(moy_match_t *m, const uint8_t mac[6]) {
    memcpy(m->s.peer_mac, mac, 6);
    m->s.bound = true;
}

// The live match's peer (WHO IS HEARD in moy_match.h).
static bool from_partner(const moy_match_t *m, const uint8_t mac[6]) {
    return m->s.live && m->s.bound && memcmp(mac, m->s.peer_mac, 6) == 0;
}

// A console heard beaconing the cart this one sits on.
static bool from_lobby(moy_match_t *m, const uint8_t mac[6]) {
    const moy_match_peer_t *p = peer_of(m, mac);
    return p != NULL && m->cart[0] && strcmp(p->cart, m->cart) == 0;
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
    // The lower of the two consoles' word, unless this one's is forced.
    uint8_t hz = m->rate ? m->rate : (uint8_t)MOY_MATCH_TICK_HZ;
    uint8_t theirs = p->hz ? p->hz : (uint8_t)MOY_MATCH_TICK_HZ;
    f[14] = m->hold || hz <= theirs ? hz : theirs;
    size_t at = 15;
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
    bind_peer(m, p->mac);
    moy_lockstep_rate(m, f[14]);
    m->s.hold = m->hold;
    m->state = 2;
    if (relaunch) {
        ask(m, cart);
    }
    return 1;
}

// Ask the peer to host, saying the tick this console holds (a JOIN can land
// before the beacon that says it).
static void join(moy_match_t *m, const moy_match_peer_t *p) {
    uint8_t j[9] = {MOY_MATCH_PROTO, MOY_MATCH_T_JOIN};
    memcpy(j + 2, p->mac, 6);
    j[8] = m->rate ? m->rate : (uint8_t)MOY_MATCH_TICK_HZ;
    broadcast(m, j, sizeof(j));
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
        join(m, p);
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
        join(m, p);
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
    // "name|board|cart|hz|ch": the first three fields, a missing one empty,
    // the tick it says it holds and its channel (negative: pinned).
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
    int hz = 0;
    while (b < e && *b >= '0' && *b <= '9' && hz < 1000) {
        hz = hz * 10 + (*b++ - '0');
    }
    p->hz = (uint8_t)(hz == MOY_MATCH_TICK_FAST ? MOY_MATCH_TICK_FAST : MOY_MATCH_TICK_HZ);
    b = b < e && *b == '|' ? b + 1 : e;
    bool neg = b < e && *b == '-';
    b += neg ? 1 : 0;
    int ch = 0;
    while (b < e && *b >= '0' && *b <= '9' && ch < 100) {
        ch = ch * 10 + (*b++ - '0');
    }
    p->ch = (uint8_t)(ch <= 14 ? ch : 0);
    p->pinned = neg && p->ch != 0;
    p->state = msg[2];
    p->seen = now;
}

static bool for_us(const moy_match_t *m, const uint8_t *msg, size_t n, size_t at) {
    return n >= at + 6u && memcmp(msg + at, m->mac, 6) == 0;
}

static void on_start(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n) {
    if (n < 15 || m->s.live || !m->cart[0] || peer_of(m, mac) == NULL) {
        return;
    }
    uint8_t session = msg[2];
    int index = msg[3];
    uint32_t seed = (uint32_t)msg[4] | ((uint32_t)msg[5] << 8) | ((uint32_t)msg[6] << 16)
                    | ((uint32_t)msg[7] << 24);
    int hz = msg[14];
    const char *body = (const char *)msg + 15;
    size_t bn = n - 15u;
    size_t cut = 0;
    while (cut < bn && body[cut] != 0) {
        cut++;
    }
    char cart[MOY_MATCH_CART + 1];
    copy_str(cart, sizeof(cart), body, cut);
    if (strcmp(cart, m->cart) != 0) {
        return;
    }
    char cfg[MOY_MATCH_CFG + 1] = "";
    if (cut < bn) {
        copy_str(cfg, sizeof(cfg), body + cut + 1, bn - cut - 1u);
    }
    add_peer(m, mac);
    // The session is built before the cart opens, so the run finds it.
    if (moy_lockstep_begin(m, index, seed, session, cfg_or_null(cfg)) != 0) {
        return;
    }
    bind_peer(m, mac);
    moy_lockstep_rate(m, hz);
    m->state = 2;
    ask(m, cart);
}

static void on_join(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n) {
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
    moy_match_peer_t *p = peer_of(m, mac);
    if (p == NULL || strcmp(p->cart, m->cart) != 0 || p->state == 2) {
        return;
    }
    if (n > 8) {
        p->hz = msg[8] == MOY_MATCH_TICK_FAST ? MOY_MATCH_TICK_FAST : MOY_MATCH_TICK_HZ;
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

static moy_match_port_t *port_of(moy_match_t *m, uint16_t port) {
    for (int i = 0; port != 0 && i < MOY_MATCH_PORTS; i++) {
        if (m->ports[i].port == port) {
            return &m->ports[i];
        }
    }
    return NULL;
}

// A T_PORT frame: queued on its port where the port is open and hears the
// sender (WHO IS HEARD).
static void on_port(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n) {
    if (n < MOY_MATCH_PORT_HDR || n - MOY_MATCH_PORT_HDR > MOY_MATCH_MSG_MAX) {
        return;
    }
    moy_match_port_t *q = port_of(m, (uint16_t)(msg[2] | (msg[3] << 8)));
    if (q == NULL || (q->from == MOY_MATCH_FROM_MATCH && !from_partner(m, mac))) {
        return;
    }
    if (q->n >= MOY_MATCH_PORT_DEPTH) {
        q->drops++;
        return;
    }
    unsigned at = (q->head + q->n) % MOY_MATCH_PORT_DEPTH;
    memcpy(q->mac[at], mac, 6);
    q->len[at] = (uint8_t)(n - MOY_MATCH_PORT_HDR);
    memcpy(q->data[at], msg + MOY_MATCH_PORT_HDR, n - MOY_MATCH_PORT_HDR);
    q->n++;
}

void moy_match_dispatch(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg,
                        size_t n, uint32_t now) {
    if (n < 2 || msg[0] != MOY_MATCH_PROTO) {
        return;
    }
    switch (msg[1]) {
        case MOY_MATCH_T_INPUT:
            if (from_partner(m, mac)) {
                moy_lockstep_packet(m, msg, n, now, true);
            }
            return;
        case MOY_MATCH_T_BEACON:
            on_beacon(m, mac, msg, n, now);
            return;
        case MOY_MATCH_T_MSG:
            if (m->s.live ? from_partner(m, mac) : from_lobby(m, mac)) {
                inbox_put(m, msg + 2, n - 2u);
            }
            return;
        case MOY_MATCH_T_PORT:
            on_port(m, mac, msg, n);
            return;
        case MOY_MATCH_T_BEAM:
            on_beam(m, mac, msg, n, now);
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
            if (from_partner(m, mac)) {
                lost(m, "peer left");
            }
            return;
        }
        case MOY_MATCH_T_JOIN:
            if (for_us(m, msg, n, 2)) {
                on_join(m, mac, msg, n);
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
            if (from_partner(m, mac)) {
                moy_lockstep_packet(m, buf, (size_t)len, now, true);
            }
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

static const uint8_t PROBE[] = {6, 11, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13};

// The beacon tick's channel (MOY_MATCH_HOME_CH): read where the radio is;
// a free station goes where a pinned peer was heard, else stays where its
// beam's peer was last heard (an offer taken on a probe tick, whose sender's
// beacon was missed), else home, else -- with no peer on its cart and no beam
// bound, every PROBE_EVERY-th tick -- one tick on the next probe channel. A
// listening beam's console is tuned every BEAM_PROBE_MS instead, and probes
// on every look but each BEAM_PROBE_HOME-th.
static void tune(moy_match_t *m, uint32_t now) {
    m->tuned_at = now;
    m->away = false;
    if (m->io == NULL || m->io->channel == NULL || m->s.live) {
        return;
    }
    int c = m->io->channel(m->io->ctx, 0);
    m->pinned = c < 0;
    m->ch = (uint8_t)(c < 0 ? -c : c);
    if (m->pinned || c == 0) {
        return;
    }
    int want = 0;
    bool kin = false;
    for (int i = 0; i < MOY_MATCH_PEERS; i++) {
        const moy_match_peer_t *p = &m->peers[i];
        if (!p->used || (int32_t)(now - p->seen) > MOY_MATCH_PEER_TTL_MS) {
            continue;
        }
        if (p->pinned && want == 0) {
            want = p->ch;
        }
        kin = kin || (m->cart[0] && strcmp(p->cart, m->cart) == 0);
    }
    kin = kin || beam_holds_channel(m);
    if (want == 0) {
        want = beam_rests(m, now);
    }
    if (want == 0 && (beam_hears(m, now) || beam_keeps(m, now))) {
        want = m->ch;
    }
    if (want == 0) {
        want = MOY_MATCH_HOME_CH;
        bool go = false;
        if (!kin && beam_listens(m)) {
            m->probe = (uint8_t)((m->probe + 1u) % MOY_MATCH_BEAM_PROBE_HOME);
            go = m->probe != 0;
        } else if (!kin && ++m->probe >= MOY_MATCH_PROBE_EVERY) {
            m->probe = 0;
            go = true;
        }
        if (go) {
            m->away = true;
            want = PROBE[m->probe_at];
            m->probe_at = (uint8_t)((m->probe_at + 1u) % sizeof(PROBE));
        }
    }
    if (want != m->ch) {
        int got = m->io->channel(m->io->ctx, want);
        m->pinned = got < 0;
        m->ch = (uint8_t)(got < 0 ? -got : got);
    }
}

int moy_match_poll(moy_match_t *m, uint32_t now) {
    if (!m->active) {
        return 0;
    }
    int n = 0;
    m->polled = now;
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
    beam_tick(m, now);
    bool listens = beam_listens(m);
    if (listens && (uint32_t)(now - m->tuned_at) >= MOY_MATCH_BEAM_PROBE_MS) {
        tune(m, now);
    }
    int32_t since = (int32_t)(now - m->beacon_at);
    if (since >= MOY_MATCH_BEACON_MS || since < 0) {
        m->beacon_at = now;
        if (!listens) {
            tune(m, now);
        }
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

int moy_match_port_open(moy_match_t *m, uint16_t port, int from) {
    if (port == 0 || (from != MOY_MATCH_FROM_ANY && from != MOY_MATCH_FROM_MATCH)) {
        return MOY_MATCH_PORT_BAD;
    }
    if (port_of(m, port) != NULL) {
        return MOY_MATCH_PORT_BUSY;
    }
    for (int i = 0; i < MOY_MATCH_PORTS; i++) {
        moy_match_port_t *q = &m->ports[i];
        if (q->port == 0) {
            q->port = port;
            q->from = (uint8_t)from;
            q->n = q->head = 0;
            q->drops = q->sent = q->refused = 0;
            q->budget.credit = MOY_MATCH_PORT_BURST * 1000u;
            q->budget.has_at = false;
            return 0;
        }
    }
    return MOY_MATCH_PORT_FULL;
}

int moy_match_port_close(moy_match_t *m, uint16_t port) {
    moy_match_port_t *q = port_of(m, port);
    if (q == NULL) {
        return MOY_MATCH_PORT_ABSENT;
    }
    q->port = 0;
    q->n = 0;
    return 0;
}

// `b` refilled to `now` at `rate` frames/s, held to `burst` frames: whether
// it holds a whole frame.
static bool budget_has(moy_match_budget_t *b, uint32_t now, uint32_t rate, uint32_t burst) {
    uint32_t cap = burst * 1000u;
    int32_t dt = (int32_t)(now - b->at);
    if (b->has_at && dt > 0) {
        uint64_t c = (uint64_t)b->credit + (uint64_t)dt * rate;
        b->credit = c > cap ? cap : (uint32_t)c;
    }
    if (!b->has_at || dt > 0) {
        b->at = now;
        b->has_at = true;
    }
    if (b->credit > cap) {
        b->credit = cap;
    }
    return b->credit >= 1000u;
}

int moy_match_port_send(moy_match_t *m, uint16_t port, const uint8_t *body, size_t n,
                        uint32_t now) {
    if (port == 0 || n > MOY_MATCH_MSG_MAX) {
        return MOY_MATCH_PORT_BAD;
    }
    moy_match_port_t *q = port_of(m, port);
    if (q == NULL) {
        return MOY_MATCH_PORT_ABSENT;
    }
    if (!m->active) {
        return MOY_MATCH_PORT_DOWN;
    }
    bool live = m->s.live;
    bool mine = budget_has(&q->budget, now, MOY_MATCH_PORT_RATE, MOY_MATCH_PORT_BURST);
    bool all = budget_has(&m->ports_budget, now,
                          live ? MOY_MATCH_LIVE_RATE : MOY_MATCH_PORTS_RATE,
                          live ? MOY_MATCH_LIVE_BURST : MOY_MATCH_PORTS_BURST);
    if (!mine || !all) {
        q->refused++;
        return MOY_MATCH_PORT_AGAIN;
    }
    uint8_t p[MOY_MATCH_PORT_HDR + MOY_MATCH_MSG_MAX];
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_PORT;
    p[2] = (uint8_t)(port & 0xFF);
    p[3] = (uint8_t)(port >> 8);
    if (n) {
        memcpy(p + MOY_MATCH_PORT_HDR, body, n);
    }
    if (!broadcast(m, p, MOY_MATCH_PORT_HDR + n)) {
        return MOY_MATCH_PORT_DOWN;
    }
    q->budget.credit -= 1000u;
    m->ports_budget.credit -= 1000u;
    q->sent++;
    return 0;
}

int moy_match_port_take(moy_match_t *m, uint16_t port, uint8_t mac[6], uint8_t *out,
                        size_t cap) {
    moy_match_port_t *q = port_of(m, port);
    if (q == NULL) {
        return MOY_MATCH_PORT_ABSENT;
    }
    if (q->n == 0) {
        return -1;
    }
    unsigned at = q->head;
    size_t n = q->len[at];
    if (n > cap) {
        return MOY_MATCH_PORT_FULL;
    }
    memcpy(mac, q->mac[at], 6);
    memcpy(out, q->data[at], n);
    q->head = (uint8_t)((at + 1u) % MOY_MATCH_PORT_DEPTH);
    q->n--;
    return (int)n;
}

int32_t moy_match_port_drops(const moy_match_t *m, uint16_t port) {
    const moy_match_port_t *q = port_of((moy_match_t *)m, port);
    return q == NULL ? -1 : (int32_t)q->drops;
}

int32_t moy_match_port_sent(const moy_match_t *m, uint16_t port) {
    const moy_match_port_t *q = port_of((moy_match_t *)m, port);
    return q == NULL ? -1 : (int32_t)q->sent;
}

int32_t moy_match_port_refused(const moy_match_t *m, uint16_t port) {
    const moy_match_port_t *q = port_of((moy_match_t *)m, port);
    return q == NULL ? -1 : (int32_t)q->refused;
}

int moy_match_port_waiting(const moy_match_t *m, uint16_t port) {
    const moy_match_port_t *q = port_of((moy_match_t *)m, port);
    return q == NULL ? -1 : q->n;
}

int moy_match_ports_open(const moy_match_t *m) {
    int n = 0;
    for (int i = 0; i < MOY_MATCH_PORTS; i++) {
        n += m->ports[i].port != 0;
    }
    return n;
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

// -- beaming a file (moy_match.h, BEAMING A FILE) ----------------------------------

#define W MOY_MATCH_BEAM_WINDOW
#define CHUNK MOY_MATCH_BEAM_CHUNK

typedef struct moy_beam {
    uint8_t role, state, why, what;
    bool bound, has_sent, has_ring, has_sum, summing, limited, looked;
    uint8_t peer[6];                    // the offer's sender, else the bound receiver
    uint32_t id, size, chunks, hash, sum;
    // send: the lowest chunk the receiver lacks, the next never sent and the
    // end of the receiver's room; receive: the lowest chunk it lacks and one
    // past the highest it holds.
    uint32_t base, next, limit, top;
    uint32_t bytes;                     // send: filled by the console; receive: taken by it
    uint32_t holes, since, refused_since;  // send: the pace's last report
    uint32_t sent, resent, refused, rate;
    uint32_t began, heard, sent_at, tended;
    char title[MOY_MATCH_CART + 1];
    char name[24];                      // the peer's console name
    moy_match_budget_t budget;
    uint8_t bits[W / 8];                // a ring slot's chunk: pending (send), held (receive)
    uint8_t ring[];                     // MOY_MATCH_BEAM_RING bytes once it has one
} moy_beam_t;

static bool bit_of(const uint8_t *b, unsigned i) {
    return (b[i >> 3] >> (i & 7u)) & 1u;
}

static void bit_put(uint8_t *b, unsigned i, bool on) {
    if (on) {
        b[i >> 3] = (uint8_t)(b[i >> 3] | (1u << (i & 7u)));
    } else {
        b[i >> 3] = (uint8_t)(b[i >> 3] & ~(1u << (i & 7u)));
    }
}

static bool slot_of(const moy_beam_t *b, uint32_t i) {
    return bit_of(b->bits, i % W);
}

static void slot_put(moy_beam_t *b, uint32_t i, bool on) {
    bit_put(b->bits, i % W, on);
}

static uint32_t chunk_len(const moy_beam_t *b, uint32_t i) {
    uint32_t left = b->size - i * CHUNK;
    return left < CHUNK ? left : CHUNK;
}

// The byte where chunk `i` ends, past the file's end on none.
static uint64_t chunk_end(uint32_t i) {
    return (uint64_t)(i + 1u) * CHUNK;
}

// `n` bytes between the ring at byte `at` of the file and `p`, either way.
static void ring_copy(moy_beam_t *b, uint32_t at, uint8_t *p, size_t n, bool in) {
    while (n) {
        uint32_t o = at % MOY_MATCH_BEAM_RING;
        size_t k = MOY_MATCH_BEAM_RING - o;
        if (k > n) {
            k = n;
        }
        if (in) {
            memcpy(b->ring + o, p, k);
        } else {
            memcpy(p, b->ring + o, k);
        }
        at += (uint32_t)k;
        p += k;
        n -= k;
    }
}

static size_t beam_head(uint8_t *p, uint32_t id, uint8_t kind, const uint8_t to[6]) {
    p[0] = MOY_MATCH_PROTO;
    p[1] = MOY_MATCH_T_BEAM;
    p[2] = kind;
    put32(p + 3, id);
    if (to == NULL) {
        return 7;
    }
    memcpy(p + 7, to, 6);
    return 13;
}

static bool beam_say_id(moy_match_t *m, uint32_t id, uint8_t kind, const uint8_t to[6],
                        const uint8_t *tail, size_t n) {
    uint8_t p[MOY_MATCH_FRAME];
    size_t at = beam_head(p, id, kind, to);
    if (n) {
        memcpy(p + at, tail, n);
    }
    return broadcast(m, p, at + n);
}

static bool beam_say(moy_match_t *m, const moy_beam_t *b, uint8_t kind, const uint8_t to[6],
                     const uint8_t *tail, size_t n) {
    return beam_say_id(m, b->id, kind, to, tail, n);
}

// A NO with its why, said as often as a DONE: neither is answered, and one
// frame is lost often enough.
static void beam_no(moy_match_t *m, const moy_beam_t *b, const uint8_t to[6], uint8_t why) {
    for (int k = 0; k < MOY_MATCH_BEAM_DONES; k++) {
        beam_say(m, b, MOY_MATCH_BEAM_NO, to, &why, 1);
    }
}

// The NO just said to the beam's peer, kept to be said again (moy_match.h).
static void beam_keep_no(moy_match_t *m, const moy_beam_t *b, uint8_t why) {
    m->kept = 3;
    m->kept_why = why;
    m->kept_id = b->id;
    m->kept_at = m->kept_said = m->polled;
    memcpy(m->kept_peer, b->peer, 6);
}

static bool beam_keeps(const moy_match_t *m, uint32_t now) {
    return m->kept != 0 && (int32_t)(now - m->kept_at) < MOY_MATCH_BEAM_KEEP_MS;
}

static void beam_fail(moy_match_t *m, moy_beam_t *b, uint8_t why, bool tell) {
    if (tell && (b->bound || b->state == MOY_BEAM_OFFERED)) {
        beam_no(m, b, b->peer, why);
        beam_keep_no(m, b, why);
    }
    b->state = MOY_BEAM_FAILED;
    b->why = why;
}

static bool beam_busy(const moy_beam_t *b) {
    return b->state != MOY_BEAM_NONE && b->state != MOY_BEAM_DONE
           && b->state != MOY_BEAM_FAILED;
}

static bool beam_holds_channel(const moy_match_t *m) {
    const moy_beam_t *b = m->beam;
    return b != NULL && (b->state == MOY_BEAM_OFFERED || b->state == MOY_BEAM_MOVING);
}

// The channel the beam's peer -- the one bound or offering, else the one a
// kept verdict is for -- last said it rests on (its beacon's), 0 unknown: a
// frame is heard a channel away at times, and an answer goes where its
// reader is.
static int beam_rests(moy_match_t *m, uint32_t now) {
    const uint8_t *mac = beam_holds_channel(m) ? m->beam->peer
                         : beam_keeps(m, now) ? m->kept_peer : NULL;
    const moy_match_peer_t *p = mac != NULL ? peer_of(m, mac) : NULL;
    if (p == NULL || (int32_t)(now - p->seen) > MOY_MATCH_PEER_TTL_MS) {
        return 0;
    }
    return p->ch;
}

static bool beam_listens(const moy_match_t *m) {
    return m->beam != NULL && m->beam->state == MOY_BEAM_LISTENING;
}

// The beam's peer was heard on this channel within BEAM_HERE_MS.
static bool beam_hears(const moy_match_t *m, uint32_t now) {
    return beam_holds_channel(m) && (int32_t)(now - m->beam->heard) < MOY_MATCH_BEAM_HERE_MS;
}

static moy_beam_t *beam_new(moy_match_t *m, uint8_t role, bool ring, uint32_t now) {
    moy_match_beam_end(m);
    moy_beam_t *b = moy_kalloc(sizeof(moy_beam_t) + (ring ? MOY_MATCH_BEAM_RING : 0u),
                               MOY_KMEM_PSRAM | MOY_KMEM_ZERO);
    if (b != NULL) {
        b->role = role;
        b->has_ring = ring;
        b->began = b->tended = now;
        m->beam = b;
    }
    return b;
}

static uint32_t beam_ram(const moy_beam_t *b) {
    return (uint32_t)sizeof(moy_beam_t) + (b->has_ring ? MOY_MATCH_BEAM_RING : 0u);
}

// -- the receiver ---------------------------------------------------------------

// The first chunk the console has not taken whole: the window's foot.
static uint32_t taken_chunks(const moy_beam_t *b) {
    return b->bytes == b->size ? b->chunks : b->bytes / CHUNK;
}

static uint32_t room_end(const moy_beam_t *b) {
    uint32_t end = taken_chunks(b) + W;
    return end < b->chunks ? end : b->chunks;
}

// The receiver's report: the lowest chunk it lacks (the count: all in), the
// top of what it holds, the end of its room and which of the WINDOW chunks
// from the lowest it lacks.
static void beam_need(moy_match_t *m, moy_beam_t *b, uint32_t now) {
    uint8_t t[12 + W / 8] = {0};
    put32(t, b->base);
    put32(t + 4, b->top);
    put32(t + 8, room_end(b));
    uint32_t room = room_end(b);
    for (unsigned k = 0; k < W && b->base + k < b->chunks; k++) {
        uint32_t i = b->base + k;
        if (i >= room || !slot_of(b, i)) {
            bit_put(t + 12, k, true);
        }
    }
    beam_say(m, b, MOY_MATCH_BEAM_NEED, b->peer, t, sizeof(t));
    b->sent_at = now;
    b->has_sent = true;
}

static void beam_done(moy_match_t *m, const moy_beam_t *b) {
    uint8_t ok = b->state == MOY_BEAM_DONE;
    beam_say(m, b, MOY_MATCH_BEAM_DONE, b->peer, &ok, 1);
}

// Every chunk in, every byte taken and the sender's hash heard: the verdict,
// said BEAM_DONES times and kept for a SUM that comes after.
static void beam_verdict(moy_match_t *m, moy_beam_t *b, uint32_t now) {
    if (b->state != MOY_BEAM_MOVING || b->base < b->chunks || b->bytes < b->size
            || !b->has_sum) {
        return;
    }
    if (b->hash == b->sum) {
        b->state = MOY_BEAM_DONE;
    } else {
        b->state = MOY_BEAM_FAILED;
        b->why = MOY_BEAM_WHY_DAMAGED;
    }
    m->kept = b->state == MOY_BEAM_DONE ? 1 : 2;
    m->kept_id = b->id;
    m->kept_at = now;
    memcpy(m->kept_peer, b->peer, 6);
    for (int k = 0; k < MOY_MATCH_BEAM_DONES; k++) {
        beam_done(m, b);
    }
}

// An OFFER heard while listening: held, and its sender's name and title
// read from "name|board|title".
static void beam_heard_offer(moy_beam_t *b, const uint8_t mac[6], const uint8_t *msg,
                             size_t n, uint32_t now) {
    if (n < 12) {
        return;
    }
    uint32_t id = get32(msg + 3), size = get32(msg + 7);
    uint8_t what = msg[11];
    if (b->state == MOY_BEAM_OFFERED) {
        if (id == b->id && memcmp(mac, b->peer, 6) == 0) {
            b->heard = now;
        }
        return;
    }
    if (b->state != MOY_BEAM_LISTENING || size == 0 || size > MOY_MATCH_BEAM_MAX
            || (what != MOY_BEAM_CART && what != MOY_BEAM_FILE)) {
        return;
    }
    const char *s = (const char *)msg + 12, *e = (const char *)msg + n;
    const char *bar = s;
    while (bar < e && *bar != '|') {
        bar++;
    }
    copy_str(b->name, sizeof(b->name), s, (size_t)(bar - s));
    s = bar < e ? bar + 1 : e;
    bar = s;
    while (bar < e && *bar != '|') {
        bar++;
    }
    s = bar < e ? bar + 1 : e;
    copy_str(b->title, sizeof(b->title), s, (size_t)(e - s));
    memcpy(b->peer, mac, 6);
    b->id = id;
    b->size = size;
    b->what = what;
    b->chunks = (uint32_t)(((uint64_t)size + CHUNK - 1) / CHUNK);
    b->heard = now;
    b->state = MOY_BEAM_OFFERED;
}

static void beam_heard_data(moy_match_t *m, moy_beam_t *b, const uint8_t *msg, size_t n,
                            uint32_t now) {
    if (b->state == MOY_BEAM_DONE || b->state == MOY_BEAM_FAILED) {
        if (b->why == 0 || b->why == MOY_BEAM_WHY_DAMAGED) {
            beam_done(m, b);
        }
        return;
    }
    if (b->state != MOY_BEAM_MOVING || n < MOY_MATCH_BEAM_HDR) {
        return;
    }
    uint32_t i = get32(msg + 13);
    if (i >= b->chunks || n - MOY_MATCH_BEAM_HDR != chunk_len(b, i)) {
        return;
    }
    b->heard = now;
    if (i < taken_chunks(b) || i >= room_end(b) || slot_of(b, i)) {
        return;
    }
    memcpy(b->ring + (i % W) * CHUNK, msg + MOY_MATCH_BEAM_HDR, n - MOY_MATCH_BEAM_HDR);
    slot_put(b, i, true);
    if (i + 1u > b->top) {
        b->top = i + 1u;
    }
    uint32_t room = room_end(b);
    while (b->base < room && slot_of(b, b->base)) {
        b->base++;
    }
    if (b->base == b->chunks) {
        beam_need(m, b, now);           // all in: the sender's SUM is asked for at once
    }
}

uint32_t moy_match_beam_held(const moy_match_t *m) {
    const moy_beam_t *b = m->beam;
    if (b == NULL || b->role != MOY_BEAM_RECEIVE || b->state != MOY_BEAM_MOVING) {
        return 0;
    }
    uint64_t end = (uint64_t)b->base * CHUNK;
    if (end > b->size) {
        end = b->size;
    }
    return end > b->bytes ? (uint32_t)(end - b->bytes) : 0u;
}

size_t moy_match_beam_take(moy_match_t *m, uint8_t *out, size_t n, uint32_t now) {
    moy_beam_t *b = m->beam;
    uint32_t held = moy_match_beam_held(m);
    if (n > held) {
        n = held;
    }
    if (n == 0) {
        return 0;
    }
    uint32_t from = taken_chunks(b);
    ring_copy(b, b->bytes, out, n, false);
    b->hash = moy_lockstep_hash(b->hash, out, n);
    b->bytes += (uint32_t)n;
    for (uint32_t i = from; i < taken_chunks(b); i++) {
        slot_put(b, i, false);
    }
    beam_verdict(m, b, now);
    return n;
}

// -- the sender ----------------------------------------------------------------

// Chunk `i` is whole in the ring.
static bool filled(const moy_beam_t *b, uint32_t i) {
    return b->bytes == b->size || chunk_end(i) <= b->bytes;
}

uint32_t moy_match_beam_room(const moy_match_t *m) {
    const moy_beam_t *b = m->beam;
    if (b == NULL || b->role != MOY_BEAM_SEND
            || (b->state != MOY_BEAM_OFFERING && b->state != MOY_BEAM_MOVING)) {
        return 0;
    }
    uint64_t end = (uint64_t)(b->base + W) * CHUNK;
    if (end > b->size) {
        end = b->size;
    }
    return end > b->bytes ? (uint32_t)(end - b->bytes) : 0u;
}

int moy_match_beam_fill(moy_match_t *m, const uint8_t *p, size_t n) {
    moy_beam_t *b = m->beam;
    if (b == NULL || b->role != MOY_BEAM_SEND
            || (b->state != MOY_BEAM_OFFERING && b->state != MOY_BEAM_MOVING)) {
        return MOY_BEAM_E_STATE;
    }
    uint32_t room = moy_match_beam_room(m);
    if (n > room) {
        n = room;
    }
    ring_copy(b, b->bytes, (uint8_t *)p, n, true);
    b->hash = moy_lockstep_hash(b->hash, p, n);
    b->bytes += (uint32_t)n;
    return (int)n;
}

static void beam_offer_frame(moy_match_t *m, moy_beam_t *b, uint32_t now) {
    uint8_t t[5 + sizeof(m->name) + sizeof(m->board) + MOY_MATCH_CART + 2];
    put32(t, b->size);
    t[4] = b->what;
    int n = snprintf((char *)t + 5, sizeof(t) - 5, "%s|%s|%s", m->name, m->board, b->title);
    size_t len = 5 + (n < 0 ? 0u : (size_t)n >= sizeof(t) - 5 ? sizeof(t) - 6 : (size_t)n);
    if (len > MOY_MATCH_FRAME - 7) {
        len = MOY_MATCH_FRAME - 7;
    }
    beam_say(m, b, MOY_MATCH_BEAM_OFFER, NULL, t, len);
    b->sent_at = now;
    b->has_sent = true;
}

static void beam_sum(moy_match_t *m, moy_beam_t *b, uint32_t now) {
    uint8_t t[4];
    put32(t, b->hash);
    beam_say(m, b, MOY_MATCH_BEAM_SUM, b->peer, t, sizeof(t));
    b->sent_at = now;
}

// The pace, once a report: down a quarter where a send was refused or the
// holes grew by more than a quarter of what went out since, up a quarter
// where the rate held the sender back.
static void beam_pace(moy_beam_t *b, uint32_t holes) {
    uint32_t r = b->rate;
    if (b->refused_since || holes > b->holes + b->since / 4u) {
        r -= r / 4u;
    } else if (b->limited) {
        r += r / 4u + 1u;
    }
    b->rate = r < MOY_MATCH_BEAM_RATE_MIN ? MOY_MATCH_BEAM_RATE_MIN
              : r > MOY_MATCH_BEAM_RATE_MAX ? MOY_MATCH_BEAM_RATE_MAX : r;
    b->holes = holes;
    b->since = b->refused_since = 0;
    b->limited = false;
}

// A NEED at the sender: the first binds its receiver; each moves the window
// and marks pending what it lost below its top (everything it lacks once
// nothing else is left to send), and sets the pace.
static void beam_heard_need(moy_match_t *m, moy_beam_t *b, const uint8_t mac[6],
                            const uint8_t *msg, size_t n, uint32_t now) {
    if (n < 25 + W / 8) {
        return;
    }
    if (b->state == MOY_BEAM_OFFERING) {
        memcpy(b->peer, mac, 6);
        b->bound = true;
        const moy_match_peer_t *p = peer_of(m, mac);
        copy_str(b->name, sizeof(b->name), p != NULL ? p->name : "",
                 p != NULL ? strlen(p->name) : 0);
        b->rate = MOY_MATCH_BEAM_RATE;
        b->budget.credit = MOY_MATCH_BEAM_BURST * 1000u;
        b->budget.has_at = false;
        b->state = MOY_BEAM_MOVING;
    } else if (b->state != MOY_BEAM_MOVING) {
        return;
    }
    if (memcmp(mac, b->peer, 6) != 0) {
        uint8_t why = MOY_BEAM_WHY_STOPPED;
        beam_say(m, b, MOY_MATCH_BEAM_NO, mac, &why, 1);
        return;
    }
    b->heard = now;
    uint32_t base = get32(msg + 13), top = get32(msg + 17), limit = get32(msg + 21);
    const uint8_t *miss = msg + 25;
    if (base > b->chunks) {
        base = b->chunks;
    }
    if (limit > b->chunks) {
        limit = b->chunks;
    }
    if (limit > b->limit) {
        b->limit = limit;
    }
    if (base > b->base) {
        if (base - b->base >= W) {
            memset(b->bits, 0, sizeof(b->bits));
        } else {
            for (uint32_t i = b->base; i < base; i++) {
                slot_put(b, i, false);
            }
        }
        b->base = base;
        if (b->next < base) {
            b->next = base;
        }
    }
    if (b->base >= b->chunks) {
        b->summing = true;
        beam_sum(m, b, now);
        return;
    }
    bool idle = true;
    for (uint32_t i = b->base; i < b->next && idle; i++) {
        idle = !slot_of(b, i);
    }
    if (idle && b->next < b->chunks && b->next < b->limit && filled(b, b->next)) {
        idle = false;
    }
    uint32_t holes = 0;
    for (unsigned k = 0; k < W && base + k < b->next; k++) {
        uint32_t i = base + k;
        if (i < b->base) {
            continue;
        }
        if (!bit_of(miss, k)) {
            slot_put(b, i, false);
        } else if (i < top || idle) {
            slot_put(b, i, true);
            holes += i < top;
        }
    }
    beam_pace(b, holes);
}

static void beam_send_chunks(moy_match_t *m, moy_beam_t *b, uint32_t now) {
    if (b->summing) {
        if ((int32_t)(now - b->sent_at) >= MOY_MATCH_BEAM_NEED_MS) {
            beam_sum(m, b, now);
        }
        return;
    }
    uint32_t burst = b->rate * MOY_MATCH_BEAM_BURST_MS / 1000u;
    if (burst < MOY_MATCH_BEAM_BURST) {
        burst = MOY_MATCH_BEAM_BURST;
    }
    uint8_t t[4 + CHUNK];
    for (;;) {
        uint32_t i = b->base;
        while (i < b->next && !slot_of(b, i)) {
            i++;
        }
        bool again = i < b->next;
        if (!again) {
            i = b->next;
            if (i >= b->chunks || i >= b->limit || !filled(b, i)) {
                return;
            }
        }
        if (!budget_has(&b->budget, now, b->rate, burst)) {
            b->limited = true;
            return;
        }
        uint32_t len = chunk_len(b, i);
        put32(t, i);
        memcpy(t + 4, b->ring + (i % W) * CHUNK, len);
        if (!beam_say(m, b, MOY_MATCH_BEAM_DATA, b->peer, t, 4 + len)) {
            b->refused++;
            b->refused_since++;
            return;
        }
        b->budget.credit -= 1000u;
        b->sent++;
        b->since++;
        if (again) {
            b->resent++;
            slot_put(b, i, false);
        } else {
            b->next++;
        }
    }
}

// -- the frames ----------------------------------------------------------------

// A SUM at a receiver: kept for the verdict, or answered from the verdict
// this console keeps.
static void beam_heard_sum(moy_match_t *m, moy_beam_t *b, const uint8_t mac[6],
                           const uint8_t *msg, size_t n, uint32_t now) {
    uint32_t id = get32(msg + 3);
    if (b != NULL && b->role == MOY_BEAM_RECEIVE && b->state == MOY_BEAM_MOVING
            && id == b->id && memcmp(mac, b->peer, 6) == 0) {
        b->heard = now;
        b->sum = get32(msg + 13);
        b->has_sum = true;
        beam_verdict(m, b, now);
        return;
    }
    if (m->kept != 3 && m->kept && id == m->kept_id && memcmp(mac, m->kept_peer, 6) == 0
            && (int32_t)(now - m->kept_at) < MOY_MATCH_BEAM_KEEP_MS) {
        uint8_t ok = m->kept == 1;
        beam_say_id(m, id, MOY_MATCH_BEAM_DONE, mac, &ok, 1);
    }
    (void)n;
}

static void on_beam(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg, size_t n,
                    uint32_t now) {
    if (n < 7) {
        return;
    }
    moy_beam_t *b = m->beam;
    uint8_t kind = msg[2];
    if (kind == MOY_MATCH_BEAM_SUM) {
        if (n >= 17 && for_us(m, msg, n, 7)) {
            beam_heard_sum(m, b, mac, msg, n, now);
        }
        return;
    }
    // A beam this console said NO to, whose peer still sends: NO again.
    if (m->kept == 3 && beam_keeps(m, now) && get32(msg + 3) == m->kept_id
            && memcmp(mac, m->kept_peer, 6) == 0
            && (kind == MOY_MATCH_BEAM_OFFER || for_us(m, msg, n, 7))) {
        if (kind != MOY_MATCH_BEAM_NO
                && (int32_t)(now - m->kept_said) >= MOY_MATCH_BEAM_NEED_MS) {
            m->kept_said = now;
            beam_say_id(m, m->kept_id, MOY_MATCH_BEAM_NO, mac, &m->kept_why, 1);
        }
        return;
    }
    if (b == NULL) {
        return;
    }
    if (kind == MOY_MATCH_BEAM_OFFER) {
        if (b->role == MOY_BEAM_RECEIVE) {
            beam_heard_offer(b, mac, msg, n, now);
        }
        return;
    }
    // Every other kind is addressed: to this console, from the peer, on its
    // beam (a NO to everyone ends a held offer).
    bool to_me = for_us(m, msg, n, 7);
    bool to_all = n >= 13 && memcmp(msg + 7, MOY_MATCH_BROADCAST, 6) == 0;
    if (get32(msg + 3) != b->id || !(to_me || (to_all && kind == MOY_MATCH_BEAM_NO))) {
        return;
    }
    bool from_peer = memcmp(mac, b->peer, 6) == 0;
    uint8_t why = n >= 14 ? msg[13] : 0;
    if (b->role == MOY_BEAM_SEND) {
        if (kind == MOY_MATCH_BEAM_NEED) {
            beam_heard_need(m, b, mac, msg, n, now);
        } else if (kind == MOY_MATCH_BEAM_NO && b->state == MOY_BEAM_OFFERING) {
            const moy_match_peer_t *p = peer_of(m, mac);
            copy_str(b->name, sizeof(b->name), p != NULL ? p->name : "",
                     p != NULL ? strlen(p->name) : 0);
            beam_fail(m, b, why == MOY_BEAM_WHY_ROOM ? MOY_BEAM_WHY_ROOM
                      : MOY_BEAM_WHY_DECLINED, false);
        } else if (kind == MOY_MATCH_BEAM_NO && b->state == MOY_BEAM_MOVING && from_peer) {
            beam_fail(m, b, MOY_BEAM_WHY_STOPPED, false);
        } else if (kind == MOY_MATCH_BEAM_DONE && b->state == MOY_BEAM_MOVING && from_peer
                   && n >= 14) {
            if (msg[13]) {
                b->base = b->chunks;
                b->state = MOY_BEAM_DONE;
            } else {
                beam_fail(m, b, MOY_BEAM_WHY_DAMAGED, false);
            }
        }
        return;
    }
    if (!from_peer) {
        return;
    }
    if (kind == MOY_MATCH_BEAM_DATA) {
        beam_heard_data(m, b, msg, n, now);
    } else if (kind == MOY_MATCH_BEAM_NO
               && (b->state == MOY_BEAM_OFFERED || b->state == MOY_BEAM_MOVING)) {
        beam_fail(m, b, MOY_BEAM_WHY_STOPPED, false);
    }
}

static void beam_tick(moy_match_t *m, uint32_t now) {
    moy_beam_t *b = m->beam;
    if (b == NULL) {
        return;
    }
    if (m->s.live && beam_busy(b)) {
        beam_fail(m, b, MOY_BEAM_WHY_MATCH, true);
        return;
    }
    if (beam_busy(b)) {
        if (b->looked) {
            b->looked = false;
            b->tended = now;
        } else if ((int32_t)(now - b->tended) > MOY_MATCH_BEAM_TEND_MS) {
            beam_fail(m, b, MOY_BEAM_WHY_STOPPED, true);
            return;
        }
    }
    switch (b->state) {
        case MOY_BEAM_OFFERING:
            if ((int32_t)(now - b->began) >= MOY_MATCH_BEAM_WAIT_MS) {
                beam_fail(m, b, MOY_BEAM_WHY_NOBODY, false);
            } else if (!m->away && (!b->has_sent
                                    || (int32_t)(now - b->sent_at) >= MOY_MATCH_BEAM_OFFER_MS)) {
                beam_offer_frame(m, b, now);
            }
            return;
        case MOY_BEAM_LISTENING:
            if ((int32_t)(now - b->began) >= MOY_MATCH_BEAM_WAIT_MS) {
                beam_fail(m, b, MOY_BEAM_WHY_NOBODY, false);
            }
            return;
        case MOY_BEAM_OFFERED:
            if ((int32_t)(now - b->heard) > MOY_MATCH_BEAM_SILENT_MS) {
                beam_fail(m, b, MOY_BEAM_WHY_GONE, false);
            }
            return;
        case MOY_BEAM_MOVING:
            if ((int32_t)(now - b->heard) > MOY_MATCH_BEAM_SILENT_MS) {
                beam_fail(m, b, MOY_BEAM_WHY_GONE, false);
            } else if (b->role == MOY_BEAM_SEND) {
                beam_send_chunks(m, b, now);
            } else if ((int32_t)(now - b->sent_at) >= MOY_MATCH_BEAM_NEED_MS) {
                beam_need(m, b, now);
            }
            return;
        default:
            return;
    }
}

// -- the console's calls ---------------------------------------------------------

static int beam_ready(const moy_match_t *m) {
    if (!m->active) {
        return MOY_BEAM_E_DOWN;
    }
    return m->s.live ? MOY_BEAM_E_LIVE : 0;
}

int moy_match_beam_send(moy_match_t *m, const char *title, uint8_t what, uint32_t size,
                        uint32_t now) {
    int rc = beam_ready(m);
    if (rc != 0) {
        return rc;
    }
    if (size == 0 || size > MOY_MATCH_BEAM_MAX
            || (what != MOY_BEAM_CART && what != MOY_BEAM_FILE)) {
        return MOY_BEAM_E_SIZE;
    }
    moy_beam_t *b = beam_new(m, MOY_BEAM_SEND, true, now);
    if (b == NULL) {
        return MOY_BEAM_E_ROOM;
    }
    b->size = size;
    b->what = what;
    b->chunks = (uint32_t)(((uint64_t)size + CHUNK - 1) / CHUNK);
    b->hash = MOY_LOCKSTEP_HASH0;
    b->rate = MOY_MATCH_BEAM_RATE;
    copy_str(b->title, sizeof(b->title), title, title != NULL ? strlen(title) : 0);
    b->id = draw_seed(m) | 1u;
    b->state = MOY_BEAM_OFFERING;
    beam_offer_frame(m, b, now);
    return 0;
}

int moy_match_beam_listen(moy_match_t *m, uint32_t now) {
    int rc = beam_ready(m);
    if (rc != 0) {
        return rc;
    }
    moy_beam_t *b = beam_new(m, MOY_BEAM_RECEIVE, false, now);
    if (b == NULL) {
        return MOY_BEAM_E_ROOM;
    }
    b->state = MOY_BEAM_LISTENING;
    return 0;
}

int moy_match_beam_accept(moy_match_t *m, uint32_t now) {
    moy_beam_t *b = m->beam;
    if (b == NULL || b->role != MOY_BEAM_RECEIVE || b->state != MOY_BEAM_OFFERED) {
        return MOY_BEAM_E_STATE;
    }
    int rc = beam_ready(m);
    if (rc != 0) {
        return rc;
    }
    moy_beam_t *g = moy_krealloc(b, sizeof(moy_beam_t) + MOY_MATCH_BEAM_RING, MOY_KMEM_PSRAM);
    if (g == NULL) {
        beam_fail(m, b, MOY_BEAM_WHY_ROOM, true);
        return MOY_BEAM_E_ROOM;
    }
    m->beam = b = g;
    b->has_ring = true;
    memset(b->bits, 0, sizeof(b->bits));
    b->base = b->top = b->bytes = 0;
    b->hash = MOY_LOCKSTEP_HASH0;
    b->has_sum = false;
    b->bound = true;
    b->heard = b->tended = now;
    b->state = MOY_BEAM_MOVING;
    beam_need(m, b, now);
    return 0;
}

int moy_match_beam_refuse(moy_match_t *m, uint8_t why) {
    moy_beam_t *b = m->beam;
    if (b == NULL || b->role != MOY_BEAM_RECEIVE || b->state != MOY_BEAM_OFFERED) {
        return MOY_BEAM_E_STATE;
    }
    beam_fail(m, b, why, true);
    return 0;
}

void moy_match_beam_end(moy_match_t *m) {
    moy_beam_t *b = m->beam;
    if (b == NULL) {
        return;
    }
    if (b->state == MOY_BEAM_OFFERED) {
        beam_no(m, b, b->peer, MOY_BEAM_WHY_DECLINED);
        beam_keep_no(m, b, MOY_BEAM_WHY_DECLINED);
    } else if (b->state == MOY_BEAM_MOVING) {
        beam_no(m, b, b->peer, MOY_BEAM_WHY_STOPPED);
        beam_keep_no(m, b, MOY_BEAM_WHY_STOPPED);
    } else if (b->state == MOY_BEAM_OFFERING) {
        beam_no(m, b, MOY_MATCH_BROADCAST, MOY_BEAM_WHY_STOPPED);
    }
    m->beam = NULL;
    moy_kfree(b);
}

bool moy_match_beam_info(moy_match_t *m, moy_match_beam_info_t *out) {
    moy_beam_t *b = m->beam;
    if (b == NULL) {
        return false;
    }
    b->looked = true;
    out->role = b->role;
    out->state = b->state;
    out->why = b->why;
    out->what = b->what;
    out->size = b->size;
    if (b->role == MOY_BEAM_RECEIVE) {
        out->done = b->bytes;
    } else {
        uint64_t d = (uint64_t)b->base * CHUNK;
        out->done = d > b->size ? b->size : (uint32_t)d;
    }
    out->sent = b->sent;
    out->resent = b->resent;
    out->refused = b->refused;
    out->rate = b->role == MOY_BEAM_SEND ? b->rate : 0u;
    out->ram = beam_ram(b);
    copy_str(out->title, sizeof(out->title), b->title, strlen(b->title));
    copy_str(out->peer, sizeof(out->peer), b->name, strlen(b->name));
    return true;
}

#undef W
#undef CHUNK
