// moy_match: two-console play below the VM (docs/kernel_cartpath_2026-10.md
// §3.6, docs/netplay_v1.md). The radio link's protocol -- beacons, the peer
// table, the same-cart handshake (offer, join, start, the host's chase), the
// match's end -- and the lockstep session it forms: inputs, never state; a
// missing input STALLS, never extrapolates.
//
// WHY LOCKSTEP, AND ITS THREE RULES. Measured on glass, T-Deck <-> Guition,
// 2026-08-20: a 16-byte input frame round-trips in 5.0 ms median / 14.2 ms
// p99, a 250-byte state blob in 15.0 / 17.1 ms, so the payload is the inputs
// and both consoles run the same simulation from them.
//   1. INPUT DELAY: frame N's input is sent during N-delay and played on N.
//      The delay starts at one tick and raises itself to two under stall
//      pressure (ESCALATE_*), never lower: a lower delay mid-match could
//      overwrite a frame already emitted. Delay is each console's own
//      sampling lead, so the two sides may run different values.
//   2. REDUNDANCY, NOT RETRANSMIT: every packet carries the last REDUNDANCY
//      frames of input (a byte each), and the frame its sender is waiting
//      for, so a stalled peer is served the frame it is stuck on (the fixed
//      window deadlocked two consoles on a desk, 2026-08-22). The radio's ack
//      lies (64 of 200 messages arrived while every send said True).
//   3. A MISSING INPUT STALLS THE SIM. Advancing on a guess desyncs the two
//      consoles permanently and invisibly.
// Determinism: the tick is fixed (TICK_HZ), and each logic frame starts from
// a seed derived from (seed, frame), so drawing, which consumes the random
// stream at each board's own rate, cannot move it (2026-08-22). The session
// paces itself and drops any debt of a full tick by re-basing (burst
// catch-up coupled two consoles into a 30% stall, 2026-08-25); a stalled tick
// retries every loop frame, and between ticks the newest packet is resent.
// The guest (index 1) slews its phase 1 ms a tick toward SLEW_TARGET_MS of
// measured arrival margin while the delay is one; the host is the anchor.
//
// THE LINK. One instance owns the radio's one receive path. Pairing is
// same-cart: two consoles sitting on the same title find each other through
// beacons, and the LOWER MAC hosts -- symmetric, so two never both host. The
// handshake is broadcast with the destination in the payload (a unicast from
// an unregistered peer is dropped, 2026-08-22). The host resends START until
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

#define MOY_MATCH_PROTO 1
enum {
    MOY_MATCH_T_BEACON = 1, MOY_MATCH_T_JOIN = 2, MOY_MATCH_T_START = 3,
    MOY_MATCH_T_INPUT = 4, MOY_MATCH_T_MSG = 5, MOY_MATCH_T_BYE = 6,
};

// The lockstep clock and its levers (configuration; docs/netplay_v1.md has
// the measurements behind each).
#define MOY_MATCH_TICK_HZ 30
#define MOY_MATCH_DELAY 1
#define MOY_MATCH_DELAY_MAX 2
#define MOY_MATCH_ESCALATE_TICKS 90     // the escalation window, 3 s at 30 Hz
#define MOY_MATCH_ESCALATE_AT 12        // distinct stalled ticks in a window
#define MOY_MATCH_REDUNDANCY 4
#define MOY_MATCH_MAX_SPAN 24
#define MOY_MATCH_GIVE_UP 300           // stalled advances in a row: dead
#define MOY_MATCH_TAPE 256
#define MOY_MATCH_SLEW_TARGET_MS 12
#define MOY_MATCH_SLEW_BAND_MS 4
// The link's.
#define MOY_MATCH_BEACON_MS 400
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
    void (*say)(void *ctx, const char *line);   // NULL: printf
    void *ctx;
} moy_match_io_t;

typedef struct {
    uint8_t mac[6];
    char name[24], board[16], cart[MOY_MATCH_CART + 1];
    uint8_t state;                      // 0 idle (launcher), 1 in a cart, 2 matched
    uint32_t seen;
    bool used;
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
// Is a tick due? Pure: no schedule side effect.
bool moy_lockstep_pending(const moy_match_t *m, uint32_t now);
// Is the next fixed tick due? Consumes the schedule.
bool moy_lockstep_due(moy_match_t *m, uint32_t now);
// One lockstep frame with the local console's held mask: 1 simulate (the
// players' buttons and the frame's seed are set), 0 STALL, -1 no session.
// `timed` false: no clock (the phase controller and arrival stamps idle).
int moy_lockstep_advance(moy_match_t *m, uint8_t held, uint32_t now, bool timed);
void moy_lockstep_resend(moy_match_t *m);
// One inbound frame: 1 taken, 0 not this session's input.
int moy_lockstep_packet(moy_match_t *m, const uint8_t *data, size_t n, uint32_t now,
                        bool timed);
uint32_t moy_lockstep_seed_of(uint32_t seed, int32_t f);
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
