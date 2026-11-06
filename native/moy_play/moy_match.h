// moy_match: two-console play below the VM (docs/history/kernel_cartpath_2026-10.md
// §3.6, docs/netplay_v1.md). The radio link's protocol -- beacons, the peer
// table, the same-cart handshake (offer, join, start, the host's chase), the
// match's end -- and the lockstep session it forms: inputs, never state; a
// missing input STALLS, never extrapolates.
//
// WHY LOCKSTEP, AND ITS THREE RULES. Measured on glass, T-Deck <-> Guition:
// a 16-byte input frame round-trips in 5.0 ms median / 14.2 ms
// p99, a 250-byte state blob in 15.0 / 17.1 ms, so the payload is the inputs
// and both consoles run the same simulation from them.
//   1. INPUT DELAY: frame N's input is sent during N-delay and played on N.
//      The delay starts at one tick, rises a tick under the PEER's stall
//      pressure (ESCALATE_*: this console's lead is what the peer waits on)
//      and comes back down a tick when the peer reports a tick's slack to
//      spare (LOWER_*). A frame is sampled once and the tape
//      is first-write-wins, so neither direction can rewrite a frame the peer
//      may already hold: a drop only skips one sample. Delay is each
//      console's own sampling lead, so the two sides may run different
//      values.
//   2. REDUNDANCY, NOT RETRANSMIT: every packet carries the last REDUNDANCY
//      frames of input (a byte each), and the frame its sender is waiting
//      for, so a stalled peer is served the frame it is stuck on (the fixed
//      window deadlocked two consoles on a desk). The radio's ack
//      lies (64 of 200 messages arrived while every send said True).
//   3. A MISSING INPUT STALLS THE SIM. Advancing on a guess desyncs the two
//      consoles permanently and invisibly.
// A DESYNC IS SAID: every CHECK_EVERY frames each console stamps the hash of
// the inputs it applied (and its tier's world digest while the world check is
// armed), every input packet carries the newest stamp, and the first that
// disagrees is printed and noticed on the screen.
// Determinism: every frame's tick is the match's (TICK_FAST when both consoles
// say they hold it, else TICK_HZ, the host's choice in START; from a step
// down's agreed frame on, TICK_HZ), and each logic frame starts from
// a seed derived from (seed, frame), so drawing, which consumes the random
// stream at each board's own rate, cannot move it. The session
// paces itself and drops any debt of a full tick by re-basing (burst
// catch-up coupled two consoles into a 30% stall); a stalled tick
// retries every loop frame, and between ticks the newest packet is resent.
// The guest (index 1) slews its phase 1 ms a tick toward SLEW_TARGET_MS of
// measured arrival margin while the delay is one; the host is the anchor.
//
// THE LINK. One instance owns the radio's one receive path. Pairing is
// same-cart: two consoles sitting on the same title find each other through
// beacons, and the LOWER MAC hosts -- symmetric, so two never both host. The
// handshake is broadcast with the destination in the payload (a unicast from
// an unregistered peer is dropped). The host resends START until
// the guest's first input arrives and gives up after START_TRIES; a match
// whose session declares itself dead, a peer's BYE and an unanswered START
// all end the match and ask the console to re-run the cart solo.
//
// THE CONSOLE'S HALF. What the link cannot do itself -- open the cart a host
// named, re-run the cart from frame zero -- it leaves as one ACTION the
// console takes (moy_match_action): a VM's console on its frame, the kernel's
// run by ending with why LINK. The host's tuning rides START (at most
// MOY_MATCH_CFG bytes of JSON, else "{}": wrong together beats wrong apart).
//
// INSTANCES. The protocol is an instance so the host's tests run two consoles
// in one process; a board has one, the kernel's (moy_match_kernel), allocated
// from the loop's allocator (PSRAM) and wired to native/moy_net's link.

#ifndef MOY_MATCH_H
#define MOY_MATCH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_MATCH_PROTO 3
enum {
    MOY_MATCH_T_BEACON = 1, MOY_MATCH_T_JOIN = 2, MOY_MATCH_T_START = 3,
    MOY_MATCH_T_INPUT = 4, MOY_MATCH_T_MSG = 5, MOY_MATCH_T_BYE = 6,
};

