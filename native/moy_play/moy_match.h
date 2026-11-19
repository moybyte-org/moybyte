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
// WHO IS HEARD. Frames are neither signed nor encrypted, so the sender's
// address is the one check: a live match is bound to its peer's MAC, and an
// INPUT, a BYE or a net message from anyone else is dropped (outside a match
// a net message is taken from a console heard beaconing this cart). A START
// is taken only for the cart this console sits on (announce) and from a
// console heard beaconing, so it can re-run that cart and open nothing else.
// A PORT's message is taken from the console its port names: FROM_ANY any
// console in range (a datagram socket's rule: its record carries the sender
// for the process to judge), FROM_MATCH only the live match's peer.
//
// PORTS. T_MSG is port 0, the game's: the cart in front's net.send and
// on_net. A process opens a port 1..65535 (one opener a port), and T_PORT
// carries its number; a message for a port nobody opened is dropped, and
// each open port keeps its own queue, PORT_DEPTH records deep, the newest
// dropped and counted while it is full. A port never sees an INPUT frame,
// and a port's frame never reaches the session or the game's inbox. A port
// sends only while it is open and within its SEND BUDGET (below); port 0 has
// none, its sends being the game's.
//
// BEAMING A FILE. A console sends one file -- a cart's `.moy` archive or a
// person's file -- as bytes to one console nearby that is not playing a match
// (T_BEAM, BEAM_* below). The sender OFFERS it on the air (its id, size,
// what it is, its console's name and the item's title) every BEAM_OFFER_MS;
// a console takes an offer only while a person on it has opened the receive
// state (moy_match_beam_listen), holds the first one it hears and asks its
// person, or refuses it (moy_match_beam_refuse: no room). Its accept is a
// NEED, which binds the two by MAC and beam id.
//   THE BYTES MOVE THROUGH A WINDOW. Each side holds a ring of BEAM_WINDOW
// chunks and nothing else, whatever the size: the sender's console fills it
// from its store as room opens (moy_match_beam_room, _fill) and the
// receiver's empties it into its store (moy_match_beam_held, _take), so a
// beam's RAM is fixed. The receiver drives completion, because the ack lies:
// every BEAM_NEED_MS it sends the lowest chunk it lacks, the top of what it
// holds, the end of its room (the window past what its console took) and a
// bitmap of the BEAM_WINDOW chunks from the lowest; the sender sends what it
// has filled up to that room, then again the chunks a NEED reports lost
// below the top (everything the receiver lacks once nothing else is left).
//   THE PACE IS FOUND, not fixed: a beam starts at BEAM_RATE chunks a second
// and each NEED adjusts it -- down by a quarter where a send was refused
// since the last one (THE SEND BUDGET's queue on a P4) or the receiver's
// holes grew by more than a quarter of what was sent, else up by a quarter
// where the rate held the sender back -- between BEAM_RATE_MIN and
// BEAM_RATE_MAX, its bucket BEAM_BURST_MS of the rate deep.
//   THE HASH IS STREAMED. The sender folds FNV-1a over the bytes as its
// console fills them and the receiver over the bytes as its console takes
// them; once the receiver holds every chunk its NEED says so, the sender
// answers SUM (its hash, again every BEAM_NEED_MS until DONE), and the
// receiver whose console has taken the last byte answers DONE, good or
// damaged, BEAM_DONES times, and keeps answering a SUM of that beam with
// its verdict for BEAM_KEEP_MS after, whatever it ended since, so a sender
// whose DONEs were all lost still hears it.
//   NOTHING WAITS FOREVER. An offer or a receive state that nobody answers
// ends after BEAM_WAIT_MS; a bound side that hears nothing from its peer for
// BEAM_SILENT_MS ends with GONE; a beam its console stops looking at
// (moy_match_beam_info, every frame) for BEAM_TEND_MS ends with STOPPED and
// tells its peer, which is what a VM that went away leaves. A decline, a
// refusal and either side's stop is NO, carrying why. A match outranks a
// beam: none starts while a match is live, and one going on ends when one
// begins.
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
    MOY_MATCH_T_PORT = 7,               // [proto, 7, port lo, port hi, body]
    MOY_MATCH_T_BEAM = 8,               // [proto, 8, kind, id (4), ...] BEAMING A FILE
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
// is free sits on HOME_CH; with no peer on its cart heard and no beam bound,
// every PROBE_EVERY-th beacon tick it visits the next channel of the probe
// list for one tick, and a pinned peer heard there is followed to its
// channel for as long as it is heard. A pinned console never moves; a live match never tunes.
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
#define MOY_MATCH_PORTS 8               // ports open on this console at once
#define MOY_MATCH_PORT_DEPTH 4          // records a port keeps unread
#define MOY_MATCH_PORT_HDR 4
// THE SEND BUDGET. A port's frame shares the air and the transport with the
// match's inputs, and a late input stalls both consoles, so ports send from
// token buckets refilled by the clock: each port's own (fair between
// holders) and the console's, which every port's send also spends. A send
// past either answers MOY_MATCH_PORT_AGAIN and nothing goes on air.
//   Lockstep's cadence: a tick's input (30 or 60 Hz) and a stalled tick's
// retries or the resend between ticks, together at most one per LOCK_MS:
// 62.5 frames/s, about 45/s measured in a linked match; beacons 2.5/s.
//   The tightest transport is the Waveshare P4's: one FIFO, 12 deep, whose
// one consumer clears a frame per ~10 ms RPC (native/p4/moy_c6), about 100
// frames/s, so every frame queued ahead of an input delays it ~10 ms (an
// S3 sends in ~60 us).
//   While a match is live the console's bucket is LIVE_RATE/s with a burst of
// LIVE_BURST: one port frame per 100 ms, so an input waits behind at most one
// (~10 ms on the P4, under a 60 Hz tick's 16.7 ms), and inputs, beacons and
// ports together use at most 75 of the ~100 frames/s the P4 clears.
//   Outside a match it is PORTS_RATE/s with a burst of PORTS_BURST, under
// the P4 queue's 12 so a beacon or a START is never refused behind ports,
// and kind to a match nearby: eight consoles in range at their cap add eight
// frames a 30 Hz tick to its receive ring, which a drain takes DRAIN_MAX at
// a time.
//   A port's own bucket is PORT_RATE/s with a burst of PORT_BURST, the
// receiving queue's PORT_DEPTH: a burst fills a reader's queue and no more.
#define MOY_MATCH_PORT_RATE 10          // frames/s, each port
#define MOY_MATCH_PORT_BURST 4
#define MOY_MATCH_PORTS_RATE 30         // frames/s, every port together
#define MOY_MATCH_PORTS_BURST 8
#define MOY_MATCH_LIVE_RATE 10          // ...while a match is live
#define MOY_MATCH_LIVE_BURST 1
enum { MOY_MATCH_FROM_ANY = 0, MOY_MATCH_FROM_MATCH = 1 };
// BEAMING A FILE. Every frame is [proto, 8, kind, id (4)] and, but for the
// offer, the destination (6); a chunk follows its index (4) and fills the
// frame. The payloads: OFFER size (4), what (1), "name|board|title"; NEED
// lowest lacked (4), top (4), room's end (4), the bitmap (BEAM_WINDOW bits);
// DATA index (4), the bytes; NO why (1); DONE good (1); SUM hash (4). A size
// and a chunk's index are 32 bits, so a beam carries up to BEAM_MAX bytes and
// what bounds one is the receiver's free space. Each side's ring is
// BEAM_RING bytes.
#define MOY_MATCH_BEAM_MAX 0xFFFF0000u  // the size field's ceiling
#define MOY_MATCH_BEAM_HDR 17
#define MOY_MATCH_BEAM_CHUNK (MOY_MATCH_FRAME - MOY_MATCH_BEAM_HDR)
#define MOY_MATCH_BEAM_WINDOW 128
#define MOY_MATCH_BEAM_RING (MOY_MATCH_BEAM_WINDOW * MOY_MATCH_BEAM_CHUNK)
#define MOY_MATCH_BEAM_OFFER_MS 500     // an offer's resend
#define MOY_MATCH_BEAM_NEED_MS 200      // a receiver's report, and a sender's SUM
#define MOY_MATCH_BEAM_WAIT_MS 60000    // an offer or a receive state nobody answers
#define MOY_MATCH_BEAM_SILENT_MS 3000   // a bound peer heard from
#define MOY_MATCH_BEAM_TEND_MS 3000     // a beam's console looked at it
#define MOY_MATCH_BEAM_KEEP_MS 3000     // a receiver answers a finished beam's SUM
#define MOY_MATCH_BEAM_HERE_MS 1500     // a free station stays where its beam's peer was heard
#define MOY_MATCH_BEAM_RATE 60          // chunks/s a beam starts at
#define MOY_MATCH_BEAM_RATE_MIN 20
#define MOY_MATCH_BEAM_RATE_MAX 2000
#define MOY_MATCH_BEAM_BURST 6          // the bucket's floor, chunks
#define MOY_MATCH_BEAM_BURST_MS 50      // ...and its depth in time at the rate
#define MOY_MATCH_BEAM_DONES 3          // a receiver's DONE and either side's NO, sent this many times
enum { MOY_MATCH_BEAM_OFFER = 1, MOY_MATCH_BEAM_NEED = 2, MOY_MATCH_BEAM_DATA = 3,
       MOY_MATCH_BEAM_NO = 4, MOY_MATCH_BEAM_DONE = 5, MOY_MATCH_BEAM_SUM = 6 };
// What a beam carries: a cart's `.moy` archive, or a person's file.
enum { MOY_BEAM_CART = 1, MOY_BEAM_FILE = 2 };
// A beam's role, state and why it failed (moy_match_beam_info).
enum { MOY_BEAM_SEND = 1, MOY_BEAM_RECEIVE = 2 };
enum {
    MOY_BEAM_NONE = 0,
    MOY_BEAM_OFFERING = 2,              // send: the offer on the air, nobody bound
    MOY_BEAM_LISTENING = 3,             // receive: waiting for an offer
    MOY_BEAM_OFFERED = 4,               // receive: an offer held, its person asked
    MOY_BEAM_MOVING = 5,                // bound: chunks crossing
    MOY_BEAM_DONE = 6,                  // send: the receiver has it; receive: whole and checked
    MOY_BEAM_FAILED = 7,
};
enum {
    MOY_BEAM_WHY_NOBODY = 1,            // BEAM_WAIT_MS passed unanswered
    MOY_BEAM_WHY_GONE = 2,              // the peer fell silent
    MOY_BEAM_WHY_DECLINED = 3,          // the receiver said no
    MOY_BEAM_WHY_STOPPED = 4,           // the peer stopped
    MOY_BEAM_WHY_DAMAGED = 5,           // the whole failed its hash
    MOY_BEAM_WHY_MATCH = 6,             // a match began
    MOY_BEAM_WHY_ROOM = 7,              // the receiver has no room for it
};
// What the beam calls answer where they fail.
enum {
    MOY_BEAM_E_SIZE = -1,               // 0 bytes, or more than BEAM_MAX
    MOY_BEAM_E_LIVE = -2,               // a match is live
    MOY_BEAM_E_ROOM = -3,               // no block for the ring
    MOY_BEAM_E_DOWN = -4,               // the link is down
    MOY_BEAM_E_STATE = -5,              // not in the state the call needs
};