// The lockstep clock and its levers (configuration; docs/netplay_v1.md has
// the measurements behind each).
#define MOY_MATCH_TICK_HZ 30            // a match's tick unless its host asks 60
#define MOY_MATCH_TICK_FAST 60          // a cart paced 60 or "free"
#define MOY_MATCH_DELAY 1
#define MOY_MATCH_DELAY_MAX_MS 66       // the delay's ceiling: 2 ticks at 30 Hz, 4 at 60
#define MOY_MATCH_ESCALATE_S 3          // the escalation window
#define MOY_MATCH_ESCALATE_AT 12        // distinct stalled ticks in a window, per 30 Hz
// At 30 Hz (the default rate) the window in ticks and the delay's ceiling.
#define MOY_MATCH_ESCALATE_TICKS (MOY_MATCH_ESCALATE_S * MOY_MATCH_TICK_HZ)
#define MOY_MATCH_DELAY_MAX 2
#define MOY_MATCH_REDUNDANCY 4
#define MOY_MATCH_MAX_SPAN 24
#define MOY_MATCH_GIVE_UP 300           // stalled advances in a row: dead
#define MOY_MATCH_TAPE 256
// A stalled tick's retry (until its input arrives) and the resend between
// ticks go out at most every LOCK_MS, whatever the loop's rate: a loop whose
// frames between ticks are cheap sent every pass and flooded the air
// (moy_lockstep_gate).
#define MOY_MATCH_LOCK_MS 16
#define MOY_MATCH_SLEW_TARGET_MS 12
#define MOY_MATCH_SLEW_BAND_MS 4
// The desync check: every CHECK_EVERY frames each console stamps a checkpoint
// (the hash of every input it applied, and the world digest its tier gave
// it), and every input packet carries the sender's newest one.
#define MOY_MATCH_CHECK_EVERY 32
#define MOY_MATCH_CHECKS 4
#define MOY_MATCH_IN_HDR 22             // an input packet's header
// A 60 Hz match that cannot keep its rate steps down to 30 for the rest of
// the match: the host decides after a settled window ticked under STEP_BAR
// percent of the rate and names the frame the step takes, STEP_LEAD frames
// on, in every packet. The guest cannot simulate that frame before it holds
// the host's input for it, which travels in a packet that carries the notice,
// so both consoles tick the same frames at the same dt.
#define MOY_MATCH_STEP_BAR 93
#define MOY_MATCH_STEP_LEAD 32
// A console's delay sets the PEER's stalls, so the peer's reports steer it:
// up a tick when the peer's last window stalled ESCALATE_AT ticks, down a
// tick when the peer's last window was clean (a quarter of that) and every
// margin it reported through this console's window was at least a tick plus
// LOWER_GUARD_MS; down never within LOWER_HOLD windows of a change.
#define MOY_MATCH_LOWER_GUARD_MS 8
#define MOY_MATCH_LOWER_HOLD 2
// The link's.
#define MOY_MATCH_BEACON_MS 400
// THE CHANNEL: ESP-NOW is heard only on the channel it is sent on, and a
// station associated to an AP is pinned to the AP's. A console whose station
// is free sits on HOME_CH; with no peer on its cart heard, every PROBE_EVERY-th
// beacon tick it visits the next channel of the probe list for one tick, and a
// pinned peer heard there is followed to its channel for as long as it is
// heard. A pinned console never moves; a live match never tunes.
#define MOY_MATCH_HOME_CH 1
#define MOY_MATCH_PROBE_EVERY 3
#define MOY_MATCH_PEER_TTL_MS 2000
#define MOY_MATCH_DRAIN_MAX 24
#define MOY_MATCH_START_TRIES 12
#define MOY_MATCH_PEERS 8
#define MOY_MATCH_FRAME 250             // ESP-NOW's frame
#define MOY_MATCH_MSG_MAX 240           // a net.send payload
#define MOY_MATCH_CFG 100               // the host's tuning carried by START
#define MOY_MATCH_CART 120              // the title START carries
#define MOY_MATCH_DEFER 8               // non-input frames a mid-frame drain parks
#define MOY_MATCH_INBOX 8               // net.* messages waiting for the cart

// The transport. `recv` answers the payload's length, -1 when the ring is
// empty, -2 on a radio error (the link then calls `recover`).
typedef struct {
    int (*send)(void *ctx, const uint8_t mac[6], const uint8_t *data, size_t n);
    int (*recv)(void *ctx, uint8_t mac[6], uint8_t *data, uint32_t cap);
    int (*add_peer)(void *ctx, const uint8_t mac[6]);
    int (*recover)(void *ctx);          // NULL: none
    // The radio's channel: `set` > 0 moves an unassociated station there.
    // Answers the channel, negated while the station is associated (pinned
    // to its AP's); 0 unknown. NULL: the link never tunes.
    int (*channel)(void *ctx, int set);
    void (*say)(void *ctx, const char *line);   // NULL: printf
    void *ctx;
} moy_match_io_t;