// A beam as the console reads it: the peer's console name is the offer's
// (receive) or the binding NEED's sender's beacon name (send). `done` is the
// bytes across: those the receiver holds in order (send), those its console
// took (receive). `sent`, `resent` and `refused` count DATA frames, `rate` is
// the pace now (chunks/s, send) and `ram` the bytes the beam holds.
typedef struct {
    uint8_t role, state, why, what;
    uint32_t size, done;
    uint32_t sent, resent, refused, rate, ram;
    char title[MOY_MATCH_CART + 1];
    char peer[24];
} moy_match_beam_info_t;

struct moy_beam;
// What the port calls answer where they fail.
enum {
    MOY_MATCH_PORT_BAD = -1,            // port 0 or out of range, a body too big
    MOY_MATCH_PORT_FULL = -2,           // no port free; a take's `cap` short of the record
    MOY_MATCH_PORT_BUSY = -3,           // the port is open already
    MOY_MATCH_PORT_ABSENT = -4,         // the port is not open
    MOY_MATCH_PORT_DOWN = -5,           // the link is down or the radio refused the send
    MOY_MATCH_PORT_AGAIN = -6,          // past the send budget: nothing sent, try later
};

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

// A send budget: `credit` in thousandths of a frame, refilled from the ms
// since `at` (none before `has_at`).
typedef struct {
    uint32_t credit;
    uint32_t at;
    bool has_at;
} moy_match_budget_t;