typedef struct {
    uint8_t mac[6];
    char name[24], board[16], cart[MOY_MATCH_CART + 1];
    uint8_t state;                      // 0 idle (launcher), 1 in a cart, 2 matched
    uint32_t seen;
    bool used;
    uint8_t hz;                         // the tick it says it can hold; 0 unsaid (30)
    uint8_t ch;                         // the channel it beacons on; 0 unsaid
    bool pinned;                        // ...an associated station's, which it cannot leave
} moy_match_peer_t;

typedef struct {
    bool live;
    int index, peer;
    uint32_t seed;
    uint8_t session;
    uint32_t tick_ms;
    bool has_next;
    uint32_t next_ms;
    int delay, redundancy;
    int32_t frame;                      // the next frame to simulate
    uint32_t stalls, stall_ticks, packets_in, packets_out;
    bool waiting, dead;
    int32_t last_peer_frame, peer_need;
    bool has_sent;
    int32_t last_sent;
    uint32_t stall_mark;
    int32_t mine_f[MOY_MATCH_TAPE], theirs_f[MOY_MATCH_TAPE];
    uint8_t mine_m[MOY_MATCH_TAPE], theirs_m[MOY_MATCH_TAPE];
    int32_t arr_f[64];
    uint32_t arr_t[64];
    bool has_ema;
    float m_ema;
    int32_t win_mark;
    uint32_t win_stalls;
    int32_t tps_f;
    bool has_tps;
    uint32_t tps_ms;
    // The two global players' buttons as the last advance applied them.
    uint8_t held[2], prev[2], pressed[2];
    uint32_t frame_seed;                // the seed the last advance's frame starts from
    char config[MOY_MATCH_CFG + 1];     // the host's tuning, "" for none
    // The desync check. `hist` folds every applied frame's inputs; a
    // checkpoint at frame F is the state ENTERING F: hist over 0..F-1 and the
    // world digest the tier noted for F (0: none, never compared).
    uint32_t hist;
    int32_t world_f;
    uint32_t world_d;
    int32_t ck_f[MOY_MATCH_CHECKS];
    uint32_t ck_in[MOY_MATCH_CHECKS], ck_w[MOY_MATCH_CHECKS];
    int32_t ck_last;                    // the newest checkpoint's frame, -1 none
    int32_t peer_ck_f;                  // the peer's newest heard
    uint32_t peer_ck_in, peer_ck_w;
    int32_t peer_ck_cmp;                // the peer's newest compared
    int32_t desync;                     // the first checkpoint that disagreed, -1 none
    uint8_t desync_kind;                // 1 the inputs, 2 the world
    uint32_t checks;                    // checkpoints compared and agreeing
    // The match's rate (the host's, carried by START) and what it scales.
    uint16_t tick_hz;
    uint16_t tick_rem;                  // the period's fraction of a ms, in 1/hz
    int delay_max;
    int32_t escalate_ticks;
    uint32_t escalate_at;
    // The delay's way down: the peer's report of how early this console's
    // inputs reach it (ms, its margin EMA; 255 none), the window's lowest.
    uint8_t peer_margin, rep_min;
    // Stalled ticks in the last settled window: mine (reported; 255 none
    // yet) and the peer's report of its own.
    uint8_t last_win, peer_win;
    int32_t delay_mark;                 // the frame of the last delay change
    uint32_t raises, lowers;
    // The step down (STEP_*): the rate the match began at, and from frame
    // `step_from` on `step_hz` (0: none named). The window's wall clock, for
    // the host's rate reading; `hold` keeps the host's rate (a dev force).
    uint16_t hz0;
    uint8_t step_hz;
    int32_t step_from;
    bool stepped;                       // this console ticks at step_hz now
    bool hold;
    bool has_win_ms;
    uint32_t win_ms;
    bool has_lock;                      // the last pass that sent (LOCK_MS)
    uint32_t lock_at;
    // Where the tick rate goes, in ms of the tick schedule given up (the
    // PERF line's `lock=`, LockstepSession.lock): a tick that comes due a
    // whole period late drops its debt, `late` when nothing was stalled
    // (this console's own frame ran long: a collection, a heavy frame); a
    // slot that passes while a tick waits on the peer's input is `stall`;
    // `slew` is the guest's phase moved later.
    uint32_t late_ms, stall_ms;
    int32_t slew_ms;
} moy_lockstep_t;

enum { MOY_MATCH_ACT_NONE = 0, MOY_MATCH_ACT_LAUNCH = 1 };

typedef struct {
    const moy_match_io_t *io;
    bool active;
    uint8_t mac[6];
    char name[24], board[16], cart[MOY_MATCH_CART + 1];
    char cfg[MOY_MATCH_CFG + 1];        // this console's tuning, offered when it hosts
    uint8_t state;
    moy_match_peer_t peers[MOY_MATCH_PEERS];
    moy_lockstep_t s;
    uint8_t session_id;
    uint8_t start_frame[MOY_MATCH_FRAME];
    uint16_t start_len;                 // 0: no invite to chase
    uint8_t start_peer[6];
    uint32_t start_tries;
    bool beaconed;
    uint32_t beacon_at;
    uint32_t rx, tx, drops, recovers;
    uint32_t rng;
    // Non-input frames a mid-frame drain parked for the tail.
    uint8_t defer_n;
    uint8_t defer_mac[MOY_MATCH_DEFER][6];
    uint8_t defer_len[MOY_MATCH_DEFER];
    uint8_t defer[MOY_MATCH_DEFER][MOY_MATCH_FRAME];
    // net.* messages for the cart, oldest first.
    uint8_t inbox_n, inbox_head;
    uint8_t inbox_len[MOY_MATCH_INBOX];
    uint8_t inbox[MOY_MATCH_INBOX][MOY_MATCH_FRAME];
    uint32_t inbox_drops;
    // The console's pending action.
    uint8_t act;
    char act_cart[MOY_MATCH_CART + 1];
    char error[48];
    bool world_check;                   // the tiers note world digests (a diag)
    uint8_t rate;                       // the tick this console says it can hold
    bool hold;                          // ...and takes as is when it hosts (a dev force)
    uint8_t ch;                         // the channel the radio is on (0 unknown)
    bool pinned;                        // ...an associated station's
    uint8_t probe;                      // beacon ticks since the last probe, and its place
    uint8_t probe_at;
} moy_match_t;

extern const uint8_t MOY_MATCH_BROADCAST[6];

// -- the link ------------------------------------------------------------------

void moy_match_init(moy_match_t *m, const moy_match_io_t *io, const char *name,
                    const char *board, uint32_t entropy);
// The radio is up (the caller started it with the measured recipe): the link
// takes `mac` as its own and listens. Idempotent.
void moy_match_start(moy_match_t *m, const uint8_t mac[6]);
// The match ends and the peers are forgotten; the caller takes the radio down.
void moy_match_stop(moy_match_t *m);
// What this console sits on: `cart` ("" at the launcher) and `state` (0 idle,
// 1 in a cart; a live match reads 2 whatever is asked). Beacons at once.
void moy_match_announce(moy_match_t *m, const char *cart, int state);
// The tuning this console offers when it hosts: a JSON object of at most
// MOY_MATCH_CFG bytes, else "{}".
void moy_match_set_config(moy_match_t *m, const char *cfg);
// The tick this console says it can hold (MOY_MATCH_TICK_HZ or
// MOY_MATCH_TICK_FAST), in its beacon. A host starts the match at the lower
// of its own and the peer's and may step it down; `hold` starts it at this
// console's own and never steps (a dev force). The guest takes START's.
void moy_match_set_rate(moy_match_t *m, int hz, bool hold);
// The host's half at a cart's start: a peer stands by on `cart` -> 1, this
// run is a match (lower MAC) or it asked the peer to host (higher MAC) -> 0.
int moy_match_offer(moy_match_t *m, const char *cart, uint32_t now);
int moy_match_offer_seeded(moy_match_t *m, const char *cart, uint32_t now, bool has_seed,
                           uint32_t seed);
// The frame's tail: the parked frames, the ring, and on the beacon tick the
// beacon, the peers' expiry and a pairing attempt. The frames it took.
int moy_match_poll(moy_match_t *m, uint32_t now);
// Before a lockstep tick: the ring's input frames to the session, everything
// else parked for the tail (a START or a BYE must not land mid-frame).
int moy_match_drain_input(moy_match_t *m, uint32_t now, int budget);
// Drop the match; the link stays up.
void moy_match_end(moy_match_t *m);
// One frame to every console in range: false when the link is down or the
// radio refused it.
bool moy_match_broadcast(moy_match_t *m, const uint8_t *p, size_t n);
// A cart's net.send: one broadcast T_MSG. 0 sent, -1 too big, -2 not active.
int moy_match_send_msg(moy_match_t *m, const uint8_t *body, size_t n);
// The oldest waiting net.* message into `out`: its length, or -1 with none.
int moy_match_take_msg(moy_match_t *m, uint8_t *out, size_t cap);
// The console's pending action, taken: ACT_LAUNCH with the cart's title.
int moy_match_action(moy_match_t *m, char *cart, size_t cap);
int moy_match_peer_count(const moy_match_t *m, uint32_t now);
const moy_match_peer_t *moy_match_peer_at(const moy_match_t *m, int i);
// The handshake's pure halves, for the tests: a frame in, as the drain sees it.
void moy_match_dispatch(moy_match_t *m, const uint8_t mac[6], const uint8_t *msg,
                        size_t n, uint32_t now);