// One open port's queue, oldest first.
typedef struct {
    uint16_t port;                      // 0: the slot is free
    uint8_t from;                       // MOY_MATCH_FROM_*
    uint8_t n, head;
    uint32_t drops;
    uint32_t sent, refused;             // frames this port sent, sends its budget refused
    moy_match_budget_t budget;
    uint8_t mac[MOY_MATCH_PORT_DEPTH][6];
    uint8_t len[MOY_MATCH_PORT_DEPTH];
    uint8_t data[MOY_MATCH_PORT_DEPTH][MOY_MATCH_MSG_MAX];
} moy_match_port_t;

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
    // The peer's MAC a link-formed match is bound to (WHO IS HEARD); a
    // session begun alone is not bound and the link's dispatch feeds it nothing.
    uint8_t peer_mac[6];
    bool bound;
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
    moy_match_port_t ports[MOY_MATCH_PORTS];
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
    moy_match_budget_t ports_budget;    // every port's sends together
    struct moy_beam *beam;              // the beam under way, NULL none (BEAMING A FILE)
    // The last beam this console received to its end, whose SUM it answers
    // for BEAM_KEEP_MS: its id, sender and verdict (1 whole, 2 damaged).
    uint32_t kept_id, kept_at;
    uint8_t kept_peer[6];
    uint8_t kept;
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
// PORTS: `port` 1..65535 opened, its sender rule `from` (MOY_MATCH_FROM_*):
// 0, or MOY_MATCH_PORT_BAD, _FULL (every slot taken), _BUSY (open already).
// The port stays open across the link's stop and start; its close drops
// what waits.
int moy_match_port_open(moy_match_t *m, uint16_t port, int from);
int moy_match_port_close(moy_match_t *m, uint16_t port);
// One broadcast T_PORT carrying `body` on open `port` at `now` (ms): 0, or
// _BAD (port 0, more than MOY_MATCH_MSG_MAX bytes), _ABSENT (not open),
// _DOWN, _AGAIN (past the send budget).
int moy_match_port_send(moy_match_t *m, uint16_t port, const uint8_t *body, size_t n,
                        uint32_t now);
// The oldest record waiting on `port`: its sender into `mac`, its bytes into
// `out`, and its length; -1 with none waiting, _FULL where `cap` is short
// of it (it waits on), _ABSENT where the port is not open.
int moy_match_port_take(moy_match_t *m, uint16_t port, uint8_t mac[6], uint8_t *out,
                        size_t cap);
// Records `port` dropped while its queue was full, and how many wait: -1
// where it is not open.
int32_t moy_match_port_drops(const moy_match_t *m, uint16_t port);
// Frames `port` sent, and sends its budget refused: -1 where it is not open.
int32_t moy_match_port_sent(const moy_match_t *m, uint16_t port);
int32_t moy_match_port_refused(const moy_match_t *m, uint16_t port);
int moy_match_port_waiting(const moy_match_t *m, uint16_t port);
// How many ports are open.
int moy_match_ports_open(const moy_match_t *m);
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

// BEAMING A FILE. Each starting call ends a beam already there first. The
// sender's: `size` bytes of `what` titled `title` offered (OFFERING), whose
// bytes its console fills in order as `room` allows (`fill` takes at most
// `room`: the count taken, or MOY_BEAM_E_STATE). 0, or MOY_BEAM_E_*.
int moy_match_beam_send(moy_match_t *m, const char *title, uint8_t what, uint32_t size,
                        uint32_t now);
uint32_t moy_match_beam_room(const moy_match_t *m);
int moy_match_beam_fill(moy_match_t *m, const uint8_t *p, size_t n);
// The receiver's: the receive state opened (LISTENING), and its person's yes
// to the offer it holds (OFFERED -> MOVING), or the offer refused with `why`
// (MOY_BEAM_WHY_*, its sender told). 0, or MOY_BEAM_E_*.
int moy_match_beam_listen(moy_match_t *m, uint32_t now);
int moy_match_beam_accept(moy_match_t *m, uint32_t now);
int moy_match_beam_refuse(moy_match_t *m, uint8_t why);
// The bytes a MOVING receiver holds in order past what its console took, and
// the next of them copied into `out` (at most `n`: the count). What is taken
// is the console's to keep: its ring room goes back to the sender.
uint32_t moy_match_beam_held(const moy_match_t *m);
size_t moy_match_beam_take(moy_match_t *m, uint8_t *out, size_t n, uint32_t now);
// The beam ended, its ring freed: a bound peer, or a held offer's sender,
// is told NO (a decline, or a stop).
void moy_match_beam_end(moy_match_t *m);
// The beam as it stands: false with none. The console's look, every frame
// (BEAM_TEND_MS).
bool moy_match_beam_info(moy_match_t *m, moy_match_beam_info_t *out);

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
// The PERF line's net= and lock= for the kernel's match into `v` (a
// moy_perf_values_t), while a kernel run plays it in front: no console frame
// runs then to push them. Each consumes its window, as the console's do.
void moy_match_kernel_perf(void *v, uint32_t now);

#endif // MOY_MATCH_H