// The peer standing by on `cart`, or NULL.
const moy_match_peer_t *moy_match_candidate(const moy_match_t *m, const char *cart,
                                            uint32_t now);

// -- the lockstep session ------------------------------------------------------

// A session (the link begins one; the tests and a host's loopback too).
// `cfg` is the host's tuning or NULL. 0, or -1 for a bad index.
int moy_lockstep_begin(moy_match_t *m, int index, uint32_t seed, uint8_t session,
                       const char *cfg);
void moy_lockstep_close(moy_match_t *m);
// The rate frame `f` ticks at (its dt is 1/that), and the match's clock after
// `f` ticks, in ms: the same on both consoles across a step down. Pure.
int moy_lockstep_hz_at(const moy_match_t *m, int32_t f);
int32_t moy_lockstep_ms(const moy_match_t *m, int32_t f);
// The match's tick rate in Hz (begin sets MOY_MATCH_TICK_HZ): the period, and
// the delay's ceiling and the escalation window, which are times.
void moy_lockstep_rate(moy_match_t *m, int hz);
// Is a tick due? Pure: no schedule side effect.
bool moy_lockstep_pending(const moy_match_t *m, uint32_t now);
// Is the next fixed tick due? Consumes the schedule.
bool moy_lockstep_due(moy_match_t *m, uint32_t now);
// May this pass of the console's loop send: a due tick always, a stalled
// tick whose missing input has arrived always, and any other retry or the
// resend between ticks once MOY_MATCH_LOCK_MS has passed since the last pass
// that sent. A true answer is that pass.
bool moy_lockstep_gate(moy_match_t *m, uint32_t now, bool due);
// One lockstep frame with the local console's held mask: 1 simulate (the
// players' buttons and the frame's seed are set), 0 STALL, -1 no session.
// `timed` false: no clock (the phase controller and arrival stamps idle).
int moy_lockstep_advance(moy_match_t *m, uint8_t held, uint32_t now, bool timed);
void moy_lockstep_resend(moy_match_t *m);
// One inbound frame: 1 taken, 0 not this session's input.
int moy_lockstep_packet(moy_match_t *m, const uint8_t *data, size_t n, uint32_t now,
                        bool timed);
uint32_t moy_lockstep_seed_of(uint32_t seed, int32_t f);
// The desync check's world half: true when the frame about to be simulated
// is a checkpoint whose world digest is not noted yet; the tier then notes
// its digest of the world ENTERING that frame (any 32-bit value but 0).
// Only while the console's world check is armed (moy_match_world_check):
// the input half of the check is always on.
bool moy_lockstep_world_due(const moy_match_t *m);
void moy_match_world_check(moy_match_t *m, bool on);
void moy_lockstep_world(moy_match_t *m, int32_t frame, uint32_t digest);
// FNV-1a over `n` bytes, folded into `h` (start from MOY_LOCKSTEP_HASH0).
#define MOY_LOCKSTEP_HASH0 2166136261u
uint32_t moy_lockstep_hash(uint32_t h, const uint8_t *p, size_t n);
// Ticks per second since the last call (the PERF line's net=); one reader.
uint32_t moy_lockstep_tps(moy_match_t *m, uint32_t now);
int32_t moy_lockstep_expand(const moy_match_t *m, uint32_t f16);

// The layout's size and the session's offset, for a binding that maps it.
size_t moy_match_size(void);
size_t moy_match_offset_s(void);

// -- the kernel's instance -------------------------------------------------------

// The board's link over native/moy_net's ring (NULL where the image has no
// link, or before the first start); the loop's tail polls it.
moy_match_t *moy_match_kernel(void);
// Made on first use, over the board's radio: NULL where there is none.
moy_match_t *moy_match_kernel_make(const char *name, const char *board, uint32_t entropy);
// The frame tail's poll of the kernel's link, when it is live.
void moy_match_kernel_poll(uint32_t now);

#endif // MOY_MATCH_H
